#!/bin/sh
set -eu

pid="${1:?runner pid is required}"
seconds="${2:-20}"
output="${3:-/tmp/am2r-events.trace}"
resolved_event_offset="${4:-0x41b18}"
trace=/sys/kernel/tracing
mounted=0
exe="$(awk '$6 ~ /\/butterscotch$/ { print $6; exit }' "/proc/$pid/maps")"
if [ -z "$exe" ]; then exe="/proc/$pid/exe"; fi

cleanup() {
    echo 0 > "$trace/tracing_on" 2>/dev/null || true
    echo 0 > "$trace/events/uprobes/enable" 2>/dev/null || true
    echo > "$trace/uprobe_events" 2>/dev/null || true
    if [ "$mounted" -eq 1 ]; then umount "$trace" || true; fi
}
trap cleanup EXIT INT TERM

if ! grep -q "tracefs $trace" /proc/mounts; then
    mount -t tracefs tracefs "$trace"
    mounted=1
fi

echo 0 > "$trace/tracing_on"
echo 32768 > "$trace/buffer_size_kb"
echo > "$trace/trace"
echo > "$trace/uprobe_events"

# Runner_executeResolvedEvent(runner, instance, eventType, eventSubtype,
# codeId, ownerObjectIndex). ARM EABI passes the first four fields in r0-r3
# and the final two on the entry stack. Instance.objectIndex is at +4.
echo "p:resolved_event $exe:$resolved_event_offset object=+4(%r1):s32 event=%r2:s32 subtype=%r3:s32 code=+0(%sp):s32 owner=+4(%sp):s32" > "$trace/uprobe_events"
echo "r:resolved_event_ret $exe:$resolved_event_offset" >> "$trace/uprobe_events"
echo 1 > "$trace/events/uprobes/enable"
echo 1 > "$trace/tracing_on"
sleep "$seconds"
echo 0 > "$trace/tracing_on"
cat "$trace/trace" > "$output"
wc -l "$output"
