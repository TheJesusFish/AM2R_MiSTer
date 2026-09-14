# AM2R 1.1 compatibility matrix

This matrix describes the exact ARM/FPGA engineering build identified in the
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
| Story/cutscenes | Pass in opening route | Normal/additive tint, gradients, affine ship/Samus effects captured |
| Room transitions | Pass in opening route | Controller, loading, transition, title, landing and `rm_a0h01` rooms |
| First playable area | Pass | Deterministic movement/actions and active scrolling traversal |
| Aimed firing | Pass in opening gameplay | Straight, up, down, diagonal-up, and diagonal-down six-second phases hold 59.9–60.0 FPS with zero FPGA fallbacks |
| Map open/close and discovery | Pass in opening gameplay | Cold map builds once; repeated opens hit the retained surface and newly revealed cells update incrementally at sustained 60 FPS |
| Textures/sprites | Pass in route | Lazy texture pages, clipping, axis and affine nearest-neighbour draws |
| Alpha/additive blend | Pass in route and pixel test | Exact target pixel assertions plus title/story use |
| GameMaker surfaces | Pass in route | Off-screen CPU surfaces and five deliberate output readbacks in 9,000 frames |
| Underwater distortion/foreground | Pass in supplied room | Deferred application-surface export, native water alias, and repeated additive-tile fusion; 1,681-frame USB-1 jump capture had zero horizontal/vertical artifact flags and mostly 59.9–60.0 FPS telemetry |
| Audio decode/mix/output | Pass in route and transport fixtures | Fixed 48 kHz producer; exact 1 kHz transport, native Start-sound comparison, nonzero gameplay peaks, and no observed xrun |
| Save/create/reload | Pass | Stable 236,913-byte `sav1` hash across relaunch |
| Persistent save states | Pass only on the exact runner build | Four disk-backed DMTCP slots; empty load, overwrite, atomic commit, GPU/derived-surface rebuild, audio resume, and exit/relaunch passed. Format-3 metadata checks build ID plus runner CRC32 before replacing the live process. USB-1 v24 measured 60.43 s save / 2.60 s load; old process images are preserved but rejected because they embed old executable code. |
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
