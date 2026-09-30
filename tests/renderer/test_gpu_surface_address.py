#!/usr/bin/env python3
"""Bounded production surface-address state test and a real RTL fault witness.

Uses only the production GPU and the standalone directed testbench. Generated
copies, simulator libraries and logs are isolated under ignored data/build.
No Quartus or hardware access. The full descriptor/pixel differential is a
separate regression; this test covers address widths and setup/issue timing.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import shutil
import subprocess
import time

ROOT = Path(__file__).resolve().parents[2]
MARKER = "SURFACE_ADDRESS_PASS"
MESSAGES = re.compile(r"(?m)^[ \t]*(?:#[ \t]*)?\*\*[ \t]+(Fatal|Error):[ \t]*([^\r\n]*)$")
COUNTS = re.compile(r"(?m)^[ \t]*(?:#[ \t]*)?Errors:[ \t]*([0-9]+)\b")
PASS = re.compile(r"(?m)^[ \t]*(?:#[ \t]*)?" + MARKER + r"\b([^\r\n]*)$")


def check_output(text: str, *, mutation: bool = False) -> str:
    """Never accept a mere mention, unrelated failure, or compile error."""
    messages = MESSAGES.findall(text)
    counts = [int(value) for value in COUNTS.findall(text)]
    passes = PASS.findall(text)
    if mutation:
        if (len(messages) != 1 or messages[0][0] != "Fatal" or passes or
                counts != [1] or not messages[0][1].startswith("SURFACE_ADDRESS bank mismatch ")):
            raise RuntimeError("Mutation did not fail at the intended bank-address oracle")
        return messages[0][1]
    if messages or not counts or any(counts) or len(passes) != 1:
        raise RuntimeError("Production surface-address regression did not pass")
    return passes[0].strip()


def parser_selftest() -> None:
    positive = "# SURFACE_ADDRESS_PASS cases=1\n# Errors: 0, Warnings: 0\n"
    witness = "# ** Fatal: SURFACE_ADDRESS bank mismatch row=00000\n# Errors: 1, Warnings: 0\n"
    check_output(positive)
    check_output(witness, mutation=True)
    rejected = [
        ("# note: SURFACE_ADDRESS_PASS cases=1\n# Errors: 0\n", False),
        (positive + "# ** Fatal: unrelated failure\n", False),
        (positive.replace("Errors: 0", "Errors: 1"), False),
        (positive.replace("# Errors: 0, Warnings: 0\n", ""), False),
        ("# note: SURFACE_ADDRESS bank mismatch row=0\n# ** Fatal: timeout\n# Errors: 1\n", True),
        (witness.replace("** Fatal:", "** Error:"), True),
        (witness + "# ** Error: unrelated failure\n", True),
        (witness.replace("Errors: 1", "Errors: 2"), True),
        (witness.replace("# Errors: 1, Warnings: 0\n", ""), True),
        (witness + positive, True),
        (witness.replace("bank mismatch row=", "staged row bases mismatch row="), True),
    ]
    for text, mutation in rejected:
        try:
            check_output(text, mutation=mutation)
        except RuntimeError:
            continue
        raise AssertionError("Surface-address parser accepted an unrelated or incomplete result")


def run(command: list[str | Path], log: Path, *, simulation: bool = False) -> tuple[str, float]:
    start = time.monotonic()
    try:
        result = subprocess.run(list(map(str, command)), cwd=log.parent, text=True,
                                stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=300)
    except subprocess.TimeoutExpired as error:
        output = error.stdout or ""
        if isinstance(output, bytes):
            output = output.decode(errors="replace")
        log.write_text(output + "\nHARNESS_TIMEOUT: 300-second ceiling exceeded\n", encoding="utf-8")
        raise RuntimeError(f"Simulator/tool timeout, not an accepted assertion: {log}") from error
    log.write_text(result.stdout, encoding="utf-8")
    if result.returncode or (not simulation and
                            (MESSAGES.search(result.stdout) or any(int(x) for x in COUNTS.findall(result.stdout)))):
        raise RuntimeError(f"Tool failed ({result.returncode}); see {log}\n{result.stdout[-2500:]}")
    return result.stdout, time.monotonic() - start


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, help="Fresh log directory; defaults to ignored data/build")
    args = parser.parse_args()
    parser_selftest()
    output = (args.output or ROOT / "data/build" /
              ("gpu-surface-address-" + datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S"))).resolve()
    if output.exists():
        raise RuntimeError("Choose a fresh output directory")
    output.mkdir(parents=True)
    paths = [ROOT / "rtl/am2r_gpu.sv", ROOT / "tests/rtl/am2r_gpu_surface_address_tb.sv"]
    original = [path.read_bytes() for path in paths]
    for name, data in zip(("candidate.sv", "fixture.sv"), original):
        (output / name).write_bytes(data)
    tools = {name: shutil.which(name) for name in ("vlib", "vlog", "vsim")}
    if not all(tools.values()):
        raise RuntimeError("ModelSim vlib/vlog/vsim must be on PATH")
    library = output / "work"
    run([tools["vlib"], library], output / "vlib.log")
    run([tools["vlog"], "-sv", "-work", library, output / "candidate.sv", output / "fixture.sv"],
        output / "production-compile.log")
    top = "am2r_gpu_surface_address_tb"
    text, elapsed = run([tools["vsim"], "-c", "-l", output / "production-transcript.log", "-lib", library,
                         top, "-do", "run -all; quit -code 0"], output / "production.log", simulation=True)
    result = check_output(text)
    print("PASS production surface address:", result, flush=True)
    # Substitute the base without the required +1 in only the even-bank
    # alternative. Staging remains correct; the real address oracle must fail.
    source = original[0].decode()
    pattern = r"(wire\s+\[15:0\]\s+surface_issue_address_plus1\s*=\s*)surface_read_base_plus1\b"
    mutant, changed = re.subn(pattern, r"\1surface_read_base", source)
    if changed != 1:
        raise RuntimeError("Expected exactly one production plus-one address expression")
    (output / "mutated.sv").write_text(mutant, encoding="utf-8")
    run([tools["vlog"], "-sv", "-work", library, output / "mutated.sv"], output / "mutation-compile.log")
    text, mutation_elapsed = run([tools["vsim"], "-c", "-l", output / "mutation-transcript.log", "-lib", library,
                                  top, "-do", "run -all; quit -code 0"], output / "mutation.log", simulation=True)
    witness = check_output(text, mutation=True)
    print("PASS deliberate RTL fault rejected:", witness, flush=True)
    if any(path.read_bytes() != data for path, data in zip(paths, original)):
        raise RuntimeError("Production source or testbench changed during verification")
    report = {
        "source_sha256": {str(path.relative_to(ROOT)): hashlib.sha256(data).hexdigest()
                          for path, data in zip(paths, original)},
        "production_counts": {key: int(value) for key, value in re.findall(r"(\w+)=(\d+)", result)},
        "production_seconds": elapsed, "mutation_seconds": mutation_elapsed,
        "mutation_witness": witness, "parser_positive_fixtures": 2, "parser_negative_fixtures": 11,
        "scope": "Production GPU RTL setup/address/mask simulation only; not timing closure or hardware validation",
    }
    (output / "verification.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print("PASS surface-address regression:", output, flush=True)


if __name__ == "__main__":
    main()
