"""Apply AM2R 1.1's symmetric sv6 save-file XOR transform."""

from pathlib import Path
import argparse
import math


# Butterscotch's AM2R-compatible save files were created before a game_id
# built-in was registered, so GML's string(game_id) resolves to "undefined".
# Preserve that value for compatibility with every existing MiSTer save.
GAME_ID = "undefined"
KEY = "XOR_DFJykQ8xX3PuNnkLt6QviqALOLF8cxIDx1D63DAdph4KGQ4rOJ7"


def gml_round(value: float) -> int:
    return math.floor(value + 0.5)


def transform(data: bytearray, rate_argument: int = 2) -> None:
    gmid = GAME_ID
    for _ in range(5):
        gmid += gmid

    keys = []
    key_pos = 0
    for encrypted_pos in range(len(GAME_ID) * 5):
        # GameMaker strings are one-based. string_copy clamps position zero to
        # the first character, while string_char_at(0) returns an empty string.
        gmid_index = max(encrypted_pos - 1, 0)
        gmid_ord = ord(gmid[gmid_index])
        key_ord = 0 if key_pos == 0 else ord(KEY[key_pos - 1])
        keys.append(gmid_ord ^ (key_ord - math.floor(encrypted_pos / 3)))
        key_pos += 1
        if key_pos > len(KEY):
            key_pos = 1

    skip = gml_round(max(0, min(10, rate_argument)) * (len(data) / 10000.0))
    encrypted_pos = len(keys) - 1
    key_pos = 0
    file_pos = 0
    while file_pos < len(data):
        data[file_pos] ^= keys[key_pos] & 0xFF
        key_pos += 1
        if key_pos > encrypted_pos:
            key_pos = 1
        file_pos += 1 + skip


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("source", type=Path)
    parser.add_argument("destination", type=Path)
    args = parser.parse_args()
    data = bytearray(args.source.read_bytes())
    transform(data)
    args.destination.write_bytes(data)


if __name__ == "__main__":
    main()
