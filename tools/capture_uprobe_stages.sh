#!/bin/sh
set -eu

pid="${1:?runner pid is required}"
seconds="${2:-34}"
output="${3:-/tmp/am2r-stages.trace}"
step_offset="${4:-0x4f490}"
draw_offset="${5:-0x427c0}"
present_offset="${6:-0x18a2a0}"
wait_offset="${7:-0x18d764}"
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
echo 4096 > "$trace/buffer_size_kb"
echo > "$trace/trace"
echo > "$trace/uprobe_events"
echo "p:am2r_step /proc/$pid/exe:$step_offset" > "$trace/uprobe_events"
echo "r:am2r_step_ret /proc/$pid/exe:$step_offset" >> "$trace/uprobe_events"
echo "p:am2r_draw /proc/$pid/exe:$draw_offset" >> "$trace/uprobe_events"
echo "r:am2r_draw_ret /proc/$pid/exe:$draw_offset" >> "$trace/uprobe_events"
echo "p:am2r_present /proc/$pid/exe:$present_offset" >> "$trace/uprobe_events"
echo "r:am2r_present_ret /proc/$pid/exe:$present_offset" >> "$trace/uprobe_events"
echo "p:am2r_wait /proc/$pid/exe:$wait_offset" >> "$trace/uprobe_events"
echo "r:am2r_wait_ret /proc/$pid/exe:$wait_offset" >> "$trace/uprobe_events"
echo 1 > "$trace/events/uprobes/enable"
echo 1 > "$trace/tracing_on"
sleep "$seconds"
echo 0 > "$trace/tracing_on"
cat "$trace/trace" > "$output"
wc -l "$output"
