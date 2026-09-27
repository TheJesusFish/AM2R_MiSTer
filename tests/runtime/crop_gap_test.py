"""Compile and validate actual read-only crop gap diagnostics."""
import csv
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]


class CropGapTest(unittest.TestCase):
    def compiler(self):
        zig = ROOT / "third_party/zig/zig-x86_64-windows-0.16.0/zig.exe"
        return [str(zig), "cc"] if zig.exists() else [shutil.which("cc") or "cc"]

    def test_real_collector(self):
        with tempfile.TemporaryDirectory(prefix="am2r-crop-gap-") as folder:
            folder = Path(folder)
            env = dict(os.environ, ZIG_GLOBAL_CACHE_DIR=str(ROOT / "data/build/zig-crop-gap-global"),
                       ZIG_LOCAL_CACHE_DIR=str(folder / "zig-cache"), AM2R_CROP_GAPS="1")
            binary = folder / ("test.exe" if os.name == "nt" else "test")
            subprocess.run([*self.compiler(), "-std=c23", "-O2", "-Wall", "-Wextra", "-Werror",
                            "-DMISTER_RENDER_DIAGNOSTICS", str(Path(__file__).with_suffix(".c")),
                            "-o", str(binary)], env=env, check=True)
            for disabled in (None, "0", "true", "01"):
                off_env = dict(env)
                if disabled is None:
                    off_env.pop("AM2R_CROP_GAPS", None)
                else:
                    off_env["AM2R_CROP_GAPS"] = disabled
                subprocess.run([str(binary), "--check-disabled"], env=off_env, check=True)
            subprocess.run([str(binary), str(folder / "timing.csv"), str(folder)],
                           env=env, check=True)
            with (folder / "render-crop.csv").open(newline="") as stream:
                rows = list(csv.DictReader(stream))
            summaries = {int(row["frame"]): row for row in rows if row["record_kind"] == "summary"}
            uploads = [row for row in rows if row["record_kind"] == "upload"]
            self.assertEqual(len(summaries), 8)
            self.assertNotIn(106, summaries)
            self.assertEqual(len(uploads), 8)
            self.assertEqual(summaries[105]["dropped_uploads"], "1")
            self.assertEqual(summaries[105]["recorded_uploads"], "4")
            self.assertEqual(summaries[108]["dropped_uploads"], "1")
            self.assertTrue(all(row["timing_perturbed"] == "1" for row in rows))
            for row in uploads:
                self.assertEqual(int(row["single_span_bytes"]),
                                 int(row["changed_pixel_bytes"]) + int(row["interior_gap_bytes"]))
                self.assertEqual(row["predicted_bytes_gap0"], row["changed_pixel_bytes"])

    def test_disabled_macro_has_no_arguments(self):
        with tempfile.TemporaryDirectory(prefix="am2r-crop-gap-off-") as folder:
            folder = Path(folder)
            source = folder / "disabled.c"
            source.write_text('#include "render_diagnostics.h"\n'
                              'int main(void) { RD_CROP_UPLOAD(missing(), missing(), missing(), '
                              'missing(), missing()); return 0; }\n')
            env = dict(os.environ, ZIG_GLOBAL_CACHE_DIR=str(ROOT / "data/build/zig-crop-gap-global"),
                       ZIG_LOCAL_CACHE_DIR=str(folder / "zig-cache"))
            subprocess.run([*self.compiler(), "-std=c23", "-O2", "-Wall", "-Wextra", "-Werror",
                            "-I", str(ROOT / "third_party/Butterscotch/src"), str(source),
                            "-o", str(folder / "disabled.exe")], env=env, check=True)


if __name__ == "__main__":
    unittest.main()
