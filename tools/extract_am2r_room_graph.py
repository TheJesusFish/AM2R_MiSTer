#!/usr/bin/env python3
"""Extract AM2R room-transition edges from Butterscotch text reports."""

from __future__ import annotations

import argparse
import collections
import re
from pathlib import Path


ROOM = re.compile(r"^\[(\d+)\]\s+(\S+)\s+\(")
CODE = re.compile(r"^=== gml_RoomCC_(.+)_\d+_Create \(")
PUSH_INT = re.compile(r"PushI\.e\s+(-?\d+)\b")
TARGET = re.compile(r"Pop\.v\.i\s+self\.targetroom\b")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("rooms", type=Path)
    parser.add_argument("disassembly", type=Path)
    parser.add_argument("--source", type=int)
    parser.add_argument("--target", type=int)
    args = parser.parse_args()

    names: dict[int, str] = {}
    indices: dict[str, int] = {}
    for line in args.rooms.read_text(errors="replace").splitlines():
        match = ROOM.match(line)
        if match:
            index = int(match.group(1))
            names[index] = match.group(2)
            indices[match.group(2)] = index

    edges: dict[int, set[int]] = collections.defaultdict(set)
    current_room: str | None = None
    last_integer: int | None = None
    for line in args.disassembly.read_text(errors="replace").splitlines():
        match = CODE.match(line)
        if match:
            current_room = match.group(1)
            last_integer = None
            continue
        match = PUSH_INT.search(line)
        if match:
            last_integer = int(match.group(1))
        if current_room is not None and last_integer is not None and TARGET.search(line):
            source = indices.get(current_room)
            if source is not None and last_integer in names:
                edges[source].add(last_integer)
            last_integer = None

    if args.source is None or args.target is None:
        for source in sorted(edges):
            destinations = ", ".join(
                f"{target}:{names[target]}" for target in sorted(edges[source])
            )
            print(f"{source}:{names[source]} -> {destinations}")
        return 0

    queue = collections.deque([args.source])
    previous: dict[int, int | None] = {args.source: None}
    while queue:
        source = queue.popleft()
        if source == args.target:
            break
        for target in sorted(edges.get(source, ())):
            if target not in previous:
                previous[target] = source
                queue.append(target)
    if args.target not in previous:
        raise SystemExit(
            f"no directed transition path from {args.source}:{names.get(args.source)} "
            f"to {args.target}:{names.get(args.target)}"
        )

    path = []
    cursor: int | None = args.target
    while cursor is not None:
        path.append(cursor)
        cursor = previous[cursor]
    path.reverse()
    print(" -> ".join(f"{index}:{names[index]}" for index in path))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
