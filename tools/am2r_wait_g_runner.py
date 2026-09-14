"""Wait until a DMTCP-restored AM2R runner has reconstructed g_runner."""

import glob
import struct
import sys
import time


if len(sys.argv) != 2:
    raise SystemExit("usage: am2r_wait_g_runner.py G_RUNNER_ADDRESS")

address = int(sys.argv[1], 0)
deadline = time.monotonic() + 45.0
last_error = "restored process not present"

while time.monotonic() < deadline:
    for comm_path in glob.glob("/proc/[0-9]*/comm"):
        try:
            with open(comm_path, "rt", encoding="ascii") as comm_file:
                if comm_file.read().strip() != "DMTCP:buttersco":
                    continue
            pid = int(comm_path.split("/")[2])
            with open("/proc/%d/mem" % pid, "rb", buffering=0) as memory:
                memory.seek(address)
                raw = memory.read(4)
            if len(raw) != 4:
                last_error = "short read from process %d" % pid
                continue
            pointer = struct.unpack("<I", raw)[0]
            if 0x10000 <= pointer < 0xF0000000:
                print(pid)
                raise SystemExit(0)
            last_error = "process %d g_runner=%#x" % (pid, pointer)
        except (FileNotFoundError, PermissionError, ProcessLookupError, OSError) as error:
            last_error = str(error)
    time.sleep(0.25)

raise SystemExit("timed out: " + last_error)
