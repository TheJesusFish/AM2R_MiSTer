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
    r"(?P<label>am2r_upload(?:_ret)?):"
    r"(?P<args>.*)$"
)
ARG = re.compile(r"\b(?P<name>source|bytes|valid|revision)=(?P<value>0x[0-9a-f]+|\d+)")


def percentile(values: list[float], fraction: float) -> float:
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, round(fraction * (len(ordered) - 1)))]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("trace", type=Path)
    args = parser.parse_args()

    starts: dict[str, list[tuple[float, int, int, int, int]]] = defaultdict(list)
    durations: dict[tuple[int, int], list[float]] = defaultdict(list)
    revisions: dict[tuple[int, int], list[int]] = defaultdict(list)
    sources: dict[tuple[int, int], set[int]] = defaultdict(set)

    for line in args.trace.read_text(errors="replace").splitlines():
        match = EVENT.search(line)
        if not match:
            continue
        timestamp = float(match.group("timestamp")) * 1000.0
        pid = match.group("pid")
        if match.group("label").endswith("_ret"):
            if starts[pid]:
                started, size, valid, revision, source = starts[pid].pop()
                if timestamp >= started:
                    key = (size, valid)
                    durations[key].append(timestamp - started)
                    revisions[key].append(revision)
                    sources[key].add(source)
            continue

        values = {
            item.group("name"): int(item.group("value"), 0)
            for item in ARG.finditer(match.group("args"))
        }
        starts[pid].append(
            (
                timestamp,
                values["bytes"],
                values["valid"],
                values["revision"],
                values["source"],
            )
        )

    for (size, valid), values in sorted(durations.items()):
        unique_revisions = len(set(revisions[(size, valid)]))
        print(
            f"bytes={size} revision_valid={valid}: calls={len(values)} "
            f"sources={len(sources[(size, valid)])} revisions={unique_revisions} "
            f"total={sum(values):.3f} ms "
            f"median={statistics.median(values):.4f} ms "
            f"p95={percentile(values, .95):.4f} ms "
            f"p99={percentile(values, .99):.4f} ms "
            f"max={max(values):.4f} ms"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
