#!/usr/bin/env python3
"""Synthetic-only ABI regressions for the independent GPU frame reference."""

from __future__ import annotations

import contextlib
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import random
import struct
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location("am2r_gpu_reference", ROOT / "tools/am2r_gpu_reference.py")
gpu = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = gpu
SPEC.loader.exec_module(gpu)
REPLAY_SPEC = importlib.util.spec_from_file_location("replay_gpu_capture", ROOT / "tools/replay_gpu_capture.py")
replay = importlib.util.module_from_spec(REPLAY_SPEC)
REPLAY_SPEC.loader.exec_module(replay)

TEXTURE, TABLE, EXPORT, NATIVE = 0x24000000, 0x26000000, 0x27000000, 0x3A000100
WHITE, BLACK, RED, GREEN, BLUE = 0xFFFFFFFF, 0xFF000000, 0xFF0000FF, 0xFF00FF00, 0xFFFF0000


def command(op, *, width=0, height=0, blend=0, floor_tint=False, **words):
    data = [0] * 8
    data[0] = op | (blend << 8) | (int(floor_tint) << 10) | (width << 16) | (height << 32)
    for name, value in words.items():
        data[int(name[1:])] = value & 0xFFFFFFFFFFFFFFFF
    return struct.pack("<8Q", *data)


def pair(low, high):
    return (low & 0xFFFFFFFF) | ((high & 0xFFFFFFFF) << 32)


def xy(x, y):
    return (x & 65535) | ((y & 65535) << 16)


def texture_blit(**overrides):
    fields = dict(width=1, height=1, w1=pair(TEXTURE, 16), w2=0,
                  w4=0, w5=pair(65536, 65536), w6=WHITE)
    fields.update(overrides)
    return command(2, **fields)


def frame(*commands, color=BLACK):
    return command(1, w1=color) + b"".join(commands) + command(0)


def floor_tint_fixture():
    # Every RGBA lane matters, including RGB from alpha-zero texels. Odd width
    # and odd destination cover paired bank crossing plus the scalar tail.
    pixels = [0x001D0101, 0x027F0381, 0xBF40FE03, 0x7F01FF7F,
              0xFF012503, 0x017F7F7F, 0xCDAF594D, 0xFFFFFEFE] * 4
    tint, background = 0x80817F80, 0xF3D5B799
    axis = dict(width=7, height=2, blend=2, w1=pair(TEXTURE, 32), w6=tint)
    commands = frame(texture_blit(floor_tint=True, w2=xy(1, 1), **axis),
                     texture_blit(w2=xy(10, 1), **axis),
                     texture_blit(floor_tint=True, w2=xy(1, 4), **axis),
                     command(5, blend=2, w1=tint),
                     command(4, width=7, height=2, w1=pair(TEXTURE, 32), w2=xy(20, 4),
                             w3=pair(0, 8 << 16), w4=pair(0, 2 << 16),
                             w5=0, w6=pair(65536, 0), w7=pair(0, 65536)),
                     command(6, w1=EXPORT), color=background)
    return commands, struct.pack("<32I", *pixels), tint, background


def arbitrary_axis_pair_fixture():
    """Distinct source texels expose either wrong lane or unit-step fast-loop reuse.

    Padded allocations make negative and wrapped signed UVs valid addresses.
    One variant has opaque alpha and another binary alpha, so white-tint fast
    paths are exercised as well as all RGBA lanes in ordinary tinted blends.
    """
    regions = {}
    for kind in range(3):
        pixels = []
        for index in range(131072):
            # Every cache-line position and row has an unrelated RGB signature.
            value = ((index + 1) * 0x45D9F3B) & 0xFFFFFFFF
            value ^= value >> 16
            alpha = 255 if kind == 1 else (255 if index % 3 else 0) if kind == 2 else (index * 73 + 19) & 255
            pixels.append((value & 0xFFFFFF) | (alpha << 24))
        regions[TEXTURE + kind * 0x100000] = struct.pack("<131072I", *pixels)
    geometries = [
        ("repeat-low", 8 * 65536 + 16384, 0, 19),
        ("repeat-high", 9 * 65536 + 49152, 0, 19),
        ("half-forward", 7 * 65536 + 49152, 32768, 31),
        ("half-reverse", 45 * 65536 + 16384, -32768, 31),
        ("line-forward", 30 * 65536 + 49152, 32768, 9),
        ("line-reverse", 32 * 65536 + 16384, -32768, 9),
        ("unit", 32768, 65536, 37),
        ("mirror", 53 * 65536 + 32768, -65536, 37),
        ("skip-forward", 65536 + 16384, 98304, 37),
        ("skip-reverse", 60 * 65536 + 49152, -98304, 37),
        ("sparse-forward", 65536 + 16384, 163840, 25),
        ("sparse-reverse", 61 * 65536 + 49152, -163840, 25),
        ("step-four", 65536 + 32768, 262144, 15),
        ("mirror-four", 61 * 65536 + 32768, -262144, 15),
        ("near-unit", 15 * 65536 + 32768, 65535, 31),
        ("wrap-positive", 0x7FFF8000, 65536, 9),
        ("wrap-negative", -0x7FFF8000, -65536, 9),
        ("negative-u", -3 * 65536 - 32768, 32768, 15),
        ("wrap-stream", 0x7FFE8000, 65536, 9),
    ]
    styles = [
        ("opaque", 0, False, WHITE, 1),
        ("white-alpha", 0, False, 0x80FFFFFF, 2),
        ("normal-round", 0, False, 0x80817F80, 0),
        ("normal-floor", 0, True, 0x80817F80, 0),
        ("add-round", 1, False, 0x80817F80, 0),
        ("add-floor", 1, True, 0x80817F80, 0),
        ("subtract-round", 2, False, 0x80817F80, 0),
        ("subtract-floor", 2, True, 0x80817F80, 0),
    ]
    cases = []

    def add(name, style, x, y, width, height, u, du, ordinal):
        label, blend, floor_tint, tint, kind = style
        cases.append(dict(name=name + "/" + label, x=x, y=y, width=width, height=height,
                          # +8 makes the wrapping unit pair aligned to 64 bits,
                          # while physical +8 stays in its old 128-byte line.
                          base=TEXTURE + kind * 0x100000 + 0x40000 +
                               (8 if name == "wrap-stream" else 4 * (ordinal & 1)),
                          stride=260 if ordinal % 3 else 256,
                          u=u, v=3 * 65536 + 49152, du=du,
                          dv=(65536, -32768, 98304)[ordinal % 3],
                          blend=blend, floor_tint=floor_tint, tint=tint))

    for ordinal, (name, u, du, width) in enumerate(geometries):
        for column, style in enumerate(styles):
            add(name, style, column * 40 + (ordinal & 1), 10 + ordinal * 10,
                width, 3, u, du, ordinal)
    for column, style in enumerate(styles):
        add("clip-left", style, -3, 200 + column * 4, 9, 3,
            30 * 65536 + 49152, 32768, column)
        add("clip-right", style, 317, 200 + column * 4, 9, 3,
            34 * 65536 + 16384, -98304, column)
        add("clip-top", style, 40 + column * 34, -2, 31, 5,
            53 * 65536 + 32768, -65536, column)
        add("clip-bottom", style, 40 + column * 34, 238, 31, 5,
            7 * 65536 + 49152, 32768, column)
    commands = frame(*(texture_blit(
        width=case["width"], height=case["height"], blend=case["blend"],
        floor_tint=case["floor_tint"], w1=pair(case["base"], case["stride"]),
        w2=xy(case["x"], case["y"]), w4=pair(case["u"], case["v"]),
        w5=pair(case["du"], case["dv"]), w6=case["tint"]) for case in cases),
        command(6, w1=EXPORT), color=0x93D5B799)
    return commands, regions, cases, 0x93D5B799


class PixelArithmetic(unittest.TestCase):
    def test_tint_nearest_exhaustive_channels(self):
        for source in range(256):
            for tint in range(256):
                expected = round(source * tint / 255)
                self.assertEqual(gpu.tint_pixel(source, tint) & 255, expected)

    def test_tint_floor_exhaustive_channels(self):
        for source in range(256):
            for tint in range(256):
                self.assertEqual(gpu.tint_pixel(source, tint, floor=True) & 255,
                                 source * tint // 255)

    def test_floor_blends_and_alpha_lanes(self):
        self.assertEqual(gpu.blend_pixel(0x800000FF, 0x4000FF00), 0x9F007F80)
        self.assertEqual(gpu.blend_pixel(0x80FFFFFF, 0xF0F0F0F0, 1), WHITE)
        self.assertEqual(gpu.blend_pixel(0x004080FF, 0xC8C8C8C8, 2), 0xC8956300)
        self.assertEqual(gpu.blend_pixel(0x00FFFFFF, WHITE, 2), 0xFF000000)

    def test_random_channel_math(self):
        generator = random.Random(0xA2B177)
        for _ in range(3000):
            source, destination = generator.getrandbits(32), generator.getrandbits(32)
            src = [(source >> shift) & 255 for shift in (0, 8, 16, 24)]
            dst = [(destination >> shift) & 255 for shift in (0, 8, 16, 24)]
            expected = [int((src[i] * src[3] + dst[i] * (255 - src[3])) / 255) for i in range(3)]
            expected.append(src[3] + int(dst[3] * (255 - src[3]) / 255))
            self.assertEqual(gpu.blend_pixel(source, destination), sum(v << (i * 8) for i, v in enumerate(expected)))

    def test_present_swizzle_is_lossy_alpha(self):
        self.assertEqual(gpu.rgba_to_xrgb(0x12345678), 0x00785634)
        self.assertEqual(gpu.xrgb_to_rgba(0xEE785634), 0xFF345678)


class DescriptorReplay(unittest.TestCase):
    def test_arbitrary_axis_pair_sampling_and_all_blends(self):
        commands, regions, cases, background = arbitrary_axis_pair_fixture()
        expected = [background] * gpu.PIXELS
        self.assertEqual(len(cases), 184)
        for case in cases:
            for row in range(max(0, -case["y"]), min(case["height"], 240 - case["y"])):
                for column in range(max(0, -case["x"]), min(case["width"], 320 - case["x"])):
                    # Independent signed-wrap address and channel arithmetic,
                    # without calling the reference's sampling/tint helpers.
                    u = ((case["u"] + column * case["du"] + 2**31) % 2**32) - 2**31
                    v = ((case["v"] + row * case["dv"] + 2**31) % 2**32) - 2**31
                    address = (case["base"] + (v // 65536) * case["stride"] + (u // 65536) * 4) % 2**32
                    region_base = max(base for base in regions if base <= address)
                    source = struct.unpack_from("<I", regions[region_base], address - region_base)[0]
                    source_lanes = [(((source >> shift) & 255) * ((case["tint"] >> shift) & 255) +
                                     (0 if case["floor_tint"] else 127)) // 255
                                    for shift in (0, 8, 16, 24)]
                    index = (case["y"] + row) * 320 + case["x"] + column
                    destination = [(expected[index] >> shift) & 255 for shift in (0, 8, 16, 24)]
                    alpha = source_lanes[3]
                    if case["blend"] == 2:
                        channels = [destination[i] * (255 - source_lanes[i]) // 255 for i in range(4)]
                    elif case["blend"] == 1:
                        channels = [min(255, destination[i] + source_lanes[i] * alpha // 255)
                                    for i in range(3)] + [min(255, destination[3] + alpha)]
                    else:
                        channels = [(source_lanes[i] * alpha + destination[i] * (255 - alpha)) // 255
                                    for i in range(3)] + [alpha + destination[3] * (255 - alpha) // 255]
                    expected[index] = sum(lane << (8 * i) for i, lane in enumerate(channels))
        result = gpu.render(commands, regions)
        self.assertEqual(result.pixels, expected)
        self.assertEqual(gpu.read_pixels(result.exports[EXPORT]), expected)

    def test_floor_tint_exact_rgba_and_descriptor_state_reset(self):
        commands, texture, tint, background = floor_tint_fixture()
        result = gpu.render(commands, {TEXTURE: texture})
        exported = gpu.read_pixels(result.exports[EXPORT])
        self.assertEqual(exported, result.pixels)
        for row in range(2):
            for col in range(7):
                source = struct.unpack_from("<I", texture, (row * 8 + col) * 4)[0]
                for x, y, rounding in [(1, 1, 0), (10, 1, 127), (1, 4, 0), (20, 4, 127)]:
                    expected = 0
                    for shift in (0, 8, 16, 24):
                        product = ((source >> shift) & 255) * ((tint >> shift) & 255)
                        channel = ((background >> shift) & 255) * (255 - (product + rounding) // 255) // 255
                        expected |= channel << shift
                    self.assertEqual(exported[(y + row) * 320 + x + col], expected)
        self.assertNotEqual(exported[321], exported[330])

    def test_clear_preserves_rgba_alpha_and_present_swizzle(self):
        result = gpu.render(frame(color=0x12345678), {})
        self.assertEqual(result.pixels, [0x12345678] * gpu.PIXELS)
        self.assertEqual(result.xrgb_pixels[123], 0x785634)
        self.assertEqual(result.opcode_counts, {1: 1, 0: 1})

    def test_axis_opaque_transparent_partial_alpha(self):
        source = struct.pack("<4I", RED, GREEN, 0x000000FF, 0x80FF0000)
        result = gpu.render(frame(texture_blit(width=4), color=0xFF201008), {TEXTURE: source})
        self.assertEqual(result.pixels[:4], [RED, GREEN, 0xFF201008, 0xFF8F0703])

    def test_negative_clipping_still_advances_uv(self):
        source = struct.pack("<4I", RED, GREEN, BLUE, WHITE)
        result = gpu.render(frame(texture_blit(width=4, w2=xy(-2, 0))), {TEXTURE: source})
        self.assertEqual(result.pixels[:3], [BLUE, WHITE, BLACK])

    def test_reversed_and_scaled_nearest_sampling(self):
        source = struct.pack("<4I", RED, GREEN, BLUE, WHITE)
        reverse = texture_blit(width=4, w4=3 << 16, w5=pair(-65536, 0))
        half = texture_blit(width=2, w2=xy(0, 1), w5=pair(131072, 0))
        double = texture_blit(width=4, w2=xy(0, 2), w5=pair(32768, 0))
        result = gpu.render(frame(reverse, half, double), {TEXTURE: source})
        self.assertEqual(result.pixels[:4], [WHITE, BLUE, GREEN, RED])
        self.assertEqual(result.pixels[320:322], [RED, BLUE])
        self.assertEqual(result.pixels[640:644], [RED, RED, GREEN, GREEN])

    def test_fill_gradient_signed_clamping_and_row_origin(self):
        gradient = command(3, width=2, height=5, w1=RED, w2=xy(0, -1),
                           w7=0xC000000040008000)
        result = gpu.render(frame(gradient, color=0xFF201008), {})
        # At logical row 1: RGBA (127,64,0,191), blended over (8,16,32,255).
        self.assertEqual(result.pixels[0], 0xFF083361)
        self.assertEqual(result.pixels[320], 0xFF104704)
        self.assertEqual(result.pixels[640], 0xFF183B06)
        self.assertEqual(result.pixels[960], 0xFF201008)

    def test_tinted_subtract_uses_nearest_tint_and_ignores_source_alpha(self):
        source = struct.pack("<I", 0x00FEFEFE)
        result = gpu.render(frame(texture_blit(w6=0x00808080, blend=2), color=0xFF3C3C3C), {TEXTURE: source})
        # round(254*128/255)=127; floor(60*(255-127)/255)=30.
        self.assertEqual(result.pixels[0], 0xFF1E1E1E)

    def test_affine_boundaries_and_one_shot_state(self):
        source = struct.pack("<4I", RED, GREEN, BLUE, WHITE)
        affine = dict(width=3, height=1, w1=pair(TEXTURE, 16),
                      w3=pair(0, 2 << 16), w4=pair(0, 1 << 16),
                      w5=pair(-1 << 16, 0), w6=pair(65536, 0), w7=0)
        setup = command(5, blend=1, w1=0x80FFFFFF)
        result = gpu.render(frame(setup, command(4, **affine),
                                  command(4, w2=xy(0, 1), **affine)), {TEXTURE: source})
        self.assertEqual(result.pixels[:3], [BLACK, 0xFF000080, 0xFF008000])
        self.assertEqual(result.pixels[320:323], [BLACK, RED, GREEN])

    def test_affine_rotation_signed_derivatives(self):
        source = struct.pack("<4I", RED, GREEN, BLUE, WHITE)
        affine = command(4, width=2, height=2, w1=pair(TEXTURE, 8),
                         w3=pair(0, 2 << 16), w4=pair(0, 2 << 16),
                         w5=pair(0, 65536), w6=pair(0, -65536), w7=pair(65536, 0))
        result = gpu.render(frame(affine), {TEXTURE: source})
        self.assertEqual(result.pixels[:2], [BLUE, RED])
        self.assertEqual(result.pixels[320:322], [WHITE, GREEN])

    def test_exports_are_ordered_texture_snapshots_not_final_present(self):
        commands = frame(command(6, w1=EXPORT), command(3, width=1, height=1, w1=RED),
                         texture_blit(w1=pair(EXPORT, 1280), w2=xy(1, 0)),
                         command(6, w1=EXPORT + gpu.FRAME_BYTES), color=0x80705030)
        result = gpu.render(commands, {})
        self.assertEqual(struct.unpack_from("<I", result.exports[EXPORT])[0], 0x80705030)
        self.assertEqual(struct.unpack_from("<I", result.exports[EXPORT + gpu.FRAME_BYTES])[0], RED)
        # Partial-alpha source-over of same RGB retains RGB and changes alpha.
        self.assertEqual(result.pixels[1], 0xBF705030)

    def test_later_export_replaces_earlier_same_address(self):
        result = gpu.render(frame(command(6, w1=EXPORT), command(1, w1=RED),
                                  command(6, w1=EXPORT), command(1, w1=GREEN),
                                  texture_blit(w1=pair(EXPORT, 1280))), {})
        self.assertEqual(result.pixels[0], RED)
        self.assertEqual(gpu.read_pixels(result.exports[EXPORT])[0], RED)

    def test_overlapping_export_ranges_report_final_memory(self):
        result = gpu.render(frame(command(6, w1=EXPORT), command(1, w1=RED),
                                  command(6, w1=EXPORT + 8)), {})
        first = gpu.read_pixels(result.exports[EXPORT])
        self.assertEqual(first[:4], [BLACK, BLACK, RED, RED])
        self.assertEqual(first[-1], RED)

    def test_water_rows_odd_source_destination_alpha_only(self):
        row = struct.pack("<320I", RED, GREEN, 0, BLUE, *([WHITE] * 316))
        table = struct.pack("<4H", 3, 3, 1, 0)
        water = command(7, width=1, w1=pair(TEXTURE, 1280), w2=pair(TABLE, 1), w6=0x80000000)
        result = gpu.render(frame(water), {TEXTURE: row, TABLE: table})
        self.assertEqual(result.pixels[323:326], [0xFF008000, BLACK, 0xFF800000])

    def test_native_water_uses_completed_native_not_descriptor(self):
        prior = struct.pack("<320I", 0x00010203, *([0] * 319))
        table = struct.pack("<4H", 0, 1, 0, 0)
        native = command(8, width=1, w1=0xDEADBEEF, w2=TABLE, w6=WHITE)
        result = gpu.render(frame(native), {NATIVE: prior, TABLE: table}, native_source_base=NATIVE)
        self.assertEqual(result.pixels[0], 0xFF030201)

    def test_existing_rtl_lighting_golden_vector(self):
        # Same synthetic descriptors and literal expected pixels as the final
        # lighting job in tests/rtl/am2r_gpu_tb.sv; no RTL helper is imported.
        source = struct.pack("<4I", 0xFF808080, 0x00808080, 0x80808080, 0)
        axis = texture_blit(width=3, blend=2, w2=xy(1, 1))
        solid = command(3, width=3, height=1, blend=2, w1=0x00808080, w2=xy(1, 2))
        affine = command(4, width=2, height=1, w1=pair(TEXTURE, 16), w2=xy(1, 3),
                         w3=pair(0, 4 << 16), w4=pair(0, 1 << 16),
                         w6=pair(65536, 0), w7=pair(0, 65536))
        result = gpu.render(frame(axis, solid, command(5, blend=2, w1=WHITE), affine,
                                  command(6, w1=EXPORT), color=0xFF64503C), {TEXTURE: source})
        for x, y in [(1, 1), (2, 1), (3, 1), (1, 2), (2, 2), (3, 2), (1, 3), (2, 3)]:
            self.assertEqual(result.xrgb_pixels[y * 320 + x], 0x001D2731)
        exported = gpu.read_pixels(result.exports[EXPORT])
        self.assertEqual(exported[321:324], [0x0031271D, 0xFF31271D, 0x7F31271D])
        self.assertEqual(exported[643], 0xFF31271D)

    def test_tiled_additive_phase_wrap_alpha_only(self):
        tile = struct.pack("<32I", *[0xFF000000 | i for i in range(32)])
        tiled = command(9, width=5, height=1, blend=1, w1=pair(TEXTURE, 128),
                        w3=30, w5=pair(65536, 65536), w6=0x80000000)
        result = gpu.render(frame(tiled), {TEXTURE: tile})
        self.assertEqual(result.pixels[:5], [0xFF00000F, 0xFF00000F, BLACK, BLACK, 0xFF000001])

    def test_uninitialized_bram_rejected_and_explicit_initial_accepted(self):
        with self.assertRaisesRegex(gpu.CaptureError, "initial BRAM"):
            gpu.render(command(0), {})
        with self.assertRaisesRegex(gpu.CaptureError, "initial BRAM"):
            gpu.render(command(3, width=1, height=1, w1=0x800000FF) + command(0), {})
        result = gpu.render(command(0), {}, [0x80010203] * gpu.PIXELS)
        self.assertEqual(result.pixels[0], 0x80010203)
        # Fully opaque overwrites are valid initialization without a CLEAR.
        result = gpu.render(command(3, width=320, height=240, w1=WHITE) + command(0), {})
        self.assertEqual(result.pixels[0], WHITE)

    def test_rejects_malformed_descriptors_and_references(self):
        cases = [b"", b"x" * 65, command(42), command(1), command(0) + command(1),
                 frame(command(3, width=1, height=1, blend=3)),
                 frame(command(2, width=1, height=1, blend=3, floor_tint=True)),
                 frame(command(3, width=1, height=1, floor_tint=True)),
                 frame(command(5, w1=WHITE)),
                 frame(command(2, width=1, height=1)),
                 frame(command(3, width=0, height=1)),
                 frame(command(3, width=65535, height=65535)),
                 frame(command(6, w1=EXPORT + 1)),
                 frame(command(7, width=241)), frame(command(8, width=1, w2=TABLE)),
                 frame(command(9, width=321, height=1))]
        for commands in cases:
            with self.subTest(commands=commands[:16]), self.assertRaises(gpu.CaptureError):
                gpu.render(commands, {TABLE: bytes(8)})
        for regions in ({1: b"a", 0: b"bb"}, {2**32: b"x"}, {0: b""}):
            with self.assertRaises(gpu.CaptureError):
                gpu.render(frame(), regions)


class ManifestReplay(unittest.TestCase):
    def fixture(self, directory, mismatch=False):
        commands = frame(command(6, w1=EXPORT), color=0x12345678)
        (directory / "commands.bin").write_bytes(commands)
        expected = [0x785634] * gpu.PIXELS
        if mismatch:
            expected[321] ^= 1
        (directory / "expected.bin").write_bytes(gpu.pixel_bytes(expected))
        (directory / "export.bin").write_bytes(gpu.pixel_bytes([0x12345678] * gpu.PIXELS))
        manifest = {"format": gpu.FORMAT,
                    "commands": {"file": "commands.bin", "sha256": hashlib.sha256(commands).hexdigest()},
                    "regions": [], "expected": {"file": "expected.bin", "format": "xrgb8888"},
                    "expected_exports": [{"base": hex(EXPORT), "file": "export.bin"}]}
        (directory / "manifest.json").write_text(json.dumps(manifest))
        return manifest

    def test_cli_success_export_comparison_and_output(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            self.fixture(directory)
            stdout = io.StringIO()
            with contextlib.redirect_stdout(stdout):
                code = gpu.main([str(directory / "manifest.json"), "--output", str(directory / "results")])
            self.assertEqual(code, 0)
            report = json.loads(stdout.getvalue())
            self.assertEqual(report["comparison_status"], "match")
            self.assertTrue((directory / "results/reference.ppm").is_file())
            self.assertEqual(report["exports"][hex(EXPORT)]["different_pixels"], 0)
            self.assertEqual(report["cpu_comparison_status"], "not_compared")
            self.assertEqual(report["cpu_comparison_coverage"]["compared_cpu_reference_count"], 0)

    def test_cpu_crop_is_compared_directly_with_hardware_rgba(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            manifest = self.fixture(directory)
            (directory / "cpu-export.bin").write_bytes((directory / "export.bin").read_bytes())
            manifest["expected_exports"][0]["cpu_reference"] = "cpu-export.bin"
            (directory / "manifest.json").write_text(json.dumps(manifest))
            stdout = io.StringIO()
            with contextlib.redirect_stdout(stdout):
                self.assertEqual(gpu.main([str(directory / "manifest.json")]), 0)
            report = json.loads(stdout.getvalue())
            self.assertEqual(report["comparison_status"], "match")
            self.assertEqual(report["cpu_comparison_status"], "match")
            self.assertEqual(report["cpu_vs_captured_exports"][hex(EXPORT)]["different_pixels"], 0)
            coverage = report["cpu_comparison_coverage"]
            self.assertEqual(coverage["scope"], "declared 320x240 RGBA export crops")
            self.assertFalse(coverage["full_logical_surfaces_compared"])
            self.assertEqual(coverage["compared_cpu_reference_count"], 1)
            self.assertEqual(coverage["exports_without_cpu_reference"], [])

    def test_cpu_alpha_only_mismatch_fails_even_when_gpu_reference_matches_hardware(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            manifest = self.fixture(directory)
            cpu = gpu.read_pixels((directory / "export.bin").read_bytes())
            cpu[321] ^= 0x01000000
            (directory / "cpu-export.bin").write_bytes(gpu.pixel_bytes(cpu))
            manifest["expected_exports"][0]["cpu_reference"] = "cpu-export.bin"
            (directory / "manifest.json").write_text(json.dumps(manifest))
            stdout = io.StringIO()
            with contextlib.redirect_stdout(stdout):
                self.assertEqual(gpu.main([str(directory / "manifest.json")]), 1)
            report = json.loads(stdout.getvalue())
            self.assertEqual(report["gpu_comparison_status"], "match")
            self.assertEqual(report["comparison_status"], "mismatch")
            self.assertEqual(report["cpu_comparison_status"], "mismatch")
            comparison = report["cpu_vs_captured_exports"][hex(EXPORT)]
            self.assertEqual(comparison["different_pixels"], 1)
            self.assertEqual(comparison["bounds"], [1, 1, 1, 1])

    def test_cpu_reference_coverage_does_not_include_other_exports(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            manifest = self.fixture(directory)
            data = (directory / "export.bin").read_bytes()
            (directory / "cpu-export.bin").write_bytes(data)
            manifest["expected_exports"][0]["cpu_reference"] = "cpu-export.bin"
            second = EXPORT + gpu.FRAME_BYTES
            manifest["expected_exports"].append({"base": second, "file": "export.bin"})
            comparisons, coverage = gpu.compare_cpu_captured_exports(
                manifest, directory, {EXPORT: data, second: data})
            self.assertEqual(set(comparisons), {hex(EXPORT)})
            self.assertEqual(coverage["produced_export_count"], 2)
            self.assertEqual(coverage["compared_cpu_reference_count"], 1)
            self.assertEqual(coverage["exports_without_cpu_reference"], [hex(second)])
            self.assertFalse(coverage["complete"])
            self.assertEqual(gpu.captured_comparison_status(list(comparisons.values()), coverage), "partial_match")

    def test_declared_cpu_references_are_strictly_validated(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            manifest = self.fixture(directory)
            produced = {EXPORT: (directory / "export.bin").read_bytes()}
            for value in (None, 42, False, {}, [], "", "../export.bin", "missing.bin", str(directory / "export.bin")):
                with self.subTest(value=value):
                    manifest["expected_exports"][0]["cpu_reference"] = value
                    with self.assertRaises(gpu.CaptureError):
                        gpu.compare_cpu_captured_exports(manifest, directory, produced)
            for size in (gpu.FRAME_BYTES - 4, gpu.FRAME_BYTES + 4):
                with self.subTest(size=size):
                    (directory / "cpu-export.bin").write_bytes(bytes(size))
                    manifest["expected_exports"][0]["cpu_reference"] = "cpu-export.bin"
                    with self.assertRaises(gpu.CaptureError):
                        gpu.compare_cpu_captured_exports(manifest, directory, produced)

    def test_rtl_replay_cpu_mismatch_fails_despite_model_and_hardware_agreement(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            manifest = self.fixture(directory)
            result = gpu.render((directory / "commands.bin").read_bytes(), {})
            cpu = gpu.read_pixels(result.exports[EXPORT])
            cpu[5] ^= 0x01000000
            (directory / "cpu-export.bin").write_bytes(gpu.pixel_bytes(cpu))
            manifest["expected_exports"][0]["cpu_reference"] = "cpu-export.bin"

            def fake_run(command, work, name, timeout):
                if name == "vsim":
                    (work / "rtl-status.txt").write_text("1000 900 1")
                    return "PASS: captured GPU job completed"
                return ""

            def fake_frame(path):
                return result.xrgb_pixels if path.name == "rtl-native.hex" else gpu.read_pixels(result.exports[EXPORT])

            with mock.patch.object(replay.shutil, "which", return_value="mock-tool"), \
                 mock.patch.object(replay, "prepare", return_value=(result, manifest, 1000, [EXPORT])), \
                 mock.patch.object(replay, "_run", side_effect=fake_run), \
                 mock.patch.object(replay, "_read_frame", side_effect=fake_frame), \
                 contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(replay.main([str(directory / "manifest.json"), "--output", str(directory)]), 1)
            report = json.loads(next(directory.glob("gpu-replay-*/comparison.json")).read_text())
            self.assertTrue(report["reference_vs_rtl"]["matching"])
            self.assertEqual(report["captured_comparison_status"], "match")
            self.assertEqual(report["cpu_comparison_status"], "mismatch")
            self.assertEqual(report["cpu_vs_captured_exports"][hex(EXPORT)]["different_pixels"], 1)
            self.assertFalse(report["matching"])

    def test_rtl_replay_missing_cpu_reference_rejects_before_simulation(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            manifest = self.fixture(directory)
            result = gpu.render((directory / "commands.bin").read_bytes(), {})
            manifest["expected_exports"][0]["cpu_reference"] = "missing.bin"
            with mock.patch.object(replay.shutil, "which", return_value="mock-tool"), \
                 mock.patch.object(replay, "prepare", return_value=(result, manifest, 1000, [EXPORT])), \
                 mock.patch.object(replay, "_run") as simulator, \
                 contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
                with self.assertRaises(SystemExit) as raised:
                    replay.main([str(directory / "manifest.json"), "--output", str(directory)])
            self.assertEqual(raised.exception.code, 2)
            simulator.assert_not_called()

    def test_cli_mismatch_reports_pixel_and_returns_failure(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            self.fixture(directory, mismatch=True)
            stdout = io.StringIO()
            with contextlib.redirect_stdout(stdout):
                code = gpu.main([str(directory / "manifest.json")])
            self.assertEqual(code, 1)
            report = json.loads(stdout.getvalue())
            self.assertEqual(report["framebuffer"]["different_pixels"], 1)
            self.assertEqual(report["framebuffer"]["bounds"], [1, 1, 1, 1])

    def test_hash_mismatch_and_path_traversal_rejected(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            manifest = self.fixture(directory)
            manifest["commands"]["sha256"] = "0" * 64
            (directory / "manifest.json").write_text(json.dumps(manifest))
            with self.assertRaisesRegex(gpu.CaptureError, "SHA-256"):
                gpu.load_capture(directory / "manifest.json")
            manifest["commands"] = {"file": "../commands.bin"}
            (directory / "manifest.json").write_text(json.dumps(manifest))
            with self.assertRaisesRegex(gpu.CaptureError, "relative"):
                gpu.load_capture(directory / "manifest.json")

    def test_declared_blob_size_and_command_count_are_validated(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            manifest = self.fixture(directory)
            manifest["commands"]["bytes"] = 64
            (directory / "manifest.json").write_text(json.dumps(manifest))
            with self.assertRaisesRegex(gpu.CaptureError, "byte count"):
                gpu.load_capture(directory / "manifest.json")
            manifest["commands"]["bytes"] = 192
            manifest["command_count"] = 2
            (directory / "manifest.json").write_text(json.dumps(manifest))
            with self.assertRaisesRegex(gpu.CaptureError, "command_count"):
                gpu.load_capture(directory / "manifest.json")
            manifest["command_count"] = 3
            (directory / "manifest.json").write_text(json.dumps(manifest))
            gpu.load_capture(directory / "manifest.json")

    def test_uncompared_capture_never_claims_match(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            manifest = self.fixture(directory)
            del manifest["expected"]
            del manifest["expected_exports"]
            (directory / "manifest.json").write_text(json.dumps(manifest))
            stdout = io.StringIO()
            with contextlib.redirect_stdout(stdout):
                self.assertEqual(gpu.main([str(directory / "manifest.json")]), 0)
            self.assertEqual(json.loads(stdout.getvalue())["comparison_status"], "not_compared")

    def test_missing_export_expectation_marks_partial_comparison(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            manifest = self.fixture(directory)
            del manifest["expected_exports"]
            (directory / "manifest.json").write_text(json.dumps(manifest))
            stdout = io.StringIO()
            with contextlib.redirect_stdout(stdout):
                self.assertEqual(gpu.main([str(directory / "manifest.json")]), 0)
            report = json.loads(stdout.getvalue())
            self.assertEqual(report["comparison_status"], "partial_match")
            self.assertEqual(report["comparison_coverage"]["missing_export_bases"], [hex(EXPORT)])

    def test_duplicate_expected_exports_cannot_hide_an_earlier_mismatch(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            manifest = self.fixture(directory)
            (directory / "bad-export.bin").write_bytes(bytes(gpu.FRAME_BYTES))
            manifest["expected_exports"].insert(0, {"base": hex(EXPORT), "file": "bad-export.bin"})
            with self.assertRaisesRegex(gpu.CaptureError, "duplicate"):
                gpu.compare_captured_exports(manifest, directory, {EXPORT: (directory / "export.bin").read_bytes()})

    def test_rtl_replay_rejects_captured_export_mismatch_even_if_model_matches(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            manifest = self.fixture(directory)
            result = gpu.render((directory / "commands.bin").read_bytes(), {})
            (directory / "export.bin").write_bytes(bytes(gpu.FRAME_BYTES))
            def fake_run(command, work, name, timeout):
                if name == "vsim":
                    (work / "rtl-status.txt").write_text("1000 900 1")
                    return "PASS: captured GPU job completed"
                return ""
            def fake_frame(path):
                return result.xrgb_pixels if path.name == "rtl-native.hex" else gpu.read_pixels(result.exports[EXPORT])
            with mock.patch.object(replay.shutil, "which", return_value="mock-tool"), \
                 mock.patch.object(replay, "prepare", return_value=(result, manifest, 1000, [EXPORT])), \
                 mock.patch.object(replay, "_run", side_effect=fake_run), \
                 mock.patch.object(replay, "_read_frame", side_effect=fake_frame), \
                 contextlib.redirect_stdout(io.StringIO()):
                code = replay.main([str(directory / "manifest.json"), "--output", str(directory)])
            self.assertEqual(code, 1)
            report = json.loads(next(directory.glob("gpu-replay-*/comparison.json")).read_text())
            self.assertTrue(report["reference_vs_rtl"]["matching"])
            self.assertTrue(report["exports"][hex(EXPORT)]["matching"])
            self.assertFalse(report["rtl_vs_captured_exports"][hex(EXPORT)]["matching"])
            self.assertEqual(report["captured_comparison_status"], "mismatch")
            self.assertFalse(report["matching"])

    def test_simulator_zero_exit_error_cannot_be_hidden_by_pass_marker(self):
        with tempfile.TemporaryDirectory() as temporary:
            output = "# ** Fatal: injected test failure\n# PASS: captured GPU job completed\n"
            process = subprocess.CompletedProcess(["mock-vsim"], 0, stdout=output, stderr="")
            with mock.patch.object(replay.subprocess, "run", return_value=process):
                with self.assertRaisesRegex(gpu.CaptureError, "despite zero exit"):
                    replay._run(["mock-vsim"], Path(temporary), "vsim", 1)

    @unittest.skipUnless(os.environ.get("AM2R_TEST_RTL") == "1", "set AM2R_TEST_RTL=1 for ModelSim differential replay")
    def test_rtl_arbitrary_axis_pairs_under_two_ddr_schedules(self):
        output = ROOT / "data/build/gpu-reference-synthetic"
        output.mkdir(parents=True, exist_ok=True)
        directory = Path(tempfile.mkdtemp(prefix="axis-pairs-", dir=output))
        commands, regions, _, _ = arbitrary_axis_pair_fixture()
        (directory / "commands.bin").write_bytes(commands)
        manifest = {"format": gpu.FORMAT, "commands": {"file": "commands.bin"},
                    "regions": [], "synthetic": True}
        for index, (base, data) in enumerate(regions.items()):
            name = f"texture-{index}.bin"
            (directory / name).write_bytes(data)
            manifest["regions"].append({"base": base, "file": name})
        (directory / "manifest.json").write_text(json.dumps(manifest))
        for latency, stall, response in [(2, 11, 0), (5, 7, 3)]:
            process = subprocess.run([sys.executable, str(ROOT / "tools/replay_gpu_capture.py"),
                                      str(directory / "manifest.json"), "--output", str(directory),
                                      "--read-latency", str(latency), "--stall-period", str(stall),
                                      "--response-gap", str(response)], capture_output=True, text=True, timeout=240)
            self.assertEqual(process.returncode, 0, process.stdout + process.stderr)

    @unittest.skipUnless(os.environ.get("AM2R_TEST_RTL") == "1", "set AM2R_TEST_RTL=1 for ModelSim differential replay")
    def test_rtl_floor_tint_rgba_export_and_capability_handshake(self):
        output = ROOT / "data/build/gpu-reference-synthetic"
        output.mkdir(parents=True, exist_ok=True)
        directory = Path(tempfile.mkdtemp(prefix="floor-tint-", dir=output))
        commands, texture, _, _ = floor_tint_fixture()
        (directory / "commands.bin").write_bytes(commands)
        (directory / "texture.bin").write_bytes(texture)
        manifest = {"format": gpu.FORMAT, "commands": {"file": "commands.bin"},
                    "regions": [{"base": TEXTURE, "file": "texture.bin"}], "synthetic": True}
        (directory / "manifest.json").write_text(json.dumps(manifest))
        for latency, stall, response in [(2, 11, 0), (5, 7, 3)]:
            process = subprocess.run([sys.executable, str(ROOT / "tools/replay_gpu_capture.py"),
                                      str(directory / "manifest.json"), "--output", str(directory),
                                      "--read-latency", str(latency), "--stall-period", str(stall),
                                      "--response-gap", str(response)], capture_output=True, text=True, timeout=240)
            self.assertEqual(process.returncode, 0, process.stdout + process.stderr)

    @unittest.skipUnless(os.environ.get("AM2R_TEST_RTL") == "1", "set AM2R_TEST_RTL=1 for ModelSim differential replay")
    def test_rtl_replay_all_opcodes_under_two_ddr_schedules(self):
        output = ROOT / "data/build/gpu-reference-synthetic"
        output.mkdir(parents=True, exist_ok=True)
        directory = Path(tempfile.mkdtemp(prefix="fixture-", dir=output))
        texture = struct.pack("<640I", *[(0x80000000 if i % 7 == 0 else 0xFF000000) | ((i * 79) & 0xFFFFFF) for i in range(640)])
        table = struct.pack("<8H", 3, 17, 1, 0, 4, 16, 0, 1)
        native_base = NATIVE + gpu.FRAME_BYTES
        native = struct.pack("<76800I", *[(i * 123) & 0xFFFFFF for i in range(gpu.PIXELS)])
        commands = frame(texture_blit(width=32, height=2, w1=pair(TEXTURE, 128), w2=xy(1, 1)),
                         command(3, width=9, height=3, w1=0x800000FF, w2=xy(20, 20), w7=0xD000000000003000),
                         command(5, blend=2, w1=0x80808080),
                         command(4, width=3, height=2, w1=pair(TEXTURE, 128), w2=xy(5, 10),
                                 w3=pair(0, 32 << 16), w4=pair(0, 2 << 16), w5=0,
                                 w6=pair(65536, 0), w7=pair(65536, 65536)),
                         command(6, w1=EXPORT),
                         command(7, width=2, w1=pair(EXPORT, 1280), w2=pair(TABLE, 30), w6=0x33FFFFFF),
                         command(8, width=2, w2=pair(TABLE, 35), w6=0x80FFFFFF),
                         command(9, blend=1, width=320, height=2, w1=pair(TEXTURE + 1536, 128),
                                 w2=xy(0, 40), w3=29, w5=pair(65536, 65536), w6=0x80FFFFFF),
                         command(6, w1=EXPORT), color=0xC864503C)
        (directory / "commands.bin").write_bytes(commands)
        manifest = {"format": gpu.FORMAT, "commands": {"file": "commands.bin"},
                    "regions": [], "native_source_base": native_base, "synthetic": True}
        for i, (base, data) in enumerate({TEXTURE: texture, TABLE: table, native_base: native}.items()):
            (directory / f"region-{i}.bin").write_bytes(data)
            manifest["regions"].append({"base": base, "file": f"region-{i}.bin"})
        (directory / "manifest.json").write_text(json.dumps(manifest))
        for latency, stall, response in [(2, 11, 0), (5, 7, 3)]:
            process = subprocess.run([sys.executable, str(ROOT / "tools/replay_gpu_capture.py"),
                                      str(directory / "manifest.json"), "--output", str(directory),
                                      "--read-latency", str(latency), "--stall-period", str(stall),
                                      "--response-gap", str(response)], capture_output=True, text=True, timeout=240)
            self.assertEqual(process.returncode, 0, process.stdout + process.stderr)


if __name__ == "__main__":
    unittest.main()
