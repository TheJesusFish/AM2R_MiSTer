# GPU allocation lifecycle audit

September 26, 2026. This is an allocation/ownership audit, not an attribution of
the reported progressive slowdown. The fixture executes extracted production
allocation/upload functions and the actual unified target manager in ordinary
RAM. Completion/submission succeeds in a mock; it does not execute GPU pixels
or measure MiSTer performance. No game data is part of the fixture.

Status: the narrow fixes and bounded reusable arena are implemented and pass
host tests. MiSTer acceptance of the arena remains a separate step. No RTL,
command ABI, game behavior, artwork or `sys/` changes are part of this work.

## Reproductions

Run `python tests/renderer/test_gpu_allocator_churn.py`. To reproduce the two
subsequently fixed bugs against the preserved pre-change source, run the same
driver with `--legacy-source <path-to-old-mister.c> --expect-legacy-leaks`.
The manager comes from the selected source's sibling header when present;
`--manager-source` can override it. A historical source without a manager header
executes only the legacy cases. The preserved `reconstructed53-resource-leak-fix`
tree contains the narrow fixes but the old monotonic manager for before/after tests.
`--arm-output <path>` compiles an ordinary-RAM ARM fixture; it does not execute it.

The shared pool is 134,217,728 bytes. The following tests deliberately hold only
one user allocation live at a time, except the explicit pinned snapshot source.
They release each generation before creating the next; these are not tests of
legitimately excessive live game data.

| Workload | Observed result before general reclamation |
| --- | --- |
| 1,000 same-size immutable generations | One record; 4,096 bytes reserved, stable. |
| 1,000 same-size dynamic generations | One record; 8,192 bytes reserved, stable. |
| Released immutable sizes 4,096 + 128n | 128-record table exhausted; 1,564,672 reserved; peak live 20,352 bytes. |
| Released dynamic sizes 4,096 + 128n | 128-record table exhausted; 3,129,344 reserved; peak live 40,704 bytes. |
| Released dynamic sizes 1 MiB + 128n | Pool exhausted after 63 generations; peak live 2,113,024 bytes. |
| Manager sizes 128/64/256/64/128/256 by 512, 1,000 generations | Stable after first shape cycle, at 786,432 bytes. |
| Manager widths 64 through 768, height 512, release each | Failure after 451 generations; 133,763,072 reserved; 1,052,672 retained; 132,710,400 lost to replaced capacities. |
| Self-snapshot widths 64 through 768 by 512 | Failure after 447 sizes; 133,758,976 reserved including pinned 2 MiB source; current snapshot 1,044,480; 130,617,344 lost to replaced capacities. |

The pre-change current source and preserved `reconstructed52-default` source
produce identical legacy churn counts. Manager growing-size results are current
manager evidence, not a claim that the historical renderer had that manager.
Stride rounding means two consecutive odd/even widths can share one capacity.

## Narrow fixes implemented

Two independent bugs were fixed before attempting general reclamation:

1. A released double-buffer texture can be reused as immutable. Its retained
   second allocation remains valid capacity, but a later mutable upload used to
   overwrite `physical[1]` with another allocation. Both CPU dynamic upload and
   GPU-write preparation now allocate that second copy only when absent.
2. New dynamic, sparse, cropped and GPU-write textures used two separate pool
   reservations. When only the first fit, creation failed but consumed that
   memory without an owning record. `reserveGpuTexturePair` now reserves both
   padded copies atomically, publishing neither output on failure.

The actual helper tests cover both immutable-to-mutable entry points. A thousand
same-size generations now reserve 8,192 bytes with no orphaned capacity, compared
with 4,100,096 reserved / 4,091,904 orphaned before the fix. They also verify that
the earlier immutable pixel version remains unchanged. All four constructors
consume zero bytes on a near-full-pool failure, versus 128 bytes each previously.
Pair tests cover 1/127/128/129/255/256/257-byte requests, alignment, padding,
overflow, unchanged failure outputs, and pool boundary failure. Successful
addresses and pixel copying remain unchanged; no rendering equation changed.

The existing manager fixture remains PASS (918 submissions, 917 fences, 2,666
wait calls); 108 independent clear-fold vectors, offscreen transaction tests,
13,574 crop-span cases and 400 repeated/alternating crop sequences also pass.
Those two narrow changes alone do **not** fix distinct-size record exhaustion,
manager growth, or snapshot growth. The arena work below addresses those cases.
Raising a table limit again would only defer the failures.

## Source ownership findings

Before the arena change, `reserveGpuTexture` was a 128-byte aligned/padded monotonic allocator.
There is no general free operation. `MisterGpuTexture.bytes` currently serves as
both logical extent and assumed allocation size, and released records can only
be reused at exactly that size. Released incompatible records retain both DDR
and CPU shadow memory and continue occupying table entries.

The manager already reuses a released capacity large enough for a new target.
If none fits, it replaces a free record's address and forgets the old allocation.
The single overlap-snapshot address behaves similarly when it grows. Existing
finite-shape lifecycle tests correctly demonstrate bounded reuse, but cannot
prove bounded behavior under increasing-size churn; the new tests fill that gap.

The pool also contains permanent/lazily permanent allocations: command packet
banks, offscreen crop banks, water tables, framebuffer staging, and derived
cache textures. These must not accidentally become free while their owning
subsystems retain addresses. Static atlases remain live until explicitly released.

## Implemented reusable arena

The pure allocator lives in `src/backends/mister_gpu_arena_impl.h`; its call-site
ownership integration is in `mister.c` and `mister_surfaces_impl.h`. The contract is:

- Use a bounded 1,024-segment table with coalescing free extents. All
  blocks remain 128-byte aligned and padded through the last cache line. Prefer
  deterministic smallest-fitting free storage; keep a high-water extent for
  diagnostics and existing pool-address bounds.
  Allocation and source validation scan only the current high used metadata
  index, not all 1,024 slots; coalescing trims trailing unused metadata.
- Give each allocation an explicit capacity, allocation identity and owner
  lifetime. Track logical image bytes separately from DDR and shadow capacity.
  Free or resize only released objects. Do not move live pixels, evict a live
  atlas, discard a valid surface, compact live memory, or grow beyond the pool.
  Atomic texture pairs are one parent allocation with two logical copy bases;
  immutable-to-dynamic conversion can instead own two independent allocations.
  Retirement must resolve and free unique parents, never free the second pair
  address as though it were an independent whole allocation.
- Pin fixed packet/offscreen/water/frame allocations. Explicitly
  recycle released texture storage, replaced free manager capacities, and old
  self-snapshot storage. Free-list metadata itself must have a hard bound and
  defined no-loss failure behavior.
- Keep released crop shadows/cache content while capacity fits, preserving the
  existing sparse upload speedup. A logical-size change invalidates size-sensitive
  revision/opacity/shadow facts. Repurpose a retired record slot rather than
  monotonically appending records forever.
- Make the low-level allocator pure: it may consume a safe free extent or grow
  the arena, but must **never recursively flush or reclaim**. Reclamation occurs
  at explicit API boundaries before accepting a new operation. This prevents a
  pressure path from re-entering the logical batch it is currently translating.
- Before reusing storage, retire every relevant logical batch, unsubmitted
  descriptor, deferred export and submitted job. In unified mode, the bounded no-present drain
  provides that ordering. A completed submitted sequence alone does not prove
  that the current command bank no longer references the allocation.
- Old-RBF legacy mode never publishes a partially drawn frame just to free
  memory. Explicit pressure retirement refuses an open frame, deferred exports,
  active offscreen recording, or an unclassified nonempty command list. Safe
  closed-frame retirement waits for the submitted job. Fitting-capacity reuse
  preserves the existing previous-frame quarantine and completion wait.
- A new job starts with invalid texture cache. CPU overwrites of a recycled
  address must therefore occur after the completed drain and before the next
  job. Preserve cache/derived-record generation invalidation and every explicit
  release cleanup; do not assume an address change alone invalidates caches.
- Allocation, CPU shadow allocation, metadata insertion and ownership transfer
  must be transactional. Fence or allocation failure must leave live objects,
  revisions, authoritative pixels and previously accepted descriptors intact.
  Genuine oversized live working sets still fail safely and explicitly.

Manager generic source interval checks now reject freed holes, not merely
addresses beyond the arena high-water mark. Cache-line padding belongs to its
parent allocation. Diagnostic telemetry separately reports high-water, currently
owned, free and largest-free bytes, so high-water growth is not mislabeled a leak.
The arena's CPU metadata participates in process checkpoints; it is not reset
when external DDR is remapped. Live authoritative pixels are republished by the
existing restore protocol.

## Arena results

The current actual-helper fixture completes all 4,096 increasing-size generations
for each of immutable, dynamic and approximately 1 MiB dynamic images. The
preserved source stopped at 128, 128 and 63 respectively. Optional released
texture caches can still occupy much of the fixed pool before pressure retirement;
high-water is historical extent, not outstanding ownership. No live allocations
are evicted to make these tests pass.

All 705 increasing manager target sizes now complete with a 1,572,864-byte
high-water/retained allocation instead of failure after 451 generations at
133,763,072 bytes. All 705 snapshot sizes complete at 3,670,016 bytes, including
the pinned 2,097,152-byte source, with no lost earlier snapshot capacity.

Additional actual-helper coverage passes:

- 6,000 random allocate/release operations with 64 bounded-live slots, verifying
  contents, nonoverlap, alignment, exact whole-pool accounting and coalescing;
  high-water remains below 4 MiB.
- Best-fit holes, full-pool and metadata exhaustion, unchanged failure metadata,
  cache-line padding, pair parent identity, invalid/interior/double retirement.
- All constructor malloc failure points, failed retirement fence, legacy
  open-frame/deferred-export/offscreen refusal and no forced presentation.
- Manager target/snapshot growth fence failure preserves the old allocation;
  generic source packets cannot read an interval after it becomes free.
- The complete manager pixel fixture passes 919 submissions / 918 fences /
  2,677 waits, including raw alpha, source mutation, overlap, Step ordering,
  software materialization, poisoned-DDR checkpoint restoration, and 180
  create/copy/release cycles. The latter stabilizes at 5,841,024 bytes rather than
  the previous 8,548,224. Arena metadata is unchanged across its 17 checkpoint
  restore checks. The independent 108 clear-fold vectors remain exact.

The isolated fixtures do not execute a complete game or prove a hardware speedup.
Fragmentation around genuinely live, immovable allocations and truly excessive
live data can still cause safe allocation failure. Legacy midframe pressure can
also decline reclamation; it does not use new no-present operations on old RBFs.

## DMTCP and `.fast` are distinct restore paths

The audit independently reproduced a pre-existing dormant-DMTCP bug: a released
sparse crop retained a CPU shadow, external DDR was changed, and next-frame reuse
could skip uploading unchanged rows. `gpuInvalidateReleasedTextureShadows` is now
called in `MisterGpu_afterSaveStateRestore`; the actual-helper regression poisons
both DDR copies and verifies full exact restoration. The preserved baseline
reproduces the stale pixels. Ordinary in-process crop reuse keeps its optimization.

This is **not** claimed as a `.fast` bug fix. `am2r_fast_state.c` calls
`SWRenderer_fastStateResetSurfaces` and then `SWRenderer_fastStateRestoreSurface`:
it releases old handles and reconstructs CPU surfaces from saved RGBA in the same
process, without replacing external DDR independently of shadow/arena metadata.
It does not call the DMTCP restore helper. Separate backend tests cover that
release/recreate contract with changed saved pixels, reused revision numbers and
the next alternate-buffer update. A manager pixel regression also queues a later
state, resets handles, restores saved raw RGBA and verifies a fresh generation.
Those are restore-lifecycle tests, not a full `.fast` file/serializer test.

## Hardware and follow-on acceptance

- Thousands of varying-size, dynamic/immutable, cropped, target, copy and
  snapshot generations with bounded peak live demand: bounded pool/record usage.
- Free extent splitting/coalescing, best fit, exact cache padding, genuine OOM,
  metadata exhaustion, double retirement, invalid ranges, and rollback at every
  allocation/malloc/fence failure point.
- Reused CPU pointer and reused DDR address with new generations, including
  cached opacity, sparse shadows and derived caches; stale handles rejected.
- Queued source then release/resize, logical batch source references, active
  submitted bank plus unsubmitted bank, and self-copy snapshots. No overwrite
  before all references retire; no duplicate commands or hidden presentation.
- Odd strides, large tiled targets, transparent nonzero RGB, disappeared draws,
  surface readback, software fallback, full/partial copy, save/restore and poisoned
  external DDR. Existing manager pixel fixtures must remain exact.
- Test legacy RBF capability behavior separately from unified no-present mode.
  Hardware soak and representative rooms remain required after host proofs.

Baseline fixture mode deliberately asserts historical exhaustion; current arena
mode asserts successful bounded progress. Neither result establishes the cause
of the user's observed slowdown without a corresponding real-session trace.
