#!/usr/bin/env python3
"""Measure integer background motion in a native-size AM2R capture."""

from __future__ import annotations

import argparse
import subprocess
from pathlib import Path

import numpy as np


WIDTH = 320
HEIGHT = 240


def decode(ffmpeg: Path, video: Path) -> np.ndarray:
    command = [
        str(ffmpeg),
        "-v",
        "error",
        "-i",
        str(video),
        "-vf",
        "crop=640:480:40:0,scale=320:240:flags=neighbor,format=gray",
        "-f",
        "rawvideo",
        "-pix_fmt",
        "gray",
        "-",
    ]
    raw = subprocess.check_output(command)
    frame_bytes = WIDTH * HEIGHT
    if len(raw) % frame_bytes:
        raise RuntimeError(f"unexpected raw frame length: {len(raw)}")
    return np.frombuffer(raw, np.uint8).reshape((-1, HEIGHT, WIDTH))


def edges(frame: np.ndarray) -> np.ndarray:
    x = np.abs(np.diff(frame.astype(np.int16), axis=1, prepend=frame[:, :1]))
    y = np.abs(np.diff(frame.astype(np.int16), axis=0, prepend=frame[:1, :]))
    return (x + y) >= 32


def edge_alignment(previous: np.ndarray, current: np.ndarray, maximum: int) -> tuple[int, float, float]:
    # Ignore the HUD and outer edge. Dynamic sprites occupy little of this area,
    # while room geometry supplies stable high-contrast edges.
    a = edges(previous)[48:224, 12:308]
    b = edges(current)[48:224, 12:308]
    scores: list[tuple[float, int]] = []
    for shift in range(-maximum, maximum + 1):
        if shift < 0:
            aa = a[:, -shift:]
            bb = b[:, :shift]
        elif shift > 0:
            aa = a[:, :-shift]
            bb = b[:, shift:]
        else:
            aa = a
            bb = b
        union = np.count_nonzero(aa | bb)
        score = np.count_nonzero(aa & bb) / union if union else 1.0
        scores.append((score, shift))
    scores.sort(reverse=True)
    best_score, best_shift = scores[0]
    margin = best_score - scores[1][0]
    return best_shift, best_score, margin


def projection_alignment(
    previous: np.ndarray, current: np.ndarray, maximum: int, axis: int
) -> tuple[int, float, float]:
    """Estimate one motion axis while tolerating simultaneous orthogonal motion."""
    a = edges(previous)[48:224, 12:308].sum(axis=axis).astype(np.int32)
    b = edges(current)[48:224, 12:308].sum(axis=axis).astype(np.int32)
    scores: list[tuple[float, int]] = []
    for shift in range(-maximum, maximum + 1):
        if shift < 0:
            aa = a[-shift:]
            bb = b[:shift]
        elif shift > 0:
            aa = a[:-shift]
            bb = b[shift:]
        else:
            aa = a
            bb = b
        scale = max(1, int(np.abs(aa).sum() + np.abs(bb).sum()))
        score = 1.0 - float(np.abs(aa - bb).sum()) / scale
        scores.append((score, shift))
    scores.sort(reverse=True)
    best_score, best_shift = scores[0]
    margin = best_score - scores[1][0]
    return best_shift, best_score, margin


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("video", type=Path)
    parser.add_argument("--ffmpeg", type=Path, required=True)
    parser.add_argument("--start", type=int, default=0)
    parser.add_argument("--end", type=int)
    parser.add_argument("--maximum", type=int, default=8)
    parser.add_argument("--two-axis", action="store_true")
    args = parser.parse_args()

    frames = decode(args.ffmpeg, args.video)
    end = min(args.end if args.end is not None else len(frames), len(frames))
    if args.two_axis:
        print("capture_frame,background_dx,background_dy,x_score,x_margin,y_score,y_margin")
    else:
        print("capture_frame,background_dx,edge_score,score_margin")
    for index in range(max(1, args.start), end):
        if args.two_axis:
            dx, x_score, x_margin = projection_alignment(
                frames[index - 1], frames[index], args.maximum, axis=0
            )
            dy, y_score, y_margin = projection_alignment(
                frames[index - 1], frames[index], args.maximum, axis=1
            )
            print(
                f"{index},{dx},{dy},{x_score:.6f},{x_margin:.6f},"
                f"{y_score:.6f},{y_margin:.6f}"
            )
        else:
            shift, score, margin = edge_alignment(
                frames[index - 1], frames[index], args.maximum
            )
            print(f"{index},{shift},{score:.6f},{margin:.6f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
