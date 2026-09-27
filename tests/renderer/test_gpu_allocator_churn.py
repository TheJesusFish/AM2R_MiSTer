#!/usr/bin/env python3
"""Check bounded-live allocation ownership using production helpers.

Ordinary RAM only: no FPGA or renderer timing claim. Legacy helper source can
be selected from a retained baseline. Manager code defaults to the source's
sibling header when present; --manager-source overrides it. Baseline mode
asserts historical exhaustion; current arena mode asserts bounded progress.
"""
import argparse
import hashlib
import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parents[2]


def function(source, name):
    match = re.search(r"^(?:static )?(?:inline )?(?:bool|void|uint32_t|MisterGpuTexture\*) " +
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
    parser.add_argument("--legacy-source", type=Path,
                        default=ROOT / "third_party/Butterscotch/src/backends/mister.c")
    parser.add_argument("--arm-output", type=Path)
    parser.add_argument("--manager-source", type=Path)
    parser.add_argument("--expect-legacy-leaks", action="store_true",
                        help="Assert the preserved baseline's two narrow ownership bugs")
    args = parser.parse_args()
    source = args.legacy_source.read_text()
    types = source[source.index("typedef struct { uint64_t word[8]; }"):
                   source.index("static Runner *g_runner")]
    names = ["reserveGpuTexture"]
    if "static bool reserveGpuTexturePair(" in source:
        names.append("reserveGpuTexturePair")
    arena = "#include \"mister_gpu_arena_impl.h\"" in source
    if arena:
        names += ("gpuTextureRetirementBarrier", "gpuReclaimReleasedTextures", "gpuTextureEnsureRecordSlot",
                  "gpuTextureNewRecord", "gpuReserveTextureWithReclaim", "gpuReserveTexturePairWithReclaim")
    names += ("gpuBuffersEqual", "nextTextureGeneration",
             "warnTextureTableExhausted", "reuseReleasedTexture", "MisterGpu_uploadTexture",
             "uploadSparseDynamicTexture", "uploadDynamicTexture",
             "copyCroppedTextureRows", "gpuCropReadWord", "gpuCropBlockEqual32", "gpuCropChangedBounds",
             "copyCroppedTextureSplitSpan", "updateCroppedTextureChangedRows", "reuseReleasedCroppedTexture",
             "MisterGpu_uploadSparseDynamicTextureCropRevision", "MisterGpu_prepareDynamicTextureGpuWrite",
             "MisterGpu_releaseTexture")
    restored = "static void gpuInvalidateReleasedTextureShadows(" in source
    if restored:
        names.append("gpuInvalidateReleasedTextureShadows")
    code = "\n\n".join(function(source, name) for name in names)
    if arena:
        code = '#include "mister_gpu_arena_impl.h"\n' + code
    manager = args.manager_source or args.legacy_source.parent / "mister_surfaces_impl.h"
    defines = "\n".join(line for line in source.splitlines() if line.startswith("#define ") and
                        line.split()[1] in {"MISTER_WIDTH", "MISTER_HEIGHT", "MISTER_FB_BYTES",
                            "GPU_COMMAND_BUFFER_COUNT", "GPU_COMMAND_BYTES", "GPU_COMMAND_CAPACITY",
                            "GPU_TEXTURE_PHYS", "GPU_TEXTURE_BYTES", "GPU_TEXTURE_RECORDS",
                            "GPU_OFFSCREEN_RECORDS"})
    zig = ROOT / "third_party/zig/zig-x86_64-windows-0.16.0/zig.exe"
    compiler = [str(zig), "cc"] if zig.exists() else [shutil.which("cc") or "cc"]
    compiler += ["-DEXPECT_LEGACY_ALLOCATION_LEAKS=" + str(int(args.expect_legacy_leaks))]
    compiler += ["-DHAS_REUSABLE_ARENA=" + str(int(arena)), "-DHAS_TEST_MANAGER=" + str(int(manager.exists()))]
    compiler += ["-DHAS_RESTORE_INVALIDATE=" + str(int(restored))]
    if "static bool reserveGpuTexturePair(" in source:
        compiler += ["-DHAS_ATOMIC_TEXTURE_PAIR=1"]
    if args.arm_output:
        compiler += ["-target", "arm-linux-gnueabihf.2.30", "-mcpu=cortex_a9"]
    with tempfile.TemporaryDirectory(prefix="am2r-gpu-allocator-") as folder:
        directory = Path(folder)
        (directory / "allocator_types.inc").write_text(defines + "\n" + types)
        (directory / "allocator_production.inc").write_text(code)
        if manager.exists():
            (directory / "allocator_manager.inc").write_text(manager.read_text())
        env = dict(os.environ)
        env["ZIG_GLOBAL_CACHE_DIR"] = str(ROOT / "data/build/zig-unified-tests-global")
        env["ZIG_LOCAL_CACHE_DIR"] = str(directory / "zig-local")
        binary = args.arm_output or directory / ("churn.exe" if os.name == "nt" else "churn")
        subprocess.run([*compiler, "-std=c11", "-O2", "-Wall", "-Wextra", "-Werror", "-Wno-unused-function", "-Wno-unused-variable",
                        "-I", str(directory), "-I", str(ROOT / "third_party/Butterscotch/src/backends"),
                        str(Path(__file__).with_name("gpu_allocator_churn_regression.c")),
                        "-o", str(binary)], check=True, env=env)
        print("Extracted production allocator/legacy helpers SHA256:", hashlib.sha256(code.encode()).hexdigest(), flush=True)
        print("Legacy source:", args.legacy_source, flush=True)
        if args.arm_output:
            print("Built ordinary-RAM ARM churn fixture; not executed:", binary)
        else:
            subprocess.run([str(binary)], check=True)


if __name__ == "__main__":
    main()
