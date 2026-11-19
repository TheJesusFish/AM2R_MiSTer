#!/usr/bin/env python3
"""Run production v4/v5 stream orchestration with bounded auxiliary mocks.

Actual metadata, CRC, room-visual codecs, reset and persistent-room restore code
are extracted from production. Actor/VM/audio pools use independent fixture
codecs; this test does not claim pixel, ABI, or hardware coverage.
"""
from pathlib import Path
import os
import re
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / 'third_party/Butterscotch/src'


def extract(source, name):
    signature = re.search(r'^(?:static\s+)?(?:inline\s+)?[\w*]+\s+\b' + name +
                          r'\([^;{}]*\)\s*\{', source, re.M)
    if signature is None:
        raise AssertionError('missing production function: ' + name)
    start = source.index('{', signature.start())
    pos, depth = start + 1, 1
    while depth:
        depth += (source[pos] == '{') - (source[pos] == '}')
        pos += 1
    return source[signature.start():pos]


def main():
    fast = (SRC / 'am2r_fast_state.c').read_text()
    runner = (SRC / 'runner.c').read_text()
    room_header = (SRC / 'data_win.h').read_text()
    parser = (SRC / 'data_win.c').read_text()
    for field in ('Width', 'Height', 'Speed', 'Persistent'):
        assert 'initial' + field in room_header
        assert f'room->initial{field} = room->{field.lower()};' in parser
    reset = extract(runner, 'Runner_beginFastStateRestore')
    assert reset.index('Runner_reset(runner);') < reset.index('resetFastStateRoomMetadata(runner);')
    init = extract(runner, 'initRoom')
    branch_start = init.index('if (room->persistent && savedState->initialized) {')
    block_start = init.index('{', branch_start)
    pos, depth = block_start + 1, 1
    while depth:
        depth += (init[pos] == '{') - (init[pos] == '}')
        pos += 1
    branch = init[branch_start:pos]
    definitions = []
    for name in ('FastStateHeader', 'FastWriter', 'FastReader'):
        match = re.search(r'typedef struct \{[^{}]*\} ' + name + r';', fast)
        assert match, name
        definitions.append(match.group(0))
    constants = '\n'.join(re.findall(r'^#define FAST_STATE_(?:VERSION|LEGACY_VERSION|MAX_BYTES|MAX_COUNT|MAX_ROOM_DIMENSION|MAX_ROOM_SPEED)\b[^\r\n]*', fast, re.M))
    primitives = ('crc32Bytes', 'writerReserve', 'writerBytes', 'writerU8', 'writerU32',
                  'writerI32', 'readerBytes', 'readerU8', 'readerU32', 'readerI32',
                  'countOkay', 'roomMetadataOkay', 'writeRoomMetadata',
                  'readRoomMetadata', 'recoverLegacyRoomPersistence')
    with tempfile.TemporaryDirectory(prefix='am2r-fast-room-metadata-') as directory:
        temp = Path(directory)
        (temp / 'fast_room_primitives.inc').write_text(constants + '\n' +
             '\n'.join(definitions) + '\n' + '\n\n'.join(extract(fast, n) for n in primitives))
        (temp / 'fast_room_reset.inc').write_text(extract(runner, 'resetFastStateRoomMetadata'))
        (temp / 'fast_room_visual.inc').write_text('\n\n'.join(extract(fast, n) for n in ('writeRoomVisual', 'readRoomVisual')))
        (temp / 'fast_room_stream.inc').write_text('\n\n'.join(extract(fast, n) for n in ('serializeState', 'deserializeState')))
        (temp / 'fast_room_reenter.inc').write_text(branch)
        env = dict(os.environ, ZIG_GLOBAL_CACHE_DIR=str(ROOT / 'data/build/zig-global-cache'),
                   ZIG_LOCAL_CACHE_DIR=str(temp / 'zig-cache'))
        binary = temp / ('fast-room.exe' if os.name == 'nt' else 'fast-room')
        command = [str(ROOT / 'third_party/zig/zig-x86_64-windows-0.16.0/zig.exe'),
                   'cc', '-std=gnu99', '-O2', '-Wall', '-Wextra', '-Werror',
                   '-I', str(temp), '-I', str(ROOT / 'third_party/Butterscotch/vendor/stb/ds'),
                   str(Path(__file__).with_name('fast_room_metadata_regression.c')), '-o', str(binary)]
        subprocess.run(command, env=env, check=True, timeout=120)
        subprocess.run([str(binary)], check=True, timeout=30)
        for label, filename, old, new in (
            ('lost-v4-persistence', 'fast_room_stream.inc',
             'recoverLegacyRoomPersistence(runner);', '(void)recoverLegacyRoomPersistence;'),
            ('future-timeline-leak', 'fast_room_reset.inc',
             'room->persistent = room->initialPersistent;', '(void)room->initialPersistent;'),
        ):
            include = temp / filename
            original = include.read_text()
            assert original.count(old) == 1, label
            include.write_text(original.replace(old, new))
            subprocess.run(command, env=env, check=True, timeout=120)
            rejected = subprocess.run([str(binary)], capture_output=True, text=True, timeout=30)
            include.write_text(original)
            assert rejected.returncode != 0, 'old defect escaped regression: ' + label
        print('PASS: omitted-v4-recovery and future-timeline persistence mutations rejected')


if __name__ == '__main__':
    main()
