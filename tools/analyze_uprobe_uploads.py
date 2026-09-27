#!/usr/bin/env python3
"""Summarize uploadDynamicTexture timings by upload size and revision mode."""

from __future__ import annotations

import argparse
import re
import statistics
from collections import defaultdict
from pathlib import Path


EVENT = re.compile(
    r"-(?P<pid>\d+)\s+\[[^]]+\].*?\s"
    r"(?P<timestamp>\d+\.\d+):\s+"
    r"(?P<label>am2r_upload(?:_(?:static|sparse|crop))?(?:_ret)?):"
    r"(?P<args>.*)$"
)
ARG = re.compile(r"\b(?P<name>source|bytes|valid|revision|source_row_bytes|crop_row_bytes|rows)=(?P<value>0x[0-9a-f]+|\d+)")


def percentile(values: list[float], fraction: float) -> float:
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, round(fraction * (len(ordered) - 1)))]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("trace", type=Path)
    args = parser.parse_args()

    starts: dict[tuple[str, str], list[tuple[float, int, int, int, int]]] = defaultdict(list)
    durations: dict[tuple[str, int, int], list[float]] = defaultdict(list)
    revisions: dict[tuple[str, int, int], list[int]] = defaultdict(list)
    sources: dict[tuple[str, int, int], set[int]] = defaultdict(set)

    for line in args.trace.read_text(errors="replace").splitlines():
        match = EVENT.search(line)
        if not match:
            continue
        timestamp = float(match.group("timestamp")) * 1000.0
        label = match.group("label")
        kind = label.removesuffix("_ret")
        key_thread = (match.group("pid"), kind)
        if label.endswith("_ret"):
            if starts[key_thread]:
                started, size, valid, revision, source = starts[key_thread].pop()
                if timestamp >= started:
                    key = (kind, size, valid)
                    durations[key].append(timestamp - started)
                    revisions[key].append(revision)
                    sources[key].add(source)
            continue

        values = {
            item.group("name"): int(item.group("value"), 0)
            for item in ARG.finditer(match.group("args"))
        }
        size = values.get("bytes", values.get("crop_row_bytes", 0) * values.get("rows", 0))
        starts[key_thread].append(
            (
                timestamp,
                size,
                values.get("valid", 0),
                values.get("revision", 0),
                values["source"],
            )
        )

    for (kind, size, valid), values in sorted(durations.items()):
        unique_revisions = len(set(revisions[(kind, size, valid)]))
        print(
            f"kind={kind} bytes={size} revision_valid={valid}: calls={len(values)} "
            f"sources={len(sources[(kind, size, valid)])} revisions={unique_revisions} "
            f"total={sum(values):.3f} ms "
            f"median={statistics.median(values):.4f} ms "
            f"p95={percentile(values, .95):.4f} ms "
            f"p99={percentile(values, .99):.4f} ms "
            f"max={max(values):.4f} ms"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
