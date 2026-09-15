# AM2R MiSTer

AM2R MiSTer is an experimental hybrid core for the DE10-Nano. The ARM HPS
runs AM2R 1.1 through a patched Butterscotch GameMaker runtime, while a custom
FPGA GPU handles the dominant clear, fill, blit, affine, alpha, additive, and
presentation operations. Native 320×240, 59.94 Hz scanout feeds MiSTer's
standard HDMI and analog-video paths.

The current v24 engineering build launches as a normal MiSTer core, reaches
gameplay, supports standard in-game saves and four persistent process-level
save-state slots, and has been exercised on real MiSTer hardware. It remains a
tester build rather than a claim of complete-game compatibility.

This repository contains no AM2R game data. Testers must provide their own
lawfully obtained AM2R 1.1 files and place the required files into a ZIP.

## Install a tester build

Download `AM2R_MiSTer_Discord_Test_v24.zip` from the project's GitHub Release
and extract it at the root of the MiSTer SD card. It installs this layout:

```text
/media/fat/MiSTer_AM2R
/media/fat/_Other/AM2R.rbf
/media/fat/games/am2r/bin/butterscotch
/media/fat/games/am2r/dmtcp/
```

Add this section to `/media/fat/MiSTer.ini`:

```ini
[AM2R]
main=MiSTer_AM2R
```

Create an ordinary ZIP named `AM2R.zip` containing the 45 files listed in
[GAME_DATA.md](GAME_DATA.md). Put those paths at the root of the ZIP, then copy
it to `/media/fat/games/am2r/AM2R.zip`. Any normal ZIP program can create it;
compression level does not matter. Do not distribute the resulting archive.

Launch **AM2R** from MiSTer's **Other** folder. The first launch validates and
extracts the archive into RAM, so it takes longer than a warm relaunch.

## Files and saves

```text
/media/fat/games/am2r/AM2R.zip
/media/fat/saves/AM2R/config.ini
/media/fat/saves/AM2R/
/media/fat/savestates/AM2R/slot1.dmtcp ... slot4.dmtcp
```

Normal AM2R saves and MiSTer save states are separate. Save states are full
DMTCP process checkpoints, are tied to the exact frontend/runner build, and
are intentionally rejected when incompatible. Saving a checkpoint can take
about a minute on current hardware; loading is much faster. A near-instant
implementation would require a game-specific serializer.

## Controls

The mapper exposes actions in this order:

1. Fire — X
2. Jump — A
3. Missiles — Y
4. Walk — B
5. Aim Up — R
6. Aim Down — L
7. Weapon Select — Select
8. Start — Start
9. Morph — unbound
10. Save State — unbound

Classic Morph Ball accepts crouch followed by Down again, so the Morph action
is optional with the game's default setting. Weapon Select+Start exits the ARM
runtime and returns to `menu.rbf`.

## CRT Adjust

The optional **CRT Adjust** submenu uses the upstream
[MiSTer-CRT-Adjust](https://github.com/rmonic79/MiSTer-CRT-Adjust) core-side
pipeline. It provides H-Size, H-Position, V-Shift, and experimental V-Size.
Everything defaults to Off/zero; Off bypasses the complete pipeline and retains
the native clock, RGB, blanking, sync, and latency.

V-Size offers two modes. **Cabinet** is the default and retains native sync by
photometrically redistributing adjacent scanlines; its trade-off is slight
vertical softness. **PVM** keeps every source line unique but changes the line
rate, so it is intended only for monitors with sufficient H-lock range. One
V-Size menu step is three lines (about 1.1% of the complete raster or 1.25% of
the 240-line active image). Negative values make the picture shorter; start at
`-2` and move toward `-4` only if the tube needs closer to a 5% reduction.

MiSTer's core interface exposes one video stream before the framework splits
the direct analog path from the HDMI/ascal path. Consequently, CRT Adjust also
changes the stream presented to HDMI while it is On. Leave it Off when an
untouched HDMI source is required. The internal video clock is 50 MHz with a
divide-by-8 pixel enable; the resulting native output remains 6.25 MHz,
398×262 total, 15.704 kHz, and 59.94 Hz.

The MiSTer framework remains on the core's conventional 20 MHz system clock.
Core-local build constraints keep ASCal's existing line, coefficient, and
palette stores in M10K RAM after CRT-Adjust raises overall block-RAM use; no
file under `sys/` is modified.

## Architecture

```text
AM2R.zip
   │ validated extraction to RAM
   ▼
patched Butterscotch runner on ARM
   │ commands, textures, audio, input
   ▼
HPS/FPGA shared DDR ──► fixed-function FPGA GPU ──► 320×240 native scanout
                                                         │
                                                         ▼
                                          MiSTer HDMI / analog framework
```

More detail is in [architecture.md](docs/architecture.md), with source and
revision provenance in [sources.md](docs/sources.md). The compatibility matrix
in [compatibility.md](docs/compatibility.md) distinguishes tested behavior from
areas that still need coverage.

## Build from source

Required external projects are deliberately not vendored. Their pinned
revisions and roles are documented in [sources.md](docs/sources.md).

- Quartus Prime Lite 17.0 builds the FPGA project with
  `scripts/build-fpga.ps1`.
- ModelSim exercises the command, timing, scanout, framebuffer, and arbiter
  contracts with `scripts/test-rtl.ps1`.
- Main_MiSTer commit `915ca3395aa5a26322007974faa757299a56b856`
  supplies the HPS frontend base used by `scripts/build-hps-wrapper.ps1`.
- Butterscotch commit `7c2503efc25f20dddb9ba7b7cf7b46fd4f63ba08`
  is reconstructed with the ordered patches in [patches/README.md](patches/README.md)
  and built with `scripts/build-butterscotch-mister.ps1`.
- DMTCP commit `bc38d1a3bdfca87905f1a3adfada1e63d64042e5`
  uses `patches/dmtcp-armv7-mister.patch` for the ARMv7 save-state runtime.

Release builds strip debugging symbols. Diagnostic and test utilities in
`tools/` are source-only and are not placed in the tester runtime. Generate a
runtime asset with `scripts/package-release.ps1`, or generate the clean GitHub
source archive with `scripts/package-source-release.ps1`.

## Publication and licensing

The GitHub source archive intentionally excludes:

- AM2R game files, music, extracted assets, saves, and DMTCP checkpoints;
- local credentials and raw captures;
- fetched dependency/toolchain checkouts and Quartus/ModelSim build products;
- RBF and runtime binaries, which belong on the GitHub Releases page.

See [LICENSES.md](LICENSES.md) for the component-by-component license map and
upstream attribution. AM2R game data, artwork, audio, names, and trademarks are
not licensed by this repository. This independent project is not affiliated
with Nintendo, the AM2R developers, or the MiSTer project.

Before staging the first public commit, follow
[COPYRIGHT_REVIEW.md](COPYRIGHT_REVIEW.md).

Contributions must follow [CONTRIBUTING.md](CONTRIBUTING.md), especially the
rule against uploading game data or process checkpoints.
