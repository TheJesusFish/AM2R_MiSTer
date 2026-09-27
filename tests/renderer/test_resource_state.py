#!/usr/bin/env python3
"""Bounded actual-helper tests; no game data, GPU access, or release execution."""
from __future__ import annotations
import os
from pathlib import Path
import re
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "third_party/Butterscotch/src"


class ResourceState(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.directory = tempfile.TemporaryDirectory(prefix="am2r-resource-state-")
        temp = Path(cls.directory.name)
        backend = (SRC / "backends/mister.c").read_text()
        start = backend.index("// RESOURCE_STATE_DIAGNOSTICS_BEGIN")
        end = backend.index("// RESOURCE_STATE_DIAGNOSTICS_END", start)
        cls.helper = backend[start:end]
        (temp / "resource_state_production.inc").write_text(cls.helper)
        types = backend[backend.index("typedef struct { uint64_t word[8]; }"):
                        backend.index("static Runner *g_runner")]
        defines = "\n".join(line for line in backend.splitlines()
                            if line.startswith("#define ") and line.split()[1] in {
                                "GPU_TEXTURE_RECORDS", "GPU_OFFSCREEN_RECORDS",
                                "GPU_COMMAND_BUFFER_COUNT", "GPU_TEXTURE_BYTES"})
        manager = (SRC / "backends/mister_surfaces_impl.h").read_text()
        surface_types = manager[manager.index("#define GPU_SURFACE_RECORDS"):
                                manager.index("static GpuSurface g_gpu_surfaces")]
        arena = (SRC / "backends/mister_gpu_arena_impl.h").read_text()
        arena_types = arena[arena.index("#define GPU_ARENA_SEGMENTS"):
                            arena.index("static inline void gpuArenaReset")]
        (temp / "resource_state_types.inc").write_text(defines + "\n" + types + surface_types + arena_types)
        env = dict(os.environ)
        env["ZIG_GLOBAL_CACHE_DIR"] = str(ROOT / "data/build/zig-global-cache")
        env["ZIG_LOCAL_CACHE_DIR"] = str(temp / "zig-cache")
        cls.binary = temp / ("resources.exe" if os.name == "nt" else "resources")
        cls.compiler = [str(ROOT / "third_party/zig/zig-x86_64-windows-0.16.0/zig.exe"),
                        "cc", "-std=gnu99", "-O2", "-Wall", "-Wextra", "-Werror",
                        "-DUSE_MISTER", "-DENABLE_WAD14", "-DMISTER_RENDER_DIAGNOSTICS",
                        "-I", str(SRC), "-I", str(temp), "-I",
                        str(ROOT / "third_party/Butterscotch/vendor/stb/ds")]
        subprocess.run([*cls.compiler, str(Path(__file__).with_name("resource_state_regression.c")),
                        "-o", str(cls.binary)], check=True, env=env)
        cls.env = env
        cls.temp = temp

    @classmethod
    def tearDownClass(cls):
        cls.directory.cleanup()

    def run_sample(self, setting, scenario="normal"):
        return subprocess.check_output([str(self.binary), setting, scenario], text=True)

    def test_disabled_invalid_and_private_input_never_logged(self):
        for setting in ("unset", "", "0", "-1", "+3", "1 ", "721", "9999999999",
                        "C:/private/credentials", "0000"):
            with self.subTest(setting=setting):
                self.assertEqual(self.run_sample(setting),
                                 "QA_RESOURCE samples=0 env_reads=1 read_only=1\n")

    def test_exact_counts_and_render_state(self):
        text = self.run_sample("2")
        expected = {
            "sample": "2/2", "frame": "300", "room": "166", "gpu_available": "1",
            "texture_highwater_bytes": "9876543", "texture_total": "4", "texture_scanned": "4",
            "arena_owned_bytes": "1152", "arena_free_bytes": "134216576",
            "arena_largest_free_bytes": "134216320", "arena_live_allocations": "2",
            "arena_initialized": "1",
            "texture_live": "3", "texture_released": "1", "texture_zero": "2",
            "offscreen_keys": "2", "offscreen_current": "1", "target_slots": "2",
            "target_live": "1", "sw_surface_total": "4", "sw_surface_scanned": "4",
            "sw_surface_live": "3", "sw_surface_truncated": "0", "atlas_total": "4",
            "atlas_scanned": "4", "atlas_loaded": "3", "atlas_zero": "1",
            "atlas_truncated": "0", "vblank_disabled": "1", "vblank_timeouts": "3",
            "blend_enabled": "1", "blend": "3", "factors": "2,2,5,6", "fog": "1",
            "write_mask": "13", "alpha_test": "1", "alpha_ref": "127", "shader": "-1",
        }
        actual = dict(re.findall(r"(\w+)=([^\s]+)", text.splitlines()[0]))
        for key, value in expected.items():
            self.assertEqual(actual[key], value, key)
        self.assertNotIn("texture_bytes", actual)
        self.assertIn("QA_RESOURCE samples=2 env_reads=1 read_only=1", text)

    def test_hard_sample_limit_and_read_only(self):
        text = self.run_sample("720")
        self.assertIn("sample=720/720 frame=215700", text)
        self.assertIn("QA_RESOURCE samples=720 env_reads=1 read_only=1", text)

    def test_legacy_renderer_is_also_observed(self):
        text = self.run_sample("1", "legacy")
        self.assertIn("unified=0 strict=0", text)
        self.assertIn("frame_eligible=1 offscreen_enabled=1", text)
        self.assertIn("QA_RESOURCE samples=1 env_reads=1 read_only=1", text)
        uninitialized = self.run_sample("1", "uninitialized")
        for token in ("arena_initialized=0", "arena_owned_bytes=0",
                      "arena_free_bytes=134217728", "arena_largest_free_bytes=134217728",
                      "arena_live_allocations=0"):
            self.assertIn(token, uninitialized)

    def test_metadata_scans_truncate_explicitly(self):
        text = self.run_sample("1", "truncated")
        for token in ("texture_total=4294967295 texture_scanned=128", "texture_released=125",
                      "sw_surface_total=4294967295 sw_surface_scanned=128",
                      "sw_surface_truncated=1", "atlas_total=4294967295 atlas_scanned=128",
                      "atlas_truncated=1", "arena_live_allocations=1024",
                      "arena_owned_bytes=131072 arena_free_bytes=0 arena_largest_free_bytes=0"):
            self.assertIn(token, text)

    def test_release_preprocessing_omits_helper_api_and_call(self):
        header = (SRC / "backends/mister_gpu.h").read_text()
        backend = (SRC / "backends/mister.c").read_text()
        helper_start = backend.rfind("#if defined(MISTER_RENDER_DIAGNOSTICS)", 0,
                                     backend.index("// RESOURCE_STATE_DIAGNOSTICS_BEGIN"))
        helper_end = backend.index("#endif", backend.index("// RESOURCE_STATE_DIAGNOSTICS_END")) + 6
        renderer = (SRC / "sw_renderer.c").read_text()
        call = ("#if defined(USE_MISTER) && defined(MISTER_RENDER_DIAGNOSTICS)\n"
                "    if ((renderer->runner->frameCount % 300) == 0)\n"
                "        MisterGpu_logResourceState(renderer);\n#endif")
        self.assertIn(call, renderer)
        probe = self.temp / "release_probe.c"
        probe.write_text(header + "\n" + backend[helper_start:helper_end] + "\nvoid probe(void){\n" + call + "\n}\n")
        compiler = [value for value in self.compiler if value != "-DMISTER_RENDER_DIAGNOSTICS"]
        output = subprocess.check_output([*compiler, "-E", "-P", str(probe)], env=self.env, text=True)
        self.assertNotIn("MisterGpu_logResourceState", output)
        self.assertNotIn("AM2R_RESOURCE_SAMPLES", output)
        self.assertNotIn("MiSTer resource state", output)


if __name__ == "__main__":
    unittest.main()
