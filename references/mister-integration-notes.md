# MiSTer integration notes for AM2R

These notes adapt relevant concepts from the supplied archive to an ARM-runtime plus FPGA-output project. They are advisory, not a completed design or proof of feasibility. Read exact ports, register maps, protocol sequencing, and configuration behavior from the pinned framework and Linux-side sources used by the implementation.

## Runtime and framework boundaries

The normal MiSTer framework connects a core's `emu` boundary to display, audio, controller, and HPS services. An AM2R ARM process introduces a second software component alongside MiSTer's existing Linux-side program. Investigate launch, process lifetime, controller ownership, menu/OSD interaction, and return-to-menu before promising that selecting an `.rbf` alone will start everything.

Choose one clear owner for each interface. Directly sharing an HPS protocol channel with MiSTer's main process requires an explicit coordination mechanism. The presence of `hps_io`, `ioctl`, or `CONF_STR` does not automatically provide a general userspace game API. Core menu bits, downloads, disk mounting, and runtime IPC are different mechanisms. Add only the ones this project needs.

## Shared frame storage

Define a complete producer/consumer contract between the ARM renderer and FPGA scanout. It must specify buffer allocation, physical/device address translation, format, stride, dimensions, buffer count, ownership transitions, frame sequence, and recovery on exit or reset. Use an allocation/mapping method compatible with the actual MiSTer kernel and memory layout. Derive usable regions from verified configuration; an example physical address in another core is not an allocation for this process.

Ordinary process memory is not automatically suitable for DMA or FPGA access. Establish whether mappings are coherent and, if needed, how CPU cache maintenance, barriers, and device visibility work. A completed rendering call or a C/C++ `volatile` store alone does not establish that the FPGA can see all pixels. Publish frame completion only after the agreed visibility operations; reclaim a buffer only after scanout has relinquished it.

Account for DDR3 arbitration and worst-case latency with a line buffer or FIFO and measurable low-water/underrun handling. Honour busy/wait and read-valid indications from the chosen port. Preserve framework bridge termination across core changes and resets. Treat addresses, burst lengths, alignment, and byte enables as version-specific contracts.

Buffer switching should occur at a defined frame boundary. Specify whether a late frame repeats the last complete frame, queues, or replaces an older pending frame. Keep display timing running independently of runtime stalls. Measure added latency before choosing a buffering depth.

## Native CRT output and HDMI capture

The target is a 320×240 active game image in a valid approximately 60 Hz, 15 kHz progressive analog mode, with a separate HDMI path usable by the Ugreen capture dongle. Active image dimensions alone do not specify a video mode. Select and record pixel cadence, horizontal/vertical totals, porches, sync widths/polarities at each boundary, and the resulting refresh rate. Confirm the exact game build's 4:3 mode and frame pacing.

At the `emu` video boundary, provide correctly timed RGB, pixel enable, blanking/data-enable, and sync signals according to the selected framework. Keep timing running through blanking, and align any RGB pipeline delay with the corresponding control signals. Use frame markers and diagnostic patterns to expose cropping, stale buffers, tearing, and line errors.

Framework framebuffer/scaler facilities may offer an HDMI route without supplying the native analog raster required here. Verify both routes explicitly. A successful HDMI image is not evidence of 240p analog scanout, and a captured 60 FPS recording is not proof of game-frame delivery or latency. Determine whether the design feeds native scanout into the HDMI scaler or uses separate paths, then test consistency.

Target the standard MiSTer framework analog output and preserve normal framework configuration. CRT model and analog IO hardware are not prerequisites for development or reasons to specialize the core. Preserve a known-good setup for recovery. The user clarified that `USB-1` identifies the JTAG USB-Blaster, while the Ugreen HDMI capture dongle is a separate device. SSH details are recorded separately in [hardware.md](../docs/hardware.md). Discover capture device names from the connected tools; do not use `USB-1` as a capture index. Capture hardware may accept only scaled HDMI modes and should not define the CRT raster.

## Audio, input, and persistence

Evaluate the existing Linux/ALSA-to-framework audio path as well as core-provided PCM. Select one route and verify sample format, channel order, sample rate, buffering, underrun behavior, mute/exit behavior, and analog/HDMI output. Relate audio pacing to the chosen game and video clocks; test long sessions for drift.

Evaluate controller events from Linux or framework input, with explicit ownership and pause/menu behavior. Verify simultaneous buttons, reconnects, mapping persistence, and input latency. Avoid delivering one physical button through both routes.

AM2R save files and configuration are runtime files. Preserve their version compatibility and use durable file-write semantics. MiSTer ROM downloads, emulated SD/block devices, and hardware save-state protocols are optional mechanisms rather than default requirements. Keep supplied game data separate from writable saves and test artifacts; agree on deployment paths after inspecting the actual target.

Primary source entry points: [Template framework](https://github.com/MiSTer-devel/Template_MiSTer), [MiSTer Linux-side application](https://github.com/MiSTer-devel/Main_MiSTer), and [MiSTer CRT documentation](https://mister-devel.github.io/MkDocs_MiSTer/advanced/crt/). Pin and inspect the implementation before adopting its contracts.
