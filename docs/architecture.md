# Implemented architecture

Start with [bootstrap.md](../bootstrap.md). Hardware constraints and source
references live in [hardware.md](hardware.md) and [sources.md](sources.md). The
architecture below is implemented and validated on the USB-1 DE10-Nano. The
game is **AM2R**; the workspace directory is intentionally named
`A2MR_MiSTer`.

## Intended experience and boundaries

The target is local AM2R gameplay on a MiSTer DE10-Nano, with a core-style
launch experience and standard MiSTer framework analog output. The implemented
video path presents the game's native 320×240 progressive 4:3 picture at 60 Hz;
its raster/framebuffer contracts pass simulation and HDMI hardware capture.
Physical analog sync/geometry on the user's CRT remains an acceptance test. The
core does not depend on a particular CRT model or analog IO-board variant.

This is a hybrid core, not a pure FPGA reimplementation. The ARM HPS executes
the patched Butterscotch VM, game logic, asset decoding, input, saves, and
audio. The FPGA implements the hot rendering operations and presentation. That
split keeps GameMaker semantics on the CPU while meeting the native 60 Hz frame
budget in representative gameplay.

## Source, runtime, and assets are separate inputs

Public reconstructed GML source exists for AM2R Community Updates 1.5.x. It does not provide the proprietary GameMaker runner or the complete game assets. Access to any other development branch must be established separately. Pin the user's exact game version and data format before choosing a runtime.

An open runner may read existing compiled game data without another decompilation or a GML rebuild. Changing and recompiling GML would require a compatible build toolchain and reconstruction inputs. Keep those workflows separate. Record hashes and local provenance of user-supplied game files; do not place game payloads in this bootstrap repository.

## Runtime candidates

**Butterscotch is the selected runtime.** The validated port pins commit
`7c2503efc25f20dddb9ba7b7cf7b46fd4f63ba08` and carries the reproducible patch
set under `patches/`. Its VM/WAD 14 path, file/save builtins, software-renderer
bridge, miniaudio backend, and MiSTer platform backend have been exercised with
the user's exact AM2R 1.1 data on the DE10-Nano. Compatibility outside the
scripted opening route remains an explicit user-test boundary.

**gmloader/droidports is an alternate route.** AM2R has run on other ARM Linux devices through its Android GameMaker runner. gmloader-next supports an ARM hard-float target and loads the APK's native runner, but its graphics path requests OpenGL ES 2.0. MiSTer does not supply a conventional GLES GPU. This route therefore needs a compatible software renderer or substantial graphics adaptation, plus a compatible ARM runner and Linux dependencies. Success on handhelds does not establish MiSTer performance.

## Component boundaries

```text
User-supplied AM2R.zip → validated RAM extraction
                    |
HPS frontend: normal core lifecycle, OSD, DMTCP slot coordinator
                    |
HPS runner: Butterscotch VM, game, input, persistent saves,
            audio, CPU texture shadows and upload
                    |
double-buffered 64-byte GPU descriptors + texture pool in reserved HPS DDR
                    |
FPGA GPU: clear/fill, axis + affine texture, tint/gradient,
          normal + additive alpha, on-chip 320×240 RGBA target
                    |
FPGA DDR present DMA → MiSTer local framebuffer → framework video
HPS ALSA audio ───────────────────────────────→ framework audio

DMTCP image + metadata ──atomic commit──→ /media/fat/savestates/AM2R
```

The command ABI is implemented in `rtl/am2r_gpu.sv` and
`third_party/Butterscotch/src/backends/mister_gpu.h`:

- control block `0x23ff0000` (`A2GP` magic);
- alternating 64 KiB descriptor buffers at `0x23fe0000` and `0x23fd0000`;
- texture pool beginning at `0x24000000`;
- three 320×240 XRGB8888 native presentation buffers beginning at
  `0x3a000100`, stride 1,280;
- opcodes: end/present, clear, axis blit, solid/vertical-gradient fill, and
  affine blit.

Textures are uploaded lazily in 4 MiB pages. Mutable GameMaker surface uploads
alternate two DDR allocations so the ARM can update frame N+1 while the FPGA
still samples frame N; the HPS copy remains available for the rare
application-surface readbacks. At each frame boundary the software bridge
retires the preceding job and submits the ordered list it built concurrently.
The GPU renders into banked M10K memory and performs the final coherent DDR
burst transfer. There is no GLES dependency.

The runner maps reserved physical memory through `/dev/mem`, publishes command
descriptors and texture bytes, issues ARM memory barriers, then advances the
producer sequence. Command and dynamic-texture double buffering overlaps ARM
game/render preparation with FPGA rendering. It waits before reusing an
in-flight allocation, publishing the next one-slot mailbox job, performing a
surface readback, falling back to CPU presentation, or exiting. Linux RAM ends
below the reserved regions on the validated target. The plug-in SDRAM module is
not used.

The normal-core frontend is `/media/fat/MiSTer_AM2R`, selected by the `[AM2R]`
`main` mapping in `MiSTer.ini`. It validates the 45 required members in
`/media/fat/games/am2r/AM2R.zip`, checks CRCs while extracting them to RAM,
starts the runner, services MiSTer OSD save/load triggers, and returns to the
stock menu after the runner exits.

Save states are DMTCP process checkpoints retained in four disk slots under
`/media/fat/savestates/AM2R`. Before a checkpoint the runner waits for FPGA GPU
completion, tears down the miniaudio worker/device, and disconnects its
frontend socket. The wrapper waits for the coordinator to finish the image,
then atomically commits both the uncompressed checkpoint and exact-build
metadata. Format-3 metadata includes both the frontend build identifier and a
CRC32 of the installed runner. Because DMTCP restores executable memory as well
as game data, the wrapper rejects either mismatch before terminating the live
game; this prevents an older checkpoint from silently replacing newer runtime
fixes. An interrupted write cannot replace the previous good slot.

On restore, DMTCP reconstructs the selected GameMaker/runner process. The
runner re-uploads every CPU-shadowed texture to shared DDR, reconstructs the
audio engine and active sounds, and reconnects to the current frontend through
a resume marker and Unix datagram socket. This keeps a state reusable across
core exit/relaunch as well as within one session. Empty slots leave the active
game running; corrupt or incompatible states are rejected and recover to a new
game instead of stranding the core. Normal AM2R save files remain separately
persistent under `/media/fat/saves/AM2R` on the SD card.

Game stepping is paced at 60 Hz and the GPU runs at 88 MHz. Its paired-pixel
fast path resolves two adjacent opaque or transparent pixels per common
textured draw cycle. Completed frames rotate among three HPS-DDR buffers so the
GPU can avoid the immutable buffer currently in scanout without waiting a full
raster; scanout still adopts only the newest complete buffer at a frame
boundary. It prefetches each line through a show-ahead dual-clock FIFO. A
dedicated 50 MHz video PLL with pixel enable divided by eight produces a
398×262 total raster: 320×240 active, 15.704 kHz and 59.94 Hz. The framework
remains on its separate 20 MHz system clock. `VGA_SCALER=0` keeps the core-owned
15 kHz raster on analog VGA while the framework can process the same timing for
HDMI. ALSA feeds MiSTer's normal
audio mixer independently. Input is sampled from `MiSTer virtual input` at the
runtime event boundary. The HPS frontend traps normal/error exits and reloads
`/media/fat/menu.rbf`; Weapon Select+Start requests a normal runner exit. The
bridge assigns all 70 renderer hooks. Operations that cannot use the FPGA fast
path explicitly synchronize/fall back to the CPU shadow and increment a
per-reason counter; only the documented optional tile hook uses its ordinary
sprite-part fallback.

An on-hardware paired-marker test isolates the renderer/publication portion of
latency: ten of twelve sampled input edges reached the normal rendered marker
one captured frame after the immediate scanout marker, and two took two frames,
for a mean of 1.17 frames (about 19.4 ms). The game still samples input once per
60 Hz step, and controller polling, game animation/subpixel response, raster
position, and the display add latency outside that measurement. Native scanout
adopts only a completed buffer at a frame boundary; removing that boundary
would trade latency variance for tearing. There is no frame scaler queue,
CRT-safe frame buffer, or intentional input delay in the core. The optional
core-side CRT-Adjust pipeline provides horizontal geometry and two experimental
vertical-size methods; it is an exact zero-latency bypass when disabled. A
controller-to-photodiode test is still required to
establish end-to-end latency.

The pinned framework tree under `sys/` is not patched. Quartus 17 expanded the
framework's behavioral OSD arrays into registers after the vertical resizer was
added, exceeding the Cyclone V logic budget. The project therefore selects the
interface-identical core-local `rtl/am2r_osd.v` through `am2r_sys.tcl` and
`am2r_sys.qip`; only the OSD storage is replaced by an explicit dual-clock M10K.
The project also constrains ASCal's existing line, coefficient, and palette
arrays to M10K RAM. Without those constraints Quartus 17 implements the three
coefficient arrays alone as 30,720 flip-flops once CRT-Adjust raises overall
M10K use. These core-local build choices change neither the framework split nor
the CRT-Adjust video stream.

## Evidence policy

Maintain compatibility and performance evidence for the exact runtime revision, game data hashes, toolchain, hardware, and settings. Desktop correctness, an HPS CPU lower bound, synthetic FPGA scanout, and a complete playable port are distinct results. HDMI capture is useful for image and pacing analysis; it cannot prove analog CRT sync, geometry, signal quality, or display latency. See [milestones.md](milestones.md) for gates.
