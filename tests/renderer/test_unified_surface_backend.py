#!/usr/bin/env python3
"""Run the production surface manager/builders against ordinary-RAM DDR.

The mock executes the wire descriptors and records fences/publications; this is
an ownership/tiling contract test, not hardware timing or RTL pixel evidence.
"""
import argparse
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[2]


def function(source, name):
    match = re.search(r"^(?:static )?(?:bool|void|uint32_t|uint64_t|MisterGpuCommand\*) " +
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
    parser.add_argument("--arm-output", type=Path)
    args = parser.parse_args()
    backend = ROOT / "third_party/Butterscotch/src/backends"
    source = (backend / "mister.c").read_text()
    names = ("MisterGpu_beginFrame", "gpuLastBuiltCommand", "gpuBlendFlags", "MisterGpu_addClear", "MisterGpu_addFill",
             "MisterGpu_addFillVGradient", "MisterGpu_addBlit", "MisterGpu_addBlitFloorTint",
             "MisterGpu_addBlitVGradient", "MisterGpu_addAffineBlit")
    code = "\n\n".join(function(source, name) for name in names)
    zig = ROOT / "third_party/zig/zig-x86_64-windows-0.16.0/zig.exe"
    compiler = [str(zig), "cc"] if zig.exists() else [shutil.which("cc") or "cc"]
    if args.arm_output:
        compiler += ["-target", "arm-linux-gnueabihf.2.30", "-mcpu=cortex_a9"]
    with tempfile.TemporaryDirectory(prefix="am2r-unified-surface-") as folder:
        directory = Path(folder)
        (directory / "unified_builders.inc").write_text(code)
        env = dict(os.environ)
        env["ZIG_GLOBAL_CACHE_DIR"] = str(ROOT / "data/build/zig-unified-tests-global")
        env["ZIG_LOCAL_CACHE_DIR"] = str(directory / "zig-local")
        binary = args.arm_output or directory / ("unified.exe" if os.name == "nt" else "unified")
        subprocess.run([*compiler, "-std=c11", "-O2", "-Wall", "-Wextra", "-Werror",
                        "-I", str(directory), "-I", str(backend),
                        str(Path(__file__).with_name("unified_surface_backend_regression.c")),
                        "-o", str(binary)], check=True, env=env)
        if args.arm_output:
            print("Built ordinary-RAM ARM regression:", binary)
        else:
            env["AM2R_TEST_FOLD_VECTORS"] = "1"
            result = subprocess.run([str(binary)], env=env, capture_output=True, text=True)
            if result.returncode:
                print(result.stdout)
                print(result.stderr, file=sys.stderr)
                result.check_returncode()
            sys.path.insert(0, str(ROOT / "tools"))
            import am2r_gpu_reference as reference
            count = 0
            for line in result.stdout.splitlines():
                if line.startswith("FOLD "):
                    mode, source_color, background, folded = map(int, line.split()[1:])
                    assert folded == reference.blend_pixel(source_color, background, mode)
                    count += 1
                else:
                    print(line)
            assert count == 108
            print("Actual C clear-fold packets match independent scalar blend reference: 108 vectors, old/new capabilities")


if __name__ == "__main__":
    main()
