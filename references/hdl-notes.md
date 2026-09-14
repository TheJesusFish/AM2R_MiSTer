# HDL notes for AM2R on MiSTer

These are project-specific adaptations of the supplied bootstrap archive, not authoritative specifications. They apply when implementing or reviewing this project's FPGA scanout, buffering, and any later graphics accelerator. Verify interface details against the selected, pinned MiSTer source and the documentation for the Quartus version actually used. The project bootstrap and user instructions define scope; imported hardware-emulation policies do not.

## Architecture and resource use

AM2R is a software game. The proposed core uses the ARM for the runtime and the FPGA for display and selected services. There is no vintage AM2R CPU or chip architecture to reproduce. Judge FPGA blocks by observable game correctness, display deadlines, interface contracts, resource use, and measured performance. Original-chip cycle matching and era-specific restrictions on DSPs or parallelism are inapplicable.

Before implementing a block, state its inputs, outputs, clock domains, throughput, latency, storage capacity, backpressure behavior, and reset behavior. A scanout deadline is different from the runtime's average frame rate: a complete frame arriving late must not stall video timing.

Use explicit widths and signedness, intentional truncation, bounded loops, and registered or pipelined arithmetic where useful. Account for the hardware inferred by multiplication, division, variable shifts, and large multiplexers. DSP acceleration is permitted when it helps the actual workload. Confirm resource mapping in synthesis and fitter reports. The archive confuses logic elements with ALMs in its device budget; obtain resource totals from the selected device and actual reports instead of copying those numbers.

## Sequential logic and clocks

Use one procedural driver per register, nonblocking assignments for sequential state, and complete assignments in combinational blocks. Avoid inferred latches and logic-generated clocks. Express lower-rate work using clock enables where practical; use supported clock-control or PLL resources for actual clock generation.

Document reset assertion and release in every clock domain. Synchronize reset release as required by the implementation, and account for PLL lock and domains whose clocks can stop. Core reset, bridge reset, runtime exit, and loading another `.rbf` are distinct events. Reset must leave memory transactions, FIFO ownership, and audio/video outputs in defined states.

## Clock-domain crossings and flow control

Use an appropriate synchronizer for a stable single-bit level. A short pulse can disappear between clocks; use a toggle, request/acknowledge handshake, or another proven pulse-transfer scheme. Transfer multi-bit descriptors and counters through a coherent handshake or an asynchronous FIFO. Independently synchronizing each bit does not make a coherent word.

For a ready/valid interface, a transfer occurs on the clock edge with both signals asserted. Retain valid and payload while stalled. Include all transfer metadata in the payload stability contract. Add FIFOs or registered stages where they solve a demonstrated throughput or timing problem, and verify capacity, overflow, underflow, reset, and simultaneous read/write behavior.

Cross-clock FIFOs need proven pointer synchronization and correct constraints. Their CDC constraints are part of the design, not optional report suppression. Separate frame-complete notification from pixel storage ownership so that a newly synchronized flag cannot expose an unfinished frame.

## Memories and timing evidence

Choose register, MLAB, or M10K storage from capacity and access requirements. Specify read latency, read-during-write behavior, byte enables, and initialization explicitly. Check that simulation behavior matches the chosen inference template or primitive. Line buffers and pixel FIFOs should fit the real display schedule; a larger register array is not a substitute for verifying memory inference.

Use the selected framework's DDR3 handshake and bridge protection logic. DDR3 has variable latency and competing clients. Exercise stalls and delayed read responses in simulation; preserve safe handling of in-flight transactions across reset.

Maintain real clock, generated-clock, I/O, and CDC constraints. Treat false paths and multicycle paths as claims that require an architectural explanation. Report setup and hold results and unresolved constraints. A generated `.rbf` does not establish that timing passed.

For a meaningful build result, retain the source revision and local patch state, framework revision, tool version, build command, configuration, reports, and output hash. For targeted RTL tests, retain the test command and result. Useful scanout tests check active dimensions, blanking, sync cadence, buffer handoff, underrun recovery, and reset during traffic. Broader testing should follow new changes or unresolved concerns.

Primary starting points: [MiSTer Template](https://github.com/MiSTer-devel/Template_MiSTer), [Altera design recommendations](https://docs.altera.com/r/docs/683323/current), and [Cyclone V documentation](https://docs.altera.com/r/docs/683375/current/cyclone-v-device-handbook-volume-1-device-interfaces-and-integration). Record the actual source revisions in the project's source register.
