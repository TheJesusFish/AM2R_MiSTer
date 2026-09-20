#!/usr/bin/env python3
"""Locate symbols from an unstripped ARM ELF in a matching stripped build.

The two links may lay functions out differently, so this votes on exact chunks
inside each function rather than assuming one global address delta.  It is a
diagnostic helper; it never modifies either executable.
"""

from __future__ import annotations

import argparse
import collections
import struct
from pathlib import Path


def unpack(fmt: str, data: bytes, offset: int):
    return struct.unpack_from("<" + fmt, data, offset)


class Elf32:
    def __init__(self, path: Path):
        self.path = path
        self.data = path.read_bytes()
        if self.data[:7] != b"\x7fELF\x01\x01\x01":
            raise ValueError(f"{path} is not a little-endian ELF32 file")

        header = unpack("16sHHIIIIIHHHHHH", self.data, 0)
        self.phoff, self.shoff = header[5], header[6]
        self.phentsize, self.phnum = header[9], header[10]
        self.shentsize, self.shnum = header[11], header[12]

        self.loads = []
        for i in range(self.phnum):
            off = self.phoff + i * self.phentsize
            p_type, p_offset, p_vaddr, _, p_filesz, p_memsz, p_flags, _ = unpack(
                "IIIIIIII", self.data, off
            )
            if p_type == 1:
                self.loads.append((p_vaddr, p_vaddr + p_memsz, p_offset, p_filesz, p_flags))

        self.sections = []
        for i in range(self.shnum):
            off = self.shoff + i * self.shentsize
            self.sections.append(unpack("IIIIIIIIII", self.data, off))

    def vaddr_to_offset(self, address: int) -> int:
        for start, end, file_off, file_size, _ in self.loads:
            if start <= address < end and address - start < file_size:
                return file_off + address - start
        raise ValueError(f"virtual address 0x{address:x} is not file-backed")

    def symbols(self):
        for section in self.sections:
            _, sh_type, _, _, sh_offset, sh_size, sh_link, _, _, sh_entsize = section
            if sh_type != 2 or not sh_entsize:  # SHT_SYMTAB
                continue
            strings = self.sections[sh_link]
            string_data = self.data[strings[4] : strings[4] + strings[5]]
            for off in range(sh_offset, sh_offset + sh_size, sh_entsize):
                name_off, value, size, info, _, shndx = unpack("IIIBBH", self.data, off)
                if not name_off or not value or not size or shndx == 0:
                    continue
                end = string_data.find(b"\0", name_off)
                name = string_data[name_off:end].decode("utf-8", "replace")
                yield name, value, size, info


def all_offsets(haystack: bytes, needle: bytes):
    start = 0
    while True:
        found = haystack.find(needle, start)
        if found < 0:
            return
        yield found
        start = found + 1


def match_symbol(source: Elf32, target: Elf32, value: int, size: int, chunk_size: int):
    source_offset = source.vaddr_to_offset(value)
    code = source.data[source_offset : source_offset + size]
    votes = collections.Counter()
    evidence = collections.defaultdict(list)
    step = 4
    for relative in range(0, max(0, len(code) - chunk_size + 1), step):
        chunk = code[relative : relative + chunk_size]
        matches = list(all_offsets(target.data, chunk))
        if len(matches) != 1:
            continue
        candidate = matches[0] - relative
        votes[candidate] += 1
        evidence[candidate].append((relative, matches[0]))
    return votes, evidence


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("source", type=Path, help="unstripped ELF with symbols")
    parser.add_argument("target", type=Path, help="stripped ELF to identify")
    parser.add_argument("symbols", nargs="+")
    parser.add_argument("--chunk", type=int, default=20)
    args = parser.parse_args()

    source = Elf32(args.source)
    target = Elf32(args.target)
    wanted = set(args.symbols)
    found = {name: (value, size) for name, value, size, _ in source.symbols() if name in wanted}
    missing = wanted - found.keys()
    if missing:
        print("missing symbols: " + ", ".join(sorted(missing)))

    for name in args.symbols:
        if name not in found:
            continue
        value, size = found[name]
        votes, evidence = match_symbol(source, target, value, size, args.chunk)
        print(f"{name}: source_vaddr=0x{value:x} size={size}")
        for candidate, count in votes.most_common(5):
            try:
                target_vaddr = next(
                    start + candidate - file_off
                    for start, end, file_off, file_size, _ in target.loads
                    if file_off <= candidate < file_off + file_size
                )
                address = f"0x{target_vaddr:x}"
            except StopIteration:
                address = "not-loadable"
            spans = ",".join(f"+0x{rel:x}" for rel, _ in evidence[candidate][:6])
            print(f"  candidate_file=0x{candidate:x} vaddr={address} votes={count} evidence={spans}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
