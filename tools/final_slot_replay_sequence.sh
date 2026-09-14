#!/bin/sh
# Replay an attached AM2R regression checkpoint from the isolated bind-mounted
# scratch directory, then apply deterministic gameplay stress inputs.

set -eu

slot="${1:-}"
case "$slot" in
	2|3|4) ;;
	*) printf '%s\n' 'usage: final_slot_replay_sequence.sh {2|3|4}' >&2; exit 2 ;;
esac

target=/media/fat/savestates/AM2R
scratch_root=/savestates/AM2R-regression-20260908-v10
log="/tmp/am2r-final-slot${slot}-sequence.log"
: > "$log"

if ! grep -F " $scratch_root $target " /proc/self/mountinfo >/dev/null; then
	printf '%s\n' 'refused: isolated savestate bind mount is not active' >> "$log"
	exit 1
fi

write_mask() {
	printf "$1" > "/tmp/am2r-final-slot${slot}-mask.bin"
	dd if="/tmp/am2r-final-slot${slot}-mask.bin" of=/dev/shm/am2r-joy \
		bs=1 seek=8 conv=notrunc 2>/dev/null
}

sleep 12
pid="$(pidof butterscotch | awk '{print $1}')"
signal=$((38 + slot - 1))
printf '%s load slot=%s pid=%s signal=%s\n' "$(date '+%s')" "$slot" "$pid" "$signal" >> "$log"
kill -"$signal" "$pid"

# Compressed v8 checkpoints normally restore in under five seconds. Leave a
# wide margin for cold storage before applying gameplay inputs.
sleep 15
front="$(pidof MiSTer_AM2R | awk '{print $1}')"
printf '%s input-begin wrapper=%s\n' "$(date '+%s')" "$front" >> "$log"
kill -STOP "$front"

write_mask '\000\000\000\000'
sleep 1

# Repeated jumps exercise the former above-Samus/pit vertical-line defect.
i=0
while [ "$i" -lt 8 ]; do
	write_mask '\040\000\000\000'
	sleep 0.20
	write_mask '\000\000\000\000'
	sleep 0.55
	i=$((i + 1))
done

# Exercise straight and both aimed-fire directions.
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
sleep 1

# Open/close the map/pause view if the checkpoint permits it.
write_mask '\000\010\000\000'
sleep 0.20
write_mask '\000\000\000\000'
sleep 2
write_mask '\000\010\000\000'
sleep 0.20
write_mask '\000\000\000\000'
sleep 1

kill -CONT "$front"
printf '%s input-end wrapper=%s\n' "$(date '+%s')" "$front" >> "$log"
rm -f "/tmp/am2r-final-slot${slot}-mask.bin"
