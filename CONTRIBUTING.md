# Contributing

Contributions are welcome for FPGA rendering, MiSTer integration, runtime
compatibility, documentation, and testing.

## Ground rules

- Do not commit or attach AM2R game data, `data.win`, music, save states,
  process checkpoints, proprietary DLLs, or extracted assets.
- Do not commit credentials, local device addresses, captures, Quartus build
  databases, compiler toolchains, or dependency checkouts.
- Keep changes to Butterscotch and DMTCP as reproducible patches against the
  pinned revisions documented in `patches/README.md`.
- Preserve upstream copyright and license notices. Add an SPDX identifier to
  new source files when practical.
- Describe which tests were run. Rendering and timing changes should include
  simulation results; claims about MiSTer behavior require real-hardware
  evidence.

## Before submitting a change

Run the focused checks that apply to your work:

```powershell
powershell -ExecutionPolicy Bypass -File scripts/test-rtl.ps1
powershell -ExecutionPolicy Bypass -File scripts/package-source-release.ps1
```

Quartus, ARM runtime, and hardware tests require the external dependencies and
private game input described in the README. Never include those inputs in a
bug report. A concise reproduction description is preferred over a save-state
attachment because DMTCP checkpoints contain executable memory and game data.
