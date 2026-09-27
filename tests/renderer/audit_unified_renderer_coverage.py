#!/usr/bin/env python3
"""Inventory guard, not a claim of FPGA implementation or visual correctness.

Every RendererVtable hook has an explicit category in the unified-renderer
coverage document. Optional exact WAD14 input reports statically referenced
rendering builtins separately from similarly named game scripts. This script
reads game input only, never executes or modifies it.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import re
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools"))
from audit_am2r_function_surface import wad14_function_references  # noqa: E402


HOOK_GROUPS = {
    "lifecycle": "init destroy beginFrame endFrameInit endFrameEnd flush",
    "view": "beginView endView applyProjection beginGUI setGuiProjection endGUI setMatrix",
    "quad": "drawSprite drawSpritePart drawSpritePartColor drawSpritePos drawRectangle drawRectangleColor drawSurface drawSurfaceColor",
    "line-triangle": "drawLine drawLineColor drawTriangle",
    "text": "drawText drawTextColor",
    "clear": "clearScreen",
    "sprite-lifecycle": "createSpriteFromSurface deleteSprite",
    "blend": "gpuGetBlendFactors gpuGetBlendMode gpuSetBlendMode gpuSetBlendModeExt gpuSetBlendEnable gpuGetBlendEnable",
    "pixel-state": "gpuSetAlphaTestEnable gpuGetAlphaTestEnable gpuSetAlphaTestRef gpuSetColorWriteEnable gpuGetColorWriteEnable gpuSetFog",
    "tiling": "drawTile drawSpriteTiled drawSurfaceTiled drawTiledPart",
    "surface-lifecycle": "createSurface surfaceExists setRenderTarget ensureApplicationSurface getSurfaceWidth getSurfaceHeight surfaceResize surfaceFree",
    "copy-readback": "surfaceCopy surfaceGetPixels",
    "shader": "gpuSetShader gpuResetShader shaderGetUniform shaderGetSamplerIndex shaderSetUniformF shaderSetUniformFArray shaderSetUniformI shaderIsCompiled shadersSupported",
    "texture-metadata": "spriteGetTexture surfaceGetTexture textureGetTexelWidth textureGetTexelHeight textureGetUVs textureSetStage",
}

# These are CPU pixel-producing sites in the audited pre-unification baseline,
# not a prohibition on retaining a correctness reference or explicit readback.
# Keep the historical inventory even if a new renderer eliminates a site.
BASELINE_CPU_SITES = {
    "blendSubtractSolidSpan": "solid subtract RGBA",
    "blendSubtractTextureUnitSpan": "unit scaled texture subtract RGBA",
    "blendSubtractTextureSpan": "arbitrary X scale texture subtract RGBA",
    "blendPixel": "normal/add/subtract/min/max/reverse-subtract and pixel states",
    "swGpuPrepareAxisTexture": "nearest/clamped large dynamic surface staging",
    "swGpuQuarterTurnTexture": "immutable atlas transpose/resample cache creation",
    "swOffscreenReplay": "full deferred clear/subtract surface materialization",
    "rasterizeTriangle": "barycentric coverage, tint, texture sampling and blending",
    "rasterizeAxisAlignedQuad": "fills, four-corner gradient staging, generic quad sampling/blending",
    "SWRenderer_clearFrameBuffer": "host RGBA clear",
    "swClearScreen": "target RGBA clear fallback",
    "swSurfaceCopy": "overlap-safe temporary snapshot plus copy",
    "swSurfaceGetPixels": "CPU-visible readback/copy",
    "SWRenderer_fastStateRestoreSurface": "restore serialized RGBA contents",
}

RENDER_PREFIXES = (
    "draw_", "gpu_", "surface_", "sprite_", "shader_", "d3d_", "texture_",
    "matrix_", "application_", "background_", "tile_", "font_", "part_",
)


def audit_hooks(root: Path) -> dict:
    source_root = root / "third_party" / "Butterscotch" / "src"
    header = (source_root / "renderer.h").read_text(encoding="utf-8")
    source = (source_root / "sw_renderer.c").read_text(encoding="utf-8")
    first = header.index("// ===[ Renderer Vtable ]===")
    last = header.index("} RendererVtable;", first)
    hooks = set(re.findall(r"\(\*(\w+)\)\s*\(", header[first:last]))
    classified = [hook for group in HOOK_GROUPS.values() for hook in group.split()]
    duplicates = sorted({hook for hook in classified if classified.count(hook) != 1})
    missing = sorted(hooks - set(classified))
    obsolete = sorted(set(classified) - hooks)
    if duplicates or missing or obsolete:
        raise ValueError(f"coverage inventory stale: duplicate={duplicates}, new={missing}, removed={obsolete}")
    assignments = dict(re.findall(r"swVtable\.(\w+)\s*=\s*(\w+)", source))
    if hooks != set(assignments):
        raise ValueError("renderer vtable assignment inventory differs from header")
    nulls = sorted(hook for hook, impl in assignments.items() if impl == "nullptr")
    if nulls != ["drawTile"]:
        raise ValueError(f"re-audit optional renderer paths: {nulls}")
    doc = (root / "docs" / "unified-renderer-coverage.md").read_text(encoding="utf-8")
    for group in HOOK_GROUPS:
        if f"<!-- hook-group:{group} -->" not in doc:
            raise ValueError(f"coverage document is missing hook group {group}")
    return {
        "hook_count": len(hooks),
        "hook_groups": {group: names.split() for group, names in HOOK_GROUPS.items()},
        "assignments": assignments,
        "baseline_cpu_sites": BASELINE_CPU_SITES,
        "caution": "Inventory only; assigned hooks may be stubs and CPU paths may remain.",
    }


def static_game_inventory(root: Path, data_win: Path) -> dict:
    refs = wad14_function_references(data_win)
    source = (root / "third_party" / "Butterscotch" / "src" / "vm_builtins.c").read_text(encoding="utf-8")
    registered = set(re.findall(r'VM_registerBuiltin\(ctx,\s*"([^"]+)"', source))
    relevant = {name: count for name, count in sorted(refs.items())
                if name.startswith(RENDER_PREFIXES)}
    return {
        "data_sha256": hashlib.sha256(data_win.read_bytes()).hexdigest(),
        "all_function_count": len(refs),
        "registered_render_builtins": {name: count for name, count in relevant.items() if name in registered},
        "other_render_named_functions": {name: count for name, count in relevant.items() if name not in registered},
        "caution": "FUNC occurrence counts are static call references, not execution frequencies or proof of parameter values. Unregistered names can be game scripts, not missing builtins.",
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=ROOT)
    parser.add_argument("--data-win", type=Path)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    report = audit_hooks(args.root)
    if args.data_win:
        report["game"] = static_game_inventory(args.root, args.data_win)
    if args.json:
        print(json.dumps(report, indent=2))
    else:
        print(f"Coverage inventory: {report['hook_count']} hooks in {len(HOOK_GROUPS)} explicit groups; baseline has {len(BASELINE_CPU_SITES)} CPU pixel/copy sites.")
        print(report["caution"])
        if "game" in report:
            print(json.dumps(report["game"], indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
