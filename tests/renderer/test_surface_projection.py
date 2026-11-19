#!/usr/bin/env python3
"""Exercise actual SW/Runner surface projection helpers without GPU or game data."""
import hashlib
import os
from pathlib import Path
import subprocess
import tempfile

from test_unified_bridge import extract

ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "third_party/Butterscotch/src"


def main():
    sw = (SRC / "sw_renderer.c").read_text()
    runner = (SRC / "runner.c").read_text()
    names = (
        "swMarkSurfaceWrite", "swUseSurfaceTarget", "swSetViewTransform",
        "swBeginView", "worldToScreen", "swSetRenderTarget", "swBeginGUI",
        "swSetGuiProjection", "SWRenderer_fastStateResetSurfaces",
    )
    runner_names = (
        "findFreeStackSlot", "findStackTop", "Runner_surfaceSetTarget",
        "Runner_surfaceResetTarget",
    )
    actual = "\n\n".join(
        [extract(sw, name) for name in names]
        + [extract(runner, name) for name in runner_names]
    )
    # beginFrame has extensive unrelated framebuffer/backend dependencies.
    # Its invalidation is also checked structurally; the actual fast-reset
    # helper is executed below, not replaced with a test implementation.
    assert "sw->applicationViewValid = false;" in extract(sw, "swBeginFrame")
    assert "swBeginView(" not in extract(sw, "swSetRenderTarget")
    assert "swBeginView(" not in extract(sw, "swBeginGUI")
    assert "swBeginView(" not in extract(sw, "swSetGuiProjection")

    # End Step draws occur before the normal room beginView, while target
    # switches inside Draw occur after it. Neither ordering may be conflated.
    loop = (SRC / "loop.c").read_text()
    assert loop.index("Runner_step(runner);") < loop.index("Runner_drawPre(runner, fbWidth, fbHeight);")
    assert loop.index("Runner_drawPre(runner, fbWidth, fbHeight);") < loop.index("Runner_beginFrame(runner, gameW, gameH, winW, winH, fbWidth, fbHeight);")
    assert loop.index("Runner_beginFrame(runner, gameW, gameH, winW, winH, fbWidth, fbHeight);") < loop.index("Runner_drawViews(runner, gameW, gameH, debugShowCollisionMasks);")
    draw = extract(runner, "Runner_drawViews")
    view = draw.index("renderer->vtable->beginView(renderer, viewX, viewY, viewW, viewH, portX, portY, portW, portH, viewAngle);")
    assert draw.index("Runner_draw(runner);", view) > view
    step = extract(runner, "Runner_step")
    assert step.index("Runner_executeEventForAll(runner, EVENT_STEP, STEP_END);") < step.index("updateViews(runner);")

    with tempfile.TemporaryDirectory(prefix="am2r-surface-projection-") as directory:
        temp = Path(directory)
        (temp / "surface_projection_actual.inc").write_text(actual)
        binary = temp / ("projection.exe" if os.name == "nt" else "projection")
        env = dict(os.environ)
        env["ZIG_GLOBAL_CACHE_DIR"] = str(ROOT / "data/build/zig-global-cache")
        env["ZIG_LOCAL_CACHE_DIR"] = str(temp / "zig-cache")
        compile_command = [
            str(ROOT / "third_party/zig/zig-x86_64-windows-0.16.0/zig.exe"),
            "cc", "-std=gnu99", "-O2", "-Wall", "-Wextra", "-Werror",
            "-Wno-unused-function", "-DENABLE_WAD14", "-I", str(SRC), "-I", str(temp),
            str(Path(__file__).with_name("surface_projection_regression.c")),
            "-o", str(binary),
        ]
        subprocess.run(compile_command, env=env, check=True)
        subprocess.run([str(binary)], check=True)
        # Restore the old zero-origin behavior in an isolated copy: every
        # implicit reset now takes the local-view branch. Compilation must
        # succeed, and the same actual-helper fixture must then reject it.
        marker = "if (implicitApplicationSurface &&"
        assert actual.count(marker) == 1
        mutant = actual.replace(marker, "if (false && implicitApplicationSurface &&", 1)
        (temp / "surface_projection_actual.inc").write_text(mutant)
        subprocess.run(compile_command, env=env, check=True)
        rejected = subprocess.run([str(binary)], capture_output=True, text=True)
        assert rejected.returncode != 0, "Old zero-origin reset escaped the regression"
        assert "memcmp(matrix,&sw->worldToScreen,sizeof(*matrix))==0" in rejected.stderr, rejected.stderr
        print("Old zero-origin reset mutation rejected at exact room-matrix comparison")
    print("Surface projection runtime ordering and invalidation checks PASS")
    print("Extracted source SHA256", hashlib.sha256(actual.encode()).hexdigest())


if __name__ == "__main__":
    main()
