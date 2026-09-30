#!/usr/bin/env python3
"""Cycle/pixel differential proof for speculative solid tint staging.

Run the production GPU against the reviewed pre-hoist RTL under the existing
GPU and surface testbenches. Both receive identical DDR inputs and mirrored
testbench BRAM initialization. Only unconsumed tint-register values may differ:
state, enabled framebuffer writes, DDR traffic and publication must agree on
every clock. This is RTL simulation, not timing closure or hardware evidence.
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

ROOT = Path(__file__).resolve().parents[2]
BASELINE = "e0f92950d2fd9ebef2d4f30f741c234ec4a07d6e"
OUTPUT_PORTS = {
    "ddram_burstcnt": 8, "ddram_addr": 29, "ddram_rd": 1,
    "ddram_din": 64, "ddram_be": 8, "ddram_we": 1,
    "native_frame": 32, "native_buffer": 2,
}

MONITOR = r"""
    integer tint_ab_cycles=0, tint_ab_writes=0, tint_ab_ddr_writes=0;
    integer tint_ab_pairs=0, tint_ab_scalars=0, tint_ab_clips=0;
    integer tint_ab_textured=0, tint_ab_affine=0, tint_ab_unused_changes=0;
    reg [5:0] tint_ab_previous=0;
    reg tint_ab_previous_solid=0;
    always @(negedge clk) if (!reset) begin
        tint_ab_cycles=tint_ab_cycles+1;
        if (dut.state !== reference_dut.state)
            $fatal(1,"Tint A/B state/cycle diverged at %0d: %0d/%0d", tint_ab_cycles,dut.state,reference_dut.state);
        if ({ddram_rd,ddram_we,native_frame,native_buffer} !==
            {reference_ddram_rd,reference_ddram_we,reference_native_frame,reference_native_buffer})
            $fatal(1,"Tint A/B DDR enable/publication divergence at %0d",tint_ab_cycles);
        if ((ddram_rd || ddram_we) &&
            {ddram_addr,ddram_burstcnt} !== {reference_ddram_addr,reference_ddram_burstcnt})
            $fatal(1,"Tint A/B active DDR address/burst divergence at %0d",tint_ab_cycles);
        if (ddram_we) begin
            tint_ab_ddr_writes=tint_ab_ddr_writes+1;
            if ({ddram_din,ddram_be} !== {reference_ddram_din,reference_ddram_be})
                $fatal(1,"Tint A/B DDR payload divergence at %0d",tint_ab_cycles);
        end
        if ({dut.fb_even_we,dut.fb_odd_we} !== {reference_dut.fb_even_we,reference_dut.fb_odd_we})
            $fatal(1,"Tint A/B framebuffer enable divergence at %0d",tint_ab_cycles);
        if (dut.fb_even_we) begin
            tint_ab_writes=tint_ab_writes+1;
            if ({dut.fb_even_write_address,dut.fb_even_write_data} !==
                {reference_dut.fb_even_write_address,reference_dut.fb_even_write_data})
                $fatal(1,"Tint A/B even framebuffer write divergence at %0d",tint_ab_cycles);
        end
        if (dut.fb_odd_we) begin
            tint_ab_writes=tint_ab_writes+1;
            if ({dut.fb_odd_write_address,dut.fb_odd_write_data} !==
                {reference_dut.fb_odd_write_address,reference_dut.fb_odd_write_data})
                $fatal(1,"Tint A/B odd framebuffer write divergence at %0d",tint_ab_cycles);
        end
        if (dut.tinted_pixel !== reference_dut.tinted_pixel ||
            dut.tinted_pixel_1 !== reference_dut.tinted_pixel_1)
            tint_ab_unused_changes=tint_ab_unused_changes+1;
        // These states consume the tint, rather than merely speculatively
        // storing it. Textured/affine paths must replace both relevant lanes.
        if (dut.state==dut.ST_BLEND_READ || dut.state==dut.ST_BLEND_WRITE ||
            dut.state==dut.ST_BLEND_COMMIT || dut.state==dut.ST_SUBTRACT_PRODUCT ||
            dut.state==dut.ST_SUBTRACT_RESULT || dut.state==dut.ST_PAIR_BLEND_READ ||
            dut.state==dut.ST_PAIR_BLEND_WRITE || dut.state==dut.ST_PAIR_BLEND_COMMIT) begin
            if (dut.tinted_pixel !== reference_dut.tinted_pixel)
                $fatal(1,"Tint A/B consumed first lane divergence at %0d state%0d",tint_ab_cycles,dut.state);
            if (dut.pair_mode && dut.tinted_pixel_1 !== reference_dut.tinted_pixel_1)
                $fatal(1,"Tint A/B consumed second lane divergence at %0d state%0d",tint_ab_cycles,dut.state);
        end
        if(tint_ab_previous==dut.ST_BLIT_PIXEL && tint_ab_previous_solid &&
           dut.state==dut.ST_PAIR_BLEND_READ) tint_ab_pairs=tint_ab_pairs+1;
        if(dut.state==dut.ST_SOLID_READY) tint_ab_scalars=tint_ab_scalars+1;
        if(dut.state==dut.ST_BLIT_PIXEL) begin
            if(($signed(dut.dst_x)+$signed({1'b0,dut.blit_x}))<0 ||
               ($signed(dut.dst_x)+$signed({1'b0,dut.blit_x}))>=320 ||
               ($signed(dut.dst_y)+$signed({1'b0,dut.blit_y}))<0 ||
               ($signed(dut.dst_y)+$signed({1'b0,dut.blit_y}))>=240 ||
               (dut.affine_mode && (dut.u_current<dut.u_min || dut.u_current>=dut.u_max ||
                                    dut.v_current<dut.v_min || dut.v_current>=dut.v_max)))
                tint_ab_clips=tint_ab_clips+1;
            if(!dut.solid_mode && !dut.affine_mode) tint_ab_textured=tint_ab_textured+1;
            if(dut.affine_mode) tint_ab_affine=tint_ab_affine+1;
        end
        tint_ab_previous=dut.state;
        tint_ab_previous_solid=dut.solid_mode;
    end
    task automatic tint_ab_report;
        begin
            if(tint_ab_cycles==0 || tint_ab_writes==0 || tint_ab_ddr_writes==0)
                $fatal(1,"Tint A/B empty observation");
            REQUIRE_GPU_COVERAGE
            $display("TINT_AB_PASS cycles=%0d fb_writes=%0d ddr_writes=%0d solid_pairs=%0d solid_ready=%0d clipped=%0d textured=%0d affine=%0d unused_tint_differences=%0d",
                tint_ab_cycles,tint_ab_writes,tint_ab_ddr_writes,tint_ab_pairs,tint_ab_scalars,
                tint_ab_clips,tint_ab_textured,tint_ab_affine,tint_ab_unused_changes);
        end
    endtask
"""

# Additional mixed command jobs prove the newly speculative stores are benign
# after clipping and before a different producer replaces them. Existing
# framebuffer oracles and source-alpha/add/subtract fixtures remain unchanged.
EXTRA_JOBS = r"""
        for(tint_extra_case=0;tint_extra_case<6;tint_extra_case=tint_extra_case+1) begin
            for(n=0;n<120;n=n+1) commands[n]=0;
            commands[0]=64'd1;
            commands[1]=64'hff644832;
            commands[8]=64'd3 | (64'd5<<16) | (64'd2<<32);
            if(tint_extra_case%3==1) commands[8][8]=1;
            if(tint_extra_case%3==2) commands[8][9]=1;
            commands[9]=tint_extra_case<3 ? 64'h807b3915 : 64'h004913a2;
            commands[10]=(64'd4<<16) | 64'hfffe;
            // Immediately change from clipped solid to a differently tinted texture.
            commands[16]=64'd2 | (64'd3<<16) | (64'd1<<32);
            commands[17]=(64'd16<<32) | 64'h24000000;
            commands[18]=(64'd9<<16) | 64'd17;
            commands[21]=(64'h10000<<32) | 64'h10000;
            commands[22]=64'h9ac17342;
            commands[24]=64'd5;
            commands[25]=64'hc35f91d2;
            commands[32]=64'd4 | (64'd3<<16) | (64'd2<<32);
            commands[33]=(64'd16<<32) | 64'h24000000;
            commands[34]=(64'd11<<16) | 64'd23;
            commands[35]=64'd4<<48;
            commands[36]=64'd2<<48;
            commands[38]=(64'h10000<<32) | 64'h10000;
            commands[39]=64'h10000;
            // Bottom/right clipped scalar and pair boundary, followed by END.
            commands[40]=64'd3 | (64'd3<<16) | (64'd2<<32);
            commands[41]=64'h80a2b31c;
            commands[42]=(64'd239<<16) | 64'd319;
            commands[48]=0;
            control[1]=(64'd7<<32) | 64'h23fe0000;
            control[0]=((64'd12+tint_extra_case)<<32) | 64'h50473241;
            wait(control[3][31:0]==12+tint_extra_case);
        end
"""


def instrument(text: str, *, gpu: bool) -> str:
    match = re.search(r"\bam2r_gpu\s+dut\s*\(.*?\);", text, re.S)
    if match is None:
        raise RuntimeError("Expected one named production DUT")
    reference = match.group().replace("am2r_gpu", "am2r_gpu_reference", 1).replace(" dut", " reference_dut", 1)
    declarations = []
    for port, width in OUTPUT_PORTS.items():
        expression = rf"(\.{port}\s*\()\s*{port}\s*(\))"
        reference, count = re.subn(expression, rf"\1reference_{port}\2", reference)
        if count != 1:
            raise RuntimeError(f"Unexpected output binding: {port}")
        declarations.append(f"wire [{width-1}:0] reference_{port};")
    guard = ""
    if gpu:
        guard = """if(tint_ab_pairs==0 || tint_ab_scalars==0 || tint_ab_clips==0 ||
                       tint_ab_textured==0 || tint_ab_affine==0 || tint_ab_unused_changes==0)
                         $fatal(1,"Tint A/B required branch not exercised");"""
    inserted = "\n".join(declarations) + "\n" + reference + MONITOR.replace("REQUIRE_GPU_COVERAGE", guard)
    text = text[:match.end()] + "\n" + inserted + text[match.end():]
    # Do not permit an initializer to initialize only one of the independent RAMs.
    initialization = r"(?m)^(\s*)dut\.(fb_even|fb_odd)\[([^\]]+)\]\s*=\s*([^;]+);"
    text, mirrored = re.subn(initialization,
        lambda m: m.group() + f"\n{m[1]}reference_dut.{m[2]}[{m[3]}] = {m[4]};", text)
    if mirrored == 0:
        raise RuntimeError("No mirrored BRAM initializers found")
    if re.search(r"\b(force|release)\s+(?:dut|reference_dut)\.", text):
        raise RuntimeError("Unexpected hierarchical force/release in stimulus")
    if gpu:
        marker = "\t\tif (errors == 0) begin"
        if text.count(marker) != 1:
            raise RuntimeError("Expected one GPU success point")
        text = text.replace(marker, EXTRA_JOBS + "\n" + marker)
        text = text.replace("integer errors = 0;", "integer errors = 0;\ninteger tint_extra_case;")
    if text.count("$finish;") != 1:
        raise RuntimeError("Expected one normal fixture finish")
    return text.replace("$finish;", "tint_ab_report(); $finish;")


def run(command, path):
    started = time.monotonic()
    result = subprocess.run(list(map(str, command)), cwd=ROOT, capture_output=True, text=True)
    output = result.stdout + result.stderr
    path.write_text(output)
    if result.returncode or re.search(r"\*\*\s+(Fatal|Error):|Errors:\s*[1-9]", output):
        raise RuntimeError(f"Simulation/compile failed ({result.returncode}); see {path}\n{output[-3000:]}")
    return output, time.monotonic()-started


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--gpu-only", action="store_true")
    args = parser.parse_args()
    output = args.output or ROOT / "data/build" / ("gpu-tint-staging-" + datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S"))
    output = output.resolve()
    if output.exists():
        raise RuntimeError("Choose a fresh output directory")
    output.mkdir(parents=True)
    candidate_path = ROOT / "rtl/am2r_gpu.sv"
    candidate = candidate_path.read_bytes()
    reference = subprocess.check_output(["git", "show", BASELINE+":rtl/am2r_gpu.sv"], cwd=ROOT)
    reference = reference.replace(b"module am2r_gpu\n", b"module am2r_gpu_reference\n", 1)
    if b"module am2r_gpu_reference\n" not in reference:
        raise RuntimeError("Unable to rename baseline module")
    (output / "candidate.sv").write_bytes(candidate)
    (output / "reference.sv").write_bytes(reference)
    tools = {name: shutil.which(name) for name in ("vlib", "vlog", "vsim")}
    if not all(tools.values()):
        raise RuntimeError("Existing ModelSim toolchain is required")
    library = output / "work"
    run([tools["vlib"], library], output / "vlib.log")
    cases = ["am2r_gpu_tb"] + ([] if args.gpu_only else ["am2r_gpu_surface_tb"])
    results = {}
    for name in cases:
        source = (ROOT / "tests/rtl" / (name+".sv")).read_text()
        fixture = output / (name+".sv")
        fixture.write_text(instrument(source, gpu=name=="am2r_gpu_tb"))
        run([tools["vlog"], "-sv", "-work", library,
             output / "candidate.sv", output / "reference.sv", fixture], output / (name+"-compile.log"))
        text, elapsed = run([tools["vsim"], "-c", "-lib", library, name,
                             "-do", "run -all; quit -code 0"], output / (name+".log"))
        match = re.search(r"TINT_AB_PASS\s+([^\r\n]+)", text)
        if match is None or "PASS" not in text:
            raise RuntimeError(f"No differential pass marker; see {output / (name+'.log')}")
        results[name] = {"elapsed_seconds": elapsed,
                         "counts": {key: int(value) for key, value in re.findall(r"(\w+)=(\d+)", match[1])}}
        print(name, json.dumps(results[name]), flush=True)
    if candidate_path.read_bytes() != candidate:
        raise RuntimeError("Production RTL changed during differential verification")
    report = {"baseline_commit": BASELINE,
              "candidate_sha256": hashlib.sha256(candidate).hexdigest(),
              "baseline_renamed_sha256": hashlib.sha256(reference).hexdigest(),
              "results": results, "equivalence": "cycle-by-cycle state, active DDR and enabled framebuffer writes",
              "scope": "RTL simulation only; not FPGA timing/hardware evidence"}
    (output / "verification.json").write_text(json.dumps(report, indent=2)+"\n")
    print("PASS speculative tint staging differential:", output)


if __name__ == "__main__":
    main()
