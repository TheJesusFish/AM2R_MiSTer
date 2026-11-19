#!/usr/bin/env python3
"""Synthetic affine-cache scalar/historical RTL regression; no hardware access.

Candidate checks always run. --baseline additionally compares performance with
an archived pre-cache RTL file. All simulator products go to a fresh build folder.
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

ROOT = Path(__file__).resolve().parents[2]
BENCH = ROOT / "tests/rtl/am2r_gpu_affine_cache_tb.sv"


def execute(command, directory: Path, name: str) -> str:
    result = subprocess.run(list(map(str, command)), cwd=directory,
                            capture_output=True, text=True, timeout=180)
    output = result.stdout + result.stderr
    (directory / (name + ".log")).write_text(output)
    if result.returncode or re.search(r"(?mi)^\s*(?:#\s*)?\*\*\s*(?:Error|Fatal)\b", output):
        raise RuntimeError(f"{name} failed; see {directory / (name + '.log')}\n{output[-3500:]}")
    return output


def simulate(directory: Path, rtl: bytes, cache: bool, executables: dict) -> dict:
    directory.mkdir()
    source = directory / "am2r_gpu.sv"
    source.write_bytes(rtl)
    execute([executables["vlib"], "work"], directory, "vlib")
    execute([executables["vlog"], "-sv", "-work", "work",
             *(["+define+AFFINE_CACHE_PRESENT"] if cache else []),
             *(["+define+TEX_PAIR_ADVANCE_PRESENT"] if b"ST_TEX_PAIR_ADVANCE" in rtl else []),
             *(["+define+AFFINE_FLOW_PRESENT"] if b"ST_AFFINE_FLOW" in rtl else []),
             *(["+define+AFFINE_ADVANCE_TUPLE_PRESENT"] if b"affine_u_next_lookahead" in rtl else []),
             source, BENCH], directory, "vlog")
    line_beats = 16 if b"if (affine_line_fill == 4'd15)" in rtl else 8
    output = execute([executables["vsim"], "-c", "-lib", "work", "am2r_gpu_affine_cache_tb",
                      f"-gEXPECT_CACHE={int(cache)}",
                      f"-gAFFINE_LINE_BEATS={line_beats}", "-do",
                      "onerror {quit -code 1}; run -all; quit -code 0"], directory, "vsim")
    passed = re.search(r"AFFINE_CACHE_PASS ([^\r\n]+)", output)
    if passed is None:
        raise RuntimeError(f"No pass marker: {directory}")
    coverage = {k: int(v) for k, v in re.findall(r"(\w+)=(\d+)", passed[1])}
    flow = re.search(r"AFFINE_FLOW_PASS ([^\r\n]+)", output)
    if b"ST_AFFINE_FLOW" in rtl:
        if flow is None:
            raise RuntimeError("Missing large-affine pipeline coverage")
        coverage.update({"flow_" + k: int(v) for k, v in re.findall(r"(\w+)=(\d+)", flow[1])})
    performance = [{k: int(v) for k, v in re.findall(r"(\w+)=(\d+)", line)}
                   for line in re.findall(r"AFFINE_CACHE_PERF ([^\r\n]+)", output)]
    if len(performance) != 2 or {row["schedule"] for row in performance} != {0, 1}:
        raise RuntimeError("Missing both DDR performance schedules")
    return {"sha256": hashlib.sha256(rtl).hexdigest(), "coverage": coverage, "performance": performance}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--baseline", type=Path)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--mutation-check", action="store_true",
                        help="Require invalidation, lane-selection, and state-width defects to fail")
    args = parser.parse_args()
    executables = {name: shutil.which(name) for name in ("vlib", "vlog", "vsim")}
    if not all(executables.values()):
        raise RuntimeError("Existing ModelSim vlib/vlog/vsim are required")
    candidate_path = ROOT / "rtl/am2r_gpu.sv"
    candidate = candidate_path.read_bytes()
    baseline = args.baseline.read_bytes() if args.baseline else None
    if b"affine_cache_valid" not in candidate or (baseline is not None and b"affine_cache_valid" in baseline):
        raise RuntimeError("Expected a cache candidate and, when supplied, a pre-cache historical baseline")
    parent = args.output or ROOT / "data/build"
    parent.mkdir(parents=True, exist_ok=True)
    directory = Path(tempfile.mkdtemp(prefix="affine-cache-" + datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S-"), dir=parent)).resolve()
    print(f"Running isolated affine tests: {directory}", flush=True)
    report = {"scope": "Synthetic scalar RTL comparison; not timing or hardware evidence",
              "candidate": simulate(directory / "candidate", candidate, True, executables)}
    if baseline is not None:
        report["baseline"] = simulate(directory / "baseline", baseline, False, executables)
        for new, old in zip(report["candidate"]["performance"], report["baseline"]["performance"]):
            if new["schedule"] != old["schedule"] or not (new["cycles"] < old["cycles"] and
                    new["requests"] < old["requests"] and new["beats"] < old["beats"]):
                raise RuntimeError(f"Reuse fixture failed to improve all metrics: {new} versus {old}")
    if args.mutation_check:
        mutations = {}
        # Leave reset invalidation intact: this specifically tests same-job op4
        # invalidation, not the fact that a fresh job happened to start clean.
        decode = candidate.index(b"8'd4: begin", candidate.index(b"ST_CMD_DECODE: begin"))
        guard = b"affine_cache_valid <= 0;"
        position = candidate.index(guard, decode)
        mutations["missing-command-invalidation"] = (candidate[:position] + b"/* removed by mutation */" + candidate[position + len(guard):], "RGBA mismatch")
        lane = b"source_pixel <= source_byte_addr[2] ? affine_cache_data[affine_cache_index][63:32] :"
        if candidate.count(lane) != 1:
            raise RuntimeError("Expected unique affine lane selector")
        mutations["wrong-source-lane"] = (candidate.replace(lane, lane.replace(b"source_byte_addr[2]", b"!source_byte_addr[2]")), "RGBA mismatch")
        reset_position = candidate.index(guard)
        if reset_position >= decode:
            raise RuntimeError("Expected reset invalidation before command decoder")
        mutations["missing-reset-invalidation"] = (candidate[:reset_position] + b"/* removed by mutation */" + candidate[reset_position + len(guard):], "Reset retained affine cache entries")
        if b"ST_AFFINE_FLOW" in candidate:
            line_guard = b"affine_line_valid[affine_line_reset] <= 0;"
            command_position = candidate.index(line_guard, decode)
            mutations["missing-line-command-invalidation"] = (candidate[:command_position] + b"/* removed by mutation */" + candidate[command_position + len(line_guard):], "RGBA mismatch")
            line_reset = candidate.index(line_guard)
            mutations["missing-line-reset-invalidation"] = (candidate[:line_reset] + b"/* removed by mutation */" + candidate[line_reset + len(line_guard):], "Reset retained affine line entries")
            line_lane = b"water_pipe0_pixel0 <= source_byte_addr[2] ? affine_line_data_q[affine_line_hit_way][63:32] :"
            if candidate.count(line_lane) != 1:
                raise RuntimeError("Expected unique affine line lane selector")
            mutations["wrong-line-source-lane"] = (candidate.replace(line_lane, line_lane.replace(b"source_byte_addr[2]", b"!source_byte_addr[2]")), "RGBA mismatch")
            wider_lines = b"if (affine_line_fill == 4'd15)" in candidate
            final_beat = b"if (affine_line_fill == 4'd15) begin" if wider_lines else b"if (affine_line_fill == 3'd7) begin"
            if candidate.count(final_beat) != 1:
                raise RuntimeError("Expected unique affine refill completion")
            late, early = (b"4'd15", b"4'd14") if wider_lines else (b"3'd7", b"3'd6")
            mutations["premature-line-valid"] = (candidate.replace(final_beat, final_beat.replace(late, early)), "Affine line became valid before final beat")
            split_banks = b"affine_line_half_q" in candidate
            refill = b"data[{affine_line_set[3:0], affine_line_fill}] <= ddram_dout;" if split_banks else b"data[{affine_line_set, affine_line_fill}] <= ddram_dout;"
            if candidate.count(refill) != 1:
                raise RuntimeError("Expected unique affine refill write")
            flip = b"4'd1" if wider_lines else b"3'd1"
            mutations["wrong-line-refill-index"] = (candidate.replace(refill, refill.replace(b"affine_line_fill}", b"(affine_line_fill ^ " + flip + b")}")), "RGBA mismatch")
            if split_banks:
                half_read = b"affine_line_half_q <= affine_lookup_set[4];"
                half_write = b"affine_line_set[4] == affine_line_half"
                if candidate.count(half_read) != 1 or candidate.count(half_write) != 1:
                    raise RuntimeError("Expected unique registered physical-bank selectors")
                mutations["wrong-line-read-half"] = (candidate.replace(half_read, half_read.replace(b"affine_lookup_set[4]", b"!affine_lookup_set[4]")), "RGBA mismatch")
                mutations["wrong-line-write-half"] = (candidate.replace(half_write, half_write.replace(b"affine_line_set[4]", b"!affine_line_set[4]")), "RGBA mismatch")
            signed_u = b"{{14{affine_stage_u[31]}}, affine_stage_u[31:16], 2'b00}"
            if candidate.count(signed_u) != 1:
                raise RuntimeError("Expected unique sign-extended affine displacement")
            mutations["unsigned-affine-displacement"] = (candidate.replace(signed_u, b"{14'd0, affine_stage_u[31:16], 2'b00}"), "Unexpected read")
            mutations["implicit-unsigned-base-context"] = (candidate.replace(signed_u, b"(affine_stage_u >>> 16) * 4"), "Unexpected read")
            scalar_u = b"source_byte_addr <= source_row_addr + {{14{u_current[31]}}, u_current[31:16], 2'b00};"
            if candidate.count(scalar_u) != 1:
                raise RuntimeError("Expected unique sign-extended scalar-affine displacement")
            mutations["unsigned-scalar-affine-displacement"] = (candidate.replace(scalar_u, scalar_u.replace(b"{{14{u_current[31]}}", b"{14'd0")), "Unexpected read")
            if b"affine_u_next_lookahead" in candidate:
                tuple_mutants = {
                    "stale-affine-last-column": (
                        b"affine_last_lookahead <= blit_x + 1'b1 >= blit_width;",
                        b"affine_last_lookahead <= affine_x_lookahead >= blit_width;",
                        "Affine advance tuple mismatch"),
                    "affine-miss-replay-advances-destination": (
                        b"AF_RAM: affine_flow_phase <= AF_EMIT;",
                        b"AF_RAM: begin affine_destination_lookahead <= affine_destination_lookahead + 17'd1; affine_flow_phase <= AF_EMIT; end",
                        "Affine advance tuple mismatch"),
                    "wrong-affine-coordinate-copy": (
                        b"u_current <= prefetched ? affine_u_lookahead : u_current + u_step;",
                        b"u_current <= prefetched ? affine_v_lookahead : u_current + u_step;",
                        "Affine next-U invariant"),
                    "wrong-unused-affine-next-U-copy": (
                        b"u_next <= prefetched ? affine_u_next_lookahead : u_next + u_step;",
                        b"u_next <= prefetched ? affine_u_lookahead : u_next + u_step;",
                        "Affine next-U invariant"),
                }
                for name, (original, replacement, witness) in tuple_mutants.items():
                    if candidate.count(original) != 1:
                        raise RuntimeError(f"Expected unique advance tuple mutation: {name}")
                    mutations[name] = (candidate.replace(original, replacement), witness)
        if b"ST_TEX_PAIR_ADVANCE" in candidate:
            enum = b"localparam [6:0] ST_AFFINE_SOURCE_ADDRESS"
            if candidate.count(enum) != 1:
                raise RuntimeError("Expected unique widened upper state enumeration")
            mutations["truncated-pair-advance-state"] = (candidate.replace(enum, enum.replace(b"[6:0]", b"[5:0]")),
                                                        "Pair advancement state aliases/truncates RESET")
        report["mutations"] = {}
        for name, (mutated, expected_witness) in mutations.items():
            try:
                simulate(directory / name, mutated, True, executables)
            except RuntimeError:
                witness = (directory / name / "vsim.log").read_text()
                if expected_witness not in witness:
                    raise RuntimeError(f"Mutation {name} failed for an unrelated reason")
                report["mutations"][name] = expected_witness
            else:
                raise RuntimeError(f"Mutation escaped: {name}")
    if candidate_path.read_bytes() != candidate:
        raise RuntimeError("Production RTL changed during the run; repeat on a stable candidate")
    (directory / "verification.json").write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps(report, indent=2), flush=True)
    print(f"PASS affine cache regression: {directory}", flush=True)


if __name__ == "__main__":
    main()
