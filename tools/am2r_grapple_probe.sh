#!/bin/sh
# Deterministic ledge-grab input probe for the user-supplied post-Metroid state.
# The frontend is paused only while this script owns the shared joystick mask.

set -eu

direction="${1:-right}"
case "$direction" in
	right)
		held='\001\000\000\000'
		jump='\041\000\000\000'
		;;
	left)
		held='\002\000\000\000'
		jump='\042\000\000\000'
		;;
	*)
		printf '%s\n' 'usage: am2r_grapple_probe.sh {right|left}' >&2
		exit 2
		;;
esac

front="$(pidof MiSTer_AM2R | awk '{print $1}')"
test -n "$front"
test -e /dev/shm/am2r-joy

mask=/tmp/am2r-grapple-mask.bin
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

i=0
while [ "$i" -lt 12 ]; do
	write_mask "$jump"
	sleep 0.24
	write_mask "$held"
	sleep 0.51
	i=$((i + 1))
done

write_mask "$held"
sleep 2
