#!/usr/bin/env python3
"""Summarize AM2R ARM/GPU/scanout event timing from a pacing CSV."""

from __future__ import annotations

import argparse
import bisect
import csv
import statistics
from collections import Counter
from pathlib import Path


def percentile(values: list[float], quantile: float) -> float:
    ordered = sorted(values)
    index = min(len(ordered) - 1, round(quantile * (len(ordered) - 1)))
    return ordered[index]


def describe(label: str, values: list[float]) -> None:
    if not values:
        print(f"{label}: no samples")
        return
    print(
        f"{label}: n={len(values)} min={min(values):.3f} "
        f"median={statistics.median(values):.3f} "
        f"p95={percentile(values, .95):.3f} "
        f"p99={percentile(values, .99):.3f} max={max(values):.3f} ms"
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("trace", type=Path)
    parser.add_argument("--start-ms", type=float, default=0.0)
    parser.add_argument("--end-ms", type=float)
    parser.add_argument("--anomalies", action="store_true")
    args = parser.parse_args()

    rows = list(csv.DictReader(args.trace.open(newline="")))
    samples: list[dict[str, int]] = []
    for row in rows:
        parsed = {key: int(float(value)) for key, value in row.items()}
        elapsed_ms = parsed["elapsed_ns"] / 1_000_000.0
        if elapsed_ms < args.start_ms:
            continue
        if args.end_ms is not None and elapsed_ms >= args.end_ms:
            continue
        samples.append(parsed)
    if len(samples) < 2:
        raise SystemExit("trace window has fewer than two samples")

    def changes(field: str, allow_zero: bool = True) -> list[tuple[float, int]]:
        result: list[tuple[float, int]] = []
        previous = samples[0][field]
        for row in samples[1:]:
            value = row[field]
            if value != previous and (allow_zero or value != 0):
                result.append((row["elapsed_ns"] / 1_000_000.0, value))
            previous = value
        return result

    heartbeats = changes("vblank")
    game_frames = changes("frame")
    submissions = changes("submitted", allow_zero=False)
    completions = changes("completed", allow_zero=False)
    scanouts = changes("scanout_frame")
    natives = changes("native_frame")
    heartbeat_times = [event[0] for event in heartbeats]

    describe("heartbeat interval", [b[0] - a[0] for a, b in zip(heartbeats, heartbeats[1:])])
    describe("game-frame interval", [b[0] - a[0] for a, b in zip(game_frames, game_frames[1:])])
    describe("submission interval", [b[0] - a[0] for a, b in zip(submissions, submissions[1:])])
    describe("completion interval", [b[0] - a[0] for a, b in zip(completions, completions[1:])])

    def phases(events: list[tuple[float, int]]) -> list[float]:
        result: list[float] = []
        for timestamp, _ in events:
            index = bisect.bisect_right(heartbeat_times, timestamp) - 1
            if index >= 0:
                result.append(timestamp - heartbeat_times[index])
        return result

    describe("game-frame phase after heartbeat", phases(game_frames))
    describe("submission phase after heartbeat", phases(submissions))
    describe("completion phase after heartbeat", phases(completions))
    describe("scanout-detail phase after heartbeat", phases(scanouts))

    submitted_at = {sequence: timestamp for timestamp, sequence in submissions}
    render_times = [
        timestamp - submitted_at[sequence]
        for timestamp, sequence in completions
        if sequence in submitted_at and timestamp >= submitted_at[sequence]
    ]
    describe("submit-to-complete", render_times)

    for label, events in (("heartbeat", heartbeats), ("game", game_frames),
                          ("submit", submissions), ("complete", completions),
                          ("scanout", scanouts), ("native", natives)):
        deltas = Counter(b[1] - a[1] for a, b in zip(events, events[1:]))
        print(f"{label} value deltas: {dict(sorted(deltas.items()))}")

    # Sampled detail values are stable for a full raster, so group their change
    # at each heartbeat and report whether scanout repeated or skipped a frame.
    heartbeat_rows: list[dict[str, int]] = []
    previous_vblank = samples[0]["vblank"]
    for row in samples[1:]:
        if row["vblank"] != previous_vblank:
            heartbeat_rows.append(row)
            previous_vblank = row["vblank"]
    scanout_deltas = Counter(
        b["scanout_frame"] - a["scanout_frame"]
        for a, b in zip(heartbeat_rows, heartbeat_rows[1:])
    )
    native_deltas = Counter(
        b["native_frame"] - a["native_frame"]
        for a, b in zip(heartbeat_rows, heartbeat_rows[1:])
    )
    print(f"per-heartbeat scanout deltas: {dict(sorted(scanout_deltas.items()))}")
    print(f"per-heartbeat native deltas: {dict(sorted(native_deltas.items()))}")
    if args.anomalies:
        for before, after in zip(heartbeat_rows, heartbeat_rows[1:]):
            delta = after["scanout_frame"] - before["scanout_frame"]
            if delta != 1:
                print(
                    "scanout anomaly "
                    f"t={after['elapsed_ns'] / 1_000_000.0:.3f} ms "
                    f"delta={delta} frame={after['frame']} "
                    f"character=({after['character_x']},{after['character_y']}) "
                    f"camera=({after['camera_x']},{after['camera_y']})"
                )

    completion_times = [event[0] for event in completions]
    if len(heartbeat_times) >= 3 and completion_times:
        print("simulated completions per display boundary:")
        for half_ms in range(0, 34):
            offset = half_ms / 2.0
            counts: Counter[int] = Counter()
            for first, second in zip(heartbeat_times[1:-1], heartbeat_times[2:]):
                begin = first + offset
                end = second + offset
                count = bisect.bisect_left(completion_times, end) - bisect.bisect_left(
                    completion_times, begin
                )
                counts[count] += 1
            print(f"  offset={offset:4.1f} ms {dict(sorted(counts.items()))}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
