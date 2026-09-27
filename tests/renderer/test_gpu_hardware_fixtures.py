#!/usr/bin/env python3
"""Exercise the actual C parser without opening a device or launching hardware.

Malformed bundles have valid recomputed CRCs: descriptor/indirect pointer checks,
not the integrity field alone, must reject them before any DDR publication.
"""
import hashlib
import os
from pathlib import Path
import struct
import subprocess
import sys
import tempfile
import unittest
import zlib

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools"))
import make_gpu_hardware_fixtures as bundles
from test_gpu_targets import write_fixture

# Preserve the six bundles already exercised on USB-1; adding families must not
# silently change their input bytes or baseline expected results.
BASELINE_HASHES = {
    "generic": "7072660df299d2217aa8984336dc468f6427306affd335c7ccfe088bd31df8ec",
    "transfer": "8bc813daf4d9fa8b7a317c036d621aced7e2cc7beed33bfadc6f39046b986706",
    "bounded-clear": "405524d7e269ada709e67a1aacebd16039ea3b1769bbd309162ad03aadb8e9e2",
    "axis": "1f180a1ac53ce43a294a0cdbc34469b4bf409ffaa1f777ec9deffe1ffc00a99c",
    "axis-fallback": "d8fba90dac4bf79a80e34295bfd48d7df4dbb304b41cfbe35ccff29092a42640",
    "water": "363c091498508a0edc73f4b7629d864522163db644b4f0964c497f817f6b251e",
}


def seal(blob):
    struct.pack_into("<I", blob, 8, len(blob))
    struct.pack_into("<I", blob, 28, zlib.crc32(blob[40:]))
    return blob


def spans(blob):
    count, inputs, outputs = struct.unpack_from("<3I", blob, 12)
    offset = 40 + count * 64
    result = []
    for group, number in enumerate((inputs, outputs)):
        for _ in range(number):
            address, size = struct.unpack_from("<2I", blob, offset)
            result.append((group, address, size, offset, offset + 8))
            offset += 8 + size
    return result


def data_offset(blob, address):
    for group, base, size, _, start in spans(blob):
        if not group and base <= address < base + size:
            return start + address - base
    raise AssertionError("address not in input fixture")


def descriptor(blob, opcode):
    count = struct.unpack_from("<I", blob, 12)[0]
    return next(40 + i * 64 for i in range(count) if blob[40 + i * 64] == opcode)


class HardwareFixtureParser(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory(prefix="gpu-fixture-parser-")
        cls.directory = Path(cls.temp.name)
        cls.binary = cls.directory / ("fixture.exe" if os.name == "nt" else "fixture")
        zig = ROOT / "third_party/zig/zig-x86_64-windows-0.16.0/zig.exe"
        compiler = [str(zig), "cc"] if os.name == "nt" else ["cc"]
        env = os.environ.copy()
        if os.name == "nt":
            env["ZIG_GLOBAL_CACHE_DIR"] = str(ROOT / "data/build/zig-global-cache")
            env["ZIG_LOCAL_CACHE_DIR"] = str(ROOT / "data/build/zig-fixture-cache")
        subprocess.run(compiler + ["-std=c99", "-O2", "-Wall", "-Wextra", "-Werror",
                       str(ROOT / "tools/am2r_gpu_fixture.c"), "-o", str(cls.binary)],
                       check=True, cwd=ROOT, env=env, capture_output=True, text=True)
        cls.fixtures = {name: bundles.make_fixture(name)[0] for name in bundles.FAMILIES}

    @classmethod
    def tearDownClass(cls):
        cls.temp.cleanup()

    def validate(self, blob, accepted=False):
        path = self.directory / "candidate.a2gt"
        path.write_bytes(blob)
        result = subprocess.run([str(self.binary), "--validate", str(path)],
                                capture_output=True, text=True, timeout=10)
        if accepted:
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertIn("VALID", result.stdout)
        else:
            self.assertNotEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertIn("REJECT", result.stderr)

    def mutated(self, name, change):
        blob = bytearray(self.fixtures[name])
        change(blob)
        self.validate(seal(blob))

    def test_all_independent_fixture_families_accept(self):
        for name, blob in self.fixtures.items():
            with self.subTest(name=name):
                self.validate(blob, True)

    def test_original_six_bundles_remain_byte_reproducible(self):
        for name, expected in BASELINE_HASHES.items():
            self.assertEqual(hashlib.sha256(self.fixtures[name]).hexdigest(), expected, name)

    def test_header_and_crc_reject(self):
        for at, value in ((0, 0), (4, 2), (12, 1025), (16, 129), (20, 0), (24, 15), (32, 1)):
            with self.subTest(at=at):
                self.mutated("generic", lambda b: struct.pack_into("<I", b, at, value))
        corrupt = bytearray(self.fixtures["generic"])
        corrupt[-1] ^= 1
        self.validate(corrupt)
        self.validate(self.fixtures["generic"][:100])
        self.validate(seal(bytearray(self.fixtures["generic"]) + b"\0\0\0\0"))

    def test_declared_regions_cannot_escape_or_overlap(self):
        for target in (0x23FF0000, 0x23FE0000, 0x2C000000, 0x24000004):
            with self.subTest(target=target):
                self.mutated("axis", lambda b: struct.pack_into("<I", b, spans(b)[0][3], target))
        self.mutated("axis", lambda b: struct.pack_into("<I", b, spans(b)[1][3], spans(b)[0][1]))
        self.mutated("axis", lambda b: struct.pack_into("<I", b, next(s[3] for s in spans(b) if s[0]), 0x2BF00000))

    def test_completion_and_initialization_cannot_publish_display(self):
        self.mutated("axis", lambda b: b.__setitem__(descriptor(b, 12), 0))
        self.mutated("axis", lambda b: b.__setitem__(40, 12))
        self.mutated("axis", lambda b: struct.pack_into("<Q", b, 40, 14 | 1 << 16 | 1 << 32))

    def test_direct_descriptor_addresses_rechecked(self):
        for op, fixture in ((2, "axis"), (10, "axis"), (11, "axis"), (13, "generic")):
            with self.subTest(op=op):
                self.mutated(fixture, lambda b: struct.pack_into("<I", b, descriptor(b, op) + 8, 0x23FF0000))
        self.mutated("axis", lambda b: struct.pack_into("<I", b, descriptor(b, 2) + 8, 0x2BFFFFFF))
        def stride_mutation(blob):
            draw = descriptor(blob, 2)
            # Make the initially single-row sample actually consume the stride.
            struct.pack_into("<I", blob, draw + 36, 1 << 16)
            struct.pack_into("<I", blob, draw + 12, 0xFFFFFFFF)
        self.mutated("axis", stride_mutation)

    def test_reserved_opcodes_and_fields_reject(self):
        for op in (8, 15, 255):
            with self.subTest(op=op):
                self.mutated("axis", lambda b: b.__setitem__(descriptor(b, 2), op))
        self.mutated("axis", lambda b: b.__setitem__(descriptor(b, 2) + 1, 128))
        self.mutated("bounded-clear", lambda b: struct.pack_into("<Q", b, descriptor(b, 14) + 24, 1))

    def test_offscreen_declared_work_is_still_bounded(self):
        for opcode in (2, 3, 4):
            with self.subTest(opcode=opcode):
                def mutate(blob):
                    draw = descriptor(blob, 2)
                    blob[draw:draw + 64] = bundles.command(opcode, 65535, 65535,
                        x=32767, y=32767, w3=(65536 << 32) if opcode == 4 else 0,
                        w4=(65536 << 32) if opcode == 4 else 0)
                self.mutated("axis", mutate)

    def test_affine_setup_is_a_complete_one_shot_descriptor(self):
        for offset, value in ((0, 5 | 3 << 8), (0, 5 | 4 << 8), (0, 5 | 1 << 16),
                              (8, 1 << 32), (16, 1), (56, 1)):
            with self.subTest(offset=offset, value=value):
                self.mutated("affine", lambda b: struct.pack_into("<Q", b,
                    descriptor(b, 5) + offset, value))
        # Neither a second setup nor another real descriptor may consume what
        # was incorrectly assumed to be an opaque affine extension body.
        for replacement in (bundles.command(5), bundles.command(1), bundles.command(12)):
            self.mutated("affine", lambda b: b.__setitem__(slice(descriptor(b, 4),
                descriptor(b, 4) + 64), replacement))
        self.mutated("affine", lambda b: b.__setitem__(slice(descriptor(b, 12) - 64,
            descriptor(b, 12)), bundles.command(5)))

    def test_affine_shape_bounds_and_all_source_cachelines_rechecked(self):
        for offset, value in ((0, 4 | 1 << 8 | 17 << 16 | 9 << 32),
                              (16, 1 << 32), (24, 0), (32, 0),
                              (24, 0xFFFF0000 | 65536 << 32),
                              (8, 0x23FF0000 | 260 << 32),
                              (8, 0x24800001 | 260 << 32),
                              (8, 0x24800000 | 261 << 32),
                              (40, 0xFFFF0000 << 32),
                              (48, 10000 << 16)):
            with self.subTest(offset=offset, value=value):
                def mutate(blob):
                    at = descriptor(blob, 4)
                    struct.pack_into("<Q", blob, at + offset, value)
                    if offset == 40:
                        struct.pack_into("<Q", blob, at + 32, 0xFFFE0000 | 65536 << 32)
                    if offset == 48:
                        struct.pack_into("<Q", blob, at + 24, 0x7FFFFFFF << 32)
                self.mutated("affine", mutate)

    def test_affine_wrapped_accumulation_and_half_open_bounds(self):
        def replace(blob, width, umax, start, step):
            at = descriptor(blob, 4)
            blob[at:at + 64] = bundles.command(4, width, 1,
                base=0x24800000 + 8192 * 4 - 4, stride=0,
                w3=umax << 32, w4=65536 << 32,
                w5=start, w6=step)
        # U=max is excluded, so the nonexistent next cacheline is never read.
        good = bytearray(self.fixtures["affine"])
        replace(good, 2, 65536, 0, 65536)
        self.validate(seal(good), True)
        self.mutated("affine", lambda b: replace(b, 2, 2 << 16, 0, 65536))
        # An initially out-of-range coordinate wraps into range at col1 and
        # col3; validating only the original/corner range would miss this read.
        good = bytearray(self.fixtures["affine"])
        replace(good, 3, 3 << 16, 0x7FFF0000, 0x80010000)
        self.validate(seal(good), True)
        self.mutated("affine", lambda b: replace(b, 4, 3 << 16, 0x7FFF0000, 0x80010000))

    def test_gather_and_ordering_fetches_still_obey_reserved_ddr(self):
        for name in ("gather", "gather-ordering"):
            with self.subTest(name=name):
                self.mutated(name, lambda b: struct.pack_into("<I", b,
                    descriptor(b, 2) + 8, 0x23FF0000))

    def test_generic_indirect_texture_and_numeric_state_rechecked(self):
        def packet_change(blob, offset, value):
            count = struct.unpack_from("<I", blob, 12)[0]
            address = next(struct.unpack_from("<I", blob, 40 + i * 64 + 8)[0]
                           for i in range(count) if blob[40 + i * 64] == 13 and
                           struct.unpack_from("<Q", blob, data_offset(blob,
                               struct.unpack_from("<I", blob, 40 + i * 64 + 8)[0]))[0] & (1 << 32))
            struct.pack_into("<Q", blob, data_offset(blob, address) + offset, value)
        self.mutated("generic", lambda b: packet_change(b, 0, 0))
        self.mutated("generic", lambda b: packet_change(b, 8, 0x23FF0000 | 128 << 32))
        self.mutated("generic", lambda b: packet_change(b, 16, 1 << 63))
        self.mutated("generic", lambda b: packet_change(b, 24, 0))
        self.mutated("generic", lambda b: packet_change(b, 8 * 17, 0x7FFFFFFFFFFFFFFF))
        self.mutated("generic", lambda b: packet_change(b, 8 * 39, 1))

    def test_packet_cannot_be_overwritten_by_any_export(self):
        def mutate(blob):
            packet = struct.unpack_from("<I", blob, descriptor(blob, 13) + 8)[0]
            store = descriptor(blob, 11)
            blob[store:store + 64] = bundles.command(11, 1, 1, base=packet, stride=8)
        self.mutated("generic", mutate)

    def test_water_indirect_table_and_rows_rechecked(self):
        self.mutated("water", lambda b: struct.pack_into("<I", b, descriptor(b, 7) + 16, 0x23FF0000))
        def mutate(blob):
            table = struct.unpack_from("<I", blob, descriptor(blob, 7) + 16)[0]
            struct.pack_into("<H", blob, data_offset(blob, table) + 6, 65535)
        self.mutated("water", mutate)

    def test_windows_hardware_run_refused(self):
        if os.name != "nt":
            self.skipTest("Linux hardware execution is never invoked by this test")
        result = subprocess.run([str(self.binary), "--run", "not-opened.a2gt"], capture_output=True, text=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("hardware execution requires", result.stderr)

    @unittest.skipUnless(os.getenv("AM2R_TEST_RTL") == "1", "set AM2R_TEST_RTL=1")
    def test_new_exact_hardware_bundles_replay_under_two_ddr_schedules(self):
        parent = ROOT / "data/work/unified-renderer-20260925"
        parent.mkdir(parents=True, exist_ok=True)
        directory = Path(tempfile.mkdtemp(prefix="hardware-fixture-reference-", dir=parent))
        for name in ("gather", "gather-ordering", "affine"):
            blob = self.fixtures[name]
            count = struct.unpack_from("<I", blob, 12)[0]
            commands = blob[40:40 + count * 64]
            regions = {base: blob[start:start + size]
                       for group, base, size, _, start in spans(blob) if not group}
            case = directory / name
            case.mkdir()
            path = write_fixture(case, commands, regions)
            for latency, stall, gap in ((2, 11, 0), (5, 7, 3)):
                result = subprocess.run([sys.executable, str(ROOT / "tools/replay_gpu_capture.py"),
                    str(path), "--output", str(case), "--read-latency", str(latency),
                    "--stall-period", str(stall), "--response-gap", str(gap)],
                    capture_output=True, text=True, timeout=240)
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)


if __name__ == "__main__":
    unittest.main()
