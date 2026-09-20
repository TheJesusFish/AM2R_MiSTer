#!/bin/sh
set -eu

pid="${1:?runner pid is required}"
seconds="${2:-24}"
output="${3:-/tmp/am2r-sand-ops.trace}"
trace=/sys/kernel/tracing
mounted=0

# DMTCP-restored processes identify /proc/PID/exe as mtcp_restart. The mapped
# Butterscotch image remains the actual executable backing for these probes.
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
echo 8192 > "$trace/buffer_size_kb"
echo > "$trace/trace"
echo > "$trace/uprobe_events"

add_pair() {
    name="$1"
    offset="$2"
    echo "p:$name $exe:$offset" >> "$trace/uprobe_events"
    echo "r:${name}_ret $exe:$offset" >> "$trace/uprobe_events"
}

# Addresses are ELF file offsets for the exact v30/db14af00 runner symbol
# image. They cover the collision-grid and instance lifecycle work most likely
# to be amplified when a beam destroys many adjacent sand blocks.
add_pair grid_sync       0x5ab88
add_pair grid_query      0x5b868
add_pair cleanup         0x4b7f0
add_pair destroy         0x4b5d0
add_pair bi_destroy      0xb1e58
add_pair bi_create       0xb21c0
add_pair bi_create_depth 0xb24e8
add_pair bi_create_layer 0xb293c
add_pair collision_line  0xd1cd8
add_pair collision_rect  0xd0728
add_pair collision_point 0xd3110
add_pair instance_place  0xd5ed0
add_pair instance_pos    0xd67d0

echo 1 > "$trace/events/uprobes/enable"
echo 1 > "$trace/tracing_on"
sleep "$seconds"
echo 0 > "$trace/tracing_on"
cat "$trace/trace" > "$output"
wc -l "$output"
