# Portable project automation

- `inventory-inputs.ps1` hashes every supplied non-guide input into the ignored
  `.local/input-manifest.json`.
- `run-butterscotch-smoke.ps1` runs the deterministic AM2R 1.1 desktop and save
  persistence probe without overwriting earlier runs.
- `test-rtl.ps1` runs the renderer-hook audit and C axis-coverage regression,
  then verifies the native raster, framebuffer contract, DDR paths, and complete
  GPU command/pixel path with ModelSim.
- `build-fpga.ps1` runs the Quartus 17.0.2 flow and reports the RBF hash.
- `artifact_provenance.py` snapshots component source before each build and
  seals only a newly produced RBF, frontend, or runner. It can assemble an
  attested three-component build set and compare its hashes with `sha256sum`
  output from the actual MiSTer. `observe` records an existing set without
  claiming its source provenance.
- `build-butterscotch-mister.ps1` configures and builds the patched ARMv7-A
  hard-float runner with Cortex-A9 tuning, thin LTO, and the pinned CMake,
  Ninja, and Zig toolchain.
  Rendering diagnostics are disabled by default; `-RenderDiagnostics` makes
  an opt-in QA runner with finite timing capture and GPU job snapshots.
  See [rendering diagnostics](../docs/render-diagnostics.md). Do not distribute
  that diagnostic runner as the normal tester/release binary.
- `build-game-archive.ps1` creates the single, deterministic 45-member
  `AM2R.zip` consumed by the core wrapper and rejects any unsupported
  `data.win` before packaging.
- `build-hps-wrapper.ps1` cross-builds the core-style `MiSTer_AM2R` frontend
  against the pinned Main_MiSTer source revision.
- `package-release.ps1` creates the standalone RBF and complete installable
  runtime ZIP, includes the plain 45-file game-data list, writes SHA-256
  manifests, and verifies that neither proprietary `AM2R.zip` nor an end-user
  PowerShell packager is included. It requires current build sidecars; the
  explicit `-AllowUnattestedArtifacts` switch makes a hash-only diagnostic
  package when rebuilding all components is impractical.
- `package-source-release.ps1` creates a deterministic, GitHub-ready source
  archive from an explicit allowlist. It excludes game data, local connection
  settings, dependency checkouts, build products, captures, and release
  binaries, then rejects credential-like text and prohibited file types.
- `AM2R.sh` is retained only as a legacy development/recovery launcher. The
  production installation launches **Other → AM2R** as a normal core through
  the `MiSTer_AM2R` frontend; users should not start it from Scripts.

Scripts use project-relative paths and never embed private target credentials.
