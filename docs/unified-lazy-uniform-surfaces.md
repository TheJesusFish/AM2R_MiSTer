# Lazy uniform surfaces

Status: the lazy-uniform implementation is exercised in isolated USB-1
unified-renderer QA, September 26, 2026. The follow-on conservative background
rectangle exports below pass host tests and await hardware acceptance. This is
not a claim of final 60 FPS or a tester deployment. The implementation
changes surface storage/ordering only, not game code, blending, artwork,
command formats, the Main lifecycle fix, or `sys/`.

The implementation is in
`third_party/Butterscotch/src/backends/mister_surfaces_impl.h`, carried by the
tracked [unified-renderer patch](../patches/butterscotch-unified-renderer.patch).
Its checked source SHA-256 is
`24f8db5595e1c7e83b7a259c4527f3a90c033cf6e269f59a54e5cbe6e15bf467`.
No public manager API or save-state format changed. Unified rendering remains
an opt-in QA path; the legacy renderer is not replaced by this change alone.

## Captured opportunity

The diagnostic job `job-40000-16153-1` is room 166, game frame 32013, with
222 commands. Its nine raw stores write 1,769,472 bytes and its one raw load
reads 307,200 bytes. This is an intrusive diagnostic capture, not a frame-time
measurement. The following command indexes are zero-based:

| Commands | Contents and dependency | Assessment |
| --- | --- | --- |
| 0 | Store the prior host target to `0x2404b000` | No command in this job reads those bytes before store 113 overwrites them; possible separate dead-store optimization, not part of this implementation. |
| 44 | Store the transparent HUD to `0x24096000` | Required by the final HUD blend at 220. |
| 86, 101, 104, 107 | Store all four tiles of the 512×256 light target | The composition at 216 samples the first 320×240 tile; the other tiles preserve the full logical surface for arbitrary later reads/copies/saves. Dropping them is not equivalent. |
| 111 | Store a 64×64 target | Required by the scene draw at 118. |
| 112, 113, 218 | Clear the host to opaque black, store it, then reload it | The host has remained a known constant. Keep the clear as metadata and emit it immediately before its first real draw. |
| 217, 219 | Store the completed application target, then blend it onto the host | This is an explicit blended surface draw, not the automatic raw-copy path. Do not substitute a raw copy merely because this capture happens to be opaque. |
| 220, 221 | Blend HUD, present | Preserve exactly. |

For the known-black host sequence, the manager replaces clear 112 → store 113
→ load 218 with one equivalent clear before the first host draw. The later
real-device capture below confirms **614,400 transfer bytes removed**, without
eliminating or changing either final blend. This does not by itself establish
a millisecond saving or 60 FPS. Command 0 is explicitly excluded from the saving.

The captured application target does contain alpha 255 in every pixel, but
that observation is not a general proof for other frames. No dynamic readback,
per-pixel CPU opacity scan, effect name, room ID, or special lighting exemption
is used to turn draw 219 into a raw copy.

## Real-device capture and independent replay

The later `job-40000-43325-1` capture is room 166, game frame 27333, with 220
commands. Its command-file SHA-256 is
`3cb4c21282be8817ac9b0832ca3d79e25ba487ada19f6f0098944c461472b0fe`.
Independent scalar replay and current RTL replay under two DDR schedules
match the captured framebuffer and all three raw RGBA export ranges exactly:
zero differing pixels, no masked comparison region, and raw alpha included in
the export comparisons. The complete 307,200-byte initial BRAM image is captured,
not inferred from black or substituted from the native RGB output.

| Target transfer | Earlier job 16153 | Later job 43325 |
| --- | ---: | ---: |
| Raw STORE descriptors | 9 | 8 |
| Raw STORE bytes | 1,769,472 | 1,462,272 |
| Raw LOAD descriptors | 1 | 0 |
| Raw LOAD bytes | 307,200 | 0 |
| Combined target-transfer bytes | 2,076,672 | 1,462,272 |

This is a 614,400-byte / 29.59% reduction in these target transfers, **not** in
all GPU traffic or frame time. Texture sampling, descriptor traffic, and native
publication are outside this accounting. The surviving stores preserve three
independent 320×240 targets, the entire four-tile 512×256 target, and a 64×64
target. No independent surface version was replaced by a blended/lossy copy.

The replays use RTL SHA-256
`fe70f73b2e491bead0973c9457e256a8b954012dcdb3e4c1fc106131efddf8eb`.
Their GPU operation counts are 977,230 and 1,112,365 cycles under DDR
latency/stall/gap schedules 2/11/0 and 5/7/3. These are simulation results, not
release hardware frame times. The two captured jobs are different animation
frames: axis destination work grew by 954 pixels, and the earlier timing model
used different RTL. Their cycle difference therefore cannot isolate this
manager optimization. The capture itself declares two intrusive observer fences
and `perturbs_timing=true`.

Raw evidence is retained locally under
`data/work/unified-renderer-20260925/capture-audit/UNIFORM-FRAME-REPLAY.md`, with
the capture, scalar comparison, and two RTL comparison directories recorded
there. Agreement between hardware, scalar, and RTL validates the submitted
commands; it is **not** native Windows/GameMaker equivalence. Native presentation
does not retain the logical surfaces' raw alpha, so a matching visible/native
frame alone cannot prove raw-RGBA or future-compositing correctness.

## Representation and invariants

Each persistent surface now has a bounded uniform representation:

- `uniform_valid`: the base version before the selected target's pending draw
  batch is exactly one raw RGBA value over its full width and height. With no
  pending batch, this is the current logical version.
- `uniform_rgba`: that exact value, including alpha zero with nonzero RGB.
- `uniform_ddr_current`: the existing canonical allocation contains the same
  version, or an ordered store of that version has already been queued.

The manager retains the existing key/generation, dimensions, stride, revision,
CPU-shadow, resident-BRAM, and allocation ownership rules. No additional texture-size
allocation is needed. An unknown surface continues to use the existing path.

The representation refers to a logical version, not a claim that either its
CPU shadow or its canonical DDR bytes are current. A full accepted clear still
advances the revision and invalidates the CPU shadow immediately. Never return
a stale canonical address just because the pixels can be reconstructed later.

At most the selected target has a pending draw batch. The implementation keeps
the record's uniform base unchanged until the entire batch is encoded. Every
needed tile gets its exact clear seed before its nonconstant operations; the
uniform fact is cleared only after all tile commands are accepted. Revision
advancement on draw acceptance does not incorrectly make this base metadata a
claim about the deferred draw result.

## Implemented boundary

The implementation is deliberately narrow:

1. At `gpuSurfaceFlushBatch`, a batch containing only a full clear (including
   the existing proven constant-fill fold) becomes uniform metadata. It does
   not acquire BRAM, spill an unrelated resident target, emit pixels, or wait.
   If the cleared target itself owned dirty BRAM, that obsolete version can be
   invalidated; previously queued commands remain in order.
2. `gpuSurfaceLoad` can reconstruct a uniform target with opcode 1 or bounded
   opcode 14 instead of a DDR load. When the following real draw executes,
   normal residency/dirty tracking resumes and the uniform fact is cleared.
   A first partial draw on a large target initializes/exports untouched tiles
   too if the uniform canonical allocation is not yet current. If it is already
   current, untouched tiles need no transfer. This distinction prevents stale
   borders without making every later one-pixel edit export a full surface.
3. A target switch may retain a uniform surface as metadata rather than
   storing its reconstructible constant pixels. This is not permission to drop
   stores of nonuniform targets or externally observable earlier versions.
4. `surfaceTexture`, partial-copy sources, self-copy snapshots, and CPU
   readback must first materialize a uniform version into canonical DDR when
   required. Emit normal ordered tile clears/stores; do not fill a full
   surface on the CPU. Any selected batch that would be displaced by this
   materialization must first be sealed and its nonuniform residency preserved.
5. True CPU barriers keep their existing completion requirements. An
   ordering-only flush remains ordering-only. Uniform materialization may
   drain on ordinary bounded queue exhaustion, not add unconditional waits.

No constants are inferred from arbitrary texturing, partial draws,
triangles, gradients, or custom blends. Those operations simply materialize
their uniform base and use the existing exact GPU commands. Extending exact
one-color folding across a batch boundary is not implemented or needed for the
captured host-clear saving.

## Ordering and lifecycle requirements

- A previously returned canonical address is a dependency. Target selection
  and the existing batch-order rules must seal its consumers before a later
  clear or CPU edit can replace the source's version. Do not overwrite DDR on
  the CPU to implement lazy clear.
- Materialization is queued in the same command stream as its consumers.
  Mark `uniform_ddr_current` only after all required store descriptors have
  been accepted; submission failure remains fatal, not a stale-CPU fallback.
- `surfaceReadback`/checkpoint preparation materialize when needed and await
  completion before copying exact raw RGBA. No change to snapshot formats is
  required: restore can conservatively start as a normal DDR-backed surface.
- `surfaceCpuWritten` invalidates the uniform fact unless a separate complete
  proof exists. No CPU scanning is introduced to seek such a proof.
- Release, generation reuse, allocation failure, disable, and reset cannot
  retain old uniform metadata. Pending older versions and consumers still obey
  the established retirement fence.
- Full copies from uniform sources currently use normal canonical
  materialization, not a new metadata-copy shortcut. The earlier small-target
  resident full-copy shortcut applies only to nonuniform sources. Partial
  copies materialize the destination's base before overwriting a rectangle;
  self/overlapping copies retain immutable snapshot semantics.
- When presenting a uniform host without any draws, reconstruct it in BRAM
  before END_PRESENT. Never publish a previous frame's resident pixels.
- Old-capability cores must retain the existing bounded correctness path:
  opcode 1 may initialize a partial workspace tile, but only the target's
  owned rectangle may be exported. Do not require opcode 14 for correctness.

## Verification and regression coverage

The initial lazy-uniform [production-manager fixture](../tests/renderer/unified_surface_backend_regression.c)
and [test driver](../tests/renderer/test_unified_surface_backend.py) passed on the
host and as an ordinary-RAM ARM executable on USB-1: **444 submissions, 443
fences, 1,020 completion-wait calls, one explicit publication**, and 164 immutable
generic-packet tile transforms. Wait-call counts are fixture coverage, not
blocked waits or per-frame costs. The independent scalar clear/fill comparison
also passes 108 vectors across old/new bounded-clear capabilities. Two
independent source reviews found no blocking defect.

The ordinary-RAM executor validates ownership/ordering and its implemented
pixel operations; it is not real FPGA execution, and its generic-packet tests
are plane/state/immutability proof, not a separate custom-blend pixel oracle.
The captured scalar/RTL comparisons above provide the real-job pixel evidence.
The manager regression matrix includes:

1. `clear host → draw other target → draw host` emits no constant-host DDR
   round trip and retains an unrelated dirty target's required store. The
   real-job replay separately checks the scene/HUD exports byte-for-byte.
2. Uniform clear colors include alpha 0, 1, 127, 254, 255 and nonzero RGB under
   zero alpha. Tests exercise normal, additive, inverse-source/subtractive, and
   generic first draws over that base, with the generic test limitation above.
3. Dimensions include 1×1, 319×239, 320×240, 321×241, 512×256, and 639×479;
   tests cover odd lanes, stride padding, partial tiles, and old/new capabilities.
4. A clear-only target is sampled before any subsequent draw: canonical
   materialization occurs once, before its consumer; repeat sampling does not
   repeat it. Sampling another source must not discard a dirty unrelated target.
5. Draw using source version A, then clear source to B, then draw using B:
   the first consumer sees A, the second sees B, including across bank overflow.
6. Same-target sampling and all four overlapping-copy directions preserve an
   immutable snapshot. Exact no-op self-copy remains a no-op.
7. Readback, checkpoint preparation, restore, clear-after-restore, and CPU
   modification preserve raw pixels and authority. The fixture deliberately
   poisons canonical DDR between checkpoint preparation and restore.
8. Clear-only presentation, no-present fences, target release/reuse, reset,
   disable/fallback, and allocation failure never expose stale uniform state.
9. Failure while queuing a materialization does not publish a successful DDR
   version or advance ownership optimistically. Failure is injected before
   each CLEAR and each STORE across all four tiles, not only at tile boundaries.
10. Capacity exhaustion during clear reconstruction/consumer batching retains
    command order, generic packet immutability, and no duplicate execution.

A deferred constant target's unused canonical DDR bytes may legitimately
differ until materialization; that representation change is not a pixel match
or mismatch. Capture replay checks every store the new stream actually issues.
The two animation captures do not establish identical pre/post visible frames,
nor does this bounded fixture establish full-game coverage. Final acceptance
still requires normal, uncaptured USB-1 cadence, strict zero-CPU-raster evidence,
save/load/reset/warm-relaunch checks, and native-game visual comparison where
behavior is disputed. Do not infer final 60 FPS from transfer counts or
simulated cycle estimates alone.

## Conservative background rectangle exports

The next manager-only optimization is implemented and host-tested; it has not
yet been measured or accepted on hardware. The command ABI and blending remain
unchanged. It does not recognize the HUD, a room, an effect, or a game asset.

Each surface now has two independent bounded facts:

- `background`: the sealed logical version is exactly one raw RGBA background
  outside a conservative rectangle containing every possible non-background
  pixel. This is the base before the selected target's pending batch.
- `canonical_background`: the same kind of fact for the last fully accepted
  canonical DDR version. It can describe an older version than `background`.

Only a full clear establishes a known background. Subsequent draw rectangles
are clipped to the logical surface and unioned conservatively; no pixel scan,
opacity inference, interval list, or CPU rendering is introduced. Arbitrary
textured/affine/generic operations may enlarge the rectangle to the full target.
CPU writes, copies, restore, and unknown initial pixels conservatively discard
the relevant proof. Generation reuse, release, reset, and disable cannot retain
an earlier allocation's proof.

A STORE may cover only the union of the logical and canonical dirty rectangles
**if both backgrounds are known and all 32 raw RGBA bits match**. That union
erases old glyphs/draws that moved or disappeared, as well as storing new pixels.
Outside it, both versions are already proved byte-identical. A changed background
(including alpha-only changes or RGB changes under alpha zero), unknown state,
or full bounding rectangle uses the full export. An empty union needs no store.

The desired rectangle is intersected with each BRAM tile. Existing opcode 11
receives the canonical first-pixel address, original surface stride, and the
correct nonzero tile-local X/Y origin. A large batch publishes its new canonical
fact only after every necessary tile STORE descriptor is accepted. Failure is
fatal and cannot advertise a partially completed canonical version. Clear-only
lazy materialization uses the same union rule to erase old non-background pixels
before declaring its constant allocation current. Every source lookup/readback
still sees a complete canonical raw-RGBA surface, not a sparse texture with holes.

In the ordinary-RAM fixture, a general 320×240 target cleared to a fixed raw
background and drawn only in the first 32 rows exports 307,200 bytes on first
initialization, then 40,960 on repetition: **266,240 bytes avoided**. This
reproduces the draw bounds in capture 43325 but is a synthetic transfer count,
not a new hardware measurement or a promise that all subsequent frames have
those bounds. Old drawing near the bottom can expand the erase union again.

The expanded host fixture passes **918 submissions / 917 fences / 2,666 wait
calls**, 164 immutable generic transforms and the same 108 independent scalar
clear/fill vectors. Tests cover old/new bounded-clear capabilities; odd and
multi-tile targets; moving/disappearing/negative-origin drawings; alpha/RGB-only
background changes; unknown CPU pixels; ordered old/new source consumers;
self-source and overlapping copies; logical/descriptor queue overflow; failure
at the small-target spill and each large tile's STORE; checkpoint DDR poisoning;
and generation reuse. The ARM fixture is built separately for USB-1 execution;
building it is not a hardware test.

The lifecycle fixture also executes 180 create/copy/overlap/release cycles with
varying dimensions and 17 checkpoint restores. Its reserved pool high-water
mark stays at 8,548,224 bytes after the first complete size cycle, with no live
handle growth. This is bounded reuse evidence, not proof of general allocator
reclamation: the existing bump allocator does not reclaim an old smaller block
when a surface allocation or self-copy snapshot grows. A succession of distinct
larger sizes can still consume additional reserved space. No caches were cleared
to obtain the result, and this experimental manager is not an explanation for
the user's progressive-slowdown report on the earlier released runtime.

## Explicitly deferred

Job-local dead-store elimination needs a separate proof of every intervening
source read, native publication, diagnostic export/observer, and CPU-visible
fence. It must not be bundled into lazy uniform targets. The initial store in
this capture is evidence for investigation, not blanket permission to delete
stores or alter capture semantics.
