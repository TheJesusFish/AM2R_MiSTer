#!/bin/sh
# Reproduce the user's slot-1 action: face right, release, then fire once.
# The restored runner must be stopped on entry; it is stopped again afterward.

set -eu

runner="${1:?usage: am2r_door_single_shot.sh RUNNER_PID}"
frontend="$(pidof MiSTer_AM2R | awk '{print $1}')"
test -n "$frontend"
test -e /dev/shm/am2r-joy

mask=/tmp/am2r-door-mask.bin
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
sleep 0.50
# A short tap faces right without walking Samus off the save station.  A longer
# hold drops her to the lower floor, where a straight shot passes below this
# elevated door and is not a valid reproduction of the user's action.  Keep
# the tap long enough to span several frames after resuming a stopped runner.
write_mask '\001\000\000\000'
sleep 0.12
write_mask '\000\000\000\000'
sleep 0.20
write_mask '\020\000\000\000'
sleep 0.25
write_mask '\000\000\000\000'
sleep 0.25
kill -STOP "$runner"

printf 'AM2R_DOOR_SHOT runner=%s frontend=%s\n' "$runner" "$frontend"
