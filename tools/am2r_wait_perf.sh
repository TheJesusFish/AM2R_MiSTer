#!/bin/sh
# Start the deterministic controller stress sequence at the exact end of an
# input-recording bootstrap, independent of archive and asset load time.
while ! grep -q 'Playback ended' /tmp/am2r-session.log 2>/dev/null; do
	sleep 1
done
exec /tmp/am2r_uinput_perf "$@"
