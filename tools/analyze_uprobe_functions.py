#!/usr/bin/env python3
"""Aggregate paired function durations from a tracefs uprobe capture."""

from __future__ import annotations

import argparse
import re
import statistics
from collections import defaultdict
from pathlib import Path


EVENT = re.compile(
    r"(?P<task>\S+)-(?P<pid>\d+)\s+\[[^]]+\].*?\s"
    r"(?P<timestamp>\d+\.\d+):\s+"
    r"(?P<label>[A-Za-z0-9_]+):"
)


def percentile(values: list[float], fraction: float) -> float:
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, round(fraction * (len(ordered) - 1)))]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("trace", type=Path)
    args = parser.parse_args()

    starts: dict[tuple[str, str], list[float]] = defaultdict(list)
    durations: dict[str, list[float]] = defaultdict(list)
    counts: dict[str, int] = defaultdict(int)
    for line in args.trace.read_text(errors="replace").splitlines():
        match = EVENT.search(line)
        if not match:
            continue
        timestamp = float(match.group("timestamp")) * 1000.0
        label = match.group("label")
        pid = match.group("pid")
        if label.endswith("_ret"):
            function = label[:-4]
            key = (pid, function)
            if starts[key]:
                started = starts[key].pop()
                if timestamp >= started:
                    durations[function].append(timestamp - started)
        else:
            counts[label] += 1
            starts[(pid, label)].append(timestamp)

    for function in sorted(counts):
        values = durations[function]
        if not values:
            print(f"{function}: calls={counts[function]} no paired returns")
            continue
        print(
            f"{function}: calls={counts[function]} paired={len(values)} "
            f"total={sum(values):.3f} ms "
            f"median={statistics.median(values):.4f} ms "
            f"p95={percentile(values, .95):.4f} ms "
            f"p99={percentile(values, .99):.4f} ms "
            f"max={max(values):.4f} ms"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
