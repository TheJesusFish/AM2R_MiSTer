"""Fire finite, straight-up missiles for the staged first-Alpha hardware test."""

import mmap
import struct
import time


UP = 1 << 3
FIRE = 1 << 4


with open("/dev/shm/am2r-joy", "r+b", buffering=0) as file:
    with mmap.mmap(file.fileno(), 16) as shared:
        def write(mask):
            struct.pack_into("<I", shared, 8, mask)
            shared.flush()

        write(UP)
        time.sleep(0.30)
        for shot in range(10):
            print("staged missile %d/10" % (shot + 1), flush=True)
            write(UP | FIRE)
            time.sleep(0.12)
            write(UP)
            time.sleep(0.48)
        write(0)
        time.sleep(3.0)

print("staged finite missile volley complete")
