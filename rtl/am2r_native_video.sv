//============================================================================
// AM2R native 320x240p scanout
//
// CLK_VIDEO is a dedicated 25 MHz PLL output. CE_PIXEL divides it by four for
// a 6.25 MHz effective pixel clock. The 398x262 raster is
// 15.704 kHz / 59.94 Hz,
// suitable for standard 15 kHz analog displays while retaining a framework-
// compliant video clock for HDMI/ascal.
//
// SPDX-License-Identifier: GPL-2.0-or-later
//============================================================================

module am2r_native_video
(
	input              clk,
	input              reset,
	input              frame_ready,
	input       [7:0]  frame_r,
	input       [7:0]  frame_g,
	input       [7:0]  frame_b,
	output reg         ce_pix = 0,
	output reg         hblank = 0,
	output reg         hsync = 0,
	output reg         vblank = 0,
	output reg         vsync = 0,
	output reg         new_frame = 0,
	output reg         new_line = 0,
	output reg         pace_tick = 0,
	output reg  [7:0]  r = 0,
	output reg  [7:0]  g = 0,
	output reg  [7:0]  b = 0
);

	localparam integer H_ACTIVE = 320;
	localparam integer H_FP = 16;
	localparam integer H_SYNC = 30;
	localparam integer H_TOTAL = 398;
	localparam integer V_ACTIVE = 240;
	localparam integer V_FP = 5;
	localparam integer V_SYNC = 3;
	localparam integer V_TOTAL = 262;
	localparam integer CE_DIV = 4;

	reg [3:0] ce_count = 0;
	reg [8:0] h_count = 0;
	reg [8:0] v_count = 0;

	wire active = (h_count < H_ACTIVE) && (v_count < V_ACTIVE);
	always @(posedge clk) begin
		ce_pix <= 0;
		new_frame <= 0;
		new_line <= 0;
		pace_tick <= 0;
		if (reset) begin
			ce_count <= 0;
			h_count <= 0;
			v_count <= 0;
			hblank <= 0;
			hsync <= 0;
			vblank <= 0;
			vsync <= 0;
			new_frame <= 0;
			new_line <= 0;
			pace_tick <= 0;
			r <= 0;
			g <= 0;
			b <= 0;
		end else if (ce_count == CE_DIV - 1) begin
			ce_count <= 0;
			ce_pix <= 1;
			hblank <= (h_count >= H_ACTIVE);
			hsync <= (h_count >= H_ACTIVE + H_FP) &&
			         (h_count < H_ACTIVE + H_FP + H_SYNC);
			vblank <= (v_count >= V_ACTIVE);
			vsync <= (v_count >= V_ACTIVE + V_FP) &&
			         (v_count < V_ACTIVE + V_FP + V_SYNC);

			if (!active) begin
				r <= 0;
				g <= 0;
				b <= 0;
			end else if (frame_ready) begin
				r <= frame_r;
				g <= frame_g;
				b <= frame_b;
			end else begin
				{r,g,b} <= 24'h000000;
			end

			if (h_count == H_TOTAL - 1) begin
				// Start the next game tick 48 scanlines (3.06 ms) before
				// vertical blank. CPU drawing and FPGA presentation then finish
				// ahead of the publication edge instead of straddling it. The
				// cadence remains exactly one pulse per native raster.
				if (v_count == V_ACTIVE - 49)
					pace_tick <= 1;
				h_count <= 0;
				if (v_count == V_TOTAL - 1) begin
					v_count <= 0;
				end else begin
					v_count <= v_count + 1'b1;
				end
				if (v_count == V_ACTIVE - 1)
					new_frame <= 1;
			end else begin
				h_count <= h_count + 1'b1;
			end

			if (h_count == H_ACTIVE - 1)
				new_line <= 1;
		end else begin
			ce_count <= ce_count + 1'b1;
		end
	end

endmodule
