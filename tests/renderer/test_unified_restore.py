#!/usr/bin/env python3
"""Failure injection through the actual checkpoint hook and surface uploader."""
import os
from pathlib import Path
import subprocess
import tempfile
from test_unified_bridge import extract

ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "third_party/Butterscotch/src"


def main():
    backend = (SRC / "backends/mister.c").read_text()
    surfaces = (SRC / "backends/mister_surfaces_impl.h").read_text()
    function = extract(backend, "MisterGpu_afterSaveStateRestore")
    assert function.count("return ") == 3
    assert function.count("return gpuCompleteSaveStateRestore(") == 3
    functions = "\n\n".join([
        extract(surfaces, "gpuSurfaceRestoreCheckpoint"),
        extract(backend, "gpuCompleteSaveStateRestore"), function,
    ])
    with tempfile.TemporaryDirectory(prefix="am2r-unified-restore-") as directory:
        temp = Path(directory)
        (temp / "unified_restore_production.inc").write_text(functions)
        binary = temp / ("restore.exe" if os.name == "nt" else "restore")
        env = dict(os.environ)
        env["ZIG_GLOBAL_CACHE_DIR"] = str(ROOT / "data/build/zig-global-cache")
        env["ZIG_LOCAL_CACHE_DIR"] = str(temp / "zig-cache")
        subprocess.run([
            str(ROOT / "third_party/zig/zig-x86_64-windows-0.16.0/zig.exe"), "cc",
            "-std=gnu99", "-O2", "-Wall", "-Wextra", "-Werror", "-I", str(temp),
            str(Path(__file__).with_name("unified_restore_regression.c")), "-o", str(binary),
        ], env=env, check=True)
        for mode in ("success", "legacy-success", "legacy-init-fail", "legacy-framebuffer-fail"):
            result = subprocess.run([str(binary), mode], capture_output=True, text=True)
            assert result.returncode == 0 and "RETURNED_AFTER_RESTORE" in result.stdout, result
        cases = [("partial", str(index), 1 + index * 2) for index in range(3)]
        cases += [("init-fail", None, 0), ("framebuffer-fail", None, 1),
                  ("failed-manager", None, 7), ("generic-lost", None, 7), ("targets-lost", None, 1)]
        for mode, argument, expected_copies in cases:
            args = [str(binary), mode] + ([] if argument is None else [argument])
            result = subprocess.run(args, capture_output=True, text=True)
            assert result.returncode != 0 and "refusing partial GPU state" in result.stderr, result
            assert f"RESTORE_UNMAPPED copies={expected_copies}" in result.stderr, result
            assert "RETURNED_AFTER_RESTORE" not in result.stdout, result
        print("Actual unified restore hook PASS: 4 success/legacy cases, 8 fatal failures including every partial-surface boundary")


if __name__ == "__main__":
    main()
