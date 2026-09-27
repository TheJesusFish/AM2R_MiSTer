#!/usr/bin/env python3
"""Replay a captured GPU job through unmodified RTL and compare every pixel.

Requires ModelSim vlib/vlog/vsim on PATH. The generated mapping is a bounded
simulated DDR image, not real /dev/mem access. There is no MiSTer deployment.
Capture inputs and results may contain private game graphics; keep them in data/.
Bounded-clear jobs retain exact untouched BRAM comparison: include observer-fenced
raw initial_framebuffer RGBA when no preceding operation initializes all pixels.
Unknown pixels are never initialized to black or excluded from comparisons.
SPDX-License-Identifier: GPL-2.0-or-later
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import re
import shutil
import struct
import subprocess
import tempfile
from typing import Sequence

import am2r_gpu_reference as reference

ROOT = Path(__file__).resolve().parents[1]
COMMAND_BASE = 0x23FE0000
CONTROL_BASE = 0x23FF0000
NATIVE_BASE = 0x3A000100
MAX_REPLAY_BYTES = 136 * 1024 * 1024


def _overlaps(a: int, count: int, b: int, other_count: int) -> bool:
    return a < b + other_count and b < a + count


def _hex_file(path: Path, data: bytes) -> None:
    if len(data) % 8:
        raise reference.CaptureError("RTL memory input must be 64-bit aligned")
    with path.open("w", encoding="ascii", newline="\n") as stream:
        for start in range(0, len(data), 8192 * 8):
            stream.write("".join(f"{word:016x}\n" for (word,) in struct.iter_unpack("<Q", data[start:start + 8192 * 8])))


def prepare(path: Path, directory: Path) -> tuple[reference.RenderResult, dict, int, list[int]]:
    manifest, commands, regions, initial = reference.load_capture(path)
    native = manifest.get("native_source_base")
    native = reference._integer(native, "native_source_base") if native is not None else None
    result = reference.render(commands, regions, initial, native_source_base=native)
    if native is not None and native not in [NATIVE_BASE + i * reference.FRAME_BYTES for i in range(3)]:
        raise reference.CaptureError("native_source_base is not an actual AM2R native buffer")
    prior_native = 0 if native is None else (native - NATIVE_BASE) // reference.FRAME_BYTES
    memory = dict(regions)
    for base, data in memory.items():
        if base & 7 or len(data) & 7:
            raise reference.CaptureError("RTL replay requires 64-bit-aligned captured DDR regions")
        if _overlaps(base, len(data), COMMAND_BASE, 65536) or _overlaps(base, len(data), CONTROL_BASE, 88):
            raise reference.CaptureError("captured region aliases replay command/control mailbox")
    memory[COMMAND_BASE] = commands

    def reserve_output(base: int, count: int) -> None:
        if _overlaps(base, count, COMMAND_BASE, 65536) or _overlaps(base, count, CONTROL_BASE, 88):
            raise reference.CaptureError("output aliases replay command/control mailbox")
        overlapping = []
        for existing, data in memory.items():
            if existing <= base and base + count <= existing + len(data):
                return
            if _overlaps(base, count, existing, len(data)):
                overlapping.append((existing, data))
        # Sparse input captures need not include an entire later output. The
        # strict scalar replay above has already rejected every logical read of
        # uncaptured/unwritten bytes. Reserve only this output's missing bytes,
        # preserving all captured bytes in partially overlapping input regions.
        first = min([base] + [start for start, _ in overlapping])
        end = max([base + count] + [start + len(data) for start, data in overlapping])
        if end - first > MAX_REPLAY_BYTES:
            raise reference.CaptureError("output reservation exceeds bounded limit")
        merged = bytearray(end - first)
        for start, data in overlapping:
            merged[start - first:start - first + len(data)] = data
            del memory[start]
        memory[first] = bytes(merged)

    for index in range(3):
        reserve_output(NATIVE_BASE + index * reference.FRAME_BYTES, reference.FRAME_BYTES)
    exports = sorted(result.exports)
    for base in exports:
        aligned = base & ~7
        count = (base + len(result.exports[base]) + 7 & ~7) - aligned
        reserve_output(aligned, count)
    if len(memory) > reference.MAX_REGIONS:
        raise reference.CaptureError("too many RTL memory regions")
    total = sum(len(data) for data in memory.values())
    if total > MAX_REPLAY_BYTES:
        raise reference.CaptureError("RTL replay memory exceeds bounded limit")
    mappings = []
    offset = 0
    with (directory / "memory.hex").open("w", encoding="ascii", newline="\n") as stream:
        for base, data in sorted(memory.items()):
            mappings.append((base >> 3, len(data) // 8, offset))
            for start in range(0, len(data), 8192 * 8):
                stream.write("".join(f"{word:016x}\n" for (word,) in struct.iter_unpack("<Q", data[start:start + 8192 * 8])))
            offset += len(data) // 8
    mapping = f"{len(mappings)} {len(exports)} {len(commands) // 64} {prior_native} {int(initial is not None)} {int(result.presented)}\n"
    mapping += "".join(f"{base:08x} {words} {start}\n" for base, words, start in mappings)
    mapping += "".join(f"{base:08x} {len(result.exports[base])}\n" for base in exports)
    (directory / "mapping.txt").write_text(mapping, encoding="ascii")
    if initial is not None:
        _hex_file(directory / "initial.hex", reference.pixel_bytes(initial))
    return result, manifest, offset, exports


def _read_frame(path: Path) -> list[int]:
    if not path.is_file() or path.stat().st_size > 1024 * 1024:
        raise reference.CaptureError(f"missing or excessive RTL output: {path.name}")
    lines = path.read_text(encoding="ascii").splitlines()
    if len(lines) != reference.PIXELS // 2 or any(len(line) != 16 for line in lines):
        raise reference.CaptureError(f"incomplete RTL framebuffer: {path.name}")
    try:
        words = [int(line, 16) for line in lines]
    except ValueError as error:
        raise reference.CaptureError(f"unknown X/Z pixels in RTL output: {path.name}") from error
    return [pixel for word in words for pixel in (word & 0xFFFFFFFF, word >> 32)]


def _read_export(path: Path, base: int, size: int) -> bytes:
    """The simulator dumps complete containing DDR beats; compare only writes."""
    if size == reference.FRAME_BYTES and base & 7 == 0:
        return reference.pixel_bytes(_read_frame(path))
    count = (size + (base & 7) + 7) // 8
    if not path.is_file() or path.stat().st_size > count * 19:
        raise reference.CaptureError(f"missing or excessive RTL export: {path.name}")
    lines = path.read_text(encoding="ascii").splitlines()
    if len(lines) != count or any(len(line) != 16 for line in lines):
        raise reference.CaptureError(f"incomplete RTL export: {path.name}")
    try:
        data = b"".join(struct.pack("<Q", int(line, 16)) for line in lines)
    except ValueError as error:
        raise reference.CaptureError(f"unknown X/Z export bytes: {path.name}") from error
    return data[base & 7:(base & 7) + size]


def _run(command: list[str], directory: Path, name: str, timeout: int) -> str:
    process = subprocess.run(command, cwd=directory, capture_output=True, text=True,
                             errors="replace", timeout=timeout, check=False)
    output = process.stdout + process.stderr
    (directory / f"{name}.log").write_text(output, encoding="utf-8")
    if process.returncode:
        raise reference.CaptureError(f"{name} failed ({process.returncode}); see {directory / (name + '.log')}")
    # Some simulator script/finish configurations still exit zero after an
    # error. Do not let a completion marker elsewhere conceal such failures.
    if re.search(r"(?mi)^\s*(?:#\s*)?\*\*\s*(?:Error|Fatal)\b", output):
        raise reference.CaptureError(f"{name} reported an error despite zero exit code; see {directory / (name + '.log')}")
    return output


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("manifest", type=Path)
    parser.add_argument("--output", type=Path, required=True, help="parent for a new isolated replay directory")
    parser.add_argument("--read-latency", type=int, default=2)
    parser.add_argument("--stall-period", type=int, default=11, help="0 disables request stalls")
    parser.add_argument("--response-gap", type=int, default=0, help="0 disables response gaps; N pauses after N returned beats")
    parser.add_argument("--max-cycles", type=int, default=20000000)
    parser.add_argument("--timeout", type=int, default=180, help="per-tool wall-time limit in seconds")
    args = parser.parse_args(argv)
    try:
        if not 0 <= args.read_latency <= 1000 or not 0 <= args.stall_period <= 1000 or not 0 <= args.response_gap <= 1000:
            raise reference.CaptureError("DDR latency/stall parameters must be between 0 and 1000")
        if args.stall_period == 1:
            raise reference.CaptureError("stall-period 1 never accepts a request")
        if not 1 <= args.max_cycles <= 100000000 or not 1 <= args.timeout <= 600:
            raise reference.CaptureError("cycle/timeout limits out of range")
        executables = {name: shutil.which(name) for name in ("vlib", "vlog", "vsim")}
        if not all(executables.values()):
            raise reference.CaptureError("ModelSim vlib/vlog/vsim must be available on PATH")
        args.output.mkdir(parents=True, exist_ok=True)
        directory = Path(tempfile.mkdtemp(prefix="gpu-replay-", dir=args.output)).resolve()
        print(f"Preparing isolated RTL replay: {directory}", flush=True)
        result, manifest, memory_words, exports = prepare(args.manifest, directory)
        # Validate optional CPU files before launching the simulator. Their
        # pixels are compared directly with captured hardware, independently
        # of the model-versus-RTL checks below.
        cpu_comparisons, cpu_coverage = reference.compare_cpu_captured_exports(
            manifest, args.manifest.parent, result.exports)
        _run([executables["vlib"], "work"], directory, "vlib", args.timeout)
        _run([executables["vlog"], "-sv", "-work", "work", str(ROOT / "rtl/am2r_gpu.sv"),
              str(ROOT / "tests/rtl/am2r_gpu_replay_tb.sv")], directory, "vlog", args.timeout)
        output = _run([executables["vsim"], "-c", "-lib", "work", "am2r_gpu_replay_tb",
                       f"-gMEMORY_WORDS={memory_words}", f"-gREAD_LATENCY={args.read_latency}",
                       f"-gSTALL_PERIOD={args.stall_period}", f"-gRESPONSE_GAP={args.response_gap}",
                       f"-gMAX_CYCLES={args.max_cycles}", "-do",
                       "onerror {quit -code 1}; run -all; quit -code 0"], directory, "vsim", args.timeout)
        if "PASS: captured GPU job completed" not in output:
            raise reference.CaptureError(f"RTL completion marker absent; see {directory / 'vsim.log'}")
        actual = _read_frame(directory / ("rtl-native.hex" if result.presented else "rtl-bram.hex"))
        reference_frame = result.xrgb_pixels if result.presented else result.pixels
        cycles, operation_cycles, output_buffer = map(int, (directory / "rtl-status.txt").read_text().split())
        report = {"format": "am2r-gpu-rtl-replay-v1", "directory": str(directory),
                  "rtl_sha256": hashlib.sha256((ROOT / "rtl/am2r_gpu.sv").read_bytes()).hexdigest(),
                  "commands_sha256": hashlib.sha256(reference._blob(args.manifest.parent, manifest["commands"], reference.MAX_COMMANDS * 64)).hexdigest(),
                  "ddr_model": {"read_latency": args.read_latency, "stall_period": args.stall_period,
                                "response_gap": args.response_gap},
                  "simulation_cycles": cycles, "gpu_operation_cycles": operation_cycles,
                  "native_output_buffer": output_buffer,
                  "presented": result.presented,
                  "initial_framebuffer_supplied": "initial_framebuffer" in manifest,
                  "reference_vs_rtl": reference.compare_pixels(reference_frame, actual),
                  "cpu_vs_captured_exports": cpu_comparisons,
                  "cpu_comparison_coverage": cpu_coverage,
                  "cpu_comparison_status": reference.captured_comparison_status(
                      list(cpu_comparisons.values()), cpu_coverage),
                  "exports": {}}
        actual_exports = {}
        for index, base in enumerate(exports):
            actual_exports[base] = _read_export(directory / f"rtl-export-{index}.hex", base, len(result.exports[base]))
            report["exports"][hex(base)] = reference.compare_rgba_bytes(result.exports[base], actual_exports[base])
        report["rtl_vs_captured_exports"], report["captured_comparison_coverage"] = reference.compare_captured_exports(
            manifest, args.manifest.parent, actual_exports)
        if "expected" in manifest:
            entry = manifest["expected"]
            expected = reference.read_pixels(reference._blob(args.manifest.parent, entry, reference.FRAME_BYTES))
            if entry.get("format") == "rgba8888" and result.presented:
                expected = [reference.rgba_to_xrgb(pixel) for pixel in expected]
            elif entry.get("format") not in (("rgba8888",) if not result.presented else ("xrgb8888",)):
                raise reference.CaptureError("unknown captured expected format")
            report["rtl_vs_captured_expected"] = reference.compare_pixels(actual, expected)
        captured_comparisons = list(report["rtl_vs_captured_exports"].values())
        if "rtl_vs_captured_expected" in report:
            captured_comparisons.append(report["rtl_vs_captured_expected"])
        report["captured_comparison_status"] = reference.captured_comparison_status(
            captured_comparisons, report["captured_comparison_coverage"])
        comparisons = [report["reference_vs_rtl"], *report["exports"].values(),
                       *captured_comparisons, *cpu_comparisons.values()]
        report["matching"] = all(item["matching"] for item in comparisons)
        (directory / ("rtl-native.xrgb" if result.presented else "rtl-bram.rgba")).write_bytes(reference.pixel_bytes(actual))
        (directory / "rtl-output.ppm").write_bytes(reference._ppm(
            [reference.xrgb_to_rgba(pixel) for pixel in actual] if result.presented else actual))
        (directory / "difference.ppm").write_bytes(reference._ppm([0 if a == b else 0xFF for a, b in zip(reference_frame, actual)]))
        (directory / "comparison.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
        print(json.dumps(report, indent=2))
        return 0 if report["matching"] else 1
    except (reference.CaptureError, OSError, ValueError, TypeError, subprocess.TimeoutExpired) as error:
        parser.exit(2, f"RTL replay rejected: {error}\n")


if __name__ == "__main__":
    raise SystemExit(main())
