#!/usr/bin/env python3
"""Audit AM2R's exact WAD14 function surface against Butterscotch.

The FUNC chunk is authoritative for functions referenced by the compiled game.
This tool intersects it with Butterscotch's explicit stub declarations and can
ask a compatible runner for its unknown-function report.  Reconstructed GML is
used only to identify likely source callers; it is not treated as authoritative
game data.
"""

from __future__ import annotations

import argparse
import pathlib
import re
import struct
import subprocess


ANSI_RE = re.compile(r"\x1b\[[0-9;]*m")
REGISTER_RE = re.compile(r'VM_registerBuiltin\(ctx,\s*"([^"]+)"')
STUB_RE = re.compile(
    r"^\s*STUB_RETURN_(?:ZERO|TRUE|FALSE|UNDEFINED|VALUE)\(\s*([A-Za-z0-9_]+)",
    re.MULTILINE,
)
SEMISTUB_RE = re.compile(r'logSemiStubbedFunction\([^,]+,\s*"([^"]+)"\)')


def chunks(blob: bytes) -> dict[bytes, tuple[int, int]]:
    if blob[:4] != b"FORM":
        raise ValueError("data file does not start with FORM")
    result: dict[bytes, tuple[int, int]] = {}
    offset = 8
    while offset + 8 <= len(blob):
        name = blob[offset : offset + 4]
        length = struct.unpack_from("<I", blob, offset + 4)[0]
        body = offset + 8
        end = body + length
        if end > len(blob):
            raise ValueError(f"chunk {name!r} extends past end of file")
        result[name] = (body, length)
        offset = end
    return result


def c_string(blob: bytes, offset: int) -> str:
    if offset <= 0 or offset >= len(blob):
        raise ValueError(f"invalid string offset 0x{offset:x}")
    end = blob.find(b"\0", offset)
    if end < 0:
        raise ValueError(f"unterminated string at 0x{offset:x}")
    return blob[offset:end].decode("utf-8", errors="replace")


def wad14_function_references(path: pathlib.Path) -> dict[str, int]:
    blob = path.read_bytes()
    table = chunks(blob)
    if b"FUNC" not in table:
        raise ValueError("FUNC chunk is missing")
    body, length = table[b"FUNC"]
    if length % 12:
        raise ValueError(
            f"FUNC length {length} is not a WAD14 flat table (not divisible by 12)"
        )
    functions: dict[str, int] = {}
    for offset in range(body, body + length, 12):
        name_offset, occurrences, _first_address = struct.unpack_from("<III", blob, offset)
        functions[c_string(blob, name_offset)] = occurrences
    return functions


def runner_unknowns(runner: pathlib.Path, data_win: pathlib.Path) -> list[str]:
    proc = subprocess.run(
        [str(runner), str(data_win), "--print-unknown-functions", "--disable-log-colors"],
        check=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    output = ANSI_RE.sub("", proc.stdout)
    return [
        line[2:].strip()
        for line in output.splitlines()
        if line.startswith("- ")
    ]


def source_callers(root: pathlib.Path, names: set[str]) -> dict[str, list[str]]:
    found = {name: [] for name in names}
    patterns = {name: re.compile(rf"\b{re.escape(name)}\s*\(") for name in names}
    for path in root.rglob("*.gml"):
        text = path.read_text(encoding="utf-8", errors="replace")
        rel = path.relative_to(root).as_posix()
        for name, pattern in patterns.items():
            if pattern.search(text):
                found[name].append(rel)
    return found


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("data_win", type=pathlib.Path)
    parser.add_argument("--builtins", required=True, type=pathlib.Path)
    parser.add_argument("--runner", type=pathlib.Path)
    parser.add_argument("--gml-root", type=pathlib.Path)
    args = parser.parse_args()

    references = wad14_function_references(args.data_win)
    source = args.builtins.read_text(encoding="utf-8")
    registered = set(REGISTER_RE.findall(source))
    explicit_stubs = {name for name in STUB_RE.findall(source) if name != "name"}
    semistubs = set(SEMISTUB_RE.findall(source))
    referenced_stubs = sorted(set(references) & explicit_stubs)
    referenced_semistubs = sorted(set(references) & semistubs)
    unknowns = runner_unknowns(args.runner, args.data_win) if args.runner else []

    caller_names = set(referenced_stubs) | set(referenced_semistubs) | set(unknowns)
    callers = source_callers(args.gml_root, caller_names) if args.gml_root else {}

    print(f"compiled function references: {len(references)}")
    print(f"Butterscotch registered builtins: {len(registered)}")
    print(f"referenced explicit stubs: {len(referenced_stubs)}")
    for name in referenced_stubs:
        suffix = f"; callers={','.join(callers.get(name, [])) or 'none-found'}"
        print(f"  STUB {name} ({references[name]} refs{suffix})")
    print(f"referenced semi-stubs: {len(referenced_semistubs)}")
    for name in referenced_semistubs:
        suffix = f"; callers={','.join(callers.get(name, [])) or 'none-found'}"
        print(f"  SEMISTUB {name} ({references[name]} refs{suffix})")
    if args.runner:
        print(f"unknown functions: {len(unknowns)}")
        for name in unknowns:
            suffix = f"; callers={','.join(callers.get(name, [])) or 'none-found'}"
            print(f"  UNKNOWN {name} ({references.get(name, 0)} refs{suffix})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
