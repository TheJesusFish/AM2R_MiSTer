#!/usr/bin/env python3
"""Measure relative input-poll-to-game-motion latency in an RGB24 capture."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np


def parse_rect(value: str) -> tuple[int, int, int, int]:
    parts = tuple(int(part) for part in value.split(","))
    if len(parts) != 4:
        raise argparse.ArgumentTypeError("rectangle must be x,y,width,height")
    return parts


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("raw", type=Path)
    parser.add_argument("--width", type=int, required=True)
    parser.add_argument("--height", type=int, required=True)
    parser.add_argument("--fps", type=float, required=True)
    parser.add_argument("--marker", type=parse_rect, default=(0, 80, 8, 16))
    parser.add_argument("--subject", type=parse_rect, required=True)
    parser.add_argument("--pixel-threshold", type=int, default=40)
    parser.add_argument("--lookahead", type=int, default=10)
    args = parser.parse_args()

    frame_bytes = args.width * args.height * 3
    byte_count = args.raw.stat().st_size
    if byte_count % frame_bytes:
        raise ValueError(f"{args.raw}: incomplete RGB24 frame")
    frame_count = byte_count // frame_bytes
    frames = np.memmap(
        args.raw,
        dtype=np.uint8,
        mode="r",
        shape=(frame_count, args.height, args.width, 3),
    )

    mx, my, mw, mh = args.marker
    marker_mean = np.mean(frames[:, my : my + mh, mx : mx + mw], axis=(1, 2, 3))
    marker_on = marker_mean >= 160.0
    rising_edges = np.flatnonzero(marker_on & ~np.r_[False, marker_on[:-1]])

    sx, sy, sw, sh = args.subject
    records = []
    for edge_value in rising_edges:
        edge = int(edge_value)
        if edge < 3 or edge + args.lookahead >= frame_count:
            continue
        baseline = np.median(
            frames[edge - 3 : edge, sy : sy + sh, sx : sx + sw].astype(np.int16),
            axis=0,
        )
        samples = []
        for offset in range(-2, args.lookahead + 1):
            current = frames[edge + offset, sy : sy + sh, sx : sx + sw].astype(
                np.int16
            )
            delta = np.max(np.abs(current - baseline), axis=2)
            samples.append(
                {
                    "offset": offset,
                    "changed_pixels": int(
                        np.count_nonzero(delta >= args.pixel_threshold)
                    ),
                    "mean_absolute_delta": float(np.mean(delta)),
                }
            )
        records.append(
            {
                "marker_frame": edge,
                "marker_seconds": edge / args.fps,
                "marker_mean": float(marker_mean[edge]),
                "samples": samples,
            }
        )

    print(
        json.dumps(
            {
                "raw": str(args.raw),
                "frame_count": frame_count,
                "fps": args.fps,
                "marker_edges": len(records),
                "subject": args.subject,
                "pixel_threshold": args.pixel_threshold,
                "events": records,
            },
            indent=2,
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()
