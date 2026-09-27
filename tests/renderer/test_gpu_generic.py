#!/usr/bin/env python3
"""Synthetic independent coverage for the unified general pixel packet."""
from __future__ import annotations

import itertools
import json
import os
from pathlib import Path
import random
import struct
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools"))
import am2r_gpu_reference as gpu
from test_unified_blend_contract import blend_rgba
from test_gpu_targets import command, write_fixture

TEXTURE, PACKETS, EXPORT = 0x24000000, 0x26000000, 0x27000000
ONE = 1 << 32
MODES = ("normal", "add", "max", "subtract", "min", "reverse_subtract", "custom")


def rgba(values):
    return sum(c << (8 * i) for i, c in enumerate(values))


def packet(*, textured=False, triangle=False, mode=0, enabled=True,
           mask=15, alpha_test=False, alpha_ref=0, fog=None, factors=(2, 2, 2, 2)):
    q = [0] * 64
    q[0] = 0x31504741 | int(textured) << 32 | int(triangle) << 33
    q[1] = TEXTURE | 128 << 32
    q[2] = 8 | 8 << 16 | mode << 32 | mask << 40 | alpha_ref << 48
    q[2] |= int(alpha_test) << 56 | int(fog is not None) << 57 | int(enabled) << 58
    q[3] = rgba(factors) | (rgba((*fog, 0)) << 32 if fog else 0)
    for c in range(4):
        q[23 + c * 4] = ONE
    return q


def encoded(q):
    return struct.pack("<64Q", *(v & 0xFFFFFFFFFFFFFFFF for v in q))


def draw(q, width, height, *, x=0, y=0, background=0x73625140, texture=None):
    commands = (command(1, base=background) + command(13, width, height, base=PACKETS, x=x, y=y)
                + command(6, base=EXPORT) + command(0))
    regions = {PACKETS: encoded(q)}
    if texture is not None:
        regions[TEXTURE] = texture
    return gpu.render(commands, regions)


def generic_fixture():
    """Disjoint labelled small draws stress state transitions and interpolation."""
    rng = random.Random(0xA6F1)
    texture = bytes(rng.randrange(256) for _ in range(8 * 128))
    packets = bytearray()
    commands = [command(1, base=0x73625140)]
    cases = []
    for index in range(100):
        q = packet(textured=index % 3 != 0, triangle=index % 4 == 0,
                   mode=index % 7, enabled=index % 13 != 0, mask=index % 16,
                   alpha_test=index % 3 == 0, alpha_ref=(index * 37) % 256,
                   fog=(45, 197, 128) if index % 5 == 0 else None,
                   factors=tuple((index + k * 7) % 11 + 1 for k in range(4)))
        # Inclusive triangle x+y<=7 at sample centres; bounding quad 8x8.
        q[8:17] = [ONE // 2, ONE, 0, ONE // 2, 0, ONE,
                    6 * ONE, -ONE, -ONE]
        q[17:23] = [-2 * ONE + ONE // 8, ONE * 3 // 2, -ONE // 8,
                    7 * ONE + ONE // 2, ONE // 8, -ONE // 2]
        for c in range(4):
            q[23 + c * 4:27 + c * 4] = [ONE * (c + 1) // 8,
                ONE // 16, (-ONE if c % 2 else ONE) // 32,
                (-ONE if c == 3 else ONE) // 128]
        base = PACKETS + len(packets)
        packets += encoded(q)
        x, y = (index % 20) * 16 + (index & 1), (index // 20) * 40 + 3
        commands.append(command(13, 8, 8, base=base, x=x, y=y))
        cases.append((x, y, q))
    commands.extend((command(6, base=EXPORT), command(0)))
    return b"".join(commands), {TEXTURE: texture, PACKETS: bytes(packets)}, cases


class GenericPackets(unittest.TestCase):
    def test_all_modes_factors_transparency_and_masks_against_independent_spec(self):
        rng = random.Random(1915)
        for index in range(2000):
            src = tuple(rng.randrange(256) for _ in range(4))
            dst = tuple(rng.randrange(256) for _ in range(4))
            mode, mask = index % 7, index % 16
            factors = tuple(rng.randrange(1, 12) for _ in range(4))
            alpha_test, alpha_ref = index % 3 == 0, index % 256
            fog = (1, 129, 237) if index % 5 == 0 else None
            enabled = index % 11 != 0
            q = packet(mode=mode, mask=mask, factors=factors, alpha_test=alpha_test,
                       alpha_ref=alpha_ref, fog=fog, enabled=enabled)
            expected = blend_rgba(src, dst, MODES[mode], factors=factors,
                enabled=enabled, alpha_test=alpha_test, alpha_ref=alpha_ref,
                fog=fog, write_mask=tuple(bool(mask & (1 << c)) for c in range(4)))
            self.assertEqual(gpu.blend_generic(rgba(src), rgba(dst), q[2], q[3] & 0xFFFFFFFF, q[3] >> 32), rgba(expected))

    def test_bilinear_colour_is_not_quantized_to_byte_tint(self):
        q = packet(enabled=False)
        for c in range(4):
            q[23 + c * 4:27 + c * 4] = [ONE // 3, ONE // 32, ONE // 16, -ONE // 256]
        result = draw(q, 9, 7)
        for y in range(7):
            for x in range(9):
                fixed = ONE // 3 + x * (ONE // 32) + y * (ONE // 16) - x * y * (ONE // 256)
                channel = (255 * fixed) // ONE
                self.assertEqual(result.pixels[y * 320 + x], rgba((channel,) * 4))

    def test_triangle_coverage_uses_all_three_edges_and_pixel_centres(self):
        q = packet(triangle=True, enabled=False)
        q[8:17] = [ONE // 2, ONE, 0, ONE // 2, 0, ONE, 3 * ONE, -ONE, -ONE]
        result = draw(q, 8, 8, background=0)
        for y in range(8):
            for x in range(8):
                self.assertEqual(result.pixels[y * 320 + x], 0xFFFFFFFF if x + y <= 3 else 0)

    def test_texture_clamp_negative_and_high_coordinates_preserves_zero_alpha_rgb(self):
        q = packet(textured=True, enabled=False)
        texture = b"".join(struct.pack("<I", rgba((x * 17, y * 23, x ^ y, 0)))
                           for y in range(8) for x in range(32))
        q[17:23] = [-2 * ONE, 2 * ONE, 0, 9 * ONE, 0, -2 * ONE]
        result = draw(q, 7, 7, texture=texture)
        for y in range(7):
            for x in range(7):
                sx, sy = min(7, max(0, 2 * x - 2)), min(7, max(0, 9 - 2 * y))
                self.assertEqual(result.pixels[y * 320 + x], rgba((sx * 17, sy * 23, sx ^ sy, 0)))

    def test_packet_and_descriptor_validation(self):
        for word, value in ((0, 0), (0, 0x31504741 | 4 << 32), (4, 1), (63, 1),
                            (2, 7 << 32), (2, 1 << 59), (2, 1 << 44)):
            q = packet()
            q[word] = value
            with self.subTest(word=word, value=value), self.assertRaises(gpu.CaptureError):
                draw(q, 1, 1)
        q = packet(textured=True)
        q[1] = TEXTURE | 4 << 32
        with self.assertRaises(gpu.CaptureError):
            draw(q, 1, 1, texture=bytes(1024))

    def test_synthetic_state_transition_fixture_replays(self):
        commands, regions, _ = generic_fixture()
        result = gpu.render(commands, regions)
        self.assertEqual(result.opcode_counts[13], 100)
        self.assertEqual(result.exports[EXPORT], gpu.pixel_bytes(result.pixels))

    @unittest.skipUnless(os.getenv("AM2R_TEST_RTL") == "1", "set AM2R_TEST_RTL=1")
    def test_rtl_generic_packets_under_two_ddr_schedules(self):
        parent = ROOT / "data/work/unified-renderer-20260925"
        parent.mkdir(parents=True, exist_ok=True)
        directory = Path(tempfile.mkdtemp(prefix="generic-reference-", dir=parent))
        commands, regions, _ = generic_fixture()
        path = write_fixture(directory, commands, regions)
        for latency, stall, gap in ((2, 11, 0), (5, 7, 3)):
            result = subprocess.run([sys.executable, str(ROOT / "tools/replay_gpu_capture.py"),
                str(path), "--output", str(directory), "--read-latency", str(latency),
                "--stall-period", str(stall), "--response-gap", str(gap)],
                capture_output=True, text=True, timeout=240)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)


if __name__ == "__main__":
    unittest.main()
