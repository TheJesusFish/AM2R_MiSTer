#!/usr/bin/env python3
"""Generate synthetic-only, integrity-checked reserved-DDR GPU QA bundles.

No hardware access. The C executor independently revalidates every descriptor
and indirect packet/table pointer; CRC32 is corruption detection, not a signature.
All jobs initialize BRAM explicitly and end with raw RGBA STORE + END_NO_PRESENT.
"""
from __future__ import annotations
import argparse
import hashlib
import json
from pathlib import Path
import struct
import sys
import zlib

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "tools"), str(ROOT / "tests/renderer")]
import am2r_gpu_reference as gpu
from test_gpu_targets import command, transfer_fixture, bounded_clear_fixture
from test_gpu_generic import generic_fixture
from test_gpu_axis_stream import (axis_fixture, fallback_fixture, water_fixture,
                                  gather_fixture, gather_ordering_fixture,
                                  expected_blend, pattern, signed)

MAGIC, VERSION = 0x54473241, 1
INITIAL, OUTPUT = 0x2B000000, 0x2B100000
FIRST, END = 0x24000000, 0x2C000000
FAMILIES = ("generic", "transfer", "bounded-clear", "axis", "axis-fallback", "water",
            "gather", "gather-ordering", "affine")


def affine_fixture():
    """Bounded legacy affine cases with an independent wrapped pixel walker.

    expected_blend is the independent byte equation from the axis fixtures,
    not the scalar renderer's tint/blend implementation. No game data is used.
    """
    source = 0x24800000
    memory = struct.pack("<8192I", *pattern(8192, 113))
    initial = pattern(320 * 240, 67)
    expected = list(initial)
    commands = []

    def pair(a, b):
        return (a & 0xFFFFFFFF) | (b & 0xFFFFFFFF) << 32

    def add(*, width=17, height=9, x=0, y=0, base=source, stride=260,
            u=-65536, v=32768, ux=65536, vx=0, uy=0, vy=65536,
            umin=0, umax=16 << 16, vmin=0, vmax=8 << 16,
            tint=0xFFFFFFFF, mode=0, setup=True):
        if setup:
            commands.append(command(5, base=tint, w0=5 | mode << 8))
        elif tint != 0xFFFFFFFF or mode:
            raise AssertionError("standalone affine must use reset one-shot state")
        commands.append(command(4, width, height, base=base, stride=stride, x=x, y=y,
            w3=pair(umin, umax), w4=pair(vmin, vmax), w5=pair(u, v),
            w6=pair(ux, vx), w7=pair(uy, vy)))
        for row in range(height):
            for col in range(width):
                if not (0 <= x + col < 320 and 0 <= y + row < 240):
                    continue
                su = signed(u + row * uy + col * ux, 32)
                sv = signed(v + row * vy + col * vx, 32)
                if not (umin <= su < umax and vmin <= sv < vmax):
                    continue
                address = (base + ((sv // 65536) % 65536) * stride + (su // 65536) * 4) % (1 << 32)
                offset = address - source
                if not 0 <= offset <= len(memory) - 4:
                    raise AssertionError("affine fixture source escaped its allocation")
                pixel = int.from_bytes(memory[offset:offset + 4], "little")
                at = (y + row) * 320 + x + col
                expected[at] = expected_blend(pixel, expected[at], tint, mode, False)

    shapes = (
        {},
        dict(width=17, height=11, u=32768, v=15 << 16, ux=0, vx=-65536,
             uy=65536, vy=0, umax=12 << 16, vmax=16 << 16),
        dict(width=31, height=7, ux=32768, vx=16384, uy=16384, vy=65536,
             umax=24 << 16, vmax=16 << 16),
        dict(width=33, height=5, u=31 << 16, v=3 << 16, ux=-65536,
             uy=32768, vy=-32768, umax=32 << 16),
        dict(width=32, height=3, u=(32 << 16) - 1, v=3 << 16, ux=0, vy=0,
             umax=32 << 16),
        # Exact signed32 wrap can bring a later sample back inside the bounds.
        dict(width=5, height=3, u=0x7FFF0000, v=0, ux=-2147418112,
             uy=65536, vy=65536),
        dict(width=5, height=3, u=0, v=0x7FFF0000, ux=65536,
             vx=-2147418112, vy=65536),
        # Negative V uses the hardware's unsigned row-index truncation; zero
        # stride makes this a small, legal test of signed source bounds.
        dict(width=9, height=3, stride=0, u=0, v=-65536,
             vmin=-2 << 16, vmax=65536, vy=32768),
    )
    for mode in range(3):
        for index, shape in enumerate(shapes):
            add(x=(index % 3) * 81 + (index & 1), y=mode * 65 + (index // 3) * 16,
                mode=mode, tint=(0x00C159A3 if mode == 2 else 0x817FC13B), **shape)
    # Clipping on all four destination borders retains UV phase; first sample
    # and half-open U/V maximum boundaries must not fetch excluded texels.
    add(width=327, height=5, x=-3, y=-2, umax=64 << 16, tint=0x80FFFFFF)
    add(width=17, height=9, x=315, y=237, mode=1, tint=0x01FEFF80)
    # A configured draw must not tint/blend its following standalone draw.
    add(x=1, y=211, mode=2, tint=0x00ABCDEF)
    add(x=21, y=211, setup=False)
    # Endpoint alpha/RGB values exercise rounded tint, not the newer floor mode.
    for index, tint in enumerate((0, 0xFFFFFFFF, 0xFF000000, 0x00FFFFFF, 0x0101FEFF, 0xFEFF0001)):
        add(width=33, height=2, x=index * 43, y=225, u=0, umax=33 << 16,
            tint=tint, mode=index % 3)
    commands.append(command(12))
    return b"".join(commands), {source: memory}, initial, expected


def source_fixture(name):
    if name == "affine":
        return affine_fixture()
    if name == "generic":
        commands, regions, _ = generic_fixture()
        return commands, regions, None, None
    if name == "transfer":
        commands, regions, expected, _ = transfer_fixture()
        return commands, regions, None, expected
    if name == "bounded-clear":
        commands, regions, initial, expected = bounded_clear_fixture()
        return commands, regions, initial, expected
    factory = {"axis": axis_fixture, "axis-fallback": fallback_fixture, "water": water_fixture,
               "gather": gather_fixture, "gather-ordering": gather_ordering_fixture}[name]
    commands, regions, initial, expected, _, _ = factory()
    return commands, regions, initial, expected


def merge_regions(original, allocations):
    intervals = []
    for base, size in [(a, len(data)) for a, data in original.items()] + list(allocations):
        low, high = base & ~127, (base + size + 127) & ~127
        if not FIRST <= low < high <= END:
            raise ValueError("fixture exceeds reserved GPU DDR")
        intervals.append((low, high))
    merged = []
    for low, high in sorted(intervals):
        if merged and low <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(high, merged[-1][1]))
        else:
            merged.append((low, high))
    result = {low: bytearray(high - low) for low, high in merged}
    for base, data in original.items():
        for low, payload in result.items():
            if low <= base and base + len(data) <= low + len(payload):
                payload[base - low:base - low + len(data)] = data
                break
        else:
            raise AssertionError("input lost while merging")
    return {base: bytes(data) for base, data in result.items()}


def make_fixture(name):
    commands, regions, initial, independent_pixels = source_fixture(name)
    original = gpu.render(commands, regions, initial)
    if independent_pixels is not None and original.pixels != independent_pixels:
        raise AssertionError(f"independent expected pixels disagree for {name}")
    words = list(struct.iter_unpack("<8Q", commands))
    if words[-1][0] not in (0, 12):
        raise ValueError("fixture lacks a unique final completion")
    prefix = command(1, base=0)
    if initial is not None:
        regions = dict(regions)
        regions[INITIAL] = struct.pack(f"<{len(initial)}I", *initial)
        prefix += command(10, 320, 240, base=INITIAL, stride=1280)
    commands = (prefix + commands[:-64] + command(11, 320, 240, base=OUTPUT, stride=1280) + command(12))
    # First render enforces the original strict dependency contract: no new
    # zero allocation may hide an unknown read in the authored fixture.
    rendered = gpu.render(commands, regions)
    assert rendered.pixels == original.pixels
    regions = merge_regions(regions, [(base, len(data)) for base, data in rendered.exports.items()])
    rendered = gpu.render(commands, regions)
    assert rendered.pixels == original.pixels
    expected = {base: bytearray(data) for base, data in regions.items()}
    for address, data in rendered.exports.items():
        for base, payload in expected.items():
            if base <= address and address + len(data) <= base + len(payload):
                payload[address - base:address - base + len(data)] = data
                break
        else:
            raise AssertionError("export lost while merging")
    expected = {base: bytes(data) for base, data in expected.items()}
    # Check all declared DDR bytes, not just raster outputs: immutable texture,
    # packet, table and row/cache padding must also remain unchanged.
    payload = bytearray(commands)
    for spans in (regions, expected):
        for base, data in spans.items():
            payload += struct.pack("<2I", base, len(data)) + data
    header = struct.pack("<10I", MAGIC, VERSION, 40 + len(payload), len(commands) // 64,
                         len(regions), len(expected), 31, zlib.crc32(payload), 0, 0)
    blob = header + payload
    metadata = dict(name=name, commands=len(commands)//64, regions=len(regions),
                    bytes_compared=sum(map(len, expected.values())), bytes=len(blob),
                    sha256=hashlib.sha256(blob).hexdigest(), crc32=f"{zlib.crc32(payload):08x}",
                    opcodes=dict(rendered.opcode_counts), completion="END_NO_PRESENT",
                    output_format="raw RGBA8888", native_framebuffer_access=False)
    return blob, metadata


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    metadata = []
    for name in FAMILIES:
        blob, report = make_fixture(name)
        target = args.output / f"{name}.a2gt"
        if target.exists() and target.read_bytes() != blob:
            raise SystemExit(f"Refusing to overwrite different fixture: {target}")
        target.write_bytes(blob)
        metadata.append(report)
        print(f"{name}: {report['commands']} commands, {report['bytes_compared']} checked bytes, SHA256 {report['sha256']}")
    (args.output / "manifest.json").write_text(json.dumps(metadata, indent=2) + "\n")


if __name__ == "__main__":
    main()
