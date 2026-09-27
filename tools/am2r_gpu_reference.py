#!/usr/bin/env python3
"""Independent scalar reference for the AM2R GPU's submitted descriptor ABI.

No runtime/RTL code is executed or imported. This models pixel results, not DDR
timing, cache coherency, game draw-call translation, or CPU offscreen rendering.
All packed pixels are little-endian RGBA (0xAABBGGRR); present output is XRGB
(0x00RRGGBB). Tint is nearest /255, or floor for axis-blit descriptor bit 10;
blends use floor /255, as in current RTL.
Water/tiled paths modulate alpha only. Native-water input is the *prior completed*
native framebuffer, never the descriptor's source pointer or the current target.

Usage: python tools/am2r_gpu_reference.py capture/manifest.json --output results
Captures may contain private game graphics. Keep captures/results under data/.
SPDX-License-Identifier: GPL-2.0-or-later
"""

from __future__ import annotations

import argparse
from bisect import bisect_right
from collections import Counter
from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
import struct
from typing import Mapping, Sequence

WIDTH, HEIGHT = 320, 240
PIXELS = WIDTH * HEIGHT
FRAME_BYTES = PIXELS * 4
MAX_COMMANDS = 1024
MAX_MEMORY_BYTES = 132 * 1024 * 1024
MAX_REGIONS = 4096
MAX_PIXEL_WORK = 32 * 1024 * 1024
FORMAT = "am2r-gpu-capture-v1"


class CaptureError(ValueError):
    """Invalid, unsupported, incomplete, or excessive captured job."""


def _integer(value: object, name: str) -> int:
    if isinstance(value, str):
        try:
            return int(value, 0)
        except ValueError:
            pass
    elif type(value) is int:
        return value
    raise CaptureError(f"{name} must be an integer (or 0x-prefixed integer string)")


def _signed(value: int, bits: int) -> int:
    value &= (1 << bits) - 1
    return value - (1 << bits) if value & (1 << (bits - 1)) else value


def _lanes(pixel: int) -> list[int]:
    return [(pixel >> shift) & 255 for shift in (0, 8, 16, 24)]


def _pack(lanes: Sequence[int]) -> int:
    return sum(value << (index * 8) for index, value in enumerate(lanes))


def tint_pixel(pixel: int, tint: int, floor: bool = False) -> int:
    """Channel multiplication; bit-10 axis blits request CPU floor rounding."""
    return _pack([(a * b + (0 if floor else 127)) // 255
                  for a, b in zip(_lanes(pixel), _lanes(tint))])


def blend_pixel(source: int, destination: int, mode: int = 0) -> int:
    """mode 0: source-over, 1: additive, 2: ZERO/INV_SRC_COLOR (GM subtract)."""
    src, dst = _lanes(source), _lanes(destination)
    alpha = src[3]
    if mode == 2:
        return _pack([d * (255 - s) // 255 for s, d in zip(src, dst)])
    if mode == 1:
        return _pack([min(255, dst[i] + src[i] * alpha // 255) for i in range(3)]
                     + [min(255, dst[3] + alpha)])
    if mode != 0:
        raise CaptureError(f"unsupported blend mode {mode}")
    return _pack([(src[i] * alpha + dst[i] * (255 - alpha)) // 255
                  for i in range(3)] + [alpha + dst[3] * (255 - alpha) // 255])


def blend_generic(source: int, destination: int, state: int, factors: int, fog: int) -> int:
    """General packet state. Unlike the legacy shortcut, zero alpha is data.

    Named mode numbers are the GameMaker renderer's 0..5; mode6 carries
    separate additive RGB/alpha factors. The software renderer's old complex
    blend fallback is not an oracle for this arithmetic.
    """
    src, dst = _lanes(source), _lanes(destination)
    alpha = src[3]
    mode = (state >> 32) & 255
    if state & (1 << 56) and alpha < ((state >> 48) & 255):
        return destination
    if state & (1 << 57):
        src[:3] = _lanes(fog)[:3]
    if not state & (1 << 58):
        result = src
    elif mode == 0:
        result = [(src[c] * alpha + dst[c] * (255 - alpha)) // 255 for c in range(3)]
        result += [alpha + dst[3] * (255 - alpha) // 255]
    elif mode == 1:
        result = [min(255, dst[c] + src[c] * alpha // 255) for c in range(3)]
        result += [min(255, dst[3] + alpha)]
    elif mode == 2:
        result = [max(a, b) for a, b in zip(src, dst)]
    elif mode == 3:
        result = [b * (255 - a) // 255 for a, b in zip(src, dst)]
    elif mode == 4:
        result = [min(a, b) for a, b in zip(src, dst)]
    elif mode == 5:
        result = [max(0, src[c] * alpha // 255 - dst[c]) for c in range(3)] + [alpha]
    elif mode == 6:
        def factor(kind: int, channel: int) -> int:
            if kind == 1: return 0
            if kind == 2: return 255
            if kind == 3: return src[channel]
            if kind == 4: return 255 - src[channel]
            if kind == 5: return alpha
            if kind == 6: return 255 - alpha
            if kind == 7: return dst[3]
            if kind == 8: return 255 - dst[3]
            if kind == 9: return dst[channel]
            if kind == 10: return 255 - dst[channel]
            if kind == 11: return 255 if channel == 3 else min(alpha, 255 - dst[3])
            raise CaptureError("invalid generic blend factor")
        result = []
        for c in range(4):
            shift = 16 if c == 3 else 0
            sf = factor((factors >> shift) & 255, c)
            df = factor((factors >> (shift + 8)) & 255, c)
            result.append(min(255, (src[c] * sf + dst[c] * df) // 255))
    else:
        raise CaptureError("invalid generic blend mode")
    return _pack([result[c] if state & (1 << (40 + c)) else dst[c] for c in range(4)])


def decode_generic_packet(data: bytes) -> tuple[int, ...]:
    if len(data) != 512:
        raise CaptureError("generic packet must contain exactly512bytes")
    q = struct.unpack("<64Q", data)
    if q[0] & 0xFFFFFFFF != 0x31504741 or q[0] >> 32 & ~3:
        raise CaptureError("invalid generic packet magic/flags")
    if any(q[4:8]) or any(q[39:]):
        raise CaptureError("nonzero generic packet reserved words")
    if q[2] >> 59 or (q[2] >> 44) & 15:
        raise CaptureError("nonzero generic pixel-state reserved bits")
    mode = (q[2] >> 32) & 255
    if mode > 6:
        raise CaptureError("invalid generic blend mode")
    if mode == 6 and any(not 1 <= (q[3] >> shift) & 255 <= 11 for shift in (0, 8, 16, 24)):
        raise CaptureError("invalid generic blend factor")
    if q[0] & (1 << 32):
        width, height = q[2] & 65535, q[2] >> 16 & 65535
        base, stride = q[1] & 0xFFFFFFFF, q[1] >> 32
        if not width or not height or base & 3 or stride & 3 or stride < width * 4:
            raise CaptureError("invalid generic texture layout")
        if base + (height - 1) * stride + width * 4 > 2**32:
            raise CaptureError("generic texture address overflow")
    return q


def rgba_to_xrgb(pixel: int) -> int:
    return ((pixel & 255) << 16) | (pixel & 0xFF00) | ((pixel >> 16) & 255)


def xrgb_to_rgba(pixel: int) -> int:
    return 0xFF000000 | ((pixel & 255) << 16) | (pixel & 0xFF00) | ((pixel >> 16) & 255)


def pixel_bytes(pixels: Sequence[int]) -> bytes:
    return struct.pack(f"<{len(pixels)}I", *pixels)


def read_pixels(data: bytes) -> list[int]:
    if len(data) != FRAME_BYTES:
        raise CaptureError(f"framebuffer must have exactly {FRAME_BYTES} bytes")
    return list(struct.unpack(f"<{PIXELS}I", data))


class _Memory:
    def __init__(self, regions: Mapping[int, bytes]):
        if len(regions) > MAX_REGIONS:
            raise CaptureError("too many memory regions")
        self.regions = []
        total = 0
        end = 0
        for base, data in sorted(regions.items()):
            if type(base) is not int or base < end or base < 0 or base + len(data) > 2**32:
                raise CaptureError("invalid or overlapping physical memory regions")
            if not isinstance(data, bytes) or not data:
                raise CaptureError("memory regions must be nonempty bytes")
            self.regions.append((base, data))
            total += len(data)
            end = base + len(data)
        if total > MAX_MEMORY_BYTES:
            raise CaptureError("memory capture exceeds bounded limit")
        self.bases = [base for base, _ in self.regions]
        self.writes: list[tuple[int, bytes]] = []

    def read(self, address: int, length: int) -> bytes:
        if address < 0 or length < 0 or address + length > 2**32:
            raise CaptureError(f"physical address overflow: {address:#x}+{length}")
        if not length:
            return b""
        # Exports are ordered writes: later sources see the latest snapshot.
        # A tiled STORE can leave thousands of row fragments in a merged export.
        # Resolve splits iteratively: one recursive call per row exceeded Python's
        # stack limit despite valid, completely defined captured memory.
        pending = [(address, address + length)]
        pieces = []
        while pending:
            start, end = pending.pop()
            for base, data in reversed(self.writes):
                low, high = max(start, base), min(end, base + len(data))
                if low >= high:
                    continue
                pieces.append((low, data[low - base:high - base]))
                if start < low:
                    pending.append((start, low))
                if high < end:
                    pending.append((high, end))
                break
            else:
                index = bisect_right(self.bases, start) - 1
                if index >= 0:
                    base, data = self.regions[index]
                    if end <= base + len(data):
                        pieces.append((start, data[start - base:end - base]))
                        continue
                raise CaptureError(f"unmapped memory read at {start:#010x}, {end - start} bytes")
        return b"".join(data for _, data in sorted(pieces))

    def pixel(self, address: int) -> int:
        if address & 3:
            raise CaptureError(f"unaligned pixel source {address:#x}")
        return struct.unpack("<I", self.read(address, 4))[0]

    def export(self, address: int, data: bytes) -> None:
        if address & 3 or not data or len(data) & 3 or not 0 <= address <= 2**32 - len(data):
            raise CaptureError(f"invalid export address {address:#x}")
        self.writes.append((address, data))


@dataclass
class RenderResult:
    pixels: list[int]
    exports: dict[int, bytes]
    opcode_counts: dict[int, int]
    pixel_work: int
    presented: bool = True

    @property
    def xrgb_pixels(self) -> list[int]:
        return [rgba_to_xrgb(pixel) for pixel in self.pixels]


def decode_commands(commands: bytes) -> list[tuple[int, ...]]:
    if not isinstance(commands, bytes) or not commands or len(commands) % 64:
        raise CaptureError("commands must be a nonempty sequence of 64-byte descriptors")
    if len(commands) > MAX_COMMANDS * 64:
        raise CaptureError(f"more than {MAX_COMMANDS} descriptors")
    decoded = list(struct.iter_unpack("<8Q", commands))
    for index, words in enumerate(decoded):
        op = words[0] & 255
        if op > 14:
            raise CaptureError(f"command {index}: unknown opcode {op}; refusing RTL default-present")
        if op in (0, 12) and index != len(decoded) - 1:
            raise CaptureError(f"command {index}: early completion hides trailing descriptors")
        flags = (words[0] >> 8) & 255
        allowed = 7 if op == 2 else 3 if op in (3, 5) else 1 if op in (7, 9) else 0
        if flags & ~allowed or (op in (2, 3, 5) and flags & 3 == 3):
            raise CaptureError(f"command {index}: unsupported flags {flags:#x} for opcode {op}")
        if op in (10, 11):
            if words[0] >> 48 or words[2] >> 32 or any(words[3:]):
                raise CaptureError(f"command {index}: nonzero reserved rectangle fields")
        if op == 12 and (words[0] != 12 or any(words[1:])):
            raise CaptureError(f"command {index}: nonzero no-present completion fields")
        if op == 13 and (words[0] >> 48 or words[1] >> 32 or words[2] >> 32 or any(words[3:])):
            raise CaptureError(f"command {index}: nonzero generic descriptor reserved fields")
        if op == 14 and (words[0] >> 48 or words[1] >> 32 or words[2] >> 32 or any(words[3:])):
            raise CaptureError(f"command {index}: nonzero bounded-clear reserved fields")
    if decoded[-1][0] & 255 not in (0, 12):
        raise CaptureError("missing terminal present/completion opcode")
    return decoded


def render(commands: bytes, regions: Mapping[int, bytes],
           initial_framebuffer: Sequence[int] | None = None, *,
           native_source_base: int | None = None) -> RenderResult:
    """Replay one bounded submitted job; no assumptions about initial BRAM.

    A leading full clear is normally required. Explicit initial RGBA is also allowed;
    prior native XRGB is NOT interchangeable because presentation loses alpha.
    A partial opcode14 clear does not initialize untouched BRAM: captured jobs
    using it must include the observer-fenced raw initial RGBA (or other complete
    initialization). Never invent zeros for unknown pixels or mask comparisons.
    Invalid out-of-frame water/tiled rows are rejected (RTL expects pre-clipping).
    """
    decoded = decode_commands(commands)
    memory = _Memory(regions)
    if initial_framebuffer is not None:
        if len(initial_framebuffer) != PIXELS or any(type(p) is not int or not 0 <= p < 2**32
                                                     for p in initial_framebuffer):
            raise CaptureError("initial framebuffer must contain 320x240 uint32 RGBA pixels")
        framebuffer: list[int | None] = list(initial_framebuffer)
    else:
        framebuffer = [None] * PIXELS
    exports: dict[int, bytes] = {}
    rectangle_writes: list[tuple[int, int]] = []
    counts: Counter[int] = Counter()
    work = 0
    affine_tint, affine_mode = 0xFFFFFFFF, 0
    affine_pending = False

    def charge(count: int) -> None:
        nonlocal work
        work += count
        if work > MAX_PIXEL_WORK:
            raise CaptureError("job exceeds bounded pixel-work limit")

    def resolved() -> list[int]:
        if None in framebuffer:
            raise CaptureError("job depends on uncaptured initial BRAM; capture a clear-led job")
        return list(framebuffer)  # type: ignore[arg-type]

    def draw(index: int, source: int, mode: int) -> None:
        if mode != 2 and source >> 24 == 0:
            return
        if mode == 0 and source >> 24 == 255:
            framebuffer[index] = source
            return
        destination = framebuffer[index]
        if destination is None:
            raise CaptureError(f"blend reads uncaptured initial BRAM pixel {index}")
        framebuffer[index] = blend_pixel(source, destination, mode)

    for command_index, words in enumerate(decoded):
        op = words[0] & 255
        counts[op] += 1
        mode = 2 if words[0] & 512 else 1 if words[0] & 256 else 0
        width, height = (words[0] >> 16) & 65535, (words[0] >> 32) & 65535
        base, stride = words[1] & 0xFFFFFFFF, words[1] >> 32
        x, y = _signed(words[2], 16), _signed(words[2] >> 16, 16)
        if op in (0, 12):
            if affine_pending:
                raise CaptureError("unconsumed affine setup would leak state into the next job")
            # Expected exports are captured after completion. Distinct export
            # ranges can overlap, so report final memory, not the old snapshots.
            # Merge adjacent/overlapping rectangle rows into the exact union
            # of written bytes. Never include uncaptured padding. Legacy full
            # framebuffer-export identities remain independently comparable.
            merged: list[list[int]] = []
            for start, end in sorted(rectangle_writes):
                if merged and start <= merged[-1][1]:
                    merged[-1][1] = max(end, merged[-1][1])
                else:
                    merged.append([start, end])
            for start, end in merged:
                count = max(end - start, len(exports.get(start, b"")))
                exports[start] = memory.read(start, count)
            final_exports = {address: memory.read(address, len(data)) for address, data in exports.items()}
            # A no-present batch can legally touch only part of BRAM. Full
            # framebuffer replay still needs explicit initialization evidence;
            # the tooling must not silently treat unknown pixels as black.
            return RenderResult(resolved(), final_exports, dict(counts), work, presented=op == 0)
        if op == 1:
            charge(PIXELS)
            framebuffer = [base] * PIXELS
        elif op == 14:
            # Raw RGBA replacement, not an alpha-blended fill. Hardware treats
            # malformed descriptors as no-ops; replay rejects them so capture
            # validation cannot silently bless a malformed submitted command.
            if not width or not height:
                continue
            left, top = words[2] & 65535, words[2] >> 16 & 65535
            if left + width > WIDTH or top + height > HEIGHT:
                raise CaptureError("bounded clear rectangle outside BRAM workspace")
            charge(width * height)
            for row in range(top, top + height):
                start = row * WIDTH + left
                framebuffer[start:start + width] = [base] * width
        elif op == 5:
            affine_tint, affine_mode = base, mode
            affine_pending = True
        elif op == 6:
            if base & 7:
                raise CaptureError("framebuffer export must be 64-bit aligned")
            charge(PIXELS)
            snapshot = pixel_bytes(resolved())
            memory.export(base, snapshot)
            exports[base] = snapshot
        elif op in (10, 11):
            if not width or not height:
                continue
            if x < 0 or y < 0 or x + width > WIDTH or y + height > HEIGHT:
                raise CaptureError("transfer rectangle outside BRAM workspace")
            if base & 3 or stride & 7 or stride < width * 4:
                raise CaptureError("invalid transfer alignment/stride")
            if base + (height - 1) * stride + width * 4 > 2**32:
                raise CaptureError("transfer physical address overflow")
            charge(width * height)
            for row in range(height):
                address = base + row * stride
                start = (y + row) * WIDTH + x
                if op == 10:
                    data = memory.read(address, width * 4)
                    framebuffer[start:start + width] = struct.unpack(f"<{width}I", data)
                else:
                    pixels = framebuffer[start:start + width]
                    if None in pixels:
                        raise CaptureError("rectangle export reads uncaptured initial BRAM")
                    data = pixel_bytes(pixels)
                    memory.export(address, data)
                    rectangle_writes.append((address, address + len(data)))
        elif op == 13:
            if not width or not height:
                continue
            if base & 7 or x < 0 or y < 0 or x + width > WIDTH or y + height > HEIGHT:
                raise CaptureError("generic draw requires aligned packet and pre-clipped bounds")
            q = decode_generic_packet(memory.read(base, 512))
            charge(width * height)
            textured, triangle = bool(q[0] & (1 << 32)), bool(q[0] & (1 << 33))
            texture_base, texture_stride = q[1] & 0xFFFFFFFF, q[1] >> 32
            tw, th = q[2] & 65535, q[2] >> 16 & 65535

            def plane(offset: int, col: int, row: int, bilinear: bool = False) -> int:
                values = [_signed(q[offset + i], 64) for i in range(4 if bilinear else 3)]
                result = values[0] + col * values[1] + row * values[2]
                if bilinear:
                    result += col * row * values[3]
                return _signed(result, 64)

            def coordinate(fixed: int, limit: int) -> int:
                # Truncation towardzero followed by clamping. Negative values
                # all map to zero, avoiding signed-language division ambiguity.
                return min(limit - 1, max(0, fixed // (1 << 32)))

            for row in range(height):
                for col in range(width):
                    if triangle and any(plane(offset, col, row) < 0 for offset in (8, 11, 14)):
                        continue
                    texel = 0xFFFFFFFF
                    if textured:
                        u = coordinate(plane(17, col, row), tw)
                        v = coordinate(plane(20, col, row), th)
                        texel = memory.pixel(texture_base + v * texture_stride + u * 4)
                    source = _pack([lane * min(1 << 32, max(0, plane(23 + c * 4, col, row, True))) >> 32
                                    for c, lane in enumerate(_lanes(texel))])
                    index = (y + row) * WIDTH + x + col
                    if framebuffer[index] is None:
                        raise CaptureError("generic blend reads uncaptured initial BRAM")
                    framebuffer[index] = blend_generic(source, framebuffer[index], q[2], q[3] & 0xFFFFFFFF, q[3] >> 32)
        elif op in (2, 3, 4):
            if not width or not height:
                raise CaptureError(f"command {command_index}: zero rectangle dimension")
            charge(width * height)
            if op == 4:
                umin, umax = _signed(words[3], 32), _signed(words[3] >> 32, 32)
                vmin, vmax = _signed(words[4], 32), _signed(words[4] >> 32, 32)
                if umin > umax or vmin > vmax:
                    raise CaptureError("reversed affine source bounds")
                u0, v0 = _signed(words[5], 32), _signed(words[5] >> 32, 32)
                ux, vx = _signed(words[6], 32), _signed(words[6] >> 32, 32)
                uy, vy = _signed(words[7], 32), _signed(words[7] >> 32, 32)
                tint, mode = affine_tint, affine_mode
                affine_tint, affine_mode = 0xFFFFFFFF, 0
                affine_pending = False
                steps = [0] * 4
            else:
                u0, v0 = _signed(words[4], 32), _signed(words[4] >> 32, 32)
                ux, vy = _signed(words[5], 32), _signed(words[5] >> 32, 32)
                uy, vx = 0, 0
                tint = base if op == 3 else words[6] & 0xFFFFFFFF
                steps = [_signed(words[7] >> shift, 16) for shift in (0, 16, 32, 48)]
            for row in range(max(0, -y), min(height, HEIGHT - y)):
                row_tint = _pack([min(255, max(0, _signed((lane << 16) + row * (step << 8), 25) >> 16))
                                  for lane, step in zip(_lanes(tint), steps)])
                for col in range(max(0, -x), min(width, WIDTH - x)):
                    u = _signed(u0 + row * uy + col * ux, 32)
                    v = _signed(v0 + row * vy + col * vx, 32)
                    if op == 4 and not (umin <= u < umax and vmin <= v < vmax):
                        continue
                    if op == 3:
                        source = row_tint
                    else:
                        # The RTL's row-index stage is unsigned 16-bit, while
                        # horizontal source displacement remains signed 32-bit.
                        address = (base + ((v >> 16) & 65535) * stride + (u >> 16) * 4) & 0xFFFFFFFF
                        source = tint_pixel(memory.pixel(address), row_tint,
                                            floor=(op == 2 and bool(words[0] & 1024)))
                    draw((y + row) * WIDTH + x + col, source, mode)
        elif op in (7, 8):
            rows = width
            destination_y = (words[2] >> 32) & 65535
            if not 1 <= rows <= HEIGHT or destination_y + rows > HEIGHT:
                raise CaptureError("water destination rows outside framebuffer")
            table_address = words[2] & 0xFFFFFFFF
            if table_address & 7:
                raise CaptureError("unaligned water table")
            table = memory.read(table_address, rows * 8)
            if op == 8:
                if native_source_base is None:
                    raise CaptureError("native-water command requires prior completed native_source_base")
                source_base = native_source_base
            else:
                source_base = base
            if type(source_base) is not int or source_base & 7:
                raise CaptureError("water/native source must be 64-bit aligned")
            for row, (dx, length, sx, sy) in enumerate(struct.iter_unpack("<4H", table)):
                if dx + length > WIDTH or sx + length > WIDTH:
                    raise CaptureError("water row requires pre-clipped source and destination")
                if op == 8 and sy >= HEIGHT:
                    raise CaptureError("native-water source row outside prior framebuffer")
                charge(WIDTH)
                # RTL always reads a complete 1280-byte row; descriptor stride
                # is ignored, including zero stride in native-water commands.
                source_row = memory.read(source_base + sy * WIDTH * 4, WIDTH * 4)
                for col in range(length):
                    source = struct.unpack_from("<I", source_row, (sx + col) * 4)[0]
                    if op == 8:
                        source = xrgb_to_rgba(source)
                    alpha = ((source >> 24) * ((words[6] >> 24) & 255) + 127) // 255
                    source = (source & 0xFFFFFF) | (alpha << 24)
                    draw((destination_y + row) * WIDTH + dx + col, source, 0)
        elif op == 9:
            if not width or not height or x < 0 or y < 0 or x + width > WIDTH or y + height > HEIGHT:
                raise CaptureError("tiled rectangle requires pre-clipping")
            charge(width * height)
            u, v0 = _signed(words[4], 32), _signed(words[4] >> 32, 32)
            vy, phase = _signed(words[5] >> 32, 32), words[3] & 31
            for row in range(height):
                v = _signed(v0 + row * vy, 32)
                address = (base + ((v >> 16) & 65535) * stride + (u >> 16) * 4) & 0xFFFFFFFF
                if address & 7:
                    raise CaptureError("tiled 32-pixel source must be 64-bit aligned")
                tile = struct.unpack("<32I", memory.read(address, 128))
                for col in range(width):
                    source = tile[(phase + col) & 31]
                    alpha = ((source >> 24) * ((words[6] >> 24) & 255) + 127) // 255
                    draw((y + row) * WIDTH + x + col, (source & 0xFFFFFF) | (alpha << 24), 1)
    raise AssertionError("decoder guaranteed completion")


def _blob(directory: Path, descriptor: object, limit: int) -> bytes:
    if not isinstance(descriptor, dict) or not isinstance(descriptor.get("file"), str):
        raise CaptureError("blob descriptor requires a file string")
    relative = Path(descriptor["file"])
    if relative.is_absolute() or ".." in relative.parts or relative.drive or ":" in descriptor["file"]:
        raise CaptureError("capture file must be relative and stay inside capture directory")
    path = (directory / relative).resolve()
    if not path.is_relative_to(directory.resolve()) or not path.is_file():
        raise CaptureError("capture file is missing or escapes capture directory")
    if path.stat().st_size > limit:
        raise CaptureError(f"capture file exceeds size limit: {relative}")
    data = path.read_bytes()
    if "bytes" in descriptor and _integer(descriptor["bytes"], "blob bytes") != len(data):
        raise CaptureError(f"declared byte count mismatch: {relative}")
    expected = descriptor.get("sha256")
    if expected is not None and expected != hashlib.sha256(data).hexdigest():
        raise CaptureError(f"SHA-256 mismatch: {relative}")
    return data


def load_capture(path: Path) -> tuple[dict, bytes, dict[int, bytes], list[int] | None]:
    if path.stat().st_size > 1024 * 1024:
        raise CaptureError("manifest exceeds size limit")
    manifest = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(manifest, dict) or manifest.get("format") != FORMAT:
        raise CaptureError(f"manifest format must be {FORMAT}")
    directory = path.resolve().parent
    commands = _blob(directory, manifest.get("commands"), MAX_COMMANDS * 64)
    if "command_count" in manifest:
        count = _integer(manifest["command_count"], "command_count")
        if not 1 <= count <= MAX_COMMANDS or len(commands) != count * 64:
            raise CaptureError("declared command_count does not match complete descriptors")
    entries = manifest.get("regions")
    if not isinstance(entries, list) or len(entries) > MAX_REGIONS:
        raise CaptureError("manifest regions must be a bounded list")
    regions: dict[int, bytes] = {}
    total = 0
    for entry in entries:
        if not isinstance(entry, dict):
            raise CaptureError("invalid region descriptor")
        base = _integer(entry.get("base"), "region base")
        if base in regions:
            raise CaptureError("duplicate region base")
        data = _blob(directory, entry, MAX_MEMORY_BYTES - total)
        total += len(data)
        regions[base] = data
    initial = read_pixels(_blob(directory, manifest["initial_framebuffer"], FRAME_BYTES)) if "initial_framebuffer" in manifest else None
    return manifest, commands, regions, initial


def compare_pixels(actual: Sequence[int], expected: Sequence[int]) -> dict:
    if len(actual) != PIXELS or len(expected) != PIXELS:
        raise CaptureError("comparison expects two complete 320x240 frames")
    indices = [index for index, pair in enumerate(zip(actual, expected)) if pair[0] != pair[1]]
    return {
        "matching": not indices,
        "different_pixels": len(indices),
        "bounds": [min(i % WIDTH for i in indices), min(i // WIDTH for i in indices),
                   max(i % WIDTH for i in indices), max(i // WIDTH for i in indices)] if indices else None,
        "max_channel_error": max((abs(a - b) for i in indices for a, b in
                                   zip(_lanes(actual[i]), _lanes(expected[i]))), default=0),
        "first_differences": [{"x": i % WIDTH, "y": i // WIDTH,
                               "reference": f"{actual[i]:08x}", "captured": f"{expected[i]:08x}"}
                              for i in indices[:16]],
    }


def compare_rgba_bytes(actual: bytes, expected: bytes) -> dict:
    """Compare complete declared RGBA writes, including non-frame row exports."""
    if len(actual) != len(expected) or not actual or len(actual) & 3:
        raise CaptureError("RGBA comparison requires equal nonempty whole-pixel ranges")
    if len(actual) == FRAME_BYTES:
        return compare_pixels(read_pixels(actual), read_pixels(expected))
    left = list(struct.unpack(f"<{len(actual) // 4}I", actual))
    right = list(struct.unpack(f"<{len(expected) // 4}I", expected))
    indices = [i for i, (a, b) in enumerate(zip(left, right)) if a != b]
    return {"matching": not indices, "pixel_count": len(left),
            "different_pixels": len(indices), "byte_count": len(actual),
            "max_channel_error": max((abs(a - b) for i in indices for a, b in
                                       zip(_lanes(left[i]), _lanes(right[i]))), default=0),
            "first_differences": [{"byte_offset": i * 4, "reference": f"{left[i]:08x}",
                                   "captured": f"{right[i]:08x}"} for i in indices[:16]]}


def _expected_export_entries(manifest: dict, produced: Mapping[int, bytes]) -> list[tuple[int, dict]]:
    """Validate declared export identities before building comparison maps.

    Duplicate bases are invalid, even when their data matches: otherwise a later
    matching comparison could overwrite a preceding mismatch in the JSON map.
    """
    entries = manifest.get("expected_exports", [])
    if not isinstance(entries, list) or len(entries) > MAX_COMMANDS:
        raise CaptureError("invalid expected_exports list")
    validated = []
    expected_bases = set()
    for entry in entries:
        if not isinstance(entry, dict):
            raise CaptureError("invalid expected export descriptor")
        base = _integer(entry.get("base"), "expected export base")
        if base in expected_bases:
            raise CaptureError(f"duplicate expected export base {base:#x}")
        if base not in produced:
            raise CaptureError(f"expected export {base:#x} not produced")
        if entry.get("format", "rgba8888") != "rgba8888":
            raise CaptureError("expected exports must contain RGBA8888 pixels")
        expected_bases.add(base)
        validated.append((base, entry))
    return validated


def compare_captured_exports(manifest: dict, directory: Path,
                             produced: Mapping[int, bytes]) -> tuple[dict, dict]:
    """Compare GPU model/RTL exports with captured hardware and disclose coverage."""
    comparisons = {}
    entries = _expected_export_entries(manifest, produced)
    expected_bases = {base for base, _ in entries}
    for base, entry in entries:
        comparisons[hex(base)] = compare_rgba_bytes(produced[base],
                                                    _blob(directory, entry, len(produced[base])))
    missing = sorted(set(produced) - expected_bases)
    coverage = {"framebuffer_expected": "expected" in manifest,
                "produced_export_count": len(produced),
                "compared_export_count": len(expected_bases),
                "missing_export_bases": [hex(base) for base in missing],
                "complete": "expected" in manifest and not missing}
    return comparisons, coverage


def compare_cpu_captured_exports(manifest: dict, directory: Path,
                                 produced: Mapping[int, bytes]) -> tuple[dict, dict]:
    """Compare declared CPU crops directly with captured RGBA hardware exports.

    This comparison does not use model/RTL pixels as the CPU expectation. Each
    provided file covers only the exported 320x240 crop, not the full logical
    GameMaker surface or exports without their own CPU reference.
    """
    comparisons = {}
    referenced = set()
    for base, entry in _expected_export_entries(manifest, produced):
        if "cpu_reference" not in entry:
            continue
        relative = entry["cpu_reference"]
        if not isinstance(relative, str):
            raise CaptureError("cpu_reference must be a relative file string")
        count = len(produced[base])
        cpu = _blob(directory, {"file": relative, "bytes": count}, count)
        captured = _blob(directory, entry, count)
        comparisons[hex(base)] = compare_rgba_bytes(cpu, captured)
        referenced.add(base)
    missing = sorted(set(produced) - referenced)
    coverage = {"scope": ("declared 320x240 RGBA export crops" if all(len(data) == FRAME_BYTES for data in produced.values())
                          else "declared RGBA export byte ranges"),
                "full_logical_surfaces_compared": False,
                "produced_export_count": len(produced),
                "compared_cpu_reference_count": len(referenced),
                "exports_without_cpu_reference": [hex(base) for base in missing],
                "complete": bool(produced) and not missing}
    return comparisons, coverage


def captured_comparison_status(comparisons: Sequence[dict], coverage: dict) -> str:
    if not comparisons:
        return "not_compared"
    if not all(item["matching"] for item in comparisons):
        return "mismatch"
    return "match" if coverage["complete"] else "partial_match"


def _ppm(pixels: Sequence[int]) -> bytes:
    return f"P6\n{WIDTH} {HEIGHT}\n255\n".encode("ascii") + bytes(
        channel for pixel in pixels for channel in _lanes(pixel)[:3])


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("manifest", type=Path)
    parser.add_argument("--output", type=Path, help="optional generated reference/difference files")
    args = parser.parse_args(argv)
    try:
        manifest, commands, regions, initial = load_capture(args.manifest)
        native = manifest.get("native_source_base")
        result = render(commands, regions, initial,
                        native_source_base=_integer(native, "native_source_base") if native is not None else None)
        report: dict = {"format": FORMAT, "opcode_counts": result.opcode_counts,
                        "pixel_work": result.pixel_work,
                        "presented": result.presented,
                        "reference_rgba_sha256": hashlib.sha256(pixel_bytes(result.pixels)).hexdigest()}
        expected = None
        if "expected" in manifest:
            entry = manifest["expected"]
            expected = read_pixels(_blob(args.manifest.parent, entry, FRAME_BYTES))
            fmt = entry.get("format")
            if fmt not in ("rgba8888", "xrgb8888"):
                raise CaptureError("expected format must be rgba8888 or xrgb8888")
            if not result.presented and fmt != "rgba8888":
                raise CaptureError("no-present batch expects raw BRAM RGBA, not native XRGB output")
            report["framebuffer"] = compare_pixels(result.pixels if fmt == "rgba8888" else result.xrgb_pixels, expected)
        report["exports"], report["comparison_coverage"] = compare_captured_exports(
            manifest, args.manifest.parent, result.exports)
        comparisons = ([report["framebuffer"]] if "framebuffer" in report else []) + list(report["exports"].values())
        report["gpu_comparison_status"] = captured_comparison_status(comparisons, report["comparison_coverage"])
        report["cpu_vs_captured_exports"], report["cpu_comparison_coverage"] = compare_cpu_captured_exports(
            manifest, args.manifest.parent, result.exports)
        report["cpu_comparison_status"] = captured_comparison_status(
            list(report["cpu_vs_captured_exports"].values()), report["cpu_comparison_coverage"])
        report["comparison_status"] = ("mismatch" if report["cpu_comparison_status"] == "mismatch"
                                       else report["gpu_comparison_status"])
        output = json.dumps(report, indent=2)
        print(output)
        if args.output:
            args.output.mkdir(parents=True, exist_ok=True)
            (args.output / "reference.rgba").write_bytes(pixel_bytes(result.pixels))
            (args.output / "reference.xrgb").write_bytes(pixel_bytes(result.xrgb_pixels))
            (args.output / "reference.ppm").write_bytes(_ppm(result.pixels))
            (args.output / "comparison.json").write_text(output + "\n", encoding="utf-8")
            if expected is not None:
                comparison = result.pixels if manifest["expected"]["format"] == "rgba8888" else result.xrgb_pixels
                (args.output / "difference.ppm").write_bytes(_ppm([0xFF if a != b else 0 for a, b in zip(comparison, expected)]))
        return 1 if report["comparison_status"] == "mismatch" else 0
    except (CaptureError, OSError, ValueError, TypeError, KeyError) as error:
        parser.exit(2, f"capture rejected: {error}\n")


if __name__ == "__main__":
    raise SystemExit(main())
