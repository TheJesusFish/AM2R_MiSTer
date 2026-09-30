#!/usr/bin/env python3
"""Bounded unsigned row-address/cycle proof; no FPGA or hardware access.

Default runs the production pipeline against a 64-bit arithmetic oracle and
the previous RTL, then requires a high-half placement mutation to fail.
--full-differential also reuses the existing GPU/surface descriptor, pixel,
DDR-stall and four-buffer tests with a cycle-by-cycle old/new monitor.
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
import time

import test_gpu_tint_staging as tint

ROOT = Path(__file__).resolve().parents[2]
BASELINE = "847782444c2e364d82c93efe0b19fc411fbd923f"
ERROR = re.compile(r"\*\*\s+(?:Fatal|Error):|Errors:\s*[1-9]|(?m:^\s*#?\s*FAIL\b)")

FOCUSED_MONITOR = r"""
    integer row_ab_cycles=0;
    always @(negedge clk) if (!reset) begin
        row_ab_cycles=row_ab_cycles+1;
        if (dut.state !== reference_dut.state ||
            (dut.state==dut.ST_GENERIC && dut.generic_state !== reference_dut.generic_state))
            $fatal(1,"ROW_AB state/cycle divergence at %0d",row_ab_cycles);
        if (dut.source_row_addr !== reference_dut.source_row_addr)
            $fatal(1,"ROW_AB row-address divergence at %0d",row_ab_cycles);
        if ({ddram_rd,ddram_we,native_frame,native_buffer,dut.fb_even_we,dut.fb_odd_we} !==
            {reference_ddram_rd,reference_ddram_we,reference_native_frame,reference_native_buffer,
             reference_dut.fb_even_we,reference_dut.fb_odd_we})
            $fatal(1,"ROW_AB DDR/publication/BRAM enable divergence");
        if ((ddram_rd || ddram_we) && {ddram_addr,ddram_burstcnt} !==
            {reference_ddram_addr,reference_ddram_burstcnt})
            $fatal(1,"ROW_AB active DDR address divergence");
        if (ddram_we && {ddram_din,ddram_be} !== {reference_ddram_din,reference_ddram_be})
            $fatal(1,"ROW_AB active DDR payload divergence");
    end
"""


def run(command, log, *, allow_failure=False):
    start = time.monotonic()
    result = subprocess.run(list(map(str, command)), cwd=ROOT, text=True,
                            capture_output=True, timeout=300)
    text = result.stdout + result.stderr
    log.write_text(text)
    failed = result.returncode != 0 or ERROR.search(text) is not None
    if failed and not allow_failure:
        raise RuntimeError(f"Failed: {log}\n{text[-1800:]}")
    return text, time.monotonic()-start, failed


def instrument_focused(source):
    match = re.search(r"\bam2r_gpu\s+dut\s*\(.*?\);", source, re.S)
    if match is None:
        raise RuntimeError("Expected one named GPU instance")
    reference = match.group().replace("am2r_gpu", "am2r_gpu_reference", 1).replace(" dut", " reference_dut", 1)
    declarations = []
    for port, width in tint.OUTPUT_PORTS.items():
        reference, count = re.subn(rf"(\.{port}\s*\()\s*{port}\s*(\))",
                                  rf"\1reference_{port}\2", reference)
        if count != 1:
            raise RuntimeError(f"Unexpected binding for {port}")
        declarations.append(f"wire [{width-1}:0] reference_{port};")
    # Mirror every directed setup deposit, not only RAM initialization. Never
    # force the state transitions or results during either clock under test.
    assignment = r"(?m)^([ \t]*)dut\.([A-Za-z_]\w*(?:\[[^;\]\n]+\])?)\s*=(?!=)\s*([^;]+);"
    source, count = re.subn(assignment, lambda m: m.group()+
                           f"\n{m[1]}reference_dut.{m[2]} = {m[3]};", source)
    if count < 50:
        raise RuntimeError("Directed fixture setup shape changed")
    match = re.search(r"\bam2r_gpu\s+dut\s*\(.*?\);", source, re.S)
    insertion = "\n".join(declarations)+"\n"+reference+FOCUSED_MONITOR
    source = source[:match.end()]+"\n"+insertion+source[match.end():]
    if source.count("$finish;") != 1:
        raise RuntimeError("Expected one normal fixture finish")
    return source.replace("$finish;", '$display("ROW_AB_PASS cycles=%0d",row_ab_cycles); $finish;')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--full-differential", action="store_true")
    args = parser.parse_args()
    output = (args.output or ROOT / "data/build" /
              ("gpu-row-pipeline-"+datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S"))).resolve()
    if output.exists():
        raise RuntimeError("Choose a fresh output directory")
    output.mkdir(parents=True)
    source_path = ROOT / "rtl/am2r_gpu.sv"
    source = source_path.read_bytes()
    reference = subprocess.check_output(["git", "show", BASELINE+":rtl/am2r_gpu.sv"], cwd=ROOT)
    reference = reference.replace(b"module am2r_gpu\n", b"module am2r_gpu_reference\n", 1)
    if reference.count(b"module am2r_gpu_reference\n") != 1:
        raise RuntimeError("Baseline module rename failed")
    (output / "candidate.sv").write_bytes(source)
    (output / "reference.sv").write_bytes(reference)
    tools = {name: shutil.which(name) for name in ("vlib", "vlog", "vsim")}
    if not all(tools.values()):
        raise RuntimeError("ModelSim tools must be on PATH")
    library = output / "work"
    run([tools["vlib"], library], output / "vlib.log")
    results = {}
    name = "am2r_gpu_row_pipeline_tb"
    fixture = output / (name+".sv")
    fixture.write_text(instrument_focused((ROOT / "tests/rtl" / (name+".sv")).read_text()))
    run([tools["vlog"], "-sv", "-work", library, output / "candidate.sv",
         output / "reference.sv", fixture], output / "focused-compile.log")
    text, elapsed, _ = run([tools["vsim"], "-c", "-lib", library, name,
                            "-do", "run -all; quit -code 0"], output / "focused.log")
    for marker in ("ROW_PIPELINE_PASS", "ROW_AB_PASS"):
        match = re.search(marker+r"\s+([^\r\n]+)", text)
        if match is None:
            raise RuntimeError(f"Missing {marker}")
        results[marker] = {key: int(value) for key,value in re.findall(r"(\w+)=(\d+)",match[1])}
    results["focused_seconds"] = elapsed
    print("PASS focused row pipeline:", json.dumps(results), flush=True)

    # A real production-code mutant: shift the high product to the wrong half.
    # Compile success is required, then accept only the expected row oracle
    # failure, never arbitrary simulator startup/errors/timeouts as evidence.
    candidate_text = source.decode()
    mutation, count = re.subn(r"\{\s*source_row_product_high\s*,\s*16'd0\s*\}",
                              "{16'd0, source_row_product_high}", candidate_text)
    if count != 1:
        raise RuntimeError("Expected one shared row-product combine expression")
    (output / "mutated.sv").write_text(mutation)
    run([tools["vlog"], "-sv", "-work", library, output / "mutated.sv"], output / "mutation-compile.log")
    witness, _, failed = run([tools["vsim"], "-c", "-lib", library, name,
                             "-do", "run -all; quit -code 0"], output / "mutation.log", allow_failure=True)
    fatal = re.findall(r"(?m)^\s*#?\s*\*\* Fatal: ([^\r\n]+)", witness)
    if not failed or len(fatal)!=1 or not fatal[0].startswith("ROW_PIPELINE row mismatch"):
        raise RuntimeError("High-product mutation did not fail specifically at the row oracle")
    results["mutation"] = {"rejected": True, "witness": fatal[0]}
    print("PASS mutation witness:", fatal[0], flush=True)

    if args.full_differential:
        # Same pre/post implementation, so unused tint registers must NOT be
        # required to differ. Retain all substantive state/pixel/DDR checks.
        for name in ("am2r_gpu_tb", "am2r_gpu_surface_tb"):
            body = (ROOT / "tests/rtl" / (name+".sv")).read_text()
            body = tint.instrument(body, gpu=name=="am2r_gpu_tb")
            if name=="am2r_gpu_tb":
                guard=" || tint_ab_unused_changes==0"
                if body.count(guard)!=1:
                    raise RuntimeError("Unexpected tint-only coverage guard")
                body=body.replace(guard, "", 1)
            fixture = output / (name+".sv")
            fixture.write_text(body)
            run([tools["vlog"], "-sv", "-work", library, output / "candidate.sv",
                 output / "reference.sv", fixture], output / (name+"-compile.log"))
            text, elapsed, _=run([tools["vsim"], "-c", "-lib", library, name,
                                 "-do", "run -all; quit -code 0"], output / (name+".log"))
            match=re.search(r"TINT_AB_PASS\s+([^\r\n]+)",text)
            if match is None:
                raise RuntimeError(f"Missing differential result for {name}")
            results[name]={"seconds": elapsed, "counts":{
                key:int(value) for key,value in re.findall(r"(\w+)=(\d+)",match[1])}}
            print(name,json.dumps(results[name]),flush=True)
    if source_path.read_bytes()!=source:
        raise RuntimeError("Production RTL changed during verification")
    report={"baseline_commit":BASELINE,"candidate_sha256":hashlib.sha256(source).hexdigest(),
            "baseline_renamed_sha256":hashlib.sha256(reference).hexdigest(),"results":results,
            "scope":"RTL arithmetic/state/pixel/DDR simulation; not timing closure or hardware validation"}
    (output / "verification.json").write_text(json.dumps(report,indent=2)+"\n")
    print("PASS row pipeline verification:",output,flush=True)


if __name__=="__main__":
    main()
