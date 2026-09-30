#!/usr/bin/env python3
"""Exercise source-derived HPS wiring and the unchanged framework video_calc.

Generated simulation files/logs live in a fresh ignored data/build directory.
Only video_calc is extracted from sys/hps_io.sv; its behavior is not replaced.
A declaration-only adapter hoists module declarations and spells implicit static
block variables explicitly for ModelSim 10.5b. A small direction probe substitutes for the unrelated HPS command
decoder. The fixture copies the actual core measurement expressions/connection,
and guards the decoder's real bus directions against the probe's assumptions.
No Quartus, hardware, or source-tree modifications are performed.
"""
from __future__ import annotations

import argparse
import hashlib
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]


def uncomment(text: str) -> str:
    return re.sub(r"/\*.*?\*/|//[^\n]*", "", text, flags=re.S)


def balanced(text: str, opening: int) -> tuple[str, int]:
    depth = 0
    for pos in range(opening, len(text)):
        if text[pos] == "(":
            depth += 1
        elif text[pos] == ")":
            depth -= 1
            if depth == 0:
                return text[opening + 1:pos], pos + 1
    raise ValueError("Unbalanced source connection")


def port(instance: str, name: str) -> str:
    matches = list(re.finditer(r"\." + re.escape(name) + r"\s*\(", instance))
    if len(matches) != 1:
        raise ValueError(f"Expected exactly one {name} connection")
    return balanced(instance, matches[0].end() - 1)[0].strip()


def compact(text: str) -> str:
    return re.sub(r"\s+", "", text)


def make_fixture(top: str, framework: str) -> tuple[str, str]:
    core = uncomment(top)
    match = re.search(r"\bhps_io\s*#\s*\(\s*\.CONF_STR\s*\(\s*CONF_STR\s*\)\s*\)\s+hps_io\s*\(", core)
    if not match:
        raise ValueError("Cannot identify production hps_io instance")
    instance, _ = balanced(core, match.end() - 1)
    bus = port(instance, "HPS_BUS")
    if compact(bus) != "{HPS_BUS[45:42],measured_video,HPS_BUS[37:0]}":
        raise ValueError("Production HPS bus must preserve clocks/control and isolate only [41:38]")
    if compact(port(instance, "direct_video")) != "direct_video":
        raise ValueError("Production direct_video output is not connected")
    mode = port(instance, "new_vmode")
    if compact(mode) != "native_pal":
        raise ValueError("Production new_vmode must observe the frame-latched native PAL mode")
    declarations = []
    for name, width in (("native_de", ""), ("measured_video", "[3:0]")):
        matches = list(re.finditer(r"\bwire\s*(\[[^\]]+\])?\s*" + name + r"\s*=\s*([^;]+);", core))
        if len(matches) != 1 or compact(matches[0].group(1) or "") != width:
            raise ValueError(f"Cannot identify production {name} declaration")
        declarations.append(matches[0].group(0))
    if compact(declarations[0]) != "wirenative_de=~(hblank|vblank);":
        raise ValueError("native_de is not derived from native blanking")
    if compact(declarations[1]) != "wire[3:0]measured_video=direct_video?HPS_BUS[41:38]:{ce_pix,native_de,hsync,vsync};":
        raise ValueError("Unexpected production native/direct-video selector")

    source = uncomment(framework)
    for lhs, rhs in (("[37]", "ioctl_wait"), ("[36]", "clk_sys"), ("[32]", "io_wide"),
                     ("[15:0]", "EXT_BUS[32]?EXT_BUS[15:0]:fp_enable?fp_dout:io_dout")):
        if f"assignHPS_BUS{lhs}={rhs};" not in compact(source):
            raise ValueError(f"Framework HPS direction changed at {lhs}; review the direction probe")
    video_instance = re.search(r"\bvideo_calc\s+video_calc\s*\(", source)
    if not video_instance:
        raise ValueError("Framework video_calc instance missing")
    video_ports, _ = balanced(source, video_instance.end() - 1)
    mapping = {"clk_100": 43, "clk_vid": 42, "ce_pix": 41, "de": 40,
               "hs": 39, "vs": 38, "vs_hdmi": 44, "f1": 45}
    for name, bit in mapping.items():
        if compact(port(video_ports, name)) != f"HPS_BUS[{bit}]":
            raise ValueError(f"Framework measurement mapping changed for {name}")
    modules = re.findall(r"(?ms)^module video_calc\b.*?^endmodule\b", framework)
    if len(modules) != 1:
        raise ValueError("Expected exactly one unchanged video_calc module")

    fixture = """// Generated from AM2R.sv; do not use as production RTL.
module am2r_hps_measurement_fixture #(
    parameter OLD_MAPPING=0, parameter MISSING_NOTIFICATION=0
)(
    inout [45:0] HPS_BUS,
    input clk_video, ce_pix, hblank, vblank, hsync, vsync, native_pal,
    input sim_direct_video, input [4:0] par_num,
    input [15:0] probe_reply, input probe_wait, probe_wide,
    output [18:0] probe_command, output [15:0] dout,
    output [7:0] measured_bundle
);
wire direct_video;
""" + "\n".join(declarations) + "\n"
    fixture += """generate if (OLD_MAPPING) begin : old_mapping
    am2r_hps_direction_probe probe(
        .HPS_BUS(HPS_BUS),
""" + f"        .new_vmode(MISSING_NOTIFICATION ? 1'b0 : {mode}),\n" + """        .clk_sys(clk_video), .direct_video(direct_video),
        .sim_direct_video(sim_direct_video), .par_num(par_num), .dout(dout),
        .probe_reply(probe_reply), .probe_wait(probe_wait), .probe_wide(probe_wide),
        .probe_command(probe_command), .measured_bundle(measured_bundle));
end else begin : production
    am2r_hps_direction_probe probe(
""" + f"        .HPS_BUS({bus}),\n        .new_vmode(MISSING_NOTIFICATION ? 1'b0 : {mode}),\n" + """        .clk_sys(clk_video), .direct_video(direct_video),
        .sim_direct_video(sim_direct_video), .par_num(par_num), .dout(dout),
        .probe_reply(probe_reply), .probe_wait(probe_wait), .probe_wide(probe_wide),
        .probe_command(probe_command), .measured_bundle(measured_bundle));
end endgenerate
endmodule

// This probe only replaces unrelated command decode. The bus ownership and
// video_calc mappings above were checked against unchanged sys/hps_io.sv.
module am2r_hps_direction_probe(
    inout [45:0] HPS_BUS, input clk_sys, new_vmode, sim_direct_video,
    output direct_video, input [4:0] par_num, output [15:0] dout,
    input [15:0] probe_reply, input probe_wait, probe_wide,
    output [18:0] probe_command, output [7:0] measured_bundle
);
assign direct_video = sim_direct_video;
assign HPS_BUS[37] = probe_wait;
assign HPS_BUS[36] = clk_sys;
assign HPS_BUS[32] = probe_wide;
assign HPS_BUS[15:0] = probe_reply;
assign probe_command = {HPS_BUS[35:33], HPS_BUS[31:16]};
assign measured_bundle = HPS_BUS[45:38];
video_calc actual_framework_measurement(
    .clk_100(HPS_BUS[43]), .clk_vid(HPS_BUS[42]), .clk_sys(clk_sys),
    .ce_pix(HPS_BUS[41]), .de(HPS_BUS[40]), .hs(HPS_BUS[39]), .vs(HPS_BUS[38]),
    .vs_hdmi(HPS_BUS[44]), .f1(HPS_BUS[45]), .new_vmode(new_vmode),
    .video_rotated(1'b0), .par_num(par_num), .dout(dout));
endmodule
"""
    original_calc = modules[0].replace("\r\n", "\n") + "\n"
    adapted_calc, count = re.subn(r"(?m)^(\t)reg\b", r"\1static reg", original_calc)
    if count == 0 or adapted_calc.replace("\tstatic reg", "\treg") != original_calc:
        raise ValueError("video_calc declaration-only adaptation was not reversible")
    module_declaration = r"(?m)^reg\s+[^\n]+;\n"
    declarations = re.findall(module_declaration, adapted_calc)
    body = re.sub(module_declaration, "", adapted_calc)
    adapted_calc = body.replace("always @(posedge clk_sys)", "".join(declarations) + "\nalways @(posedge clk_sys)", 1)
    if re.sub(module_declaration, "", adapted_calc).replace("\tstatic reg", "\treg").split() != re.sub(module_declaration, "", original_calc).split():
        raise ValueError("video_calc declaration hoist changed executable logic")
    return fixture, adapted_calc


def run(command: list[str], log: Path, timeout: int = 900) -> str:
    # The production case deliberately covers over 100 full raster frames,
    # including the framework's 16-frame notification debounce. ModelSim 10.5b
    # needs several minutes on a shared host; retain a bounded 15-minute ceiling.
    result = subprocess.run(command, cwd=log.parent, text=True, stdout=subprocess.PIPE,
                            stderr=subprocess.STDOUT, timeout=timeout)
    log.write_text(result.stdout, encoding="utf-8")
    if result.returncode:
        raise RuntimeError(f"Command failed ({result.returncode}); see {log}\n{result.stdout[-4000:]}")
    return result.stdout


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--prepare-only", action="store_true", help="Only create and validate source-derived fixtures")
    args = parser.parse_args()
    top_path = ROOT / "AM2R.sv"
    framework_path = ROOT / "sys/hps_io.sv"
    top = top_path.read_text(encoding="utf-8")
    framework_bytes = framework_path.read_bytes()
    fixture, video_calc = make_fixture(top, framework_bytes.decode("utf-8"))
    build_parent = ROOT / "data/build"
    build_parent.mkdir(parents=True, exist_ok=True)
    output = Path(tempfile.mkdtemp(prefix="hps-video-measurement-", dir=build_parent))
    fixture_path = output / "production_hps_fixture.sv"
    calc_path = output / "video_calc_declaration_adapter.sv"
    fixture_path.write_text(fixture, encoding="utf-8")
    calc_path.write_text(video_calc, encoding="utf-8")
    (output / "source-sha256.txt").write_text(
        f"AM2R.sv {hashlib.sha256(top_path.read_bytes()).hexdigest()}\n"
        f"sys/hps_io.sv {hashlib.sha256(framework_bytes).hexdigest()}\n", encoding="utf-8")
    print(f"Source-derived measurement fixture: {output}", flush=True)
    if args.prepare_only:
        print("PASS: production selector/directions/notification and framework extraction validated; simulation not run.")
        return 0
    for tool in ("vlib", "vlog", "vsim"):
        if not shutil.which(tool):
            raise RuntimeError(f"Required simulator tool is not on PATH: {tool}")
    library = output / "work"
    run(["vlib", str(library)], output / "vlib.log")
    inputs = ["rtl/am2r_native_video.sv", "rtl/am2r_crt_resync.sv", "rtl/am2r_video_line_ram.sv",
              "rtl/am2r_video_hscale.sv", "rtl/am2r_crt_video.sv", str(calc_path), str(fixture_path),
              "tests/rtl/am2r_hps_video_measurement_tb.sv"]
    compile_output = run(["vlog", "-sv", "-work", str(library), *(str(ROOT / p) for p in inputs)], output / "vlog.log")
    if re.search(r"\*\*\s+(Fatal|Error):|Errors:\s*[1-9]", compile_output):
        raise RuntimeError(f"RTL compilation reported an error; see {output / 'vlog.log'}")
    cases = [("production", [], None),
             ("old-mapping-witness", ["-gOLD_MAPPING=1"], "native active dimensions"),
             ("missing-notification-witness", ["-gMISSING_NOTIFICATION=1"], "PAL transition notification")]
    for name, parameters, expected_failure in cases:
        command = ["vsim", "-c", "-l", str(output / f"{name}-transcript.log"), "-lib", str(library), "am2r_hps_video_measurement_tb", *parameters,
                   "-do", "run -all; quit -code 0"]
        text = run(command, output / f"{name}.log")
        fatal = re.search(r"\*\*\s+(Fatal|Error):|Errors:\s*[1-9]", text)
        if expected_failure:
            if not fatal or expected_failure not in text or "PASS: HPS measurement" in text:
                raise RuntimeError(f"Mutation witness was not rejected for the expected reason: {name}; see {output}")
            print(f"PASS: {name} rejected ({expected_failure}).", flush=True)
        elif fatal or "PASS: HPS measurement" not in text:
            raise RuntimeError(f"Production regression failed; see {output / (name + '.log')}\n{text[-5000:]}")
        else:
            print(text[text.rfind("# PASS:"):].strip(), flush=True)
    if framework_path.read_bytes() != framework_bytes or top_path.read_text(encoding="utf-8") != top:
        raise RuntimeError("Source changed during measurement regression; rerun against stable inputs")
    print(f"PASS: native/direct measurement and mutation witnesses; sys/ unchanged. Logs: {output}")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (ValueError, RuntimeError, subprocess.TimeoutExpired) as error:
        print(f"FAIL: {error}", file=sys.stderr)
        raise SystemExit(1)
