#!/bin/sh
# Pulse AM2R's game-facing Jump bit while temporarily owning the shared mask.

set -eu

count="${1:-1}"
case "$count" in
	*[!0-9]*|'') exit 2 ;;
esac

front="$(pidof MiSTer_AM2R | awk '{print $1}')"
test -n "$front"
test -e /dev/shm/am2r-joy
mask=/tmp/am2r-pulse-jump-mask.bin

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
while [ "$i" -lt "$count" ]; do
	write_mask '\040\000\000\000'
	sleep 0.25
	write_mask '\000\000\000\000'
	sleep 1.5
	i=$((i + 1))
done
sleep 4
