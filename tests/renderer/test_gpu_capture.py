#!/usr/bin/env python3
"""Compile and run the actual opt-in capture code against synthetic I/O."""
from pathlib import Path
import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools"))
import replay_gpu_capture as replay


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--rtl", action="store_true", help="also replay the producer fixture in RTL (requires simulator tools)")
    args = parser.parse_args()
    source = (ROOT / "third_party/Butterscotch/src/backends/mister.c").read_text()
    start = source.index("// Deliberately intrusive, one-job diagnostic captures.")
    end = source.index("\n#endif", start)
    code = source[start:end]
    submit_start = source.index("static bool submitGpuCommandsAsync(void) {")
    opening = source.index("{", submit_start)
    depth, submit_end = 1, opening + 1
    while depth:
        depth += (source[submit_end] == "{") - (source[submit_end] == "}")
        submit_end += 1
    code += "\n" + source[submit_start:submit_end]
    zig = ROOT / "third_party/zig/zig-x86_64-windows-0.16.0/zig.exe"
    compiler = [str(zig), "cc"] if zig.exists() else [shutil.which("cc") or "cc"]
    with tempfile.TemporaryDirectory(prefix="am2r-capture-") as folder:
        directory = Path(folder)
        (directory / "gpu_capture_under_test.inc").write_text(code)
        env = dict(os.environ)
        env["ZIG_GLOBAL_CACHE_DIR"] = str(ROOT / "data/build/zig-capture-tests-global")
        env["ZIG_LOCAL_CACHE_DIR"] = str(directory / "zig-local")
        binary = directory / ("capture.exe" if os.name == "nt" else "capture")
        subprocess.run([*compiler, "-std=gnu99", "-O2", "-Wall", "-Wextra",
                        "-Wno-unused-parameter", "-I", str(directory),
                        "-I", str(ROOT / "third_party/Butterscotch/src/backends"),
                        str(Path(__file__).with_name("gpu_capture_regression.c")),
                        "-o", str(binary)], check=True, env=env)
        capture = directory / "capture"
        capture.mkdir()
        fourth = directory / "fourth-buffer"
        fourth.mkdir()
        subprocess.run([str(binary), str(capture), str(fourth)], check=True)
        result = subprocess.run([sys.executable, str(ROOT / "tools/am2r_gpu_reference.py"),
                                 str(capture / "manifest.json")], check=True, capture_output=True, text=True)
        report = json.loads(result.stdout)
        assert report["framebuffer"]["matching"]
        assert report["comparison_coverage"]["complete"]
        assert len(report["exports"]) == 2
        assert all(entry["matching"] for entry in report["exports"].values())
        assert not report["presented"]
        print("C capture producer -> scalar replay: op10/11/12/13/14 narrow known-semantics fixture matched raw RGBA and both strided exports")
        result = subprocess.run([sys.executable, str(ROOT / "tools/am2r_gpu_reference.py"),
                                 str(fourth / "manifest.json")], check=True, capture_output=True, text=True)
        report = json.loads(result.stdout)
        manifest = json.loads((fourth / "manifest.json").read_text())
        assert manifest["native_source_base"] == 0x3A0E1100
        assert (fourth / "native-3.bin").stat().st_size == 320 * 240 * 4
        assert report["framebuffer"]["matching"]
        assert report["comparison_coverage"]["complete"]
        assert report["presented"]
        prepared = directory / "fourth-buffer-prepared"
        prepared.mkdir()
        replay.prepare(fourth / "manifest.json", prepared)
        assert (prepared / "mapping.txt").read_text().splitlines()[0].split()[3] == "3"
        print("C capture producer -> scalar/RTL preparation: native-water reads buffer3 exactly and presents buffer0; all four native inputs captured")
        if args.rtl:
            for fixture in (capture, fourth):
                subprocess.run([sys.executable, str(ROOT / "tools/replay_gpu_capture.py"),
                                str(fixture / "manifest.json"), "--output",
                                str(directory / (fixture.name + "-rtl"))], check=True)


if __name__ == "__main__":
    main()
