# Analog-only CRT UI inset: feasibility

Status: **proposal, not implemented or hardware-validated**. Source inspection
on September 26, 2026 establishes a route that leaves the upstream `sys/` tree
untouched. It does not establish its performance or latency.

## Current path

```text
one completed image, including UI inset
  -> native scanout -> CRT position / H Scale -> emu VGA RGB
                                                   |-> native analog
                                                   `-> HDMI ascal
```

The UI inset changes rendering coordinates before final presentation; both
outputs therefore receive those changed pixels. `AM2R.sv` connects the output
of `am2r_crt_video` to the core's sole RGB/sync interface. In `sys/sys_top.v`,
`hr_out`/`hg_out`/`hb_out` and the HDMI input timing are aliases of the same
VGA-derived stream, which feeds `ascal`.

The existing H Scale and position controls also precede this split. HDMI
scaling may conceal their visible geometry changes, but these controls are
**not literally independent HDMI and analog RGB paths**. Moving the inset
into that same CRT block would not by itself isolate it from HDMI.

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
