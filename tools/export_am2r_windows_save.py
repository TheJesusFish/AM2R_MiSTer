"""Export an ordinary MiSTer AM2R 1.1 save to an exclusive Windows copy.

Never changes the source or overwrites a destination. This is not a .fast or
.dmtcp converter. The Windows 1.1 runner accepts ds_list format 301, whereas
Butterscotch writes 303. These eight AM2R lists contain only numeric values;
convert their representation without changing any game-progress values.
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import math
import struct
from pathlib import Path

from am2r_save_crypt import GAME_ID, transform


WINDOWS_GAME_ID = "971912648"
HEADER = "[AM2R SaveData V7.0]"
COUNTS = (50, 350, 350, 100, 20, 100, 50, 6400)


def rc4(text: str) -> str:
    """Match GML RC4 over characters; file_text stores the result as UTF-8."""
    key = b"HEADER_KEY"
    state = list(range(256))
    j = 0
    for i in range(256):
        j = (j + state[i] + key[i % len(key)]) % 256
        state[i], state[j] = state[j], state[i]
    i = j = 0
    out = []
    for char in text:
        i = (i + 1) % 256
        j = (j + state[i]) % 256
        state[i], state[j] = state[j], state[i]
        out.append(chr(ord(char) ^ state[(state[i] + state[j]) % 256]))
    return "".join(out)


def decode_save(data: bytes, game_id: str, *, native: bool = False) -> list[bytes]:
    decoded = bytearray(data)
    transform(decoded, game_id=game_id, native_char_at_zero=native)
    lines = bytes(decoded).split(b"\n")
    if len(lines) != 10 or lines[-1] != b"":
        raise ValueError("Expected the header and eight ordinary-save sections")
    lines = [line.removesuffix(b"\r") for line in lines[:-1]]
    if rc4(lines[0].decode("utf-8")) != HEADER:
        raise ValueError("Save header is invalid for the selected runtime")
    return [bytes.fromhex(base64.b64decode(line, validate=True).decode("ascii"))
            for line in lines[1:]]


def read_numeric_list(blob: bytes, magic: int, count: int) -> list[float]:
    if len(blob) < 8 or struct.unpack_from("<II", blob) != (magic, count):
        raise ValueError("Unexpected list format or number of save fields")
    pos = 8
    values = []
    for _ in range(count):
        tag, = struct.unpack_from("<I", blob, pos)
        pos += 4
        if magic == 301 and tag != 0:
            raise ValueError("Native export contains a non-real numeric field")
        if tag in (0, 13):
            value, = struct.unpack_from("<d", blob, pos)
            pos += 8
            if tag == 13 and value not in (0.0, 1.0):
                raise ValueError("Invalid boolean save field")
        elif tag in (7, 10):
            integer, = struct.unpack_from("<i" if tag == 7 else "<q", blob, pos)
            pos += 4 if tag == 7 else 8
            value = float(integer)
            if int(value) != integer:
                raise ValueError("Integer cannot be preserved exactly as a native real")
        else:
            raise ValueError(f"Unsupported save field type {tag}; refusing a lossy export")
        if not math.isfinite(value):
            raise ValueError("Non-finite save field")
        values.append(value)
    if pos != len(blob):
        raise ValueError("Unexpected trailing list data")
    return values


def convert(source: bytes) -> bytes:
    sections = decode_save(source, GAME_ID)
    original_values = [read_numeric_list(blob, 303, count)
                       for blob, count in zip(sections, COUNTS, strict=True)]
    lines = [rc4(HEADER).encode("utf-8")]
    for values in original_values:
        blob = struct.pack("<II", 301, len(values))
        blob += b"".join(struct.pack("<Id", 0, value) for value in values)
        lines.append(base64.b64encode(blob.hex().upper().encode("ascii")))
    # Use Windows line endings and recompute the XOR stride for the new size.
    result = bytearray(b"\r\n".join(lines) + b"\r\n")
    transform(result, game_id=WINDOWS_GAME_ID, native_char_at_zero=True)
    check = decode_save(result, WINDOWS_GAME_ID, native=True)
    converted_values = [read_numeric_list(blob, 301, count)
                        for blob, count in zip(check, COUNTS, strict=True)]
    if converted_values != original_values:
        raise ValueError("Export changed game-progress values")
    return bytes(result)


def export_file(source: Path, destination: Path) -> dict:
    if source.resolve() == destination.resolve():
        raise ValueError("Source and destination must be different")
    original = source.read_bytes()
    converted = convert(original)
    with destination.open("xb") as output:
        output.write(converted)
        output.flush()
    if destination.read_bytes() != converted:
        raise OSError("Export readback did not match")
    if source.read_bytes() != original:
        raise OSError("Source changed during export; obtain a stable copy")
    return {
        "source": str(source), "destination": str(destination),
        "source_bytes": len(original), "destination_bytes": len(converted),
        "source_sha256": hashlib.sha256(original).hexdigest(),
        "destination_sha256": hashlib.sha256(converted).hexdigest(),
        "numeric_fields_preserved": sum(COUNTS), "source_unchanged": True,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path)
    parser.add_argument("destination", type=Path)
    args = parser.parse_args()
    try:
        result = export_file(args.source, args.destination)
    except (OSError, ValueError, struct.error) as error:
        parser.exit(1, f"Export refused: {error}\n")
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
