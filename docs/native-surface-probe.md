# Native Windows logical-surface comparison

`tools/am2r_native_surface_probe.py` samples an **already running**, original
Windows AM2R 1.1 process. It does not launch or control it. This compares the
original runner's logical surface lifetime with Butterscotch's surface census;
it is not a measurement of allocated video-memory bytes.

The script uses only `OpenProcess(QUERY_LIMITED_INFORMATION | VM_READ)`,
`QueryFullProcessImageNameW`, `ReadProcessMemory`, and `CloseHandle`.
There is no debugger attach, injection, process-memory write, suspension,
game-function call, input, termination, save access, or file output. JSON lines
go to stdout. Its only file reads are identity hashes of the executable and
adjacent `data.win`. Existing files are never changed by the sampler.

## Exact supported identity and static evidence

The method was derived by disassembling the supplied original executable with
the locally available Capstone Python module, without executing it. No game
code or data is included with the probe. The identity is fail-closed:

- Executable SHA-256:
  `fe6e75f402235e126eb75a16d2a918dc2aef1b1e3f41e5c36d198d277bc94267`.
- `data.win` SHA-256:
  `36e4a251d7b687f2d742a8e911cb1e1185aea99e36529fcf32cd18d445a355e3`.
- PE machine: x86 (`0x14c`), verified image base `0x400000`.
  Relocated or modified versions are not supported; three live function
  signatures are checked before the first registry read.

| Native function | Evidence |
| --- | --- |
| `surface_create` builtin `0x4b4360` | Calls `0x425280`; this inserts a 16-byte record, stores returned texture handle at +4, width at +8, height at +12. |
| `surface_exists` builtin `0x4b4540` | Calls `0x4253b0`; existence means a matching map entry has a non-null record. |
| `surface_free` builtin `0x4b4590` | Calls `0x4254c0`; this releases the backing texture via `0x4253f0`/`0x4277d0` and removes the map entry via `0x4251c0`. |
| Width/height builtins `0x4b45c0`/`0x4b4600` | Call `0x425560`/`0x4255b0`, reading record +8/+12 except for the active application-surface special case. |
| Map insertion/removal `0x425150`/`0x4251c0` | Increment/decrement map+8, proving this is the entry count rather than capacity or the next ID. |

The surface map starts at `0x6a13d4`: bucket pointer, mask, live entry count.
Each bucket is an 8-byte head/tail pair. Its 16-byte node contains previous,
next, ID, and record pointer. A record contains ID, texture handle, width,
height. `0x6d964c` is the **next surface ID**, not a live count. A zero-sized,
invalidated record can still satisfy native `surface_exists`, so the probe
reports it rather than silently calling it freed. Null records are not counted
as existing surfaces, but remain included in `map_entries`.

Room (`0x8f0f48`), object registry (`0x6eebb4`), variable names
(`0x907ec8`/`0x907ecc`), light object ID 727, and instance-variable layout reuse
the previously verified read-only method in
`reports/native-lighting-semantics-2026-09-25.md`. The old private helper is
`data/work/native-light-reference-20260925/native_inspect.py`; it hardcodes an
old PID and should not be run blindly. The new probe accepts an explicit PID.

## Safe comparison procedure

1. Have the user or the authorized UI task launch an **isolated copy** of the
   original game with a copied, native-compatible ordinary save and its own
   `LOCALAPPDATA`. Do not launch over the source save directory. The old native
   game can delete a save it decides is corrupt. The sampler itself does none
   of this setup or launching.
2. Find the existing process ID with a read-only process inventory. Run a
   single sample first:

   ```text
   python tools/am2r_native_surface_probe.py --pid <PID>
   ```

3. After a stable sample succeeds, sample at 5 Hz while the user or authorized
   UI task moves back and forth between the same two lighting rooms:

   ```text
   python tools/am2r_native_surface_probe.py --pid <PID> --samples 1500 --interval 0.2
   ```

4. Record a stationary baseline, at least 20 room transitions, and a stationary
   ending interval. Compare settled samples in each room: logical count,
   512x256 count, retained IDs/texture handles, and `oLightEngine.surf`. Keep
   those room IDs and transition count aligned with the runner's telemetry.
   Do not compare absolute starting counts from unrelated sessions.
5. If optional instance metadata cannot be sampled coherently, `--no-light`
   retains the independently derived room and surface registry census. Keep
   the missing ownership correlation explicit in conclusions.

If old 512x256 IDs remain present and new ones appear on each transition in
both implementations, that demonstrates matching **logical retention**. It
does not prove a driver-level VRAM leak. If native counts stabilize while
Butterscotch's keep growing under the same route, investigate runner lifecycle
semantics. If native IDs change but count remains fixed, they are being freed
or invalidated/recreated; a monotonically increasing next-ID counter alone
would miss this distinction.

## Sampling limitations and guardrails

- This is non-atomic, read-only sampling of a running process. The probe checks
  map masks, counts, addresses, links, ownership, unique keys, dimensions and
  bounded chains, then requires two complete equal snapshots. It retries at
  most three times; failures produce an `unstable` record, never a false zero
  count. Two equal reads reduce races but cannot prove atomicity or detect
  every ABA change.
- Walks are bounded to 4,096 hash buckets, 16,384 nodes, 1,024 light instances,
  20,000 variable names, and 128 bytes per name. Unknown layouts fail closed.
  All objects, both complete snapshots, and all retries share an aggregate
  budget of 32,768 memory reads / 4 MiB per sample. Exhaustion is reported as
  unstable; retries cannot replenish the budget. PIDs must fit a positive DWORD.
  A process exiting during a read is a failed sample, not evidence of freeing.
- Logical dimensions are not allocation sizes. There are no claimed VRAM
  totals, no pixel downloads, and no attempt to infer texture format, pooling,
  reference counts, device loss, or driver residency from a handle alone.
- The native initialization log identifies D3D9Ex. An injected `CreateTexture`
  / `Release` tracer is a secondary option, not needed for the logical-count
  question. Such a tracer must handle D3D9Ex creation, final COM releases,
  acquired surface references, and device reset; raw create-call counts are
  not reliable leak evidence. It would require separate authorization and
  tooling. No debugger/Frida/apitrace executable was found on PATH during this
  assessment; absence from PATH is not proof that none is installed.
- Existing `tools/gdb_list_surface_records.py` inspects Butterscotch types and
  is not a native Windows runner probe. The old native audio capture script
  launches, presses keys, and kills its child; it is unsuitable for this
  passive comparison.

## Validation

`python tests/runtime/test_native_surface_probe.py -v` exercises synthetic
memory only: valid/lost/null records, dimensions, count and bucket mismatches,
shared records, map/instance/variable cycles, invalid names/value types,
inconsistent snapshots, failed reads, aggregate multi-instance budget exhaustion,
PID truncation prevention, and successful bounded retries. No game
data or live process is necessary. Live comparison results must be recorded
separately; passing these parser tests does not establish native room behavior.

### Initial live validation, 2026-09-26

All 20 synthetic tests passed. After the UI task independently launched the
isolated original game, three passive samples at 5 Hz succeeded with both file
hashes and live function signatures verified. Native room 1 reported two
logical surfaces (IDs 1 and 2), both 320x240, with backing texture handles 22
and 21; there were no 512x256 surfaces or light instances. Counts and records
were equal in all three samples. No elevation, UI input, debugger, or process
control was used by the sampler.

This verifies live registry decoding at the title screen, not lighting-room
ownership or retention across room changes. The UI task could not obtain a
reliable native-window capture, so it stopped navigation; no transition
comparison has yet been performed. Do not infer either a native game leak or
a runner-only leak from this initial two-surface result.
