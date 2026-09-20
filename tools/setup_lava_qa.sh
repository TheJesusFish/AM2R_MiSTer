#!/bin/sh
# Prepare an isolated copy of the supplied slot-1 checkpoint on USB-1.

set -eu

target=/media/fat/savestates/AM2R
scratch=/media/fat/savestates/AM2R-qa-lava-20260917b
runner=/media/fat/games/am2r/bin/butterscotch
old_runner=/media/fat/games/am2r/bin/butterscotch.pre-perf-states-20260917

if mount | grep -F " on $target " >/dev/null; then
	printf '%s\n' 'refusing: AM2R save-state directory is already bind mounted' >&2
	exit 1
fi
if [ -e "$scratch" ]; then
	printf '%s\n' "refusing: scratch path already exists: $scratch" >&2
	exit 1
fi
test -f "$target/slot1.dmtcp"
test -f "$target/slot1.txt"
test -f "$old_runner"

mkdir "$scratch"
cp "$target/slot1.dmtcp" "$scratch/slot1.dmtcp"
cp "$target/slot1.txt" "$scratch/slot1.txt"
sync
mount --bind "$scratch" "$target"
cp "$old_runner" "$runner"
chmod +x "$runner" /tmp/am2r-uinput-test

sha256sum "$runner" "$target/slot1.dmtcp"
mount | grep -F " on $target "
printf 'load_core /media/fat/_Dev/AM2R.rbf\n' > /dev/MiSTer_cmd
