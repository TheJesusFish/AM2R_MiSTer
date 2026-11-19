#!/usr/bin/env python3
"""Exhaustive production-RTL tint rounding and compiled mutation witnesses.

Runs the unchanged am2r_gpu_blend_pipeline_tb against an immutable GPU snapshot.
Its independent historical-width oracle covers all 65536 products, direct
rounding and both tint modes, including unreachable products above 65025. The
same bench checks the full 17-bit floor domain and two-clock scalar/pair blends.
Generated RTL copies, ModelSim libraries and logs stay under ignored data/work.
No Quartus, hardware, game data, or deployment access.
SPDX-License-Identifier: GPL-2.0-or-later
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
import time

ROOT = Path(__file__).resolve().parents[2]
RTL = ROOT / "rtl/am2r_gpu.sv"
BENCH = ROOT / "tests/rtl/am2r_gpu_blend_pipeline_tb.sv"
TOP = "am2r_gpu_blend_pipeline_tb"
MESSAGES = re.compile(r"(?m)^[ \t]*(?:#[ \t]*)?\*\*[ \t]+(Fatal|Error):[ \t]*([^\r\n]*)$")
COUNTS = re.compile(r"(?m)^[ \t]*(?:#[ \t]*)?Errors:[ \t]*([0-9]+)\b")
PASS_LINES = (
    "PASS ROUND255: all 65536 products preserve bias, rounding, wrap and both tint modes",
    "PASS DIV255: all 131072 inputs preserve exact 17-bit arithmetic; legal blend range matches integer floor",
    "PASS blend pipeline: 8704 scalar/pair cases, exactly 2 clocks each (CALCULATE + COMMIT)",
)
PASSES = re.compile(r"(?m)^[ \t]*(?:#[ \t]*)?(PASS (?:ROUND255|DIV255|blend pipeline):[^\r\n]*)$")
WITNESS = re.compile(r"ROUND255 exact 16-bit mismatch at input ([0-9]+)$")


def check_output(output: str, returncode: int, *, mismatch_input: int | None = None) -> str:
    """Require real completion or the specific runtime oracle, never PASS-only."""
    messages = MESSAGES.findall(output)
    counts = [int(value) for value in COUNTS.findall(output)]
    passes = PASSES.findall(output)
    if mismatch_input is not None:
        match = WITNESS.fullmatch(messages[0][1]) if len(messages) == 1 else None
        if (not match or messages[0][0] != "Fatal" or passes or counts != [1] or
                int(match[1]) != mismatch_input):
            raise RuntimeError("Mutation did not trigger the intended ROUND255 runtime mismatch")
        return messages[0][1]
    if returncode or messages or not counts or any(counts) or tuple(passes) != PASS_LINES:
        raise RuntimeError("Production rounding/blend regression did not complete successfully")
    return "all 65536 rounding inputs, both tint modes, 131072 floor inputs, 8704 pipeline cases"


def parser_selftest() -> dict[str, int]:
    positive = "".join("# " + line + "\n" for line in PASS_LINES) + "# Errors: 0, Warnings: 0\n"
    witness = "# ** Fatal: ROUND255 exact 16-bit mismatch at input 127\n# Errors: 1, Warnings: 0\n"
    check_output(positive, 0)
    check_output(witness, 1, mismatch_input=127)
    rejected = (
        (positive, 1, None),
        (positive.replace("# Errors: 0, Warnings: 0\n", ""), 0, None),
        (positive.replace("Errors: 0", "Errors: 1"), 0, None),
        (positive.replace(PASS_LINES[1] + "\n", ""), 0, None),
        (positive.replace("# PASS ROUND255:", "# note: PASS ROUND255:"), 0, None),
        (positive + "# ** Fatal: unrelated failure\n", 0, None),
        (positive, 0, 127),
        ("# Errors: 0, Warnings: 0\n", 0, 127),
        (witness.replace("** Fatal:", "** Error:"), 1, 127),
        (witness.replace("input 127", "input 128"), 1, 127),
        (witness.replace("ROUND255 exact 16-bit mismatch", "TINT255 floor mismatch"), 1, 127),
        (witness.replace("Errors: 1", "Errors: 2"), 1, 127),
        (witness.replace("# Errors: 1, Warnings: 0\n", ""), 1, 127),
        (witness + "# ** Error: elaboration failed\n", 1, 127),
        (witness + "# " + PASS_LINES[0] + "\n", 1, 127),
        ("# note: ROUND255 exact 16-bit mismatch at input 127\n# ** Fatal: timeout\n# Errors: 1\n", 1, 127),
    )
    for output, returncode, mismatch_input in rejected:
        try:
            check_output(output, returncode, mismatch_input=mismatch_input)
        except RuntimeError:
            continue
        raise AssertionError("Rounding parser accepted an unrelated/incomplete result")
    return {"positive": 2, "negative": len(rejected)}


def execute(command: list[str | Path], directory: Path, name: str, *, simulation=False):
    start = time.monotonic()
    try:
        process = subprocess.run(list(map(str, command)), cwd=directory,
                                 capture_output=True, text=True, timeout=180)
    except subprocess.TimeoutExpired as error:
        output = error.stdout or ""
        if isinstance(output, bytes):
            output = output.decode(errors="replace")
        (directory / f"{name}.log").write_text(output + "\nHARNESS_TIMEOUT\n", encoding="utf-8")
        raise RuntimeError(f"Tool timeout is not an accepted mutation: {directory / name}") from error
    output = process.stdout + process.stderr
    (directory / f"{name}.log").write_text(output, encoding="utf-8")
    counts = [int(value) for value in COUNTS.findall(output)]
    if not simulation and (process.returncode or MESSAGES.search(output) or any(counts) or
                           (name == "vlog" and not counts)):
        raise RuntimeError(f"Compile/tool failure is not a mutation witness: {directory / name}\n{output[-2000:]}")
    return output, {"seconds": time.monotonic() - start, "returncode": process.returncode}


def simulate(directory: Path, rtl: bytes, bench: bytes, tools, mismatch_input=None):
    directory.mkdir()
    source, fixture = directory / "am2r_gpu.sv", directory / "blend_pipeline_tb.sv"
    source.write_bytes(rtl)
    fixture.write_bytes(bench)
    _, vlib = execute([tools["vlib"], "work"], directory, "vlib")
    _, vlog = execute([tools["vlog"], "-sv", "-work", "work", source, fixture], directory, "vlog")
    output, vsim = execute([tools["vsim"], "-c", "-lib", "work", TOP,
                            "-do", "onerror {quit -code 1}; run -all; quit -code 0"],
                           directory, "vsim", simulation=True)
    result = check_output(output, vsim["returncode"], mismatch_input=mismatch_input)
    return {"rtl_sha256": hashlib.sha256(rtl).hexdigest(),
            "bench_sha256": hashlib.sha256(bench).hexdigest(),
            "vlib": vlib, "vlog": vlog, "vsim": vsim, "result": result}


def round_mutants(rtl: bytes):
    # Change only the production rounding helper, not div255_floor or the bench.
    pattern = rb"function automatic \[7:0\] round_mul255_product;.*?endfunction"
    matches = list(re.finditer(pattern, rtl, re.S))
    if len(matches) != 1:
        raise RuntimeError("Expected one production rounding helper")
    match = matches[0]
    helper = match[0]
    mutations = (
        ("strict-to-nonstrict", b"(rounded_low > ~rounded_high)", b"(rounded_low >= ~rounded_high)", 127),
        ("dropped-bias-carry", b"product[15:8] + {7'b0, product[7]}", b"product[15:8]", 128),
    )
    for name, before, after, mismatch_input in mutations:
        if helper.count(before) != 1:
            raise RuntimeError(f"Expected one rounding mutation anchor: {name}")
        mutated = rtl[:match.start()] + helper.replace(before, after) + rtl[match.end():]
        yield name, mutated, mismatch_input


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, help="Parent for a fresh run, within ignored data/work")
    args = parser.parse_args()
    parser_results = parser_selftest()
    tools = {name: shutil.which(name) for name in ("vlib", "vlog", "vsim")}
    if not all(tools.values()):
        raise RuntimeError("Existing ModelSim vlib/vlog/vsim are required on PATH")
    work = (ROOT / "data/work").resolve()
    parent = (args.output or work / ("tint-rounding-" +
              datetime.now(timezone.utc).strftime("%Y%m%d"))).resolve()
    if not parent.is_relative_to(work):
        raise RuntimeError("Simulator products must remain under ignored data/work")
    parent.mkdir(parents=True, exist_ok=True)
    directory = Path(tempfile.mkdtemp(prefix="synthetic-", dir=parent)).resolve()
    print(f"Running production rounding and mutation tests: {directory}", flush=True)
    rtl, bench = RTL.read_bytes(), BENCH.read_bytes()
    report = {"scope": "Actual production RTL/bench; not timing, gameplay or hardware evidence",
              "directory": str(directory), "tools": tools, "parser_selftest": parser_results,
              "test_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
              "round_products": 65536, "direct_round_checks": 65536,
              "normal_tint_checks": 65536, "floor_tint_checks": 65536,
              "products_above_65025": 510, "floor_domain_checks": 131072,
              "pipeline_cases": 8704, "mutations": {}}
    report["production"] = simulate(directory / "production", rtl, bench, tools)
    print("PASS production: " + report["production"]["result"], flush=True)
    for name, mutated, mismatch_input in round_mutants(rtl):
        result = simulate(directory / name, mutated, bench, tools, mismatch_input)
        report["mutations"][name] = dict(result, mismatch_input=mismatch_input)
        print(f"PASS compiled {name} rejected: {result['result']}", flush=True)
    if RTL.read_bytes() != rtl or BENCH.read_bytes() != bench:
        raise RuntimeError("Production RTL or testbench changed during verification")
    (directory / "verification.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, indent=2), flush=True)


if __name__ == "__main__":
    main()
