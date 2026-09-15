//============================================================================
// AM2R native 320x240p scanout
//
// CLK_VIDEO is a dedicated 50 MHz PLL output. CE_PIXEL divides it by eight
// for a 6.25 MHz effective pixel clock. The 398x262 raster is
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
	input              diagnostic,
	input       [1:0]  pattern,
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
	localparam integer CE_DIV = 8;

	reg [3:0] ce_count = 0;
	reg [8:0] h_count = 0;
	reg [8:0] v_count = 0;
	reg [1:0] pattern_latched = 0;
	reg       diagnostic_meta = 0;
	reg       diagnostic_sync = 0;
	reg [1:0] pattern_meta = 0;
	reg [1:0] pattern_sync = 0;

	wire active = (h_count < H_ACTIVE) && (v_count < V_ACTIVE);
	always @(posedge clk) begin
		diagnostic_meta <= diagnostic;
		diagnostic_sync <= diagnostic_meta;
		pattern_meta <= pattern;
		pattern_sync <= pattern_meta;
		ce_pix <= 0;
		new_frame <= 0;
		new_line <= 0;
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
			pattern_latched <= pattern_sync;
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
			end else if (!diagnostic_sync && frame_ready) begin
				r <= frame_r;
				g <= frame_g;
				b <= frame_b;
			end else begin
				case (pattern_latched)
					2'd0: begin
						if (h_count < 40)       {r,g,b} <= 24'hffffff;
						else if (h_count < 80)  {r,g,b} <= 24'hffff00;
						else if (h_count < 120) {r,g,b} <= 24'h00ffff;
						else if (h_count < 160) {r,g,b} <= 24'h00ff00;
						else if (h_count < 200) {r,g,b} <= 24'hff00ff;
						else if (h_count < 240) {r,g,b} <= 24'hff0000;
						else if (h_count < 280) {r,g,b} <= 24'h0000ff;
						else                    {r,g,b} <= 24'h101010;
					end
					2'd1: begin
						{r,g,b} <= ((h_count[4:0] == 0) || (v_count[4:0] == 0)) ?
							24'h406080 : 24'h081018;
						if ((h_count == 160) || (v_count == 120))
							{r,g,b} <= 24'hffffff;
					end
					2'd2: begin
						r <= {h_count[7:0]};
						g <= v_count[7:0];
						b <= h_count[7:0] ^ v_count[7:0];
					end
					default: {r,g,b} <= 24'h000000;
				endcase
			end

			if (h_count == H_TOTAL - 1) begin
				h_count <= 0;
				if (v_count == V_TOTAL - 1) begin
					v_count <= 0;
					pattern_latched <= pattern_sync;
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
