# Verification

- `am2r-1.1/smoke-inputs.json` is the deterministic title-to-first-room keyboard
  sequence used by the Butterscotch smoke and persistence probe.
- `rtl/am2r_video_test_tb.sv` checks a complete synthetic raster, blanking and
  sync intervals, including the frame wrap. Its pattern generator is compiled
  only by the testbench and is not part of the production RBF.
- `renderer/am2r_axis_coverage_test.c` locks the pixel-center edge rule that
  prevents fractional sprite quads from sampling one extra atlas row.
- `renderer/audit_sw_renderer.py` verifies that all 70 renderer vtable hooks are
  assigned/accounted for and that unsupported FPGA states have explicit
  coherency/fallback telemetry.
- `runtime/overlay_file_system_atomic_test.c` verifies that text/INI updates
  publish through an atomic sibling rename and that a failed temporary write
  leaves the previous destination intact.

The remaining coverage boundary is representative later-game content and
physical controller/CRT acceptance. User game data and captures remain ignored.
