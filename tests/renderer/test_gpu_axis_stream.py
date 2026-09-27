#!/usr/bin/env python3
"""Independent op2/water stream fixtures; no game data or hardware access.

The same descriptors cover streaming-eligible and legacy fallback rectangles.
Set AM2R_TEST_RTL=1 for whole-frame/raw-export comparison under two independent
DDR schedules. The expected byte equations here do not import the GPU model's
tint/blend helpers. Passing says protocol agreement, not native GM equivalence.
"""
from __future__ import annotations

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
from test_gpu_targets import command, write_fixture

SOURCE, WRAP_SOURCE, TABLE, EXPORT = 0x24000000, 0x26000000, 0x28000000, 0x2A000000
TARGET = 0x2C000000
FRAME_BYTES = 320 * 240 * 4


def signed(value, bits):
    low = value % (1 << bits)
    return low if low < 1 << (bits - 1) else low - (1 << bits)


def pack_channels(channels):
    return int.from_bytes(bytes(channels), "little")


def channels(pixel):
    return list(pixel.to_bytes(4, "little"))


def expected_blend(pixel, destination, tint, mode, floor):
    s = [(a * b + (0 if floor else 127)) // 255
         for a, b in zip(channels(pixel), channels(tint))]
    d = channels(destination)
    if mode == 2:
        return pack_channels([(255 - s[c]) * d[c] // 255 for c in range(4)])
    if mode == 1:
        return pack_channels([min(255, d[c] + s[c] * s[3] // 255) for c in range(3)]
                             + [min(255, d[3] + s[3])])
    return pack_channels([(s[c] * s[3] + d[c] * (255 - s[3])) // 255 for c in range(3)]
                         + [s[3] + d[3] * (255 - s[3]) // 255])


def pattern(count, seed=0):
    alphas = (0, 1, 127, 128, 254, 255)
    return [pack_channels(((i * 37 + seed) % 256, (i * 73 + 93 + seed) % 256,
                           (i * 19 + 211 + seed) % 256, alphas[i % 6]))
            for i in range(count)]


def apply_axis(expected, memory, region_base, *, base, stride, width, height,
               x, y, u=0, v=0, du=65536, dv=65536, tint=0xFFFFFFFF,
               mode=0, floor=False, steps=(0, 0, 0, 0)):
    """Independent scalar equation with exact descriptor-domain wrapping."""
    for row in range(height):
        if not 0 <= y + row < 240:
            continue
        row_tint = pack_channels([min(255, max(0, signed((c << 16) + row * step * 256, 25) // 65536))
                                  for c, step in zip(channels(tint), steps)])
        for col in range(width):
            if not 0 <= x + col < 320:
                continue
            sx = signed(u + col * du, 32) // 65536
            sy = (signed(v + row * dv, 32) // 65536) % 65536
            offset = ((base + sy * stride + sx * 4) % (1 << 32)) - region_base
            if offset < 0 or offset + 4 > len(memory):
                raise AssertionError(f"fixture source outside supplied memory: {offset}")
            source = int.from_bytes(memory[offset:offset + 4], "little")
            at = (y + row) * 320 + x + col
            expected[at] = expected_blend(source, expected[at], row_tint, mode, floor)


def axis_descriptor(**case):
    mode, floor = case.get("mode", 0), case.get("floor", False)
    flags = (1 if mode == 1 else 2 if mode == 2 else 0) | (4 if floor else 0)
    return command(2, case["width"], case["height"], base=case["base"], stride=case["stride"],
        x=case["x"], y=case["y"], w0=2 | flags << 8 | case["width"] << 16 | case["height"] << 32,
        w4=(case.get("u", 0) & 0xFFFFFFFF) | (case.get("v", 0) & 0xFFFFFFFF) << 32,
        w5=(case.get("du", 65536) & 0xFFFFFFFF) | (case.get("dv", 65536) & 0xFFFFFFFF) << 32,
        w6=case.get("tint", 0xFFFFFFFF),
        w7=sum((value & 65535) << (16 * i) for i, value in enumerate(case.get("steps", (0, 0, 0, 0)))))


def axis_fixture(present=False):
    initial = pattern(320 * 240, 41)
    expected = list(initial)
    # Odd pixel stride and a separately offset source base test both source
    # lanes, row padding, and parity changes across rows. Cache padding is real.
    memory = struct.pack("<8192I", *pattern(8192, 17))
    regions = {SOURCE: memory}
    commands, cases, exports = [], [], {}

    def add(**values):
        case = dict(base=SOURCE, stride=2052, height=1, x=0, y=len(cases),
                    tint=0x81B37D41)
        case.update(values)
        cases.append(case)
        commands.append(axis_descriptor(**case))
        apply_axis(expected, memory, SOURCE, **case)

    widths = (1, 2, 3, 31, 32, 33, 63, 64, 95, 96, 127, 128, 191, 192, 193, 318, 319, 320)
    for mode in range(3):
        for floor in (False, True):
            for index, width in enumerate(widths):
                add(width=width, mode=mode, floor=floor,
                    **({"x": 1} if width < 320 and index % 2 else {}),
                    **({"base": SOURCE + 4} if index & 2 else {}))
    # Prove raw alpha0, RGB-bearing inverse-source draws are not skipped.
    for mode in range(3):
        for alpha in (0, 1, 127, 128, 254, 255):
            add(width=320, mode=mode, tint=(alpha << 24) | 0xC159A3,
                floor=bool(alpha & 1), u=1 << 15)
    exports[EXPORT] = struct.pack(f"<{len(expected)}I", *expected)
    commands.append(command(11, 320, 240, base=EXPORT, stride=1280))

    # Keep multirow cases disjoint from the boundary-alpha fixture. Clipping
    # must retain original source phase, V step, tint phase and lane masking.
    row = 130
    for index, (x, width, u) in enumerate(((-3, 320, 0), (1, 320, 7 << 16),
                                          (287, 65, 3 << 16), (0, 320, 65535),
                                          (3, 317, 0), (0, 319, 9 << 16))):
        case = dict(base=SOURCE + (index % 2) * 4, stride=2052, width=width,
                    height=5, x=x, y=row, u=u, v=4 << 16,
                    dv=(-65536 if index & 1 else 32768),
                    tint=0x7F80FE01, steps=(177, -129, 256, 83),
                    mode=index % 3, floor=bool(index & 1))
        commands.append(axis_descriptor(**case)); cases.append(case)
        apply_axis(expected, memory, SOURCE, **case)
        row += 5
    rng = random.Random(0xA21B57)
    for index in range(60):
        width = rng.choice(widths)
        case = dict(base=SOURCE + (index % 2) * 4, stride=2052, width=width,
                    height=1, x=rng.randrange(-4, 322 - width), y=row + index,
                    u=rng.randrange(4 << 16), v=rng.randrange(6 << 16),
                    tint=rng.getrandbits(32), mode=index % 3, floor=bool(index & 1))
        commands.append(axis_descriptor(**case)); cases.append(case)
        apply_axis(expected, memory, SOURCE, **case)
    for index, tint in enumerate((0, 0xFFFFFFFF, 0xFF000000, 0x00FFFFFF, 0x0101FEFF, 0xFEFF0001)):
        for mode in range(3):
            case = dict(base=SOURCE + (index % 2) * 4, stride=2052, width=320,
                        height=1, x=0, y=220 + index * 3 + mode, tint=tint,
                        mode=mode, floor=bool(index & 1))
            commands.append(axis_descriptor(**case)); cases.append(case)
            apply_axis(expected, memory, SOURCE, **case)
    exports[EXPORT + FRAME_BYTES] = struct.pack(f"<{len(expected)}I", *expected)
    commands.extend((command(11, 320, 240, base=EXPORT + FRAME_BYTES, stride=1280),
                     command(0 if present else 12)))
    return b"".join(commands), regions, initial, expected, exports, cases


def fallback_fixture(present=False):
    initial = pattern(320 * 240, 117)
    expected = list(initial)
    # Source base centered in this region permits the descriptor's signed U
    # wrap from32767 to-32768. It must retain the old scalar/cache semantics.
    memory = struct.pack("<65600I", *pattern(65600, 119))
    commands, cases = [], []
    examples = [(32760 << 16, 65536), ((32767 << 16) | 32768, 65536),
                (-32768 << 16, 65536), (128 << 16, -65536),
                (0, 32768), (0, 131072), (23 << 16, 0)]
    for index, (u, du) in enumerate(examples):
        for mode in range(3):
            case = dict(base=WRAP_SOURCE + 131072, stride=8, width=64, height=1,
                        x=index & 1, y=index * 4 + mode, u=u, du=du, dv=0,
                        tint=0x917FCF31, mode=mode, floor=bool(index & 1))
            cases.append(case); commands.append(axis_descriptor(**case))
            apply_axis(expected, memory, WRAP_SOURCE, **case)
    # Top and bottom clipping retain original V/tint row phases.
    for y in (-2, 238):
        case = dict(base=WRAP_SOURCE + 131072, stride=8, width=65, height=5,
                    x=-1, y=y, u=0, v=0, dv=0, tint=0x817F13C3,
                    steps=(117, -512, 78, 177), mode=2, floor=True)
        cases.append(case); commands.append(axis_descriptor(**case))
        apply_axis(expected, memory, WRAP_SOURCE, **case)
    commands.extend((command(11, 320, 240, base=EXPORT, stride=1280), command(0 if present else 12)))
    return b"".join(commands), {WRAP_SOURCE: memory}, initial, expected, {EXPORT: struct.pack(f"<{len(expected)}I", *expected)}, cases


def water_fixture(present=False):
    # First row exactly retains the known96+64-beat x188/189 regression.
    initial = [0xFF000000 | (i * 0x030507 & 0xFFFFFF) for i in range(320 * 240)]
    expected = list(initial)
    source = struct.pack("<1280I", *([0xFF735129] * 320 + pattern(960, 211)))
    rows = [(0, 320, 0, 0), (1, 319, 1, 1), (3, 317, 0, 2), (0, 319, 1, 3)]
    table = struct.pack("<16H", *(v for row in rows for v in row))
    commands = []
    for mode, dy, tint in ((0, 1, 0x80FFFFFF), (0, 11, 0x7F112233)):
        commands.append(command(7, len(rows), base=SOURCE, stride=1280,
            w0=7 | mode << 8 | len(rows) << 16, w2=TABLE | dy << 32, w6=tint))
        for row, (dx, length, sx, sy) in enumerate(rows):
            for col in range(length):
                pixel = int.from_bytes(source[(sy * 320 + sx + col) * 4:][:4], "little")
                # Water deliberately tints alpha only; the RGB bytes of the
                # command are intentionally nonwhite in the second case.
                at = (dy + row) * 320 + dx + col
                expected[at] = expected_blend(pixel, expected[at],
                                              (tint & 0xFF000000) | 0xFFFFFF, mode, False)
    # Existing opcode9 shares this pair pipeline but is alpha-only additive,
    # repeating a32-pixel source tile, rather than a general contiguous blit.
    commands.append(command(9, 319, 3, base=SOURCE, stride=1280, x=1, y=21,
                            w3=17, w4=1 << 48, w5=1 << 48, w6=0x7F193B57))
    for row in range(3):
        for col in range(319):
            offset = ((row + 1) * 320 + (17 + col) % 32) * 4
            pixel = int.from_bytes(source[offset:offset + 4], "little")
            at = (21 + row) * 320 + 1 + col
            expected[at] = expected_blend(pixel, expected[at], 0x7FFFFFFF, 1, False)
    commands.extend((command(11, 320, 240, base=EXPORT, stride=1280), command(0 if present else 12)))
    return b"".join(commands), {SOURCE: source, TABLE: table}, initial, expected, {EXPORT: struct.pack(f"<{len(expected)}I", *expected)}, rows


def ordering_fixture(present=False):
    initial = pattern(320 * 240, 177)
    expected = list(initial)
    source = struct.pack("<2048I", *pattern(2048, 53))
    target = bytearray(struct.pack("<1280I", *pattern(1280, 97)))
    regions = {SOURCE: source, TARGET: bytes(target)}
    commands = []

    def draw(memory, region, **case):
        commands.append(axis_descriptor(**case))
        apply_axis(expected, memory, region, **case)

    # Prime a cache line, stream into BRAM, then STORE over that cached DDR
    # source. The later stream/cache reader must observe the new raw RGBA.
    draw(target, TARGET, base=TARGET, stride=1280, width=1, height=1,
         x=319, y=239)
    draw(source, SOURCE, base=SOURCE + 4, stride=2052, width=319, height=3,
         x=1, y=1, tint=0xA193E731, mode=0, floor=True)
    commands.append(command(11, 319, 3, base=TARGET, stride=1280, x=1, y=1))
    for row in range(3):
        target[row * 1280:row * 1280 + 1276] = struct.pack("<319I", *expected[(row + 1) * 320 + 1:(row + 2) * 320])
    # Clearing after a streamed draw must wait for every in-flight write.
    commands.append(command(1, base=0x87312953))
    expected[:] = [0x87312953] * (320 * 240)
    draw(target, TARGET, base=TARGET, stride=1280, width=320, height=3,
         x=0, y=3, tint=0x717D3BFD, mode=2, floor=False)
    # LOAD must likewise follow the complete stream, with no late lane writes.
    commands.append(command(10, 319, 3, base=TARGET, stride=1280, x=0, y=7))
    for row in range(3):
        expected[(7 + row) * 320:(7 + row) * 320 + 319] = struct.unpack_from("<319I", target, row * 1280)
    # Overlap a different streaming blend and then use the narrow cached path.
    draw(source, SOURCE, base=SOURCE, stride=2052, width=320, height=3,
         x=0, y=7, tint=0xB79D1F53, mode=1, floor=False)
    draw(target, TARGET, base=TARGET, stride=1280, width=1, height=1,
         x=319, y=239, tint=0xFFFFFFFF)
    commands.extend((command(11, 320, 240, base=EXPORT, stride=1280), command(0 if present else 12)))
    exports = {EXPORT: struct.pack(f"<{len(expected)}I", *expected)}
    exports.update({TARGET + row * 1280: bytes(target[row * 1280:row * 1280 + 1276]) for row in range(3)})
    return b"".join(commands), regions, initial, expected, exports, []


def gather_fixture(present=False):
    """Arbitrary signed stepping, cache-line splits and pair/tail destinations."""
    initial = pattern(320 * 240, 203)
    expected = list(initial)
    memory = struct.pack("<65664I", *pattern(65664, 83))
    commands, cases = [], []

    def add(**values):
        case = dict(base=WRAP_SOURCE + 131072, stride=8, width=95, height=2,
                    x=len(cases) % 2, y=(len(cases) * 3) % 235, dv=0,
                    tint=0x817FC13B, steps=(101, -213, 157, 251))
        case.update(values)
        cases.append(case)
        commands.append(axis_descriptor(**case))
        apply_axis(expected, memory, WRAP_SOURCE, **case)

    # Repeated samples, adjacent/different cache lines, both traversal directions,
    # large cache jumps and signed32 fixed-coordinate wrap all remain legal.
    steps = (0, 1, 32768, 98304, 131072, -32768, -65536, -98304,
             31 << 16, 32 << 16, 33 << 16, -(33 << 16), 0x7FFFFFFF, -0x80000000)
    starts = ((31 << 16) | 32768, (32 << 16) - 1, -1, (32767 << 16) | 32768)
    for mode in range(3):
        for floor in (False, True):
            for index, du in enumerate(steps):
                add(u=starts[index % len(starts)], du=du, mode=mode, floor=floor,
                    width=(1, 2, 3, 31, 32, 33, 63, 64, 95, 96, 127, 128, 319, 320)[index],
                    base=WRAP_SOURCE + 131072 + (index % 2) * 4,
                    tint=(0x00FEDCBA if mode == 2 and index % 3 == 0 else 0x817FC13B))
    # Both-sided clipping found a signed/unsigned visible-width bug: x=-5 and
    # width327 must produce precisely320 visible pixels, for stream and gather.
    for mode in range(3):
        for du in (65536, -65536, 98304, 0):
            add(x=-5, width=327, height=3, y=135 + mode * 4, u=317 << 16,
                du=du, v=2 << 16, dv=-65536, mode=mode, floor=True)
    # Left-clipped first sample and negativeU becoming nonnegative within a row
    # must not reuse a stale stream width or lose source/tint phase.
    add(x=-5, width=69, height=1, y=151, u=32768, du=65536, mode=0)
    add(x=1, width=64, height=1, y=152, u=-360448, du=65536, mode=2, floor=True)
    rng = random.Random(0xCAC4E123)
    for index in range(70):
        add(x=rng.randrange(-9, 12), width=rng.choice((1, 2, 3, 31, 32, 33, 64, 127, 319, 327)),
            u=signed(rng.getrandbits(32), 32), du=rng.choice(steps),
            v=rng.randrange(4) << 16, dv=0, height=1,
            base=WRAP_SOURCE + 131072 + (index % 2) * 4,
            tint=rng.getrandbits(32), mode=index % 3, floor=bool(index & 1))
    commands.extend((command(11, 320, 240, base=EXPORT, stride=1280), command(0 if present else 12)))
    return b"".join(commands), {WRAP_SOURCE: memory}, initial, expected, {EXPORT: struct.pack(f"<{len(expected)}I", *expected)}, cases


def gather_ordering_fixture(present=False):
    initial = pattern(320 * 240, 47)
    expected = list(initial)
    source = struct.pack("<2048I", *pattern(2048, 133))
    target_base = 0x2A800000
    target = bytearray(struct.pack("<1280I", *pattern(1280, 191)))
    regions = {SOURCE: source, target_base: bytes(target)}
    commands = []

    def draw(memory, region, **case):
        commands.append(axis_descriptor(**case))
        apply_axis(expected, memory, region, **case)

    draw(target, target_base, base=target_base, stride=1280, width=2, height=1,
         x=317, y=239, u=31 << 16, du=65536)
    draw(source, SOURCE, base=SOURCE + 4, stride=2052, width=319, height=3,
         x=1, y=100, tint=0xA193E731, mode=0, floor=True)
    commands.append(command(11, 319, 3, base=target_base, stride=1280, x=1, y=100))
    for row in range(3):
        target[row * 1280:row * 1280 + 1276] = struct.pack("<319I", *expected[(100 + row) * 320 + 1:(101 + row) * 320])
    commands.append(command(1, base=0x9131577D))
    expected[:] = [0x9131577D] * (320 * 240)
    draw(target, target_base, base=target_base, stride=1280, width=319, height=3,
         x=1, y=1, u=318 << 16, du=-65536, tint=0x00CDB731, mode=2, floor=True)
    commands.append(command(10, 319, 3, base=target_base, stride=1280, x=0, y=12))
    for row in range(3):
        expected[(12 + row) * 320:(12 + row) * 320 + 319] = struct.unpack_from("<319I", target, row * 1280)
    draw(target, target_base, base=target_base, stride=1280, width=319, height=3,
         x=1, y=12, u=318 << 16, du=-32768, tint=0x719D1F53, mode=1, floor=False,
         steps=(128, -197, 41, 257))
    draw(target, target_base, base=target_base, stride=1280, width=3, height=1,
         x=317, y=239, u=31 << 16, du=32 << 16, tint=0xFFFFFFFF)
    commands.extend((command(11, 320, 240, base=EXPORT, stride=1280), command(0 if present else 12)))
    exports = {EXPORT: struct.pack(f"<{len(expected)}I", *expected)}
    exports.update({target_base + row * 1280: bytes(target[row * 1280:row * 1280 + 1276]) for row in range(3)})
    return b"".join(commands), regions, initial, expected, exports, []


class AxisStreaming(unittest.TestCase):
    def verify_fixture(self, factory, present=False):
        commands, regions, initial, expected, exports, _ = factory(present)
        result = gpu.render(commands, regions, initial)
        self.assertEqual(result.pixels, expected)
        for base, data in exports.items():
            # The model can coalesce adjacent raw exports.
            found = next((payload[base - start:base - start + len(data)]
                          for start, payload in result.exports.items()
                          if start <= base and base + len(data) <= start + len(payload)), None)
            self.assertEqual(found, data)
        self.assertEqual(result.presented, present)
        return commands, regions, initial

    def test_independent_axis_pixels_and_retained_outside(self):
        for present in (False, True):
            self.verify_fixture(axis_fixture, present)

    def test_independent_fixed_wrap_and_nonunit_fallback(self):
        self.verify_fixture(fallback_fixture)

    def test_water_multiple_bursts_and_alpha_only_tint(self):
        self.verify_fixture(water_fixture)

    def test_stream_store_clear_load_and_resample_ordering(self):
        self.verify_fixture(ordering_fixture)

    def test_independent_cache_gather_wrap_clipping_and_random_pairs(self):
        self.verify_fixture(gather_fixture)

    def test_stream_gather_store_load_cache_and_fence_ordering(self):
        self.verify_fixture(gather_ordering_fixture)

    def test_transparent_subtractive_source_is_not_identity(self):
        self.assertNotEqual(expected_blend(0x00FEDCBA, 0x87654321, 0xFFFFFFFF, 2, True), 0x87654321)
        self.assertEqual(expected_blend(0x00FEDCBA, 0x87654321, 0xFFFFFFFF, 0, True), 0x87654321)
        self.assertEqual(expected_blend(0x00FEDCBA, 0x87654321, 0xFFFFFFFF, 1, True), 0x87654321)

    @unittest.skipUnless(os.getenv("AM2R_TEST_RTL") == "1", "set AM2R_TEST_RTL=1")
    def test_rtl_all_pixels_and_exports_under_two_ddr_schedules(self):
        parent = ROOT / "data/work/unified-renderer-20260925"
        parent.mkdir(parents=True, exist_ok=True)
        directory = Path(tempfile.mkdtemp(prefix="axis-stream-reference-", dir=parent))
        for name, factory in (("axis", axis_fixture), ("fallback", fallback_fixture),
                              ("water", water_fixture), ("ordering", ordering_fixture),
                              ("gather", gather_fixture), ("gather-ordering", gather_ordering_fixture)):
            for present in (False, True):
                case = directory / f"{name}-{'present' if present else 'fence'}"
                case.mkdir()
                commands, regions, initial = self.verify_fixture(factory, present)
                path = write_fixture(case, commands, regions, initial)
                for latency, stall, gap in ((2, 11, 0), (5, 7, 3)):
                    result = subprocess.run([sys.executable, str(ROOT / "tools/replay_gpu_capture.py"),
                        str(path), "--output", str(case), "--read-latency", str(latency),
                        "--stall-period", str(stall), "--response-gap", str(gap)],
                        capture_output=True, text=True, timeout=240)
                    self.assertEqual(result.returncode, 0, result.stdout + result.stderr)


if __name__ == "__main__":
    unittest.main()
