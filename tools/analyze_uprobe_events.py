#!/usr/bin/env python3
"""Aggregate resolved GML-event duration by object, code, and event tuple."""

from __future__ import annotations

import argparse
import re
import statistics
from collections import defaultdict
from pathlib import Path


HEADER = re.compile(
    r"(?P<task>\S+)-(?P<pid>\d+)\s+\[[^]]+\].*?\s"
    r"(?P<timestamp>\d+\.\d+):\s+(?P<label>resolved_event(?:_ret)?):"
)
FIELD = re.compile(r"\b(object|event|subtype|code|owner)=(-?\d+)")


def percentile(values: list[float], fraction: float) -> float:
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, round(fraction * (len(ordered) - 1)))]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("trace", type=Path)
    parser.add_argument("--limit", type=int, default=40)
    parser.add_argument("--start-ms", type=float, default=0.0)
    parser.add_argument("--end-ms", type=float, default=float("inf"))
    args = parser.parse_args()

    stacks: dict[str, list[tuple[float, tuple[int, ...]]]] = defaultdict(list)
    durations: dict[tuple[int, ...], list[float]] = defaultdict(list)
    trace_start: float | None = None
    for line in args.trace.read_text(errors="replace").splitlines():
        match = HEADER.search(line)
        if not match:
            continue
        timestamp = float(match.group("timestamp")) * 1000.0
        if trace_start is None:
            trace_start = timestamp
        pid = match.group("pid")
        if match.group("label").endswith("_ret"):
            if stacks[pid]:
                started, key = stacks[pid].pop()
                elapsed = timestamp - trace_start
                if timestamp >= started and args.start_ms <= elapsed < args.end_ms:
                    durations[key].append(timestamp - started)
            continue
        fields = {name: int(value) for name, value in FIELD.findall(line)}
        if all(name in fields for name in ("object", "event", "subtype", "code", "owner")):
            key = tuple(fields[name] for name in ("object", "event", "subtype", "code", "owner"))
            stacks[pid].append((timestamp, key))

    rows = []
    for key, values in durations.items():
        rows.append((sum(values), key, values))
    for total, key, values in sorted(rows, reverse=True)[: args.limit]:
        obj, event, subtype, code, owner = key
        print(
            f"object={obj} event={event}:{subtype} code={code} owner={owner} "
            f"calls={len(values)} total={total:.3f}ms "
            f"median={statistics.median(values):.4f}ms "
            f"p95={percentile(values, .95):.4f}ms max={max(values):.4f}ms"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
