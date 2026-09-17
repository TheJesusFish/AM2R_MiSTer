#!/usr/bin/env python3
"""Static regression checks for the AM2R save-state storage contract."""

from __future__ import annotations

import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
WRAPPER = ROOT / "src" / "hps-wrapper" / "am2r_wrapper.cpp"


def require(source: str, marker: str) -> None:
    if marker not in source:
        raise SystemExit(f"missing save-state contract: {marker}")


def main() -> None:
    source = WRAPPER.read_text(encoding="utf-8")

    for contract in (
        'constexpr const char *kStateRoot = "/media/fat/savestates/AM2R";',
        "constexpr uint64_t kCheckpointFreeSpaceFloor = 256ull * 1024ull * 1024ull;",
        "statvfs(kStateRoot, &filesystem)",
        '"savestate_space_insufficient slot=%d available=%llu required=%llu"',
        "int result = error ? -1 : dmtcp_command(state, \"--bcheckpoint\", log);",
        "release_quiesced_runner(error, log);",
        '"%s/.session-%d", kStateRoot, (int)getpid()',
        "bool renamed = rename(source, temporary) == 0;",
        'rename(temporary, destination) != 0',
        '"savestate_committed slot=%d bytes=%lld install=%s path=%s"',
        '"savestate_save_complete slot=%d elapsed_ms=%.1f result=%d"',
        '"am2r-state-v30-main-915ca339-dmtcp-3.2.0-mister1"',
    ):
        require(source, contract)

    session_assignment = re.search(
        r"snprintf\(state\.sessionDirectory,.*?;", source, re.DOTALL
    )
    if session_assignment is None:
        raise SystemExit("missing save-state contract: session directory assignment")
    if "/dev/shm" in session_assignment.group(0):
        raise SystemExit("save-state session regressed to memory-backed staging")

    checkpoint = source[source.index("bool checkpoint_slot(") :]
    checkpoint = checkpoint[: checkpoint.index("int create_state_socket(")]
    space_check = checkpoint.index("statvfs(kStateRoot")
    dmtcp_call = checkpoint.index('dmtcp_command(state, "--bcheckpoint"')
    release = checkpoint.index("release_quiesced_runner(error, log)")
    if not space_check < dmtcp_call < release:
        raise SystemExit("save-state space-check/quiesce ordering changed")

    print(
        "AM2R save-state audit passed: disk staging, 256 MiB preflight, "
        "atomic slot publication, runner release, and v30 compatibility retained"
    )


if __name__ == "__main__":
    main()
