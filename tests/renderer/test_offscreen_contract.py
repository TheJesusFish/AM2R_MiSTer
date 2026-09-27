#!/usr/bin/env python3
"""Compile actual offscreen CPU replay / conservative existing-RBF planner."""

from __future__ import annotations

import os
from pathlib import Path
import shutil
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parents[2]
SOURCE = ROOT / "third_party/Butterscotch/src"


def main() -> None:
    zig = ROOT / "third_party/zig/zig-x86_64-windows-0.16.0/zig.exe"
    compiler = [str(zig), "cc"] if zig.exists() else [shutil.which("cc") or "cc"]
    with tempfile.TemporaryDirectory(prefix="am2r-offscreen-") as folder:
        directory = Path(folder)
        environment = dict(os.environ)
        environment["ZIG_GLOBAL_CACHE_DIR"] = str(ROOT / "data/build/zig-offscreen-tests-global")
        environment["ZIG_LOCAL_CACHE_DIR"] = str(directory / "zig-local")
        binary = directory / ("offscreen.exe" if os.name == "nt" else "offscreen")
        command = [*compiler, "-std=c23", "-O3", "-Wall", "-Wextra", "-Werror",
                   "-I", str(SOURCE), str(SOURCE / "mister_offscreen.c"),
                   str(Path(__file__).with_name("offscreen_contract_regression.c")),
                   "-o", str(binary)]
        if os.name != "nt":
            command.append("-lm")
        subprocess.run(command, check=True, env=environment)
        subprocess.run([str(binary)], check=True)


if __name__ == "__main__":
    main()
