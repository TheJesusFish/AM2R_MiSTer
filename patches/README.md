# Reproducible upstream changes

This directory preserves the MiSTer port made in the otherwise ignored
`third_party/Butterscotch` checkout.

- Upstream: `https://github.com/ButterscotchRunner/Butterscotch.git`
- Base commit: `7c2503efc25f20dddb9ba7b7cf7b46fd4f63ba08`
- Purpose: ARM/MiSTer backend, FPGA command transport, software-renderer bridge,
  audio instrumentation, save/file builtins, and AM2R runtime compatibility.

Apply the patches from the root of a clean checkout at that exact commit:

```sh
git apply ../../patches/butterscotch-tracked.patch
git apply ../../patches/butterscotch-mister.patch
git apply ../../patches/butterscotch-mister_gpu.patch
git apply ../../patches/butterscotch-sw_renderer.patch
git apply ../../patches/butterscotch-sw_renderer_h.patch
git apply ../../patches/butterscotch-savestates.patch
git apply ../../patches/butterscotch-persistent-savestates.patch
git apply ../../patches/butterscotch-controls-hints.patch
git apply ../../patches/butterscotch-fpga-performance.patch
git apply ../../patches/butterscotch-scanout-diagnostics.patch
git apply ../../patches/butterscotch-audio-rate.patch
git apply ../../patches/butterscotch-axis-coverage.patch
git apply ../../patches/butterscotch-renderer-audit.patch
git apply ../../patches/butterscotch-linux618-framebuffer.patch
git apply ../../patches/butterscotch-surface-revision-map-performance.patch
git apply ../../patches/butterscotch-hud-composite.patch
git apply ../../patches/butterscotch-collision-destroyed-reciprocal.patch
git apply ../../patches/butterscotch-spatial-grid-activation.patch
git apply ../../patches/butterscotch-collision-self-order.patch
git apply ../../patches/butterscotch-atomic-text-writes.patch
git apply ../../patches/butterscotch-room-state-restoration.patch
git apply ../../patches/butterscotch-application-surface-snapshot.patch
git apply ../../patches/butterscotch-opaque-prefix-cull.patch
git apply ../../patches/butterscotch-water-native-deferred-export.patch
git apply ../../patches/butterscotch-water-pipeline-performance.patch
git apply ../../patches/butterscotch-large-file-metadata.patch
```

The twenty-six files were generated from the hardware-validated working tree and
each passes `git apply --reverse --check` against that tree. See the final
hardware and save-state reports under `reports/` for build identities and test
results.
Applying all twenty-five to a fresh detached clone at the base commit reconstructs
the current runner source after normalizing checkout line endings. The first
seven patches were reconstructed and byte-compared with the
80 MHz hardware-tested runner source on 2026-09-06. The eighth adds semantic
controller bindings, recorded-script precedence for AM2R's compatibility
helpers, and the VM-only presentation stub. The ninth adds the hardware-tested
GPU map-transition compositor, retained/incremental AM2R map surface, exact
application-surface handoff, and associated performance fixes. The next four
patches add scanout diagnostics, fixed 48 kHz production audio, pixel-center
axis coverage, per-corner/gradient rendering, and explicit operation/fallback
telemetry. The Linux compatibility patch keeps the original `/dev/fb0` mmap
path and adds an `ENODEV`-only `/dev/mem` fallback for Linux 6.18. The fifteenth
patch adds revision-aware dynamic surfaces, direct small-surface uploads, the
multi-source retained map compositor, and invalidation of FPGA-only derived
surface caches after save-state restore. The first fifteen were
clean-applied to a fresh detached base checkout and normalized-content-compared
with the exact hardware candidate on 2026-09-08. The sixteenth patch exports
the application surface through the FPGA and composites AM2R's GUI/HUD layer
from a cropped transparent surface, avoiding a full 320x240 CPU upload.
The seventeenth collision-dispatch patch preserves GameMaker's reciprocal event when a
non-solid projectile destroys itself in its own handler. Without this, the
later object-index pass skipped the inactive projectile and AM2R's Alpha
Metroid never received its missile-side damage event. `git diff --check`
passes. The eighteenth patch repairs the spatial-grid activation invariant:
instances deactivated before their first pending insertion now clear their dirty
state, while clean inactive instances retain their cells so AM2R's per-frame
deactivate/reactivate culling has no steady-state grid rebuild cost. Without
this, AM2R's activation-region optimization could reactivate collision solids
which remained permanently absent from `position_meeting` and the other
accelerated collision queries.
The nineteenth patch restores forward instance order for the collision-event
self pass while retaining the precomputed handler table and target-object
buckets. The upstream responder grouping had made self-object order depend on
the lowest collision target subtype: AM2R's beam handled `oSolid` and destroyed
itself before the older `oDoor` instance received the same shot. A controlled
USB-1 A/B test leaves v15's door closed but opens it with this patch, and the
upstream WAD16 object-index collision-order fixture still passes unchanged.
The twentieth patch publishes text and INI changes through a flushed and synced
sibling temporary followed by an atomic rename. An interrupted settings write
can therefore leave either the previous complete file or the new complete file,
but cannot truncate the live `config.ini`. Native Windows, ARM, and actual
USB-2 FAT-storage tests pass; v17 also quarantines and regenerates an already
empty config at startup.
The twenty-first patch distinguishes temporary instance deactivation from
destruction when AM2R snapshots a persistent room, so off-screen exits and
solids survive item-message round trips. It also snapshots mutable legacy-tile
state for persistent rooms and restores the immutable file-authored tiles before
creation code rebuilds a non-persistent room. On USB-1, two rooms retained their
inactive exits across persistent round trips, room 62 rebuilt its runtime
destructible-wall tile, and its restored top exit delivered the character
visibly into room 61 rather than out of bounds.
The twenty-second patch reserves GameMaker surface ID zero as invalid and keeps
application-surface snapshots on the FPGA, fixing AM2R's black missile-upgrade
screen without a synchronous full-frame ARM readback. The twenty-third patch
conservatively discards a render prefix only when a later consecutive layer
group is proven to cover all 320x240 output pixels with opaque samples. Cached
one-bit opacity maps keep the steady-state proof below one millisecond on USB-1
while removing an overwritten full-screen room layer from the FPGA workload.
The twenty-fourth patch preserves application-surface exports requested during
AM2R's Step event and lowers its guarded row-by-row water distortion to the
compact FPGA water command. This fixes the jumping corruption caused when the
old bridge discarded the pre-frame export. The twenty-fifth patch proves exact
unit-step water rows in O(1), aliases their exclusive source to the immutable
published native frame, and fuses the repeated additive 32-pixel underwater
foreground. It also matches the three-buffer native publication ABI used by
the current RBF. A 28.017-second USB-1 underwater jump capture contained 1,681
frames at 60 fps with zero horizontal- or vertical-artifact flags.
The twenty-sixth patch uses the metadata-free POSIX `access(F_OK)` operation for
overlay file-existence checks. This avoids 32-bit ARM `stat()` returning
`EOVERFLOW` for ordinary saves on CIFS shares whose server-provided inode
numbers exceed 32 bits, without changing libc's file ABI. Such files now
participate in the normal save-over-bundle lookup without being recopied.

| Patch | SHA-256 |
| --- | --- |
| `butterscotch-tracked.patch` | `9f9b25ef984f20f050efdf8ece67505ae015e7f768423de0fc4cacc8adf6fbcd` |
| `butterscotch-mister.patch` | `d98bd75cfc32a8a82e47b5819ff6700103a436a17e68015e2045d01586844727` |
| `butterscotch-mister_gpu.patch` | `261865469a7c71af83848b391be24d77bd9783e01f22d9904785d087c9af5095` |
| `butterscotch-sw_renderer.patch` | `cc032a7f5f94c80ac744c51af4389ca4c01d35241b98c1f983a61ce9ca0e356e` |
| `butterscotch-sw_renderer_h.patch` | `b5f5af2b65cec0f5d4aa4effac232ad8020c4e337d4f871f17f32e16dfb78ea5` |
| `butterscotch-savestates.patch` | `eafe1099416994718a2cb5e32524ffdf8a61a8bb8bfd07ec8cdc43e57a3ca356` |
| `butterscotch-persistent-savestates.patch` | `9b1e0dda61d10b2a4a517bc5fb835072515a38bc51e3bda9b8febd515ab5a188` |
| `butterscotch-controls-hints.patch` | `d919a85c2a018f8577e27c79da1690d66eb4f231a15bd9636dd2619492fcae74` |
| `butterscotch-fpga-performance.patch` | `b3b84d90315aaab7d07185feea372cae85722f0766e9efa1287c2532a96db940` |
| `butterscotch-scanout-diagnostics.patch` | `5e85d294e306bf69c24f52baad3010f3b17c4ec34c83661859df50d2143f3b0d` |
| `butterscotch-audio-rate.patch` | `c9561ca2147ba3e44b190ddc335d52dd5901e8bf1d107477a7a9d96b38ffce11` |
| `butterscotch-axis-coverage.patch` | `7b392ba661d2c3aef7f1d76ba0c007e551cef59b9e17e6f5b845554defd19ce2` |
| `butterscotch-renderer-audit.patch` | `3debbd5cbea6338d5259004afd9f7f43d6c5ba2576f583c4eba716a64254cc21` |
| `butterscotch-linux618-framebuffer.patch` | `43d8ee4e5d52475993593a809b0b355063a298a59a9bdbd2289536bc6b529cd7` |
| `butterscotch-surface-revision-map-performance.patch` | `d80ae1aebd7687880825a4875fe3ad0c3e3bd2eee98f12c7a94212e5be885107` |
| `butterscotch-hud-composite.patch` | `2447003ce2c7f9a5d09cbd6fc9363c66b8585ae08c975d4113b07c02e83e41d2` |
| `butterscotch-collision-destroyed-reciprocal.patch` | `4ee0a7f771b88a922f67759584fae92d15f4e9efd7b1d10281da72fc23b48b47` |
| `butterscotch-spatial-grid-activation.patch` | `74eba0f27054c2848b4a7c468c4f87e1cb2deaf2eb089b278e5287018e20a6a5` |
| `butterscotch-collision-self-order.patch` | `28acba0d638c77ff7669ebf8fd6ee2febd6edf93d752e09415b26fa4c1be7208` |
| `butterscotch-atomic-text-writes.patch` | `1595ff91501acec18ccc20eadff1ca6a4ad448ccdb4f12830d9da15c579837c4` |
| `butterscotch-room-state-restoration.patch` | `fb65161ba5b9d2bd935d2a142b14f9c96909b9504c2b6256f45da2bffc485d27` |
| `butterscotch-application-surface-snapshot.patch` | `b4ba265d5dec7be08c92b5b09fea9a60be374302f3ab2ffd025a04d3081c20a4` |
| `butterscotch-opaque-prefix-cull.patch` | `bce817b57163b2ac6661064fc6bacdbcccee20c1493945d11daab2adfc737d58` |
| `butterscotch-water-native-deferred-export.patch` | `fd3c2335f90ab461adba4bc59882c3be19ef158d265aeaebde1c9caba5a920bc` |
| `butterscotch-water-pipeline-performance.patch` | `95d1c60a743ef916f0c779b92eae033b40c2628730ab02b8fb47a9ee2e2dc1c2` |
| `butterscotch-large-file-metadata.patch` | `498ac455036b22d9b3e2061c6c798a46d140830279494abcb517fe102cfff9e0` |

Persistent checkpoints use DMTCP 3.2.0. Apply the MiSTer ARMv7 portability
patch to a clean DMTCP checkout at commit
`bc38d1a3bdfca87905f1a3adfada1e63d64042e5`:

```sh
git apply ../../patches/dmtcp-armv7-mister.patch
```

| Patch | SHA-256 |
| --- | --- |
| `dmtcp-armv7-mister.patch` | `b3c416bd2142602749fba31482129dcd4828b07afac5da034d6526360728fc44` |

The patch applies cleanly and passes `git diff --check`. The distributed
runtime bundle includes the matching ARM binaries and license texts. Its file
plugin uses the explicit 64-bit metadata ABI so 32-bit MiSTer builds can open
ordinary saves stored on CIFS servers with inode numbers wider than `ino_t`.
The plugin link also omits an embedded second C++ runtime; it resolves against
the packaged `libc++_am2r.so`, matching the MiSTer runtime ABI.
