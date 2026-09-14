# Milestones and evidence gates

Read [bootstrap.md](../bootstrap.md), [architecture.md](architecture.md),
[hardware.md](hardware.md), and [sources.md](sources.md) first. These are the
evidence gates used by the project. Automated portions of milestones 0–4 now
pass on USB-1; milestone 5 is at engineering-build handoff with physical CRT,
physical controller/reconnect, and broader late-game coverage still pending.
See [state.md](state.md) and the
[pipeline hardware report](../reports/hardware-validation-2026-09-06.md) plus
[normal-core/save-state report](../reports/savestate-validation-2026-09-06.md)
rather than
inferring status from the generic checklist below.

## 0. Establish reproducible inputs

- Identify the exact AM2R release, platform payload, data format, asset sources, runtime candidates, and required extensions/shaders. Record hashes of locally supplied game files without committing payloads.
- Inventory relevant board/RAM, MiSTer software, controllers and accessible JTAG/SSH/capture interfaces. Use the current context's machine as the build host and discover Quartus locally. CRT model and analog IO hardware are not bootstrap prerequisites.
- Implement the agreed 320×240 4:3 native analog target through the standard MiSTer framework; record selected timings and gameplay pacing. Preserve the established hybrid-core scope.

**Gate:** another developer can identify the same inputs and constraints from the recorded manifest. No runtime is selected solely because its README names AM2R or ARM Linux.

## 1. Desktop runtime compatibility

Evaluate Butterscotch first. Use gmloader as a documented alternative if necessary, keeping each runtime's results separate. Inventory used GML functions and rendering/audio features, then exercise title/menu, room transitions, representative enemies and effects, collision-sensitive movement, save/load, and sustained gameplay. Include scenes stressing surfaces, blending, transforms, and palette behavior where the chosen build uses them.

**Gate:** a compatibility matrix distinguishes working, failing, untested, and intentionally altered behavior. Record runtime commit, build settings, logs, reproducible saves/routes, and reference comparisons. A booting title screen does not pass. An unresolved essential behavior blocks claiming a playable port; decide whether to fix it, change runtime, or revise scope.

## 2A. HPS CPU and memory experiment

After the runtime can execute representative gameplay, run the same workload on the actual HPS with drawing cost isolated. Preserve simulation and render-dependent state: surfaces or image data used by collisions and scripts cannot simply disappear. Specify whether audio decoding/mixing remains active and what the benchmark excludes.

Measure update time, p50/p95/p99/max frame work, peak memory, loading behavior, and sustained behavior across light and heavy scenes. Record CPU clocks, compiler settings, game/runtime versions, and sampling duration. Separate active work from waits and frame pacing.

**Gate:** evidence provides a CPU lower bound, not a full-game frame-rate claim. At a nominal 60 Hz, total frame work has about 16.67 ms; logic already consuming that budget requires optimization or a scope change before rendering can fit. Establish explicit rendering, audio, and presentation budgets from the measurements.

## 2B. Independent FPGA video experiment

Proceed alongside runtime research using the standard MiSTer framework analog interface and the locally discovered toolchain. Generate a 320×240 active raster with documented progressive approximately 15 kHz/60 Hz timing. Verify timing, blanking, aspect ratio, buffer handoff and restoration on exit in simulation and the framework integration. Test HDMI separately and record its scaling and refresh behavior.

**Gate:** native-raster/interface checks pass and the FPGA artifact satisfies timing constraints. Record physical analog-output testing separately as passed, failed or not yet tested. An unavailable CRT does not block subsequent development; a stable HDMI recording alone does not verify physical analog output.

## 3. Select and prove the renderer boundary

Profile a correct renderer using the representative scenes. Try the least complex compatible software path first; evaluate FPGA acceleration only against measured expensive operations. Document the supported drawing/effect semantics and any proposed visual compromises before accepting them.

Specify buffer ownership transitions, cache coherency, barriers, frame submission/acknowledgment, and recovery. Stress shared DDR arbitration with concurrent runtime, audio, and scanout traffic. Detect stale or partially written frames, tearing, underruns, and missed presentations.

**Gate:** correctness comparisons pass; measured throughput, latency, memory, and bandwidth fit the agreed budgets with recorded headroom. A fast synthetic blitter alone does not pass. Record the decision and evidence for the selected runtime and renderer.

## 4. Integrate gameplay and MiSTer operation

Connect controllers, audio, save locations, launch, exit, and recovery. Verify mapping and reconnect behavior, save/load across relaunch, and error handling for missing or invalid data. Specify the relationship among game stepping, video refresh, and audio clocks, including buffering and any resampling. Measure input-to-presentation behavior rather than promising a latency level.

**Gate:** a repeatable launch → play → save → exit → relaunch workflow; no unexplained save loss or timing drift. Run a proposed 30-minute stress session with no observed audio underruns, recording counters and settings. Choose a longer run if observed failures justify it.

## 5. Hardware regression and handoff

Repeat representative light/heavy scenes, transitions, effects, and sustained play on the target DE10-Nano. Report full-frame work and presentation pacing, missed frames, audio underruns, peak memory, and input behavior. Test physical analog output when observable; otherwise record that evidence as outstanding while completing available regression and handoff. Use HDMI captures for comparisons while accounting for capture-device buffering and frame-rate conversion.

**Gate:** the agreed compatibility and performance targets pass on hardware, or remaining failures are explicitly documented as blockers. Deliver reproducible build/run instructions, exact dependency revisions, interface documentation, benchmark methods/results, known limitations, and a clean recovery procedure. Describe the result accurately as hybrid or pure FPGA according to where execution actually occurs.
