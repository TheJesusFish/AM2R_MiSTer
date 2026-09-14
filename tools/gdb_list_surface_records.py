"""List software surfaces and their MiSTer GPU texture-cache records."""

import binascii
import gdb


runner = gdb.parse_and_eval("g_runner").dereference()
renderer = runner["renderer"].cast(gdb.lookup_type("SWRenderer").pointer()).dereference()
inferior = gdb.selected_inferior()

surface_count = int(renderer["surfaceCount"])
print(
    "SURFACE_TABLE count=%d app=%d current=%d"
    % (surface_count, int(runner["applicationSurfaceId"]), int(renderer["currentSurface"]))
)
surface_by_address = {}
for index in range(surface_count):
    exists = bool(renderer["surfaceExistsFlags"][index])
    address = int(renderer["surfacePixels"][index])
    width = int(renderer["surfaceWidths"][index])
    height = int(renderer["surfaceHeights"][index])
    revision = int(renderer["surfaceRevisions"][index])
    if address:
        surface_by_address[address] = index
    crc = "-"
    size = max(0, width) * max(0, height) * 4
    if exists and address and 0 < size <= 1024 * 1024:
        try:
            crc = "%08x" % (
                binascii.crc32(bytes(inferior.read_memory(address, size))) & 0xFFFFFFFF
            )
        except gdb.MemoryError:
            crc = "unreadable"
    print(
        "SURFACE id=%d exists=%d address=0x%x size=%dx%d bytes=%d revision=%d crc32=%s"
        % (index, 1 if exists else 0, address, width, height, size, revision, crc)
    )

record_count = int(gdb.parse_and_eval("g_gpu_texture_record_count"))
records = gdb.parse_and_eval("g_gpu_texture_records")
print("TEXTURE_RECORD_TABLE count=%d" % record_count)
for index in range(record_count):
    record = records[index]
    source = int(record["source"])
    shadow = int(record["shadow"])
    size = int(record["bytes"])
    shadow_valid = bool(record["shadow_valid"])
    crc = "-"
    if shadow_valid and shadow and 0 < size <= 1024 * 1024:
        try:
            crc = "%08x" % (
                binascii.crc32(bytes(inferior.read_memory(shadow, size))) & 0xFFFFFFFF
            )
        except gdb.MemoryError:
            crc = "unreadable"
    print(
        "TEXTURE_RECORD index=%d surface=%d source=0x%x shadow=0x%x bytes=%d "
        "physical=%08x,%08x active=%d dynamic=%d shadow_valid=%d "
        "generation=%d source_revision=%d source_revision_valid=%d crc32=%s"
        % (
            index,
            surface_by_address.get(source, -1),
            source,
            shadow,
            size,
            int(record["physical"][0]),
            int(record["physical"][1]),
            int(record["active"]),
            1 if bool(record["dynamic"]) else 0,
            1 if shadow_valid else 0,
            int(record["generation"]),
            int(record["source_revision"]),
            1 if bool(record["source_revision_valid"]) else 0,
            crc,
        )
    )
