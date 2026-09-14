#!/bin/sh
# Reload the isolated slot-1 DMTCP state and stop the newly restored runner as
# soon as it appears, so a hostile scene cannot advance during diagnostics.

set -eu

old_pid="${1:?usage: reload_and_freeze_am2r.sh OLD_PID}"
kill -38 "$old_pid"
# A prior diagnostic may have deliberately left the runner stopped.  SIGCONT
# lets it process the queued real-time load request; a running process is
# unaffected.
kill -CONT "$old_pid"

iteration=0
while [ "$iteration" -lt 200 ]; do
    for process in /proc/[0-9]*; do
        test -r "$process/comm" || continue
        command_name="$(cat "$process/comm" 2>/dev/null || true)"
        case "$command_name" in
            butterscotch|DMTCP:buttersco)
                new_pid="${process#/proc/}"
                if [ "$new_pid" != "$old_pid" ]; then
                    kill -STOP "$new_pid"
                    printf 'AM2R_RELOADED old_pid=%s new_pid=%s iteration=%s\n' \
                        "$old_pid" "$new_pid" "$iteration"
                    exit 0
                fi
                ;;
        esac
    done
    sleep 0.1
    iteration=$((iteration + 1))
done

printf 'AM2R_RELOAD_TIMEOUT old_pid=%s\n' "$old_pid" >&2
exit 1
