#!/usr/bin/env python3
"""Test actual pointer-to-surface cache across high-water growth and lifetime changes."""
import os
from pathlib import Path
import subprocess
import tempfile

from test_unified_bridge import extract

ROOT = Path(__file__).resolve().parents[2]


def main():
    source = (ROOT / "third_party/Butterscotch/src/sw_unified_renderer.h").read_text()
    with tempfile.TemporaryDirectory(prefix="am2r-surface-source-cache-") as directory:
        temp = Path(directory)
        (temp / "surface_source_cache_actual.inc").write_text(extract(source, "swUnifiedSourceId"))
        binary = temp / ("source-cache.exe" if os.name == "nt" else "source-cache")
        env = dict(os.environ)
        env["ZIG_GLOBAL_CACHE_DIR"] = str(ROOT / "data/build/zig-global-cache")
        env["ZIG_LOCAL_CACHE_DIR"] = str(temp / "zig-cache")
        subprocess.run([
            str(ROOT / "third_party/zig/zig-x86_64-windows-0.16.0/zig.exe"),
            "cc", "-std=gnu99", "-O2", "-Wall", "-Wextra", "-Werror",
            "-I", str(temp), str(Path(__file__).with_name("surface_source_cache_regression.c")),
            "-o", str(binary),
        ], env=env, check=True)
        subprocess.run([str(binary)], check=True)


if __name__ == "__main__":
    main()
