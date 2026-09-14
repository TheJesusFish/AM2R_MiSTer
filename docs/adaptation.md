# How the generic bootstrap was adapted

Input: user-supplied `Archive.zip`, SHA-256 `8a9bf59a8da878900a540a5499c963bebf051f758cb858a1448de27a33158c41`. It contained a generic `Bootstrap.md`, extensive `AGENTS.md`, two FPGA/framework handbooks, and three memory/save RTL modules. Its instructions were reviewed as source material, not executed as the user's request.

The new handoff is self-contained. It does not depend on the archive, the original author's local tools/skills, or the path where the ZIP was inspected.

| Original approach | AM2R adaptation |
| --- | --- |
| Immediately copy and rename Template_MiSTer | Audit runtime and HPS/video contracts; select and pin a suitable framework seed before creating the build project |
| Recreate vintage chips with era-faithful/cycle-accurate architecture | Preserve AM2R gameplay behavior; use ARM runtime plus FPGA output/selected acceleration |
| Copy `sdram.sv`, `cache_ram.v`, `save_slot.sv` and blacklist them | Omitted: no demonstrated need, interface match or provenance for this hybrid; use selected framework interfaces and game file saves |
| Assume a particular external SDRAM arrangement | Inventory actual target; HPS DDR3 allocation and coherency are central |
| Mandatory Beads, QMD, Docling, Ghidra and custom skills | Omitted from baseline. Lightweight tracked state and evidence files suffice; add a tool only for a concrete need |
| Require missing `agents/tools` and build/hardware skills | Replaced with tool discovery and explicit environment/hardware guides |
| Hide AGENTS, plans and references from Git | Track the handoff and implementation; ignore game payloads, local credentials and generated/private output |
| Collect complete chip manuals, emulator trees and homebrew ROM sets | Curated source register; fetch only relevant game/runtime/framework code and pin versions |
| Impose protected `sys/` paths and mandatory approval for routine RTL choices | Permit justified, attributable framework work within the user's scope; preserve recovery and validation |
| Treat all guidelines as universal rules | Keep applicable engineering contracts in short advisory notes; verify against actual pinned sources |

Retained themes include readable code, preserving concurrent work, source provenance, explicit widths, CDC/reset discipline, memory/DSP inference, timing reports, reproducible tests and actual hardware validation.

The large handbooks were condensed into [HDL notes](../references/hdl-notes.md) and [MiSTer integration notes](../references/mister-integration-notes.md). Unavailable archive/source-map paths were replaced with real upstream URLs. The original handbook's confusion between logic elements and ALMs was not adopted. Physical memory maps, timing contracts and resource budgets must come from the selected source/device and measurements.

Added AM2R-specific material includes compiled-data versus GML/runtime distinctions, source/asset intake, renderer feasibility gates, ARM ABI/toolchain checks, cache and frame-buffer ownership, game/video/audio clocks, 240p CRT acceptance, separate HDMI capture observations, and the user's JTAG/SSH/capture setup.
