#!/usr/bin/env python3
"""Independent RGBA, ordering and DDR-work tests for generic additive no-ops.

Synthetic only: no game payload or hardware. Prefix exports prevent later draws
from hiding errors. Rejection mutants check blend/alpha/plane guards and work
avoidance, not just final-frame equality. SPDX-License-Identifier: GPL-2.0-or-later
"""
from __future__ import annotations
import argparse
import csv
import hashlib
import json
from pathlib import Path
import struct
import subprocess
import sys
import tempfile

from test_gpu_generic import ONE, MODES, TEXTURE, PACKETS, packet, encoded, rgba
from test_gpu_targets import command, write_fixture
from test_unified_blend_contract import blend_rgba
import am2r_gpu_reference as gpu

ROOT = Path(__file__).resolve().parents[2]
EXPORT = 0x28000000


def signed(value):
    value &= (1 << 64) - 1
    return value - (1 << 64) if value >> 63 else value


def lanes(pixel):
    return tuple(pixel >> (8 * c) & 255 for c in range(4))


def fixture():
    initial = [((i * 0x19372B57) ^ 0x5C7E91B3) & 0xFFFFFFFF for i in range(gpu.PIXELS)]
    expected = list(initial)
    texels = [rgba(((i * 17 + 3) & 255, (i * 73 + 5) & 255,
                   (i * 31 + 9) & 255, (i * 11 + 1) & 255)) for i in range(256)]
    packets, commands, cases, exports = bytearray(), [], [], {}
    configurations = []
    for alpha in (0, -1, -669, -(1 << 63)):
        for textured in (False, True):
            for triangle in (False, True):
                for mask in (0, 1, 8, 15):
                    configurations.append((dict(textured=textured, triangle=triangle, mode=1, mask=mask),
                                           {35: alpha}, True, 'identity'))
    for alpha in (1, ONE // 255 + 1, ONE // 2, ONE, 2 * ONE):
        configurations.append((dict(textured=True, mode=1), {35: alpha}, False, 'positive-alpha'))
    for derivative in (36, 37, 38):
        configurations.append((dict(textured=True, triangle=True, mode=1),
                               {35: -669, derivative: ONE // 8}, False, 'alpha-gradient'))
    for mode in (0, 2, 3, 4, 5, 6):
        configurations.append((dict(textured=True, mode=mode), {35: -669}, False, f'mode-{mode}'))
    configurations.extend([
        (dict(textured=True, mode=1, enabled=False), {35: -669}, False, 'replacement'),
        (dict(textured=True, mode=1, fog=(251, 2, 129)), {35: -669}, False, 'fog'),
        (dict(textured=True, mode=1, alpha_test=True, alpha_ref=128), {35: -669}, False, 'alpha-test'),
    ])
    for index, (options, overrides, eligible, label) in enumerate(configurations):
        q = packet(**options)
        q[8:17] = [ONE // 2, ONE, 0, ONE // 2, 0, ONE, 10 * ONE, -ONE, -ONE]
        q[17:23] = [-ONE // 4, ONE, ONE // 8, 7 * ONE, ONE // 8, -ONE]
        for c in range(3):
            q[23 + c * 4:27 + c * 4] = [(c + 1) * ONE // 4, ONE // 128, -ONE // 256, ONE // 1024]
        for word, value in overrides.items():
            q[word] = value
        x, y = (index % 20) * 16, (index // 20) * 40
        base = PACKETS + len(packets)
        packets += encoded(q)
        ci = len(commands)
        commands.append(command(13, 12, 12, base=base, x=x, y=y))
        for row in range(12):
            for col in range(12):
                if options.get('triangle') and col + row > 10:
                    continue
                if options.get('textured'):
                    coords = [min(7, max(0, (signed(q[17 + c * 3]) + col * signed(q[18 + c * 3])
                                      + row * signed(q[19 + c * 3])) // ONE)) for c in range(2)]
                    texel = lanes(texels[coords[1] * 32 + coords[0]])
                else:
                    texel = (255,) * 4
                source = []
                for c in range(4):
                    colour = (signed(q[23 + c * 4]) + col * signed(q[24 + c * 4])
                              + row * signed(q[25 + c * 4]) + col * row * signed(q[26 + c * 4]))
                    source.append(texel[c] * min(ONE, max(0, colour)) // ONE)
                at = (y + row) * 320 + x + col
                expected[at] = rgba(blend_rgba(source, lanes(expected[at]), MODES[options.get('mode', 0)],
                    factors=options.get('factors', (2, 2, 2, 2)), enabled=options.get('enabled', True),
                    fog=options.get('fog'), alpha_test=options.get('alpha_test', False),
                    alpha_ref=options.get('alpha_ref', 0),
                    write_mask=tuple(bool(options.get('mask', 15) & (1 << c)) for c in range(4))))
        out = EXPORT + index * 1024
        commands.append(command(11, 16, 16, base=out, stride=64, x=x, y=y))
        exports[out] = b''.join(struct.pack('<16I', *expected[(y + row) * 320 + x:(y + row) * 320 + x + 16])
                                for row in range(16))
        cases.append(dict(index=ci, eligible=eligible, label=label))
    # No STORE/cache operation between these packets: ordinary visible generic
    # -> identity -> identity -> visible. Full-screen identities must not alter
    # any pixel, and the successor must fetch its own packet/texel/state.
    for chain, alpha in enumerate((ONE, 0, -669, ONE)):
        q = packet(textured=True, mode=1)
        q[35] = alpha
        q[17], q[20] = (1 if chain == 0 else 2) * ONE, ONE
        base = PACKETS + len(packets)
        packets += encoded(q)
        eligible = chain in (1, 2)
        ci = len(commands)
        commands.append(command(13, 320 if eligible else 3, 240 if eligible else 2,
                                base=base, x=0 if eligible else 25, y=0 if eligible else 201))
        if eligible:
            cases.append(dict(index=ci, eligible=True, label='direct-chain-identity'))
        else:
            source = lanes(texels[33 if chain == 0 else 34])
            for row in range(201, 203):
                for col in range(25, 28):
                    at = row * 320 + col
                    expected[at] = rgba(blend_rgba(source, lanes(expected[at]), 'add'))
    out = EXPORT + len(configurations) * 1024
    commands.append(command(11, 16, 16, base=out, stride=64, x=24, y=192))
    exports[out] = b''.join(struct.pack('<16I', *expected[(192 + row) * 320 + 24:(192 + row) * 320 + 40])
                            for row in range(16))
    # Ordered STORE -> ordinary textured draw checks cache and command state.
    commands.append(command(2, 1, 1, base=EXPORT, stride=64, x=319, y=239, w6=0xFFFFFFFF))
    source = lanes(initial[0])
    expected[-1] = rgba(blend_rgba(source, lanes(expected[-1]))) if source[3] else expected[-1]
    commands.append(command(12))
    return b''.join(commands), {TEXTURE: struct.pack('<256I', *texels), PACKETS: bytes(packets)}, initial, expected, exports, cases


def run(directory, source, *, latency=2, stall=11, gap=0):
    directory.mkdir()
    rtl = directory / 'am2r_gpu.sv'
    rtl.write_bytes(source)
    commands, regions, initial, expected, exports, cases = fixture()
    result = gpu.render(commands, regions, initial)
    produced = {base: next((payload[base - start:base - start + len(data)]
                            for start, payload in result.exports.items()
                            if start <= base and base + len(data) <= start + len(payload)), None)
                for base, data in exports.items()}
    if result.pixels != expected or produced != exports:
        raise RuntimeError('Independent generic prefix/final RGBA mismatch')
    manifest = write_fixture(directory, commands, regions, initial)
    process = subprocess.run([sys.executable, ROOT / 'tools/replay_gpu_capture.py', manifest,
        '--rtl', rtl, '--output', directory, '--read-latency', str(latency),
        '--stall-period', str(stall), '--response-gap', str(gap)], capture_output=True, text=True, timeout=240)
    (directory / 'replay.log').write_text(process.stdout + process.stderr, encoding='utf-8')
    if process.returncode:
        comparisons = list(directory.glob('gpu-replay-*/comparison.json'))
        if len(comparisons) == 1 and json.loads(comparisons[0].read_text())['matching'] is False:
            raise RuntimeError('RTL pixel replay failed: ' + process.stdout[-1000:] + process.stderr[-1000:])
        raise RuntimeError('RTL execution failed: ' + process.stdout[-1000:] + process.stderr[-1000:])
    paths = list(directory.glob('gpu-replay-*/rtl-profile.csv'))
    if len(paths) != 1:
        raise RuntimeError('Expected exactly one replay profile')
    counts = {(r['kind'], int(r['index'])): int(r['cycles'])
              for r in csv.DictReader(paths[0].open(encoding='utf-8'))}
    for case in cases:
        ci, cycles = case['index'], counts['command', case['index']]
        if case['eligible']:
            if cycles > 800 or counts['texture_reads', ci] or counts['fb_writes', ci]:
                raise RuntimeError(f'Identity did raster/texture work: {case}, {cycles} cycles')
        elif cycles < 800:
            raise RuntimeError(f'Ineligible packet incorrectly culled: {case}, {cycles} cycles')
    return dict(cases=len(cases), identities=sum(c['eligible'] for c in cases),
                negative_controls=sum(not c['eligible'] for c in cases), ddr=[latency, stall, gap],
                prefix_exports=len(exports), matching=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, default=ROOT / 'data/build')
    parser.add_argument('--mutation-check', action='store_true')
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    directory = Path(tempfile.mkdtemp(prefix='generic-noop-', dir=args.output)).resolve()
    source = (ROOT / 'rtl/am2r_gpu.sv').read_bytes()
    print(f'Running generic no-op tests: {directory}', flush=True)
    report = dict(rtl_sha256=hashlib.sha256(source).hexdigest(),
                  schedules=[run(directory / f'candidate-{i}', source, latency=l, stall=s, gap=g)
                             for i, (l, s, g) in enumerate(((2, 11, 0), (7, 13, 5)))])
    if args.mutation_check:
        changes = {
            'old-no-cull': (b"else if (generic_packet[2][58:56] == 3'b100 &&", b"else if (1'b0 && generic_packet[2][58:56] == 3'b100 &&"),
            'ignore-blend-mode': (b"generic_packet[2][39:32] == 8'd1 &&", b"1'b1 &&"),
            'ignore-alpha-sign': (b"(generic_packet[35][63] || generic_packet[35] == 64'd0) &&", b"1'b1 &&"),
            'ignore-alpha-dx': (b"generic_packet[36] == 64'd0 && generic_packet[37]", b"1'b1 && generic_packet[37]"),
        }
        report['mutations'] = {}
        for name, (old, new) in changes.items():
            if source.count(old) != 1:
                raise RuntimeError(f'Expected unique mutation marker: {name}')
            try:
                run(directory / name, source.replace(old, new))
            except RuntimeError as error:
                if not any(s in str(error) for s in ('Identity did raster/texture work', 'Ineligible packet incorrectly culled', 'RTL pixel replay failed')):
                    raise
                report['mutations'][name] = dict(rejected=True, reason=str(error)[:400])
            else:
                raise RuntimeError(f'Mutation was not rejected: {name}')
    (directory / 'verification.json').write_text(json.dumps(report, indent=2) + '\n', encoding='utf-8')
    print(json.dumps(report, indent=2))


if __name__ == '__main__':
    main()
