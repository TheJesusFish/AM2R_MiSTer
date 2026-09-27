#!/usr/bin/env python3
"""Test the actual production crop-span optimization against the old baseline.

--arm-output creates a standalone ordinary-RAM test for
the existing ARMv7 toolchain; running it on a MiSTer is a separate authorized
operation. Host timings do not predict the FPGA shared-DDR write cost.
"""
from pathlib import Path
import argparse
import hashlib
import os
import re
import shutil
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parents[2]


def extract_function(source, name):
    match = re.search(r"static (?:inline )?(?:bool|uint32_t|void) " + re.escape(name) + r"\(", source)
    if not match:
        raise ValueError("Missing source function: " + name)
    start = match.start()
    opening = source.index("{", start)
    depth = 1
    at = opening + 1
    while depth:
        if source[at] == "{":
            depth += 1
        elif source[at] == "}":
            depth -= 1
        at += 1
    return source[start:at]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--benchmark", type=int, default=0, metavar="ITERATIONS")
    parser.add_argument("--arm-output", type=Path)
    parser.add_argument("--baseline-source", type=Path,
                        help="Extract original functions from the preserved pre-edit mister.c")
    args = parser.parse_args()
    source = (ROOT / "third_party/Butterscotch/src/backends/mister.c").read_text()
    code = "\n\n".join(extract_function(source, name) for name in
                         ("gpuCropReadWord", "gpuCropBlockEqual32", "gpuCropChangedBounds",
                          "copyCroppedTextureSplitSpan", "updateCroppedTextureChangedRows"))
    baseline = None
    if args.baseline_source:
        original_bytes = args.baseline_source.read_bytes()
        original = original_bytes.decode()
        names = (["gpuCropReadWord", "gpuCropBlockEqual32", "gpuCropChangedBounds"]
                 if "static bool gpuCropChangedBounds(" in original else ["gpuBuffersEqual"])
        if "static void copyCroppedTextureSplitSpan(" in original:
            names.append("copyCroppedTextureSplitSpan")
        names.append("updateCroppedTextureChangedRows")
        baseline = "\n\n".join(extract_function(original, name) for name in names)
        for name in names:
            baseline = baseline.replace(name, "baselineUpload" if name == "updateCroppedTextureChangedRows"
                                        else "baseline_" + name)
        print("Preserved baseline SHA256:", hashlib.sha256(original_bytes).hexdigest(), flush=True)
    zig = ROOT / "third_party/zig/zig-x86_64-windows-0.16.0/zig.exe"
    compiler = [str(zig), "cc"] if zig.exists() else [shutil.which("cc") or "cc"]
    if args.arm_output:
        if not zig.exists():
            parser.error("--arm-output requires the repository Zig compiler")
        compiler += ["-target", "arm-linux-gnueabihf.2.30", "-mcpu=cortex_a9"]
    with tempfile.TemporaryDirectory(prefix="am2r-crop-") as folder:
        directory = Path(folder)
        (directory / "crop_upload_production.inc").write_text(code)
        if baseline:
            (directory / "crop_upload_baseline.inc").write_text(baseline)
            compiler.append("-DCROP_BASELINE_EXTRACTED=1")
        env = dict(os.environ)
        env["ZIG_GLOBAL_CACHE_DIR"] = str(ROOT / "data/build/zig-crop-tests-global")
        env["ZIG_LOCAL_CACHE_DIR"] = str(directory / "zig-local")
        binary = args.arm_output or directory / ("crop.exe" if os.name == "nt" else "crop")
        subprocess.run([*compiler, "-std=c11", "-O3", "-Wall", "-Wextra", "-Werror",
                        "-I", str(directory), str(Path(__file__).with_name("crop_upload_regression.c")),
                        "-o", str(binary)], check=True, env=env)
        if args.arm_output:
            print("Built ARM ordinary-RAM test; no hardware execution:", binary)
        else:
            command = [str(binary)]
            if args.benchmark:
                command.append(str(args.benchmark))
            subprocess.run(command, check=True)


if __name__ == "__main__":
    main()
