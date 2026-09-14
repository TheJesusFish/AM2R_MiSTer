#!/usr/bin/env python3
"""Extract one embedded AUDO entry from a GameMaker data.win file."""

import argparse
import struct
from pathlib import Path


def u32(data: bytes, offset: int) -> int:
    return struct.unpack_from("<I", data, offset)[0]


def find_chunk(data: bytes, wanted: bytes) -> tuple[int, int]:
    if data[:4] != b"FORM":
        raise ValueError("not a GameMaker FORM file")
    offset = 8
    while offset + 8 <= len(data):
        name = data[offset : offset + 4]
        size = u32(data, offset + 4)
        payload = offset + 8
        if name == wanted:
            return payload, size
        offset = payload + size
    raise ValueError(f"chunk {wanted.decode()} not found")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("data_win", type=Path)
    parser.add_argument("index", type=int)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()

    data = args.data_win.read_bytes()
    payload, chunk_size = find_chunk(data, b"AUDO")
    count = u32(data, payload)
    if not 0 <= args.index < count:
        raise ValueError(f"AUDO index {args.index} outside 0..{count - 1}")
    pointer = u32(data, payload + 4 + args.index * 4)
    if pointer == 0:
        raise ValueError(f"AUDO index {args.index} is absent")
    size = u32(data, pointer)
    start = pointer + 4
    end = start + size
    if not payload <= pointer < payload + chunk_size or end > len(data):
        raise ValueError("AUDO entry pointer or size is invalid")
    args.output.write_bytes(data[start:end])
    print(f"AUDO[{args.index}] count={count} offset=0x{start:x} size={size} output={args.output}")


if __name__ == "__main__":
    main()
