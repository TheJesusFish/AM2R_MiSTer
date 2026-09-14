#!/bin/sh
set -eu

runtime_dir=/media/fat/games/am2r/bin
runner="$runtime_dir/butterscotch"
candidate="$runtime_dir/butterscotch.doorfix-test"
backup="$runtime_dir/butterscotch.v15-26682fe0"
expected_current=26682fe0a968eeb770b7895080a6c7640640cb4e4eb79d4454b3fdf0a7853215
expected_candidate=daea71c22addb15ab3000210096491c75a844774ea3111f3d47f69a2cbdae520

hash_of() {
    sha256sum "$1" | awk '{print $1}'
}

test "$(hash_of "$runner")" = "$expected_current"
test "$(hash_of "$candidate")" = "$expected_candidate"

# A diagnostic may have left the current runner stopped.  Let it process the
# core-exit signal before returning MiSTer to MENU.
old_runner="$(pidof butterscotch 2>/dev/null || true)"
test -z "$old_runner" || kill -CONT $old_runner
printf 'load_core %s\n' /media/fat/menu.rbf > /dev/MiSTer_cmd

iteration=0
while pidof MiSTer_AM2R >/dev/null 2>&1 || pidof butterscotch >/dev/null 2>&1; do
    iteration=$((iteration + 1))
    test "$iteration" -lt 100 || {
        printf 'AM2R_DOORFIX_ACTIVATE timeout waiting for prior runtime\n' >&2
        exit 1
    }
    sleep 0.1
done

if test -e "$backup"; then
    test "$(hash_of "$backup")" = "$expected_current"
else
    cp "$runner" "$backup"
fi

cp "$candidate" "$runtime_dir/butterscotch.next"
chmod 755 "$runtime_dir/butterscotch.next"
mv "$runtime_dir/butterscotch.next" "$runner"
sync
test "$(hash_of "$runner")" = "$expected_candidate"

printf 'load_core %s\n' /media/fat/_Other/AM2R.rbf > /dev/MiSTer_cmd
iteration=0
while ! pidof MiSTer_AM2R >/dev/null 2>&1 || ! pidof butterscotch >/dev/null 2>&1; do
    iteration=$((iteration + 1))
    test "$iteration" -lt 200 || {
        printf 'AM2R_DOORFIX_ACTIVATE timeout waiting for candidate runtime\n' >&2
        exit 1
    }
    sleep 0.1
done

printf 'AM2R_DOORFIX_ACTIVATE runner=%s frontend=%s sha256=%s\n' \
    "$(pidof butterscotch | awk '{print $1}')" \
    "$(pidof MiSTer_AM2R | awk '{print $1}')" \
    "$(hash_of "$runner")"
