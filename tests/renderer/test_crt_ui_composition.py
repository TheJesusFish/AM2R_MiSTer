#!/usr/bin/env python3
"""Test production HUD identity, glyph/composite hooks and both raster routes.

The device rasterizers are observation sinks. Synthetic opaque HUD pixels make
translation/double-inset failures visible; this is not RTL/native-game evidence.
"""
from __future__ import annotations

import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

from test_unified_bridge import extract

ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "third_party/Butterscotch/src"


class CrtUiComposition(unittest.TestCase):
    def test_actual_composition_and_dispatch(self):
        sw = (SRC / "sw_renderer.c").read_text()
        names = (
            "swCrtUiHudSurface", "swCrtUiHudCompositeOffset", "swApplyCrtUiTextOffset",
            "swGpuIntersectSurfaceContentBounds",
            "rasterizeQuad", "swBeginView", "worldToScreen", "swSurfaceExists",
            "swDrawPixelBuffer", "swDrawSurfaceColor",
        )
        zig = ROOT / "third_party/zig/zig-x86_64-windows-0.16.0/zig.exe"
        cc = [str(zig), "cc"] if zig.exists() else [shutil.which("cc") or "cc"]
        with tempfile.TemporaryDirectory(prefix="am2r-crt-ui-") as directory:
            folder = Path(directory)
            production = "\n\n".join(extract(sw, name) for name in names)
            include = folder / "crt_ui_production.inc"
            include.write_text(production)
            binary = folder / ("crt_ui.exe" if os.name == "nt" else "crt_ui")
            env = dict(os.environ, ZIG_GLOBAL_CACHE_DIR=str(ROOT / "data/build/zig-global-cache"),
                       ZIG_LOCAL_CACHE_DIR=str(folder / "zig-cache"))
            command = [
                *cc, "-std=gnu99", "-O2", "-Wall", "-Wextra", "-Werror",
                "-Wno-unused-function", "-DUSE_MISTER", "-DENABLE_WAD14",
                "-I", str(SRC), "-I", str(folder),
                "-I", str(ROOT / "third_party/Butterscotch/vendor/stb/ds"),
                str(Path(__file__).with_name("crt_ui_composition_regression.c")),
                "-o", str(binary),
            ]
            subprocess.run(command, check=True, env=env)
            result = subprocess.check_output([str(binary)], text=True)
            print(result.strip())
            self.assertIn("CRT UI composition PASS", result)
            # Mutation witness: reintroduce the actual old clipped-bottom
            # subtraction only in the disposable extracted fixture. The
            # translated-HUD crop assertions must detect it.
            old = "if (*maxY > bottom) *maxY = bottom;"
            self.assertEqual(production.count(old), 1)
            include.write_text(production.replace(old, "(void)bottom; *maxY -= 240 - sourceMaxY;"))
            subprocess.run(command, check=True, env=env)
            broken = subprocess.run([str(binary)], text=True, capture_output=True)
            self.assertNotEqual(broken.returncode, 0)
            self.assertIn("y1==distance+32", broken.stderr)
            print("CRT UI mutation witness PASS: old clipped-bottom subtraction rejected.")

    def test_hook_location_and_independent_data_proof(self):
        sw = (SRC / "sw_renderer.c").read_text()
        for name in ("swDrawText", "swDrawTextColor"):
            draw = extract(sw, name)
            self.assertEqual(draw.count("swApplyCrtUiTextOffset(sw, verts)"), 1)
            self.assertLess(draw.index("swApplyCrtUiTextOffset"), draw.index("rasterizeQuad"))
        surface = extract(sw, "swDrawSurfaceColor")
        self.assertEqual(surface.count("swCrtUiHudCompositeOffset"), 1)
        self.assertLess(surface.index("swCrtUiHudCompositeOffset"), surface.index("swDrawPixelBuffer"))
        legacy = extract(sw, "rasterizeAxisAlignedQuad")
        self.assertNotIn("CrtUi_", legacy)
        self.assertNotIn("hudOffset", legacy)
        self.assertIn("cropHeight <= 64", legacy)  # retain the upload optimization
        self.assertIn("swGpuIntersectSurfaceContentBounds(v[0].x, v[0].y,", legacy)
        self.assertNotIn("maxY -= 240 - maxSourceY", legacy)
        helper = extract(sw, "swCrtUiHudSurface")
        self.assertNotIn("surfaceContent", helper)  # restored bounds may be full-frame
        self.assertNotIn("getenv", helper)
        parser = extract((SRC / "data_win.c").read_text(), "DataWin_parse")
        assignment = "dw->am2rLightSurfaceOwnershipVerified = am2rDataProofMatches(wholeFileData, fileSize, file);"
        self.assertIn(assignment, parser)
        self.assertNotIn("AM2R_LIGHT_ORPHAN_CLEANUP", parser)
        builtin = extract((SRC / "vm_builtins.c").read_text(), "builtin_draw_background_ext")
        self.assertIn("CrtUi_am2rTitleBackgroundOffset", builtin)


if __name__ == "__main__":
    unittest.main()
