#!/usr/bin/env python3
"""Compile the real offscreen backend transaction/ownership code in ordinary RAM.

The fixture mocks GPU submission and opacity proofs, not transaction functions.
It does not prove hardware speed or shader pixel equivalence.
"""
import os
import argparse
from pathlib import Path
import re
import shutil
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parents[2]


def function(source, name):
    match = re.search(r"^(?:static )?(?:bool|void|uint32_t|uint64_t|MisterGpuTexture\*) " +
                      re.escape(name) + r"\([^;{]*\)\s*\{", source, re.M)
    if not match:
        raise ValueError(name)
    opening = source.index("{", match.start())
    depth, end = 1, opening + 1
    while depth:
        depth += (source[end] == "{") - (source[end] == "}")
        end += 1
    return source[match.start():end]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--arm-output", type=Path,
                        help="Build an ordinary-RAM ARM executable; do not run hardware")
    args = parser.parse_args()
    source = (ROOT / "third_party/Butterscotch/src/backends/mister.c").read_text()
    types = source[source.index("typedef struct { uint64_t word[8]; }"):
                   source.index("static Runner *g_runner")]
    names = ("MisterGpu_hasFloorTint", "MisterGpu_beginFrame", "reserveGpuTexture", "textureRecordForPhysical",
             "MisterGpu_beginOffscreen", "MisterGpu_abortOffscreen",
             "offscreenAxisSourceIsStable", "MisterGpu_commitOffscreen",
             "MisterGpu_findOffscreen", "MisterGpu_releaseOffscreen",
             "MisterGpu_addClear", "gpuBlendFlags", "MisterGpu_addFill",
             "MisterGpu_addBlit", "MisterGpu_addBlitFloorTint", "MisterGpu_finishFrame", "MisterGpu_continueFrame",
             "MisterGpu_useSoftwareFrame", "cullFullyCoveredPrefix", "optimizeGpuScene")
    code = '#include "mister_gpu_arena_impl.h"\n' + "\n\n".join(function(source, name) for name in names)
    defines = "\n".join(line for line in source.splitlines() if line.startswith("#define ") and
                        line.split()[1] in {"MISTER_WIDTH", "MISTER_HEIGHT", "MISTER_FB_BYTES",
                            "GPU_COMMAND_BUFFER_COUNT", "GPU_COMMAND_BYTES", "GPU_COMMAND_CAPACITY",
                            "GPU_TEXTURE_PHYS", "GPU_TEXTURE_BYTES", "GPU_TEXTURE_RECORDS",
                            "GPU_OFFSCREEN_RECORDS"})
    zig = ROOT / "third_party/zig/zig-x86_64-windows-0.16.0/zig.exe"
    compiler = [str(zig), "cc"] if zig.exists() else [shutil.which("cc") or "cc"]
    if args.arm_output:
        if not zig.exists():
            parser.error("ARM output requires the repository Zig compiler")
        compiler += ["-target", "arm-linux-gnueabihf.2.30", "-mcpu=cortex_a9"]
    with tempfile.TemporaryDirectory(prefix="am2r-offscreen-backend-") as folder:
        directory = Path(folder)
        (directory / "offscreen_types.inc").write_text(defines + "\n" + types)
        (directory / "offscreen_production.inc").write_text(code)
        env = dict(os.environ)
        env["ZIG_GLOBAL_CACHE_DIR"] = str(ROOT / "data/build/zig-offscreen-tests-global")
        env["ZIG_LOCAL_CACHE_DIR"] = str(directory / "zig-local")
        binary = args.arm_output or directory / ("offscreen.exe" if os.name == "nt" else "offscreen")
        subprocess.run([*compiler, "-std=c11", "-O2", "-Wall", "-Wextra", "-Werror",
                        "-I", str(directory), "-I", str(ROOT / "third_party/Butterscotch/src/backends"),
                        str(Path(__file__).with_name("offscreen_backend_regression.c")),
                        "-o", str(binary)], check=True, env=env)
        if args.arm_output:
            print("Built ARM ordinary-RAM backend test; no hardware execution:", binary)
        else:
            subprocess.run([str(binary)], check=True)


if __name__ == "__main__":
    main()
