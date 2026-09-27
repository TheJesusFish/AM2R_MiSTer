#!/usr/bin/env python3
"""Exercise actual renderer lifecycle glue against an independent GPU mock.

This checks CPU/GPU ownership and hook ordering, not RTL or device pixels.
The backend manager has separate actual-manager and hardware tests.
"""
from __future__ import annotations

import os
from pathlib import Path
import re
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "third_party/Butterscotch/src"


def extract(source: str, name: str) -> str:
    signature = re.search(r"^(?:static\s+)?(?:inline\s+)?[\w*]+\s*\b" + name + r"\([^;{}]*\)\s*\{", source, re.M)
    if signature is None:
        raise AssertionError("missing production definition: " + name)
    pos = source.index("{", signature.start()) + 1
    depth = 1
    while depth:
        depth += (source[pos] == "{") - (source[pos] == "}")
        pos += 1
    return source[signature.start():pos]


def main() -> None:
    bridge = (SRC / "sw_unified_renderer.h").read_text()
    sw = (SRC / "sw_renderer.c").read_text()
    bridge_names = (
        "swUnifiedFatal", "swUnifiedCheck", "swUnifiedDisable", "swUnifiedHandle",
        "swUnifiedSelect", "swUnifiedReadTarget", "swUnifiedSourceId", "swUnifiedSource",
        "swUnifiedRelease", "swUnifiedCpuBegin", "swUnifiedCpuEnd", "swUnifiedCpuAliasSnapshot",
    )
    sw_names = (
        "clampByte", "swMarkSurfaceWrite", "swUseHostTarget", "swUseSurfaceTarget",
        "swSurfaceExists", "SWRenderer_materializeSurfaces", "swClearScreen",
        "swSurfaceCopy", "swSurfaceGetPixels", "swFlush",
    )
    with tempfile.TemporaryDirectory(prefix="am2r-unified-bridge-") as directory:
        temp = Path(directory)
        (temp / "unified_bridge_production.inc").write_text(
            "static void swMarkSurfaceWrite(SWRenderer*, int32_t);\n" +
            "\n\n".join(extract(bridge, name) for name in bridge_names) + "\n" +
            "\n\n".join(extract(sw, name) for name in sw_names))
        env = dict(os.environ)
        env["ZIG_GLOBAL_CACHE_DIR"] = str(ROOT / "data/build/zig-global-cache")
        env["ZIG_LOCAL_CACHE_DIR"] = str(temp / "zig-cache")
        binary = temp / ("bridge.exe" if os.name == "nt" else "bridge")
        subprocess.run([
            str(ROOT / "third_party/zig/zig-x86_64-windows-0.16.0/zig.exe"), "cc", "-O2", "-std=gnu99",
            "-DUSE_MISTER", "-DENABLE_WAD14", "-I", str(SRC), "-I", str(temp),
            "-I", str(ROOT / "third_party/Butterscotch/vendor/stb/ds"),
            "-I", str(ROOT / "third_party/Butterscotch/vendor/sha1"),
            str(Path(__file__).with_name("unified_bridge_regression.c")), "-o", str(binary),
        ], env=env, check=True)
        subprocess.run([str(binary)], check=True)
        for reason in ("strict-raster", "strict-allocation", "device-failure", "flush-failure"):
            result = subprocess.run([str(binary), reason], capture_output=True, text=True)
            if result.returncode == 0 or "lost coherent GPU state" not in result.stderr:
                raise AssertionError(f"{reason} did not fail closed: {result.returncode}: {result.stderr}")
        print("Unified bridge fatal-path checks passed: strict raster, strict allocation and lost GPU authority.")


if __name__ == "__main__":
    main()
