#!/usr/bin/env python3
"""Summarize visible frame changes in a 60 Hz UGREEN AM2R capture."""

from __future__ import annotations

import argparse
import subprocess
from pathlib import Path

import numpy as np


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("video", type=Path)
    parser.add_argument("--ffmpeg", type=Path, required=True)
    parser.add_argument("--fps", type=int, default=60)
    args = parser.parse_args()

    command = [
        str(args.ffmpeg), "-v", "error", "-i", str(args.video),
        "-vf", "crop=640:480:40:0,scale=320:240:flags=neighbor,format=gray",
        "-f", "rawvideo", "-pix_fmt", "gray", "-",
    ]
    raw = subprocess.check_output(command)
    frame_bytes = 320 * 240
    if len(raw) % frame_bytes:
        raise RuntimeError(f"unexpected raw frame length: {len(raw)}")
    frames = np.frombuffer(raw, np.uint8).reshape((-1, 240, 320)).astype(np.int16)
    # Ignore static borders and most HUD digits; scene motion and scrolling
    # dominate this region without capture-device black-bar noise.
    scene = frames[:, 40:232, 8:312]
    mad = np.abs(np.diff(scene, axis=0)).mean(axis=(1, 2))

    print(f"frames={len(frames)} intervals={len(mad)}")
    print("second,intervals,changed_gt_0.1,changed_gt_0.2,changed_gt_0.5,mad_median,mad_max")
    for start in range(0, len(mad), args.fps):
        values = mad[start : start + args.fps]
        print(
            f"{start // args.fps},{len(values)},"
            f"{np.count_nonzero(values > .1)},"
            f"{np.count_nonzero(values > .2)},"
            f"{np.count_nonzero(values > .5)},"
            f"{np.median(values):.4f},{np.max(values):.4f}"
        )

    for threshold in (0.1, 0.2, 0.5):
        changed = mad > threshold
        longest_repeat = 0
        repeat = 0
        for item in changed:
            if item:
                repeat = 0
            else:
                repeat += 1
                longest_repeat = max(longest_repeat, repeat)
        print(
            f"threshold={threshold:.1f} changed={np.count_nonzero(changed)}/"
            f"{len(changed)} longest_unchanged_run={longest_repeat}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
