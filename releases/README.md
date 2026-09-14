# Release artifacts

`AM2R.rbf` is the standalone FPGA bitstream. `AM2R_MiSTer_runtime.zip` is the
matching installable runtime payload; extract it at `/media/fat`, add the
documented `[AM2R]` mapping to `MiSTer.ini`, and supply your own game archive.

The runtime bundle contains the RBF, current-upstream-based `MiSTer_AM2R`
frontend, patched Butterscotch ARM runner, DMTCP save-state runtime, licenses,
installation notes, and an internal SHA-256 manifest. It deliberately does not
contain `AM2R.zip` or any other proprietary AM2R data.

Create the private game-data archive with any ordinary ZIP program and copy it
to `/media/fat/games/am2r/AM2R.zip`. The required 45 paths are listed in
`GAME_DATA.md`; the tester ZIP includes the same list as `AM2R_GAME_DATA.txt`.
Verify distributable artifacts against `SHA256SUMS.txt`.

Release ZIPs and RBFs are intentionally ignored by Git. Upload the tester ZIP
as a GitHub Release asset instead of committing it to the source repository.
Create the separately shareable source snapshot with
`scripts/package-source-release.ps1`.
