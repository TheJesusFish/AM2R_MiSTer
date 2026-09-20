#!/usr/bin/env python3
"""Static regression checks for the AM2R logical save-state contract."""

from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
PATCH = ROOT / "patches" / "butterscotch-logical-savestates.patch"
WRAPPER = ROOT / "src" / "hps-wrapper" / "am2r_wrapper.cpp"


def require(source: str, marker: str) -> None:
    if marker not in source:
        raise SystemExit(f"missing logical save-state contract: {marker}")


def main() -> None:
    patch = PATCH.read_text(encoding="utf-8")
    wrapper = WRAPPER.read_text(encoding="utf-8")

    for contract in (
        "#define FAST_STATE_VERSION 4u",
        '#define FAST_STATE_PATH "/media/fat/savestates/AM2R"',
        'memcpy(header.magic, "AM2RFST", 7);',
        "header.payloadCrc32 = crc32Bytes",
        "header.dataFileSize = runner->dataWin->fileSize;",
        "header.gameId = runner->dataWin->gen8.gameID;",
        "header.licenseCrc32 = runner->dataWin->gen8.licenseCRC32;",
        "header.codeCount = runner->dataWin->code.count;",
        "header.spriteCount = runner->dataWin->sprt.count;",
        'snprintf(target, size, FAST_STATE_PATH "/slot%d.fast%s"',
        'statePath(temporary, sizeof(temporary), slot, ".new");',
        "if (fsync(fd) != 0 || close(fd) != 0)",
        "if (rename(temporary, path) != 0)",
        "Runner_beginFastStateRestore(runner, roomIndex)",
        "Runner_finishFastStateRestore(runner);",
        "writeDataStructures(writer, runner)",
        "writeOpenTextFiles(writer, runner)",
        "writeFileFindState(writer, runner)",
        "writeSurfaces(writer, runner)",
        "writeAudio(writer, runner)",
        "MaAudioSystem_prepareLogicalSave",
        "MaAudioSystem_finishLogicalSave",
        "AM2R_STATE_FAST_SAVE_COMPLETE",
        "AM2R_STATE_FAST_LOAD_COMPLETE",
    ):
        require(patch, contract)

    validation = patch.index("header.payloadCrc32 != crc32Bytes")
    mutation = patch.index("Runner_beginFastStateRestore(runner, roomIndex)")
    if validation >= mutation:
        raise SystemExit("logical state validation no longer precedes runner mutation")

    for contract in (
        "case AM2R_STATE_FAST_SAVE_COMPLETE:",
        "case AM2R_STATE_FAST_LOAD_COMPLETE:",
        'InfoMessage("Save state written"',
        'InfoMessage("Save state loaded"',
        'message.error == EBADMSG ? "Save state is corrupt or incompatible"',
    ):
        require(wrapper, contract)

    print(
        "AM2R logical save-state audit passed: versioned data fingerprint, "
        "payload CRC, validation-before-mutation, atomic publication, complete "
        "runner/renderer/audio coverage, and OSD completion handling retained"
    )


if __name__ == "__main__":
    main()
