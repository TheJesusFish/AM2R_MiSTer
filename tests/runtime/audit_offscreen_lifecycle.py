#!/usr/bin/env python3
"""Static barrier audit for deferred offscreen surfaces.

These checks establish hook placement, not pixel correctness, GPU ordering,
or complete runtime coverage. Pair them with the journal differential tests,
backend transaction tests, and hardware/save-state regressions.
"""

from __future__ import annotations

import argparse
import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
DEFAULT_SOURCE = ROOT / "third_party" / "butterscotch" / "src"


def mask_comments_and_literals(source: str) -> str:
    """Keep offsets/newlines while excluding comments and string contents."""
    tokens = re.compile(r'//[^\n]*|/\*.*?\*/|"(?:\\.|[^"\\])*"|\'(?:\\.|[^\'\\])*\'', re.S)
    return tokens.sub(lambda match: "".join("\n" if c == "\n" else " "
                                            for c in match.group()), source)


def function_body(source: str, name: str) -> str:
    masked = mask_comments_and_literals(source)
    signature = re.search(
        rf"^[\w*][\w\s*]*\b{re.escape(name)}\s*\([^;{{}}]*\)\s*\{{",
        masked, re.M,
    )
    if signature is None:
        raise AssertionError(f"missing function definition: {name}")
    start = signature.end()
    depth = 1
    for pos in range(start, len(masked)):
        if masked[pos] == "{":
            depth += 1
        elif masked[pos] == "}":
            depth -= 1
            if depth == 0:
                return masked[start:pos]
    raise AssertionError(f"unclosed function definition: {name}")


def require_call(body: str, name: str, arguments: str, label: str) -> int:
    # Ignore formatting but require the actual call, not an identifier in a
    # comment or similarly named helper.
    compact = compact_code(body)
    compact_arguments = compact_code(arguments)
    call = f"{name}({compact_arguments})"
    position = compact.find(call)
    if position < 0:
        raise AssertionError(f"{label}: missing {call}")
    return position


def compact_code(source: str) -> str:
    # Surface IDs are uint32_t in loops and int32_t at the renderer boundary;
    # an explicit cast there is equivalent wiring, not a missing barrier.
    source = re.sub(r"\(\s*(?:u?int32_t)\s*\)", "", source)
    return re.sub(r"\s+", "", source)


def require_before(body: str, call: str, arguments: str,
                   following: str, label: str) -> None:
    first = require_call(body, call, arguments, label)
    compact = compact_code(body)
    after = compact.find(compact_code(following))
    if after < 0:
        raise AssertionError(f"{label}: missing following operation {following}")
    if first >= after:
        raise AssertionError(f"{label}: materialization must precede {following}")


def audit(source_dir: Path) -> list[str]:
    sw = (source_dir / "sw_renderer.c").read_text(encoding="utf-8")
    state = (source_dir / "am2r_fast_state.c").read_text(encoding="utf-8")
    header = (source_dir / "sw_renderer.h").read_text(encoding="utf-8")
    unified = (source_dir / "sw_unified_renderer.h").read_text(encoding="utf-8")
    checked: list[str] = []

    def body(name: str) -> str:
        return function_body(sw, name)

    assert re.search(r"void\s+SWRenderer_materializeSurfaces\s*\(\s*Renderer\s*\*",
                     mask_comments_and_literals(header)), "missing public save barrier"
    save = function_body(state, "writeSurfaces")
    require_before(save, "SWRenderer_materializeSurfaces", "runner->renderer",
                   "writerU32(writer, sw->surfaceCount)", "logical save")
    require_call(body("SWRenderer_materializeSurfaces"),
                 "swOffscreenMaterialize", "sw, id", "materialize all surfaces")
    checked.append("logical save materializes before metadata/pixels")

    axis = body("rasterizeAxisAlignedQuad")
    require_call(axis, "swOffscreenMaterializeSource", "sw, texPixels", "axis source")
    require_call(axis, "swOffscreenMaterialize", "sw, sw->currentSurface", "axis destination")
    triangle = body("rasterizeTriangle")
    require_before(triangle, "swOffscreenMaterializeSource", "sw, texPixels",
                   "sampleTexture(", "triangle source")
    require_before(triangle, "swOffscreenMaterialize", "sw, sw->currentSurface",
                   "sw->framebuffer +", "triangle destination")
    require_before(body("swGpuTryAffineQuad"), "swOffscreenMaterializeSource", "sw, texPixels",
                   "swGpuTextureAddress(", "affine source")
    checked.append("axis, direct triangle, and affine paths have pixel barriers")

    copy = body("swSurfaceCopy")
    require_before(copy, "swOffscreenMaterialize", "sw, srcId",
                   "uint8_t* temp", "copy source")
    require_before(copy, "swOffscreenMaterialize", "sw, dstId",
                   "uint8_t* temp", "partial copy destination")
    require_before(body("swSurfaceGetPixels"), "swOffscreenMaterialize", "sw, id",
                   "memcpy(outRGBA", "CPU pixel readback")
    require_before(body("SWRenderer_misterDrawAm2rWater"), "swOffscreenMaterialize", "sw, surfaceId",
                   "MisterGpu_uploadDynamicTextureRevision(", "water source")
    checked.append("copy/readback/water bypasses materialize")

    for function, id_name in (
        ("swCreateSurface", "id"),
        ("swEnsureApplicationSurface", "id"),
        ("swSurfaceResize", "id"),
        ("swSurfaceFree", "id"),
        ("SWRenderer_fastStateResetSurfaces", "id"),
        ("SWRenderer_fastStateRestoreSurface", "id"),
    ):
        require_call(body(function), "swOffscreenDiscard", f"sw, {id_name}", function)
    checked.append("allocation, free, resize, and logical restore discard stale journals")

    target = body("swUseSurfaceTarget")
    if "swOffscreenMaterialize" in target:
        raise AssertionError("target selection must not replay the preceding frame's journal")
    frame = compact_code(body("swBeginFrame"))
    if frame.index("MisterGpu_beginFrame()") >= frame.index("sw->gpuSawClear=false"):
        raise AssertionError("beginFrame must open the backend before resetting clear state")
    clear = body("swClearScreen")
    # The new unified clear is a different owner and legitimately precedes
    # this journal. Audit the preserved legacy branch, not the first lexical
    # occurrence of an unrelated owner's command.
    legacy_clear = clear[clear.index("swOffscreenClear("):]
    require_before(legacy_clear, "swOffscreenEmitPending", "sw", "MisterGpu_addClear(",
                   "first application clear")
    if "sw->gpuFrameEligible&&!sw->gpuSawClear" not in compact_code(clear):
        raise AssertionError("prepass insertion must be gated to the first eligible application clear")
    checked.append("Step journal survives to first application clear; target selection stays lazy")

    def bridge(name: str) -> str:
        return function_body(unified, name)

    startup = compact_code(body("swInit"))
    if "MisterGpu_hasSurfaceTargets()&&MisterGpu_hasGenericPrimitive()&&MisterGpu_setUnifiedEnabled(true)" not in startup:
        raise AssertionError("complete unified renderer must require both target and generic primitive capabilities")
    require_before(bridge("swUnifiedTryQuad"), "swUnifiedAffineUvSupported",
                   "v,source,texW,texH", "swGpuTryAffineQuad(", "unified affine texture bounds")
    require_before(body("swGpuTryAffineQuad"), "swUnifiedAffineFixedInputs",
                   "values,sizeof(values)/sizeof(values[0])", "llround(", "unified affine conversion range")
    for function in ("swUnifiedTryQuad", "swUnifiedTryGenericQuad", "swUnifiedBuildAxisPacket"):
        require_call(bridge(function), "swUnifiedAxisGeometry", "v", function + " exact axis geometry")
        require_call(bridge(function), "swUnifiedSeparableUv", "v", function + " separable axis UV")
    fast_quad = compact_code(bridge("swUnifiedTryQuad"))
    if fast_quad.index("!MisterGpu_hasFloorTint()") > fast_quad.index("MisterOffscreen_planAxisTint("):
        raise AssertionError("unified axis must reject missing floor-tint capability before planning/publication")

    require_before(clear, "swUnifiedSelect", "sw", "MisterGpu_addClear(offscreenRgba)",
                   "unified clear selects its actual target")
    # String literals are masked by the parser: inspect calls through their
    # first arguments and check the full data-flow operations independently.
    if "swUnifiedCheck(sw,MisterGpu_addClear(offscreenRgba)," not in compact_code(clear):
        raise AssertionError("unified clear submission must be checked")
    require_call(bridge("swUnifiedSelect"), "swUnifiedHandle", "sw, sw->currentSurface",
                 "unified target ownership")
    if "MisterGpu_surfaceSelect(handle)" not in compact_code(bridge("swUnifiedSelect")):
        raise AssertionError("unified target selection must reach the backend owner")
    require_call(bridge("swUnifiedCpuBegin"), "swUnifiedReadTarget", "sw, sourceId",
                 "fallback source barrier")
    require_call(bridge("swUnifiedCpuBegin"), "swUnifiedReadTarget", "sw, sw->currentSurface",
                 "fallback destination barrier")
    require_call(bridge("swUnifiedReadTarget"), "MisterGpu_surfaceReadback",
                 "handle, pixels, (size_t)width * 4u", "unified fenced readback")
    require_call(bridge("swUnifiedCpuEnd"), "MisterGpu_surfaceCpuWritten",
                 "handle, sw->framebuffer, (size_t)sw->fbWidth * 4u, revision",
                 "fallback publication")
    for function, recursive_args in (
        ("rasterizeQuad", "sw, v, texPixels, texW, texH"),
        ("rasterizeTriangle", "sw, v0, v1, v2, texPixels, texW, texH"),
    ):
        raster = body(function)
        require_before(raster, function, recursive_args, "swUnifiedCpuEnd(sw)",
                       f"{function} fallback publication")
        compact = compact_code(raster)
        if not (compact.index("swUnifiedCpuBegin(") < compact.index("++sw->unifiedCpuDepth") <
                compact.index(function + "(" + compact_code(recursive_args) + ")") <
                compact.index("--sw->unifiedCpuDepth") < compact.index("swUnifiedCpuEnd(sw)")):
            raise AssertionError(f"{function}: fallback must fence, recurse under guard, then publish")
    materialize = body("SWRenderer_materializeSurfaces")
    require_call(materialize, "swUnifiedReadTarget", "sw, RENDER_TARGET_HOST_FRAMEBUFFER",
                 "unified save host")
    require_call(materialize, "swUnifiedReadTarget", "sw, id", "unified save surfaces")
    for function in ("swCreateSurface", "swEnsureApplicationSurface", "swSurfaceResize", "swSurfaceFree"):
        require_before(body(function), "swUnifiedRelease", "sw, id", "free(sw->surfacePixels[id])",
                       f"{function} GPU lifetime")
    reset = body("SWRenderer_fastStateResetSurfaces")
    require_before(reset, "MisterGpu_surfaceReset", "", "free(sw->surfacePixels[id])",
                   "restore GPU invalidation")
    require_call(body("SWRenderer_fastStateRestoreSurface"), "swUnifiedRelease", "sw, id",
                 "restore slot handle invalidation")
    checked.append("unified targets, fenced fallback/publication, save materialization and lifecycle are wired separately")
    return checked


def parser_self_test() -> None:
    sample = '''
// pretend(void) { bad(); }
static void actual(int n) {
    /* } fake_call(); */
    const char *s = "} fake_call();";
    if (n) { real_call(n); }
}
'''
    extracted = function_body(sample, "actual")
    assert "fake_call" not in extracted
    require_call(extracted, "real_call", "n", "parser self-test")
    try:
        function_body(sample, "pretend")
    except AssertionError:
        pass
    else:
        raise AssertionError("parser accepted a comment as a function")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-dir", type=Path, default=DEFAULT_SOURCE)
    parser.add_argument("--self-test-only", action="store_true")
    args = parser.parse_args()
    parser_self_test()
    if args.self_test_only:
        print("Offscreen lifecycle audit parser self-test passed")
        return
    checks = audit(args.source_dir)
    for check in checks:
        print(f"PASS: {check}")
    print("Offscreen lifecycle wiring audit passed; this is not pixel or hardware proof")


if __name__ == "__main__":
    main()
