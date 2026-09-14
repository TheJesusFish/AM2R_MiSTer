#!/bin/sh
set -eu

runner_pid="${1:?runner pid required}"
log=/tmp/am2r-first-beam.log
rm -f "$log"
kill -CONT "$runner_pid"
sleep 0.1
gdb -q -nx -batch /media/fat/games/am2r/bin/butterscotch \
    -p "$runner_pid" -x /tmp/gdb_trace_first_beam_collision.gdb \
    >"$log" 2>&1 &
gdb_pid=$!
sleep 1
/tmp/am2r_door_single_shot.sh "$runner_pid"
wait "$gdb_pid"
cat "$log"
kill -STOP "$runner_pid" 2>/dev/null || true
