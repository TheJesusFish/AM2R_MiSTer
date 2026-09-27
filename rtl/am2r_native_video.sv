//============================================================================
// AM2R native 320x240p scanout
//
// CLK_VIDEO is a dedicated 25 MHz PLL output. CE_PIXEL divides it by four for
// a 6.25 MHz effective pixel clock. Two rasters are available:
//
//   60 Hz (NTSC and PAL60): 398x262, 15.704 kHz / 59.94 Hz
//   50 Hz (PAL):            400x312, 15.625 kHz / 50.08 Hz
//
// NTSC and PAL60 share one raster; they differ only in the composite/S-Video
// colour subcarrier, which Main_MiSTer selects. The 50 Hz raster centres the
// same 240 active lines in PAL's taller visible area. Both remain suitable for
// standard 15 kHz analog displays while retaining a framework-compliant video
// clock for HDMI/ascal.
//
// AM2R is a 60 Hz game. In PAL mode the pacing heartbeat therefore stays on
// the 59.94 Hz raster period from a free-running counter; the GPU's triple
// buffering and the reader's vblank latch drop one published frame in six.
//
// SPDX-License-Identifier: GPL-2.0-or-later
//============================================================================

module am2r_native_video
(
	input              clk,
	input              reset,
	// 0: NTSC, 1: PAL60, 2 or 3: PAL. Sampled only at the end of a raster.
	input       [1:0]  standard,
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
	// The raster currently being generated is the 50 Hz PAL raster.
	output reg         pal = 0,
	output reg  [7:0]  r = 0,
	output reg  [7:0]  g = 0,
	output reg  [7:0]  b = 0
);

	localparam integer H_ACTIVE = 320;
	localparam integer H_FP = 16;
	localparam integer H_SYNC = 30;
	localparam integer V_ACTIVE = 240;
	localparam integer V_SYNC = 3;
	localparam integer CE_DIV = 4;

	localparam integer H_TOTAL_60 = 398;
	localparam integer V_FP_60 = 5;
	localparam integer V_TOTAL_60 = 262;

	// 6.25 MHz / 400 is exactly PAL's 15.625 kHz line rate. The 50 extra
	// lines are split around the 60 Hz porches so the image stays centred.
	localparam integer H_TOTAL_50 = 400;
	localparam integer V_FP_50 = 30;
	localparam integer V_TOTAL_50 = 312;

	// Clocks in one 59.94 Hz raster; the runner's timer fallback uses the
	// same 16.684160 ms period.
	localparam integer PACE_PERIOD_50 = CE_DIV * H_TOTAL_60 * V_TOTAL_60;

	reg [3:0] ce_count = 0;
	reg [8:0] h_count = 0;
	reg [8:0] v_count = 0;
	reg [18:0] pace_count = 0;

	wire [8:0] h_total = pal ? H_TOTAL_50[8:0] : H_TOTAL_60[8:0];
	wire [8:0] v_total = pal ? V_TOTAL_50[8:0] : V_TOTAL_60[8:0];
	wire [8:0] v_sync_start = V_ACTIVE + (pal ? V_FP_50 : V_FP_60);

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
			pace_count <= 0;
			pal <= standard[1];
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
		end else begin
			// The PAL heartbeat is independent of the raster, so it keeps
			// counting across CE phases and standard changes.
			if (!pal || pace_count == PACE_PERIOD_50 - 1)
				pace_count <= 0;
			else
				pace_count <= pace_count + 1'b1;
			if (pal && pace_count == PACE_PERIOD_50 - 1)
				pace_tick <= 1;

			if (ce_count == CE_DIV - 1) begin
				ce_count <= 0;
				ce_pix <= 1;
				hblank <= (h_count >= H_ACTIVE);
				hsync <= (h_count >= H_ACTIVE + H_FP) &&
				         (h_count < H_ACTIVE + H_FP + H_SYNC);
				vblank <= (v_count >= V_ACTIVE);
				vsync <= (v_count >= v_sync_start) &&
				         (v_count < v_sync_start + V_SYNC);

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

				if (h_count == h_total - 1'b1) begin
					// Start the next game tick 48 scanlines (3.06 ms) before
					// vertical blank. CPU drawing and FPGA presentation then
					// finish ahead of the publication edge instead of
					// straddling it. The cadence remains exactly one pulse per
					// 60 Hz raster.
					if (!pal && v_count == V_ACTIVE - 49)
						pace_tick <= 1;
					h_count <= 0;
					if (v_count == v_total - 1'b1) begin
						v_count <= 0;
						// Change rasters only between complete frames.
						pal <= standard[1];
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
	end

endmodule
