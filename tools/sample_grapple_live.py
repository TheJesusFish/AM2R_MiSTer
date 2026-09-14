"""Sample live AM2R character fields without modifying the runner.

The optional stop window sends SIGSTOP only after a falling frame enters the
six-pixel ledge-detection band.  This lets a separate, read-only GDB snapshot
inspect the exact frame without software breakpoints in the ARM executable.
"""

import argparse
import csv
import json
import os
import signal
import struct
import time


def arguments():
    parser = argparse.ArgumentParser()
    parser.add_argument("--addresses", default="/tmp/am2r-grapple-addresses.json")
    parser.add_argument("--output", default="/tmp/am2r-grapple-live.csv")
    parser.add_argument("--duration", type=float, default=12.0)
    parser.add_argument("--stop-min-y", type=float)
    parser.add_argument("--stop-max-y", type=float)
    return parser.parse_args()


def read_field(memory, description):
    fmt = "<" + description["format"]
    raw = os.pread(memory, struct.calcsize(fmt), description["address"])
    if len(raw) != struct.calcsize(fmt):
        raise RuntimeError("short process-memory read")
    return struct.unpack(fmt, raw)[0]


options = arguments()
with open(options.addresses, "r", encoding="ascii") as source:
    addresses = json.load(source)

pid = int(addresses["pid"])
field_names = list(addresses["fields"])
memory = os.open("/proc/%d/mem" % pid, os.O_RDONLY)
deadline = time.monotonic() + options.duration
previous_frame = None
stopped = False

try:
    with open(options.output, "w", newline="", encoding="ascii") as target:
        writer = csv.writer(target)
        writer.writerow(["monotonic_ns"] + field_names)
        while time.monotonic() < deadline:
            values = {
                name: read_field(memory, description)
                for name, description in addresses["fields"].items()
            }
            frame = int(values["frame"])
            if frame != previous_frame:
                writer.writerow([time.monotonic_ns()] + [values[name] for name in field_names])
                target.flush()
                previous_frame = frame
                if (options.stop_min_y is not None and
                        options.stop_max_y is not None and
                        values["yVel"] > 0.0 and
                        options.stop_min_y <= values["y"] < options.stop_max_y and
                        int(values["state"]) in (15, 16, 24)):
                    os.kill(pid, signal.SIGSTOP)
                    stopped = True
                    break
            time.sleep(0.0005)
finally:
    os.close(memory)

print("GRAPPLE_SAMPLE_DONE pid=%d stopped=%d frames=%s" % (
    pid, 1 if stopped else 0, 0 if previous_frame is None else previous_frame
))
