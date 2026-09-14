#!/bin/sh
# Deterministic, non-persistent USB-1 closeout input sequence.

set -u

log=/tmp/am2r-final-sequence.log
: > "$log"

stamp() {
	date '+%s %H:%M:%S' >> "$log"
	printf '%s\n' "$1" >> "$log"
}

wrapper_pid() {
	pidof MiSTer_AM2R | awk '{print $1}'
}

write_mask() {
	# $1 contains a four-byte little-endian joystick mask.
	printf "$1" > /tmp/am2r-final-mask.bin
	dd if=/tmp/am2r-final-mask.bin of=/dev/shm/am2r-joy bs=1 seek=8 conv=notrunc 2>/dev/null
}

# The normal playback reaches representative opening gameplay at about frame
# 7600 and ends at frame 9001. Begin direct input only after playback ends.
sleep 165
# Pause only the frontend publisher so it does not overwrite the deterministic
# masks; the game and FPGA continue running.
front="$(wrapper_pid)"
stamp "input-begin wrapper=$front"
kill -STOP "$front"

write_mask '\000\000\000\000'
sleep 1

# Jump repeatedly to exercise the former above-Samus vertical-line corruption.
i=0
while [ "$i" -lt 6 ]; do
	write_mask '\040\000\000\000'
	sleep 0.20
	write_mask '\000\000\000\000'
	sleep 0.55
	i=$((i + 1))
done

# Straight, diagonal-up, diagonal-down, vertical-up, and vertical-down fire.
write_mask '\021\000\000\000'
sleep 2
write_mask '\000\000\000\000'
sleep 0.5
write_mask '\031\001\000\000'
sleep 2
write_mask '\000\000\000\000'
sleep 0.5
write_mask '\025\002\000\000'
sleep 2
write_mask '\000\000\000\000'
sleep 0.5
write_mask '\030\000\000\000'
sleep 2
write_mask '\000\000\000\000'
sleep 0.5
write_mask '\024\000\000\000'
sleep 2
write_mask '\000\000\000\000'
sleep 1

# Start opens and closes the in-game map/pause view in this route.
write_mask '\000\010\000\000'
sleep 0.20
write_mask '\000\000\000\000'
sleep 3
write_mask '\000\010\000\000'
sleep 0.20
write_mask '\000\000\000\000'
sleep 2

kill -CONT "$front"
stamp "input-end wrapper=$front"
rm -f /tmp/am2r-final-mask.bin
