#!/usr/bin/env python3
"""Detect temporally isolated horizontal corruption in RGB24 raw video."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("raw", type=Path)
    parser.add_argument("--width", type=int, required=True)
    parser.add_argument("--height", type=int, required=True)
    parser.add_argument("--fps", type=float, required=True)
    parser.add_argument("--start-seconds", type=float, default=0.0)
    parser.add_argument("--pixel-threshold", type=int, default=32)
    args = parser.parse_args()

    frame_bytes = args.width * args.height * 3
    byte_count = args.raw.stat().st_size
    if byte_count % frame_bytes:
        raise ValueError(
            f"{args.raw}: {byte_count} bytes is not an RGB24 frame multiple"
        )
    frame_count = byte_count // frame_bytes
    frames = np.memmap(
        args.raw,
        dtype=np.uint8,
        mode="r",
        shape=(frame_count, args.height, args.width, 3),
    )

    records = []
    for frame in range(1, frame_count):
        previous = frames[frame - 1].astype(np.int16)
        current = frames[frame].astype(np.int16)
        delta = np.max(np.abs(current - previous), axis=2)
        row_energy = np.sum(delta, axis=1, dtype=np.uint64)
        total_energy = int(np.sum(row_energy))
        if total_energy == 0:
            maximum_row_share = 0.0
            top_four_share = 0.0
        else:
            maximum_row_share = float(np.max(row_energy) / total_energy)
            top_four_share = float(np.sum(np.sort(row_energy)[-4:]) / total_energy)
        column_coverage = np.sum(delta >= args.pixel_threshold, axis=1) / args.width
        maximum_coverage = float(np.max(column_coverage))
        changed_pixels = int(np.count_nonzero(delta >= args.pixel_threshold))
        records.append(
            {
                "frame": frame,
                "seconds": args.start_seconds + frame / args.fps,
                "changed_pixels": changed_pixels,
                "maximum_row_share": maximum_row_share,
                "top_four_row_share": top_four_share,
                "maximum_row_column_coverage": maximum_coverage,
            }
        )

    for record in records:
        record["horizontal_artifact_score"] = (
            record["top_four_row_share"]
            * record["maximum_row_column_coverage"]
        )
    worst = sorted(
        records,
        key=lambda record: record["horizontal_artifact_score"],
        reverse=True,
    )[:20]
    flagged = [
        record
        for record in records
        if record["top_four_row_share"] >= 0.20
        and record["maximum_row_column_coverage"] >= 0.25
        and record["changed_pixels"] >= args.width
    ]
    result = {
        "raw": str(args.raw),
        "frame_count": frame_count,
        "duration_seconds": frame_count / args.fps,
        "pixel_threshold": args.pixel_threshold,
        "flagged_frame_count": len(flagged),
        "flagged_frames": flagged[:100],
        "worst_frames": worst,
    }
    print(json.dumps(result, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
