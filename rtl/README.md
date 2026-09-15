# Project-specific FPGA logic

`am2r_video_test.sv` is the compile-time/native-video bring-up raster. It is not
an AM2R renderer and must not be used as evidence of game compatibility.

The standard MiSTer framework is imported unmodified under `sys/` from
Template_MiSTer commit `3ea1134cf05d62c2b1db30362277a823d739ced2`.
Core-specific presentation and measured graphics acceleration belongs here;
generic framework memory/scaler logic stays in `sys/`.

`crt_adjust.sv` and `crt_vsize.sv` are vendored from MiSTer-CRT-Adjust commit
`c682de9f4acc61d8f4c7779efb48149d3baa3a8e` under GPL-3.0-or-later.
`am2r_crt_pipeline.sv` supplies the core-side AM2R integration; it does not
modify the framework under `sys/`.

`am2r_osd.v` is a core-local, interface-compatible copy of the framework OSD.
Only its 4 KiB pixel store differs: an explicit dual-clock `altsyncram` keeps
each of MiSTer's two OSD instances in M10K memory under Quartus 17. The project
selects it through `../am2r_sys.tcl` and `../am2r_sys.qip`; `sys/` remains the
unmodified pinned Template tree.

See [HDL notes](../references/hdl-notes.md) and
[MiSTer integration notes](../references/mister-integration-notes.md).
