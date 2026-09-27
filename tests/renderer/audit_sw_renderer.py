#!/usr/bin/env python3
"""Static completeness audit for the MiSTer software/FPGA renderer bridge."""

from __future__ import annotations

import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
RENDERER_H = ROOT / "third_party" / "Butterscotch" / "src" / "renderer.h"
SW_RENDERER_C = ROOT / "third_party" / "Butterscotch" / "src" / "sw_renderer.c"
MISTER_BACKEND_C = (
    ROOT / "third_party" / "Butterscotch" / "src" / "backends" / "mister.c"
)


def require(source: str, marker: str) -> None:
    if marker not in source:
        raise SystemExit(f"missing renderer contract: {marker}")


def forbid(source: str, marker: str) -> None:
    if marker in source:
        raise SystemExit(f"forbidden renderer contract: {marker}")


def main() -> None:
    header = RENDERER_H.read_text(encoding="utf-8")
    source = SW_RENDERER_C.read_text(encoding="utf-8")
    mister_source = MISTER_BACKEND_C.read_text(encoding="utf-8")

    marker = "// ===[ Renderer Vtable ]==="
    start = header.index(marker)
    end = header.index("} RendererVtable;", start)
    fields = set(re.findall(r"\(\*(\w+)\)\s*\(", header[start:end]))
    assigned = set(re.findall(r"swVtable\.(\w+)\s*=", source))
    missing = sorted(fields - assigned)
    extra = sorted(assigned - fields)
    if missing or extra:
        raise SystemExit(f"vtable mismatch: missing={missing}, extra={extra}")

    null_fields = set(re.findall(r"swVtable\.(\w+)\s*=\s*nullptr", source))
    if null_fields != {"drawTile"}:
        raise SystemExit(f"unexpected null renderer hooks: {sorted(null_fields)}")

    # drawTile is deliberately optional in renderer.h and routes through the
    # normal sprite-part fallback. Every concrete primitive/state/surface hook
    # must otherwise exist, and CPU-visible writes must terminate or reconcile
    # an in-flight FPGA frame instead of silently diverging.
    for contract in (
        'swGpuFallbackIfOutput(sw, "non-axis quad")',
        'swGpuFallbackIfOutput(sw, "textured blit not recordable")',
        'swGpuFallbackIfOutput(sw, "unsupported axis quad state")',
        'swGpuFallbackIfOutput(sw, "triangle primitive")',
        'swGpuFallbackIfOutput(sw, "clear target/state")',
        'swGpuFallback(sw, "application surface copy")',
        'swGpuFallback(sw, "application surface readback")',
        "sw->base.currentShader >= 0",
        "sw->gpuFallbackReasons[reasonIndex]++",
        "swShadersSupported(void){return false;}",
        "swDrawSpritePartColor",
        "MisterGpu_addFillVGradient",
    ):
        require(source, contract)

    # Prefix elimination is legal only after a later command group is proven
    # to overwrite all 320x240 pixels. Keep every conservative guard visible
    # in the production backend: ordinary alpha blending, unit axis steps,
    # opaque tint, no gradient, valid CPU shadow, and exact bit coverage.
    for contract in (
        "static uint32_t cullFullyCoveredPrefix(void)",
        "(command->word[0] & 0x3ffu) == 2u",
        "(uint32_t)command->word[5] == 0x00010000u",
        "(uint32_t)(command->word[5] >> 32) == 0x00010000u",
        "(uint8_t)(command->word[6] >> 24) == 255u",
        "command->word[7] == 0",
        "record->shadow == NULL || !record->shadow_valid",
        "if (coverageIsFull())",
        "opaque = axisCommandSourceIsOpaque(&g_gpu_commands[i]);",
        "for (uint32_t i = groupEnd; i > groupStart; --i)",
        "bestPrefix = i - 1u;",
        "tryFuseMapBackground();\n        cullFullyCoveredPrefix();",
        "uint32_t waitBaseline = current;",
        "if (current != waitBaseline)",
        "if (++g_vblank_timeout_count >= 3)",
        "MiSTer pacing: transient FPGA heartbeat timeout",
    ):
        require(mister_source, contract)

    # AM2R's water rows consume an application-surface export produced earlier
    # in the same command list. The completed native buffer is the prior game
    # frame, so it must never replace that export merely because water is the
    # only reader. Keep the optimized batched rows but clear their HPS-only
    # recognition bit before submission as ordinary opcode 7 commands.
    for contract in (
        "static void finalizeWaterExports(void)",
        "g_gpu_commands[i].word[0] &= ~GPU_WATER_NATIVE_ALIAS_FLAG;",
        "uint32_t waterRows = tryFuseWaterEffect();\n"
        "        uint32_t nativeWaterRows = 0;\n"
        "        finalizeWaterExports();",
    ):
        require(mister_source, contract)
    forbid(mister_source, "aliasExclusiveWaterExports")

    # Sampling application_surface into a user-created surface is not the
    # normal host-framebuffer handoff.  AM2R uses this exact route to freeze
    # the gameplay image behind item-acquisition messages.  The CPU shadow is
    # stale while the FPGA owns the frame, so this path must force a readback
    # before the generic software blit consumes surfacePixels[app].
    app_snapshot_contract = re.compile(
        r"if \(id == renderer->runner->applicationSurfaceId &&\s*"
        r"sw->currentSurface != renderer->runner->applicationSurfaceId &&\s*"
        r"sw->currentSurface != RENDER_TARGET_HOST_FRAMEBUFFER &&\s*"
        r"sw->gpuFrameEligible && sw->gpuHasSkippedDraws\) \{\s*"
        r"swGpuFallbackForApplicationSurfaceSnapshot\(sw\);\s*\}",
        re.MULTILINE,
    )
    if not app_snapshot_contract.search(source):
        raise SystemExit(
            "missing renderer contract: off-screen application-surface snapshot readback"
        )
    for contract in (
        "static void swGpuFallbackForApplicationSurfaceSnapshot",
        "MisterGpu_readbackLastPresentedRgba(destination, 320, 240)",
        "MisterGpu_useSoftwareFrame();",
        "sw->gpuFallbackReasons[5]++;",
        "sw->gpuFrameEligible = false;",
    ):
        require(source, contract)

    # GameMaker surface handle zero is an invalid sentinel. AM2R relies on
    # surface_exists(0) being false before it creates the frozen 512x256 image
    # used behind item-acquisition messages.
    for contract in (
        "if (sw->surfaceCount == 0)",
        "swEnsureSurfaceCapacity(sw, 1);",
        "sw->surfaceCount = 1;",
        "for (id = 1; id < sw->surfaceCount; ++id)",
    ):
        require(source, contract)

    # The AM2R light engine draws a 320x240 viewport from a 512x256 backing
    # surface. The unused power-of-two padding must not return to the hot path:
    # cropped sparse shadows preserve the exact source stride while keeping the
    # FPGA allocation and row scans limited to visible pixels.
    for contract in (
        "MisterGpu_uploadSparseDynamicTextureCropRevision(",
        "gpuStride = 320u * 4u;",
        "texPixels, (size_t)texW * 4u, gpuStride, 240u,",
        "static void copyCroppedTextureRows",
        "reuseReleasedCroppedTexture(",
        "record->bytes != bytes || !record->sparse",
        "copyCroppedTextureSplitSpan(targetPixels + row * cropRowBytes,",
    ):
        require(source if "MisterGpu_" in contract or "gpuStride" in contract or
                "texPixels" in contract else mister_source, contract)

    # The remaining AM2R lighting work is the subtractive CPU mask itself.
    # Preserve its exact two-stage integer modulation while allowing contiguous
    # light-sprite spans to use the Cortex-A9 NEON lanes. A rotated zero-scale
    # missile-hit sprite covers no samples and must not force a GPU readback.
    for contract in (
        "#include <arm_neon.h>",
        "static inline uint8x8_t divideBy255U16",
        "static void blendSubtractTextureSpan",
        "target.val[0] = divideBy255U16(vmull_u8(target.val[0], vmvn_u8(red)));",
        "target.val[3] = divideBy255U16(vmull_u8(target.val[3], vmvn_u8(alpha)));",
        "target[0] = divideBy255U32(target[0] * (255u - red));",
        "target[3] = divideBy255U32(target[3] * (255u - alpha));",
        "(sw->blendMode == bm_normal || sw->blendMode == bm_add)) return;",
        "case bm_complex: {",
        "blendFactorByte(sf,source,dst,channel)",
        "static void blendSubtractSolidSpan",
        "blendSubtractSolidSpan(row,width,tintR,tintG,tintB,tintA);",
        "target.val[0] = divideBy255U16(vmull_u8(target.val[0], factorR));",
        "target.val[3] = divideBy255U16(vmull_u8(target.val[3], factorA));",
        "target[3] = divideBy255U32(target[3] * inverseA);",
        "const uint8_t inverseA = 255u - sourceA;",
        "sw->blendEnable && sw->blendMode == bm_subtract && normalState",
        "blendSubtractTextureSpan(destination, source, texW,",
        "fabsf(edgeX0 * edgeY1 - edgeY0 * edgeX1) < 0.000001f",
    ):
        require(source, contract)

    print(
        f"AM2R renderer audit passed: {len(fields)} vtable hooks accounted for; "
        "surface zero is reserved; blend-safe opaque-prefix, same-frame water, and "
        "cropped/vectorized subtractive-lighting contracts are present; zero-area "
        "effects are no-ops; only optional drawTile uses the documented fallback"
    )


if __name__ == "__main__":
    main()
