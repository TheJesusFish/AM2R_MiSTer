#!/usr/bin/env python3
"""Exercise actual surface identity/lifetime functions with bounded mock storage.

No game data, device access, or renderer pixel algorithm is needed. GPU hooks
track ownership only; these tests do not replace real GPU/rendering tests.
"""
from pathlib import Path
import os
import re
import subprocess
import tempfile
import unittest

from test_unified_bridge import extract

ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "third_party/Butterscotch/src"


class SurfaceIdLifetime(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.directory = tempfile.TemporaryDirectory(prefix="am2r-surface-id-lifetime-")
        cls.addClassCleanup(cls.directory.cleanup)
        temp = Path(cls.directory.name)
        source = (SRC / "sw_renderer.c").read_text()
        header = (SRC / "sw_renderer.h").read_text()
        limit = re.search(r"^#define SW_SURFACE_ID_LIMIT\s+([^\r\n]+)", header, re.M)
        if limit is None:
            raise AssertionError("missing production surface ID bound")
        functions = (
            "swCreateSurface", "swSurfaceExists", "swSurfaceFree",
            "SWRenderer_fastStateResetSurfaces", "SWRenderer_fastStateReserveSurfaceIds",
            "SWRenderer_fastStateRestoreSurface",
        )
        production = "\n\n".join(extract(source, name) for name in functions)
        (temp / "surface_id_lifetime_production.inc").write_text(production)
        (temp / "surface_id_lifetime_limit.inc").write_text(limit.group(0) + "\n")
        env = dict(os.environ, ZIG_GLOBAL_CACHE_DIR=str(ROOT / "data/build/zig-global-cache"),
                   ZIG_LOCAL_CACHE_DIR=str(temp / "zig-cache"))
        cls.binary = temp / ("surface-id-lifetime.exe" if os.name == "nt" else "surface-id-lifetime")
        compiler = [
            str(ROOT / "third_party/zig/zig-x86_64-windows-0.16.0/zig.exe"), "cc",
            "-std=gnu99", "-O2", "-Wall", "-Wextra", "-Werror", "-DUSE_MISTER",
        ]
        harness = str(Path(__file__).with_name("surface_id_lifetime_regression.c"))
        subprocess.run([*compiler, "-I", str(temp), harness, "-o", str(cls.binary)],
                       env=env, check=True, timeout=120)

        # Mutation control: compile a temporary fixture with the former hole
        # reuse policy. Never modify the production source under test.
        monotonic = """uint32_t id = sw->surfaceCount;
    if (id >= SW_SURFACE_ID_LIMIT) return -1;
    swEnsureSurfaceCapacity(sw, id + 1);
    sw->surfaceCount = id + 1;"""
        old_hole_reuse = """uint32_t id;
    for (id = 1; id < sw->surfaceCount; ++id)
        if (!sw->surfaceExistsFlags[id]) break;
    if (id >= SW_SURFACE_ID_LIMIT) return -1;
    if (id == sw->surfaceCount) {
        swEnsureSurfaceCapacity(sw, id + 1);
        sw->surfaceCount = id + 1;
    }"""
        if production.count(monotonic) != 1:
            raise AssertionError("update mutation control to match production allocator")
        mutant = temp / "old-hole-reuse"
        mutant.mkdir()
        (mutant / "surface_id_lifetime_production.inc").write_text(
            production.replace(monotonic, old_hole_reuse))
        (mutant / "surface_id_lifetime_limit.inc").write_text(limit.group(0) + "\n")
        cls.mutant_binary = mutant / cls.binary.name
        subprocess.run([*compiler, "-I", str(mutant), harness, "-o", str(cls.mutant_binary)],
                       env=env, check=True, timeout=120)

    def run_case(self, case):
        result = subprocess.run([str(self.binary), case], capture_output=True, text=True, timeout=30)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("PASS", result.stdout)

    def test_stale_transition_handle_cannot_free_new_boss(self): self.run_case("stale")
    def test_20000_retired_ids_reclaim_cpu_and_gpu_storage(self): self.run_case("churn")
    def test_retained_map_keeps_existing_reuse_contract(self): self.run_case("map")
    def test_zero_invalid_dimensions_and_application_protection(self): self.run_case("invalid")
    def test_last_id_and_cap_fail_before_allocating(self): self.run_case("cap")
    def test_restore_preserves_freed_trailing_id_high_water(self): self.run_case("restore")
    def test_reset_invalidates_and_reclaims_every_surface(self): self.run_case("reset")

    def test_old_hole_reuse_mutation_is_rejected(self):
        result = subprocess.run([str(self.mutant_binary), "stale"],
                                capture_output=True, text=True, timeout=30)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("boss > snapshot", result.stderr)


if __name__ == "__main__":
    unittest.main()
