# Reproducible upstream changes

This directory preserves the MiSTer port made in the otherwise ignored
`third_party/Butterscotch` checkout.

- Upstream: `https://github.com/ButterscotchRunner/Butterscotch.git`
- Base commit: `7c2503efc25f20dddb9ba7b7cf7b46fd4f63ba08`
- Purpose: ARM/MiSTer backend, FPGA command transport, software-renderer bridge,
  audio instrumentation, save/file builtins, and AM2R runtime compatibility.

Apply the patches from the root of a clean checkout at that exact commit:

```sh
git apply ../../patches/butterscotch-tracked.patch
git apply ../../patches/butterscotch-mister.patch
git apply ../../patches/butterscotch-mister_gpu.patch
git apply ../../patches/butterscotch-sw_renderer.patch
git apply ../../patches/butterscotch-sw_renderer_h.patch
git apply ../../patches/butterscotch-savestates.patch
git apply ../../patches/butterscotch-persistent-savestates.patch
git apply ../../patches/butterscotch-controls-hints.patch
git apply ../../patches/butterscotch-fpga-performance.patch
git apply ../../patches/butterscotch-scanout-diagnostics.patch
git apply ../../patches/butterscotch-audio-rate.patch
git apply ../../patches/butterscotch-axis-coverage.patch
git apply ../../patches/butterscotch-renderer-audit.patch
git apply ../../patches/butterscotch-linux618-framebuffer.patch
git apply ../../patches/butterscotch-surface-revision-map-performance.patch
git apply ../../patches/butterscotch-hud-composite.patch
git apply ../../patches/butterscotch-collision-destroyed-reciprocal.patch
git apply ../../patches/butterscotch-spatial-grid-activation.patch
git apply ../../patches/butterscotch-collision-self-order.patch
git apply ../../patches/butterscotch-atomic-text-writes.patch
git apply ../../patches/butterscotch-room-state-restoration.patch
git apply ../../patches/butterscotch-application-surface-snapshot.patch
git apply ../../patches/butterscotch-opaque-prefix-cull.patch
git apply ../../patches/butterscotch-water-native-deferred-export.patch
git apply ../../patches/butterscotch-water-pipeline-performance.patch
git apply ../../patches/butterscotch-large-file-metadata.patch
git apply ../../patches/butterscotch-crt-ui-inset.patch
git apply ../../patches/butterscotch-savestate-reliability.patch
git apply ../../patches/butterscotch-runtime-performance.patch
git apply ../../patches/butterscotch-heavy-room-pacing.patch
git apply ../../patches/butterscotch-hardware-reattach-vblank.patch
git apply ../../patches/butterscotch-opaque-suffix-cull.patch
git apply ../../patches/butterscotch-room122-collision-hud.patch
git apply ../../patches/butterscotch-subscreen-rendering.patch
git apply ../../patches/butterscotch-sand-churn-performance.patch
git apply ../../patches/butterscotch-texture-record-reclamation.patch
git apply ../../patches/butterscotch-lighting-pacing.patch
git apply ../../patches/butterscotch-lighting-visible-crop.patch
git apply ../../patches/butterscotch-metroid-lighting-hit.patch
git apply ../../patches/butterscotch-lighting-native-events.patch
git apply ../../patches/butterscotch-logical-savestates.patch
git apply ../../patches/butterscotch-sand-draw-cache.patch
git apply ../../patches/butterscotch-lighting-crop-reuse.patch
git apply ../../patches/butterscotch-native-inverse-source-blend.patch
git apply ../../patches/butterscotch-authored-light-step.patch
git apply ../../patches/butterscotch-neon-solid-subtract.patch
git apply ../../patches/butterscotch-render-diagnostics.patch
git apply ../../patches/butterscotch-scaled-light-upload.patch
git apply ../../patches/butterscotch-packed-mask-unroll.patch
git apply ../../patches/butterscotch-crop-gap-diagnostics.patch
git apply ../../patches/butterscotch-crop-split-upload.patch
git apply ../../patches/butterscotch-offscreen-rendering.patch
git apply ../../patches/butterscotch-unified-renderer.patch
git apply ../../patches/butterscotch-crt-ui-composition.patch
```

The fifty-four files preserve the working tree, including the native-lighting
correction described below. Each applies in the order above; applying them to a fresh
detached clone at the base commit reconstructs the current runner source after
normalizing checkout line endings. See the final hardware and save-state
reports under `reports/` for build identities and test results. The first
seven patches were reconstructed and byte-compared with the
80 MHz hardware-tested runner source on 2026-09-06. The eighth adds semantic
controller bindings, recorded-script precedence for AM2R's compatibility
helpers, and the VM-only presentation stub. The ninth adds the hardware-tested
GPU map-transition compositor, retained/incremental AM2R map surface, exact
application-surface handoff, and associated performance fixes. The next four
patches add scanout diagnostics, fixed 48 kHz production audio, pixel-center
axis coverage, per-corner/gradient rendering, and explicit operation/fallback
telemetry. The Linux compatibility patch keeps the original `/dev/fb0` mmap
path and adds an `ENODEV`-only `/dev/mem` fallback for Linux 6.18. The fifteenth
patch adds revision-aware dynamic surfaces, direct small-surface uploads, the
multi-source retained map compositor, and invalidation of FPGA-only derived
surface caches after save-state restore. The first fifteen were
clean-applied to a fresh detached base checkout and normalized-content-compared
with the exact hardware candidate on 2026-09-08. The sixteenth patch exports
the application surface through the FPGA and composites AM2R's GUI/HUD layer
from a cropped transparent surface, avoiding a full 320x240 CPU upload.
The seventeenth collision-dispatch patch preserves GameMaker's reciprocal event when a
non-solid projectile destroys itself in its own handler. Without this, the
later object-index pass skipped the inactive projectile and AM2R's Alpha
Metroid never received its missile-side damage event. `git diff --check`
passes. The eighteenth patch repairs the spatial-grid activation invariant:
instances deactivated before their first pending insertion now clear their dirty
state, while clean inactive instances retain their cells so AM2R's per-frame
deactivate/reactivate culling has no steady-state grid rebuild cost. Without
this, AM2R's activation-region optimization could reactivate collision solids
which remained permanently absent from `position_meeting` and the other
accelerated collision queries.
The nineteenth patch restores forward instance order for the collision-event
self pass while retaining the precomputed handler table and target-object
buckets. The upstream responder grouping had made self-object order depend on
the lowest collision target subtype: AM2R's beam handled `oSolid` and destroyed
itself before the older `oDoor` instance received the same shot. A controlled
USB-1 A/B test leaves v15's door closed but opens it with this patch, and the
upstream WAD16 object-index collision-order fixture still passes unchanged.
The twentieth patch publishes text and INI changes through a flushed and synced
sibling temporary followed by an atomic rename. An interrupted settings write
can therefore leave either the previous complete file or the new complete file,
but cannot truncate the live `config.ini`. Native Windows, ARM, and actual
USB-2 FAT-storage tests pass; v17 also quarantines and regenerates an already
empty config at startup.
The twenty-first patch distinguishes temporary instance deactivation from
destruction when AM2R snapshots a persistent room, so off-screen exits and
solids survive item-message round trips. It also snapshots mutable legacy-tile
state for persistent rooms and restores the immutable file-authored tiles before
creation code rebuilds a non-persistent room. On USB-1, two rooms retained their
inactive exits across persistent round trips, room 62 rebuilt its runtime
destructible-wall tile, and its restored top exit delivered the character
visibly into room 61 rather than out of bounds.
The twenty-second patch reserves GameMaker surface ID zero as invalid and keeps
application-surface snapshots on the FPGA, fixing AM2R's black missile-upgrade
screen without a synchronous full-frame ARM readback. The twenty-third patch
conservatively discards a render prefix only when a later consecutive layer
group is proven to cover all 320x240 output pixels with opaque samples. Cached
one-bit opacity maps keep the steady-state proof below one millisecond on USB-1
while removing an overwritten full-screen room layer from the FPGA workload.
The twenty-fourth patch preserves application-surface exports requested during
AM2R's Step event and lowers its guarded row-by-row water distortion to the
compact FPGA water command. This fixes the jumping corruption caused when the
old bridge discarded the pre-frame export. The twenty-fifth patch proves exact
unit-step water rows in O(1), preserves their same-frame application-surface
source, and fuses the repeated additive 32-pixel underwater foreground. The
earlier native-buffer alias was removed after an exact USB-1 A/B showed that it
sampled a one-frame-old scene and caused motion-dependent bands in lava. The
patch also matches the three-buffer native publication ABI used by the current
RBF.
The twenty-sixth patch uses the metadata-free POSIX `access(F_OK)` operation for
overlay file-existence checks. This avoids 32-bit ARM `stat()` returning
`EOVERFLOW` for ordinary saves on CIFS shares whose server-provided inode
numbers exceed 32 bits, without changing libc's file ABI. Such files now
participate in the normal save-over-bundle lookup without being recopied.
The twenty-seventh patch adds an experimental whole-pixel vertical inset for
edge UI. It moves text glyphs and AM2R's cropped HUD surface only when their
content enters the top or bottom sixteen-line band. It also moves the separate
AM2R 1.1 title-screen version and URL overlays while leaving the title artwork,
gameplay layers, camera coordinates, and collision coordinates at their native
320x240 positions. The setting is transported through a versioned
wrapper/runner shared-memory field and defaults to off.
The twenty-eighth patch removes the remaining volatile Linux endpoints from
checkpoints, bypasses DMTCP interposition during the save handshake with direct
ARM system calls, preloads the unwind library before checkpoint threads exist,
and tolerates an empty legacy resume marker until its writer has published the
result. These changes make a state portable across core exits and different
MiSTer input-device numbering without changing game state.
The twenty-ninth patch removes a full-room spatial-grid scan from ordinary
instance movement while retaining it for lifecycle cleanup and inconsistent
cache repair. It also replaces byte-at-a-time software framebuffer clears with
packed RGBA stores and adds an exact signed-32-bit fast path for GameMaker's
round-to-even coordinate conversion. USB-1 testing in the supplied high-load
room improved from roughly 46–52 FPS to 57–59 FPS without removing the stale
collision-entry protection used by room transitions.
The thirtieth patch retains exact spatial-grid bounds across AM2R's repeated
deactivate/activate cycle, accelerates single-result line and rectangle queries
without changing their original instance ordering, and removes temporary
profiling instrumentation. It also paces nominal 60 Hz rooms to the core's
exact 59.937 Hz native raster. In an exact USB-1 movement capture this reduced
repeated frames from six, including a four-frame stutter cluster, to the one
repeat expected when recording a 59.937 Hz source at 60.000 fps. A staged
ten-missile Alpha Metroid regression reduced enemy health from 70 to 40,
confirming reciprocal projectile collision behavior remains intact.
The thirty-first patch removes framebuffer, FPGA DDR, and input endpoints from
persistent checkpoints and reattaches them after save or restore. It also
phase-locks ordinary 60 Hz game ticks to a core-local native-vblank heartbeat
published by the FPGA, with the raster-matched absolute timer retained as a
backward-compatible fallback for older RBFs. In the two user-supplied pacing
rooms, controlled USB-1 captures contain one adjacent repeat in 720 and 600
moving frames respectively—the expected 60.000 Hz capture sampling of the
59.937 Hz source—with no multi-frame hitch cluster.
The thirty-second patch shortens an already proven opaque scrolling-layer
group to the latest suffix that still covers all 320x240 output pixels. In the
user-reported room 122 jump, this removes an earlier fully overwritten wrapped
background pair: the hardware command list fell from 56 commands and 247,488
drawn pixels to 53 commands and 169,114 pixels, while FPGA work fell from about
493,000 to 364,000 cycles. With the core's 48-line pacing lead, a synchronized
USB-1 trace advanced exactly one scanout frame on 1,226 of 1,228 measured
rasters and contained only one isolated repeat/skip pair instead of 106 pairs.
The thirty-third patch preserves per-object GameMaker append order while line
and rectangle collision builtins inspect only nearby spatial candidates. In
the same room it reduces missed game ticks from 29 to 8 while retaining the
exact append-order semantics required by the earlier door, grapple, and
projectile-order fixes. It also presents
the HUD as a 320-by-content-height surface view and updates only changed spans
in the inactive FPGA texture buffer. The HUD transfer fell from a median
1.094 ms to 0.277 ms; a final lossless UGREEN run without process-memory
sampling contained 1,137 unique moving frames in 1,140 captures, with three
isolated repeats and no stutter cluster.
The thirty-fourth patch keeps every subscreen primitive in the atomic FPGA
command stream. It lowers Equipment's untextured connector lines through a
one-pixel affine texture, materializes the Logs page's uncommon four-corner
label gradients as compact dynamic textures, and caches 90/270-degree chooser
art as row-major textures for the paired axis blitter. USB-1's fallback count
stayed flat through Map, Equipment, Logs, and Options, consecutive-frame sheets
contained no partial menu frames, and steady chooser cost fell from about
2.15 million to 0.76 million FPGA cycles.
The thirty-fifth patch removes the remaining full-table and full-room work from
AM2R's high-churn projectile and destructible-sand paths. Self-variable maps now
keep dense occupied/reference lists, collision dispatch uses the exact
GameMaker-ordered spatial candidate set, and drawable create, destroy, and depth
changes update the ordered list incrementally instead of rebuilding and sorting
the entire room. On USB-1, 24 rightward shots visibly cleared the supplied sand
wall with no sustained freeze; a separate 28.017-second jump/down-shot capture
cleared the floor and contained only 23 adjacent repeated frames in 1,681
captured frames.
The thirty-sixth patch gives released GameMaker surfaces back to the FPGA
texture-record pool and doubles the record-table capacity. It also reserves a
dedicated native-scanout staging allocation outside that table, so an uncommon
software fallback remains presentable even when every ordinary texture record
is occupied. The supplied first-pit checkpoint reproduced the old persistent
black screen with all 64 records occupied. A forced-full 128-record stress run
on USB-1 subsequently exercised an unsupported-draw fallback for 12.017 seconds
with no failed presentation and no black capture interval.
The thirty-seventh patch accelerates AM2R's subtractive lighting surface with
an exact opaque-rectangle path, sparse double-buffer uploads, and FPGA
subtractive blending. It executes the exact `oBlockSand` Draw event natively,
removes recurring production diagnostics, and gives the vblank-paced game and
audio callback explicit real-time priorities while keeping helper threads at
normal priority across DMTCP save/load. The supplied slot-3 lighting workload
rose from about 29.4 to 58.4 FPS; a final post-restore USB-1 movement capture
contained 1,230 frames with two isolated repeats and no hitch cluster.
The thirty-eighth patch recognizes the exact 320x240 viewport AM2R samples from
its 512x256 subtractive-lighting surface. It keeps compact sparse double-buffer
shadows and FPGA textures for only those visible rows and columns, while the
full-stride path remains available for any other sampling geometry. In the
user-supplied room-159 workload, real USB-1 movement held a 59.86 Hz sequence
rate with all 103 GPU commands present; Draw fell from roughly 5.9 ms to 3.6 ms
at the 95th percentile, and a 1,441-frame lossless UGREEN capture contained no
alternating bright/blank lighting frames.
The thirty-ninth patch vectorizes AM2R's exact subtractive light-sprite blend
on ARM NEON while preserving both integer divide-by-255 stages and the target
alpha channel. It also treats a rotated zero-area sprite as the no-op its two
software triangles already were, preventing the missile-hit effect from
forcing a mid-frame FPGA readback. In the supplied Gamma Metroid room on
USB-1, active lighting rose from roughly 43 FPS to 59.7 FPS; a 12-second
ten-missile run advanced 710 native game frames, damaged the boss from 100 to
50 health, and left the non-axis fallback count at zero. The prior capture's
wide green-corruption frames occupied as many as 65 spatial analysis tiles;
the fixed 2,101-frame UGREEN capture never exceeded 40.
The fortieth patch omits AM2R 1.1's redundant lighting Normal Step rebuild
while retaining its fade-out state change and the authored End Step rebuild.
It also replaces the common scaled-edge subtractive blend's integer divisions
with an exact divide-by-255 identity. A broader native replacement for the
Other-11 producer sequence was rejected after an exact-room USB-1 A/B: the
authored bytecode path advanced at 59.807 FPS with a 21.112 ms p99 and 52.702
ms maximum, while the native candidate measured 57.265 FPS with a 24.989 ms
p99 and 259.639 ms maximum. Other 11 therefore remains interpreted.
The forty-first patch replaces slow whole-process checkpoints with a versioned
AM2R-specific serializer. It captures the VM object graph, room/tile state,
data structures, dynamic surfaces, and active audio, validates a game-data
fingerprint and payload CRC before mutation, and atomically publishes compact
`.fast` slots. The audio engine remains allocated during saves. Real USB-1
two-generation testing completed saves in 281–468 ms and loads in 406–453 ms;
all four slot indices, cross-exit restore, corrupt-state rejection, input,
audio, and ordinary-save integrity passed.
The forty-second patch resolves and validates AM2R 1.1's exact `oBlockSand`
Draw contract once, then renders exact sand instances before generic event
resolution. It also combines each corner-variable existence check and read
into one hash lookup. Every mismatch retains the interpreted path. In the
user-supplied room-122 checkpoint on USB-1, the 24 alternating diagonal jumps
completed 2,039 measured game cycles with no cycle over 20 ms; peak work before
vblank fell from 17.576 ms to 15.977 ms, and the 2,041-frame UGREEN capture had
no repeated-frame cluster or rendering corruption.
The forty-third patch retains the CPU shadows of a cropped lighting texture
when AM2R frees and recreates its surface. After waiting for the prior FPGA
job, it updates only changed rows instead of copying both complete 320x240
textures into strongly ordered DDR every frame. In the same room-159 USB-1
scene, median cropped-upload cost fell from 8.25 to 1.15 ms; steady-state
stage tracing found no frame longer than 20 ms across 603 cycles.

| Patch | SHA-256 |
| --- | --- |
| `butterscotch-tracked.patch` | `9f9b25ef984f20f050efdf8ece67505ae015e7f768423de0fc4cacc8adf6fbcd` |
| `butterscotch-mister.patch` | `d98bd75cfc32a8a82e47b5819ff6700103a436a17e68015e2045d01586844727` |
| `butterscotch-mister_gpu.patch` | `261865469a7c71af83848b391be24d77bd9783e01f22d9904785d087c9af5095` |
| `butterscotch-sw_renderer.patch` | `cc032a7f5f94c80ac744c51af4389ca4c01d35241b98c1f983a61ce9ca0e356e` |
| `butterscotch-sw_renderer_h.patch` | `b5f5af2b65cec0f5d4aa4effac232ad8020c4e337d4f871f17f32e16dfb78ea5` |
| `butterscotch-savestates.patch` | `eafe1099416994718a2cb5e32524ffdf8a61a8bb8bfd07ec8cdc43e57a3ca356` |
| `butterscotch-persistent-savestates.patch` | `9b1e0dda61d10b2a4a517bc5fb835072515a38bc51e3bda9b8febd515ab5a188` |
| `butterscotch-controls-hints.patch` | `d919a85c2a018f8577e27c79da1690d66eb4f231a15bd9636dd2619492fcae74` |
| `butterscotch-fpga-performance.patch` | `b3b84d90315aaab7d07185feea372cae85722f0766e9efa1287c2532a96db940` |
| `butterscotch-scanout-diagnostics.patch` | `5e85d294e306bf69c24f52baad3010f3b17c4ec34c83661859df50d2143f3b0d` |
| `butterscotch-audio-rate.patch` | `c9561ca2147ba3e44b190ddc335d52dd5901e8bf1d107477a7a9d96b38ffce11` |
| `butterscotch-axis-coverage.patch` | `7b392ba661d2c3aef7f1d76ba0c007e551cef59b9e17e6f5b845554defd19ce2` |
| `butterscotch-renderer-audit.patch` | `3debbd5cbea6338d5259004afd9f7f43d6c5ba2576f583c4eba716a64254cc21` |
| `butterscotch-linux618-framebuffer.patch` | `43d8ee4e5d52475993593a809b0b355063a298a59a9bdbd2289536bc6b529cd7` |
| `butterscotch-surface-revision-map-performance.patch` | `d80ae1aebd7687880825a4875fe3ad0c3e3bd2eee98f12c7a94212e5be885107` |
| `butterscotch-hud-composite.patch` | `2447003ce2c7f9a5d09cbd6fc9363c66b8585ae08c975d4113b07c02e83e41d2` |
| `butterscotch-collision-destroyed-reciprocal.patch` | `4ee0a7f771b88a922f67759584fae92d15f4e9efd7b1d10281da72fc23b48b47` |
| `butterscotch-spatial-grid-activation.patch` | `74eba0f27054c2848b4a7c468c4f87e1cb2deaf2eb089b278e5287018e20a6a5` |
| `butterscotch-collision-self-order.patch` | `28acba0d638c77ff7669ebf8fd6ee2febd6edf93d752e09415b26fa4c1be7208` |
| `butterscotch-atomic-text-writes.patch` | `1595ff91501acec18ccc20eadff1ca6a4ad448ccdb4f12830d9da15c579837c4` |
| `butterscotch-room-state-restoration.patch` | `fb65161ba5b9d2bd935d2a142b14f9c96909b9504c2b6256f45da2bffc485d27` |
| `butterscotch-application-surface-snapshot.patch` | `b4ba265d5dec7be08c92b5b09fea9a60be374302f3ab2ffd025a04d3081c20a4` |
| `butterscotch-opaque-prefix-cull.patch` | `db1544cf91ee718365d9a769c4f9d7fb4f44f98ed4c26fee1a60612b0d2ced95` |
| `butterscotch-water-native-deferred-export.patch` | `fd3c2335f90ab461adba4bc59882c3be19ef158d265aeaebde1c9caba5a920bc` |
| `butterscotch-water-pipeline-performance.patch` | `61c5ab75182020d717c3925319efa3478c2ac1af679a59d60492491b032937e2` |
| `butterscotch-large-file-metadata.patch` | `498ac455036b22d9b3e2061c6c798a46d140830279494abcb517fe102cfff9e0` |
| `butterscotch-crt-ui-inset.patch` | `9abbe0daa25b2b047ea4e14834a373445775aa0c316c24d40c46344f78d0711f` |
| `butterscotch-savestate-reliability.patch` | `d7ee348ca1308212e0462a40657d119ff0cf11c8536c73e1acea9e18fa1554ad` |
| `butterscotch-runtime-performance.patch` | `28bcbada72e65b1a7af2cd5e8d5fa63f7ef2db669394ad3770bf0b85dc3d293c` |
| `butterscotch-heavy-room-pacing.patch` | `0c739b100eaa46d13cb260156f70fa89a469fdd6b520338f02c76dda1173a962` |
| `butterscotch-hardware-reattach-vblank.patch` | `bd95428191263c6995cb5cef10e138ab2de97683c0bab3fb4e22b6ea76f81caa` |
| `butterscotch-opaque-suffix-cull.patch` | `e644139a42d9a68db86b6c27a4d748e4c1a7a48a2b146d09ffcb8005eda4f034` |
| `butterscotch-room122-collision-hud.patch` | `e749e752243988a3cf660a6ccca3504f658beb38a190c9810a65cfc25209451a` |
| `butterscotch-subscreen-rendering.patch` | `26ea5183f48dbdc4c2b94bac8142f82bd39ee6bd36d34acb5f6189b92e5d9b7e` |
| `butterscotch-sand-churn-performance.patch` | `69da8604b2c8815395f25b299b2046112c0a3a8b55a07661813c670b98fc59ee` |
| `butterscotch-texture-record-reclamation.patch` | `b60b7a462168296c7328b5339144986eac34bd9ffcc97801f9803becddfbc0d9` |
| `butterscotch-lighting-pacing.patch` | `4c97a9c828f99cd6b990458729caf32a5dbb56086427d8c583a1621db1a1334e` |
| `butterscotch-lighting-visible-crop.patch` | `33af39ca9e3030e4229abd1027c8250d8215dba54ae48175fad1b3f0305e674e` |
| `butterscotch-metroid-lighting-hit.patch` | `3102a3da64e5b98a0084586bac8b67b8fa6766c26ed693b16e305b23616c7b38` |
| `butterscotch-lighting-native-events.patch` | `e5649a320b56f98589a87e57b0348bc1775e942f23487f72e3b1f25f7d7f72e3` |
| `butterscotch-logical-savestates.patch` | `97328613ec428eff567a19cab7e472e28d4e9edae59b4083f2448a55ef363687` |
| `butterscotch-sand-draw-cache.patch` | `df02df579c6bae64b5f2692c37b8b4a7b78480c6a96abf64121a8be76aa3f191` |
| `butterscotch-lighting-crop-reuse.patch` | `257031c7337aad28a9ad5af5cbdebdc3824b02988f16762abf1e80dec05258e7` |
| `butterscotch-native-inverse-source-blend.patch` | `648209c90f90a4fafaf5885bfddc5c44af5cb494a623f281a44d25bca8e9ab81` |
| `butterscotch-authored-light-step.patch` | `478df572b0813f72999dc1d978b0f9d9dd81b6e0505bc6d7227abb490f51c8a1` |
| `butterscotch-neon-solid-subtract.patch` | `2bc3e94b068c13fa66d2cfa399c24098c7c53ff3cb32a865720b6cf765bd5479` |
| `butterscotch-render-diagnostics.patch` | `1f54de1ff4270914337d01bf239b6ce255504fcd35dc309cc55f4c2ba82f313f` |
| `butterscotch-scaled-light-upload.patch` | `7c99d3eb60a5cbdf68b8fffe81ca743ebf69fb1ac925d72ecb3fc52628d42546` |
| `butterscotch-packed-mask-unroll.patch` | `22b71cd49fd2eb08479e0ce8b9d2b863f65e772bd8d468a01dff4f2f388e967d` |
| `butterscotch-crop-gap-diagnostics.patch` | `de988f63dfc26923de65dd3d6cfe9d8e53883764357c1b8740729d954fd36574` |
| `butterscotch-crop-split-upload.patch` | `1b239e06741857f37c1ef955d19ed308f4a1e2f79371ffa45475b2de184a9925` |
| `butterscotch-offscreen-rendering.patch` | `c4bfb6e853ae572eda91ab4f683e5fbbc245f51e7e555ab0d4a845c2eb0f3781` |
| `butterscotch-unified-renderer.patch` | `1d26a90fde435b34161c18dec55b249839d82e738711388c74910e21bc24dea6` |
| `butterscotch-crt-ui-composition.patch` | `ebf4b31b16fa44b4152300b397b127ffa9928688d6fe1de594a4a850973a03d2` |

## General FPGA rendering experiment

Patch 53, `butterscotch-unified-renderer.patch`, replaces screen/effect-specific
ownership with persistent full-size RGBA targets, tiled framebuffer transfers,
ordered copies and snapshots, and explicit no-present synchronization. It adds
constant-size setup for FPGA triangles, four-corner gradients, clamped texture
sampling, separate blend factors, channel masks, alpha testing and constant fog.
The software custom-factor reference is corrected rather than treating custom
blending as ordinary source-alpha blending. Shader support is not claimed.
Triangle coverage uses single-owner top/left edges in both packet setup and
the software reference, preventing double blending at shared quad/fan edges.

This branch is still under validation. `AM2R_GPU_UNIFIED=1` opts in on a capable
RBF; `AM2R_GPU_UNIFIED_STRICT=1` makes unexpected software rendering a QA failure.
Default builds retain the legacy path until hardware acceptance. Diagnostic
builds can capture bounded referenced DDR regions and raw before/after BRAM
using two intrusive no-present observer jobs. These captures are correctness
evidence, never frame-rate measurements. Release builds omit capture code.

See [the architecture and acceptance gates](../docs/unified-fpga-renderer.md)
and [operation coverage](../docs/unified-renderer-coverage.md). Tests distinguish
defined Q31.32 arithmetic from arbitrary floating-point/native equivalence;
neither source inventory nor an FPGA-only command count proves complete-game
correctness or speed. No game data or MiSTer framework files are included.

## Capability-gated offscreen lighting rendering

Patch 52, `butterscotch-offscreen-rendering.patch`, enables offscreen rendering
by default only when initialization has probed the companion FPGA floor-tint
capability. `AM2R_GPU_OFFSCREEN=0` forces it off; `AM2R_GPU_OFFSCREEN=1` explicitly
enables it, including on older RBFs for fallback testing. Other explicit values
disable it. With the setting absent, older RBFs keep the CPU path without
recording journals. Non-MiSTer initialization is unchanged.

It records bounded operations on 512x256
lighting surfaces and can generate their proved 320x240 visible crop as an
FPGA prepass. The complete logical surface remains available through exact
CPU replay before unsupported drawing, copies, pixel reads, and logical
save-state serialization. Surface replacement and restore discard stale
journals; static atlas sources retain immutable ownership when reusing a
previously dynamic allocation.

Prepasses run after deferred prior-frame exports and before the ordinary
application clear. Their exports have separate double-buffered storage,
revision checks, transactional failure, and a protected command prefix that
screen optimizers cannot discard. A constant solid prefix is folded into the
prepass clear with the original channel arithmetic. Texture sampling proves
every CPU-selected Y row fits a fixed-point interval, with exact row runs as
the fallback; it does not approximate endpoint rounding.

Arbitrary tint requires the companion core-local FPGA floor-tint capability.
The runtime probes that capability before setting the descriptor flag, while
explicitly opted-in older RBFs retain binary-tint eligibility and exact CPU
fallback. The matching
RTL is tracked separately in `rtl/am2r_gpu.sv`; this runtime patch does not
modify the MiSTer framework. Diagnostics builds can capture an independently
materialized CPU lighting crop beside the hardware export so command replay
does not substitute for CPU/GPU pixel comparison. Those captures perturb
timing and are not performance measurements.

Focused regressions are `tests/renderer/test_offscreen_contract.py`,
`tests/renderer/offscreen_backend_test.py`,
`tests/renderer/test_offscreen_journal.py`, and
`tests/runtime/audit_offscreen_lifecycle.py`. Hardware performance, matching
RBF identity, captured pixels, and save/load acceptance must be checked
separately for every distributed build. The journal regression also checks the
default selector and verifies that old-RBF defaults and forced-off settings
allocate no journal and emit no export.
All 52 patches were clean-applied to a fresh checkout of the pinned base on
2026-09-25; all 418 reconstructed files matched the working source after line
ending normalization, with no missing or extra implementation files.

## Cropped-upload gap measurement and bounded split experiment

Patch 50, `butterscotch-crop-gap-diagnostics.patch`, adds separately requested
read-only measurements of each selected CPU shadow/source pair before upload.
It requires a diagnostics build, `AM2R_CROP_GAPS=1`, and the existing finite
timing request. Bounded records are flushed to `render-crop.csv`, with explicit
overflow counts, existing one-span geometry, true changed-pixel bytes, and
hypothetical split-byte/call totals. It does not change uploads, GPU memory
ownership, or barriers. Normal release builds contain no hook. See
[the diagnostic workflow](../docs/render-diagnostics.md) for timing caveats.

Patch 51, `butterscotch-crop-split-upload.patch`, is the subsequent upload
experiment. It retains the existing exact first/last row bounds, but skips
interior pixel-aligned unchanged runs of at least 64 bytes. Equal 32-byte
probes use the existing NEON helper; only candidate gap edges are refined
wordwise. At most eight target copies per row are emitted; the final copy
coalesces any remaining spans. The old CPU shadow remains intact until all
segment decisions are finished, then receives the original complete span.
No FPGA DDR reads, texture allocation changes, command reordering, ownership
changes, or barrier changes are introduced.

The extracted-production regression verifies exact full shadow/target pixels,
guards, source immutability, ordered pixel-aligned writes, partial tails,
threshold boundaries, copy caps, and repeated/alternating buffer contents.
Uploaded bytes must not exceed the original one-span algorithm; they need
not equal it. Cached-RAM microbenchmarks are not MiSTer DDR performance
evidence. Hardware A/B and captured pixel validation determine whether the
candidate is acceptable; this patch description does not itself claim a
frame-rate improvement. The diagnostic `gap64` prediction merges gaps of
exactly 64 bytes whereas this candidate skips them, and its eight-copy cap
may coalesce later gaps, so prediction and actual copy totals can differ.

## Complete HUD CRT inset

Patch 54, `butterscotch-crt-ui-composition.patch`, moves the exact verified
AM2R 1.1 `oControl.gui_surface` once at final composition, before unified or
legacy raster dispatch. Its text remains at native coordinates inside the
surface, so numbers, tanks, icons and minimap stay aligned. Live owner lookup
handles recreated and restored surface IDs without relying on alpha bounds.
The old legacy-only inset is removed; sparse upload cropping now intersects
translated content bounds with the existing clip rather than subtracting from
an already-clipped full-frame rectangle. Separate title overlays retain their
existing behavior. Game data, save formats, FPGA and framework are unchanged.

Run `python tests/renderer/test_crt_ui_composition.py`. Production-hook tests
cover both dispatch paths, restored/full bounds, live option changes, identity
refusals and translated sparse clipping; a mutation witness rejects the old
bottom-edge truncation. Hardware acceptance is separate. This remains a
shared-image adjustment, not analog-only output; see
[the unimplemented isolation proposal](../docs/crt-ui-analog-isolation.md).

## Exact packed-mask channel selection

`butterscotch-packed-mask-unroll.patch` explicitly selects all four channels
from each bounded half-scale packed load, with one direction branch. This
removes the Cortex-A9 build's inner channel loop and packed-channel stack
roundtrip while preserving the selected texels, all four tint/blend channels,
clamps, scalar tails, unit-step helper and scaled self-surface ordering.
It does not include the separately investigated neutral-tint specialization.

Run `python tests/renderer/test_subtractive_blend.py`. Supplying
`--baseline-source path/to/preserved/sw_renderer.c` also compares the host
scalar and NEON-model paths against the previous implementation. Add
`--arm-output path/to/arm-test` to build the static ARM differential harness;
its `--regression-only` and `--benchmark-only` modes separate correctness and
span timing. Actual gameplay remains a separate hardware validation.

## Exact scaled-light and upload acceleration

`butterscotch-scaled-light-upload.patch` vectorizes scaled inverse-source-color
mask spans without changing signed 16.16 sampling, edge clamping, separate tint
and blend rounding, or alpha-zero RGB behavior. Half-scale spans use bounded
packed loads; other scales gather the same samples. Scaled self-surface draws
retain scalar ordering. Unit-scale spans keep a separate packed implementation.
The cropped upload path finds the same first/last changed pixel using block/word
comparisons instead of redundant bytewise scans. Upload bytes, buffer ownership,
completion waits and publication barriers are unchanged. No game data, shader
semantics, GPU commands, FPGA clock, or framework files are altered.

Run `python tests/renderer/test_subtractive_blend.py` and
`python tests/renderer/crop_upload_test.py`. Both can build standalone ARM tests;
their ordinary-memory benchmarks do not replace real gameplay frame-pacing tests.

## Opt-in rendering diagnostics

`butterscotch-render-diagnostics.patch` adds a bounded, buffered main-thread
timing recorder and synchronized single-GPU-job capture. Build with
`-RenderDiagnostics` only for QA; the default build compiles out the timers,
request-file polling, capture I/O and storage. No GPU opcodes, game logic,
blending arithmetic, `sys/` files or kernel behavior change. Captured pixels
stay outside the public repository. See [the diagnostics workflow](../docs/render-diagnostics.md)
for activation, identity binding, completeness checks and independent
reference/RTL replay. Pixel capture deliberately pauses gameplay and cannot
serve as a frame-rate benchmark.

## Native GameMaker lighting blend

`butterscotch-native-inverse-source-blend.patch` corrects the software
`bm_subtract` implementation to GameMaker 1.x's ZERO / INV_SRC_COLOR factors:
each RGBA channel is `destination * (255 - source) / 255`. RGB is not weighted
by source alpha. The generic, solid-fill, scalar-span, and NEON-span paths use
that operation, including colored pixels with zero alpha when alpha testing is
disabled. The matching core-local FPGA correction is in `rtl/am2r_gpu.sv`;
the runner and RBF should be updated together. No framework or game data is
changed. Run `python tests/renderer/test_subtractive_blend.py` and the GPU RTL
test for the arithmetic and command-path regressions. Native comparison and
hardware evidence are recorded in the September 25 lighting reports.

`butterscotch-authored-light-step.patch` removes the incorrect assumption that
End Step always rebuilds AM2R's light surface. The authored End Step only
initializes a missing surface; Normal Step updates an existing one. All light
events now run through the original bytecode. Water and sand optimizations
are unchanged. `tests/runtime/audit_am2r_native_lighting.py` prevents this
interception from returning.

`butterscotch-neon-solid-subtract.patch` accelerates the authored lighting
rectangle with eight-pixel NEON spans and exact 16-bit product/divide-by-255
arithmetic. It changes only the software solid-subtract implementation, not
the lighting event sequence, target size, rectangle bounds, or other blends.
The scalar fallback and every RGBA factor retain the same formula, including
alpha-zero sources. The compiled renderer regression covers all 65,536
channel pairs, 20,000 randomized solid spans with alignment/tail canaries,
and the full 331x251 mask rectangle inside its 512x256 surface. Hardware
performance acceptance is separate from these arithmetic checks.

The retired whole-process checkpoint path used DMTCP 3.2.0 and remains
reproducible for comparison or rollback. It is not used by the active logical
save-state path. Apply the MiSTer ARMv7 portability patch to a clean DMTCP
checkout at commit
`bc38d1a3bdfca87905f1a3adfada1e63d64042e5`:

```sh
git apply ../../patches/dmtcp-armv7-mister.patch
```

| Patch | SHA-256 |
| --- | --- |
| `dmtcp-armv7-mister.patch` | `b3c416bd2142602749fba31482129dcd4828b07afac5da034d6526360728fc44` |

The patch applies cleanly and passes `git diff --check`. The distributed
runtime bundle includes the matching ARM binaries and license texts. Its file
plugin uses the explicit 64-bit metadata ABI so 32-bit MiSTer builds can open
ordinary saves stored on CIFS servers with inode numbers wider than `ino_t`.
The plugin link also omits an embedded second C++ runtime; it resolves against
the packaged `libc++_am2r.so`, matching the MiSTer runtime ABI.
