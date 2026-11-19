#!/usr/bin/env python3
"""Compare extracted production occupancy probes with an independent linear scan.

Ordinary RAM grid/bounds/precise-test mocks check cache eligibility, sync ordering,
unchanged traversal semantics, and exact-test delegation. They are not actual
sprite-mask/OBB geometry, full-game, hardware, or frame-rate evidence.
"""
from pathlib import Path
import os
import re
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]


def extract(source, name):
    signature = re.search(r"^(?:static\s+)?(?:inline\s+)?[\w*]+\s*\b" + name +
                          r"\([^;{}]*\)\s*\{", source, re.M)
    if signature is None:
        raise AssertionError("missing production function: " + name)
    pos = source.index("{", signature.start()) + 1
    depth = 1
    while depth:
        depth += (source[pos] == "{") - (source[pos] == "}")
        pos += 1
    return source[signature.start():pos]


class PlaceOccupancy(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.directory = tempfile.TemporaryDirectory(prefix="am2r-place-occupancy-")
        cls.addClassCleanup(cls.directory.cleanup)
        temp = Path(cls.directory.name)
        source = (ROOT / "third_party/Butterscotch/src/vm_builtins.c").read_text()
        helper = extract(source, "placeUnoccupiedAt")
        wrappers = "\n\n".join(extract(source, name) for name in ("placeEmptyAt", "placeFreeAt"))
        cls.setters = {name: extract(source, name) for name in
                       ("builtin_sprite_set_offset", "builtin_sprite_set_bbox_mode")}
        setters = "\n\n".join(cls.setters.values())
        zig = ROOT / "third_party/zig/zig-x86_64-windows-0.16.0/zig.exe"
        compiler = [str(zig), "cc"] if zig.exists() else [shutil.which("cc") or "cc"]
        compiler += ["-std=gnu99", "-O2", "-Wall", "-Wextra", "-Werror",
                     "-Wno-unused-function"]
        env = dict(os.environ, ZIG_GLOBAL_CACHE_DIR=str(ROOT / "data/build/zig-global-cache"),
                   ZIG_LOCAL_CACHE_DIR=str(temp / "zig-cache"))
        harness = str(Path(__file__).with_name("place_occupancy_regression.c"))

        def compile_fixture(name, fixture_helper, float_reals=False, fixture_setters=setters):
            directory = temp / name
            directory.mkdir()
            (directory / "place_occupancy_actual.inc").write_text(
                fixture_helper + "\n\n" + wrappers + "\n\n" + fixture_setters + "\n")
            binary = directory / ("occupancy.exe" if os.name == "nt" else "occupancy")
            flags = ["-DUSE_FLOAT_REALS"] if float_reals else []
            subprocess.run([*compiler, *flags, "-I", str(directory), "-I",
                            str(ROOT / "third_party/Butterscotch/vendor/stb/ds"),
                            harness, "-o", str(binary)], env=env, check=True, timeout=120)
            return binary

        cls.binaries = [compile_fixture("actual-double", helper),
                        compile_fixture("actual-float", helper, True)]
        missing_sync, count = re.subn(r"SpatialGrid_syncGrid\([^;]*\);", "(void)0;", helper)
        if count != 1:
            raise AssertionError("update missing-sync mutation to match production helper")
        cls.missing_sync_binary = compile_fixture("missing-sync", missing_sync)
        missing_solid, count = re.subn(r"solidOnly\s*&&\s*!other->solid",
                                      "solidOnly && false", helper)
        if count != 1:
            raise AssertionError("update solid-filter mutation to match production helper")
        cls.missing_solid_binary = compile_fixture("missing-solid-filter", missing_solid)
        missing_origin_dirty, count = re.subn(
            r"SpatialGrid_markInstanceAsDirty\([^;]*\);", "(void)0;",
            cls.setters["builtin_sprite_set_offset"])
        if count != 1:
            raise AssertionError("update origin-invalidation mutation to match production setter")
        cls.missing_origin_dirty_binary = compile_fixture(
            "missing-origin-invalidation", helper, fixture_setters=missing_origin_dirty + "\n\n" +
            cls.setters["builtin_sprite_set_bbox_mode"])
        inactive_filtered, count = re.subn(r"if \(inst->destroyed\) continue;",
                                          "if (!inst->active || inst->destroyed) continue;", setters)
        if count != 2:
            raise AssertionError("update inactive-filter mutation to match production setters")
        cls.inactive_filtered_binary = compile_fixture(
            "inactive-filtered", helper, fixture_setters=inactive_filtered)

    def run_case(self, case):
        for binary in self.binaries:
            with self.subTest(real_mode=binary.parent.name):
                result = subprocess.run([str(binary), case], capture_output=True, text=True, timeout=30)
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                self.assertIn("PASS", result.stdout)

    def test_self_active_inactive_destroyed_and_solid_filters(self): self.run_case("filters")
    def test_dirty_bounds_sync_before_probe_and_real_membership_restored(self): self.run_case("dirty")
    def test_invalid_bbox_and_missing_grid_keep_exact_fallback(self): self.run_case("fallback")
    def test_touching_edges_and_rotated_mask_false_positive_delegate_precise(self): self.run_case("precise")
    def test_original_forward_instance_order_and_duplicate_entries(self): self.run_case("order")
    def test_oversized_and_out_of_room_coordinates_match_linear(self): self.run_case("extremes")
    def test_119_cached_distant_shapes_skip_bbox_recomputation(self): self.run_case("distant")
    def test_deterministic_mixed_actor_probes_match_linear(self): self.run_case("equivalence")
    def test_sprite_setters_invalidate_effective_mask_and_inactive_membership(self): self.run_case("setters")

    def test_sprite_setter_source_has_effective_mask_and_destroyed_only_guard(self):
        for name, setter in self.setters.items():
            with self.subTest(setter=name):
                self.assertRegex(setter, r"inst->maskIndex\s*>=\s*0\)?\s*\?\s*inst->maskIndex\s*:\s*inst->spriteIndex")
                self.assertRegex(setter, r"if\s*\(inst->destroyed\)\s*continue;")
                self.assertNotRegex(setter, r"!inst->active")
                self.assertIn("SpatialGrid_markInstanceAsDirty(runner->spatialGrid, inst);", setter)

    def test_missing_sync_mutation_is_rejected(self):
        result = subprocess.run([str(self.missing_sync_binary), "dirty"],
                                capture_output=True, text=True, timeout=30)
        self.assertNotEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("actual == expected", result.stderr)

    def test_missing_solid_filter_mutation_is_rejected(self):
        result = subprocess.run([str(self.missing_solid_binary), "filters"],
                                capture_output=True, text=True, timeout=30)
        self.assertNotEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("actual == expected", result.stderr)

    def test_missing_origin_invalidation_mutation_is_rejected(self):
        result = subprocess.run([str(self.missing_origin_dirty_binary), "setters"],
                                capture_output=True, text=True, timeout=30)
        self.assertNotEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("active->dirtyMarks == 1", result.stderr)

    def test_inactive_sprite_invalidation_filter_mutation_is_rejected(self):
        result = subprocess.run([str(self.inactive_filtered_binary), "setters"],
                                capture_output=True, text=True, timeout=30)
        self.assertNotEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("inactive->dirtyMarks == 1", result.stderr)


if __name__ == "__main__":
    unittest.main()
