#!/bin/sh
# Identify which retained runner binary matches a restored DMTCP text mapping.

pid="${1:?usage: identify_checkpoint_runner.sh PID}"
dump=/tmp/am2r-checkpoint-text.bin
slice=/tmp/am2r-candidate-text.bin

gdb -q -nx -batch -p "$pid" \
    -ex "dump binary memory $dump 0x0003c000 0x001b0000" \
    -ex detach >/tmp/am2r-checkpoint-dump.log 2>&1 || {
    cat /tmp/am2r-checkpoint-dump.log
    exit 1
}

for candidate in \
    /media/fat/games/am2r/bin/butterscotch \
    /media/fat/games/am2r/bin/butterscotch.pre-hud-v14-20260908 \
    /media/fat/games/am2r/bin/butterscotch.pre-multisource-fusion-20260908 \
    /media/fat/games/am2r/bin/butterscotch.pre-surface-revision-v2-20260908 \
    /media/fat/games/am2r/bin/butterscotch.pre-surface-revision-20260908
do
    test -f "$candidate" || continue
    dd if="$candidate" of="$slice" bs=4096 skip=28 count=372 2>/dev/null
    bytes="$(wc -c < "$slice")"
    differences="$(cmp -l "$dump" "$slice" 2>/dev/null | wc -l)"
    printf 'CHECKPOINT_RUNNER candidate=%s bytes=%s differing_bytes=%s\n' \
        "$candidate" "$bytes" "$differences"
done

rm -f "$dump" "$slice" /tmp/am2r-checkpoint-dump.log
