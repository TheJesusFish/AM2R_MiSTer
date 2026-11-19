#!/usr/bin/env python3
"""Bounded additive-fill regression, including tests that reject old routing.

No hardware, game payload, or deployment. Independent integer-RGBA checks
exercise every alpha, pipeline drain, negative eligibility and reset. Optional
RTL replays export unsaturated RGBA after each fill and preserve predecessor
and successor command ordering under two DDR schedules.
SPDX-License-Identifier: GPL-2.0-or-later
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import re
import shutil
import struct
import subprocess
import tempfile

from test_gpu_axis_stream import (expected_blend, gather_fixture, water_fixture,
                                  axis_descriptor, pattern, apply_axis)
from test_gpu_targets import command, write_fixture
import am2r_gpu_reference as gpu

ROOT = Path(__file__).resolve().parents[2]
BENCH = ROOT / "tests/rtl/am2r_gpu_solid_stream_tb.sv"
INITIAL, EXPORT, TEX = 0x31000000, 0x32000000, 0x33000000


def execute(command_line, directory, name, expected_failure=None):
    process = subprocess.run(list(map(str, command_line)), cwd=directory,
                             capture_output=True, text=True, timeout=180)
    output = process.stdout + process.stderr
    (directory / f"{name}.log").write_text(output, encoding="utf-8")
    failed = process.returncode != 0 or re.search(r"(?mi)^\s*(?:#\s*)?\*\*\s*(?:Error|Fatal)\b", output)
    if expected_failure:
        if not failed or expected_failure not in output:
            raise RuntimeError(f"Mutation not rejected for expected reason: {name}\n{output[-2000:]}")
    elif failed:
        raise RuntimeError(f"{name} failed\n{output[-2000:]}")
    return output


def simulate(directory, rtl, executables, expected_failure=None):
    directory.mkdir()
    source = directory / "am2r_gpu.sv"
    source.write_bytes(rtl)
    execute([executables["vlib"], "work"], directory, "vlib")
    execute([executables["vlog"], "-sv", "-work", "work", source, BENCH], directory, "vlog")
    output = execute([executables["vsim"], "-c", "-lib", "work", "am2r_gpu_solid_stream_tb",
                      "-do", "onerror {quit -code 1}; run -all; quit -code 0"], directory, "vsim", expected_failure)
    if not expected_failure:
        match = re.search(r"SOLID_STREAM_PASS ([^\r\n]+)", output)
        if not match:
            raise RuntimeError("Missing solid-stream completion marker")
        return {k: int(v) for k, v in re.findall(r"(\w+)=(\d+)", match[1])}
    return {"rejected": True, "reason": expected_failure}


def ordering_fixture(factory):
    # Retain the entire predecessor workload and its intermediate raw exports.
    commands, regions, initial, expected, exports, _ = factory(False)
    commands = [commands[:-64]]
    regions = dict(regions)
    exports = dict(exports)
    seed = pattern(gpu.PIXELS, 103)
    regions[INITIAL] = struct.pack(f"<{len(seed)}I", *seed)
    texture = struct.pack("<2048I", *pattern(2048, 147))
    regions[TEX] = texture
    # Preserve the predecessor's output for the first fill; subsequent loads
    # reset to patterned, nonsaturated RGBA. Each prefix is exported, including
    # alpha0, so later saturation cannot conceal earlier arithmetic errors.
    for index, alpha in enumerate((1, 0, 127, 128, 140, 254, 255)):
        if index:
            commands.append(command(10, 320, 240, base=INITIAL, stride=1280))
            expected = list(seed)
        colour = alpha << 24 | (0x3B719D if index & 1 else 0xFEFD01)
        commands.append(command(3, 320, 240, base=colour,
                                w0=3 | 1 << 8 | 320 << 16 | 240 << 32))
        expected = [expected_blend(colour, dst, 0xFFFFFFFF, 1, False) for dst in expected]
        # Ordinary subtractive/floor gather after each fill catches inherited
        # additive/solid state. Its odd destination lane leaves neighbours intact.
        case = dict(base=TEX + 4, stride=1280, width=63, height=2, x=1, y=1,
                    u=63 << 16, du=-65536, tint=0xA12B739D, mode=2, floor=True)
        commands.append(axis_descriptor(**case))
        apply_axis(expected, texture, TEX, **case)
        base = EXPORT + index * gpu.FRAME_BYTES
        commands.append(command(11, 320, 240, base=base, stride=1280))
        exports[base] = struct.pack(f"<{len(expected)}I", *expected)
    commands.append(command(12))
    return b"".join(commands), regions, initial, expected, exports


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=ROOT / "data/build")
    parser.add_argument("--mutation-check", action="store_true")
    parser.add_argument("--replay-ordering", action="store_true")
    args = parser.parse_args()
    executables = {name: shutil.which(name) for name in ("vlib", "vlog", "vsim")}
    if not all(executables.values()):
        raise RuntimeError("Existing ModelSim tools are required")
    rtl = (ROOT / "rtl/am2r_gpu.sv").read_bytes()
    args.output.mkdir(parents=True, exist_ok=True)
    directory = Path(tempfile.mkdtemp(prefix="solid-stream-", dir=args.output)).resolve()
    print(f"Running isolated solid-fill tests: {directory}", flush=True)
    report = {"rtl_sha256": hashlib.sha256(rtl).hexdigest(),
              "scope": "Synthetic independent RGBA/RTL; not native Windows, timing or hardware evidence",
              "candidate": simulate(directory / "candidate", rtl, executables)}
    if args.mutation_check:
        marker = b"if (descriptor[0][8] && !descriptor[0][9] &&"
        if rtl.count(marker) != 1:
            raise RuntimeError("Expected unique fill eligibility marker")
        raw = b"tiled_solid_pixel <= descriptor[1][31:0];"
        if rtl.count(raw) != 1:
            raise RuntimeError("Expected unique constant-fill source")
        mutations = {
            "old-opaque-only-routing": (rtl.replace(marker, marker + b"\n descriptor[1][31:24] == 8'hff &&"),
                                        "alpha routing mismatch"),
            "discard-source-alpha": (rtl.replace(raw, b"tiled_solid_pixel <= {8'hff, descriptor[1][23:0]};"),
                                     "RGBA mismatch"),
            "round-additive-instead-of-floor": (rtl.replace(
                b"div255_floor({1'b0, water_pipe3_source_r[0]})",
                b"round_mul255_product(water_pipe3_source_r[0])"), "RGBA mismatch"),
            "premature-fill-drain": (rtl.replace(
                b"water_pipe4_valid == 0) begin", b"1'b1) begin"), "RGBA mismatch"),
        }
        report["mutations"] = {name: simulate(directory / name, source, executables, reason)
                               for name, (source, reason) in mutations.items()}
    if args.replay_ordering:
        report["ordering"] = []
        for name, factory in (("gather", gather_fixture), ("water-tiled", water_fixture)):
            case = directory / name
            case.mkdir()
            commands, regions, initial, expected, exports = ordering_fixture(factory)
            result = gpu.render(commands, regions, initial)
            if result.pixels != expected:
                raise RuntimeError("Independent fixture final RGBA mismatch")
            for base, payload in exports.items():
                produced = next((data[base - start:base - start + len(payload)] for start, data in result.exports.items()
                                 if start <= base and base + len(payload) <= start + len(data)), None)
                if produced != payload:
                    raise RuntimeError(f"Independent prefix export mismatch at {base:x}")
            manifest = write_fixture(case, commands, regions, initial)
            for latency, stall, gap in ((2, 11, 0), (7, 13, 5)):
                execute(["python", ROOT / "tools/replay_gpu_capture.py", manifest, "--output", case,
                         "--read-latency", latency, "--stall-period", stall, "--response-gap", gap],
                        case, f"replay-{latency}-{stall}-{gap}")
                report["ordering"].append({"fixture": name, "ddr": [latency, stall, gap], "matching": True})
    (directory / "verification.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
