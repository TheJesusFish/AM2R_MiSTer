# Source and evidence register

Links were checked during bootstrap preparation in September 2026. The adopted
implementation checkouts are pinned locally: Butterscotch
`7c2503efc25f20dddb9ba7b7cf7b46fd4f63ba08`, Template_MiSTer `3ea1134c`,
Menu_MiSTer `3c3634c`, and the adopted Main_MiSTer frontend checkout
`915ca3395aa5a26322007974faa757299a56b856`. That commit and official
`origin/master` were refreshed and matched on 2026-09-06. DMTCP 3.2.0 is
pinned at `bc38d1a3bdfca87905f1a3adfada1e63d64042e5`. Butterscotch and DMTCP
modifications are preserved under `patches/`; remaining branch URLs below are
discovery entries unless an exact revision is stated.

## Primary sources

| Source | What it establishes | Limits / intended use |
| --- | --- | --- |
| [AM2R community source](https://github.com/AM2R-Community-Developers/AM2R-Community-Updates) | Public reconstructed GML for the 1.5.x branch; reconstruction setup and exclusions | Archived; README says newer work is private. Not the proprietary runtime, complete assets, or proof of matching the supplied release |
| [AM2R FAQ](https://am2r-community-developers.github.io/DistributionCenter/faq.html) | Native 320×240 4:3 / 426×240 widescreen; supported platforms; ARM Linux loader guidance | Platform documentation is not a MiSTer benchmark |
| [AM2RLauncher](https://github.com/AM2R-Community-Developers/AM2RLauncher) | Community patching and APK creation workflow using an existing 1.1 copy | Launcher source/executable alone is not game data |
| [Butterscotch](https://github.com/ButterscotchRunner/Butterscotch) | Open GameMaker runner; ARM Linux build target and AM2R example | Early implementation; exact game compatibility and software/frame-buffer rendering backend need audit |
| [DMTCP](https://github.com/dmtcp/dmtcp) | User-space Linux process checkpoint/restart used for persistent slots; adopted release 3.2.0 | MiSTer's 32-bit ARM userspace requires the tracked ARMv7 portability patch and a matching packaged runtime |
| [gmloader-next](https://github.com/JohnnyonFlame/gmloader-next) and [entry point](https://github.com/JohnnyonFlame/gmloader-next/blob/master/gmloader/main.cpp) | Android runtime compatibility layer; ARM hard-float target; GLES2 context and forwarding | Requires matching native runtime and working graphics backend; not a turnkey MiSTer renderer |
| [droidports](https://github.com/JohnnyonFlame/droidports) | Earlier ARM Linux Android-runner approach linked by AM2R developers | Use for history/compatibility evidence; choose and pin one actual loader implementation |
| [Terasic DE10-Nano specifications](https://www.terasic.com.tw/cgi-bin/page/archive.pl?CategoryNo=165&Language=English&No=1046&PartNo=2) | Board device and HPS/RAM specifications | Inspect the physical target and configuration; external SDRAM capacity is not known from this handoff |
| [MiSTer FAQ](https://mister-devel.github.io/MkDocs_MiSTer/basics/faq/) | DE10-Nano lacks a conventional GPU | FPGA video output does not implement GLES rendering by itself |
| [MiSTer CRT guide](https://mister-devel.github.io/MkDocs_MiSTer/advanced/crt/) | Analog IO, Direct Video and analog-format connection options | Apply only to the actual installed hardware and selected framework revision |
| [Template_MiSTer](https://github.com/MiSTer-devel/Template_MiSTer), [Menu_MiSTer](https://github.com/MiSTer-devel/Menu_MiSTer), [Main_MiSTer](https://github.com/MiSTer-devel/Main_MiSTer) | Actual MiSTer RTL framework, menu and HPS integration code | Audit/pin before relying on register layouts, signals, configuration syntax or build tooling |
| [NES_MiSTer](https://github.com/MiSTer-devel/NES_MiSTer) | OSD slot/save/load conventions and user expectations for reusable save states; inspected at `9a63821173b6da4d6e95dcbe2e2a322ec8171144` | NES serializes FPGA state; AM2R instead snapshots the HPS process and explicitly restores shared GPU/audio resources |
| [JTFRAME](https://github.com/jotego/jtcores/tree/master/modules/jtframe) | Signed CRT sync-position adjustment through `jtframe_resync`; adopted from commit `37c88797f66416c0f1c9b4d7d455b4a5b9a95899` | GPL-3.0-or-later; AM2R uses a resettable, core-local adaptation and does not modify `sys/` |
| [Arcade-IGSPGM_MiSTer](https://github.com/MiSTer-devel/Arcade-IGSPGM_MiSTer) | Consumer-CRT horizontal width correction using a measured, buffered scanline; adopted from commit `6f757e42779a93e940134018c25f08aafaf95b93` | GPL-3.0-or-later; the upstream option warns that it is intended for analog 15 kHz use and can interact poorly with the HDMI scaler |

## Architectural examples, not proof for AM2R

- [Experimental Sonic Mania MiSTer port](https://github.com/kimchiman52/sonic-mania-mister): ARM game execution with an FPGA display layer and menu wrapper. It uses a different engine and has its own limitations. Audit any reused code and its license; do not copy its memory addresses or performance claims into this project.
- [3S ARM MiSTer port](https://github.com/kimchiman52/3s-mister-arm): reference for
  a normal core selecting an ARM frontend through `MiSTer.ini`, inspected at
  `e37d62083bca8421f4c83eebf4d34a37e82f9992`. AM2R adopts that launch shape,
  while using its own runner, ZIP loader, DDR ABI, and FPGA GPU.

These examples support investigating the architecture. They do not establish that AM2R works or reaches 60 FPS on the DE10.

## Evidence order and capture

Use selected upstream code/specifications to establish interface contracts, original game behavior to assess game compatibility, and measured target behavior for performance and signal validation. A test harness or emulator is evidence of its own implementation, not a universal hardware oracle. Adapted archive notes are secondary engineering guidance.

For each adopted source, add a record under `reports/` with URL, commit/version, retrieval date, license, relevant files, what was learned, and outstanding uncertainty. Save small necessary excerpts with provenance; avoid copying whole websites or unrelated repositories. Cite paths from the actual checkout, not directories described by another author's source map.

Follow the original game project's reconstruction instructions only when rebuilding it. Otherwise preserve and load the user's compatible compiled data. Keep game payloads and proprietary runtime/shader binaries separate from distributable project source. Review licenses before incorporating or redistributing third-party code; no publication is part of bootstrap preparation.
