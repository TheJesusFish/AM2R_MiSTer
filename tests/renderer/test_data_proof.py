#!/usr/bin/env python3
"""Actual SHA-256/whole-data gate, cross-checked against hashlib; no hardware."""
from __future__ import annotations
import hashlib
import os
from pathlib import Path
import random
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "third_party/Butterscotch/src"


class DataProof(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory(prefix="am2r-data-proof-")
        cls.directory = Path(cls.temp.name)
        cls.binary = cls.directory / ("proof.exe" if os.name == "nt" else "proof")
        zig = ROOT / "third_party/zig/zig-x86_64-windows-0.16.0/zig.exe"
        cc = [str(zig), "cc"] if zig.exists() else [shutil.which("cc") or "cc"]
        env = dict(os.environ, ZIG_GLOBAL_CACHE_DIR=str(ROOT / "data/build/zig-global-cache"),
                   ZIG_LOCAL_CACHE_DIR=str(cls.directory / "zig-cache"))
        subprocess.run([*cc, "-std=c11", "-O2", "-Wall", "-Wextra", "-Werror", "-I", str(SRC),
                        str(Path(__file__).with_name("data_proof_regression.c")),
                        "-o", str(cls.binary)], env=env, check=True)

    @classmethod
    def tearDownClass(cls):
        cls.temp.cleanup()

    def check_hash(self, data, chunks):
        source = self.directory / "input.bin"
        source.write_bytes(data)
        expected = hashlib.sha256(data).hexdigest()
        for chunk in chunks:
            with self.subTest(length=len(data), chunk=chunk):
                actual = subprocess.check_output([str(self.binary), "hash", str(source), str(chunk)],
                                                 text=True).strip()
                self.assertEqual(actual, expected)

    def test_standard_vectors_and_streaming(self):
        for vector in (b"", b"abc", b"abcdbcdecdefdefgefghfghighijhijkijkljklmklmnlmnomnopnopq",
                       b"a" * 1000000):
            self.check_hash(vector, [1, 7, 63, 64, 65, 16384])

    def test_all_padding_boundaries_and_random_binary(self):
        rng = random.Random(256)
        for length in (1, 55, 56, 57, 63, 64, 65, 119, 120, 127, 128, 129, 65535, 65536, 65537):
            self.check_hash(rng.randbytes(length), [3, 64, 4093, 65536])

    def test_incorrect_input_and_io_failures_fail_closed(self):
        source = self.directory / "short.bin"
        source.write_bytes(b"not AM2R data" * 32)
        text = subprocess.check_output([str(self.binary), "proof", str(source)], text=True)
        self.assertEqual(text, "proof=0 cursor=13 faults=4\n")

    def test_exact_local_data_and_single_bit_changes(self):
        source = ROOT / "data/inputs/windows/data.win"
        if not source.exists():
            self.skipTest("private original data not present")
        expected = "36e4a251d7b687f2d742a8e911cb1e1185aea99e36529fcf32cd18d445a355e3"
        self.assertEqual(hashlib.sha256(source.read_bytes()).hexdigest(), expected)
        text = subprocess.check_output([str(self.binary), "proof", str(source)], text=True)
        self.assertEqual(text, "proof=1 cursor=13 faults=4\n")

    def test_parse_hook_precedes_normalization_and_is_mister_only(self):
        text = (SRC / "data_win.c").read_text()
        hook = text.index("dw->am2rLightSurfaceOwnershipVerified = am2rDataProofMatches")
        self.assertLess(hook, text.index("// Validate FORM header"))
        self.assertIn("#ifdef USE_MISTER", text[hook-30:hook])
        self.assertEqual(text.count("am2rDataProofMatches("), 1)


if __name__ == "__main__":
    unittest.main()
