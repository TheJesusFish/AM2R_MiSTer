#!/usr/bin/env python3
"""Summarize synchronized AM2R process-memory and capture motion traces."""

from __future__ import annotations

import argparse
import csv
from pathlib import Path


def percentile(values: list[float], quantile: float) -> float:
    ordered = sorted(values)
    index = min(len(ordered) - 1, round(quantile * (len(ordered) - 1)))
    return ordered[index]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("samples", type=Path)
    parser.add_argument("capture", type=Path)
    parser.add_argument("--capture-start", type=int, default=0)
    parser.add_argument("--capture-end", type=int)
    parser.add_argument("--dump-start", type=int)
    parser.add_argument("--dump-end", type=int)
    parser.add_argument("--game-dump-start-ms", type=float)
    parser.add_argument("--game-dump-end-ms", type=float)
    args = parser.parse_args()

    sample_rows = list(csv.DictReader(args.samples.open(newline="")))
    frames: list[tuple[float, int, float, float, float, float]] = []
    previous_frame = None
    for row in sample_rows:
        frame = int(row["frame"])
        if frame == previous_frame:
            continue
        frames.append(
            (
                int(row["elapsed_ns"]) / 1_000_000.0,
                frame,
                float(row["character_x"]),
                float(row["character_y"]),
                float(row["camera_x"]),
                float(row["camera_y"]),
            )
        )
        previous_frame = frame

    intervals = [frames[index][0] - frames[index - 1][0] for index in range(1, len(frames))]
    print(f"samples={len(sample_rows)} distinct_frames={len(frames)}")
    print(
        "frame_interval_ms "
        f"min={min(intervals):.3f} median={percentile(intervals, .5):.3f} "
        f"p95={percentile(intervals, .95):.3f} p99={percentile(intervals, .99):.3f} "
        f"max={max(intervals):.3f} over18={sum(value > 18 for value in intervals)} "
        f"over20={sum(value > 20 for value in intervals)}"
    )

    first_motion = next(
        index
        for index in range(1, len(frames))
        if frames[index][2:6] != frames[index - 1][2:6]
    )
    print(
        f"first_motion elapsed_ms={frames[first_motion][0]:.3f} "
        f"frame={frames[first_motion][1]} values={frames[first_motion][2:]}"
    )
    for row in frames[max(0, first_motion - 2): first_motion + 10]:
        print("game", *(f"{value:.6f}" if isinstance(value, float) else value for value in row))
    if args.game_dump_start_ms is not None:
        game_dump_end = (
            args.game_dump_end_ms
            if args.game_dump_end_ms is not None
            else args.game_dump_start_ms + 1_000.0
        )
        for row in frames:
            if args.game_dump_start_ms <= row[0] < game_dump_end:
                print(
                    "game_detail",
                    *(f"{value:.6f}" if isinstance(value, float) else value for value in row),
                )

    capture_rows = list(csv.DictReader(args.capture.open(newline="")))
    capture_rows = [
        row
        for row in capture_rows
        if int(row["capture_frame"]) >= args.capture_start
        and (args.capture_end is None or int(row["capture_frame"]) < args.capture_end)
    ]
    runs: list[tuple[int, int]] = []
    run_start = None
    previous_number = None
    for row in capture_rows:
        number = int(row["capture_frame"])
        moving = int(row["background_dx"]) != 0 or int(row.get("background_dy", 0)) != 0
        if moving and run_start is None:
            run_start = number
        if not moving and run_start is not None:
            if previous_number is not None and previous_number - run_start + 1 >= 2:
                runs.append((run_start, previous_number))
            run_start = None
        previous_number = number
    if run_start is not None and previous_number is not None:
        runs.append((run_start, previous_number))
    print("capture_motion_runs", runs[:30])

    if args.dump_start is not None:
        dump_end = args.dump_end if args.dump_end is not None else args.dump_start + 180
        for row in capture_rows:
            number = int(row["capture_frame"])
            if args.dump_start <= number < dump_end:
                print(
                    "capture",
                    number,
                    row["background_dx"],
                    row.get("background_dy", ""),
                    row.get("x_score", row.get("edge_score", "")),
                    row.get("x_margin", row.get("score_margin", "")),
                )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
