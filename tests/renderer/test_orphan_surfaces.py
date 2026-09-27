#!/usr/bin/env python3
"""Exercise the actual exact-data ownership helper; no native gameplay claim."""
from __future__ import annotations
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "third_party/Butterscotch/src"


class OrphanSurfaces(unittest.TestCase):
    def test_actual_helper_invariants(self):
        zig = ROOT / "third_party/zig/zig-x86_64-windows-0.16.0/zig.exe"
        cc = [str(zig), "cc"] if zig.exists() else [shutil.which("cc") or "cc"]
        with tempfile.TemporaryDirectory(prefix="am2r-orphan-") as directory:
            folder = Path(directory)
            binary = folder / ("orphans.exe" if os.name == "nt" else "orphans")
            env = dict(os.environ, ZIG_GLOBAL_CACHE_DIR=str(ROOT / "data/build/zig-global-cache"),
                       ZIG_LOCAL_CACHE_DIR=str(folder / "zig-cache"))
            subprocess.run([*cc,"-std=c11","-O2","-Wall","-Wextra","-Werror","-I",str(SRC),
                            "-I",str(ROOT/"third_party/Butterscotch/vendor/stb/ds"),
                            str(Path(__file__).with_name("orphan_surface_regression.c")),
                            "-o",str(binary)],check=True,env=env)
            output = subprocess.check_output([str(binary)],text=True)
            print(output.strip())
            self.assertIn("Orphan ownership helper PASS",output)

    def test_actual_hooks_and_existing_gpu_barrier(self):
        runner=(SRC/"runner.c").read_text()
        remove=runner.index("Am2rOrphanSurfaceRoomRemove(runner, inst)")
        self.assertLess(runner.index("Runner_executeEvent(runner, inst, EVENT_CLEANUP, 0)"),remove)
        self.assertLess(remove,runner.index("Instance_free(inst)",remove))
        for function in ("void Runner_reset(","void Runner_free("):
            part=runner[runner.index(function):]
            self.assertLess(part.index("Am2rOrphanSurfaceReset()"),part.index("cleanupState(runner)"))
        sw=(SRC/"sw_renderer.c").read_text()
        reset=sw[sw.index("void SWRenderer_fastStateResetSurfaces("):]
        self.assertIn("Am2rOrphanSurfaceReset()",reset[:600])
        release=(SRC/"backends/mister_surfaces_impl.h").read_text()
        release=release[release.index("bool MisterGpu_surfaceRelease("):]
        self.assertLess(release.index("MisterGpu_flushNoPresent()"),release.index("surface->live = false"))
        helper=(SRC/"am2r_orphan_surfaces_impl.h").read_text()
        self.assertNotIn("EVENT_DESTROY",helper)
        self.assertIn("surfaceFree(runner->renderer, surface)",helper)


if __name__=="__main__":
    unittest.main()
