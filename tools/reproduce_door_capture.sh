#!/bin/sh
# Reload slot 1, render the restored scene, perform one right-facing beam shot,
# and leave the restored runner stopped for post-action inspection.

set -eu

find_runner() {
    for process in /proc/[0-9]*; do
        test -r "$process/comm" || continue
        command_name="$(cat "$process/comm" 2>/dev/null || true)"
        case "$command_name" in
            butterscotch|DMTCP:buttersco)
                printf '%s\n' "${process#/proc/}"
                return 0
                ;;
        esac
    done
    return 1
}

old_runner="$(find_runner)"
/tmp/reload_and_freeze_am2r.sh "$old_runner"
new_runner="$(find_runner)"
test "$new_runner" != "$old_runner"

# Allow restore hooks and several frames to publish the checkpointed scene.
kill -CONT "$new_runner"
sleep 0.35
kill -STOP "$new_runner"

/tmp/am2r_door_single_shot.sh "$new_runner"

# Hold the post-shot result on screen long enough for capture, then preserve it
# for a read-only object-state inspection.
kill -CONT "$new_runner"
sleep 4
kill -STOP "$new_runner"

printf 'AM2R_DOOR_REPRO old_pid=%s new_pid=%s\n' "$old_runner" "$new_runner"
