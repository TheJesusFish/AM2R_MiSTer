# Standalone synthetic GPU hardware checks

`tools/make_gpu_hardware_fixtures.py` creates nine synthetic-only bundles: generic
primitives/state, surface transfers, bounded replacement, shared axis streaming,
axis fallbacks, water/burst boundaries, scaled/mirrored cache gathering,
gather/STORE/LOAD ordering, and legacy affine sprites. No AM2R data or save is
included. The original six bundles retain their byte-identical baseline hashes;
new families are additional files, not replacements for the previous checks.

Generate with:

```text
python tools/make_gpu_hardware_fixtures.py --output data/work/gpu-hardware-fixtures-expanded
```

Build `tools/am2r_gpu_fixture.c` for ARM Linux with the project's existing ARM
compiler. The driver accepts `--validate bundle.a2gt ...` without opening a device.
`tests/renderer/test_gpu_hardware_fixtures.py` compiles and exercises that exact C
validator, including malformed bundles with recomputed integrity checksums.

## Hardware run contract

This is an isolated development tool, not a core or tester launcher. The QA
frontend must already own the correct loaded RBF and have stopped and drained the
game's GPU producer. Do not run beside another core, renderer, or fixture process.
The required GPU capability bits are `0x1f`.

Run the ARM executable as the QA frontend's alternate child, from the directory
containing the generated bundles:

```text
./am2r_gpu_fixture --run generic.a2gt transfer.a2gt bounded-clear.a2gt axis.a2gt axis-fallback.a2gt water.a2gt
./am2r_gpu_fixture --run gather.a2gt gather-ordering.a2gt affine.a2gt
```

The driver does not program the FPGA, mount storage, publish a displayed frame, or
map the native framebuffer. It maps only its fixed command/control regions and
declared allocations inside reserved GPU DDR. A fresh clear/fence probe verifies
the capability result. Each test initializes BRAM, exports raw RGBA with STORE,
then ends with END_NO_PRESENT. All declared bytes are compared, including untouched
pixels, raw alpha, immutable sources, packets, tables, and padding.

Successful lines include command/span/byte counts, mismatch count, completion
sequence, GPU cycles, bundle CRC32, actual FNV-1a, and `disarmed=1`. The manifest
contains SHA-256 hashes for the immutable input bundles. Hashes identify artifacts;
byte-for-byte comparison, not matching a hash alone, determines PASS.

On timeout the tool returns failure and explicitly leaves a still-busy mailbox
armed. It does not reset the FPGA or pretend outstanding DDR traffic drained.
The controlling QA frontend must treat this as a failed shutdown and stop further
tests. TERM/INT/HUP request an orderly stop; an already submitted job is drained
within the same five-second deadline before exit. Successful completion clears
only the mailbox magic, preserving submitted/completed sequence history.

## Scope and safety boundaries

The C validator checks actual descriptors and indirect packet/table contents,
including cache-line fetch bounds and packet/table immutability across GPU writes.
It rejects presentation commands, unknown opcodes, undeclared pointers, out-of-pool
spans, reserved fields, excess work, and unsupported numerical ranges before
publishing a job. CRC32 detects transport damage; it is not an authenticity
signature. Only the generator's reviewed synthetic bundles are intended for use.

Legacy affine opcode 5 is a complete 64-byte one-shot tint/blend setup, followed
by a separate complete 64-byte opcode 4 draw; there is no opaque extension body to
skip. The QA validator requires immediate adjacency after setup and also accepts
standalone affine draws using default state. It rejects orphaned/overwritten setup
and reserved fields. Full declared raster dimensions count against the work limit,
including clipped pixels. Every visible, source-bounds-passing affine sample is
checked using 32-bit wrapped coordinates, half-open signed bounds, unsigned 16-bit
V row selection, and its whole 128-byte cache line. Negative U bounds are rejected:
the legacy affine RTL address expression does not sign-extend negative U as the
axis path does. This restricted hardware fixture does not claim that unsupported
domain is correct; a negative-U simulation demonstrated an out-of-span read.
Packet/table immutability still considers every descriptor in the job.

The affine fixture covers rotations, shears, mirroring, repeated UVs, signed 32-bit
coordinate wrap, all destination borders, half-open source edges, rounded tint
endpoints, all three legacy blend modes, and one-shot reset to default state.
The gather fixtures cover paired/cache-line-crossing samples, both DDR lanes,
mirroring/scaling/repetition, signed wrap, clipping, and source-cache invalidation
after STORE before a later gather or LOAD. With `AM2R_TEST_RTL=1`, the parser test
also replays the exact three new hardware bundles under two DDR stall schedules.

Axis, gather, affine, transfer, and bounded-clear expected pixels have independent test equations.
Generic output uses the independently implemented scalar GPU model, supplemented
by separate blend-state/geometry tests. These fixtures prove the implemented
protocol and pixel contract; they do not independently prove native GameMaker
equivalence or whole-game performance. Hardware PASS must be reported separately
from host validation and simulated replay.
