# Analog-only CRT UI inset: feasibility

Status: the HDMI/analog output split is **implemented**; the analog-only UI
inset is **not**.

## Current path

```text
GPU publishes a completed image, including UI inset, into native buffer N
  |-> native reader -> CRT position / H Scale -> emu VGA RGB -> analog
  `-> am2r_hdmi_fb (MISTER_FB, FB_BASE = buffer N) -> ascal -> HDMI
```

HDMI reads the published XRGB8888 frame from DDR through the core framebuffer
interface (`rtl/am2r_hdmi_fb.sv`), so the CRT position and horizontal-scale
controls no longer reach HDMI. `am2r_hdmi_fb` selects the latest published
buffer at each HDMI vblank, before ascal copies `FB_BASE` at the falling edge
of HDMI VSync, and reports the buffers ascal may hold so the GPU never
overwrites them. Four native buffers reduce contention, but do not guarantee
non-blocking publication: if all four are protected, the GPU waits until a
reader releases one. Frame pacing and latency require measurement for both
outputs.

The UI inset still changes rendering coordinates before publication, so both
outputs receive the moved HUD. Making it analog-only requires the second final
composition described below.

## Proposed separation

```text
shared gameplay/effects render + separately retained UI
  |-> original-layout final composition -> framework framebuffer -> HDMI
  `-> inset-UI final composition -> native scanout / CRT controls -> analog
```

Render gameplay and expensive effects once. Produce two final compositions
with different UI placement, not two game updates or two full scene renders.
UI must remain separate until composition: shifting rows of an already
flattened frame would also shift background pixels or leave the old HUD behind.
Title overlays and other inset-eligible UI need the same explicit treatment.

The existing conditional `MISTER_FB` interface in `sys/emu_ports.vh` supplies
`FB_EN`, `FB_FORMAT`, `FB_WIDTH`, `FB_HEIGHT`, `FB_BASE`, `FB_STRIDE`, and
`FB_FORCE_BLANK`, with `FB_VBL`/`FB_LL` feedback. `sys/sys_top.v` already routes
these into `ascal` when the HPS local-framebuffer override is inactive. Enabling
and driving those core ports, while retaining native VGA output, can provide
the split without editing `sys/`. The current wrapper disables the inherited
local-framebuffer route at startup; that lifecycle behavior must remain
compatible with the proposed core-owned framebuffer.

## Configuration boundaries

- Normal scaled HDMI plus native analog is the intended independent-output
  configuration.
- `direct_video` intentionally sends the native raster through HDMI. That
  output would retain the CRT adjustments; HDMI-to-CRT adapters using this
  mode are not a separate unadjusted display path.
- With `vga_scaler` or `vga_fb` enabled, analog instead uses the HDMI/scaler
  composition and bypasses the independently adjusted native raster. The UI
  and documentation must not promise analog-only inset in that configuration.

## Required implementation and validation

- Define frame publication, clock-domain crossings, and buffer ownership for
  both consumers. `ascal.vhd` latches framebuffer base at HDMI VS falling edge,
  independently of native scanout. `FB_VBL` is not an acknowledgement that
  arbitrary old buffers are safe to overwrite. Do not reuse the current
  native-only lifetime rules unchanged.
- Keep each image immutable while its consumer reads it. Bound buffering and
  backpressure; test mismatched display rates, late frames, reset, core exit,
  save-state restore, and option changes for tearing and stale images.
- Measure extra GPU work and DDR traffic in previously slow rooms. A
  320x240x4 image is 307,200 bytes; each additional full-frame transfer at 60 Hz
  is approximately 18.4 MB/s, before read/composition overhead. This arithmetic
  is not a performance guarantee; the framebuffer path may also change other
  scaler traffic.
- Recheck Quartus resource use and timing closure. Simulate publication and
  stalled-memory behavior, then verify both output paths on hardware.
- Compare HDMI pixels with inset off/on, verify complete HUD/title movement
  only on native analog, and check frame cadence and added latency explicitly.
  A UGREEN HDMI capture cannot alone establish physical CRT geometry or
  controller-to-display latency.

This is a separate presentation change from repairing incomplete HUD movement
in the current shared-output inset implementation.
