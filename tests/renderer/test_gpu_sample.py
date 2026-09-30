#!/usr/bin/env python3
"""Exercise the actual read-only GPU sampler without any device access."""
from pathlib import Path
import os
import shutil
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parents[2]


def main():
    source = (ROOT / "tools/am2r_gpu_sample.c").read_text()
    definitions = source[source.index("#define CONTROL_PHYS"):source.index("static uint64_t monotonic_ns")]
    snapshot = source[source.index("static int snapshot("):source.index("\nint main(")]
    zig = ROOT / "third_party/zig/zig-x86_64-windows-0.16.0/zig.exe"
    compiler = [str(zig), "cc"] if zig.exists() else [shutil.which("cc") or "cc"]
    with tempfile.TemporaryDirectory(prefix="am2r-gpu-sample-") as folder:
        directory = Path(folder)
        include = directory / "gpu_sample_under_test.inc"
        env = dict(os.environ)
        env["ZIG_GLOBAL_CACHE_DIR"] = str(ROOT / "data/build/zig-sampler-tests-global")
        env["ZIG_LOCAL_CACHE_DIR"] = str(directory / "zig-local")
        binary = directory / ("sampler.exe" if os.name == "nt" else "sampler")
        command = [*compiler, "-std=c99", "-O2", "-Wall", "-Wextra", "-Werror",
                   "-I", str(directory), str(Path(__file__).with_name("gpu_sample_regression.c")),
                   "-o", str(binary)]
        include.write_text(definitions + snapshot)
        subprocess.run(command, check=True, env=env)
        subprocess.run([str(binary)], check=True)
        # Prove the regression reaches the previously discarded fourth buffer.
        old = "control[2] != command_phys || control[0] != GPU_MAGIC) return 0;"
        assert snapshot.count(old) == 1
        mutation = snapshot.replace(old, "control[2] != command_phys || control[0] != GPU_MAGIC ||\n"
                                    "        (completion >> 30) >= 3u) return 0;")
        include.write_text(definitions + mutation)
        subprocess.run(command, check=True, env=env)
        result = subprocess.run([str(binary)], capture_output=True, text=True)
        assert result.returncode != 0 and "legal buffer3 was rejected" in result.stderr
        print("GPU sampler mutation witness: restoring the three-buffer filter fails specifically on buffer3")


if __name__ == "__main__":
    main()
