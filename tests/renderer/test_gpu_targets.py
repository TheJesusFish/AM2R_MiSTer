#!/usr/bin/env python3
"""Independent synthetic pixel/ordering checks for general FPGA target transfers.

Set AM2R_TEST_RTL=1 to compare the same fixtures with production RTL under two
DDR schedules. No game data, hardware access or display deployment is used.
"""
from __future__ import annotations

import importlib.util
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
import replay_gpu_capture as replay

SOURCE, TARGET, EXPORT = 0x24000000, 0x26000000, 0x28000000


def command(op, width=0, height=0, *, base=0, stride=0, x=0, y=0, **overrides):
    words = [op | width << 16 | height << 32, base | stride << 32,
             (x & 65535) | (y & 65535) << 16, 0, 0, 0, 0, 0]
    for key, value in overrides.items():
        words[int(key[1:])] = value
    return struct.pack("<8Q", *words)


def transfer_fixture(present=False):
    rng = random.Random(0x10A11)
    src = bytearray(rng.randbytes(2056 * 260))
    dst = bytearray(rng.randbytes(2056 * 260))
    regions = {SOURCE: bytes(src), TARGET: bytes(dst)}
    expected = [0x73512907] * gpu.PIXELS
    commands = [command(1, base=expected[0])]
    # Rectangles hit both DDR lanes, both BRAM banks, variable row padding,
    # transfer bursts, the last framebuffer pixel and larger logical images.
    cases = [(1, 1, 1, 1, 4, 8), (63, 3, 3, 5, 124, 2056),
             (129, 5, 7, 17, 132, 2056), (319, 7, 1, 41, 260, 2056),
             (320, 17, 0, 69, 1028, 2056), (1, 1, 319, 239, 65532, 8)]
    for number, (width, height, x, y, offset, stride) in enumerate(cases):
        target_offset = 4 + number * 20 * 2056
        commands.append(command(10, width, height, base=SOURCE + offset,
                                stride=stride, x=x, y=y))
        for row in range(height):
            data = bytes(src[offset + row * stride:offset + row * stride + width * 4])
            pixels = struct.unpack(f"<{width}I", data)
            expected[(y + row) * 320 + x:(y + row) * 320 + x + width] = pixels
            dst[target_offset + row * 2056:target_offset + row * 2056 + width * 4] = data
        commands.append(command(11, width, height, base=TARGET + target_offset,
                                stride=2056, x=x, y=y))
    # Store -> source must see new pixels, not stale texture cache data.
    commands.extend([command(2, 1, 1, base=TARGET + 4, stride=2056, x=310, y=230,
                             w6=0xFFFFFFFF),
                     command(1, base=0x01234567),
                     command(11, 1, 1, base=TARGET + 4, stride=2056),
                     command(2, 1, 1, base=TARGET + 4, stride=2056, x=310, y=230,
                             w6=0xFFFFFFFF),
                     command(10, 1, 1, base=TARGET + 4, stride=2056, x=319, y=239)])
    expected = [0x01234567] * gpu.PIXELS
    dst[4:8] = struct.pack("<I", 0x01234567)
    commands.append(command(0 if present else 12))
    return b"".join(commands), regions, expected, bytes(dst)


def bounded_clear_fixture(present=False):
    # Every outside pixel has a deterministic nonzero pattern, including alpha.
    # Expected results use rectangle replacement only, independent of GPU code.
    initial = [((index * 0x19B52731) ^ 0xA74C29E1) & 0xFFFFFFFF
               for index in range(gpu.PIXELS)]
    expected = list(initial)
    commands = []
    rectangles = [(1, 1, 1, 1, 0x00FEDCBA), (0, 2, 3, 2, 0x01020304),
                  (319, 239, 1, 1, 0x7F559977), (317, 237, 3, 3, 0x80123456),
                  (5, 7, 129, 17, 0xFE102030), (1, 60, 319, 7, 0xFF030201),
                  (0, 80, 320, 17, 0x00112233), (7, 7, 1, 3, 0x01000000)]
    for x, y, width, height, colour in rectangles:
        commands.append(command(14, width, height, base=colour, x=x, y=y))
        for row in range(y, y + height):
            for column in range(x, x + width):
                expected[row * 320 + column] = colour
    # Prime a cached source, then replace transparent RGB and later opaque RGB
    # through bounded clear + STORE. Both subsequent samples must see the STORE,
    # not the prior cached colour. Bounded clear itself has no DDR side effects.
    # The real texture cache fetches a complete128-byte line, so capture that
    # padding explicitly; a scalar one-texel read alone is not enough for RTL.
    regions = {TARGET: struct.pack("<2I", 0xFFAA3301, 0x517395B7) + bytes(120)}
    commands.append(command(2, 1, 1, base=TARGET, stride=8, x=310, y=200, w6=0xFFFFFFFF))
    expected[200 * 320 + 310] = 0xFFAA3301
    for output_x, colour in ((311, 0x00ABCDEF), (312, 0xFF124578)):
        commands.extend([command(14, 1, 1, base=colour, x=17, y=19),
                         command(11, 1, 1, base=TARGET, stride=8, x=17, y=19),
                         command(2, 1, 1, base=TARGET, stride=8, x=output_x, y=200, w6=0xFFFFFFFF)])
        expected[19 * 320 + 17] = colour
        if colour >> 24:
            expected[200 * 320 + output_x] = colour
    # Empty rectangles do not inspect coordinates, blend, or touch memory.
    commands.extend([command(14, 0, 65535, base=0xDEADBEEF, x=65535, y=65535),
                     command(14, 65535, 0, base=0x13579BDF, x=65535, y=65535),
                     command(11, 320, 240, base=EXPORT, stride=1280),
                     command(0 if present else 12)])
    return b"".join(commands), regions, initial, expected


def write_fixture(directory, commands, regions, initial=None):
    (directory / "commands.bin").write_bytes(commands)
    manifest = {"format": gpu.FORMAT, "synthetic": True,
                "commands": {"file": "commands.bin"}, "regions": []}
    if initial is not None:
        (directory / "initial.rgba").write_bytes(struct.pack(f"<{len(initial)}I", *initial))
        manifest["initial_framebuffer"] = {"file": "initial.rgba", "format": "rgba8888"}
    for index, (base, data) in enumerate(regions.items()):
        name = f"region-{index}.bin"
        (directory / name).write_bytes(data)
        manifest["regions"].append({"base": base, "file": name})
    path = directory / "manifest.json"
    path.write_text(json.dumps(manifest), encoding="utf-8")
    return path


class TargetTransfers(unittest.TestCase):
    def test_bounded_clear_raw_alpha_odd_lanes_outside_pixels_and_cached_store(self):
        for present in (False, True):
            commands, regions, initial, expected = bounded_clear_fixture(present)
            result = gpu.render(commands, regions, initial)
            self.assertEqual(result.pixels, expected)
            self.assertEqual(result.exports[EXPORT], struct.pack(f"<{len(expected)}I", *expected))
            self.assertEqual(result.exports[TARGET], struct.pack("<I", 0xFF124578))
            self.assertEqual(result.pixels[200 * 320 + 311], initial[200 * 320 + 311])
            self.assertEqual(result.pixels[0], initial[0])
            self.assertEqual(result.pixels[321], 0x00FEDCBA)
            self.assertEqual(result.opcode_counts[14], 12)
            self.assertEqual(result.presented, present)

    def test_bounded_clear_never_blends_and_has_no_ddr_access(self):
        initial = [0xFFFFFFFF] * gpu.PIXELS
        result = gpu.render(command(14, 3, 1, base=0x00010203, x=1, y=3) + command(12), {}, initial)
        self.assertEqual(result.pixels[961:964], [0x00010203] * 3)
        self.assertEqual(result.pixels[:961], initial[:961])
        self.assertEqual(result.pixels[964:], initial[964:])
        self.assertEqual(result.exports, {})
        self.assertEqual(result.pixel_work, 3)

    def test_bounded_clear_validation_and_missing_initial_remain_strict(self):
        bad = [command(14, 1, 1, x=320), command(14, 1, 1, y=240),
               command(14, 2, 1, x=319), command(14, 1, 2, y=239),
               command(14, 1, 1, x=-1), command(14, 1, 1, y=65535),
               command(14, 65535, 1), command(14, 1, 65535),
               command(14, 1, 1, w0=14 | 1 << 8 | 1 << 16 | 1 << 32),
               command(14, 1, 1, w0=14 | 1 << 16 | 1 << 32 | 1 << 48),
               command(14, 1, 1, stride=1), command(14, 1, 1, w2=1 << 32)]
        bad += [command(14, 1, 1, **{f"w{index}": 1}) for index in range(3, 8)]
        bad += [command(14, 0, 1, w7=1), command(15)]
        for descriptor in bad:
            with self.subTest(descriptor=descriptor.hex()), self.assertRaises(gpu.CaptureError):
                gpu.render(descriptor + command(12), {}, [0] * gpu.PIXELS)
        with self.assertRaisesRegex(gpu.CaptureError, "initial BRAM"):
            gpu.render(command(14, 1, 1, base=0xFFFFFFFF) + command(12), {})

    def test_empty_bounded_clear_and_full_legacy_clear_contracts(self):
        initial = [0x83072517] * gpu.PIXELS
        empty = command(14, 0, 65535, base=7, x=65535, y=65535) + command(14, 5, 0, base=9, x=-1)
        result = gpu.render(empty + command(12), {}, initial)
        self.assertEqual(result.pixels, initial)
        self.assertEqual(result.pixel_work, 0)
        # Opcode1 remains whole BRAM regardless of its historically ignored
        # dimensions/coordinates. Opcode14 requires an explicit full rectangle.
        old = gpu.render(command(1, 3, 2, base=0x01234567, x=101, y=102) + command(12), {})
        new = gpu.render(command(14, 320, 240, base=0x01234567) + command(12), {})
        self.assertEqual(old.pixels, [0x01234567] * gpu.PIXELS)
        self.assertEqual(new.pixels, old.pixels)

    def test_bounded_clear_replay_keeps_explicit_initial_frame(self):
        commands, regions, initial, _ = bounded_clear_fixture()
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            path = write_fixture(directory, commands, regions, initial)
            result, manifest, _, _ = replay.prepare(path, directory)
            self.assertEqual((directory / "mapping.txt").read_text().splitlines()[0].split()[-2:], ["1", "0"])
            self.assertIn("initial_framebuffer", manifest)
            initial_words = [int(line, 16) for line in (directory / "initial.hex").read_text().splitlines()]
            self.assertEqual([pixel for word in initial_words for pixel in (word & 0xFFFFFFFF, word >> 32)], initial)
            self.assertEqual(result.opcode_counts[14], 12)

    def test_reference_raw_alpha_stride_and_partial_rows(self):
        for present in (False, True):
            commands, regions, pixels, memory = transfer_fixture(present)
            result = gpu.render(commands, regions)
            self.assertEqual(result.pixels, pixels)
            self.assertEqual(result.presented, present)
            for base, data in result.exports.items():
                self.assertEqual(data, memory[base - TARGET:base - TARGET + len(data)])
            self.assertLess(sum(map(len, result.exports.values())), len(memory))

    def test_load_preserves_pixels_outside_transfer(self):
        commands = command(1, base=0x80030405) + command(10, 1, 1,
            base=SOURCE + 4, stride=8, x=319, y=239) + command(12)
        result = gpu.render(commands, {SOURCE: struct.pack("<2I", 0xFFFFFFFF, 0x00123456)})
        self.assertEqual(result.pixels[:-1], [0x80030405] * (gpu.PIXELS - 1))
        self.assertEqual(result.pixels[-1], 0x00123456)

    def test_contiguous_store_rows_merge_without_inventing_padding(self):
        commands = command(1, base=0x12345678) + command(11, 2, 3,
            base=TARGET, stride=8) + command(12)
        result = gpu.render(commands, {})
        self.assertEqual(result.exports, {TARGET: struct.pack("<6I", *([0x12345678] * 6))})

    def test_zero_extent_does_not_access_unmapped_memory(self):
        result = gpu.render(command(1, base=7) + command(10, 0, 5, base=3) +
                            command(11, 2, 0, base=3) + command(12), {})
        self.assertEqual(result.exports, {})

    def test_invalid_transfer_and_completion_are_rejected(self):
        invalid = [command(10, 1, 1, base=SOURCE + 1, stride=8),
                   command(11, 3, 1, base=TARGET, stride=8),
                   command(10, 1, 1, base=SOURCE, stride=12),
                   command(11, 1, 1, base=TARGET, stride=8, x=320),
                   command(10, 1, 1, base=SOURCE, stride=8, y=-1),
                   command(11, 1, 2, base=0xFFFFFFFC, stride=8),
                   command(10, 1, 1, base=SOURCE, stride=8, w3=1),
                   command(12, w1=1), command(12) + command(0)]
        for item in invalid:
            with self.subTest(item=item[:24]), self.assertRaises(gpu.CaptureError):
                gpu.render(command(1, base=0) + item + command(12), {SOURCE: bytes(16)})

    def test_no_present_does_not_invent_initial_bram(self):
        with self.assertRaisesRegex(gpu.CaptureError, "initial BRAM"):
            gpu.render(command(12), {})

    def test_variable_export_compare_rejects_length_and_marks_mismatch(self):
        self.assertTrue(gpu.compare_rgba_bytes(bytes(12), bytes(12))["matching"])
        self.assertEqual(gpu.compare_rgba_bytes(bytes(12), bytes(11) + b"\x80")["different_pixels"], 1)
        with self.assertRaises(gpu.CaptureError):
            gpu.compare_rgba_bytes(bytes(12), bytes(8))

    def test_replay_mapping_tracks_no_present_and_exact_byte_ranges(self):
        commands, regions, _, _ = transfer_fixture()
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            path = write_fixture(directory, commands, regions)
            result, _, _, exports = replay.prepare(path, directory)
            header = (directory / "mapping.txt").read_text().splitlines()[0].split()
            self.assertEqual(header[-1], "0")
            self.assertTrue(any(base & 7 for base in exports))
            self.assertTrue(any(len(data) != gpu.FRAME_BYTES for data in result.exports.values()))

    def test_sparse_input_can_partially_overlap_a_later_larger_output(self):
        commands = (command(1, base=0xFFEEDDCC) +
                    command(10, 2, 1, base=TARGET + 8, stride=8) +
                    command(11, 8, 1, base=TARGET, stride=32) + command(12))
        original = struct.pack("<2I", 0x01234567, 0x89ABCDEF)
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            path = write_fixture(directory, commands, {TARGET + 8: original})
            result, _, _, _ = replay.prepare(path, directory)
            self.assertEqual(result.exports[TARGET][:8], original)
            mapping = (directory / "mapping.txt").read_text().splitlines()
            header = mapping[0].split()
            entries = [line.split() for line in mapping[1:1 + int(header[0])]]
            entry = next(e for e in entries if int(e[0], 16) == TARGET >> 3)
            self.assertEqual(int(entry[1]), 4)
            words = (directory / "memory.hex").read_text().splitlines()
            self.assertEqual(int(words[int(entry[2]) + 1], 16), struct.unpack("<Q", original)[0])
            # A missing logical read still fails before output memory is added.
            bad = command(10, 4, 1, base=TARGET, stride=16) + command(11, 8, 1, base=TARGET, stride=32) + command(12)
            path = write_fixture(directory, command(1) + bad, {TARGET + 8: original})
            with self.assertRaises(gpu.CaptureError):
                replay.prepare(path, directory)

    @unittest.skipUnless(os.getenv("AM2R_TEST_RTL") == "1", "set AM2R_TEST_RTL=1")
    def test_rtl_bounded_clear_under_two_ddr_schedules(self):
        parent = ROOT / "data/work/unified-renderer-20260925"
        parent.mkdir(parents=True, exist_ok=True)
        directory = Path(tempfile.mkdtemp(prefix="bounded-clear-reference-", dir=parent))
        for present in (False, True):
            case = directory / ("present" if present else "fence")
            case.mkdir()
            commands, regions, initial, _ = bounded_clear_fixture(present)
            path = write_fixture(case, commands, regions, initial)
            for latency, stall, gap in ((2, 11, 0), (5, 7, 3)):
                result = subprocess.run([sys.executable, str(ROOT / "tools/replay_gpu_capture.py"),
                    str(path), "--output", str(case), "--read-latency", str(latency),
                    "--stall-period", str(stall), "--response-gap", str(gap)],
                    capture_output=True, text=True, timeout=240)
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    @unittest.skipUnless(os.getenv("AM2R_TEST_RTL") == "1", "set AM2R_TEST_RTL=1")
    def test_rtl_transfer_order_and_fence_under_two_ddr_schedules(self):
        parent = ROOT / "data/work/unified-renderer-20260925"
        parent.mkdir(parents=True, exist_ok=True)
        directory = Path(tempfile.mkdtemp(prefix="target-reference-", dir=parent))
        for present in (False, True):
            case = directory / ("present" if present else "fence")
            case.mkdir()
            commands, regions, _, _ = transfer_fixture(present)
            path = write_fixture(case, commands, regions)
            for latency, stall, gap in ((2, 11, 0), (5, 7, 3)):
                result = subprocess.run([sys.executable, str(ROOT / "tools/replay_gpu_capture.py"),
                    str(path), "--output", str(case), "--read-latency", str(latency),
                    "--stall-period", str(stall), "--response-gap", str(gap)],
                    capture_output=True, text=True, timeout=240)
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)


if __name__ == "__main__":
    unittest.main()
