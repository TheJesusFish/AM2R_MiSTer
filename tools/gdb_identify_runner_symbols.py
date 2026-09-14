"""Compare retained AM2R runner symbol layouts against a restored process."""

import gdb


CANDIDATES = (
    "/media/fat/games/am2r/bin/butterscotch",
    "/media/fat/games/am2r/bin/butterscotch.pre-hud-v14-20260908",
    "/media/fat/games/am2r/bin/butterscotch.pre-multisource-fusion-20260908",
    "/media/fat/games/am2r/bin/butterscotch.pre-surface-revision-v2-20260908",
    "/media/fat/games/am2r/bin/butterscotch.pre-surface-revision-20260908",
    "/media/fat/games/am2r/bin/butterscotch.pre-linux618-20260908",
)


for path in CANDIDATES:
    try:
        gdb.execute("symbol-file %s" % path, to_string=True)
        address = int(gdb.parse_and_eval("&g_runner"))
        pointer = int(gdb.parse_and_eval("g_runner"))
        fields = ""
        if pointer >= 0x10000:
            try:
                runner = gdb.parse_and_eval("g_runner").dereference()
                fields = " dataWin=0x%x spatialGrid=0x%x room=%d frame=%d" % (
                    int(runner["dataWin"]), int(runner["spatialGrid"]),
                    int(runner["currentRoomIndex"]), int(runner["frameCount"]),
                )
            except (gdb.error, gdb.MemoryError) as error:
                fields = " fields_error=%s" % str(error).replace("\n", " ")
        print("RUNNER_SYMBOL candidate=%s address=0x%x pointer=0x%x%s" % (
            path, address, pointer, fields
        ))
    except (gdb.error, gdb.MemoryError) as error:
        print("RUNNER_SYMBOL candidate=%s error=%s" % (
            path, str(error).replace("\n", " ")
        ))
