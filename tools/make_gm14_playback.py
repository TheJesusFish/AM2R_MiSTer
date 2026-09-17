#!/usr/bin/env python3
"""Build a GameMaker 1.4 input playback from an idle recording frame."""

from __future__ import annotations

import argparse
from pathlib import Path


FRAME_BYTES = 3034
KEYBOARD_HELD = 2060
KEYBOARD_RELEASED = 2316
KEYBOARD_PRESSED = 2572


def pulse(frames: list[bytearray], key: int, start: int, duration: int = 4) -> None:
    for index in range(start, min(start + duration, len(frames))):
        frames[index][KEYBOARD_HELD + key] = 1
    if start < len(frames):
        frames[start][KEYBOARD_PRESSED + key] = 1
    if start + duration < len(frames):
        frames[start + duration][KEYBOARD_RELEASED + key] = 1


def hold(frames: list[bytearray], key: int, start: int, end: int) -> None:
    for index in range(start, min(end, len(frames))):
        frames[index][KEYBOARD_HELD + key] = 1
    if start < len(frames):
        frames[start][KEYBOARD_PRESSED + key] = 1
    if end < len(frames):
        frames[end][KEYBOARD_RELEASED + key] = 1


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("idle_recording", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("--frames", type=int, default=1800)
    args = parser.parse_args()

    raw = args.idle_recording.read_bytes()
    if len(raw) < FRAME_BYTES or len(raw) % FRAME_BYTES:
        raise SystemExit("idle recording is not a whole number of GM 1.4 input frames")
    template = raw[:FRAME_BYTES]
    frames = [bytearray(template) for _ in range(args.frames)]

    # Old GameMaker records the current 256-entry virtual-key array first. The
    # unskippable native intro reaches the title at roughly 27 seconds.
    pulse(frames, 13, 1800)  # Return / configured Start
    pulse(frames, 90, 1920)  # Z / configured menu OK
    pulse(frames, 90, 2040)  # confirm a possible continue prompt

    # Walk left and right after room loading has had ample time to finish.
    hold(frames, 37, 2700, 2850)
    hold(frames, 39, 2850, 3000)
    hold(frames, 37, 3000, 3150)
    hold(frames, 39, 3150, 3300)

    args.output.write_bytes(b"".join(frames))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
