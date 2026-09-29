//============================================================================
// AM2R HDMI framebuffer source
//
// Feeds the framework HDMI scaler (ascal) directly from the GPU's published
// native XRGB8888 buffers through the core MISTER_FB interface, so HDMI shows
// the unadjusted 320x240 image while analog keeps the core's CRT position and
// horizontal-scale pipeline.
//
// ascal copies FB_BASE at the falling edge of HDMI VSync, which follows the
// start of HDMI vertical blank (FB_VBL). At each FB_VBL rising edge this block
// selects the latest published buffer. The previous selection stays
// protected until FB_VBL falls, by which time ascal has switched to the new
// base; the current selection stays protected for the whole HDMI frame.
//
// SPDX-License-Identifier: GPL-2.0-or-later
//============================================================================

module am2r_hdmi_fb
(
	input              clk,
	input              reset,
	input              fb_vbl,           // HDMI vertical blank, CLK_VIDEO domain
	input       [31:0] native_frame,     // publication count, clk domain
	input        [1:0] native_buffer,    // latest published buffer, clk domain
	output reg  [31:0] fb_base = 0,
	output reg         fb_force_blank = 1,
	output       [3:0] protect
);

	localparam [31:0] BUF0_BASE = 32'h3a00_0100;
	localparam [31:0] BUF_BYTES = 32'd320 * 32'd240 * 32'd4;

	reg [2:0] vbl_sync = 0;
	wire vbl_rise = vbl_sync[1] & ~vbl_sync[2];
	wire vbl_fall = ~vbl_sync[1] & vbl_sync[2];

	reg        cur_valid = 0;
	reg  [1:0] cur = 0;
	reg        prev_valid = 0;
	reg  [1:0] prev = 0;

	always @(posedge clk) begin
		if (reset) begin
			vbl_sync <= 0;
			cur_valid <= 0;
			prev_valid <= 0;
			cur <= 0;
			prev <= 0;
			fb_base <= BUF0_BASE;
			fb_force_blank <= 1;
		end else begin
			vbl_sync <= {vbl_sync[1:0], fb_vbl};
			if (vbl_rise && native_frame != 0) begin
				prev <= cur;
				prev_valid <= cur_valid;
				cur <= native_buffer;
				cur_valid <= 1;
				fb_base <= BUF0_BASE + native_buffer * BUF_BYTES;
			end else if (vbl_fall) begin
				prev_valid <= 0;
				// Show the frame only once ascal has latched a published base.
				fb_force_blank <= ~cur_valid;
			end
		end
	end

	assign protect = (cur_valid ? 4'b0001 << cur : 4'b0000) |
	                 (prev_valid ? 4'b0001 << prev : 4'b0000);

endmodule
