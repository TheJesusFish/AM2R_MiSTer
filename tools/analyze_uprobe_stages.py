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
    parser.add_argument("--bin-ms", type=float, default=0.0)
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
                if "wait_start" in frame and "wait" not in frame:
                    frame["wait"] = timestamp - frame["wait_start"]
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
            # Entry-only uprobes avoid uretprobe nesting failures on old
            # MiSTer kernels.  Consecutive main-loop stage entries still give
            # useful inclusive timings, and paired traces overwrite these
            # estimates with their exact return-probe durations below.
            if label == "draw" and "step" not in frame:
                frame["step"] = timestamp - frame["start"]
            elif label == "present":
                draw_started = stage_starts.get("draw")
                if draw_started is not None and "draw" not in frame:
                    frame["draw"] = timestamp - draw_started
            if label == "wait":
                frame["work"] = timestamp - frame["start"]
                frame["wait_start"] = timestamp
                present_started = stage_starts.get("present")
                if present_started is not None and "present" not in frame:
                    frame["present"] = timestamp - present_started

    describe("work before wait", [item["work"] for item in frames if "work" in item])
    cycle_values = [item["cycle"] for item in frames if "cycle" in item]
    trace_start = frames[0]["start"] if frames else 0.0
    print(
        "slow cycles: "
        f">20ms={sum(value > 20.0 for value in cycle_values)} "
        f">25ms={sum(value > 25.0 for value in cycle_values)} "
        f">30ms={sum(value > 30.0 for value in cycle_values)}"
    )
    for item in sorted(frames, key=lambda value: value.get("cycle", 0), reverse=True)[:20]:
        print(
            "slow cycle "
            f"t={item.get('start', trace_start) - trace_start:.3f} "
            f"cycle={item.get('cycle', 0):.3f} "
            f"work={item.get('work', 0):.3f} "
            f"step={item.get('step', 0):.3f} "
            f"draw={item.get('draw', 0):.3f} "
            f"present={item.get('present', 0):.3f} "
            f"wait={item.get('wait', 0):.3f}"
        )
    if args.bin_ms > 0 and frames:
        bins: dict[int, list[dict[str, float]]] = defaultdict(list)
        for item in frames:
            bins[int((item["start"] - trace_start) // args.bin_ms)].append(item)
        print("time bins:")
        for index in sorted(bins):
            items = bins[index]
            cycles = [item["cycle"] for item in items if "cycle" in item]
            work = [item["work"] for item in items if "work" in item]
            steps = [item["step"] for item in items if "step" in item]
            draws = [item["draw"] for item in items if "draw" in item]
            print(
                f"t={index * args.bin_ms:.0f}-{(index + 1) * args.bin_ms:.0f}ms "
                f"frames={len(items)} slow={sum(value > 25.0 for value in cycles)} "
                f"work_med={statistics.median(work):.3f} "
                f"step_med={statistics.median(steps):.3f} "
                f"draw_med={statistics.median(draws):.3f}"
            )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
