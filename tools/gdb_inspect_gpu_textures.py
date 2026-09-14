"""Report MiSTer GPU texture records while the AM2R runner is paused."""

import gdb


count = int(gdb.parse_and_eval("g_gpu_texture_record_count"))
records = gdb.parse_and_eval("g_gpu_texture_records")
for index in range(count):
    record = records[index]
    print(
        "GPU_TEXTURE index=%d bytes=%d physical=%08x/%08x generation=%d "
        "dynamic=%d shadow_valid=%d"
        % (
            index,
            int(record["bytes"]),
            int(record["physical"][0]),
            int(record["physical"][1]),
            int(record["generation"]),
            1 if bool(record["dynamic"]) else 0,
            1 if bool(record["shadow_valid"]) else 0,
        )
    )
