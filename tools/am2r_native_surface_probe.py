#!/usr/bin/env python3
"""Hash-locked, read-only AM2R 1.1 Windows logical-surface census.

No debugger attach, injection, process writes, suspension, input, game calls,
launch, termination, or save access. See docs/native-surface-probe.md.
"""
import argparse
import ctypes
import hashlib
import json
import math
import os
from pathlib import Path
import struct
import time

EXE_SHA256 = "fe6e75f402235e126eb75a16d2a918dc2aef1b1e3f41e5c36d198d277bc94267"
DATA_SHA256 = "36e4a251d7b687f2d742a8e911cb1e1185aea99e36529fcf32cd18d445a355e3"
IMAGE_BASE = 0x400000
MAX_BUCKETS = 4096
MAX_NODES = 16384
MAX_INSTANCES = 1024
MAX_READ_CALLS = 32768
MAX_READ_BYTES = 4 * 1024 * 1024


class InvalidSnapshot(ValueError):
    """Transient or unsupported metadata: do not publish as a valid census."""


class ReadBudget:
    """Aggregate cap across all objects, snapshots and retries in one sample."""
    def __init__(self, read, calls=MAX_READ_CALLS, byte_limit=MAX_READ_BYTES):
        self.read = read
        self.remaining_calls = calls
        self.remaining_bytes = byte_limit

    def __call__(self, address, size):
        if self.remaining_calls <= 0 or size > self.remaining_bytes:
            raise InvalidSnapshot("aggregate sample read budget exhausted")
        self.remaining_calls -= 1
        self.remaining_bytes -= size
        return self.read(address, size)


def checked_read(read, address, size):
    if not 0x10000 <= address <= 0xFFFFFFFF or not 0 < size <= 1024 * 1024:
        raise InvalidSnapshot("read outside bounded 32-bit user address range")
    if address + size > 0x100000000:
        raise InvalidSnapshot("read wraps address space")
    value = read(address, size)
    if len(value) != size:
        raise InvalidSnapshot("short read")
    return value


def words(read, address, count):
    if address & 3:
        raise InvalidSnapshot("unaligned metadata pointer")
    return struct.unpack("<" + "I" * count, checked_read(read, address, count * 4))


def bounded_map(read, pointer, mask, count=None):
    """Return key/value pairs from the native 8-byte-bucket hash map."""
    if mask + 1 > MAX_BUCKETS or (mask & (mask + 1)) != 0:
        raise InvalidSnapshot("unsupported map bucket mask")
    if count is not None and count > MAX_NODES:
        raise InvalidSnapshot("map entry limit exceeded")
    buckets = words(read, pointer, (mask + 1) * 2)
    result, seen, keys = [], set(), set()
    for bucket in range(mask + 1):
        node, tail = buckets[bucket * 2:bucket * 2 + 2]
        previous = 0
        while node:
            if node in seen or len(seen) >= MAX_NODES:
                raise InvalidSnapshot("cyclic/shared/oversized map chain")
            seen.add(node)
            before, after, key, value = words(read, node, 4)
            if before != previous or key & mask != bucket or key in keys:
                raise InvalidSnapshot("map links, key ownership, or uniqueness changed")
            keys.add(key)
            result.append((key, value))
            previous, node = node, after
        if previous != tail:
            raise InvalidSnapshot("map tail changed")
    if count is not None and len(result) != count:
        raise InvalidSnapshot("map entry count changed")
    if words(read, pointer, (mask + 1) * 2) != buckets:
        raise InvalidSnapshot("map buckets changed during read")
    return result


def read_name(read, pointer):
    # One-byte reads avoid crossing the readable page beyond a short string.
    name = bytearray()
    for offset in range(128):
        byte = checked_read(read, pointer + offset, 1)[0]
        if byte == 0:
            return name.decode("ascii", "strict")
        name.append(byte)
    raise InvalidSnapshot("unterminated native variable name")


def light_instances(read, va):
    registry = words(read, va(0x6EEBB4), 1)[0]
    pointer, mask, count = words(read, registry, 3)
    objects = bounded_map(read, pointer, mask, count)
    obj = dict(objects).get(727)  # Hash-locked data.win: oLightEngine.
    if not obj:
        return []
    names_pointer, names_count = words(read, va(0x907EC8), 2)
    if not 0 < names_count <= 20000:
        raise InvalidSnapshot("unsupported variable-name registry")
    names = words(read, names_pointer, names_count)
    first = words(read, obj + 0xB8, 1)[0]
    link, previous, seen, result = first, 0, set(), []
    while link:
        if link in seen or len(seen) >= MAX_INSTANCES:
            raise InvalidSnapshot("cyclic/shared/oversized instance list")
        seen.add(link)
        after, before, instance = words(read, link, 3)
        # This list's +0 is next, unlike the hash node's +4 next.
        if before != previous:
            raise InvalidSnapshot("instance links changed")
        if instance and checked_read(read, instance + 8, 2) == b"\0\0":
            identity = words(read, instance + 24, 1)[0]
            table = words(read, instance + 0xC8, 1)[0]
            head_words = words(read, table + 4, 64)
            variables, variable_nodes = [], set()
            for bucket, head in enumerate(head_words):
                node = head
                while node:
                    if node in variable_nodes or len(variable_nodes) >= MAX_NODES:
                        raise InvalidSnapshot("cyclic/shared/oversized variable chain")
                    variable_nodes.add(node)
                    raw = checked_read(read, node, 28)
                    node, key = struct.unpack_from("<I", raw)[0], struct.unpack_from("<I", raw, 24)[0]
                    if key & 63 != bucket:
                        raise InvalidSnapshot("variable bucket ownership changed")
                    if 100000 <= key < 100000 + names_count:
                        if read_name(read, names[key - 100000]) == "surf":
                            kind = struct.unpack_from("<I", raw, 20)[0]
                            value = struct.unpack_from("<d", raw, 8)[0]
                            if kind != 0 or not math.isfinite(value) or value != int(value) or not -1 <= value <= 0x7FFFFFFF:
                                raise InvalidSnapshot("unsupported light surface-handle value")
                            variables.append(int(value))
            if len(variables) > 1 or words(read, table + 4, 64) != head_words:
                raise InvalidSnapshot("light variables changed")
            result.append({"instance_id": identity, "surf": variables[0] if variables else None})
        previous, link = link, after
    if words(read, obj + 0xB8, 1)[0] != first:
        raise InvalidSnapshot("instance-list head changed")
    if words(read, registry, 3) != (pointer, mask, count):
        raise InvalidSnapshot("object map changed")
    return sorted(result, key=lambda entry: entry["instance_id"])


def read_snapshot(read, base=IMAGE_BASE, include_light=True):
    if not isinstance(read, ReadBudget):
        read = ReadBudget(read)
    va = lambda address: base + address - IMAGE_BASE
    room = words(read, va(0x8F0F48), 1)[0]
    if room > 100000 and room != 0xFFFFFFFF:
        raise InvalidSnapshot("unsupported room index")
    metadata = words(read, va(0x6A13D4), 3)
    pointer, mask, count = metadata
    entries = bounded_map(read, pointer, mask, count)
    surfaces, records = [], set()
    for identity, record in entries:
        if not record:
            continue  # Native surface_exists is false for a null map value.
        if record in records:
            raise InvalidSnapshot("shared surface record")
        records.add(record)
        key, texture, width, height = words(read, record, 4)
        if identity > 0x7FFFFFFF or key != identity or width > 65535 or height > 65535:
            raise InvalidSnapshot("unsupported surface record")
        if bool(width) != bool(height) or (texture > 0x7FFFFFFF and texture != 0xFFFFFFFF):
            raise InvalidSnapshot("inconsistent surface record")
        surfaces.append({"id": identity, "texture_handle": texture if texture != 0xFFFFFFFF else -1,
                         "width": width, "height": height})
    lights = light_instances(read, va) if include_light else None
    if words(read, va(0x6A13D4), 3) != metadata or words(read, va(0x8F0F48), 1)[0] != room:
        raise InvalidSnapshot("room/surface metadata changed during sample")
    surfaces.sort(key=lambda entry: entry["id"])
    return {"room": room if room != 0xFFFFFFFF else -1, "map_entries": count,
            "logical_surface_count": len(surfaces),
            "surfaces_512x256": sum(s["width"] == 512 and s["height"] == 256 for s in surfaces),
            "surfaces": surfaces, "light_instances": lights}


def coherent_snapshot(read, base=IMAGE_BASE, include_light=True, attempts=3):
    if not 1 <= attempts <= 10:
        raise ValueError("attempts must be 1..10")
    if not isinstance(read, ReadBudget):
        read = ReadBudget(read)
    reason = "two consecutive surface snapshots differ"
    for _ in range(attempts):
        try:
            first = read_snapshot(read, base, include_light)
            second = read_snapshot(read, base, include_light)
            if first == second:
                return second
        except (OSError, InvalidSnapshot, UnicodeError) as error:
            reason = str(error)
    raise InvalidSnapshot(reason)


class WindowsReader:
    def __init__(self, pid):
        if not 0 < pid <= 0xFFFFFFFF:
            raise ValueError("PID must fit a positive DWORD")
        if os.name != "nt":
            raise OSError("live process sampling requires Windows")
        from ctypes import wintypes as wt
        self.kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        k = self.kernel
        k.OpenProcess.argtypes, k.OpenProcess.restype = [wt.DWORD, wt.BOOL, wt.DWORD], wt.HANDLE
        k.CloseHandle.argtypes, k.CloseHandle.restype = [wt.HANDLE], wt.BOOL
        k.QueryFullProcessImageNameW.argtypes = [wt.HANDLE, wt.DWORD, wt.LPWSTR, ctypes.POINTER(wt.DWORD)]
        k.QueryFullProcessImageNameW.restype = wt.BOOL
        k.ReadProcessMemory.argtypes = [wt.HANDLE, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_size_t, ctypes.POINTER(ctypes.c_size_t)]
        k.ReadProcessMemory.restype = wt.BOOL
        self.handle = k.OpenProcess(0x1000 | 0x10, False, pid)  # QUERY_LIMITED_INFORMATION | VM_READ only.
        if not self.handle:
            raise ctypes.WinError(ctypes.get_last_error())
        try:
            path, length = ctypes.create_unicode_buffer(32768), wt.DWORD(32768)
            if not k.QueryFullProcessImageNameW(self.handle, 0, path, ctypes.byref(length)):
                raise ctypes.WinError(ctypes.get_last_error())
            self.path = Path(path.value)
            for file, expected in ((self.path, EXE_SHA256), (self.path.with_name("data.win"), DATA_SHA256)):
                with file.open("rb") as stream:
                    actual = hashlib.file_digest(stream, "sha256").hexdigest()
                if actual != expected:
                    raise ValueError(f"unsupported native identity: {file.name} SHA-256 {actual}")
            # This exact non-relocated reference is required. No guessed offsets.
            self.base = IMAGE_BASE
            if self.read(self.base, 2) != b"MZ":
                raise ValueError("native reference image is not at verified base 0x400000")
            for address, expected in ((0x4B4540, bytes.fromhex("83ec088b44241c56")),
                                      (0x4B4590, bytes.fromhex("83ec088b44241cf2")),
                                      (0x4253B0, bytes.fromhex("a1d8136a008b4c24"))):
                if self.read(address, len(expected)) != expected:
                    raise ValueError("live native code differs from verified surface getter")
        except BaseException:
            self.close()
            raise

    def read(self, address, size):
        buffer, got = ctypes.create_string_buffer(size), ctypes.c_size_t()
        if not self.kernel.ReadProcessMemory(self.handle, address, buffer, size, ctypes.byref(got)) or got.value != size:
            raise OSError(f"ReadProcessMemory {address:#x}/{size}: {ctypes.get_last_error()}")
        return buffer.raw

    def close(self):
        if self.handle:
            self.kernel.CloseHandle(self.handle)
            self.handle = None


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pid", required=True, type=int, help="existing native AM2R 1.1 process; never launched by this tool")
    parser.add_argument("--samples", default=1, type=int)
    parser.add_argument("--interval", default=1.0, type=float, help="seconds between samples")
    parser.add_argument("--no-light", action="store_true", help="omit optional light-instance correlation")
    args = parser.parse_args()
    if not 0 < args.pid <= 0xFFFFFFFF or not 1 <= args.samples <= 36000 or not math.isfinite(args.interval) or not .05 <= args.interval <= 3600:
        parser.error("require positive DWORD pid, 1..36000 samples, and interval .05..3600 seconds")
    memory = WindowsReader(args.pid)
    try:
        print(json.dumps({"type": "identity", "pid": args.pid, "exe_sha256": EXE_SHA256,
                          "data_sha256": DATA_SHA256, "image_base": hex(memory.base),
                          "method": "read-only logical surface registry, not a VRAM byte measurement"}), flush=True)
        for index in range(args.samples):
            try:
                result = coherent_snapshot(memory.read, memory.base, not args.no_light)
                result.update(type="snapshot", sample=index, time_ns=time.time_ns())
            except InvalidSnapshot as error:
                result = {"type": "unstable", "sample": index, "time_ns": time.time_ns(), "reason": str(error)}
            print(json.dumps(result, sort_keys=True), flush=True)
            if index + 1 < args.samples:
                time.sleep(args.interval)
    finally:
        memory.close()


if __name__ == "__main__":
    main()
