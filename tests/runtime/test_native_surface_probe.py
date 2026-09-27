"""Synthetic memory only: no Windows process or original game data needed."""
import importlib.util
from pathlib import Path
import struct
import unittest

ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location("native_probe", ROOT / "tools/am2r_native_surface_probe.py")
probe = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(probe)


class Memory:
    def __init__(self):
        self.bytes = {}

    def put(self, address, value):
        self.bytes.update((address + index, byte) for index, byte in enumerate(value))

    def words(self, address, *values):
        self.put(address, struct.pack("<" + "I" * len(values), *values))

    def read(self, address, size):
        try:
            return bytes(self.bytes[address + index] for index in range(size))
        except KeyError as error:
            raise OSError("unmapped synthetic byte") from error


def fixture():
    memory = Memory()
    memory.words(0x8F0F48, 166)
    memory.words(0x6A13D4, 0x100000, 1, 3)
    # IDs 2 and 4 share bucket zero, ID3 owns bucket one.
    memory.words(0x100000, 0x101000, 0x101020, 0x101040, 0x101040)
    memory.words(0x101000, 0, 0x101020, 2, 0x102000)
    memory.words(0x101020, 0x101000, 0, 4, 0x102020)
    memory.words(0x101040, 0, 0, 3, 0x102040)
    memory.words(0x102000, 2, 10, 320, 240)
    memory.words(0x102020, 4, 11, 512, 256)
    memory.words(0x102040, 3, 0xFFFFFFFF, 0, 0)
    # One light object / instance / real-valued surf variable.
    memory.words(0x6EEBB4, 0x120000)
    memory.words(0x120000, 0x121000, 0, 1)
    memory.words(0x121000, 0x120100, 0x120100)
    memory.words(0x120100, 0, 0, 727, 0x122000)
    memory.words(0x1220B8, 0x126000)
    memory.words(0x907EC8, 0x124000, 1)
    memory.words(0x124000, 0x125000)
    memory.put(0x125000, b"surf\0")
    memory.words(0x126000, 0, 0, 0x127000)
    memory.words(0x127008, 0)
    memory.words(0x127018, 1234)
    memory.words(0x1270C8, 0x128000)
    heads = [0] * 64
    heads[100000 & 63] = 0x129000
    memory.words(0x128004, *heads)
    memory.put(0x129000, struct.pack("<IIdIII", 0, 0, 4.0, 0, 0, 100000))
    return memory


class NativeSurfaceProbeTests(unittest.TestCase):
    def test_valid_snapshot_and_light(self):
        result = probe.coherent_snapshot(fixture().read)
        self.assertEqual(result["room"], 166)
        self.assertEqual(result["logical_surface_count"], 3)
        self.assertEqual(result["surfaces_512x256"], 1)
        self.assertEqual(result["light_instances"], [{"instance_id": 1234, "surf": 4}])
        self.assertEqual(result["surfaces"][1], {"id": 3, "texture_handle": -1, "width": 0, "height": 0})
        self.assertNotIn("vram_bytes", result)

    def test_null_record_is_not_surface_exists(self):
        memory = fixture()
        memory.words(0x101040 + 12, 0)
        result = probe.read_snapshot(memory.read, include_light=False)
        self.assertEqual(result["map_entries"], 3)
        self.assertEqual(result["logical_surface_count"], 2)

    def test_surface_chain_cycle(self):
        memory = fixture()
        memory.words(0x101020 + 4, 0x101000)
        with self.assertRaises(probe.InvalidSnapshot):
            probe.read_snapshot(memory.read)

    def test_wrong_prev_tail_bucket_and_count(self):
        for address, value in ((0x101020, 0), (0x100004, 0x101000),
                               (0x101020 + 8, 5), (0x6A13DC, 2)):
            with self.subTest(address=address):
                memory = fixture()
                memory.words(address, value)
                with self.assertRaises(probe.InvalidSnapshot):
                    probe.read_snapshot(memory.read)

    def test_invalid_masks_and_count_limits(self):
        for mask, count in ((2, 3), (0xFFFFFFFF, 3), (4095, 16385), (8191, 3)):
            memory = fixture()
            memory.words(0x6A13D4, 0x100000, mask, count)
            with self.assertRaises(probe.InvalidSnapshot):
                probe.read_snapshot(memory.read)

    def test_shared_records(self):
        memory = fixture()
        memory.words(0x101020 + 12, 0x102000)
        with self.assertRaises(probe.InvalidSnapshot):
            probe.read_snapshot(memory.read)

    def test_bad_record_identity_dimensions_texture(self):
        for record in ((8, 10, 320, 240), (2, 10, 65536, 240),
                       (2, 10, 0, 240), (2, 0xFFFFFFFE, 320, 240)):
            memory = fixture()
            memory.words(0x102000, *record)
            with self.assertRaises(probe.InvalidSnapshot):
                probe.read_snapshot(memory.read)

    def test_instance_and_variable_cycles(self):
        for address, value in ((0x126000, 0x126000), (0x129000, 0x129000)):
            memory = fixture()
            memory.words(address, value)
            with self.assertRaises(probe.InvalidSnapshot):
                probe.read_snapshot(memory.read)

    def test_bad_variable_kind_and_value(self):
        for value, kind in ((4.5, 0), (float("nan"), 0), (4.0, 1), (-2.0, 0)):
            memory = fixture()
            memory.put(0x129008, struct.pack("<dII", value, 0, kind))
            with self.assertRaises(probe.InvalidSnapshot):
                probe.read_snapshot(memory.read)

    def test_wrong_variable_bucket(self):
        memory = fixture()
        memory.words(0x129018, 100001)
        with self.assertRaises(probe.InvalidSnapshot):
            probe.read_snapshot(memory.read)

    def test_name_limit_and_unexpected_encoding(self):
        for value in (b"x" * 128, b"\xff\0"):
            memory = fixture()
            memory.put(0x125000, value)
            with self.assertRaises((probe.InvalidSnapshot, UnicodeError)):
                probe.read_snapshot(memory.read)

    def test_instance_previous_link_must_match(self):
        memory = fixture()
        memory.words(0x126004, 0x126000)
        with self.assertRaises(probe.InvalidSnapshot):
            probe.read_snapshot(memory.read)

    def test_missing_light_object(self):
        memory = fixture()
        memory.words(0x120000 + 8, 0)
        memory.words(0x121000, 0, 0)
        self.assertEqual(probe.read_snapshot(memory.read)["light_instances"], [])

    def test_checked_read_bounds_alignment_and_short_read(self):
        for address, size in ((0, 4), (0xFFFFFFFF, 4), (0x10000, 0), (0x10000, 1048577)):
            with self.assertRaises(probe.InvalidSnapshot):
                probe.checked_read(lambda a, n: b"" * n, address, size)
        with self.assertRaises(probe.InvalidSnapshot):
            probe.words(lambda a, n: b"\0" * n, 0x10001, 1)
        with self.assertRaises(probe.InvalidSnapshot):
            probe.checked_read(lambda a, n: b"", 0x10000, 1)

    def test_read_failure_is_not_empty_census(self):
        with self.assertRaises(probe.InvalidSnapshot):
            probe.coherent_snapshot(Memory().read)

    def test_changes_between_complete_snapshots_rejected(self):
        memory = fixture()
        reads = 0
        def read(address, size):
            nonlocal reads
            if address == 0x8F0F48:
                reads += 1
                # Each full snapshot has two equal room reads, next differs.
                memory.words(address, 166 + ((reads - 1) // 2) % 2)
            return memory.read(address, size)
        with self.assertRaises(probe.InvalidSnapshot):
            probe.coherent_snapshot(read, include_light=False)

    def test_transient_failure_retries_then_succeeds(self):
        memory = fixture()
        failed = False
        def read(address, size):
            nonlocal failed
            if not failed:
                failed = True
                raise OSError("transition")
            return memory.read(address, size)
        self.assertEqual(probe.coherent_snapshot(read)["logical_surface_count"], 3)

    def test_bucket_mutation_with_same_count_rejected(self):
        memory = fixture()
        reads = 0
        def read(address, size):
            nonlocal reads
            if address == 0x100000:
                reads += 1
                if reads == 2:
                    memory.words(address, 0)
            return memory.read(address, size)
        with self.assertRaises(probe.InvalidSnapshot):
            probe.read_snapshot(read)

    def test_read_budget_aggregate_across_multiple_instances(self):
        memory = fixture()
        # A second valid light instance has its own list node and variable table.
        memory.words(0x126000, 0x126100, 0, 0x127000)
        memory.words(0x126100, 0, 0x126000, 0x127100)
        memory.words(0x127108, 0)
        memory.words(0x127118, 1235)
        memory.words(0x1271C8, 0x128000)
        self.assertEqual(len(probe.read_snapshot(memory.read)["light_instances"]), 2)
        delivered = 0
        def read(address, size):
            nonlocal delivered
            delivered += 1
            return memory.read(address, size)
        # Count the point where the second instance begins, then exhaust there.
        first_calls = 0
        def first_count(address, size):
            nonlocal first_calls
            if address == 0x126100:
                raise RuntimeError("second instance")
            first_calls += 1
            return memory.read(address, size)
        with self.assertRaises(RuntimeError):
            probe.read_snapshot(first_count)
        budget = probe.ReadBudget(read, calls=first_calls)
        with self.assertRaisesRegex(probe.InvalidSnapshot, "budget exhausted"):
            probe.coherent_snapshot(budget)
        self.assertEqual(delivered, first_calls)  # Retries cannot replenish it.

    def test_byte_budget_and_pid_do_not_wrap(self):
        delivered = 0
        def read(address, size):
            nonlocal delivered
            delivered += 1
            return b"\0" * size
        budget = probe.ReadBudget(read, calls=100, byte_limit=7)
        budget(0x10000, 4)
        with self.assertRaises(probe.InvalidSnapshot):
            budget(0x10000, 4)
        self.assertEqual(delivered, 1)
        for pid in (-1, 0, 0x100000000):
            with self.assertRaisesRegex(ValueError, "positive DWORD"):
                probe.WindowsReader(pid)


if __name__ == "__main__":
    unittest.main()
