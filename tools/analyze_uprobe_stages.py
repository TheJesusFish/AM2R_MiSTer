#!/usr/bin/env python3
"""Summarize paired AM2R uprobe stage timings from a tracefs capture."""

from __future__ import annotations

import argparse
import re
import statistics
from collections import defaultdict
from pathlib import Path


EVENT = re.compile(
    r"\s(?P<timestamp>\d+\.\d+):\s+"
    r"(?:am2r_)?(?P<label>step|step_ret|draw|draw_ret|present|present_ret|wait|wait_ret):"
)


def percentile(values: list[float], fraction: float) -> float:
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, round(fraction * (len(ordered) - 1)))]


def describe(label: str, values: list[float]) -> None:
    if not values:
        print(f"{label}: no samples")
        return
    print(
        f"{label}: n={len(values)} min={min(values):.3f} "
        f"median={statistics.median(values):.3f} "
        f"p95={percentile(values, .95):.3f} "
        f"p99={percentile(values, .99):.3f} "
        f"max={max(values):.3f} ms "
        f">16ms={sum(value > 16.0 for value in values)}"
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("trace", type=Path)
    args = parser.parse_args()

    events: list[tuple[float, str]] = []
    starts: dict[str, float] = {}
    durations: dict[str, list[float]] = defaultdict(list)
    step_starts: list[float] = []
    for line in args.trace.read_text(errors="replace").splitlines():
        match = EVENT.search(line)
        if not match:
            continue
        timestamp = float(match.group("timestamp")) * 1000.0
        label = match.group("label")
        events.append((timestamp, label))
        if label.endswith("_ret"):
            stage = label[:-4]
            started = starts.pop(stage, None)
            if started is not None and timestamp >= started:
                durations[stage].append(timestamp - started)
        else:
            starts[label] = timestamp
            if label == "step":
                step_starts.append(timestamp)

    describe(
        "step-start interval",
        [second - first for first, second in zip(step_starts, step_starts[1:])],
    )
    for stage in ("step", "draw", "present", "wait"):
        describe(stage, durations[stage])

    frames: list[dict[str, float]] = []
    frame: dict[str, float] | None = None
    stage_starts: dict[str, float] = {}
    for timestamp, label in events:
        if label == "step":
            if frame is not None:
                frame["cycle"] = timestamp - frame["start"]
                frames.append(frame)
            frame = {"start": timestamp}
            stage_starts = {"step": timestamp}
            continue
        if frame is None:
            continue
        if label.endswith("_ret"):
            stage = label[:-4]
            started = stage_starts.pop(stage, None)
            if started is not None:
                frame[stage] = timestamp - started
        else:
            stage_starts[label] = timestamp
            if label == "wait":
                frame["work"] = timestamp - frame["start"]

    describe("work before wait", [item["work"] for item in frames if "work" in item])
    cycle_values = [item["cycle"] for item in frames if "cycle" in item]
    print(
        "slow cycles: "
        f">20ms={sum(value > 20.0 for value in cycle_values)} "
        f">25ms={sum(value > 25.0 for value in cycle_values)} "
        f">30ms={sum(value > 30.0 for value in cycle_values)}"
    )
    for item in sorted(frames, key=lambda value: value.get("cycle", 0), reverse=True)[:20]:
        print(
            "slow cycle "
            f"cycle={item.get('cycle', 0):.3f} "
            f"work={item.get('work', 0):.3f} "
            f"step={item.get('step', 0):.3f} "
            f"draw={item.get('draw', 0):.3f} "
            f"present={item.get('present', 0):.3f} "
            f"wait={item.get('wait', 0):.3f}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
