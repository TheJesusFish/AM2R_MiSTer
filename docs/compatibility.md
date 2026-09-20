# AM2R 1.1 compatibility matrix

This matrix describes the exact ARM/FPGA engineering build identified in the
[slot 1/2/3 performance report](../reports/slot123-performance-validation-2026-09-17.md),
[first-pit black-screen report](../reports/first-pit-black-screen-validation-2026-09-17.md),
[projectile and sand-clearing performance report](../reports/sand-clearing-performance-validation-2026-09-17.md),
[subscreen rendering report](../reports/subscreen-rendering-validation-2026-09-16.md),
[room-122 collision/HUD report](../reports/room122-collision-hud-validation-2026-09-16.md),
[FPGA-vblank pacing report](../reports/vblank-pacing-validation-2026-09-16.md),
[heavy-room pacing report](../reports/heavy-room-pacing-validation-2026-09-16.md),
[v24 water/pickup/latency report](../reports/v24-water-pickup-latency-savestate-validation-2026-09-11.md),
[portable-save/map hardware report](../reports/portable-save-map-performance-validation-2026-09-08.md),
[Linux 6.18 compatibility report](../reports/linux-6.18-framebuffer-validation-2026-09-08.md),
[preceding regression closeout](../reports/regression-closeout-2026-09-08.md),
[renderer audit](../reports/renderer-operation-audit-2026-09-08.md),
[audio audit](../reports/audio-fidelity-validation-2026-09-08.md),
[controls/state report](../reports/controls-state-validation-2026-09-07.md),
[performance report](../reports/aim-map-performance-validation-2026-09-07.md),
[pipeline hardware report](../reports/hardware-validation-2026-09-06.md), and
[normal-core/save-state baseline](../reports/savestate-validation-2026-09-06.md).
“Pass” means
observed on the USB-1 MiSTer, not merely supported by source inspection.

| Area | Status | Evidence / boundary |
| --- | --- | --- |
| WAD/data load | Pass | Exact AM2R 1.1 VM/WAD 14 input boots repeatedly |
| Linux framebuffer compatibility | Pass on 6.18; older path preserved in code | USB-1 6.18.38 uses the `ENODEV`-only `/dev/mem` fallback; the original `/dev/fb0` mmap remains first choice |
| Title and menu | Pass | Deterministic create/reload routes and HDMI capture |
| In-game subscreen | Pass | USB-1 lossless capture through Map, Equipment, Logs, and Options. Connector lines and four-corner gradients stay in the atomic FPGA command stream; 24-frame consecutive sheets contain no partial frames, all unsupported fallback counters remain zero, and the cached chooser runs in about 0.76 million cycles. |
| Story/cutscenes | Pass in opening route | Normal/additive tint, gradients, affine ship/Samus effects captured |
| Room transitions | Pass in opening route | Controller, loading, transition, title, landing and `rm_a0h01` rooms |
| First playable area | Pass | Deterministic movement/actions and active scrolling traversal |
| Supplied high-load rooms | Pass at native cadence | The FPGA vblank heartbeat fixes ARM/raster phase drift. Ordered nearby collision candidates and sparse double-buffered HUD updates removed the earlier room-122 spikes. Exact subtractive fills and FPGA subtract blending fixed the catastrophic slot-3 flash; compact sparse uploads now retain only the 320×240 viewport sampled from its 512×256 lighting surface. The matching room-159 USB-1 movement test delivered a 59.86 Hz sequence rate with all 103 commands present, 3.61 ms Draw p95, and no alternating blank frames in a 1,441-frame UGREEN capture. Real-time game/audio scheduling removes the recurring five-second Linux-service hitch. |
| Aimed firing | Pass in opening gameplay | Straight, up, down, diagonal-up, and diagonal-down six-second phases hold 59.9–60.0 FPS with zero FPGA fallbacks |
| Projectile/destructible-sand churn | Pass in two supplied rooms | Dense self-variable iteration and incremental drawable ordering remove the full-table and full-room work formerly triggered by each projectile/sand destruction burst. USB-1 captures visibly clear the right wall and floor without the old sustained hitch; the 28.017-second jump/down-shot run contains 1,681 frames and 23 isolated adjacent repeats. |
| Long-session texture churn / CPU fallback | Pass | The supplied first-pit checkpoint reproduced a persistent black screen with all 64 legacy texture records occupied. Released surfaces now return records for reuse, capacity is 128, and software-frame presentation has a dedicated FPGA staging allocation. A forced-full-table USB-1 run exercised an actual unsupported-draw fallback for 721 captured frames with no black interval or presentation failure. |
| Map open/close and discovery | Pass in opening gameplay | Cold map builds once; repeated opens hit the retained surface and newly revealed cells update incrementally at sustained 60 FPS |
| Textures/sprites | Pass in route | Lazy texture pages, clipping, axis and affine nearest-neighbour draws |
| Alpha/additive blend | Pass in route and pixel test | Exact target pixel assertions plus title/story use |
| GameMaker surfaces | Pass in route | Off-screen CPU surfaces and five deliberate output readbacks in 9,000 frames |
| Underwater distortion/foreground | Pass in supplied room | Deferred same-frame application-surface export, batched FPGA water rows, and repeated additive-tile fusion. The prior-native-frame alias was removed after exact slot-1 A/B exposed motion-dependent stale bands. |
| Audio decode/mix/output | Pass in route and transport fixtures | Fixed 48 kHz producer; exact 1 kHz transport, native Start-sound comparison, nonzero gameplay peaks, and no observed xrun |
| Save/create/reload | Pass | Stable 236,913-byte `sav1` hash across relaunch |
| Persistent save states | Partial; exact runner build and fresh launch required | Four DMTCP slots support empty load, overwrite, atomic commit, GPU/derived-surface rebuild, audio resume, and exit/relaunch. Format-3 metadata checks build ID plus runner CRC32 before replacing the live process. Checkpoints stage on the persistent filesystem rather than `/dev/shm`; a 256 MiB preflight prevents OOM and preserves the old slot on `ENOSPC`. The final USB-1 regression atomically committed a 171,746,660-byte checkpoint in 135.015 s, restored it, and kept the game process stable. Because DMTCP cannot reliably checkpoint a restored process, the frontend now immediately refuses that nested save with Linux `ENOTSUP`, preserves the existing slot, and resumes gameplay. Save latency varied from 49 to 135 seconds; loads completed in a few seconds. Old process images are preserved but rejected because they embed old executable code. |
| Keyboard-style MiSTer input | Pass | Live event0 handle plus deterministic D-pad/action route |
| Normal core launch/exit/recovery | Pass | **Other → AM2R** selects the core-specific HPS frontend; repeated launches and Weapon Select+Start/signal exits restored clean `MENU` state |
| OSD save-state control | Pass; user hands-on remains | Exact current build committed through OSD Save and replaced/restored the live runner through OSD Load; immediate status feedback is shown |
| Bindable Save State action | Pass; physical-controller acceptance remains | Temporary per-core mapping produced core mask `0x00002000` on USB-1 and atomically committed the selected slot; unbound by default |
| OSD Reset | Pass | Exact current build terminated the active runner and spawned a fresh runner while the AM2R core remained loaded |
| Screenshot/PNG save | Pass in target route | Real fast stored-DEFLATE PNG writer is exercised by AM2R transitions; logs show successful 320×240 read/encode/write |
| Physical controller/reconnect | User test | No automated mechanism pressed or unplugged the user's gamepad |
| Physical analog CRT | User test | Standard framework path is used; HDMI does not prove CRT behavior |
| Later areas, enemies, bosses | Partial | Supplied Hydro Station, underwater, Alpha Metroid, missile pickup, lava, room-boundary, destructible-wall, ledge, and door cases have focused hardware evidence; comprehensive playthrough remains untested |
| Every shader/effect combination | Structurally covered; later-game observation remains | All 70 renderer hooks are assigned/accounted for; unsupported states synchronize and fall back with per-reason telemetry. The final route recorded zero fallbacks. |

## Intentional platform behavior

- Display timing is fixed at native 320×240; window/fullscreen controls are
  no-ops. The former scaling-based CRT Safe option was removed.
- The core is treated as fullscreen; pause state reports false.
- Controller vibration is a no-op because no MiSTer rumble transport is wired.
- Modal OS message boxes are logged instead of opening a desktop dialog.
- Pixel-art texture sampling is nearest-neighbour.

These changes adapt desktop GameMaker APIs to a core-style appliance and do not
alter the tested game logic. Any new fallback or visual difference found during
user play should be captured with the room/effect and promoted into this matrix.
