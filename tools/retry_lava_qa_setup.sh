#!/bin/sh
# Remove only the interrupted QA scratch copy, then restart setup detached.

set -eu

scratch=/media/fat/savestates/AM2R-qa-lava-20260917b
resolved="$(readlink -f "$scratch")"
test "$resolved" = "$scratch"
if mount | grep -F ' on /media/fat/savestates/AM2R ' >/dev/null; then
	printf '%s\n' 'refusing: AM2R save-state directory is bind mounted' >&2
	exit 1
fi
rm -f "$scratch/slot1.dmtcp" "$scratch/slot1.txt"
rmdir "$scratch"
nohup /tmp/setup_lava_qa.sh >/tmp/setup_lava_qa.log 2>&1 </dev/null &
printf '%s\n' "$!"
