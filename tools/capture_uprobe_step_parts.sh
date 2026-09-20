#!/bin/sh
set -eu

pid="${1:?runner pid is required}"
seconds="${2:-24}"
output="${3:-/tmp/am2r-step-parts.trace}"
step_offset="${4:-0x4f790}"
draw_offset="${5:-0x42870}"
present_offset="${6:-0x18b5e0}"
wait_offset="${7:-0x18a338}"
event_all_offset="${8:-0x42260}"
collision_dispatch_offset="${9:-0x532d8}"
stale_refs_offset="${10:-0x4b9b0}"
cleanup_destroyed_offset="${11:-0x4b78c}"
particles_update_offset="${12:-0x7ce44}"
update_views_offset="${13:-0x54120}"
draw_views_offset="${14:-}"
draw_pre_offset="${15:-}"
draw_post_offset="${16:-}"
draw_gui_offset="${17:-}"
end_frame_init_offset="${18:-}"
end_frame_end_offset="${19:-}"
trace=/sys/kernel/tracing
mounted=0
exe="$(awk '$6 ~ /\/butterscotch$/ { print $6; exit }' "/proc/$pid/maps")"
if [ -z "$exe" ]; then exe="/proc/$pid/exe"; fi

cleanup_trace() {
    echo 0 > "$trace/tracing_on" 2>/dev/null || true
    echo 0 > "$trace/events/uprobes/enable" 2>/dev/null || true
    echo > "$trace/uprobe_events" 2>/dev/null || true
    if [ "$mounted" -eq 1 ]; then umount "$trace" || true; fi
}
trap cleanup_trace EXIT INT TERM

if ! grep -q "tracefs $trace" /proc/mounts; then
    mount -t tracefs tracefs "$trace"
    mounted=1
fi

echo 0 > "$trace/tracing_on"
echo 16384 > "$trace/buffer_size_kb"
echo > "$trace/trace"
echo > "$trace/uprobe_events"

add_pair() {
    name="$1"
    offset="$2"
    echo "p:$name $exe:$offset" >> "$trace/uprobe_events"
    echo "r:${name}_ret $exe:$offset" >> "$trace/uprobe_events"
}

# ELF file offsets for the exact no-inline sand-phase diagnostic image. The
# production implementation does not need to retain the no-inline markers.
# Every offset is overridable so a rebuilt image can be profiled without
# editing this helper again.
add_pair step               "$step_offset"
add_pair draw               "$draw_offset"
add_pair present            "$present_offset"
add_pair wait               "$wait_offset"
add_pair event_all          "$event_all_offset"
add_pair collision_dispatch "$collision_dispatch_offset"
add_pair stale_refs         "$stale_refs_offset"
add_pair cleanup_destroyed  "$cleanup_destroyed_offset"
add_pair particles_update   "$particles_update_offset"
add_pair update_views       "$update_views_offset"

# Optional whole-render subphases. Keeping these optional preserves the helper
# for older symbol images while allowing a new build to account for time that
# falls outside Runner_draw itself (view setup, post draw, and GUI composition).
if [ -n "$draw_views_offset" ]; then add_pair draw_views "$draw_views_offset"; fi
if [ -n "$draw_pre_offset" ]; then add_pair draw_pre "$draw_pre_offset"; fi
if [ -n "$draw_post_offset" ]; then add_pair draw_post "$draw_post_offset"; fi
if [ -n "$draw_gui_offset" ]; then add_pair draw_gui "$draw_gui_offset"; fi
if [ -n "$end_frame_init_offset" ]; then add_pair end_frame_init "$end_frame_init_offset"; fi
if [ -n "$end_frame_end_offset" ]; then add_pair end_frame_end "$end_frame_end_offset"; fi

echo 1 > "$trace/events/uprobes/enable"
echo 1 > "$trace/tracing_on"
sleep "$seconds"
echo 0 > "$trace/tracing_on"
cat "$trace/trace" > "$output"
wc -l "$output"
