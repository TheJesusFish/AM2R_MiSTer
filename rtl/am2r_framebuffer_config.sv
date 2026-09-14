//============================================================================
// AM2R ARM framebuffer contract
//
// The HPS-visible XRGB8888 window remains configured for runner readback and
// diagnostics, but display ownership stays with the FPGA native scanout.
//
// SPDX-License-Identifier: GPL-2.0-or-later
//============================================================================

module am2r_framebuffer_config
(
	input         diagnostic,
	output        fb_en,
	output  [4:0] fb_format,
	output [11:0] fb_width,
	output [11:0] fb_height,
	output [31:0] fb_base,
	output [13:0] fb_stride,
	output        fb_force_blank
);

	// MiSTer reserves DDR3 at 0x22000000 for HPS video.  The first 4 KiB are
	// intentionally skipped, matching Main_MiSTer's Linux framebuffer layout.
	localparam [31:0] FRAMEBUFFER_BASE = 32'h2200_1000;

	assign fb_en          = 1'b0;
	assign fb_format      = 5'b1_0110; // BGR/RGB byte swap + 32-bit XRGB8888
	assign fb_width       = 12'd320;
	assign fb_height      = 12'd240;
	assign fb_base        = FRAMEBUFFER_BASE;
	assign fb_stride      = 14'd1280;
	assign fb_force_blank = 1'b0;

endmodule
