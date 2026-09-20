#!/bin/sh
# Activate the exact M:-attached slot-1 checkpoint through an isolated bind.

set -eu

target=/media/fat/savestates/AM2R
scratch=/media/fat/savestates/AM2R-qa-attached-slot1-20260917
runner=/media/fat/games/am2r/bin/butterscotch
matching_runner=/media/fat/games/am2r/bin/butterscotch.pre-perf-states-20260917

if mount | grep -F " on $target " >/dev/null; then
	printf '%s\n' 'refusing: AM2R save-state directory is already bind mounted' >&2
	exit 1
fi
test "$(readlink -f "$scratch")" = "$scratch"
test "$(stat -c %s "$scratch/slot1.dmtcp")" = 196207972
grep -Fx 'runtime_crc32=052bf5ca' "$scratch/slot1.txt" >/dev/null
grep -Fx 'bytes=196207972' "$scratch/slot1.txt" >/dev/null
test -f "$matching_runner"

mount --bind "$scratch" "$target"
cp "$matching_runner" "$runner"
chmod +x "$runner" /tmp/am2r-uinput-test
sha256sum "$runner" "$target/slot1.dmtcp"
printf 'load_core /media/fat/_Dev/AM2R.rbf\n' > /dev/MiSTer_cmd
