# Licensing and provenance

This repository is an aggregate of independently licensed components. The
root `LICENSE` (GNU GPL version 3) applies to original project files that do
not carry a more specific license notice. It does not replace the notices or
licenses inherited from upstream projects.

| Area | License | Provenance |
| --- | --- | --- |
| Original scripts, documentation, tests, and utilities without another notice | GPL-3.0-or-later | AM2R MiSTer contributors |
| `src/hps-wrapper/` | GPL-3.0-or-later | AM2R MiSTer frontend based on the MiSTer Main integration contract |
| `AM2R.sv`, project RTL, and MiSTer framework files under `rtl/` and `sys/` | GPL-2.0-or-later unless an individual file says otherwise | MiSTer Template/framework; see file headers |
| `rtl/am2r_native_reader.sv` | GPL-2.0-or-later | Adapted from the 3S-ARM native video reader, itself marked GPL-2.0-or-later |
| `rtl/crt_adjust.sv`, `rtl/crt_vsize.sv` | GPL-3.0-or-later | MiSTer-CRT-Adjust, commit `c682de9f4acc61d8f4c7779efb48149d3baa3a8e` |
| `rtl/am2r_crt_pipeline.sv` | GPL-3.0-or-later | AM2R core-side integration glue for MiSTer-CRT-Adjust |
| `rtl/am2r_osd.v`, `am2r_sys.tcl`, `am2r_sys.qip` | GPL-2.0-or-later | Core-local MiSTer Template integration; OSD storage changed to explicit Cyclone V M10K RAM |
| `patches/butterscotch-*.patch` | AGPL-3.0-only | Modifications to Butterscotch, based on commit `7c2503efc25f20dddb9ba7b7cf7b46fd4f63ba08` |
| `patches/dmtcp-armv7-mister.patch` | LGPL-3.0-or-later | Modification to DMTCP 3.2.0, based on commit `bc38d1a3bdfca87905f1a3adfada1e63d64042e5` |
| Intel/Altera generated PLL files | Their embedded vendor notice | Generated Quartus IP output; see each file |

Canonical license texts are kept in `LICENSES/`:

- `AGPL-3.0-only.txt`
- `GPL-2.0-or-later.txt`
- `GPL-3.0-or-later.txt`
- `LGPL-3.0-or-later.txt`

The distributable runtime is an aggregate of the MiSTer frontend, FPGA
bitstream, patched Butterscotch runner, and DMTCP components. Its release ZIP
must include the corresponding license texts and notices. The project does not
grant rights to AM2R game data, artwork, audio, names, or trademarks.

This file records project provenance; it is not legal advice.

See `COPYRIGHT_REVIEW.md` for the pre-staging list of private/copyrighted
material that must not enter a public commit.
