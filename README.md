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
/media/fat/savestates/AM2R/slot1.fast ... slot4.fast
```

Normal AM2R saves and MiSTer save states are separate. Save states use a
versioned logical snapshot of the GameMaker VM, rooms, instances, data
structures, surfaces, audio, and runner state. Each slot is validated with a
payload CRC and an AM2R data fingerprint before live state is changed. The
final file is published by an atomic same-filesystem rename, so an interrupted
write cannot replace the previous slot. Wait for the **Save state written**
message before loading or copying a slot.

On USB-1, ordinary 1.53 MB gameplay states saved in 0.28–0.68 seconds and
loaded in 0.40–0.49 seconds during the final repeated test. States survive a
core exit and can be overwritten after loading. Legacy `.dmtcp` files are left
untouched but are not logical-state slots and cannot be converted to `.fast`.

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

## Video output

**Video Standard** selects the analog raster:

| Setting | Raster | Line rate | Refresh | Composite/S-Video subcarrier |
| --- | --- | --- | --- | --- |
| NTSC | 427×262 | 15.704 kHz | 59.94 Hz | from `ntsc_mode` in MiSTer.ini |
| PAL60 | 427×262 | 15.704 kHz | 59.94 Hz | PAL (4.43 MHz) |
| PAL | 429×312 | 15.631 kHz | 50.10 Hz | PAL (4.43 MHz) |

The 320 active pixels span 47.7 µs per line, approximately 6.8% narrower than
the previous 51.2 µs raster without resampling the artwork. This provides
more horizontal overscan margin; actual fit still depends on the CRT and
does not change the 240 active lines or vertical overscan. NTSC and PAL60 have
identical RGB timing; PAL60 only changes the colour
subcarrier for composite and S-Video. PAL centres the 240 active lines in the
taller 50 Hz frame. The game still runs at full 60 Hz speed in PAL mode, so
one frame in six is not displayed.

**Scale** provides MiSTer's standard HDMI integer-scaling modes. Gamma curves
from the MiSTer video menu apply to the analog output, and to HDMI only with
`direct_video=1`. Normal HDMI output reads the rendered 320×240 frames
directly from DDR (see below), so it is not gamma corrected; the framework
scaler has no gamma stage for 32-bit framebuffers. With `forced_scandoubler=1`
in MiSTer.ini, the analog output is line-doubled to 31 kHz for VGA monitors,
and **Scandoubler Fx** offers HQ2x or scanlines. For HDMI, `vsync_adjust=1` or
`vsync_adjust=2` in MiSTer.ini matches the output refresh to the core instead
of periodically repeating or dropping a frame.

## CRT adjustments

The core's **CRT Adjustments** submenu provides signed horizontal and vertical
sync positioning plus an optional 75–123% horizontal line scaler. The scaler
is intended for 15 kHz analog displays whose visible raster clips the native
image. All controls default to zero/off; in that state the CRT-adjustment
block bypasses scaling and positioning. The downstream gamma/video output
pipeline still adds registered pixel delay, but no additional frame buffer.

The **CRT UI V Inset** option moves the complete in-game HUD as one layer,
keeping numbers, tanks, weapon icons and minimap aligned. It also adjusts
edge text and the title screen's separate version and URL overlays.
It keeps those elements at their original pixel size: the
320×240 game scene, camera, collision coordinates, title background, and other
artwork are neither scaled nor cropped. Values are the number of whole pixels
moved toward the center; 6px is the initial approximately-five-percent
safe-area trial. The option is off by default.

HDMI reads the GPU's published 320×240 frames directly from DDR through the
framework framebuffer interface, so the position and horizontal-scale
controls affect only the analog output in the normal configuration.
`direct_video=1` instead sends the adjusted native raster over HDMI.
Conversely, `vga_scaler=1` (or `vga_fb`) routes the HDMI/scaler image to analog,
bypassing the native analog positioning, H Scale and gamma path.
The CRT UI V Inset is the exception: it changes where the runner draws the
HUD and edge text inside the published frame, so HDMI shows the same small
shift. Horizontal scaling buffers one scanline, not a frame. The
horizontal scaler is disabled while `forced_scandoubler` is active because it
exists only for 15 kHz displays.

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
