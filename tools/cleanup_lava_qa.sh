#!/bin/sh
# Remove only the isolated USB-1 slot-1 QA copy created by setup_lava_qa.sh.

set -eu

target=/media/fat/savestates/AM2R
scratch=/media/fat/savestates/AM2R-qa-lava-20260917b

printf 'load_core /media/fat/menu.rbf\n' > /dev/MiSTer_cmd
sleep 3
if ps -eo comm | grep -Eq '^(MiSTer_AM2R|butterscotch|DMTCP:buttersco)$'; then
	printf '%s\n' 'refusing: an AM2R process is still running' >&2
	exit 1
fi
if mount | grep -F " on $target " >/dev/null; then
	umount "$target"
fi
resolved="$(readlink -f "$scratch")"
test "$resolved" = "$scratch"
rm -f "$scratch/slot1.dmtcp" "$scratch/slot1.txt"
rmdir "$scratch"

test "$(stat -c %s "$target/slot1.dmtcp")" = 196343140
printf '%s\n' 'isolated older slot-1 scratch removed; persistent slot visible'
