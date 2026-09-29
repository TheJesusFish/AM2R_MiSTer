#!/usr/bin/env python3
"""Write a simulation copy of sys/video_mixer.sv for open-source simulators.

Upstream declares R_in/G_in/B_in inside unnamed generate blocks and uses them
outside, which Quartus accepts but Icarus and Verilator do not. The copy hoists
the full-depth declarations (HALF_DEPTH=0, the only depth AM2R uses) out of
the generate block. sys/ itself is never modified.
"""
import pathlib
import re
import sys

root = pathlib.Path(__file__).resolve().parents[3]
source = (root / "sys" / "video_mixer.sv").read_text()
block = re.compile(r"generate\s*\n\s*if\(GAMMA && HALF_DEPTH\) begin.*?endgenerate\n", re.S)
hoisted = ("wire [DWIDTH:0] R_in = frz ? 1'd0 : R;\n"
           "wire [DWIDTH:0] G_in = frz ? 1'd0 : G;\n"
           "wire [DWIDTH:0] B_in = frz ? 1'd0 : B;\n")
result, count = block.subn(hoisted, source, count=1)
if count != 1:
    sys.exit("sys/video_mixer.sv no longer has the expected R_in generate block")
pathlib.Path(sys.argv[1]).write_text(result)
