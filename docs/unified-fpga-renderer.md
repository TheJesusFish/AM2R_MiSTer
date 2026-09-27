# Unified FPGA renderer

Status: hardware-validated tester candidate on `unified-fpga-renderer`. This is the
user-approved replacement of effect-specific acceleration with a general
normal pixel-rendering backend. It is **deployed to USB-1 and M:** with a matched
new Main, RBF and stripped runtime; full-game user acceptance remains pending.

## Boundaries

The ARM continues to execute the GameMaker VM, draw-event code, transforms,
resource decoding, input, audio and file I/O. The FPGA owns normal rasterization,
texture sampling, blending, clears and surface copies. CPU pixel readback for
the game or save-state serialization is a synchronization operation, not a
reason to redraw the surface on the CPU.

Game data, artwork, gameplay, native scanout and `sys/` remain unchanged. The
software renderer stays available as the independent comparison/recovery path,
including support for old RBFs. A successful GPU command does not by itself prove
that all game drawing used the GPU: coverage must count every software pixel path.

The candidate now requests unified rendering by default and enables it only
after all required FPGA capabilities are negotiated. `AM2R_GPU_UNIFIED=0`
selects the legacy path; older RBFs retain that path automatically. Strict GPU
coverage remains a separate QA setting. The independent lighting-orphan cleanup
also defaults on, but only for the exact verified AM2R1.1 input and newly proven
owners; see [the lifetime audit](light-surface-ownership-audit.md). Neither
default alone is evidence of full-game compatibility. The measured acceptance
and deployment record is in the [progress report](../reports/unified-renderer-progress-2026-09-25.md).

The old runtime `d472d6c1` / RBF `8ce9fa07` remain as recoverable deployment
backups; the current matched set is runner`fc829dd5`, RBF`0c8f3e59`, and
required Main`dbd66fd5`. The dirty baseline was retained without a
commit in `data/work/unified-renderer-20260925/baseline-source.tar`, SHA-256
`11e80fcf3b6200e32b3c4b2952af6c40da82cc09a595943647ac81cd5d99a8dc`.
Creating a branch alone does not isolate uncommitted files; use that snapshot
and the existing 52-patch reconstruction for recovery, never a broad reset.

## Architecture contract

1. Each logical image has persistent full-size RGBA storage in the existing
   reserved DDR pool, with explicit dimensions, padded stride, generation and
   CPU/GPU ownership. Reused IDs and pointers must not inherit an older image.
2. The existing 320×240 block-RAM framebuffer remains the fast pixel workspace.
   Larger targets use tiles, preserving operation order within each tile.
   Partial tiles preserve all pixels outside their rectangles.
3. Consecutive draws to a target are batched. Surface dependencies, source
   mutations, readbacks, frees, resizes and capacity limits create explicit
   ordering boundaries. An overlapping copy snapshots the complete source
   before any destination tile is changed.
4. Main-image pixels, including alpha, must survive target switches. A target
   flush is not a display publication. Native video continues showing the last
   completed frame until the actual end-of-frame presentation.
   The renderer's ordinary `flush` closes an ordered batch; it is not a CPU
   completion wait. Readback, CPU mutation, snapshots and hardware detach use
   explicit completion barriers. Looking up an unrelated texture must not
   spill the current target merely because its pixels remain GPU-owned.
5. Step-event draws survive `beginFrame`. GPU writes invalidate the CPU copy
   immediately; CPU access waits for completion and reads the authoritative
   pixels. Save/restore uses the same path and never reads XRGB scanout as an
   RGBA substitute.
6. Screen-only optimizers may not cross target changes or perform CPU pixel
   work in unified mode. General operations replace special-case surface
   assumptions; measured reusable fast paths may remain behind the same API.

## Initial descriptor extension

Existing 64-byte descriptors and opcodes 0–9 retain their meaning.
The capability mask adds bit1 (rectangle transfers) and bit2 (no-present end),
in addition to bit0 (floor tint). New runtime code must check all required bits.

| Opcode | Meaning | Descriptor fields |
| --- | --- | --- |
| 10 | Load raw RGBA rectangle into BRAM | word0: opcode, width at16, height at32; word1: first DDR pixel address at0, row stride bytes at32; word2: local BRAM x at0, y at16 |
| 11 | Store raw RGBA rectangle from BRAM | Same fields as load |
| 12 | Complete without presenting | Only opcode; other fields zero |
| 14 | Replace a bounded BRAM rectangle with raw RGBA | word0: opcode, width at16, height at32; word1: RGBA low32; word2: local x at0, y at16; all other bits zero |

Transfer addresses are 4-byte aligned; stride is 8-byte aligned and at least
`width*4`. Rectangles fit the 320×240 workspace. Head/tail byte enables preserve
neighboring pixels for odd addresses/widths. Zero extents are no-ops. Runtime
validation proves the complete DDR range belongs to an allocation. RTL guards
local dimensions, alignment and stride. Writes invalidate stale source-cache
contents. Bus payloads remain stable under backpressure. Opcode12 publishes
completion/capabilities but changes neither native-frame count nor native buffer.

Opcode14 is separately gated by capability bit4. It preserves raw alpha and
all neighbouring pixels; it is not a blended fill. Invalid bounds/reserved
fields and zero extents perform no writes. Old bit0..3 cores continue to use
whole-workspace opcode1 clears. The host may combine a leading known clear
with a uniform full-target or full-tile fill using the existing exact blend
equations. Partial uncovered borders and all following draws remain intact.
Immutable opaque texture coverage may discard hidden tile-local prefixes,
but never prior dependencies, externally visible writes, affine state or
descriptor-indexed generic packets.

These are the surface-storage foundation, **not complete renderer coverage**.
Remaining primitive/state support is tracked separately in the coverage audit.

### General primitive packet

Capability bit3 adds opcode13. Its descriptor has the same width/height and
local BRAM x/y fields as a rectangle transfer; word1 is an 8-byte-aligned
pointer to an immutable 512-byte packet. All other descriptor fields are zero.
The runtime snapshots each draw's geometry/state and derives a separate packet
for each clipped tile using constant-size setup arithmetic, not CPU pixel loops.

| Packet qwords | Contents |
| --- | --- |
| 0 | Low32 magic `0x31504741` (AGP1); high32 flags bit0 textured, bit1 triangle edge test |
| 1 | Texture physical base low32, row stride bytes high32 |
| 2 | Texture width16/height16; blend mode8 at32; RGBA write mask4 at40; alpha reference8 at48; alpha-test/fog/blend enable bits56/57/58 |
| 3 | Four byte blend-factor IDs: source RGB, destination RGB, source alpha, destination alpha; fog RGBA high32 |
| 4–7 | Reserved zero |
| 8–16 | Three edge planes, each signed64 start/dx/dy |
| 17–22 | U and V planes, each signed64 start/dx/dy in texel units |
| 23–38 | R/G/B/A planes, each signed64 start/dx/dy/dxy in normalized colour units |
| 39–63 | Reserved zero |

All plane coefficients use signed Q31.32 at the first bounding-box pixel centre.
Triangle coverage requires all three edges nonnegative; triangle colour cross
terms are zero. Axis gradients use bilinear colour planes. UVs truncate toward
zero and clamp to source dimensions. Each colour is clamped to [0,1], multiplied
by its texture channel (255 for untextured draws), then floored. Named blend
modes0..5 match the renderer enum; mode6 means separate additive blend factors
with IDs1..11. Alpha test precedes constant fog replacement and blending; channel
masks apply last. Zero source alpha is not a universal discard: custom ONE/ONE,
minimum/maximum, blend-disabled replacement and inverse-colour subtraction need
their actual equations.

This fixed-point contract is independently modelled. It is not a claim of
bit-identical arbitrary IEEE floating-point software interpolation. Quantization,
edge coverage and texel-boundary differences require differential tests and
native visual comparison. Existing verified legacy fast paths retain their
explicit byte-tint rounding rules. No programmable shader support is claimed.

## Acceptance gates

- Independent reference/RTL pixels under multiple DDR stall/latency schedules,
  including raw alpha, odd dimensions, partial tiles, every supported blend,
  transformed/gradient primitives and source bounds.
- Surface lifecycle: A→B→A dependencies, cross-tile overlap in every direction,
  source mutation, free/reuse, resize, allocation/capacity failure and Step draws.
- No-present batches cannot modify native publication; one normal frame presents
  exactly once. FIFO/burst/memory range violations fail tests.
- Source patch reconstruction and sealed runtime/RBF artifacts; FPGA timing,
  resources and unchanged `sys/` verified before hardware deployment.
- Real save/load, corrupt-slot recovery, cross-exit restoration, and old-RBF
  recovery with original saves/checkpoints preserved.
- USB-1 supplied-state playback, title/game menus, map, HUD, water/lava,
  lighting, particles/projectiles, transitions and captures. Explicit fallback
  counters must show the coverage boundary; no silent unsupported draw dropping.
- Diagnostics-OFF performance and frame-pacing comparison against the same
  known-good installation. GPU-only placement is not a claim of locked 60 FPS.

No tester build should be described as unified/complete while a known normal
AM2R pixel operation is still silently handled by software.
