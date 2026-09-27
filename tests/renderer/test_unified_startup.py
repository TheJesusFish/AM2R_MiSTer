#!/usr/bin/env python3
"""Compile the real initializer/restore guard; fake only external dependencies."""
import os
from pathlib import Path
import subprocess
import tempfile
from test_unified_bridge import extract

ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "third_party/Butterscotch/src"


def main():
    sw = (SRC / "sw_renderer.c").read_text()
    bridge = (SRC / "sw_unified_renderer.h").read_text()
    backend = (SRC / "backends/mister.c").read_text()
    init = extract(backend, "initGpu")
    # The actual negotiated path must call the tested guard after completing
    # the legacy probe, never restore its bool with a weaker second predicate.
    assert init.index("GPU_CAPABILITY_MAGIC_WORD] = 0") < init.index("submitGpuCommandsAsync()")
    assert init.index("waitGpuCompletion(&cycles)") < init.index("gpuRestoreUnifiedModeAfterProbe(restoreUnified)")
    assert init.count("gpuRestoreUnifiedModeAfterProbe(restoreUnified)") == 1
    assert "g_gpu_unified_enabled = restoreUnified" not in init
    assert "g_gpu_generic_primitive =" in init
    functions = "\n\n".join([
        extract(sw, "swOffscreenEnabledFromSetting"),
        extract(sw, "swUnifiedEnabledFromSetting"),
        extract(bridge, "swUnifiedFatal"),
        extract(sw, "swInit"),
        extract(backend, "gpuRestoreUnifiedModeAfterProbe"),
    ])
    with tempfile.TemporaryDirectory(prefix="am2r-unified-startup-") as directory:
        temp = Path(directory)
        (temp / "unified_startup_production.inc").write_text(functions)
        binary = temp / ("startup.exe" if os.name == "nt" else "startup")
        env = dict(os.environ)
        env["ZIG_GLOBAL_CACHE_DIR"] = str(ROOT / "data/build/zig-global-cache")
        env["ZIG_LOCAL_CACHE_DIR"] = str(temp / "zig-cache")
        subprocess.run([
            str(ROOT / "third_party/zig/zig-x86_64-windows-0.16.0/zig.exe"), "cc",
            "-std=gnu99", "-O2", "-Wall", "-Wextra", "-Werror", "-DUSE_MISTER",
            "-I", str(temp), str(Path(__file__).with_name("unified_startup_regression.c")),
            "-o", str(binary),
        ], env=env, check=True)
        subprocess.run([str(binary)], check=True)
        fatal_cases = 0
        for available in (False, True):
            for capabilities in range(32):
                capable = available and capabilities & 15 == 15
                result = subprocess.run([str(binary), "restore", str(capabilities), str(int(available))], capture_output=True, text=True)
                if capable:
                    assert result.returncode == 0 and "RETURNED_AFTER_RESTORE" in result.stdout
                    assert "RESTORE_UNMAPPED" not in result.stderr
                else:
                    assert result.returncode != 0 and "checkpoint requires" in result.stderr
                    assert "RESTORE_UNMAPPED" in result.stderr and "RETURNED_AFTER_RESTORE" not in result.stdout
                    fatal_cases += 1
        for capabilities, available in ((0, True), (1, True), (7, True), (8, True), (15, False), (15, True), (31, True)):
            result = subprocess.run([str(binary), "strict", str(capabilities), str(int(available))], capture_output=True, text=True)
            capable = available and capabilities & 15 == 15
            assert (result.returncode == 0) == capable
            if not capable:
                assert "strict unified renderer unavailable" in result.stderr
        print(f"Restore fail-closed subprocess checks PASS: {fatal_cases}; strict startup checks PASS: 7")


if __name__ == "__main__":
    main()
