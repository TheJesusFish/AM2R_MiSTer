"""Inspect live MiSTer GPU subtract descriptors and their texture records."""

import gdb


def signed(value, bits):
    value = int(value) & ((1 << bits) - 1)
    sign = 1 << (bits - 1)
    return value - (1 << bits) if value & sign else value


def rgba_stats(data):
    if not data:
        return "empty"
    pixels = len(data) // 4
    mins = [255, 255, 255, 255]
    maxs = [0, 0, 0, 0]
    zeros = [0, 0, 0, 0]
    full = [0, 0, 0, 0]
    for offset in range(0, pixels * 4, 4):
        for channel in range(4):
            value = data[offset + channel]
            mins[channel] = min(mins[channel], value)
            maxs[channel] = max(maxs[channel], value)
            zeros[channel] += value == 0
            full[channel] += value == 255
    return "pixels=%d min=%s max=%s zero=%s full=%s" % (
        pixels, mins, maxs, zeros, full
    )


inferior = gdb.selected_inferior()
count = int(gdb.parse_and_eval("g_gpu_command_count"))
commands = gdb.parse_and_eval("g_gpu_commands")
records = gdb.parse_and_eval("g_gpu_texture_records")
record_count = int(gdb.parse_and_eval("g_gpu_texture_record_count"))
gpu_textures = int(gdb.parse_and_eval("g_gpu_textures"))

print("GPU_SUBTRACT_SCAN commands=%d records=%d textures=%#x" % (
    count, record_count, gpu_textures
))

found = 0
for command_index in range(count):
    words = [int(commands[command_index]["word"][i]) for i in range(8)]
    if not (words[0] & (1 << 9)):
        continue
    found += 1
    opcode = words[0] & 0xff
    width = (words[0] >> 16) & 0xffff
    height = (words[0] >> 32) & 0xffff
    source = words[1] & 0xffffffff
    stride = (words[1] >> 32) & 0xffffffff
    x = signed(words[2], 16)
    y = signed(words[2] >> 16, 16)
    print(
        "GPU_SUBTRACT command=%d opcode=%d size=%dx%d dst=%d,%d "
        "source=%#x stride=%d words=%s"
        % (command_index, opcode, width, height, x, y, source, stride,
           " ".join("%016x" % word for word in words))
    )

    if opcode not in (2, 4):
        continue
    for record_index in range(record_count):
        record = records[record_index]
        bytes_count = int(record["bytes"])
        for copy in range(2):
            physical = int(record["physical"][copy])
            if not physical or not (physical <= source < physical + bytes_count):
                continue
            offset = source - physical
            source_pointer = int(record["source"]) + offset
            mapped_pointer = gpu_textures + source - 0x24000000
            available = bytes_count - offset
            cpu = bytes(inferior.read_memory(source_pointer, available))
            mapped = bytes(inferior.read_memory(mapped_pointer, available))
            differing = sum(a != b for a, b in zip(cpu, mapped))
            print(
                "GPU_SUBTRACT_RECORD index=%d copy=%d active=%d bytes=%d "
                "offset=%d sparse=%d dynamic=%d revision=%d generation=%d diffs=%d"
                % (record_index, copy, int(record["active"]), bytes_count,
                   offset, int(record["sparse"]), int(record["dynamic"]),
                   int(record["source_revision"]), int(record["generation"]),
                   differing)
            )
            print("GPU_SUBTRACT_CPU " + rgba_stats(cpu))
            print("GPU_SUBTRACT_DDR " + rgba_stats(mapped))

if not found:
    print("GPU_SUBTRACT none in the currently open frame")
