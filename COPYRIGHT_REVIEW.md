# Copyright and publication review

This is the manual pre-publication checklist requested for this repository.
It is intentionally **not** ignored, so it remains visible before anything is
staged. It is an engineering inventory, not legal advice.

## Do not commit

Local AM2R game payloads, extracted assets, saves, checkpoints, recordings,
and development binaries are stored under `data/`. Git ignores that complete
tree. No such files were detected outside `data/` on 2026-09-14.

Manually remove from the Git index if any of these ever appear in a staged
change:

- `AM2R.zip`, `data.win`, `AM2R.exe`, extracted AM2R artwork/audio/data, or any
  other file sourced from a game installation;
- user save files (`*.sav`) and DMTCP process checkpoints (`*.dmtcp`);
- `MiSTer_AM2R`, `butterscotch`, RBF/SOF files, shared objects, recordings,
  screenshots, raw captures, credentials, or private machine configuration.

## Copyrighted third-party source that is intentionally present

These files are copyrighted, but are included as source under their stated
licenses. Review their notices before the first public commit:

| Paths | Upstream / license | Publication note |
| --- | --- | --- |
| `sys/` and the MiSTer-derived top-level framework portions | MiSTer Template, GPL-2.0-or-later | Must remain an unmodified copy of Template commit `3ea1134cf05d62c2b1db30362277a823d739ced2`. |
| `rtl/am2r_native_reader.sv` | 3S-ARM, GPL-2.0-or-later | Adapted source; its attribution header must remain. |
| `rtl/jtframe_sdram/` | JTFRAME, GPL-3.0-or-later | Adapted SDRAM-control source with upstream notices. |
| `rtl/crt_adjust.sv`, `rtl/crt_vsize.sv` | MiSTer-CRT-Adjust, GPL-3.0-or-later | Vendored at commit `c682de9f4acc61d8f4c7779efb48149d3baa3a8e`; no game data is present. |
| `rtl/am2r_osd.v`, `am2r_sys.tcl`, `am2r_sys.qip` | MiSTer Template, GPL-2.0-or-later | Core-local derived integration files; the tracked `sys/` copy remains byte-for-byte upstream. |
| `patches/butterscotch-*.patch` | Butterscotch, AGPL-3.0-only | Derived patch series; distributing a built runner also requires the corresponding source offer/license compliance. |
| `patches/dmtcp-armv7-mister.patch` | DMTCP, LGPL-3.0-or-later | Derived portability patch. |
| generated PLL/QIP sources under `rtl/` and `sys/` | Intel/Altera notices embedded in the files | Tool-generated build inputs; do not remove or replace their vendor notices. |

The matching license texts and fuller provenance are in `LICENSES/`,
`LICENSES.md`, and `docs/sources.md`.

## Names and marks

The repository necessarily uses “AM2R” and “MiSTer” descriptively. It grants
no rights to Nintendo, Metroid, AM2R, or MiSTer names, artwork, characters, or
trademarks. Keep the non-affiliation statement in `README.md` in any public
copy.
