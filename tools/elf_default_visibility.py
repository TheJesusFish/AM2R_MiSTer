#!/usr/bin/env python3
"""Promote hidden/protected global symbols in ELF relocatable objects.

Zig builds its bundled libc++ as an internal static archive whose public ABI
symbols carry hidden visibility.  For the AM2R DMTCP runtime we deliberately
turn copies of those archive members into one private shared C++ runtime so
all DMTCP DSOs share RTTI, locale facets, and allocator state.

Only ELF32 little-endian relocatable files are accepted.  Local symbols and
all symbol bindings other than GLOBAL/WEAK are left untouched.
"""

from __future__ import annotations

import pathlib
import struct
import sys


ELF_MAGIC = b"\x7fELF"
ELFCLASS32 = 1
ELFDATA2LSB = 1
ET_REL = 1
SHT_SYMTAB = 2
STB_GLOBAL = 1
STB_WEAK = 2


def promote(path: pathlib.Path) -> int:
    data = bytearray(path.read_bytes())
    if len(data) < 52 or data[:4] != ELF_MAGIC:
        raise ValueError(f"{path}: not an ELF file")
    if data[4] != ELFCLASS32 or data[5] != ELFDATA2LSB:
        raise ValueError(f"{path}: expected ELF32 little-endian")
    if struct.unpack_from("<H", data, 16)[0] != ET_REL:
        raise ValueError(f"{path}: expected a relocatable ELF object")

    section_offset = struct.unpack_from("<I", data, 32)[0]
    section_size = struct.unpack_from("<H", data, 46)[0]
    section_count = struct.unpack_from("<H", data, 48)[0]
    if section_size < 40:
        raise ValueError(f"{path}: invalid ELF section-header size")

    changed = 0
    for index in range(section_count):
        header = section_offset + index * section_size
        if header + section_size > len(data):
            raise ValueError(f"{path}: truncated section-header table")
        section_type = struct.unpack_from("<I", data, header + 4)[0]
        if section_type != SHT_SYMTAB:
            continue
        symbols_offset = struct.unpack_from("<I", data, header + 16)[0]
        symbols_size = struct.unpack_from("<I", data, header + 20)[0]
        entry_size = struct.unpack_from("<I", data, header + 36)[0]
        if entry_size < 16 or symbols_offset + symbols_size > len(data):
            raise ValueError(f"{path}: invalid symbol table")
        for symbol in range(symbols_offset, symbols_offset + symbols_size, entry_size):
            binding = data[symbol + 12] >> 4
            visibility = data[symbol + 13] & 0x03
            if binding in (STB_GLOBAL, STB_WEAK) and visibility:
                data[symbol + 13] &= 0xFC
                changed += 1

    if changed:
        path.write_bytes(data)
    return changed


def main(argv: list[str]) -> int:
    if len(argv) < 2:
        print(f"usage: {argv[0]} OBJECT...", file=sys.stderr)
        return 2
    total = 0
    paths: list[pathlib.Path] = []
    for name in argv[1:]:
        path = pathlib.Path(name)
        if path.is_dir():
            paths.extend(sorted(path.rglob("*.o")))
        else:
            paths.append(path)
    for path in paths:
        total += promote(path)
    print(f"promoted {total} global/weak symbols across {len(paths)} objects")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
