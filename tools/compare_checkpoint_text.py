"""Rank local ARM runner artifacts against a dumped DMTCP text mapping."""

from __future__ import annotations

import argparse
import pathlib


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("dump", type=pathlib.Path)
    parser.add_argument("root", type=pathlib.Path)
    args = parser.parse_args()

    expected = args.dump.read_bytes()
    file_offset = 0x1C000
    candidates: set[pathlib.Path] = set()
    for directory in ("build", "artifacts", "work"):
        base = args.root / directory
        if not base.exists():
            continue
        for path in base.rglob("butterscotch*"):
            if path.is_file() and not path.suffix.lower() in {
                ".c", ".cpp", ".h", ".log", ".wav", ".zip", ".pdb"
            }:
                candidates.add(path)

    results = []
    for path in candidates:
        try:
            with path.open("rb") as source:
                source.seek(file_offset)
                candidate = source.read(len(expected))
        except OSError:
            continue
        if len(candidate) != len(expected):
            continue
        differences = sum(left != right for left, right in zip(expected, candidate))
        results.append((differences, path.stat().st_size, path))

    for differences, size, path in sorted(results)[:30]:
        print("CHECKPOINT_TEXT differences=%d size=%d path=%s" % (
            differences, size, path
        ))
    return 0 if results else 1


if __name__ == "__main__":
    raise SystemExit(main())
