#!/usr/bin/env python3
"""Native-GM ZERO/INV_SRC_COLOR reference and compiled CPU regressions.

AM2R 1.1 draw_set_blend_mode(3): 0x4202d0 -> 0x420250(1,4), D3D9
ZERO / INVSRCCOLOR. Compile actual helpers, not just a Python duplicate.
"""

from __future__ import annotations

import argparse
import os
from pathlib import Path
import random
import shutil
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parents[2]


def fast_divide_by_255(value: int) -> int:
    return (value + 1 + (value >> 8)) >> 8


def reference(destination: int, source: int, tint: int) -> int:
    return destination * (255 - source * tint // 255) // 255


def extract_baseline_spans(source: str) -> str:
    start = source.index("static void blendSubtractTextureSpan")
    if "static void blendSubtractTextureUnitSpan" in source:
        # Patch48 isolated exact unit steps in a guarded, non-inlined helper.
        # Preserve that helper too, rather than calling the candidate's copy.
        start = source.rfind("#if", 0, source.index("static void blendSubtractTextureUnitSpan"))
        assert start >= 0
    spans = source[start:source.index("static inline void blendPixel")]
    return spans.replace("blendSubtractTextureUnitSpan", "blendSubtractTextureUnitSpanBaseline").replace(
        "blendSubtractTextureSpan", "blendSubtractTextureSpanBaseline")


def compile_regression(arm_output: Path | None = None, baseline_source: Path | None = None) -> None:
    source = (ROOT / "third_party/Butterscotch/src/sw_renderer.c").read_text()
    helpers = source[source.index("static inline uint8_t clampByte"):
                     source.index("static inline void sampleTexture")]
    zig = ROOT / "third_party/zig/zig-x86_64-windows-0.16.0/zig.exe"
    compiler = [str(zig), "cc"] if zig.exists() else [shutil.which("cc") or "cc"]
    harness = Path(__file__).with_name("subtractive_blend_regression.c")
    with tempfile.TemporaryDirectory(prefix="am2r-blend-") as folder:
        directory = Path(folder)
        (directory / "sw_blend_under_test.inc").write_text(helpers)
        if baseline_source:
            (directory / "sw_blend_baseline.inc").write_text(extract_baseline_spans(baseline_source.read_text()))
        env = dict(os.environ)
        env["ZIG_GLOBAL_CACHE_DIR"] = str(ROOT / "data/build/zig-blend-tests-global")
        env["ZIG_LOCAL_CACHE_DIR"] = str(directory / "zig-local")
        for neon in (False, True):
            binary = directory / ("neon-model.exe" if neon else "scalar.exe")
            command = [*compiler, "-std=c23", "-O3", "-I", str(directory),
                       str(harness), "-o", str(binary)]
            if neon:
                command.append("-DTEST_NEON_MODEL=1")
            if baseline_source:
                command.append("-DTEST_BASELINE_BENCHMARK=1")
            if os.name != "nt":
                command.append("-lm")
            subprocess.run(command, check=True, env=env)
            subprocess.run([str(binary), "--regression-only"], check=True)
        if arm_output:
            if not zig.exists():
                raise RuntimeError("ARM standalone test currently requires the configured Zig toolchain")
            if not baseline_source:
                raise ValueError("--arm-output requires --baseline-source for the preserved pre-edit renderer")
            arm_output = arm_output.resolve()
            arm_output.parent.mkdir(parents=True, exist_ok=True)
            command = [str(zig), "cc", "-target", "arm-linux-musleabihf", "-mcpu=cortex_a9",
                       "-mfpu=neon", "-mfloat-abi=hard", "-std=c23", "-O3", "-static",
                       "-DUSE_MISTER=1", "-DTEST_BASELINE_BENCHMARK=1", "-I", str(directory),
                       str(harness), "-o", str(arm_output), "-lm"]
            subprocess.run(command, check=True, env=env)
            print(f"ARMv7 NEON differential/microbenchmark built, not executed: {arm_output}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--arm-output", type=Path, help="Also build a static Cortex-A9 NEON differential/microbenchmark executable")
    parser.add_argument("--baseline-source", type=Path, help="Preserved pre-edit sw_renderer.c for host differential tests and the ARM A/B benchmark")
    arguments = parser.parse_args()
    for value in range(255 * 255 + 1):
        assert fast_divide_by_255(value) == value // 255

    assert reference(60, 254, 128) == 30  # Native room159, not black.
    generator = random.Random(0xA2_4D_52)
    for _ in range(250_000):
        destination, source, tint = (generator.randrange(256) for _ in range(3))
        assert fast_divide_by_255(destination * (255 - fast_divide_by_255(source * tint))) == reference(destination, source, tint)
    gl = (ROOT / "third_party/Butterscotch/src/gl_common/gl_common.c").read_text()
    for mapping in ("case bm_subtract:         return GL_FUNC_ADD;",
                    "case bm_subtract:         return GL_ZERO;",
                    "case bm_subtract:         return GL_ONE_MINUS_SRC_COLOR;"):
        assert mapping in gl, mapping
    compile_regression(arguments.arm_output, arguments.baseline_source)

    print("Native GM inverse-source blend passed: exhaustive /255, 250000 reference channels, compiled scalar and NEON-lane-model CPU paths")


if __name__ == "__main__":
    main()
