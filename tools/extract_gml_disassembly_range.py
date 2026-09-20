#!/usr/bin/env python3
"""Print one address range from Butterscotch's GameMaker disassembly."""

from __future__ import annotations

import argparse
import re
import subprocess
from pathlib import Path


ADDRESS = re.compile(r"^\s*(?P<address>[0-9A-Fa-f]{4})\s+\(")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("runner", type=Path)
    parser.add_argument("data_win", type=Path)
    parser.add_argument("code")
    parser.add_argument("start", type=lambda value: int(value, 0))
    parser.add_argument("end", type=lambda value: int(value, 0))
    args = parser.parse_args()

    result = subprocess.run(
        [str(args.runner), str(args.data_win), "--disassemble", args.code],
        check=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        errors="replace",
    )
    for line in result.stdout.splitlines():
        match = ADDRESS.match(line)
        if match and args.start <= int(match.group("address"), 16) < args.end:
            print(line)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
