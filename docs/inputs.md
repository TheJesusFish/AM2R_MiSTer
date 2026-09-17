# Game inputs

Supply files from your existing AM2R installation. The public source repository does not include the complete game. The entire local `data/` tree is ignored by Git and is not included in source packages.

## Development input

Copy the contents of the **complete working Windows game folder** to `data/inputs/windows/`. Preserve the original filenames and directory structure, including the executable, `data.win` if separate, music/audio, `lang`, configuration, and other companion files. Some releases package data differently; keep the whole folder contents instead of extracting only one file or renaming files to fit an example.

The Windows build provides a reference game and often convenient compiled game data for runtime experiments. Running that reference may require a Windows machine or another verified compatible environment. Merely having a Mac does not prove the original Windows game can run there.

## Core game-data file

The installed core consumes one user-supplied file:
`/media/fat/games/am2r/AM2R.zip`. Create it with any ordinary ZIP program from
the complete verified AM2R 1.1 Windows folder. The executable, DLLs, desktop
readme, configuration files, and saves are not part of the core payload.

The exact 45-member path list and supported `data.win` identity are in
[GAME_DATA.md](../GAME_DATA.md). Put those paths at the root of the ZIP and keep
their capitalization unchanged. The frontend checks that every required member
exists and verifies its ZIP CRC while extracting to the generated
`/media/fat/games/am2r/.runtime-cache`. It reuses that cache only while the ZIP's
size and modification time still match. Do not substitute files from another
AM2R release without re-validating the runtime.

## Additional useful inputs

| Destination | What to put there | Purpose |
| --- | --- | --- |
| `data/inputs/android/` | An AM2R APK, preferably matching the Windows release | Android runner/gmloader investigation; inspect for a compatible 32-bit ARM native library |
| `data/inputs/linux/AM2R-<version>/` | Complete Linux installation, including runner, data and external assets | Alternative reference/runtime input; Linux x86 executables do not run natively on the DE10 ARM |
| `data/inputs/archives/` | Original game ZIPs, including your original 1.1 archive if available | Preserve provenance; enables the official patching workflow if needed |
| `data/inputs/saves/<version>/` | Copies of useful save files | Repeatable test routes; never use the sole copy of a personal save |

You do not need every platform before source research can start. A launcher executable or launcher source alone is not a playable game payload. Do not acquire missing commercial shader packages or install an old GameMaker IDE until an actual chosen workflow needs them.

## What the building context should do

1. List existing inputs without executing them. Record release/platform, file sizes and SHA-256 hashes in an ignored manifest under `.local/`. Read archive member lists first; extract only into `data/work/` after checking paths. Reject absolute paths, path traversal, and links escaping the destination.
2. Determine whether game logic is VM bytecode or native/YYC, its data version, and the APK library architecture where applicable. Butterscotch compatibility must be established for that format; an ARM64-only runner cannot execute on the Cortex-A9.
3. Record a minimal sanitized input identity and test version in `reports/`; keep payloads and full private manifests out of tracked source. Record uncertainty when the version cannot be identified from the files.
4. Use a working copy under `data/work/` for extraction, patches, runtime configuration, and test saves. Keep the supplied directories unchanged.
5. If rebuilding the GML project becomes necessary, use the matching public source and documented GMXDataSync workflow, preserve asset order/IDs, and account for excluded shaders and datafiles. Do not assume the archived source matches the newest executable exactly.

An open runner may consume the existing game data directly. This is separate from reconstructing editable GML or compiling the original project. See [sources](sources.md) and [architecture](architecture.md).

## Optional local notes

Create `.local/game-notes.md` if useful, with the version, which input is known to play correctly, useful saves/routes, and any mods. If modded, identify the base release and changes. The initial compatibility baseline should use an unmodified release where available.
