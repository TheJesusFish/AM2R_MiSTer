#!/bin/sh
# Drive the user-supplied room-159 checkpoint without hot-plugging a pad.
# The caller must restore the checkpoint with the shared input mask cleared.

set -eu

front="$(pidof MiSTer_AM2R | awk '{print $1}')"
test -n "$front"
test -e /dev/shm/am2r-joy

mask=/tmp/am2r-metroid-room-mask.bin

write_mask() {
	printf "$1" > "$mask"
	dd if="$mask" of=/dev/shm/am2r-joy bs=1 seek=8 conv=notrunc 2>/dev/null
}

cleanup() {
	write_mask '\000\000\000\000' || true
	kill -CONT "$front" 2>/dev/null || true
	rm -f "$mask"
}
trap cleanup EXIT HUP INT TERM

# Stop only the frontend input publisher. The game, audio, and FPGA continue.
kill -STOP "$front"
write_mask '\000\000\000\000'
sleep 8

# Hold right and jump repeatedly to clear the room geometry and trigger the
# Gamma encounter, then keep walking briefly after the final landing.
i=0
while [ "$i" -lt 12 ]; do
	write_mask '\041\000\000\000'
	sleep 0.22
	write_mask '\001\000\000\000'
	sleep 0.53
	i=$((i + 1))
done
write_mask '\001\000\000\000'
sleep 2
write_mask '\000\000\000\000'
sleep 4
