#!/bin/sh
# Deterministically enter AM2R's ordinary slot-A save from the title screen.
# The frontend is paused only while this helper owns the shared input mask.

set -eu

selector_only=0
if test "${1:-}" = "--selector"; then
	selector_only=1
elif test "$#" -ne 0; then
	printf '%s\n' 'usage: am2r_load_save_direct.sh [--selector]' >&2
	exit 2
fi

front="$(pidof MiSTer_AM2R | awk '{print $1}')"
test -n "$front"
test -e /dev/shm/am2r-joy

mask=/tmp/am2r-load-save-mask.bin
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

kill -STOP "$front"
write_mask '\000\000\000\000'
sleep 1

# Start enters the save selector. Its normal fade/transition is deliberately
# long; --selector resumes from a selector that is already visible.
if test "$selector_only" -eq 0; then
	write_mask '\000\010\000\000'
	sleep 0.25
	write_mask '\000\000\000\000'
	sleep 20
fi

# Jump/A chooses slot A and then Continue.
write_mask '\040\000\000\000'
sleep 0.22
write_mask '\000\000\000\000'
sleep 1.4
write_mask '\040\000\000\000'
sleep 0.22
write_mask '\000\000\000\000'
sleep 5
