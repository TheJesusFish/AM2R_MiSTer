# AM2R MiSTer

This is an AI generated readme. I will do a better one once the core is closer to ready.

## Install a tester build

Download `AM2R_MiSTer_runtime.zip` from the project's GitHub Release
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
extracts the archive into a generated `.runtime-cache` beside `AM2R.zip`, so it
takes longer than a warm relaunch. Later launches reuse the cache while the
archive's size and modification time are unchanged.

## Files and saves

```text
/media/fat/games/am2r/AM2R.zip
/media/fat/saves/AM2R/config.ini
/media/fat/saves/AM2R/
/media/fat/savestates/AM2R/slot1.dmtcp ... slot4.dmtcp
```

Normal AM2R saves and MiSTer save states are separate. Save states are full
DMTCP process checkpoints, are tied to the exact frontend/runner build, and
are intentionally rejected when incompatible. Checkpoints are staged directly
under `/media/fat/savestates/AM2R`, not in RAM; this prevents the Linux OOM
killer from terminating the game while DMTCP creates a 150–180 MB image. At
least 256 MiB must be free before capture begins. If it is not, the request is
rejected, the previous slot remains valid, and gameplay resumes. The final file
is published by an atomic same-filesystem rename. Wait for the **Save state
written** message before loading or copying that slot. Save time depends heavily
on SD/network storage and can approach 90 seconds on a nearly full card;
hardware-tested loads completed in a few seconds.

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

## CRT adjustments

The core's **CRT Adjustments** submenu provides signed horizontal and vertical
sync positioning plus an optional 75–123% horizontal line scaler. The scaler
is intended for 15 kHz analog displays whose visible raster clips the native
image. All controls default to zero/off; in that state the RTL is an exact
clock, RGB, blanking, and sync bypass and adds no buffering.

The **CRT UI V Inset** option moves only UI pixels which enter the
top or bottom sixteen-line edge band, plus the title screen's separate version
and URL overlays. It keeps those elements at their original pixel size: the
320×240 game scene, camera, collision coordinates, title background, and other
artwork are neither scaled nor cropped. Values are the number of whole pixels
moved toward the center; 6px is the initial approximately-five-percent
safe-area trial. The option is off by default.

MiSTer's core interface exposes one video stream before the framework splits
the direct analog path from the HDMI/ascal path. Consequently, enabling a CRT
adjustment also changes the signal presented to ascal even though the final
HDMI mode is still produced by the normal framework. Leave the controls off
when only HDMI is in use. That limitation also applies to the UI inset because
the runner composes it before the framework split. Horizontal scaling buffers
one scanline, not a frame; the UI inset adds no video buffering.

## Architecture

```text
AM2R.zip
   │ validated extraction to generated local cache
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
