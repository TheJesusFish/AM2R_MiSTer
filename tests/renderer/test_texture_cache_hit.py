#!/usr/bin/env python3
"""Check production unchanged-texture dispatch against bounded backend mocks.

Actual helpers/wrapper are extracted; no device, game, pixel-equivalence or
performance claim is made. Mutation controls reject fence-first dispatch and
omitted mixed versioned/unversioned revision bookkeeping.
"""
from pathlib import Path
import os
import re
import shutil
import subprocess
import tempfile
import unittest

from test_unified_bridge import extract

ROOT = Path(__file__).resolve().parents[2]


class TextureCacheHit(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.directory = tempfile.TemporaryDirectory(prefix="am2r-texture-cache-hit-")
        cls.addClassCleanup(cls.directory.cleanup)
        temp = Path(cls.directory.name)
        source = (ROOT / "third_party/Butterscotch/src/backends/mister.c").read_text()
        texture_type = re.search(
            r"typedef struct \{\s*const void\* source;[\s\S]+?\} MisterGpuTexture;", source)
        if texture_type is None:
            raise AssertionError("missing production texture record type")
        equal = extract(source, "gpuBuffersEqual")
        helper = extract(source, "gpuUnchangedDynamicTexture")
        wrapper = extract(source, "MisterGpu_uploadDynamicTexture")
        zig = ROOT / "third_party/zig/zig-x86_64-windows-0.16.0/zig.exe"
        compiler = [str(zig), "cc"] if zig.exists() else [shutil.which("cc") or "cc"]
        compiler += ["-std=gnu99", "-O2", "-Wall", "-Wextra", "-Werror",
                     "-Wno-unused-function"]
        env = dict(os.environ, ZIG_GLOBAL_CACHE_DIR=str(ROOT / "data/build/zig-global-cache"),
                   ZIG_LOCAL_CACHE_DIR=str(temp / "zig-cache"))
        harness = str(Path(__file__).with_name("texture_cache_hit_regression.c"))

        def compile_fixture(name, fixture_helper, fixture_wrapper):
            directory = temp / name
            directory.mkdir()
            (directory / "texture_cache_hit_types.inc").write_text(texture_type.group(0) + "\n")
            (directory / "texture_cache_hit_actual.inc").write_text(
                "\n\n".join((equal, fixture_helper, fixture_wrapper)) + "\n")
            binary = directory / ("cache-hit.exe" if os.name == "nt" else "cache-hit")
            subprocess.run([*compiler, "-I", str(directory), harness, "-o", str(binary)],
                           env=env, check=True, timeout=120)
            return binary

        cls.binary = compile_fixture("actual", helper, wrapper)
        old_wrapper = """uint32_t MisterGpu_uploadDynamicTexture(const void* rgbaPixels, size_t bytes) {
    RD_SCOPE(RD_UPLOAD, -1, 0);
    if (g_gpu_unified_enabled && !MisterGpu_flushNoPresent()) return 0;
    return uploadDynamicTexture(rgbaPixels, bytes, false, 0);
}"""
        cls.fence_first_binary = compile_fixture("old-fence-first", helper, old_wrapper)
        missing_memo, count = re.subn(
            r"(?m)^[ \t]*\w+->source_revision(?:_valid)?\s*=\s*(?:0|false);[ \t]*$",
            "", helper)
        if count != 2:
            raise AssertionError("update revision mutation control to match production helper")
        cls.missing_memo_binary = compile_fixture("missing-revision-memo", missing_memo, wrapper)

    def run_case(self, case):
        result = subprocess.run([str(self.binary), case], capture_output=True, text=True, timeout=30)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("PASS", result.stdout)

    def test_repeated_white_hits_do_not_flush_or_upload(self): self.run_case("hits")
    def test_changed_same_frame_data_keeps_barrier_before_write(self): self.run_case("changed")
    def test_release_pointer_reuse_reset_and_active_address(self): self.run_case("lifetime")
    def test_invalid_records_and_revision_only_match_cannot_hit(self): self.run_case("guards")
    def test_plain_hit_clears_mixed_callers_revision_memo(self): self.run_case("metadata")
    def test_gpu_failure_and_invalid_inputs_fail_closed(self): self.run_case("failure")
    def test_nonunified_path_still_calls_existing_uploader(self): self.run_case("legacy")
    def test_unaligned_source_and_shadow_are_not_modified(self): self.run_case("immutable")

    def test_historical_flush_before_lookup_mutation_is_rejected(self):
        result = subprocess.run([str(self.fence_first_binary), "hits"],
                                capture_output=True, text=True, timeout=30)
        self.assertNotEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("flushCalls == 0", result.stderr)

    def test_omitted_revision_bookkeeping_mutation_is_rejected(self):
        result = subprocess.run([str(self.missing_memo_binary), "metadata"],
                                capture_output=True, text=True, timeout=30)
        self.assertNotEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("source_revision == 0", result.stderr)


if __name__ == "__main__":
    unittest.main()
