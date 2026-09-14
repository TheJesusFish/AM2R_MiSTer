#!/bin/sh
# Exact-artifact save/load regression helper. Refuse to run unless the normal
# state directory is bind-mounted from the isolated closeout scratch path.

set -eu

scratch=/media/fat/savestates/AM2R-regression-20260908-v10
target=/media/fat/savestates/AM2R
log=/tmp/am2r-final-savestate-sequence.log
: > "$log"

scratch_root=/savestates/AM2R-regression-20260908-v10
if ! grep -F " $scratch_root $target " /proc/self/mountinfo >/dev/null; then
	printf '%s\n' 'refused: isolated savestate bind mount is not active' >> "$log"
	exit 1
fi

sleep 15
pid="$(pidof butterscotch | awk '{print $1}')"
printf '%s save slot1 pid=%s signal=34\n' "$(date '+%s')" "$pid" >> "$log"
kill -34 "$pid"

sleep 22
pid="$(pidof butterscotch | awk '{print $1}')"
printf '%s load slot1 pid=%s signal=38\n' "$(date '+%s')" "$pid" >> "$log"
kill -38 "$pid"

sleep 12
pid="$(pidof butterscotch | awk '{print $1}')"
printf '%s resumed pid=%s\n' "$(date '+%s')" "$pid" >> "$log"
