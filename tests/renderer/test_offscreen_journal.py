#!/usr/bin/env python3
"""Compile the actual SW offscreen journal and pixel-barrier implementations.

Backend calls are stubs: this proves CPU replay/lifecycle semantics, not FPGA
execution, DDR ownership, or frame time. Use the backend and hardware tests too.
"""
from __future__ import annotations

import argparse
import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parents[2]
SOURCE = ROOT / "third_party/butterscotch/src"


def extract_function(source: str, name: str) -> str:
    pattern = (r"^(?:static )?(?:inline )?(?:bool|void|int32_t) " +
               re.escape(name) + r"\([^;{}]*\)\s*\{")
    match = re.search(pattern, source, re.M)
    if match is None:
        raise ValueError("Missing production function " + name)
    opening = source.index("{", match.start())
    depth, pos = 1, opening + 1
    while depth:
        if source[pos] == "{":
            depth += 1
        elif source[pos] == "}":
            depth -= 1
        pos += 1
    return source[match.start():pos]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--arm-output", type=Path)
    args = parser.parse_args()
    source = (SOURCE / "sw_renderer.c").read_text()
    initializer = extract_function(source, "swInit")
    if not re.search(r"sw->offscreenEnabled\s*=\s*swOffscreenEnabledFromSetting\s*\(\s*"
                     r'getenv\("AM2R_GPU_OFFSCREEN"\)\s*,\s*MisterGpu_hasFloorTint\(\)\s*\)',
                     initializer):
        raise AssertionError("swInit must select the offscreen default from the probed capability")
    helpers = source[source.index("static inline uint8_t clampByte"):
                     source.index("static uint32_t blendFactorByte")]
    journal = source[source.index("#define SW_OFFSCREEN_MAX_OPS"):
                     source.index("static bool swGpuTryAffineQuad")]
    functions = "\n\n".join(extract_function(source, name) for name in (
        "swMarkSurfaceWrite", "swSurfaceExists", "swSurfaceCopy", "swSurfaceGetPixels",
        "swOffscreenEnabledFromSetting",
    ))
    zig = ROOT / "third_party/zig/zig-x86_64-windows-0.16.0/zig.exe"
    compiler = [str(zig), "cc"] if zig.exists() else [shutil.which("cc") or "cc"]
    with tempfile.TemporaryDirectory(prefix="am2r-journal-") as folder:
        directory = Path(folder)
        (directory / "offscreen_journal_production.inc").write_text(
            helpers + "\n" + journal + "\n" + functions)
        env = dict(os.environ)
        env["ZIG_GLOBAL_CACHE_DIR"] = str(ROOT / "data/build/zig-journal-tests-global")
        env["ZIG_LOCAL_CACHE_DIR"] = str(directory / "zig-local")
        command = [*compiler, "-std=c23", "-O3", "-DUSE_MISTER=1", "-Wall", "-Wextra",
                   "-Wno-unused-function", "-I", str(directory), "-I", str(SOURCE),
                   str(Path(__file__).with_name("offscreen_journal_regression.c")),
                   str(SOURCE / "mister_offscreen.c")]
        if args.arm_output:
            if not zig.exists():
                raise RuntimeError("ARM test requires the configured Zig compiler")
            binary = args.arm_output.resolve()
            binary.parent.mkdir(parents=True, exist_ok=True)
            command += ["-target", "arm-linux-musleabihf", "-mcpu=cortex_a9",
                        "-mfpu=neon", "-mfloat-abi=hard", "-static"]
        else:
            binary = directory / ("journal.exe" if os.name == "nt" else "journal")
        command += ["-o", str(binary)]
        if os.name != "nt" or args.arm_output:
            command.append("-lm")
        subprocess.run(command, check=True, env=env)
        if args.arm_output:
            print("ARM journal test built, not executed:", binary)
        else:
            subprocess.run([str(binary)], check=True)


if __name__ == "__main__":
    main()
