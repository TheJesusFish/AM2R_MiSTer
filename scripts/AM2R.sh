#!/bin/bash
# Menu launcher for the AM2R hybrid MiSTer core.

set -u

AM2R_DIR="/media/fat/games/AM2R-dev-7c2503ef"
AM2R_CORE="$AM2R_DIR/AM2R.rbf"
AM2R_RUNNER="$AM2R_DIR/butterscotch"
AM2R_DATA="$AM2R_DIR/game/data.win"
AM2R_SAVE="$AM2R_DIR/saves/release"
AM2R_LOG="$AM2R_DIR/am2r.log"
AM2R_SESSION_LOG="/tmp/am2r-session.log"
MISTER_COMMAND="/dev/MiSTer_cmd"
MISTER_MENU="/media/fat/menu.rbf"

restore_menu() {
    sync
    if [ -w "$MISTER_COMMAND" ] && [ -f "$MISTER_MENU" ]; then
        printf 'load_core %s\n' "$MISTER_MENU" > "$MISTER_COMMAND"
    fi
}
trap restore_menu EXIT HUP INT TERM

for required in "$AM2R_CORE" "$AM2R_RUNNER" "$AM2R_DATA"; do
    if [ ! -f "$required" ]; then
        echo "AM2R: missing $required"
        exit 1
    fi
done

mkdir -p "$AM2R_SAVE"
chmod +x "$AM2R_RUNNER"
printf 'load_core %s\n' "$AM2R_CORE" > "$MISTER_COMMAND"
sleep 1

cd "$AM2R_DIR" || exit 1
: > "$AM2R_SESSION_LOG"
"$AM2R_RUNNER" "$AM2R_DATA" \
    --renderer software \
    --disable-log-colours \
    --save-folder "$AM2R_SAVE" \
    "$@" >> "$AM2R_SESSION_LOG" 2>&1
AM2R_STATUS=$?
cat "$AM2R_SESSION_LOG" >> "$AM2R_LOG"
echo "AM2R runner exited with status $AM2R_STATUS" >> "$AM2R_LOG"
exit "$AM2R_STATUS"
