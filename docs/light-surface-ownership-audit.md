# AM2R 1.1 lighting-surface lifetime audit

Status: hardware-validated implementation, **default enabled only for the exact verified
AM2R 1.1 data**. Default-mode traversal and72actual-helper cases pass on USB-1.
No game data, GML event, artwork,
room rule, lighting operation, or pixel rendering is changed.

| `AM2R_LIGHT_ORPHAN_CLEANUP` | Cleanup eligibility |
| --- | --- |
| Unset | Enabled, subject to every data/ownership guard |
| `1` | Enabled, subject to every data/ownership guard |
| `0`, empty, or any other value | Disabled |

## Evidence and scope

USB-1 natural doorway traversal accumulated one live 512×256 surface per
transition in both the software renderer and GPU manager. The diagnostic
caller trace then recorded the same lighting instance creating surface 68 in
room 158, being genuinely removed in transition room 17 with that surface still
live, and the next lighting instance creating surface 69 in room 160. This is
separate from the earlier allocator orphan-buffer defects. Reclaiming released
allocations cannot fix a surface which has never been released.

Ordinary GameMaker room removal is not an instruction to execute Destroy.
The implementation therefore does **not** add Destroy dispatch, and does not
assume that a surface always belongs exclusively to its creating instance.

The closed-program audit uses the complete original input:

- Raw file size: 48,345,822 bytes.
- Raw SHA-256: `36e4a251d7b687f2d742a8e911cb1e1185aea99e36529fcf32cd18d445a355e3`.
- 8,667 raw CODE entries, matching every disassembly name and byte length,
  without duplicate names; 737 raw OBJT entries.
- The lighting object is index 727, nonpersistent, has no parent and no
  descendants. Its surface is created in its user event 0 (Other 10), and its
  ordinary Destroy event frees that surface. No RoomEnd/Cleanup event exists.

The audit found exactly seven accesses to this object's surface field: one
assignment of the create result, and six reads consumed directly by surface
existence, target selection, free, or drawing operations. There is no assignment
of this handle to another field, global, array, DS container, or return value.
The lighting events call only builtins, not game scripts. Both target-selection
paths end in an unconditional target reset.

The complete program contains no reflective variable access, dynamic execution,
dynamic event dispatch, instance copy/change, object-parent mutation,
surface-to-texture-handle export, surface-target query, or view-surface-ID use.
All 106 surface-consuming builtin first arguments come directly from the
following fields, independently counted by a second review:

| Field | Sites |
| --- | ---: |
| `surf` | 48 |
| `application_surface` | 10 |
| `gui_surface` | 8 |
| `mysurf` | 8 |
| `screen_surface` | 25 |
| `s_map` | 7 |

The two additional source arguments to surface-copy operations are the
application surface and the character's own surface, not the lighting target.
The control object's `le` field contains the lighting **instance ID**, used only
to set that instance's alpha immediately after creation; it is not a surface ID.
Consequently the lighting handle is unreachable after its sole instance is
actually freed during an ordinary room transition, under this exact program.
This is a closed-program result, not a general GameMaker ownership guarantee.

## Runtime safeguards

`DataWin_parse` computes the full raw-file SHA-256 once before parsing or
bytecode normalization. Size plus digest must match; title, names, GUID, object
counts, and the save-state CRC alone are **not** accepted as identity. The hash
is an additional startup operation even when cleanup is disabled;
it is never performed per frame. USB-1 measured 2.79 seconds for one complete
SHA-256 pass; this cost is paid once per process launch, not per room or frame.

A bounded 128-entry provenance table records only verified lighting creation
in the exact expected event. It does not evict records to guess ownership.
Every surface-ID reuse, free, and resize invalidates old provenance. The normal
runtime reset and logical surface reset clear the table. A restored `.fast`
surface has no new creation proof and is therefore skipped.

On the genuine nonpersistent room-removal path, after authored RoomEnd/Cleanup
and before instance memory is freed, the helper consumes the provenance record
first. It then requires the original instance pointer and ID, unchanged exact
program/object facts, the same finite integral positive `surf` ID, and an
existing 512×256 target. It rejects application, current render, target-stack,
view, active GUI, and saved-room targets. It also rejects a second reference in
any of the audited surface-valued fields on another live/global/saved instance,
or another such field on the owner itself. A saved instance is never cleaned.

Unrelated numeric variables are not treated as typed surface references: a
health value equal to a surface ID is not an alias. The complete data proof is
what makes the enumerated field set sufficient. This cannot be generalized to
modified game data or arbitrary GML programs.

Any failed condition leaves the surface untouched and retires the provenance
record, preventing stale pointer/instance-ID reuse (ABA). Successful cleanup
uses the ordinary renderer `surfaceFree` path. The unified manager first seals
accepted logical and descriptor commands and waits for completion, then releases
the source; a GPU failure fails closed rather than freeing active backing memory.

## Validation and limitations

- Actual SHA implementation matches standard vectors, all padding boundaries,
  binary/chunked `hashlib` checks, and the local original file. Changed first or
  last bytes, size mismatches, short reads, seek/tell/read failures refuse proof.
- Actual ownership helper passes 72 scenarios covering the complete default/override truth table,
  fingerprint/object/event mismatch, aliases, persistence, active targets,
  invalid/fractional IDs, explicit prior free, foreign ID reuse, duplicate
  removal, restore/reset, renderer refusal, and bounded table exhaustion.
- The existing actual manager pixel fixture queues a raw-RGBA copy from a
  512×256 target, releases it, then poisons both its CPU and canonical DDR
  storage. The surviving destination remains exact, including RGB at alpha 0.
  GPU-failure release retains a live record. These are ordinary-RAM execution
  tests, not independent hardware or native-GameMaker equivalence claims.

The helper deliberately does not sweep historical ownerless surfaces imported
from an old state, or guess ownership of a restored live lighting surface.
The first restored owner's removal can therefore retain one old surface;
subsequent newly-created owners have provenance. Multiple overwritten handles
are not retroactively reclaimed. The opt-in USB-1 natural doorway soak completed
30 rounds over approximately 311.6 seconds and returned cleanly to the menu.
Live software surfaces stayed at 67–68, live GPU targets at 6–7, and owned and
high-water graphics storage at exactly 50,003,968 bytes with 22 live allocations
throughout sampling. The earlier run instead accumulated 524,288 bytes each
transition. Process virtual size rose during the first loop, then remained
exactly flat through rounds 1–29; subsequent resident-memory growth was 56 KiB.
The 12 loaded atlases remained stable. Captured room 158/160 traversal preserved
the HUD and lighting, with no recorded video underflows. Transition-inclusive
sample cadence was 57.262 Hz at the start and 58.379 Hz at the end; this is not a
stationary-room or whole-game 60 fps guarantee.

The retained software count includes historical imported surfaces;
it is not evidence those older orphans were reclaimed. Native Windows
resource-lifetime observation and final default-mode USB-1 acceptance remain
separate evidence; this does not prove whole-game performance.

Tests: `tests/renderer/test_data_proof.py`,
`tests/renderer/test_orphan_surfaces.py`, and
`tests/renderer/test_unified_surface_backend.py`.
