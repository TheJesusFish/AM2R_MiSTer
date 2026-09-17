#!/usr/bin/env python3
"""Measure per-frame and alternating-frame changes in a static video region."""

from __future__ import annotations

import argparse
import subprocess
from pathlib import Path

import numpy as np


WIDTH = 320
HEIGHT = 240


def percentile(values: np.ndarray, fraction: float) -> float:
    return float(np.percentile(values, fraction * 100.0))


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("video", type=Path)
    parser.add_argument("--ffmpeg", type=Path, required=True)
    parser.add_argument("--start", type=float, default=0.0)
    parser.add_argument("--duration", type=float, required=True)
    parser.add_argument("--crop", default="0:0:320:240", metavar="X:Y:W:H")
    parser.add_argument("--top", type=int, default=12)
    args = parser.parse_args()

    x, y, width, height = (int(value) for value in args.crop.split(":"))
    if x < 0 or y < 0 or width <= 0 or height <= 0:
        raise SystemExit("invalid crop")
    if x + width > WIDTH or y + height > HEIGHT:
        raise SystemExit("crop exceeds 320x240 frame")

    command = [
        str(args.ffmpeg),
        "-v", "error",
        "-ss", str(args.start),
        "-t", str(args.duration),
        "-i", str(args.video),
        "-vf", "crop=640:480:40:0,scale=320:240:flags=neighbor,format=gray",
        "-f", "rawvideo",
        "-pix_fmt", "gray",
        "-",
    ]
    raw = subprocess.check_output(command)
    frame_bytes = WIDTH * HEIGHT
    if len(raw) % frame_bytes:
        raise RuntimeError(f"unexpected decoded byte count: {len(raw)}")
    frames = np.frombuffer(raw, np.uint8).reshape((-1, HEIGHT, WIDTH))
    region = frames[:, y:y + height, x:x + width].astype(np.int16)
    if len(region) < 3:
        raise SystemExit("fewer than three decoded frames")

    diff1 = np.abs(region[1:] - region[:-1]).mean(axis=(1, 2))
    diff2 = np.abs(region[2:] - region[:-2]).mean(axis=(1, 2))
    luminance = region.mean(axis=(1, 2))
    print(
        f"frames={len(region)} region={x}:{y}:{width}:{height} "
        f"luma_min={luminance.min():.4f} luma_max={luminance.max():.4f}"
    )
    for label, values in (("adjacent_mad", diff1), ("two_frame_mad", diff2)):
        print(
            f"{label} min={values.min():.4f} median={np.median(values):.4f} "
            f"p95={percentile(values, .95):.4f} "
            f"p99={percentile(values, .99):.4f} max={values.max():.4f}"
        )

    order = np.argsort(diff1)[::-1][: max(0, args.top)]
    for index in order:
        frame = int(index + 1)
        two_frame = float(diff2[index - 1]) if index > 0 else float("nan")
        print(
            f"change frame={frame} time={args.start + frame / 60.0:.3f} "
            f"adjacent_mad={diff1[index]:.4f} two_frame_mad={two_frame:.4f} "
            f"luma={luminance[frame]:.4f}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
