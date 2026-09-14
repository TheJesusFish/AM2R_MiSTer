# Project-specific ARM/HPS code

Runtime integration, launcher/IPC, platform input, audio and save code belongs
here. See [architecture](../../docs/architecture.md).

`framebuffer_probe.py` is the first executable HPS-to-FPGA contract test. It
writes a deterministic 320x240 XRGB8888 image into MiSTer's reserved DDR3
framebuffer at `0x22001000`, which the AM2R core exposes through its core-owned
framebuffer interface. It deliberately has no third-party Python dependencies
so it can run with the stock MiSTer `python3`.
