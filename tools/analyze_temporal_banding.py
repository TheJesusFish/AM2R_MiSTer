#!/usr/bin/env python3
"""Rank captured frames by broad vertical temporal-phase discontinuities."""

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
    parser.add_argument("--top", type=int, default=24)
    parser.add_argument("--bottom", type=int, default=232)
    parser.add_argument("--span", type=int, default=24)
    args = parser.parse_args()

    frame_bytes = args.width * args.height * 3
    byte_count = args.raw.stat().st_size
    if byte_count % frame_bytes:
        raise ValueError("raw byte count is not an RGB24 frame multiple")
    frame_count = byte_count // frame_bytes
    frames = np.memmap(
        args.raw,
        dtype=np.uint8,
        mode="r",
        shape=(frame_count, args.height, args.width, 3),
    )
    span = args.span
    records = []
    for frame in range(1, frame_count - 1):
        previous = frames[frame - 1, args.top : args.bottom].astype(np.int16)
        current = frames[frame, args.top : args.bottom].astype(np.int16)
        following = frames[frame + 1, args.top : args.bottom].astype(np.int16)
        previous_error = np.mean(np.abs(current - previous), axis=(0, 2))
        following_error = np.mean(np.abs(current - following), axis=(0, 2))
        total = previous_error + following_error
        phase = np.divide(
            previous_error - following_error,
            total,
            out=np.zeros_like(total),
            where=total >= 1.0,
        )
        activity = np.minimum(1.0, total / 16.0)
        weighted_phase = phase * activity
        kernel = np.ones(span, dtype=np.float64) / span
        smooth = np.convolve(weighted_phase, kernel, mode="valid")
        # Compare equal-width regions on either side of every possible seam.
        seam_delta = np.abs(smooth[span:] - smooth[:-span])
        if seam_delta.size:
            seam_offset = int(np.argmax(seam_delta))
            seam_score = float(seam_delta[seam_offset])
            seam_x = seam_offset + span
        else:
            seam_score = 0.0
            seam_x = 0
        records.append(
            {
                "frame": frame,
                "seconds": args.start_seconds + frame / args.fps,
                "seam_x": seam_x,
                "score": seam_score,
                "mean_activity": float(np.mean(activity)),
            }
        )

    worst = sorted(records, key=lambda item: item["score"], reverse=True)[:20]
    scores = np.array([item["score"] for item in records], dtype=np.float64)
    result = {
        "raw": str(args.raw),
        "frame_count": frame_count,
        "duration_seconds": frame_count / args.fps,
        "score_p50": float(np.percentile(scores, 50)) if scores.size else 0.0,
        "score_p95": float(np.percentile(scores, 95)) if scores.size else 0.0,
        "score_p99": float(np.percentile(scores, 99)) if scores.size else 0.0,
        "worst_frames": worst,
    }
    print(json.dumps(result, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
