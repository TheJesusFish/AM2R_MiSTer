#!/bin/sh
# Face right on the slot-one save station, open the elevated beam door, and
# continue right through it.  The runner must be stopped on entry and is left
# stopped for state inspection.

set -eu

runner="${1:?usage: am2r_open_and_enter_door.sh RUNNER_PID}"
frontend="$(pidof MiSTer_AM2R | awk '{print $1}')"
mask=/tmp/am2r-open-door-mask.bin

write_mask() {
    printf "$1" > "$mask"
    dd if="$mask" of=/dev/shm/am2r-joy bs=1 seek=8 conv=notrunc 2>/dev/null
}

cleanup() {
    write_mask '\000\000\000\000' || true
    kill -CONT "$frontend" 2>/dev/null || true
    rm -f "$mask"
}
trap cleanup EXIT HUP INT TERM

kill -STOP "$frontend"
write_mask '\000\000\000\000'
kill -CONT "$runner"
sleep 0.10
write_mask '\001\000\000\000'
sleep 0.04
write_mask '\000\000\000\000'
sleep 0.06
write_mask '\020\000\000\000'
sleep 0.18
write_mask '\000\000\000\000'
sleep 0.90
write_mask '\001\000\000\000'
sleep 4.00
write_mask '\000\000\000\000'
sleep 2.00
kill -STOP "$runner"

printf 'AM2R_OPEN_ENTER_DOOR runner=%s frontend=%s\n' "$runner" "$frontend"
