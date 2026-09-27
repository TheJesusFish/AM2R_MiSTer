#!/usr/bin/env python3
"""Independent integer RGBA equations for the proposed unified pixel contract.

These tests establish the specification; they do not invoke production C/RTL.
Import blend_rgba in descriptor/RTL comparison tests. Existing regression-tested
named mode arithmetic is retained; it is NOT all native-runner-verified (see
docs/unified-renderer-coverage.md, named blend compatibility limitations).
Custom factors, transparent replacement and pixel
state behavior are explicit, and not derived from the old SW bm_complex bug.
AM2R's ONE/ONE call is authored bytecode evidence, not a new native pixel capture.
"""

from __future__ import annotations

import itertools
import unittest

ZERO, ONE, SRC_COLOR, INV_SRC_COLOR, SRC_ALPHA, INV_SRC_ALPHA = range(1, 7)
DEST_ALPHA, INV_DEST_ALPHA, DEST_COLOR, INV_DEST_COLOR, SRC_ALPHA_SAT = range(7, 12)


def factor_value(factor: int, source: tuple, destination: tuple, channel: int) -> int:
    if factor == ZERO:
        return 0
    if factor == ONE:
        return 255
    if factor == SRC_COLOR:
        return source[channel]
    if factor == INV_SRC_COLOR:
        return 255 - source[channel]
    if factor == SRC_ALPHA:
        return source[3]
    if factor == INV_SRC_ALPHA:
        return 255 - source[3]
    if factor == DEST_ALPHA:
        return destination[3]
    if factor == INV_DEST_ALPHA:
        return 255 - destination[3]
    if factor == DEST_COLOR:
        return destination[channel]
    if factor == INV_DEST_COLOR:
        return 255 - destination[channel]
    if factor == SRC_ALPHA_SAT:
        return 255 if channel == 3 else min(source[3], 255 - destination[3])
    raise ValueError(f"invalid GameMaker blend factor {factor}; valid range 1..11")


def blend_rgba(source, destination, mode="normal", *, factors=None,
               enabled=True, alpha_test=False, alpha_ref=0, fog=None,
               write_mask=(True, True, True, True)):
    """Return a complete RGBA pixel using exact byte-domain arithmetic.

    Source has already been sampled/tinted. Alpha test precedes constant fog.
    No universal transparent-source discard: custom factors, min/max and
    blend-disabled replacement can use nonzero RGB even with source alpha 0.
    """
    source, destination = tuple(source), tuple(destination)
    if len(source) != 4 or len(destination) != 4 or len(write_mask) != 4:
        raise ValueError("RGBA and write mask require four channels")
    if not all(isinstance(x, int) and 0 <= x <= 255 for x in source + destination):
        raise ValueError("RGBA values must be bytes")
    if not 0 <= alpha_ref <= 255:
        raise ValueError("alpha reference must be a byte")
    if alpha_test and source[3] < alpha_ref:
        return destination
    if fog is not None:
        if len(fog) != 3 or not all(0 <= x <= 255 for x in fog):
            raise ValueError("constant fog requires three byte channels")
        source = tuple(fog) + (source[3],)
    sa, da = source[3], destination[3]
    clamp = lambda value: max(0, min(255, value))
    if not enabled:
        result = source
    elif mode == "normal":
        result = tuple((source[c] * sa + destination[c] * (255 - sa)) // 255
                       for c in range(3)) + (sa + da * (255 - sa) // 255,)
    elif mode == "add":
        result = tuple(clamp(destination[c] + source[c] * sa // 255)
                       for c in range(3)) + (clamp(da + sa),)
    elif mode == "subtract":
        result = tuple(destination[c] * (255 - source[c]) // 255 for c in range(4))
    elif mode == "max":
        result = tuple(max(source[c], destination[c]) for c in range(4))
    elif mode == "min":
        result = tuple(min(source[c], destination[c]) for c in range(4))
    elif mode == "reverse_subtract":
        result = tuple(clamp(source[c] * sa // 255 - destination[c])
                       for c in range(3)) + (sa,)
    elif mode == "custom":
        if factors is None or len(factors) not in (2, 4):
            raise ValueError("custom blend requires RGB pair or separate RGB/alpha factors")
        sf, df, saf, daf = tuple(factors) * 2 if len(factors) == 2 else factors
        result = tuple(clamp((source[c] * factor_value(saf if c == 3 else sf, source, destination, c)
                             + destination[c] * factor_value(daf if c == 3 else df, source, destination, c)) // 255)
                       for c in range(4))
    else:
        raise ValueError(f"unsupported blend mode {mode}")
    return tuple(result[c] if write_mask[c] else destination[c] for c in range(4))


class UnifiedBlendContract(unittest.TestCase):
    SOURCE = (200, 17, 0, 128)
    DESTINATION = (100, 80, 250, 64)

    def test_named_modes_known_pixels(self):
        expected = {
            "normal": (150, 48, 124, 159),
            "add": (200, 88, 250, 192),
            "subtract": (21, 74, 250, 31),
            "max": (200, 80, 250, 128),
            "min": (100, 17, 0, 64),
            "reverse_subtract": (0, 0, 0, 128),
        }
        for mode, pixel in expected.items():
            with self.subTest(mode=mode):
                self.assertEqual(blend_rgba(self.SOURCE, self.DESTINATION, mode), pixel)

    def test_one_one_is_not_ordinary_add(self):
        self.assertEqual(blend_rgba(self.SOURCE, self.DESTINATION, "custom", factors=(ONE, ONE)),
                         (255, 97, 250, 192))
        self.assertNotEqual(blend_rgba(self.SOURCE, self.DESTINATION, "custom", factors=(ONE, ONE)),
                            blend_rgba(self.SOURCE, self.DESTINATION, "add"))
        self.assertEqual(blend_rgba((200, 17, 1, 0), self.DESTINATION, "custom", factors=(ONE, ONE)),
                         (255, 97, 251, 64))

    def test_all_factor_values_against_known_vectors(self):
        rgb0 = [0, 255, 200, 55, 128, 127, 64, 191, 100, 155, 128]
        alpha = [0, 255, 128, 127, 128, 127, 64, 191, 64, 191, 255]
        for factor in range(1, 12):
            self.assertEqual(factor_value(factor, self.SOURCE, self.DESTINATION, 0), rgb0[factor - 1])
            self.assertEqual(factor_value(factor, self.SOURCE, self.DESTINATION, 3), alpha[factor - 1])
        self.assertEqual(factor_value(SRC_ALPHA_SAT, (1, 2, 3, 200), (4, 5, 6, 100), 2), 155)
        with self.assertRaises(ValueError):
            factor_value(0, self.SOURCE, self.DESTINATION, 0)
        with self.assertRaises(ValueError):
            factor_value(12, self.SOURCE, self.DESTINATION, 0)

    def test_separate_alpha_and_integer_sum_rounding(self):
        self.assertEqual(blend_rgba(self.SOURCE, self.DESTINATION, "custom",
                                   factors=(SRC_ALPHA, INV_SRC_ALPHA, ONE, INV_SRC_ALPHA)),
                         blend_rgba(self.SOURCE, self.DESTINATION, "normal"))
        # Products must be summed before floor. Two separate truncations lose1.
        self.assertEqual(blend_rgba((1, 1, 1, 128), (1, 1, 1, 128), "custom",
                                   factors=(SRC_ALPHA, SRC_ALPHA)), (1, 1, 1, 128))
        self.assertEqual(blend_rgba(self.SOURCE, self.DESTINATION, "custom",
                                   factors=(ZERO, ONE, ONE, ZERO)), (100, 80, 250, 128))

    def test_transparent_replace_subtract_and_alpha_test(self):
        transparent = (255, 100, 50, 0)
        self.assertEqual(blend_rgba(transparent, self.DESTINATION, enabled=False), transparent)
        self.assertEqual(blend_rgba(transparent, self.DESTINATION, "subtract"), (0, 48, 200, 64))
        self.assertEqual(blend_rgba(self.SOURCE, self.DESTINATION, alpha_test=True, alpha_ref=129), self.DESTINATION)
        self.assertEqual(blend_rgba(self.SOURCE, self.DESTINATION, alpha_test=True, alpha_ref=128),
                         blend_rgba(self.SOURCE, self.DESTINATION))
        self.assertEqual(blend_rgba(transparent, self.DESTINATION, enabled=False,
                                   alpha_test=True, alpha_ref=1), self.DESTINATION)
        self.assertEqual(blend_rgba(transparent, self.DESTINATION, enabled=False,
                                   alpha_test=True, alpha_ref=0), transparent)

    def test_fog_and_all_write_masks(self):
        fog = (0, 250, 12)
        expected = (49, 165, 130, 159)
        self.assertEqual(blend_rgba(self.SOURCE, self.DESTINATION, fog=fog), expected)
        for mask in itertools.product((False, True), repeat=4):
            self.assertEqual(blend_rgba(self.SOURCE, self.DESTINATION, fog=fog, write_mask=mask),
                             tuple(expected[c] if mask[c] else self.DESTINATION[c] for c in range(4)))
        self.assertEqual(blend_rgba(self.SOURCE, self.DESTINATION, fog=fog,
                                   alpha_test=True, alpha_ref=129), self.DESTINATION)

    def test_factor_pairs_at_alpha_boundaries(self):
        for sa, da, sf, df in itertools.product((0, 1, 127, 254, 255),
                                               (0, 1, 127, 254, 255), range(1, 12), range(1, 12)):
            source = (20, 129, 255, sa)
            destination = (255, 90, 1, da)
            result = blend_rgba(source, destination, "custom", factors=(sf, df))
            self.assertTrue(all(0 <= channel <= 255 for channel in result))
            self.assertEqual(blend_rgba(source, destination, "custom", factors=(ZERO, ONE)), destination)
            self.assertEqual(blend_rgba(source, destination, "custom", factors=(ONE, ZERO)), source)


if __name__ == "__main__":
    unittest.main()
