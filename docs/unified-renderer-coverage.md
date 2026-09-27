# Unified FPGA renderer: coverage and acceptance inventory

The matrices below preserve the **pre-implementation gap inventory**, not
evidence that a replacement renderer passes these cases. The final section
records the implementation/test status and current startup policy separately. The baseline audit
describes the software/FPGA bridge shipped on
September 25, 2026 (runtime `d472d6c1`, RBF `8ce9fa07`) and is the checklist for
the `unified-fpga-renderer` work. Keep the software implementation as a reference
and explicit temporary fallback; do not confuse a non-null renderer hook with
an implemented or accelerated operation.

## Scope and evidence

The desired boundary is: ARM runs GameMaker logic, command setup, text layout,
resource management and explicit CPU readback; FPGA performs normal pixel
coverage, sampling, tinting, blending, clearing and render-target copying.
Decoding immutable PNG assets once and transferring save-state bytes are not
per-frame software rendering. CPU gradient-image generation, nearest-neighbor
resampling into a staging image, and software drawing into an offscreen surface
**are** software rendering and must not disappear from the accounting merely
because their output later becomes an FPGA texture.

Audited sources are `third_party/Butterscotch/src/{renderer.h,sw_renderer.h,
sw_renderer.c,vm_builtins.c}`, `src/backends/{mister.c,mister_gpu.h}`, and
`rtl/am2r_gpu.sv`. These checkout paths are relative to the project except
`src/backends`, which is inside the same Butterscotch checkout. The exact game
input is AM2R 1.1 WAD14, SHA256
`36e4a251d7b687f2d742a8e911cb1e1185aea99e36529fcf32cd18d445a355e3`.
Its `FUNC` table contains 520 names, including game scripts and builtins.

Reproduce the public, read-only inventory:

```text
python tests/renderer/audit_unified_renderer_coverage.py
python tests/renderer/audit_unified_renderer_coverage.py --data-win data/inputs/windows/data.win --json
```

This guard classifies every vtable hook, rejects newly unclassified hooks, and
optionally lists exact static game references. It deliberately does not declare
GPU coverage from function names. Static reference counts are **call sites**, not
execution counts, reachability proof or parameter-range proof. Absence from
`FUNC` does not exclude an operation called internally by the engine.

### Exact compiled-game surface

| Family | Direct builtin references in AM2R 1.1 | What this establishes |
| --- | --- | --- |
| Sprite and backgrounds | `draw_sprite` 192, `draw_sprite_ext` 575, `draw_sprite_part` 1, `draw_sprite_part_ext` 2, `draw_self` 17; background draw/ext/part/part_ext/tiled/tiled_ext 40/116/8/2/2/5 | Axis, rotation, tint, crop and tiling must work on any valid target; argument values still require tracing/fixtures. |
| Primitives | `draw_rectangle` 68, `draw_rectangle_color` 7, `draw_line` 43, `draw_point` 2, `draw_circle_color` 4 | Circles lower to lines or filled triangles. No direct `draw_triangle` reference is needed to exercise triangle coverage. |
| Text | `draw_text` 149, `draw_text_ext` 6, `draw_text_color` 2, `font_add_sprite` 3 | Glyph atlas and sprite-font paths, multicolour glyphs, transformed text and offscreen HUD text belong in scope. |
| Surfaces | create 22, exists 36, target/reset 16 each, free 14, resize 1, copy 2, save 1; draw/ext/part/part_ext 4/30/1/1 | Full-size persistent surfaces and same-frame dependencies are real game behavior, not speculative APIs. |
| Blend/clear | `draw_set_blend_mode` 259, `draw_set_blend_mode_ext` 1, clear/clear_alpha 9/5 | Named modes plus at least one custom-factor mode must be correct. |
| Shader API | `shader_get_uniform` 15; no direct shader bind/reset/uniform-write reference | Uniform lookup alone is not evidence of an active shader. The baseline advertises shaders unsupported. |
| Additional hooks | No direct sprite-from-surface, fog, alpha-test, channel-mask, explicit GPU blend-enable, general surface-color/tiled, matrix-set references | API possibility rather than observed direct AM2R use. Keep correct explicit fallback or implement, but do not silently ignore. |

Existing local disassembly in
`data/work/crt-ui-analysis/am2r-disassembly.txt` identifies the custom blend in
`gml_Object_oFXTrail_Draw_0`: the `white == 1` branch uses factors `(2,2)`
(`bm_one, bm_one`) before a tinted/rotated sprite. This requires saturated
source-plus-destination **without multiplying RGB by source alpha**, and is not
the current `bm_add` equation. This is a correctness gap in the old CPU
reference as well as the GPU path. The four circle-color calls occur in the
mobile-control GUI; their existence does not prove that GUI is active on
MiSTer. Static inventory counts are freshly verified against the above data
hash; the older disassembly is supporting caller evidence, not a new execution
trace.

### Named blend compatibility limitations

The current migration preserves the regression-tested named-mode pixel contract;
**passing model/CPU/RTL comparisons is not proof of native GameMaker blend
semantics**. The tests intentionally retain historical normal/add destination
alpha and channelwise `bm_max`. Those require a separate native compatibility
decision, not an unannounced ownership-migration change:

| Mode | Preserved byte-domain behavior | Documentation discrepancy |
| --- | --- | --- |
| Normal | RGB uses source-alpha/inverse-source-alpha; alpha is `sa + da*(255-sa)/255`. | Applying the same factors to alpha instead gives `(sa*sa + da*(255-sa))/255`. |
| Add | RGB uses source-alpha/one; alpha saturates `sa + da`. | Applying the same factors to alpha instead adds `sa*sa/255`, not `sa`. |
| Max | Componentwise maximum of source/destination. | Named GameMaker `bm_max` is source-alpha/inverse-source-colour addition, not a max equation. |

The [YoYo Games GM:S 1.4 named-mode documentation, mirrored from its shipped
manual](https://www.hitscan.org/dadiospice/002_reference/drawing/colour%20and%20blending/draw_set_blend_mode.html)
lists these factor pairs. Its [extended-mode factor table](https://www.hitscan.org/dadiospice/002_reference/drawing/colour%20and%20blending/draw_set_blend_mode_ext.html)
describes factors over all RGBA channels. The current official
[surface-blending guide](https://manual.gamemaker.io/lts/en/Additional_Information/Guide_To_Using_Blendmodes.htm)
explicitly illustrates squared source-alpha contribution to an initially
transparent surface. The alternate upstream OpenGL backend also uses one pair
of factors for RGBA, but is not the proprietary Windows AM2R runner. Neither
that backend nor a WAD14 format label establishes exact native AM2R behavior.
The freshly read raw GEN8 version fields are `1.0.0.1474`, so do not infer
an executable's exact graphics implementation from the phrase "GM1.4".

Fresh read-only raw `FUNC`/`VARI` reference-chain and `CODE` instruction-boundary
inventory can be reproduced without extracting game code:

```text
python tests/renderer/audit_am2r_blend_calls.py data/inputs/windows/data.win
```

All 259 named call sites resolve to 128 literal normal (`0`), 124 literal add
(`1`), six literal subtract (`3`), and one direct `blendmode` variable in
`oStageRectangle_Draw_0`. Its only direct bytecode assignment is literal add
(`1`) in `rm_a1b02_3749_Create`. No literal max/min/reverse-subtract call sites
are present. This does not exclude indirect variable writes or prove runtime
frequency/reachability. The six subtract callers are `oLiquidFilter`, `oFXTrail`,
`oFlashLight`, `oFlashLight2`, and two `oLightEngine` events. The sole extended
call is exactly ONE/ONE in `oFXTrail`.

Alpha is not irrelevant just because max is unobserved: all five authored
`draw_clear_alpha` calls explicitly clear to alpha zero. In the supporting local
disassembly, `draw_character_to_surface` then draws with `image_alpha`, and
`draw_character_from_surface` later samples that surface with `draw_surface_ext`.
This is a real potentially alpha-sensitive dependency, although this static
inspection does not establish partial-alpha runtime values in a reported room.
Native comparison should include a partial-alpha sprite drawn into a transparent
surface and then composited, plus a known ONE/ONE trail and subtractive lighting.
A purpose-built GameMaker fixture would need a matching trusted runner/compiler;
patching the supplied game or treating the alternative GL backend as native proof
is not part of this audit. Until that comparison exists, retain and identify the
known contract, report any new-renderer differences, and do not claim all blend
APIs have native-perfect semantics.

Exact `draw_*` names and static reference counts in this FUNC table (including
scripts rather than silently classifying them as builtins):

```text
Registered builtins:
draw_background 40             draw_background_ext 116
draw_background_part 8         draw_background_part_ext 2
draw_background_tiled 2        draw_background_tiled_ext 5
draw_circle_color 4            draw_clear 9
draw_clear_alpha 5             draw_line 43
draw_point 2                   draw_rectangle 68
draw_rectangle_color 7         draw_self 17
draw_set_alpha 171             draw_set_blend_mode 259
draw_set_blend_mode_ext 1       draw_set_color 161
draw_set_colour 1              draw_set_font 89
draw_set_halign 70             draw_set_valign 2
draw_sprite 192                draw_sprite_ext 575
draw_sprite_part 1             draw_sprite_part_ext 2
draw_surface 4                 draw_surface_ext 30
draw_surface_part 1            draw_surface_part_ext 1
draw_text 149                  draw_text_color 2
draw_text_ext 6

Game/script names not registered as rendering builtins:
draw_character 3              draw_character_from_surface 17
draw_character_to_surface 2    draw_cool_text 15
draw_game_surface 1            draw_gui 1
draw_gui_map 1                 draw_map_surf 1681
draw_mapblock 16               draw_surface_map 2
draw_text_shadow 40            draw_txt_1button 12
draw_txt_2buttons 2
```

There are no other FUNC names beginning `draw_`, `gpu_`, `d3d_` or containing
`blend` in this input. This statement is a symbol inventory, not a claim that
dynamic execution exercised all relevant state combinations.

## Primitive and target gap matrix

| Operation | Baseline screen/app GPU | Baseline user surfaces | Unified contract |
| --- | --- | --- | --- |
| Full clear | 320x240 BRAM clear | CPU except clear-started 512x256 deferred journal | Clear arbitrary allocated target dimensions, independent of clip/blend state, with exact RGBA. |
| Uniform axis texture/solid | Normal/add/subtract; shader off; blending on; alpha/fog/masks default | CPU except restricted subtract journal from immutable atlas | Identical draw command semantics for screen, HUD, map, light, character and snapshot targets. No game-object or dimension whitelist. |
| Vertical tint | GPU fill/texture gradient on output only | CPU | Native destination gradient on any target, same edge/sample/tint rounding. |
| Four-corner axis tint | Untextured output first rasterized on CPU into up to four 320x240 scratch textures; textured gradient falls back | CPU | Bilinear per-corner colour/alpha, textured or solid, generated by FPGA; no staging pixel loop. |
| Uniform affine quad | FPGA inverse affine texture sampling; rotated solid lines use white texture | CPU | Any target and valid source; preserve signed transform, pivot, crop and scissor. |
| Nonuniform affine/deformed quad | CPU two-triangle path when affine route rejects | CPU | Triangle coverage + interpolated UV/RGBA or exactly equivalent primitive. Must not assume all quads are parallelograms. |
| Explicit filled triangle | CPU; forces output fallback | CPU | FPGA coverage/interpolation/blend, including circle/ellipse decomposition. |
| Line/outline/point | Lines become quads; uniform affine/axis cases can accelerate | CPU | Lower to generic FPGA primitives without CPU pixels; zero-length and zero-area behavior explicit. |
| Text | Regular/sprite-font glyphs lower to quads; transforms/tint can force fallback | CPU | ARM layout/kerning/wrapping stays; every glyph's pixels follow common FPGA primitives. |
| Tiling | Sprite/surface/nine-slice helper loops lower to quads | CPU | Command setup on ARM is fine; all final tile pixels on FPGA. Preserve clipped source-page rectangles, negative scales and repeat/mirror rules. |
| Surface copy | One exact 320x240 app-to-user copy exports BRAM | General copies use CPU temporary buffer | GPU-to-GPU rectangle copy, clipped both ends, snapshot semantics for overlaps, source/destination generations ordered. |
| Surface sampling | CPU shadow upload, with special app handoff/export and lighting crop | General offscreen producer draws stay CPU | Resident GPU image may be sampled after producer fence without readback/upload; aliases and write-after-read safe. |
| Large source | >1MiB dynamic source sampled/clamped by CPU into 320x240 staging (map is 2048x1024) | CPU image | Resident full map or page/tile-backed source with equivalent address/stride/clamp semantics; no CPU resampling. |
| Quarter-turn source | Immutable atlas transpose/resample is CPU cache preparation, then paired blit | Not general | Prefer efficient FPGA axis/affine sampling; one-time immutable preparation may remain documented, but not hidden per-frame work. |
| Readback/save | Output GPU checkpoint/last-presented special paths, then memcpy | Materialize full journal/CPU shadow | Fence current logical surface version, read exact full-size RGBA (not visible crop); resume without displaying unfinished frame. |
| Resize/free/create | CPU allocation; zero-initializes; IDs reused, zero reserved | Same | Drain or version queued references before release/reuse; retain defined clear-on-resize baseline unless native evidence requires otherwise. |
| Sprite from surface | Stub returns -1; sprite deletion no-op | Same | Not a direct AM2R call. Explicit unsupported diagnostic remains permissible; if claimed implemented, snapshot pixels plus metadata and remove-background behavior need tests. |

### State semantics that cannot be skipped

| State | Baseline behavior | Required decision/testing |
| --- | --- | --- |
| Normal | GPU/CPU floor blend; legacy GPU tint rounds, CPU-compatible offscreen tint floors | Preserve explicit tint policy. Treat 1-LSB differences as failures unless an intentional native-correctness change has evidence. |
| Add | Clamp destination + floor(source RGB * source alpha /255); alpha clamps destination alpha + source alpha | Distinct from ONE/ONE custom mode. |
| Subtract | Native legacy ZERO/INV_SRC_COLOR for all RGBA; alpha-zero RGB can still affect destination | Never alpha-discard this path merely because source alpha is zero. |
| Max/min/reverse subtract | Implemented in CPU switch, no current GPU mode | Add generic channel equations or diagnosed fallback. Static `draw_set_blend_mode` references alone do not prove which mode values occur. |
| Extended blend factors | Setter records separate RGB/alpha factors but CPU `bm_complex` defaults to normal equation | Correct the CPU oracle before using it for custom-factor equivalence; implement source/destination colour/alpha and saturation factors with separate alpha rules. At minimum exact game ONE/ONE must not fall back to wrong normal blending. |
| Blend disabled | CPU writes modulated source (but skips alpha-zero source in existing `blendPixel`) | Distinguish baseline from native-correct semantics. Verify transparent replacement explicitly before changing. |
| Alpha test | CPU rejects `sa < ref` (after source tint) | Test equality, ref0/255, transparent source and every blend mode. |
| Fog | CPU replaces RGB with fog color, keeps alpha | Do not treat as generic distance fog; existing API models constant replacement only. |
| Channel write masks | CPU applies each channel mask after blend | Preserve untouched destination channels; test all16 masks. |
| Shader | Advertised unsupported; current active shader forces software fallback but software does not execute shader | No programmable-shader coverage claim. Log/diagnose unexpected bind rather than silently claiming equivalent rendering. |
| Transform/scissor | ARM builds view/GUI transform and clips to target bounds | Keep legal ARM vertex setup. Do not change camera, geometry or artwork to fit hardware tile coordinates. `setMatrix` currently stores values; `worldToScreen` consumes its separate matrix, so general matrix support is not proven by hook presence. |

## Every renderer hook accounted for

These groups correspond exactly to `HOOK_GROUPS` in the inventory guard.

<!-- hook-group:lifecycle -->
**Lifecycle:** init/destroy, beginFrame, endFrameInit/endFrameEnd, flush.
In this baseline inventory, logical target work can occur during Step before beginFrame. Command batching
must not erase it. EndFrame host/app handoff must not introduce a visible
intermediate presentation. Baseline `flush` was a no-op; the implemented
ordering-only replacement is described below, separately from readback fences.

<!-- hook-group:view -->
**View:** begin/endView, applyProjection, begin/endGUI, setGuiProjection,
setMatrix. ARM state/vertex setup is allowed. Preserve nested target/GUI state,
viewport offsets and scissor while emitting target-local pixel commands.

<!-- hook-group:quad -->
**Quads:** sprite, part, per-corner part, arbitrary-position sprite, rectangle,
per-corner rectangle, surface and per-corner surface. All funnel through axis
or triangle rasterization. This is the correct common integration layer.

<!-- hook-group:line-triangle -->
**Lines/triangles:** uniform or colour line and filled/outline triangle.
Explicit triangles bypass quad acceleration today and must be intercepted.
Circles/ellipses/arrows lower here; there is no need for effect-specific opcodes.

<!-- hook-group:text -->
**Text:** plain and colour text resolve font/glyphs and emit quads. Sprite-font
texture lookup and text-origin/CRT UI offsets must remain identical.

<!-- hook-group:clear -->
**Clear:** clearScreen is a full RGBA replacement. The separate public
`SWRenderer_clearFrameBuffer` host clear is another CPU pixel site outside the
vtable and must be accounted for in tracing.

<!-- hook-group:sprite-lifecycle -->
**Sprite lifecycle:** createSpriteFromSurface/deleteSprite are explicit current
stubs, not usable CPU references. Exact AM2R input does not directly call them.

<!-- hook-group:blend -->
**Blend:** getters, named/extended setters and enable getter/setter. Snapshot
all state into submitted draws so later state changes cannot mutate earlier
commands. Fix custom-factor oracle gap noted above.

<!-- hook-group:pixel-state -->
**Pixel state:** alpha test/ref, channel-mask getter/setter and fog. These are
CPU-supported states with no baseline FPGA implementation, even though no direct
AM2R call is present. They must be implemented or explicitly counted fallback.

<!-- hook-group:tiling -->
**Tiling:** drawTile optional null routes via ordinary sprite-part helper;
drawSpriteTiled, drawSurfaceTiled, drawTiledPart emit quads. The optional null
does not itself indicate a missing rendering capability.

<!-- hook-group:surface-lifecycle -->
**Surface lifecycle:** create/exists/target/ensureApp/dimensions/resize/free.
GPU allocation ownership, stable IDs, clear initialization and reuse fences
replace the current assumption that allocated CPU pixels are always current.
The map-retention cache must not resurrect a freed generation or skip dirty
content because the CPU shadow is stale.

<!-- hook-group:copy-readback -->
**Copy/readback:** surfaceCopy and surfaceGetPixels. Save-state materialization,
restoration, `surface_save` PNG output and application-snapshot helpers are
additional non-vtable clients. Same-target overlap requires a snapshot, not an
order-dependent forward blit. Render-target readback is allowed at these
explicit API barriers, not every target switch.

<!-- hook-group:shader -->
**Shader:** bind/reset, uniform/sampler lookup, float/array/integer writes,
compiled/support queries. Unsupported capabilities must remain honestly
reported; FPGA-only normal AM2R pixels does not mean an arbitrary GLSL engine.

<!-- hook-group:texture-metadata -->
**Texture metadata:** sprite/surface handles, texel dimensions/UVs and stage
selection. Current handles distinguish sprite pages and surfaces; source
resolution must preserve target dimensions and live generation. Stage setter
is currently a no-op, relevant chiefly to unsupported programmable shaders.

## CPU pixel sites and completion accounting

The inventory script lists baseline CPU writers/samplers and copies. They
include subtract span helpers, `blendPixel`, axis and triangle rasterizers,
offscreen replay, gradient scratch generation, large-surface staging, atlas
quarter-turn preprocessing, clear, copy and save restoration. Also inspect
`swEndFrameEnd` host memcpy, allocation zero-fill, image decoding and backend
upload/shadow copies. The latter may be necessary memory transfers but should
not be mistaken for free rendering work.

Before handing off a purported FPGA-only normal renderer, counters should
distinguish: FPGA draw pixels/commands per target; CPU raster pixels per reason
and target; CPU resample/gradient pixels; CPU upload and readback bytes; GPU
target-load/store bytes; command splits/waits; allocation eviction. A zero
**output-frame fallback** counter is insufficient: the old offscreen HUD,
map, lights and gradient staging can consume substantial CPU while that counter
is zero. Strict diagnostic mode should fail immediately on an unexpected CPU
raster path, while normal mode retains safe fallback with bounded reporting.

## Independent acceptance suite

1. **Whole-operation semantics:** replay recorded high-level draw/target/state
   calls through independent CPU reference, descriptor model and RTL. Compare
   every RGBA byte of every live target, including padding never displayed.
   Fix known custom-factor reference holes before treating it as ground truth.
2. **Primitive cross-product:** axis/negative-scale/fractional/quarter-turn/
   arbitrary affine/deformed quads, points/lines, triangles of both windings,
   degenerate dimensions, textured and untextured 4-corner gradients, glyphs,
   every state listed above; tiny and odd-width targets as well as 320x240,
   512x256 and 2048x1024. Test clip edges and target boundaries at subpixel
   values on both sides of half-pixel coverage decisions.
3. **Numeric boundaries:** exact tint and blend rounding, alpha0/1/127/254/255,
   signed U/V wrapping, out-of-range clamp, negative coordinates, fixed-point
   accumulation seams, cache-line crossings and integer overflow rejection.
   A tiled implementation must produce the same pixels as one untiled target;
   tile-local phase must never restart interpolation.
4. **Ownership/order:** A draw -> B samples A -> A changes -> C samples old B;
   two sampled versions of one surface in one frame; draw during Step before
   beginFrame; non-clear-started draws; overlapping self-copy in all directions;
   source equals target; free/resize/reuse before submission; resource eviction;
   more than one command batch; failure partway through a batch. CPU fallback
   must resume the same logical image without displaying partial work.
5. **Readback/save:** full-size current-frame readback after each operation,
   save during active target rendering, load after relaunch, corrupt-state
   rejection, shader/texture handle restoration, same-ID new allocation, and
   save-state format compatibility. Preserve originals using QA copies.
6. **DDR adversity:** random waitrequest/read delay and backpressure, source
   and destination sharing cache lines, odd X/width, output-buffer fences,
   timeout/recovery and reset mid-job. Byte-exact comparisons plus bounded
   completion; no fixed-latency assumptions.
7. **Native oracle:** target known intentional correctness changes (ONE/ONE
   trail, blend-disable transparent replacement, triangle edge conventions)
   against Windows AM2R. Matching a buggy CPU reference is insufficient.
8. **Real USB-1 routes:** exact supplied states plus title/save select, HUD,
   map/equipment enter/exit, item pickup, water/lava/dark rooms, saturated fire,
   sand clearing, missile-hit effects, room transitions, save/load and fresh
   launch. Record frame pacing and CPU/GPU/memory costs, not only average FPS.
   Capture several seconds per effect and inspect anomalous frames. Capture
   buffering does not measure input latency or physical CRT timing.

Completion means no unaccounted normal AM2R CPU pixel path across the inventory
and tests, not that every room has been exhaustively played or every frame is
guaranteed under16.7ms. Game logic still executes on ARM. Keep `sys/` unchanged,
retain the installed recovery build, and do not deploy this migration merely
because the first lighting room becomes faster.

## Implementation status and startup policy (updated September 26, 2026)

The renderer was opt-in during initial bring-up; those earlier experiment
descriptions are historical. Current source requests unified rendering when
`AM2R_GPU_UNIFIED` is absent or exactly `1`. Exactly `0`, an empty value, or any
other value selects the legacy/recovery path. The request succeeds only when
the probed FPGA is available, **both** persistent-target and general-primitive
capabilities are present, and backend enable succeeds. Partial capability0x7
is insufficient; the present capability contract requires all low four bits
(0x0f or a superset). An older RBF, unavailable device, or failed enable retains
the legacy renderer unless strict QA mode requires termination. This startup
default is not evidence of a deployed release, perfect60fps, or full-game
acceptance; see the current progress report for artifact/hardware status.

The separate `AM2R_LIGHT_ORPHAN_CLEANUP=1` experiment remains **default OFF**
pending its enabled doorway soak. That whole-data/provenance-gated lifetime
fix is not the switch that selects the renderer. Ordinary explicit surface
free/release behavior remains active in both rendering modes.

`test_unified_startup.py` compiles the actual initializer and capability-restore
guard:834 startup/legacy-restore truth-table cases,62 incompatible unified
checkpoint restore subprocess exits, and7 strict startup cases pass. A resumed
unified checkpoint cannot silently negotiate down to legacy when target or
general-primitive support is missing. These host checks do not replace real
restore/relaunch testing.

The DMTCP recovery hook also retains the entry rendering mode before device
initialization can clear it. Every return path terminates on a failed unified
restore or lost manager authority, even when an older caller ignores its boolean
result. `test_unified_restore.py` executes the actual public hook and surface
uploader against poisoned ordinary RAM: four success/legacy cases and eight
fatal cases pass, including missing CPU authority at each of three surface
positions after different amounts of partial upload. The success case compares
restored bytes exactly. This prevents continuation with partial GPU state; it
does not turn a failed restore into a successful recovery, and is distinct from
ordinary `.fast` loading. Combined bridge, manager, allocator, lifecycle and
save-state audits remain passing after the startup-policy change.

The new
bridge routes axis and arbitrary-affine quads, filled triangles, lines/glyphs
lowered to those primitives, gradients, all named/custom blend factors,
alpha-test, fog and write masks through GPU descriptors. General opcode 13
uses constant-size ARM plane setup; it does not manufacture gradient textures,
transpose sprites or resample targets on the CPU. The bounded legacy exact
uniform-axis path is retained; unrepresentable/clamped samples use the general
primitive. These are implementation claims, not completed hardware acceptance.

The preserved affine fast path is now conservative in unified mode: it accepts
only bounded, nondegenerate rectangular UV domains. Rotated crops that need
texture-edge clamping, deformed UVs or constant-coordinate sampling use the
dimensioned general primitive instead of opcode4's unchecked texture addresses.
Finite signed16.16 coefficient bounds are checked before conversion, including
near-collinear transforms. Legacy mode is unchanged. The actual helper tests
cover negative/overrun UV, nonrectangular and constant UV, mirrored valid UV,
nonfinite inputs and fixed-point conversion overflow; source-level wiring audit
ensures these checks precede affine submission/conversion.

The final bounded dispatch audit also checks the actual assumptions behind each
packet, not just whether the draw's name has a GPU implementation:

| Route | Required preconditions / retained limits |
| --- | --- |
| Fast axis | Exact rectangular geometry, uniform normalized RGBA, supported ordinary state, separable UVs, positive source dimensions and floor-tint capability. The existing axis planner proves sample rows/range/tint and completes all tiles before publication. |
| Fast affine | Uniform normalized RGBA and supported ordinary state; the retained parallelogram test allows 0.01px residual, UVs must be bounded/separable/nondegenerate, and every actual Q16 conversion input plus all four accumulated bounding-box corners must fit signed Q16. Unified-only overflow rejection routes through generic triangles. This legacy tolerance and Q16 sampling are not new native-exact claims. |
| Generic axis | Exact rectangular geometry and separable UVs. Out-of-range or constant UV domains remain legal because this packet has texture dimensions and clamps sampling. Four-corner RGBA remains bilinear. |
| Generic triangles | Arbitrary nonseparable UVs and deformed/rotated geometry; two triangles are fully set up before publication, sharing one source snapshot. Both windings and shared-edge ownership are tested. |
| Every generic packet | Finite coordinates/attributes, coordinates within ±32767, validated state/factors and positive dimensions, every signed-Q32 coefficient and accumulated bound within the stated safe range. Out-of-contract numeric extremes reject explicitly, never silently wrap. |

Near-axis geometry no longer silently snaps merely because it is within 0.01px;
it uses affine/triangles. Exact axis game dimensions are unchanged. Fast
byte-tint setup rejects finite colours outside `[0,1]` so the generic clamp can
handle them without overflowing the old round-before-clamp conversion. A device
with generic/target support but no floor-tint capability uses generic textured
axis draws instead of accepting a plan and then failing its first descriptor.
The existing tiny-dimension/triangle-degeneracy tolerances and fixed-point
rounding remain identified implementation limits; these tests do not prove
native floating-point equivalence. `test_unified_dispatch.py` compiles the actual
selectors/setup with the production axis planner and independently observed
device sinks: 25 cases cover geometry, arbitrary/mirrored/constant/clamped UVs,
gradients, pixel state, absent capability, invalid numbers/resources, empty
coverage, and one source acquisition for a two-triangle quad. The affine sink
is mocked here; its conversion math has separate actual-helper tests.

Every surface (including host and application) has an independent generation
handle. Target selection, source dependencies, alias snapshots, raw surface
copies and explicit CPU readback go through the persistent-target manager.
Automatic application presentation is a raw GPU copy; explicit `draw_surface`
retains its ordinary blend/alpha/scissor/transform semantics. Save serialization
materializes all registered surfaces including host. Release, resize and logical
restore invalidate GPU handles before CPU storage can be reused. CPU fallback
fences source/target, takes an independent self-source snapshot if needed, and
publishes the resulting pixels before GPU rendering resumes.

`AM2R_GPU_UNIFIED_STRICT=1` fails closed if unified mode is unavailable, a draw
requires CPU raster fallback, or resource pressure would disable GPU ownership.
Do not enable it for normal testers yet. Lost GPU authority is always fatal;
an obsolete CPU shadow is never accepted as successful recovery. Non-strict
resource fallback materializes and invalidates all targets before returning to
the software renderer. Unsupported programmable shaders remain unsupported.

Diagnostic builds report cumulative GPU/CPU draw counts, explicit CPU publish
bytes, readback bytes and initial-shadow registration bytes every 300 frames.
Registration is lazy, so **initial shadow bytes are not uploaded-byte traffic**;
a following full clear may eliminate that import completely. Inspect backend
traffic counters for actual DDR transfers. A GPU coverage claim additionally
requires `active=1` throughout, not just `cpu=0` after a renderer was disabled.

New independent tests run successfully on the development host:

- `test_unified_blend_contract.py`: seven contract tests, all factor pairs,
  boundary alpha, masks and state ordering. The actual production blend helper
  now implements custom factors rather than treating them as normal alpha.
- `test_unified_blend_production.py`: 6,000 deterministic randomized cases
  compare the compiled production helper with independent channel equations,
  including transparent RGB, custom factors, fog, alpha-test and masks.
- `test_unified_packet_setup.py`: actual compiled packet setup checked at
  76,429 locations; winding normalization, degenerate triangles, invalid
  states and individual/accumulated fixed-point overflow rejection included.
- `test_unified_bridge.py`: actual lifecycle/copy/readback/fallback glue against
  separate CPU/GPU mock images; canonical source ownership, overlapping-source
  snapshots, clipped raw copy, fenced publication, save materialization,
  generation reuse, coherent allocation fallback and three fail-closed modes.
- `audit_offscreen_lifecycle.py`: separately asserts new owner/fence/save
  wiring and preserved legacy journal ordering. Existing journal regression
  still passes 120 randomized full-size replay cases; original assertions remain.

Q31.32 interpolation is not claimed bit-identical to native IEEE float. The
large 317x241 fractional bilinear-gradient fixture measured maximum normalized
coefficient error `8.651292389860732e-06` and **190 channel-byte quantization
differences** from independent double-precision formulas; binary-exact small
fixtures were identical. These differences are reported rather than hidden by
tolerance. Texture-coordinate boundary, tile seam and native image comparisons
remain required before release, as do RTL stalls, real-device captures, pacing,
save/load and the supplied problematic rooms. Host mock/model passes alone do
not establish any of those hardware results.

### Correctness correction: shared triangle edges

`characterize_unified_shared_edges.py` first exposed a shared-edge error in both
the general-quad packets and historical CPU triangle rasterizer. A rotated,
nonuniform-colour quad split across a pixel-centre diagonal drew that sample
twice. The synthetic fixture `(2,0),(6,2),(4,6),(0,4)` with alpha0.5 produced
alpha190 instead of single-fragment alpha127 at `(2,1)` and `(3,4)`. Matching
the old CPU reference did not make that correct:
[OpenGL 2.1 §3.5.1](https://registry.khronos.org/OpenGL/specs/gl/glspec21.pdf)
requires exactly one adjacent polygon to own a sample on a shared edge.

The host setup now applies a one-Q32-LSB coverage-only bias to bottom/right
edges; unbiased geometric planes still determine UV and colour. CPU triangles
use the equivalent top/left ownership predicate. No packet ABI or RTL changes
are required. Uniform axis/affine paths are unchanged. The characterization now
serves as a regression: five actual-setup/actual-CPU fixtures pass an independent
geometric oracle for both windings, the original quad diagonal, four triangle
fan spokes meeting at a sample centre, and outer top/left boundaries. Covered
sample counts are20/20/64/64/36, with no double-owned samples and unchanged
alpha127. This is host arithmetic proof; native Windows room impact and hardware
coverage remain separate acceptance items.

### Bounded raw clear / replacement (opcode 14)

Capability bit4 permits replacing just a pre-clipped BRAM rectangle with exact
RGBA, including nonzero RGB at alpha zero. It is not a blended fill; legacy
opcode1 still clears the whole320x240 workspace. Descriptor word0 contains the
opcode and16-bit width/height; word1 low32 is RGBA; word2 low32 contains unsigned
16-bit x/y. All other bits/words are reserved zero. Empty extents are no-ops.
The RTL safely no-ops malformed/out-of-bounds descriptors; the independent
capture model instead rejects malformed input so a bad command cannot be
reported as successfully reproduced rendering.

Partial clear does not initialize untouched BRAM. Actual diagnostic captures
provide observer-fenced raw initial RGBA; synthetic tests provide a deterministic
full pattern. Missing initial pixels still cause strict rejection when they
are needed; comparison masks or invented black pixels are not used.

`test_gpu_targets.py` now passes16 tests with `AM2R_TEST_RTL=1`, including eight
ModelSim replays across present/no-present completion and two DDR schedules.
Four replays specifically exercise opcode14: both BRAM lanes, odd coordinates
and widths, borders, overlap, zero extents, raw alpha0/1/127/128/254/255, unchanged
outside pixels, and clear→STORE→cached-texture sampling order. The other four
retain the prior raw target-transfer fixtures. Full raw exports are compared
even when presentation discards alpha. Reference-only tests also reject
reserved bits, invalid bounds and unknown opcodes, and preserve old opcode1
dimension-ignoring behavior. This establishes independent model/RTL pixel and
ordering agreement, not a new hardware frame-rate measurement.

### Final bounded SW/manager audit: ownership and remaining limits

The September26 read-only follow-up traced all70 classified renderer hooks,
normal quad/triangle entry points, transformed text, clear/copy/readback,
host/application handoff, resource teardown, save materialization and the
current target manager. No additional normal AM2R CPU-raster escape was found
in those inspected paths. This is bounded source/test evidence, **not** proof
that every game operation or every AM2R room is compatible. The legacy software
renderer remains available as a regression reference and explicit recovery
path; the native-equation caveats above still apply to both implementations.

| Operation | Implemented ownership / ordering contract |
| --- | --- |
| Renderer `flush`, including runner target switches | Close the logical draw batch through `MisterGpu_surfaceFlush`; no unconditional CPU readback or device-completion fence. Capacity pressure can still submit/wait. The actual bridge regression checks the no-readback path, failed flush, and legacy no-op. |
| `beginFrame` after Step writes | Preserve queued target work and descriptor order. Do not clear the unified queue as the legacy frame path does. |
| Sample a different surface | Target selection previously sealed that source's batch; spill only that source if its newest image is still resident, and lazy-import only untouched CPU-initialized content. An unrelated dirty current target need not spill. |
| Sample the current target | Seal/spill first, then create a full raw GPU snapshot. Fence before reusing the shared snapshot allocation; a two-triangle logical quad acquires one source snapshot. |
| Readback / CPU fallback / save | Complete queued GPU work, then obtain current raw RGBA. Publish CPU fallback results before any later GPU draw. Saves materialize every registered live surface, including host; CPU-initialized unregistered surfaces are already current. |
| Copy | Clipped raw RGBA independent of blend state. Self-overlap snapshots. Full equal-size copy from resident source preserves its canonical image before transferring BRAM residency to the distinct destination. |
| Release / resize / logical restore | Fence readers/writers before invalidating generation handles or reusing CPU storage. Restored contents register fresh handles. |
| Present | Select the dedicated320x240 host image and submit ordered presentation. A failed unified presentation withholds stale CPU pixels; subsequent checked ownership operations fail closed on lost device authority. |
| Deferred command payloads | Copy state/setup at acceptance. Generic512-byte payloads are copied immediately and uploaded into the same fenced bank/slot as their command. Opaque culling excludes commands whose relocation could break payload addressing. |

The new immutable-atlas opaque-prefix culler is a CPU metadata/coverage proof,
not software pixel rendering. It only removes a prefix inside one tile and one
submission sequence after a later normal, alpha255, unit-step atlas suffix
proves complete coverage. Dynamic surface shadows, unknown/stale generations,
alpha pinholes, coverage gaps, observable transfers and generic/affine payloads
cannot prove that overwrite. `test_unified_opaque_cull.py` runs12 tests against
the actual C helpers and independently renders before/after commands, including
full/partial/odd tiles,30 randomized layouts, generation refresh, reserved bits
and safe preceding LOAD/STORE work. Its strict descriptor preflight closes the
previously found unknown-flag/reserved-word acceptance hole.

Known limits must remain visible rather than being labeled complete coverage:

- Programmable shaders are advertised unsupported; shader uniforms/stages are
  stubs. `setMatrix` stores metadata but does not apply general GML matrices to
  the existing SW projection. Sprite-from-surface creation returns failure.
  The exact AM2R inventory has no direct binds/matrix writes/sprite creation;
  internal or indirect behavior is not excluded by that absence.
- The manager has128 live target records,512 queued draws, dimensions up to
  4096x4096 and a finite reserved-DDR pool. Allocation pressure is explicit
  coherent fallback, or fatal in strict QA, not unlimited resource support.
- Nonfinite/extreme geometry, fixed-point bounds, degeneracy tolerances,
  texture layout assumptions and the identified Q16/Q32 rounding remain part
  of the dispatch contract. They are not native floating-point equivalence.
- Normal/add destination alpha and named max retain the historical tested
  equations, not a claim of verified proprietary-runner semantics. General
  custom factors, pixel state and gradients have independent arithmetic tests,
  but only a native comparison can resolve remaining compatibility questions.
- CPU raster counts must be read together with strict/active status. After a
  deliberate whole-renderer non-strict disable, later legacy rendering is not
  evidence of FPGA coverage. CPU asset decode, zero initialization, opacity
  metadata, explicit readback/save copies and resource transfers remain allowed.
- Passing these host tests neither establishes60fps nor validates analog video,
  input latency or all-room behavior. Current hardware bring-up/performance
  evidence belongs in the linked progress report. The capability-gated startup
  default above does not waive those separate acceptance gates.

### Independent streaming-axis and pipeline-drain regression

`test_gpu_axis_stream.py` independently computes submitted op2 byte equations
without importing the reference model's tint/blend helpers. Its210 axis draws
cover normal/add/inverse-source, legacy rounded versus floor RGBA tint, all
channel endpoints, source/destination lane parity, widths1 through320 around
the32-pixel eligibility threshold and96-beat request boundary, odd padded
stride, fractional starts, clipping, row tint gradients and nonunit/reversed V.
Another23 cases exercise signed16.16 U wrap, mirrored/scaled/constant U and
top/bottom clipping through the preserved fallback semantics. Transparent RGB
under inverse-source blending is explicitly non-identity.

Separate water fixtures retain the original320-pixel96+64-beat boundary failure
over patterned destination pixels, odd partial rows and the shared opcode9
alpha-only additive path. Ordering fixtures prime a cached source, then test
stream→STORE→clear→sample-updated-source→LOAD→overlapping-stream→narrow-cached-read
before final raw export. They detect in-flight writes crossing command boundaries
and stale cached DDR after STORE, without assuming a native blend oracle.

All six tests pass with `AM2R_TEST_RTL=1`:16 production-RTL replays across the four
fixture families, both present/no-present completion and DDR schedules
`(latency2, stall11, gap0)` / `(latency5, stall7, gap3)`. Every framebuffer byte,
untouched outside pixel and raw export is compared; no tolerance/masks are used.
The checked RTL hash is
`2659899b45e377243d40e62dba4331fd2ea1d235ac8b5a172994b57dc80caea3`.
This establishes scalar/RTL pixel and ordering agreement, not synthesis timing,
hardware arbitration behavior, a frame-rate improvement or full-game acceptance.

The subsequent cache-gather change is independently covered by two additional
fixture families. Another168 axis draws exercise repeated, mirrored, fractional,
skipped and signed-wrap U, cache-line crossings, both-sided destination clipping
(`x=-5,width327`) for both unit streaming and gather, row gradients, odd lanes,
and all three existing blend modes. A separate ordering sequence interleaves
stream/gather with STORE, LOAD and clear while sampling modified source memory.
The actual source equations are computed independently of the model's helpers.
All eight tests pass:24 production-RTL replays across six fixture families,
present/fence endings and the same two DDR schedules, with exact full-frame and
raw-export comparisons. Checked RTL SHA-256:
`3a094b7cafcf219e40aa45e15fd64f949e4c06eb918c9a119b484cd3bb551aad`.
Evidence is under ignored `axis-stream-reference-uj5cb8n3`. The read-only RTL
review found no pending-pair/refill/drain blocker; synthesis and hardware gates
remain separate.

The affine-bound regression extracts the actual host dispatch helper. A nearly
degenerate finite quad can have individually representable coefficients while
their accumulation wraps into the UV range, painting pixels outside the quad.
The unified guard checks all four corners using wide arithmetic and dispatches
to the generic route, without CPU rasterization. The independent rational
coverage fixture has100 covered samples and zero false positives; the unchanged
legacy control has1,154 false positives. Actual emitted generic packets match
that independent image in both DDR schedules (`test_unified_affine_bounds.py`).

### Bounded progressive-slowdown source audit

The reported regression concerns the previously released `d472d6c1` runner,
after roughly five minutes involving expensive rooms and a return to formerly
fast rooms. The new unified manager is not that released renderer. The audit
compared the reconstructed52-patch baseline and current shared code; it did not
reproduce the user's natural route or establish a cause.

| Persistent state | Inspected behavior / diagnostic implication |
| --- | --- |
| Ordinary legacy draw rejection | `gpuFrameEligible`, scratch indices, and backend `offscreen_allowed` reset every `beginFrame`. A single unsupported draw does not itself latch software rendering across rooms. |
| Lighting journal / crop records | Journals are bounded256 operations per reused surface ID. The backend has16 crop records, released on surface free/discard, with retained physical capacity and same-frame reuse protection. Failed recording materializes correctly and can retry next frame. |
| Texture arena / record table | Shared128MiB bump allocation and128 texture records do not shrink until process exit. Legacy released-record reuse requires exactly equal byte sizes and an earlier release frame. Distinct-sized churn can therefore exhaust capacity even when many records are released; that is a real limit, not proof it occurred here. |
| Immutable atlas pages | The supplied AM2R data contains20 pages totaling75,497,472 RGBA bytes (72MiB). These alone do not exhaust128MiB. They load lazily and remain resident; a failed first GPU upload stores address0 without retry for that page. |
| GPU/device failure | Timeout or invalid presentation completion sets `g_gpu_available=false` persistently. Such failure is logged and is distinct from ordinary unsupported-operation fallback. |
| Pacing fallback | Three consecutive heartbeat timeouts, or lost heartbeat magic, latch timer pacing. Inspect the relevant warning/status; reduced cadence is not by itself proof this occurred. |
| Map/fusion/quarter-turn caches | Bounded retained buffers; texture release invalidates dependent map/fusion identities. Quarter-turn cache has eight lifetime entries and no eviction, but a miss still has the affine route. No unbounded per-frame cache insertion was found. |
| Room and instance lifecycle | Old spatial grid is freed/rebuilt on entry; nonpersistent instances, destroyed instances, draw-cache entries and outgoing lazy room payloads have explicit cleanup. No accumulation defect was identified in these inspected paths. |

The earlier first-pit failure did prove texture-table exhaustion on an old
64-record build; its fix increased the limit and reused exact-size released
records, not a general allocation/eviction scheme. Keep that historical evidence
separate from this new report. Likewise, the new manager's180 create/copy/release
cycles and17 checkpoint restores stabilize at8,548,224 bytes for the tested
bounded shapes, but do not establish arbitrary-size churn or old-renderer safety.

The useful next check is one uninterrupted visit/return route, with no reload
that might reset accumulated state. Compare the same room/position before and
after: GPU availability, texture bytes/total/live/released records, atlas uploads
with address0, surface live count, renderer blend/fog/write-mask state, pacing
fallback status, CPU Step/Draw timing, GPU cycles and process memory. Stable RSS
alone cannot exclude a persistent renderer fallback or retained GPU arena state.

Diagnostic builds now expose a finite metadata-only sampler for that check:
set `AM2R_RESOURCE_SAMPLES=180` before launch for at most180 lines (roughly
15minutes at60fps). The valid range is1..720; absent, zero, malformed or larger
values disable it. It samples once per300 **game frames**, so a slow game takes
longer than five seconds between lines. Each `MiSTer resource state:` line
contains GPU availability, explicit texture arena high-water bytes, currently
owned/free/largest-free arena bytes and live allocation count, texture total/live/released/missing
addresses, legacy crop keys/current versions, allocated/live unified target
records, SW surfaces, loaded atlas pages with missing GPU addresses, pacing
fallback, and blend/fog/write-mask/alpha/shader state. No addresses, filenames,
paths, credentials, pixels or game variables are logged. Surface/atlas scans
are capped128 records and explicitly marked truncated when incomplete. Arena
accounting scans exactly the bounded1,024 metadata slots, includes pinned
packet/frame allocations and retained released-object capacity, and never reads
their contents. `texture_highwater_bytes` may stay high after recycling;
`arena_owned_bytes` is currently reserved capacity, not just live game images.
Before arena initialization, owned/allocation counts are zero and the complete
pool is reported free without initializing or mutating it.

The sampler neither fences nor reads/writes shared GPU memory, performs no
allocation or cache mutation, and stops permanently when the requested count
is reached. Only its own sample bookkeeping changes. It and its call/prototype
are compiled out without `MISTER_RENDER_DIAGNOSTICS`.
`test_resource_state.py` checks the actual helper with real renderer layouts:
default/invalid settings, bounded parsing, exact counters/state,720-sample stop,
duplicate suppression, truncated arrays, invalid active-buffer index, unchanged
metadata, pinned/fragmented/uninitialized/full-table arena accounting and
deliberately invalid pixel-pointer sentinels. Release preprocessing
contains no telemetry helper, environment key or log format. Root build/hash
verification remains the binary-level gate. The new diagnostic runner in legacy
mode supports a baseline-equivalent comparison but is not the exact old
`d472d6c1` executable; identify that distinction in results.

The subsequent natural-doorway experiment found live SW surfaces and live GPU
targets increasing by one512×256 allocation on every transition. This is not
dead arena capacity and cannot be solved by recycling released allocations.
Exact supplied1.1 bytecode gives the nonpersistent lighting object an
unconditional512×256 create and a Destroy-event free, but no Room End/Cleanup
handler or inherited parent. The runner removes ordinary nonpersistent room
instances with Cleanup, not Destroy. That observation alone does **not** prove
the runner is wrong: do not insert blanket Destroy events or automatically free
creator-associated resources without establishing native semantics and sharing.
The screenshot-save builtin allocates and frees temporary CPU PNG buffers but
does not create a renderer surface; its transition log is only correlated.

For direct attribution, diagnostic builds support
`AM2R_SURFACE_TRACE_LIMIT=1024` (valid1..1024, defaultOFF). A shared finite budget
records GML surface create/free/resize calls and removal of recorded creators
after the existing room Cleanup event, before the existing instance free. Each
line includes frame/room, bounded sanitized code name, event and instance/object
IDs, surface ID, requested/current dimensions, current existence, and recorded
creator identity. There are128 fixed metadata records; overflow is explicitly
`tracked=0`. Creator removal is logged once per recorded creation and does not
mean the surface lacks references elsewhere. At removal, `room` is already the
destination selected by the runner; `created_room` retains the original room.
No game/resource lifetime is
changed, no pixel data is accessed, and no GPU fence or allocation is performed.
After the line budget is exhausted all metadata work stops. Use a fresh process
and natural traversal after loading the checkpoint: preexisting restored surfaces
are not retrospectively attributed. Logical `.fast` restore explicitly clears
creator records before replacing surfaces, preserving the finite process log
budget so reused surface/instance IDs cannot inherit stale attribution.
`test_surface_lifetime.py` passes seven actual-helper tests covering invalid/default
OFF settings, shared hard cap, metadata/instance immutability, creator-ID and
surface-ID reuse, capacity overflow/reuse, sanitized caller text, ignored frees,
restore identity invalidation with unchanged budget, and release preprocessing
without the helper, environment key, calls or strings.
Native-engine resource lifetime equivalence remains a separate check.
