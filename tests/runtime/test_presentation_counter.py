#!/usr/bin/env python3
"""Compile presentation-counter regressions and reject specific bad RTL copies.

Uses the existing counter/video benches and an isolated ModelSim library for
each variant. Compiler/elaboration errors, arbitrary fatals and timeouts never
count as mutation detection. Generated sources/logs stay under data/work.
No FPGA, ARM hardware, game data, or production source is modified.
SPDX-License-Identifier: GPL-2.0-or-later
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import re
import shutil
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parents[2]
DIAGNOSTIC = re.compile(r"(?mi)^\s*(?:#\s*)?\*\*\s*(?:Fatal|Error)\b")
FATAL = re.compile(r"(?mi)^\s*(?:#\s*)?\*\*\s*Fatal:\s*([^\r\n]+)")
RUNTIME_TIME = re.compile(r"(?mi)^\s*(?:#\s*)?Time:\s*([0-9.]+)\s+\w+")


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def execute(command: list, directory: Path, label: str) -> tuple[int, str]:
    result = subprocess.run(list(map(str, command)), cwd=directory,
                            capture_output=True, text=True, timeout=120)
    output = result.stdout + result.stderr
    (directory / (label + ".log")).write_text(output, encoding="utf-8")
    return result.returncode, output


def require_tool_success(code: int, output: str, label: str) -> None:
    if code or DIAGNOSTIC.search(output) or "Error loading design" in output:
        raise RuntimeError(f"{label} failed; not mutation evidence:\n{output[-2500:]}")


def runtime_rejection(output: str, expected: str) -> bool:
    """Require the exact bench assertion at an actual nonzero simulation time."""
    fatals = FATAL.findall(output)
    times = RUNTIME_TIME.findall(output)
    return (fatals == [expected] and any(float(value) > 0 for value in times)
            and not re.search(r"(?mi)^\s*(?:#\s*)?\*\*\s*Error\b", output)
            and "PASS:" not in output)


def compile_variant(directory: Path, source_name: str, source: bytes,
                    bench_name: str, bench: bytes, executables: dict) -> None:
    directory.mkdir()
    (directory / source_name).write_bytes(source)
    (directory / bench_name).write_bytes(bench)
    code, log = execute([executables["vlib"], "work"], directory, "vlib")
    require_tool_success(code, log, "vlib")
    code, log = execute([executables["vlog"], "-sv", "-work", "work",
                         source_name, bench_name], directory, "vlog")
    require_tool_success(code, log, "vlog")


def simulate(directory: Path, top: str, executables: dict, *, standard=None,
             expected_fatal: str | None = None, pass_marker: str = "PASS:") -> dict:
    parameters = [] if standard is None else [f"-gSTANDARD={standard}"]
    suffix = "" if standard is None else f"-standard{standard}"
    command = [executables["vsim"], "-c", "-lib", "work", top, *parameters]
    # Explicitly establish successful elaboration before running a mutation.
    code, log = execute([*command, "-do", "quit -code 0"], directory, "elaborate" + suffix)
    require_tool_success(code, log, "elaboration")
    if f"Loading work.{top}" not in log:
        raise RuntimeError("Missing elaboration load marker; not mutation evidence")
    code, log = execute([*command, "-do",
                         "onbreak {quit -code 86}; onerror {quit -code 87}; run -all; quit -code 0"],
                        directory, "simulate" + suffix)
    if expected_fatal is not None:
        if not runtime_rejection(log, expected_fatal):
            raise RuntimeError(f"Expected runtime rejection {expected_fatal!r}, exit={code}; "
                               f"not detected:\n{log[-2500:]}")
        return {"compiled": True, "elaborated": True, "rejected": True,
                "assertion": expected_fatal, "simulation_exit": code,
                "simulation_log": "simulate" + suffix + ".log"}
    require_tool_success(code, log, "simulation")
    if pass_marker not in log:
        raise RuntimeError(f"No expected PASS marker in {directory}")
    return {"compiled": True, "elaborated": True, "passed": True,
            "standard": standard, "simulation_log": "simulate" + suffix + ".log"}


def replace_once(source: bytes, old: bytes, new: bytes) -> bytes:
    if source.count(old) != 1:
        raise RuntimeError(f"Expected unique mutation target: {old!r}")
    return source.replace(old, new)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=ROOT / "data/work/presentation-counter-tests")
    args = parser.parse_args()
    executables = {name: shutil.which(name) for name in ("vlib", "vlog", "vsim")}
    if not all(executables.values()):
        raise RuntimeError("Existing ModelSim vlib/vlog/vsim are required")
    # Keep an immutable snapshot of every input, including benches and top-level
    # wiring. Concurrent FPGA compilation reads the untouched production files.
    paths = {"arbiter": ROOT / "rtl/am2r_ddr_arbiter.sv",
             "video": ROOT / "rtl/am2r_native_video.sv",
             "counter_bench": ROOT / "tests/rtl/am2r_presentation_counter_tb.sv",
             "video_bench": ROOT / "tests/rtl/am2r_native_video_tb.sv",
             "top": ROOT / "AM2R.sv"}
    inputs = {key: path.read_bytes() for key, path in paths.items()}
    if len(re.findall(rb"\.display_tick\s*\(\s*active_frame\s*\)", inputs["top"])) != 1:
        raise RuntimeError("Production top must route active_frame to the diagnostic edge")
    # Detector negative controls: unrelated fatals or compiler text containing
    # assertion strings cannot accidentally satisfy mutation verification.
    target = "GPU granted during presentation reservation"
    assert not runtime_rejection("** Error: " + target, target)
    assert not runtime_rejection("** Fatal: unrelated\nTime: 10 ns", target)
    assert not runtime_rejection("** Fatal: " + target, target)
    args.output.mkdir(parents=True, exist_ok=True)
    directory = Path(tempfile.mkdtemp(prefix="presentation-counter-", dir=args.output)).resolve()
    print(f"Running isolated compiled presentation tests: {directory}", flush=True)
    report = {"scope": "Synthetic RTL only; no FPGA timing, hardware, HDMI, or full reader late-latch proof",
              "inputs": {key: {"path": str(paths[key].relative_to(ROOT)), "sha256": sha256(data)}
                         for key, data in inputs.items()}, "candidates": {}, "mutations": {}}
    baseline = directory / "candidate-counter"
    compile_variant(baseline, "am2r_ddr_arbiter.sv", inputs["arbiter"],
                    "am2r_presentation_counter_tb.sv", inputs["counter_bench"], executables)
    report["candidates"]["counter"] = simulate(baseline, "am2r_presentation_counter_tb", executables,
                                               pass_marker="PASS: native presentation counters")
    baseline_video = directory / "candidate-video"
    compile_variant(baseline_video, "am2r_native_video.sv", inputs["video"],
                    "am2r_native_video_tb.sv", inputs["video_bench"], executables)
    report["candidates"]["video"] = [simulate(baseline_video, "am2r_native_video_tb", executables,
                                                   standard=standard, pass_marker=f"PASS: standard {standard},")
                                     for standard in (0, 1, 2)]
    reservation, count = re.subn(
        rb"end else if \(presentation_pending\) begin\s*// One reservation cycle.*?"
        rb"(?=end else if \(gpu_rd \|\| gpu_we\) begin)",
        b"", inputs["arbiter"], flags=re.S)
    if count != 1:
        raise RuntimeError("Expected one combinational presentation reservation branch")
    mutations = [
        ("missing-reservation", "arbiter", reservation, target),
        ("premature-even-header", "arbiter", replace_once(inputs["arbiter"],
             b"{record_generation - 32'd1, PRESENT_MAGIC}", b"{record_generation, PRESENT_MAGIC}"),
         "even header before complete payload"),
        # This deliberately restores a blank-start diagnostic pulse. It proves
        # the active-edge contract, not all native-reader late-restart behavior.
        ("blank-start-instead-of-active", "video", replace_once(inputs["video"],
             b"if (h_count == 0 && v_count == 0)",
             b"if (h_count == 0 && v_count == V_ACTIVE)"),
         "presentation edge is not the first active pixel"),
    ]
    for name, kind, source, assertion in mutations:
        variant = directory / name
        bench_key = "counter_bench" if kind == "arbiter" else "video_bench"
        compile_variant(variant, paths[kind].name, source, paths[bench_key].name,
                        inputs[bench_key], executables)
        result = simulate(variant, paths[bench_key].stem, executables,
                          standard=0 if kind == "video" else None, expected_fatal=assertion)
        result["rtl_sha256"] = sha256(source)
        report["mutations"][name] = result
    for key, path in paths.items():
        if path.read_bytes() != inputs[key]:
            raise RuntimeError(f"Input changed during run: {path}; rerun against a stable snapshot")
    report["production_inputs_unchanged"] = True
    (directory / "verification.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, indent=2), flush=True)


if __name__ == "__main__":
    main()
