#!/bin/sh
set -eu

pid="${1:?runner pid is required}"
seconds="${2:-34}"
output="${3:-/tmp/am2r-stages.trace}"
# The debugger reports virtual addresses 0x20000 above the corresponding ELF
# file offsets used by tracefs uprobes for this ARM image.
step_offset="${4:-0x4f4b0}"
draw_offset="${5:-0x427e0}"
present_offset="${6:-0x18b330}"
wait_offset="${7:-0x18a088}"
probe_mode="${8:-paired}"
trace=/sys/kernel/tracing
mounted=0

# A DMTCP-restored process reports mtcp_restart through /proc/PID/exe even
# though its executable mappings still come from the real Butterscotch image.
# Bind uprobes to that mapped file so profiling works for supplied save states
# as well as for a freshly launched runner.
exe="$(awk '$6 ~ /\/butterscotch$/ { print $6; exit }' "/proc/$pid/maps")"
if [ -z "$exe" ]; then
    exe="/proc/$pid/exe"
fi

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
if [ "$probe_mode" = "wait-only" ]; then
    echo "p:am2r_wait $exe:$wait_offset" > "$trace/uprobe_events"
else
    echo "p:am2r_step $exe:$step_offset" > "$trace/uprobe_events"
    echo "p:am2r_draw $exe:$draw_offset" >> "$trace/uprobe_events"
    echo "p:am2r_present $exe:$present_offset" >> "$trace/uprobe_events"
    echo "p:am2r_wait $exe:$wait_offset" >> "$trace/uprobe_events"
fi
if [ "$probe_mode" = "paired" ]; then
    echo "r:am2r_step_ret $exe:$step_offset" >> "$trace/uprobe_events"
    echo "r:am2r_draw_ret $exe:$draw_offset" >> "$trace/uprobe_events"
    echo "r:am2r_present_ret $exe:$present_offset" >> "$trace/uprobe_events"
    echo "r:am2r_wait_ret $exe:$wait_offset" >> "$trace/uprobe_events"
fi
echo 1 > "$trace/events/uprobes/enable"
echo 1 > "$trace/tracing_on"
sleep "$seconds"
echo 0 > "$trace/tracing_on"
cat "$trace/trace" > "$output"
wc -l "$output"
