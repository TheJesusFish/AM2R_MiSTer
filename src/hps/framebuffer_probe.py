#!/usr/bin/env python3
"""Write a deterministic AM2R test image to MiSTer's reserved DDR3 framebuffer."""

from __future__ import annotations

import argparse
import hashlib
import mmap
import os
import struct
import time


WIDTH = 320
HEIGHT = 240
STRIDE = WIDTH * 4
FRAMEBUFFER_BASE = 0x22001000
FRAMEBUFFER_BYTES = STRIDE * HEIGHT


FONT = {
    " ": (0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00),
    "-": (0x00, 0x00, 0x00, 0x1F, 0x00, 0x00, 0x00),
    ">": (0x10, 0x08, 0x04, 0x02, 0x04, 0x08, 0x10),
    "0": (0x0E, 0x11, 0x13, 0x15, 0x19, 0x11, 0x0E),
    "2": (0x0E, 0x11, 0x10, 0x08, 0x04, 0x02, 0x1F),
    "3": (0x1E, 0x01, 0x01, 0x0E, 0x01, 0x01, 0x1E),
    "4": (0x08, 0x0C, 0x0A, 0x09, 0x1F, 0x08, 0x08),
    "8": (0x0E, 0x11, 0x11, 0x0E, 0x11, 0x11, 0x0E),
    "A": (0x0E, 0x11, 0x11, 0x1F, 0x11, 0x11, 0x11),
    "B": (0x1E, 0x11, 0x11, 0x1E, 0x11, 0x11, 0x1E),
    "D": (0x1E, 0x11, 0x11, 0x11, 0x11, 0x11, 0x1E),
    "E": (0x1F, 0x01, 0x01, 0x0F, 0x01, 0x01, 0x1F),
    "F": (0x1F, 0x01, 0x01, 0x0F, 0x01, 0x01, 0x01),
    "G": (0x0E, 0x11, 0x01, 0x1D, 0x11, 0x11, 0x0E),
    "I": (0x1F, 0x04, 0x04, 0x04, 0x04, 0x04, 0x1F),
    "M": (0x11, 0x1B, 0x15, 0x15, 0x11, 0x11, 0x11),
    "P": (0x0F, 0x11, 0x11, 0x0F, 0x01, 0x01, 0x01),
    "R": (0x0F, 0x11, 0x11, 0x0F, 0x05, 0x09, 0x11),
    "T": (0x1F, 0x04, 0x04, 0x04, 0x04, 0x04, 0x04),
    "V": (0x11, 0x11, 0x11, 0x11, 0x11, 0x0A, 0x04),
    "X": (0x11, 0x11, 0x0A, 0x04, 0x0A, 0x11, 0x11),
}


def pixel_word(red: int, green: int, blue: int) -> bytes:
    return struct.pack("<I", (red << 16) | (green << 8) | blue)


def fill_rect(frame: bytearray, x: int, y: int, w: int, h: int, rgb: tuple[int, int, int]) -> None:
    x0 = max(0, x)
    y0 = max(0, y)
    x1 = min(WIDTH, x + w)
    y1 = min(HEIGHT, y + h)
    if x0 >= x1 or y0 >= y1:
        return
    row = pixel_word(*rgb) * (x1 - x0)
    for py in range(y0, y1):
        start = py * STRIDE + x0 * 4
        frame[start:start + len(row)] = row


def draw_text(frame: bytearray, x: int, y: int, text: str, scale: int, rgb: tuple[int, int, int]) -> None:
    for char in text:
        glyph = FONT.get(char.upper(), FONT[" "])
        for row, bits in enumerate(glyph):
            for col in range(5):
                if bits & (1 << col):
                    fill_rect(frame, x + col * scale, y + row * scale, scale, scale, rgb)
        x += 6 * scale


def make_frame(marker: int = -1) -> bytearray:
    frame = bytearray(pixel_word(7, 12, 24) * (WIDTH * HEIGHT))
    bars = (
        (235, 48, 48),
        (51, 214, 90),
        (45, 111, 255),
        (244, 211, 61),
        (221, 66, 245),
        (46, 216, 230),
        (245, 245, 245),
        (100, 110, 125),
    )
    bar_width = WIDTH // len(bars)
    for index, color in enumerate(bars):
        fill_rect(frame, index * bar_width, 22, bar_width, 82, color)

    # Pixel-precise border and checkerboard expose cropping, scaling, and stride errors.
    fill_rect(frame, 0, 0, WIDTH, 2, (255, 255, 255))
    fill_rect(frame, 0, HEIGHT - 2, WIDTH, 2, (255, 255, 255))
    fill_rect(frame, 0, 0, 2, HEIGHT, (255, 255, 255))
    fill_rect(frame, WIDTH - 2, 0, 2, HEIGHT, (255, 255, 255))
    for py in range(126, 190, 8):
        for px in range(16, 304, 8):
            color = (37, 48, 67) if ((px // 8) ^ (py // 8)) & 1 else (185, 196, 214)
            fill_rect(frame, px, py, 8, 8, color)

    draw_text(frame, 30, 198, "AM2R ARM->FPGA FB", 2, (255, 255, 255))
    draw_text(frame, 76, 216, "320X240 XRGB8888", 1, (80, 220, 255))

    if marker >= 0:
        fill_rect(frame, marker % WIDTH, 106, 3, 17, (255, 255, 255))
    return frame


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--device", default="/dev/mem")
    parser.add_argument("--frames", type=int, default=1, help="number of frames to write")
    parser.add_argument("--fps", type=float, default=60.0, help="pace animated writes; zero runs unpaced")
    args = parser.parse_args()

    if args.frames < 1 or args.fps < 0:
        parser.error("--frames must be positive and --fps must not be negative")

    fd = os.open(args.device, os.O_RDWR | os.O_SYNC)
    try:
        mapping_offset = FRAMEBUFFER_BASE if args.device == "/dev/mem" else 0
        framebuffer = mmap.mmap(
            fd,
            FRAMEBUFFER_BYTES,
            flags=mmap.MAP_SHARED,
            prot=mmap.PROT_READ | mmap.PROT_WRITE,
            offset=mapping_offset,
        )
        try:
            start = time.monotonic()
            deadline = start
            last_frame = bytearray()
            for index in range(args.frames):
                marker = index if args.frames > 1 else -1
                last_frame = make_frame(marker)
                framebuffer.seek(0)
                framebuffer.write(last_frame)
                # The MiSTer DDR3 aperture is device memory. Writes are visible
                # immediately; Linux correctly rejects msync(2) on this mapping.
                if args.fps:
                    deadline += 1.0 / args.fps
                    delay = deadline - time.monotonic()
                    if delay > 0:
                        time.sleep(delay)
            elapsed = time.monotonic() - start
            digest = hashlib.sha256(last_frame).hexdigest()
            framebuffer.seek(0)
            readback_digest = hashlib.sha256(framebuffer.read(FRAMEBUFFER_BYTES)).hexdigest()
            print(
                f"wrote {args.frames} frame(s), {FRAMEBUFFER_BYTES} bytes each, "
                f"{args.frames / elapsed:.2f} fps, sha256={digest}, "
                f"readback_sha256={readback_digest}, match={digest == readback_digest}"
            )
        finally:
            framebuffer.close()
    finally:
        os.close(fd)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
