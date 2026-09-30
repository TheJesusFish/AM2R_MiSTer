#!/usr/bin/env python3
"""Generate declaration-only video simulation copies for ModelSim 10.5b.

Usage: make_sim_video_mixer.py OUTPUT_VIDEO_MIXER
Also writes scandoubler_sim.sv and gamma_corr_sim.sv beside that output.
sys/ is never edited. These copies retain the original logic; only the
full-depth mixer generate declarations, forward declarations and an explicit
static qualifier are adapted. Pinned, LF-normalized hashes reject unsupported
upstream revisions before any files are written.

The focused test links an HQ2x stub and never selects scandoubler output.
A guard in the generated mixer fails if that unsupported path is selected.
This is not a scandoubler/HQ2x functional test.
"""
import hashlib
import pathlib
import re
import sys

ROOT = pathlib.Path(__file__).resolve().parents[3]
PINNED = {
    "video_mixer.sv": "ca02e63c3e61d8a8c817fa10008a7a9873eb34a40ab89e2e2a596c590f9f0cf9",
    "scandoubler.v": "d36181c5fde9df0a504c547e23f60bcf201420052ada2682f081b00fd9485e6b",
    "gamma_corr.sv": "2754944c6fe05d921374d1fecc26621782520b3bc6fbf600ca561e9f37c7af88",
}


def replace_exact(source, old, new, count=1):
    if source.count(old) != count:
        raise ValueError(f"expected {count} occurrence(s) of {old!r}")
    return source.replace(old, new)


def adapt_sources(sources):
    for name, expected in PINNED.items():
        actual = hashlib.sha256(sources[name].encode("utf-8")).hexdigest()
        if actual != expected:
            raise ValueError(f"unsupported sys/{name} revision ({actual}); review the syntax adapter")

    mixer = sources["video_mixer.sv"]
    block = re.compile(r"generate\s*\n\s*if\(GAMMA && HALF_DEPTH\) begin.*?endgenerate\n", re.S)
    hoisted = ("wire [DWIDTH:0] R_in = frz ? 1'd0 : R;\n"
               "wire [DWIDTH:0] G_in = frz ? 1'd0 : G;\n"
               "wire [DWIDTH:0] B_in = frz ? 1'd0 : B;\n")
    mixer, count = block.subn(hoisted, mixer)
    if count != 1:
        raise ValueError("expected one full-depth mixer declaration block")
    mixer = replace_exact(mixer, "[DWIDTH:0]", "[(HALF_DEPTH ? 3 : 7):0]", 6)
    mixer = replace_exact(mixer, "endmodule", """
// This adapter intentionally supports only this suite's full-depth bypass.
initial if (HALF_DEPTH != 0) $fatal(1, "simulation adapter requires HALF_DEPTH=0");
always @(posedge CLK_VIDEO)
    if (scandoubler === 1'b1)
        $fatal(1, "HQ2x is stubbed: scandoubler output must not be selected");
endmodule""")

    doubler = sources["scandoubler.v"]
    doubler = replace_exact(doubler, "[DWIDTH:0]", "[(HALF_DEPTH ? 3 : 7):0]", 7)
    declarations = ("reg ce_x4o, ce_x2o;", "reg [1:0] sd_line;", "reg [8:0] hbo;")
    for declaration in declarations:
        doubler = replace_exact(doubler, declaration, "")
    doubler = replace_exact(doubler, "reg  [7:0] pix_len = 0;",
                           "\n".join(declarations) + "\nreg  [7:0] pix_len = 0;")

    gamma = sources["gamma_corr.sv"]
    declarations = "reg [9:0] gamma_index;\nreg [7:0] gamma;"
    gamma = replace_exact(gamma, declarations, "")
    gamma = replace_exact(gamma, '(* ramstyle="no_rw_check" *) reg [7:0] gamma_curve[768];',
                          declarations + '\n(* ramstyle="no_rw_check" *) reg [7:0] gamma_curve[768];')
    gamma = replace_exact(gamma, "reg [1:0] ctr = 0;", "static reg [1:0] ctr = 0;")
    return {"mixer": mixer, "scandoubler_sim.sv": doubler, "gamma_corr_sim.sv": gamma}


def main():
    if len(sys.argv) != 2:
        raise ValueError("usage: make_sim_video_mixer.py OUTPUT_VIDEO_MIXER")
    output = pathlib.Path(sys.argv[1]).resolve()
    if output.is_relative_to((ROOT / "sys").resolve()):
        raise ValueError("simulation copies must not be written into sys/")
    sources = {name: (ROOT / "sys" / name).read_text(encoding="utf-8") for name in PINNED}
    adapted = adapt_sources(sources)
    output.parent.mkdir(parents=True, exist_ok=True)
    for name, source in adapted.items():
        target = output if name == "mixer" else output.parent / name
        target.write_text(source, encoding="utf-8")
    print("Generated syntax-only video copies; scandoubler/HQ2x output is not covered.")


if __name__ == "__main__":
    try:
        main()
    except (OSError, ValueError) as error:
        sys.exit(str(error))
