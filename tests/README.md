# Verification

- `am2r-1.1/smoke-inputs.json` is the deterministic title-to-first-room keyboard
  sequence used by the Butterscotch smoke and persistence probe.
- `rtl/am2r_video_test_tb.sv` checks a complete synthetic raster, blanking and
  sync intervals, including the frame wrap. Its pattern generator is compiled
  only by the testbench and is not part of the production RBF.
- `renderer/am2r_axis_coverage_test.c` locks the pixel-center edge rule that
  prevents fractional sprite quads from sampling one extra atlas row.
- `renderer/test_subtractive_blend.py` exhaustively proves the NEON helper's
  divide-by-255 identity and compares 250,000 deterministic modulated channels
  with the scalar subtractive-lighting formula.
- `renderer/audit_sw_renderer.py` verifies that all 70 renderer vtable hooks are
  assigned/accounted for, subtractive/additive layers cannot trigger opaque
  prefix removal, water samples the current application-surface export rather
  than the prior native frame, the exact subtractive-lighting span has its
  scalar and ARM NEON paths, zero-area hit effects remain no-ops, and
  unsupported FPGA states have explicit coherency/fallback telemetry.
- `runtime/audit_am2r_native_lighting.py` locks the exact AM2R 1.1 lighting
  Step identities, redundant-Normal-Step removal, fade-out behavior, and the
  real-hardware decision to keep the authored Other-11 producer bytecode.
- `runtime/overlay_file_system_atomic_test.c` verifies that text/INI updates
  publish through an atomic sibling rename and that a failed temporary write
  leaves the previous destination intact.
- `runtime/audit_savestate_pipeline.py` prevents save-state staging from
  returning to memory-backed `/dev/shm`, and locks the disk-space preflight,
  atomic publication, runner-release, and build-compatibility contracts.
- `runtime/audit_logical_savestate.py` locks the version-4 logical-state data
  fingerprint, payload CRC, validation-before-mutation ordering, atomic
  publication, serialized runner/renderer/audio coverage, and OSD completion
  handling.
- `hardware/logical_savestate_hardware_regression.py` backs up one selected
  `.fast` slot on USB-1, performs two save/load generations, verifies that the
  runner PID and ordinary saves are unchanged, rejects a corrupt copy, and
  restores the original slot in a `finally` block. Build
  `tools/am2r_logical_state_request.c` for ARM before running it.
- `hardware/run-savestate-hardware-regression.ps1` runs the real DMTCP path on
  USB-1. It temporarily renames one selected slot, then checks save, load,
  low-space refusal when applicable, runner survival, absence of a new kernel
  OOM event, and preservation of the ordinary AM2R save. The original slot is
  restored in a `finally` block. Start AM2R and load an existing game before
  running it; slot 4 is used by default.

The Gamma-room hardware regression uses the user-supplied checkpoint, USB-1,
and the UGREEN capture path. With lighting and the boss active, sample native
game-frame progression, exercise movement and successful missile hits, and
inspect a lossless 60 Hz capture for corruption. QA-only health and missile
writes are applied to the live process after launch and are never compiled or
saved. The final exact-source run advanced 1,190 game frames in 20 seconds with
no skipped game counters, while its 1,561-frame UGREEN capture showed the
expected dark-room motion and lighting without a wide corruption frame. The
broader native Other-11 experiment remains disabled because its hardware A/B
had worse tail latency than the authored bytecode path.

The remaining coverage boundary is representative later-game content and
physical controller/CRT acceptance. User game data and captures remain ignored.
