#!/usr/bin/env python3
"""Synthetic guarded narrow-op2 stream pixels, ordering, routing and mutations.

The scalar byte equations are independent of the GPU model/RTL (shared with
test_gpu_axis_stream). Set AM2R_TEST_RTL=1 to run actual production RTL and three
compiled mutation witnesses. Only isolated data/work products are generated;
there is no game-data, hardware, Quartus, or deployment access.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import struct
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools"))
import am2r_gpu_reference as gpu
import replay_gpu_capture as replay
from test_gpu_axis_stream import apply_axis, axis_descriptor, pattern, signed
from test_gpu_targets import command, write_fixture

SOURCE, TARGET, EXPORT = 0x24000000, 0x26000000, 0x28000000
SCHEDULES = ((2, 11, 0), (7, 13, 5))
WIDTHS = (1, 2, 3, 15, 31, 32, 33)


def stream_rows(case):
    """Independent descriptor contract, including first-visible-sample guards."""
    if case.get("du", 65536) != 65536:
        return 0
    dv = signed(case.get("dv", 65536), 32)
    minimum = 2 if case["stride"] >= 256 and abs(dv) >= 65536 else 32
    first = max(0, -case["x"])
    width = min(case["width"] - first, 320 - (case["x"] + first))
    u = signed(case.get("u", 0) + first * 65536, 32)
    if not minimum <= width <= 320 or u < 0 or u // 65536 + width > 32768:
        return 0
    rows = 0
    for row in range(case["height"]):
        if 0 <= case["y"] + row < 240:
            sy = (signed(case.get("v", 0) + row * dv, 32) // 65536) % 65536
            address = case["base"] + sy * case["stride"] + (u // 65536) * 4
            rows += address % 4 == 0
    return rows


def narrow_fixture():
    initial = pattern(gpu.PIXELS, 201)
    expected = list(initial)
    memory = struct.pack("<131072I", *pattern(131072, 59))
    target = bytearray(struct.pack("<1024I", *pattern(1024, 143)))
    regions = {SOURCE: memory, TARGET: bytes(target)}
    commands, cases, exports = [], [], {}

    def prefix(y, height):
        first, last = max(0, y), min(240, y + height)
        if first >= last:
            return
        base = EXPORT + len(exports) * 4096
        exports[base] = struct.pack(f"<{(last - first) * 320}I",
                                   *expected[first * 320:last * 320])
        commands.append(command(11, 320, last - first, base=base, stride=1280, y=first))

    def draw(label, *, export=True, source=memory, region=SOURCE, **values):
        case = dict(base=SOURCE + 131072, stride=256, width=3, height=3,
                    x=0, y=17 + len(cases) % 211, u=0, v=4 << 16,
                    du=65536, dv=65536, tint=0x817DC351, mode=0, floor=False,
                    steps=(173, -217, 101, 83))
        case.update(values)
        cases.append(dict(case, label=label, command=len(commands), stream_rows=stream_rows(case)))
        commands.append(axis_descriptor(**case))
        apply_axis(expected, source, region, **case)
        if export:
            prefix(case["y"], case["height"])

    # Every width/mode/rounding combination covers even/odd source and target,
    # left/right/top/bottom clipping, fractional U, and both signs of full V.
    layouts = (
        dict(x=0, base=SOURCE + 131072, stride=256, u=0, dv=65536),
        dict(x=1, base=SOURCE + 131076, stride=260, u=32768, dv=-65536),
        dict(x=-1, base=SOURCE + 131072, stride=516, u=65535, dv=65536),
        dict(x=318, base=SOURCE + 131076, stride=256, u=3 << 16, dv=-131072),
        dict(x=3, base=SOURCE + 131072, stride=260, u=1 << 16, dv=131072),
        dict(x=1, base=SOURCE + 131076, stride=256, u=2 << 16, dv=65536),
    )
    tints = (0xFFFFFFFF, 0x7F80FE01, 0x00FEDCBA, 0x0101FEFF, 0xFEFF0001, 0x815731C9)
    for mode in range(3):
        for floor in (False, True):
            for width in WIDTHS:
                for layout, values in enumerate(layouts):
                    values = dict(values)
                    if layout == 5:
                        values["y"] = -1 if width % 2 else 238
                    draw(f"matrix-w{width}-m{mode}-f{int(floor)}-p{layout}",
                         width=width, mode=mode, floor=floor, tint=tints[layout], **values)

    # Narrow negative guards must not enable streams. Width32/33 controls show
    # that rejecting *narrow* routing does not disable the old wide path.
    for width in (2, 3, 15, 31, 32, 33):
        for label, values in (("stride252", dict(stride=252)),
                              ("dv0", dict(dv=0)),
                              ("dv+subunit", dict(dv=65535)),
                              ("dv-subunit", dict(dv=-65535))):
            draw(f"guard-w{width}-{label}", width=width, **values)
    for label, values in (("du0", dict(du=0)), ("du-negative", dict(du=-65536, u=40 << 16)),
                          ("du-half", dict(du=32768)), ("du-double", dict(du=131072)),
                          ("negative-u", dict(u=-65536)),
                          ("signed-u-wrap", dict(u=32767 << 16))):
        for mode in range(3):
            draw(f"reject-{label}-m{mode}", width=3, mode=mode, floor=bool(mode & 1), **values)

    # No intervening descriptor resets: eligible -> changed guard -> eligible
    # tests stale narrow metadata, while rejected -> eligible tests stale false.
    for index, (label, values) in enumerate((
        ("eligible", {}), ("small-stride", dict(stride=252)),
        ("eligible2", {}), ("zero-v", dict(dv=0)),
        ("negative-full-v", dict(dv=-65536)), ("positive-sub-v", dict(dv=65535)),
        ("positive-full-v", {}), ("negative-sub-v", dict(dv=-65535)),
        ("super-v", dict(dv=131072)), ("nonunit-u", dict(du=32768)),
        ("eligible3", {}), ("single-visible", dict(x=319)),
        ("eligible4", {}))):
        values = dict(dict(x=index % 2), **values)
        draw(f"direct-chain-{label}", export=False, y=180,
             mode=index % 3, floor=bool(index & 1), **values)
    prefix(180, 3)

    # Prime a cached source, STORE streamed pixels over it, CLEAR, then read
    # them with both narrow-stream and LOAD paths. Prefixes retain exact RGBA,
    # including alpha and untouched neighbour lanes, before overlapping draws.
    draw("ordering-prime-cache", source=target, region=TARGET, base=TARGET,
         width=1, height=1, y=239, x=319, u=0, v=0, stride=256)
    draw("ordering-stream-before-store", width=31, height=3, x=1, y=40,
         mode=1, floor=True, tint=0xA193E731)
    commands.append(command(11, 31, 3, base=TARGET, stride=256, x=1, y=40))
    for row in range(3):
        payload = struct.pack("<31I", *expected[(40 + row) * 320 + 1:(40 + row) * 320 + 32])
        target[row * 256:row * 256 + 124] = payload
        exports[TARGET + row * 256] = payload
    commands.append(command(1, base=0x73512987))
    expected[:] = [0x73512987] * gpu.PIXELS
    draw("ordering-read-new-ddr", source=target, region=TARGET, base=TARGET,
         stride=256, width=31, height=3, x=1, y=44, v=0, mode=2, floor=True)
    commands.append(command(10, 31, 3, base=TARGET, stride=256, x=3, y=50))
    for row in range(3):
        expected[(50 + row) * 320 + 3:(50 + row) * 320 + 34] = struct.unpack_from("<31I", target, row * 256)
    prefix(50, 3)
    draw("ordering-overlap-stream", width=3, height=3, x=3, y=50, mode=1)
    draw("ordering-following-cache", width=3, height=3, x=3, y=50, dv=0, mode=2, floor=True)
    base = EXPORT + 0x400000
    exports[base] = struct.pack(f"<{gpu.PIXELS}I", *expected)
    commands.extend((command(11, 320, 240, base=base, stride=1280), command(12)))
    assert len(commands) <= 1024
    return b"".join(commands), regions, initial, expected, exports, cases


def check_scalar(fixture):
    commands, regions, initial, expected, exports, _ = fixture
    actual = gpu.render(commands, regions, initial)
    if actual.pixels != expected or actual.presented:
        raise AssertionError("independent final RGBA mismatch")
    for base, wanted in exports.items():
        found = next((data[base - start:base - start + len(wanted)]
                      for start, data in actual.exports.items()
                      if start <= base and base + len(wanted) <= start + len(data)), None)
        if found != wanted:
            raise AssertionError(f"independent prefix RGBA mismatch at {base:#x}")


def instrumented_bench():
    """Observe actual stream entry; never change DUT or Avalon memory timing."""
    source = (ROOT / "tests/rtl/am2r_gpu_replay_tb.sv").read_text()
    declaration = "    integer previous_command_index = 0;"
    source = source.replace(declaration, declaration + "\n    integer narrow_stream_rows [0:1023];")
    initialization = "            command_cycles[profile_index] = 0;"
    source = source.replace(initialization, initialization + "\n            narrow_stream_rows[profile_index] = 0;")
    observation = "            state_cycles[dut.state] = state_cycles[dut.state] + 1;"
    source = source.replace(observation, observation + """
            if (dut.command_index < command_count && dut.axis_stream_mode &&
                dut.state == dut.ST_WATER_SOURCE_ADDRESS)
                narrow_stream_rows[dut.command_index] = narrow_stream_rows[dut.command_index] + 1;
""")
    output = '        $fclose(profile_fd);'
    source = source.replace(output, """
        for (profile_index = 0; profile_index < command_count; profile_index = profile_index + 1)
            $fdisplay(profile_fd, "axis_stream_rows,%0d,%0d", profile_index, narrow_stream_rows[profile_index]);
""" + output)
    for marker in (declaration, initialization, observation, output):
        if source.count(marker) != 1:
            raise AssertionError(f"replay instrumentation anchor not unique: {marker}")
    return source


def run_rtl(directory, fixture, rtl, schedule):
    directory.mkdir(parents=True)
    commands, regions, initial, _, _, _ = fixture
    manifest = write_fixture(directory, commands, regions, initial)
    result, _, memory_words, exports = replay.prepare(manifest, directory)
    snapshot = directory / "am2r_gpu.sv"
    snapshot.write_bytes(rtl.encode("utf-8"))
    bench = directory / "narrow_axis_replay_tb.sv"
    bench.write_bytes(instrumented_bench().encode("utf-8"))
    executables = {name: shutil.which(name) for name in ("vlib", "vlog", "vsim")}
    if not all(executables.values()):
        raise RuntimeError("ModelSim vlib/vlog/vsim must be available")
    replay._run([executables["vlib"], "work"], directory, "vlib", 60)
    replay._run([executables["vlog"], "-sv", "-work", "work", str(snapshot), str(bench)],
                directory, "vlog", 60)
    latency, stall, gap = schedule
    output = replay._run([executables["vsim"], "-c", "-lib", "work", "am2r_gpu_replay_tb",
        f"-gMEMORY_WORDS={memory_words}", f"-gREAD_LATENCY={latency}",
        f"-gSTALL_PERIOD={stall}", f"-gRESPONSE_GAP={gap}", "-gMAX_CYCLES=20000000",
        "-do", "onerror {quit -code 1}; run -all; quit -code 0"], directory, "vsim", 180)
    if "PASS: captured GPU job completed" not in output:
        raise RuntimeError("RTL simulation did not complete (not a valid mutation witness)")
    actual = replay._read_frame(directory / "rtl-bram.hex")
    # Count mismatching complete outputs, not individual differing pixels.
    pixel_errors = int(actual != fixture[3])
    for index, base in enumerate(exports):
        pixel_errors += replay._read_export(directory / f"rtl-export-{index}.hex", base,
                                           len(result.exports[base])) != result.exports[base]
    profile = {}
    for line in (directory / "rtl-profile.csv").read_text().splitlines()[1:]:
        kind, index, count = line.split(",")
        profile[kind, int(index)] = int(count)
    routing = {case["label"]: profile["axis_stream_rows", case["command"]]
               for case in fixture[5]}
    wrong_routes = [case["label"] for case in fixture[5]
                    if routing[case["label"]] != case["stream_rows"]]
    eligible_commands = [case["command"] for case in fixture[5]
                         if 2 <= case["width"] < 32 and case["stream_rows"]]
    return dict(rtl_sha256=hashlib.sha256(snapshot.read_bytes()).hexdigest(),
                bench_sha256=hashlib.sha256(bench.read_bytes()).hexdigest(),
                pixel_errors=pixel_errors, wrong_routes=wrong_routes, routing=routing,
                narrow_cycles=sum(profile["command", index] for index in eligible_commands),
                operation_cycles=int((directory / "rtl-status.txt").read_text().split()[1]))


class NarrowAxisStream(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.fixture = narrow_fixture()

    def test_independent_rgba_all_prefixes_and_final_frame(self):
        check_scalar(self.fixture)
        cases = self.fixture[5]
        for width in WIDTHS:
            self.assertTrue(any(c["width"] == width and c["stream_rows"] for c in cases) if width > 1
                            else all(c["stream_rows"] == 0 for c in cases if c["width"] == 1))
        self.assertTrue(all(c["stream_rows"] == 0 for c in cases
                            if c["label"].startswith("guard-w") and c["width"] < 32))

    @unittest.skipUnless(os.getenv("AM2R_TEST_RTL") == "1", "set AM2R_TEST_RTL=1")
    def test_actual_stream_routing_pixels_ordering_and_compiled_mutations(self):
        check_scalar(self.fixture)
        parent = ROOT / "data/work/narrow-axis-stream-20261004"
        parent.mkdir(parents=True, exist_ok=True)
        directory = Path(tempfile.mkdtemp(prefix="synthetic-", dir=parent)).resolve()
        rtl = (ROOT / "rtl/am2r_gpu.sv").read_text()
        report = dict(directory=str(directory), cases=len(self.fixture[5]),
                      commands=len(self.fixture[0]) // 64, prefixes=len(self.fixture[4]),
                      schedules={}, mutations={})
        for schedule in SCHEDULES:
            result = run_rtl(directory / ("production-" + "-".join(map(str, schedule))),
                             self.fixture, rtl, schedule)
            self.assertEqual(result["pixel_errors"], 0, "independent RTL RGBA mismatch")
            self.assertEqual(result["wrong_routes"], [], "actual stream-routing contract mismatch")
            report["schedules"][str(schedule)] = result
        old, replacements = re.subn(r"axis_stream_width_stage\s*>=\s*[^&\n]+",
                                    "axis_stream_width_stage >= 17'd32 ", rtl, count=1)
        self.assertEqual(replacements, 1)
        self.assertNotEqual(old, rtl, "candidate must have guarded narrow minimum")
        old_result = run_rtl(directory / "old-minimum32", self.fixture, old, SCHEDULES[0])
        self.assertEqual(old_result["pixel_errors"], 0, "old routing mutation must remain pixel-correct")
        self.assertTrue(old_result["wrong_routes"], "compiled old32 mutation escaped actual routing check")
        production = report["schedules"][str(SCHEDULES[0])]
        self.assertLess(production["narrow_cycles"], old_result["narrow_cycles"],
                        "narrow streaming must beat historical cache routing on this fixture")
        report["mutations"]["old-minimum32"] = old_result
        metadata = re.findall(r"axis_stream_narrow\s*<=\s*descriptor\[1\]\[63:32\][\s\S]*?;", rtl)
        self.assertEqual(len(metadata), 1, "opcode2 narrow metadata must be assigned exactly once")
        rhs = metadata[0].split("<=", 1)[1].rstrip(";")
        stale = rtl.replace(metadata[0], "axis_stream_narrow <= axis_stream_narrow || (" + rhs + ");")
        stale_result = run_rtl(directory / "inherited-narrow-metadata", self.fixture, stale, SCHEDULES[1])
        self.assertEqual(stale_result["pixel_errors"], 0,
                         "metadata mutation must complete with equivalent pixels")
        self.assertTrue(any(label.startswith("direct-chain-") for label in stale_result["wrong_routes"]),
                        "consecutive changed-guard descriptors did not catch inherited metadata")
        report["mutations"]["inherited-narrow-metadata"] = stale_result
        address = "axis_stream_row_address <= axis_source_addr0 & 32'hffff_fff8;"
        self.assertEqual(rtl.count(address), 1)
        wrong = rtl.replace(address, "axis_stream_row_address <= (axis_source_addr0 & 32'hffff_fff8) + 32'd8;")
        wrong_result = run_rtl(directory / "wrong-source-beat", self.fixture, wrong, SCHEDULES[1])
        self.assertGreater(wrong_result["pixel_errors"], 0,
                           "compiled wrong-source-beat mutation escaped independent RGBA check")
        report["mutations"]["wrong-source-beat"] = wrong_result
        (directory / "result.json").write_text(json.dumps(report, indent=2) + "\n")
        print(json.dumps({key: report[key] for key in ("directory", "cases", "commands", "prefixes")},
                         indent=2))


if __name__ == "__main__":
    unittest.main()
