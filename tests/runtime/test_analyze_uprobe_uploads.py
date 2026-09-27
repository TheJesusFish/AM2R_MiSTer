"""Check nested and legacy GPU-upload trace decoding."""

from __future__ import annotations

import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


SCRIPT = Path(__file__).resolve().parents[2] / "tools" / "analyze_uprobe_uploads.py"


class UploadTraceTest(unittest.TestCase):
    def test_nested_paths_are_accounted_separately(self) -> None:
        lines = (
            "game-77 [000] .... 1.000000: am2r_upload: source=0x1 bytes=16384 valid=1 revision=9\n"
            "game-77 [000] .... 1.000020: am2r_upload_sparse: source=0x2 bytes=40960\n"
            "game-77 [000] .... 1.000220: am2r_upload_sparse_ret:\n"
            "game-77 [000] .... 1.000300: am2r_upload_ret:\n"
            "game-77 [000] .... 1.001000: am2r_upload_crop: source=0x3 source_row_bytes=2048 crop_row_bytes=1280 rows=240\n"
            "game-77 [000] .... 1.001800: am2r_upload_crop_ret:\n"
        )
        with tempfile.TemporaryDirectory() as directory:
            trace = Path(directory) / "trace.txt"
            trace.write_text(lines)
            result = subprocess.run([sys.executable, str(SCRIPT), str(trace)],
                                    text=True, capture_output=True, check=False)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("kind=am2r_upload bytes=16384", result.stdout)
        self.assertIn("kind=am2r_upload_sparse bytes=40960", result.stdout)
        self.assertIn("kind=am2r_upload_crop bytes=307200", result.stdout)


if __name__ == "__main__":
    unittest.main()
