#!/usr/bin/env python3
"""Independent byte-memory oracle for fragmented captured GPU STORE ranges."""
from pathlib import Path
import random
import sys
import unittest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools"))
import am2r_gpu_reference as gpu


class MemoryIntervals(unittest.TestCase):
    def test_thousands_of_fragmented_rows_without_recursive_stack(self):
        expected = bytearray((i * 31 + 7) & 255 for i in range(20000))
        memory = gpu._Memory({0x24000000: bytes(expected)})
        for row in range(2400):
            address = row * 8
            payload = bytes((row & 255, 0, 255, (row * 13) & 255))
            expected[address:address + 4] = payload
            memory.export(0x24000000 + address, payload)
        self.assertEqual(memory.read(0x24000000, len(expected)), expected)

    def test_random_overlaps_latest_write_wins(self):
        rng = random.Random(921600)
        expected = bytearray(rng.randbytes(4096))
        memory = gpu._Memory({0x24000000: bytes(expected)})
        for _ in range(400):
            start = rng.randrange(1024) * 4
            size = rng.randrange(1, min(128, (4096 - start) // 4) + 1) * 4
            payload = rng.randbytes(size)
            memory.export(0x24000000 + start, payload)
            expected[start:start + size] = payload
            left = rng.randrange(4096)
            right = rng.randrange(left, 4097)
            self.assertEqual(memory.read(0x24000000 + left, right - left), expected[left:right])
        self.assertEqual(memory.read(0x24000000, 4096), expected)

    def test_written_data_can_define_unmapped_memory_but_holes_stay_errors(self):
        memory = gpu._Memory({})
        memory.export(0x24000000, b"abcd")
        memory.export(0x24000008, b"ijkl")
        with self.assertRaises(gpu.CaptureError):
            memory.read(0x24000000, 12)
        memory.export(0x24000004, b"efgh")
        self.assertEqual(memory.read(0x24000000, 12), b"abcdefghijkl")

    def test_invalid_addresses_still_fail_closed(self):
        memory = gpu._Memory({})
        for address, length in ((-1, 1), (0, -1), (0xFFFFFFFF, 2)):
            with self.subTest(address=address, length=length), self.assertRaises(gpu.CaptureError):
                memory.read(address, length)
        self.assertEqual(memory.read(0x24000000, 0), b"")


if __name__ == "__main__":
    unittest.main()
