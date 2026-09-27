# Rendering diagnostics

The tooling attributes rendering cost without changing game rules, graphics,
GPU commands, clocks, or production defaults. It is not itself a performance
fix. Use the exact supplied checkpoint, matched RBF/runner/Main identities, and
recorded input route; do not silently substitute a reconstructed room.

## Unified report

`tools/analyze_render_diagnostics.py` combines **labelled observations**, not
overlapping costs. It accepts these sources:

| Kind | Input | What it measures |
| --- | --- | --- |
| `stages` | `capture_uprobe_stages.sh` trace | Paired Step/Draw/present/wait durations; Step-to-wait work envelope and game-tick cadence |
| `functions` | Paired function/target uprobe trace | Inclusive duration of the explicitly labelled upload or other function |
| `events` | Resolved-event uprobe trace | Inclusive GML-event durations, preserving nested calls and process identity |
| `gpu` | `am2r_gpu_sample.c` CSV | Sampled completed-job cost, native publication cadence, vblank cadence and sampled underflow flags |
| `surfaces` | Opt-in runner timing CSV | Per-frame surface/operation cost, calls, sampled pixels and actual uploaded bytes when measured |

Every source is hashed in the generated report. An optional source `sha256`
rejects a mismatched input. Artifact/state hashes in the manifest are operator
claims: independently verify the deployed files and live runner executable;
the analyzer cannot check an offline hardware installation.

Example manifest (replace placeholder hashes; save under ignored `data/`):

```json
{
  "schema_version": 1,
  "experiment_id": "room160-right-fire",
  "identities": {
    "rbf_sha256": "<64 hex digits>",
    "runner_sha256": "<64 hex digits>",
    "main_sha256": "<64 hex digits>",
    "state_sha256": "<64 hex digits>"
  },
  "comparison": "independent-runs",
  "samples": [
    {
      "id": "fire-cpu",
      "run_id": "usb1-fire-paired-001",
      "room": "160:rm_a3b04",
      "route": "Load exact slot 2; face right; repeat Fire; initial/final idle excluded.",
      "instrumentation": "Paired stage probes enabled",
      "sources": [
        {"kind": "stages", "path": "fire-stages.trace", "window_ms": [4000, 14000]}
      ]
    },
    {
      "id": "fire-cadence",
      "run_id": "usb1-fire-no-function-probes-002",
      "route": "Repeat the same checkpoint and input route in a separate pass.",
      "instrumentation": "Read-only GPU sampler; no function probes",
      "sources": [
        {"kind": "gpu", "path": "fire-gpu.csv", "gpu_clock_hz": 88000000,
         "window_ms": [4000, 14000]}
      ]
    }
  ]
}
```

Paths resolve relative to the manifest. Unknown artifact/state identities must
be explicit `null`, never an invented hash; the report marks that limitation.
Sample IDs and run IDs must be unique. Put sources from one acquisition in one
sample with explicit `"association": "same-run"` if it has multiple sources.
Separate passes require `"comparison": "independent-runs"` at the top level.
The analyzer never aligns different clocks or merges frames merely because
files share a room, process, timestamp-like filename, or input route.

`window_ms` is `[inclusive-start, exclusive-end]`, with `null` meaning no upper
bound. For tracefs sources it is relative to the first parsed trace event; for
GPU sources it is relative to sampler start. Surface CSV uses its producer's
elapsed origin. Even sources in the same run do not have an implicit shared
zero. Record deliberate synchronization separately before claiming per-frame
correlation. Calls straddling a selected window are excluded from its duration
distribution; frame cycles are included only when both Step boundaries fit.

```text
python tools/analyze_render_diagnostics.py data/artifacts/qa/manifest.json
python tools/analyze_render_diagnostics.py data/artifacts/qa/manifest.json --format json --output data/artifacts/qa/report.json
python tools/analyze_render_diagnostics.py data/artifacts/qa/manifest.json --output data/artifacts/qa/report.md
python tests/runtime/test_analyze_render_diagnostics.py
```

## Interpretation rules

- CPU events, upload calls and surface work are **inclusive**. Light-mask
  Other 11 is inside Step; uploads can be inside Draw and other upload APIs.
  Do not add these to CPU work a second time. `work_before_wait` is an elapsed
  wall-time envelope, not a sum of sampled stages or pure scheduled CPU time.
- FPGA work overlaps the ARM's work. CPU plus GPU milliseconds is not the
  pipeline's frame budget. A 20 ms CPU envelope can miss 60 Hz while an 8 ms
  GPU job has capacity left.
- Entry-only stage traces provide cadence and entry-to-next-entry envelopes.
  These are explicitly separate from paired function durations: gaps between
  calls are not presented as time inside the function.
- Native publication is the change in `native_frame` divided by elapsed
  time. It is not the completed GPU job count, number of commands, vblank rate,
  capture-device frame rate, or an end-to-end input-latency measurement.
- `not_collected`, `no_observations`, and missing numeric fields are unknown.
  An unavailable vblank heartbeat does not become zero FPS. An empty capture
  does not become zero underflows or zero rendering work.
- A zero GPU fallback count does not cover supported software rendering on
  user/offscreen surfaces. The light mask can be expensive without any fallback.
- Both thread-local nested function pairing and event pairing are preserved.
  Counter wraps are handled modulo 32 bits; apparent resets are rejected rather
  than turned into enormous FPS values. Trace loss and unmatched boundaries are
  disclosed. A lost nested event can still compromise attribution: recapture
  before drawing a strong conclusion from a lossy trace.
- Detailed probes or pixel captures can perturb gameplay. Repeat the route
  without them and use only read-only cadence sampling to check the conclusion.
  `capture_perturbation` records are diagnostic disturbance, not gameplay cost.

## Read-only FPGA sampling

`tools/am2r_gpu_sample.c` promotes the sampler used for the September 25 supplied
checkpoint diagnosis. It maps only the existing AM2R GPU control page at
`0x23ff0000`, opens `/dev/mem` read-only, uses `PROT_READ`, and never submits
commands, reads framebuffer pixels, or writes mapped memory. It polls at 1 ms,
collects bounded records in RAM, unmaps/closes, and only then writes its CSV.
Its limit is 180 seconds. This still incurs polling/scheduler overhead, so it
is low-perturbation, not mathematically zero-overhead.

```text
scripts/zig-cc-arm-linux.cmd -std=c11 -O2 -Wall -Wextra -Werror -s tools/am2r_gpu_sample.c -o data/build/am2r-gpu-sample
```

On the authorized MiSTer, after confirming the matching AM2R control ABI:

```sh
/tmp/am2r-gpu-sample 20000 > /tmp/qa/fire-gpu.csv
```

Do not use this physical address on another core or an unknown layout. It
rejects incoherent/mid-submission observations and invalid GPU magic. The
scanout counters are asynchronous: they are not guaranteed to describe exactly
the same instant as the sampled completed job. Polling can miss completions;
the report retains actual counter increments rather than pretending every
publication was captured. Zero **sampled** underflow flags is the precise claim.
An old RBF without the heartbeat cannot establish native publication cadence.
Pass the clock belonging to the exact tested RBF; this is not auto-detected.

## Optional surface CSV interchange

Required columns are:

```text
elapsed_ns,frame,surface_id,operation,calls,pixels,elapsed_work_ns,uploaded_bytes
```

Additional runner columns `room`, `input_mask`, and `dropped_scopes` are retained
as coverage metadata. Rows represent operation buckets for a frame; optional
metrics should be blank when not measured rather than reporting a false zero.
`elapsed_work_ns` is inclusive. Despite its historical name, `output_axis`
includes output axis/triangle/clear dispatch and may include command emission
and uploads, while `user_axis` is CPU user-surface work. `subtract_scaled` and
`subtract_unit` are nested subcategories of user-axis work; unit versus scaled
refers to the horizontal source step that selects the unit-span fast path.
Do not classify all drawing
as FPGA merely because a GPU source exists in the report. Do not add output,
surface, upload and top-level CPU buckets together.

The report groups by surface ID and operation, preserving call count and
per-row distributions. Frame identities include elapsed time because loading a
state can rewind the game's frame number. Scope-drop counts are deduplicated
per frame and reported; overflow is incomplete evidence, not a silent pass.
This interchange alone does not enable producer instrumentation in a release
runner. Build-time/activation controls belong to the runner implementation.

The initial producer records bounding-box candidate pixels for axis draws and
full-target pixels for clears, not unique pixels changed. Triangle scopes do
not yet count pixels and emit a placeholder zero; do not infer that a timed
triangle touched no pixels. Upload bytes count actual shared-texture DDR writes,
excluding CPU shadow copies. Timed `upload` scopes cover the public upload APIs;
water-table, reattach and software-present copies can add uploaded bytes outside
those API scopes. Therefore uploaded bytes and API duration are related
observations, not a universally identical scope or an exact bandwidth benchmark.

## Capture/reference boundary

The performance report is not a pixel reference renderer or a hardware replay.
For pixel correctness, use the separate self-contained frame fixture tooling:
commands alone are insufficient without all texture ranges and initial/export
surface dependencies. Preserve command order, exact blend rounding, signed
sampling and same-frame surface ownership. Keep all game-derived fixtures,
screenshots, logs, state files and generated reports under ignored `data/`;
only synthetic/minimized test inputs and sanitized summaries belong in Git.

The reference and RTL replay tools distinguish agreement with each other from
agreement with captured hardware. Both compare every declared captured export,
reject duplicate export expectations, and report missing captured-output
coverage as `partial_match` or `not_compared`, never a full capture match.
`replay_gpu_capture.py` includes `rtl_vs_captured_exports` and
`captured_comparison_status` alongside model/RTL comparisons. An overall model
match without captured expectations is not a hardware-corruption clearance.
Declared blob `bytes` and `command_count` metadata are checked when present.
Simulator errors/fatals reject a replay even if the process exits zero.

### Building and collecting a QA run

Build with `scripts/build-butterscotch-mister.ps1 -BuildDirectory
data/build/render-diagnostics -RenderDiagnostics -KeepSymbols`. Ordinary builds
explicitly set diagnostics OFF, including when reusing a prior CMake directory.
Symbols are optional and must not be included in a normal distributed runner.
Use only an authorized test MiSTer, retain its original binary, and isolate its
ordinary saves and save states before launching the QA runner.

For timings, create an existing private output directory and atomically publish
`/tmp/am2r-render-diag.request` containing one line, for example
`300 /media/fat/_Dev/qa-fire`. The runner consumes the request within 16 frames,
records up to 1,800 frames in bounded RAM, then writes `render-timing.csv` once.
The output is exclusive-create: an existing CSV is never overwritten. Allocation,
request handling and final file flush are outside measured work. Discard the
boundary pause when measuring cadence. There is no per-frame logging or file I/O.
Requests made before the previous finite pass completes are pending, not a way
to stop it early. Exiting mid-pass leaves an empty/incomplete output, not valid data.

Set `AM2R_GPU_CAPTURE_DIR` to an existing absolute QA directory before launching
the diagnostic runner. An empty regular `capture.request` inside that directory
requests one GPU job. It creates a fresh `job-<pid>-<sequence>-<attempt>` directory
with commands, the allocated texture-pool prefix, all three native buffers,
completed output, and any exports. A final `manifest.json` is the completion
marker. Partial directories without it are invalid. Capture is bounded to eight
requests per process and 128 MiB of texture input per job; storage is checked
first. It uses a 16 KiB streaming buffer, not another texture-pool-sized RAM copy.
Prefer disk-backed storage with ample space, not a nearly full `/tmp` tmpfs.

Do not read or archive a capture before its manifest exists, or copy an archive
while it is still being written. Record exact runner/RBF/Main/state identities
alongside the captured files; filenames and build labels alone are insufficient.
Restore the installed runtime, save mounts and original hashes after testing.

### Optional cropped-upload gap measurement

Patch 50 adds a second, deliberately intrusive diagnostic experiment. Build
with `-RenderDiagnostics`, set `AM2R_CROP_GAPS=1` in the isolated QA runner's
environment, and make the same finite `/tmp/am2r-render-diag.request` described
above. Use a new private output directory. The pass writes a separate
`render-crop.csv` only when collection finishes; existing files are never
overwritten. Values other than exactly `1`, or no active timing request, leave
gap collection inactive. Ordinary release builds compile the hook out.

The hook reads only the chosen upload buffer's **CPU shadow and CPU source**,
before the shadow is overwritten. It never reads GPU texture DDR and does not
change upload ranges, buffer selection, completion waits or barriers. Comparing
two arbitrary GPU captures is not a substitute: their post-upload textures lack
the prior shadow/buffer pairing needed for this measurement.

Each frame has a `summary` row carrying observed, recorded and dropped upload
counts, followed by at most four `upload` rows. Summary zeroes are coverage
metadata, not upload measurements. Each accepted upload is limited to 4,096
bytes per cropped row, 512 rows and 1 MiB of cropped pixels; invalid/over-limit
observations increase `dropped_uploads` and are not silently counted as zero.
Frame, room, input mask and upload ordinal identify the observation. The same
1,800-frame maximum as the timing collector applies, with bounded in-memory
records and no per-frame file I/O.

For upload rows:

- `single_span_bytes` and `single_span_calls` describe the existing one-span-
  per-changed-row algorithm. These are reconstructed from the exact paired
  pixels, not an additional set of actual DDR-write counters.
- `changed_pixel_bytes` counts all four RGBA bytes when any channel differs;
  a partial final pixel contributes only its valid bytes.
- `interior_gap_bytes` counts unchanged whole pixels between changed pixels.
  `single_span_bytes = changed_pixel_bytes + interior_gap_bytes` must hold.
- `predicted_bytes_gapN` / `predicted_calls_gapN` estimate split uploads when
  unchanged gaps of at most N bytes are copied rather than split. N is 0, 16,
  32, 64 or 128. These are **predictions**, not implemented uploads or measured
  speedups. More small copies and the additional scan itself can cost time.

All rows mark `timing_perturbed=1`. `measurement_ns` reports the added scan's
duration, but subtracting it does not recover uninstrumented timing because
cache and scheduling effects remain. Use a short 60–120-frame pass to decide
whether interior gaps merit an experiment, then repeat any performance claim
with gap measurement and other intrusive tracing disabled. The normal timing
CSV and its actual `uploaded_bytes` retain their original meanings.

Synthetic contract tests: `python tests/runtime/crop_gap_test.py`. They cover
dense and separated changes, alpha-only differences, threshold boundaries,
strided partial rows, input immutability, rejection/overflow reporting,
incomplete-frame reset, exclusive output, runtime activation and disabled
release-macro arguments.

### Reference and RTL replay

Run `python tools/am2r_gpu_reference.py <capture>/manifest.json --output
data/artifacts/reference` for independent scalar pixels, a difference image and
comparison JSON. Run `python tools/replay_gpu_capture.py <capture>/manifest.json
--output data/artifacts/rtl-replay` for the same inputs through the unchanged
production GPU RTL in ModelSim. The latter records tool/source/input identities
and configurable DDR latency/backpressure; `--help` lists those controls.

Synthetic validation: `python tests/renderer/test_gpu_reference.py`,
`python tests/renderer/test_gpu_capture.py`, and
`python tests/runtime/test_render_diagnostics.py`. Set `AM2R_TEST_RTL=1` for the
reference test's all-opcode RTL differential checks. A passing submitted-job
comparison does not validate CPU-generated light pixels or the game's draw-call
translation: both are already inputs to this GPU fixture. Use native comparison
and CPU raster tests for that separate boundary.

### GPU job initialization

The opt-in runner capture records the original submitted commands without
inserting a clear or changing prefix-culling behavior. Prior on-chip RGBA
framebuffer contents are not exposed by the existing hardware interface; the
native XRGB scanout copy discards alpha and is not a valid substitute.

A capture without a leading clear therefore declares
`"initial_framebuffer_status":"unavailable"` and
`"requires_initialization_proof":true`. Its DDR inputs and paired output are
captured, but it is not a usable self-contained replay fixture until the
independent reference proves that every needed pixel is initialized before any
read or final presentation. An opaque full-screen prefix can pass that proof;
a continuation requiring prior pixels must fail. An export before any drawing
is rejected by capture immediately. No tool may silently seed black pixels or
claim that a successful file capture alone proves completeness.
