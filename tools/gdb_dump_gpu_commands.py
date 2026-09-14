"""Decode the MiSTer FPGA command list at MisterGpu_finishFrame."""

import gdb


count = int(gdb.parse_and_eval("g_gpu_command_count"))
commands = gdb.parse_and_eval("g_gpu_commands")
area_by_opcode = {}
print("GPU_COMMAND_LIST count=%d" % count)
for index in range(count):
    command = commands[index]
    words = [int(command["word"][word]) for word in range(8)]
    opcode = words[0] & 0xFF
    additive = (words[0] >> 8) & 1
    width = (words[0] >> 16) & 0xFFFF
    height = (words[0] >> 32) & 0xFFFF
    x = words[2] & 0xFFFF
    y = (words[2] >> 16) & 0xFFFF
    if x >= 0x8000:
        x -= 0x10000
    if y >= 0x8000:
        y -= 0x10000
    clipped_width = max(0, min(320, x + width) - max(0, x))
    clipped_height = max(0, min(240, y + height) - max(0, y))
    area = clipped_width * clipped_height
    area_by_opcode[opcode] = area_by_opcode.get(opcode, 0) + area
    if opcode in (2, 3):
        source = words[1] & 0xFFFFFFFF
        stride = (words[1] >> 32) & 0xFFFFFFFF
        u = words[4] & 0xFFFFFFFF
        v = (words[4] >> 32) & 0xFFFFFFFF
        du = words[5] & 0xFFFFFFFF
        dv = (words[5] >> 32) & 0xFFFFFFFF
        tint = words[6] & 0xFFFFFFFF
        print(
            "GPU_COMMAND index=%d op=%d add=%d size=%dx%d dst=%d,%d clip_area=%d "
            "source=%08x stride=%d uv=%08x,%08x step=%08x,%08x tint=%08x gradient=%016x"
            % (
                index, opcode, additive, width, height, x, y, area,
                source, stride, u, v, du, dv, tint, words[7],
            )
        )
    else:
        print(
            "GPU_COMMAND index=%d op=%d add=%d size=%dx%d dst=%d,%d clip_area=%d word1=%016x"
            % (index, opcode, additive, width, height, x, y, area, words[1])
        )

print(
    "GPU_COMMAND_AREAS %s"
    % " ".join("op%d=%d" % item for item in sorted(area_by_opcode.items()))
)
