"""Wait until the DMTCP-restored AM2R main thread reaches its frame sleep."""

import glob
import sys
import time


deadline = time.monotonic() + 45.0
candidate = None
consecutive = 0
last_status = "restored process not present"

while time.monotonic() < deadline:
    observed = None
    for comm_path in glob.glob("/proc/[0-9]*/comm"):
        try:
            with open(comm_path, "rt", encoding="ascii") as comm_file:
                if comm_file.read().strip() != "DMTCP:buttersco":
                    continue
            pid = int(comm_path.split("/")[2])
            with open("/proc/%d/syscall" % pid, "rt", encoding="ascii") as syscall_file:
                fields = syscall_file.read().split()
            last_status = "process %d syscall=%s" % (
                pid, fields[0] if fields else "unavailable")
            # The MiSTer runner paces the main loop with ARM's time64
            # clock_nanosleep syscall (407). DMTCP's restore trampoline runs
            # at low executable addresses and does not remain in this state.
            if len(fields) >= 2 and fields[0] == "407" and int(fields[-1], 0) >= 0x10000000:
                observed = pid
                break
        except (FileNotFoundError, PermissionError, ProcessLookupError, OSError, ValueError) as error:
            last_status = str(error)

    if observed is not None and observed == candidate:
        consecutive += 1
    elif observed is not None:
        candidate = observed
        consecutive = 1
    else:
        candidate = None
        consecutive = 0

    if consecutive >= 4:
        print(candidate)
        raise SystemExit(0)
    time.sleep(0.10)

raise SystemExit("timed out: " + last_status)
