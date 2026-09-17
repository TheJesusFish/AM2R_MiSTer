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
        "(command->word[0] & 0x1ffu) == 2u",
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

    print(
        f"AM2R renderer audit passed: {len(fields)} vtable hooks accounted for; "
        "surface zero is reserved; opaque-prefix guards are present; only optional "
        "drawTile uses the documented fallback"
    )


if __name__ == "__main__":
    main()
