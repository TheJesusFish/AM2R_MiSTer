# Project-specific FPGA logic

`am2r_video_test.sv` is a testbench-only native-video timing raster. It is not
compiled into the production core, is not an AM2R renderer, and must not be
used as evidence of game compatibility.

The standard MiSTer framework is imported unmodified under `sys/` from
Template_MiSTer commit `3ea1134cf05d62c2b1db30362277a823d739ced2`.
Core-specific presentation and measured graphics acceleration belongs here;
generic framework memory/scaler logic stays in `sys/`.

See [HDL notes](../references/hdl-notes.md) and
[MiSTer integration notes](../references/mister-integration-notes.md).
