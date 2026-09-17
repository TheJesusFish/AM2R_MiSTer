#!/bin/sh
set -eu

pid="${1:?runner pid is required}"
seconds="${2:-10}"
output="${3:-/tmp/am2r-upload-args.trace}"
offset="${4:-0x188014}"
trace=/sys/kernel/tracing
mounted=0

cleanup() {
    echo 0 > "$trace/tracing_on" 2>/dev/null || true
    echo 0 > "$trace/events/uprobes/enable" 2>/dev/null || true
    echo > "$trace/uprobe_events" 2>/dev/null || true
    if [ "$mounted" -eq 1 ]; then
        umount "$trace" || true
    fi
}
trap cleanup EXIT INT TERM

if ! grep -q "tracefs $trace" /proc/mounts; then
    mount -t tracefs tracefs "$trace"
    mounted=1
fi

echo 0 > "$trace/tracing_on"
echo > "$trace/trace"
echo > "$trace/uprobe_events"
echo "p:am2r_upload /proc/$pid/exe:$offset source=%r0:x32 bytes=%r1:u32 valid=%r2:u32 revision=%r3:u32" > "$trace/uprobe_events"
echo "r:am2r_upload_ret /proc/$pid/exe:$offset" >> "$trace/uprobe_events"
echo 1 > "$trace/events/uprobes/enable"
echo 1 > "$trace/tracing_on"
sleep "$seconds"
echo 0 > "$trace/tracing_on"
cat "$trace/trace" > "$output"
wc -l "$output"
