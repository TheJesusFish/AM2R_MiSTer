//============================================================================
// AM2R core-side integration for MiSTer-CRT-Adjust.
//
// The upstream stages remain unmodified in crt_vsize.sv and crt_adjust.sv.
// This wrapper supplies AM2R's read clock-enable and preserves a zero-latency,
// bit-exact native bypass while CRT Adjust is disabled.
//
// MiSTer-CRT-Adjust revision: c682de9f4acc61d8f4c7779efb48149d3baa3a8e
// SPDX-License-Identifier: GPL-3.0-or-later
//============================================================================

module am2r_crt_pipeline #(
	parameter integer H_TOTAL          = 398,
	parameter integer V_TOTAL          = 262,
	parameter integer PIXEL_CLK_CYCLES = 8,
	parameter integer RING_LINES       = 46,
	parameter integer LINE_PX          = 384,
	parameter integer HPOS_MODE        = 0
)(
	input                    clk,
	input                    reset,
	input                    active,
	input signed [4:0]       hsize,
	input signed [8:0]       hposition,
	input signed [5:0]       vshift,
	input signed [5:0]       vsize,
	input                    cabinet_mode,
	input                    ce_pix_in,
	input       [7:0]        r_in,
	input       [7:0]        g_in,
	input       [7:0]        b_in,
	input                    hs_in,
	input                    hblank_in,
	input                    vs_in,
	input                    vblank_in,
	output                   ce_pix_out,
	output      [7:0]        r_out,
	output      [7:0]        g_out,
	output      [7:0]        b_out,
	output                   hs_out,
	output                   de_out,
	output                   hblank_out,
	output                   vs_out,
	output                   vblank_out
);

	localparam integer READ_BASE_Q = PIXEL_CLK_CYCLES * 4;
	wire pipeline_on = active && !reset;

	// CRT V-Size is the first stage. Passing its regenerated CE and true
	// vertical blank downstream is required for both PVM and Cabinet modes.
	wire [7:0] vz_r;
	wire [7:0] vz_g;
	wire [7:0] vz_b;
	wire       vz_hs;
	wire       vz_vs;
	wire       vz_de;
	wire       vz_vb;
	wire       vz_ce;

	crt_vsize #(
		.RING_LINES(RING_LINES),
		.LINE_PX   (LINE_PX)
	) vertical_size (
		.clk       (clk),
		.pxl_cen   (ce_pix_in),
		.active    (pipeline_on),
		.tube_mode (cabinet_mode),
		.vsize     (vsize),
		.r_in      (r_in),
		.g_in      (g_in),
		.b_in      (b_in),
		.hs_in     (hs_in),
		.vs_in     (vs_in),
		.de_in     (~(hblank_in | vblank_in)),
		.vb_in     (vblank_in),
		.r_out     (vz_r),
		.g_out     (vz_g),
		.b_out     (vz_b),
		.hs_out    (vz_hs),
		.vs_out    (vz_vs),
		.de_out    (vz_de),
		.vb_out    (vz_vb),
		.ce_out    (vz_ce)
	);

	// CRT Adjust reads the line buffer at (base + H-size) quarter-clocks.
	// Its own horizontal reference resets this generator so position and size
	// changes cannot accumulate a line-to-line phase error.
	wire hs_ref;
	reg  hs_ref_d = 0;
	wire hs_ref_rise = hs_ref && !hs_ref_d;
	wire [7:0] rd_period = READ_BASE_Q[7:0] + {{3{hsize[4]}}, hsize};
	reg  [7:0] rd_acc = 0;
	wire rd_tick = (rd_acc + 8'd4) >= rd_period;

	always @(posedge clk) begin
		if (reset) begin
			hs_ref_d <= 0;
			rd_acc <= 0;
		end else begin
			hs_ref_d <= hs_ref;
			if (hs_ref_rise)
				rd_acc <= 0;
			else if (rd_tick)
				rd_acc <= rd_acc + 8'd4 - rd_period;
			else
				rd_acc <= rd_acc + 8'd4;
		end
	end

	wire rd_ce = pipeline_on ? rd_tick : vz_ce;
	wire [7:0] adjusted_r;
	wire [7:0] adjusted_g;
	wire [7:0] adjusted_b;
	wire       adjusted_hs;
	wire       adjusted_vs;
	wire       adjusted_hblank;
	wire       adjusted_vblank;

	crt_adjust #(
		.VTOTAL   (V_TOTAL),
		.HTOTAL   (H_TOTAL),
		.HPOS_MODE(HPOS_MODE)
	) geometry_adjust (
		.clk        (clk),
		.pxl_cen    (vz_ce),
		.pxl2_cen   (rd_ce),
		.active     (pipeline_on),
		.hsize      (hsize),
		.hoffset    (hposition),
		.voffset    (vshift),
		.r_in       (vz_r),
		.g_in       (vz_g),
		.b_in       (vz_b),
		.hs_in      (vz_hs),
		.vs_in      (vz_vs),
		.hb_in      (~vz_de),
		.vb_in      (vz_vb),
		.r_out      (adjusted_r),
		.g_out      (adjusted_g),
		.b_out      (adjusted_b),
		.hs_out     (adjusted_hs),
		.vs_out     (adjusted_vs),
		.hb_out     (adjusted_hblank),
		.vb_out     (adjusted_vblank),
		.hs_ref_out (hs_ref)
	);

	// Keep the framework/OSD display window anchored to the incoming active
	// line, as required by the upstream core-side integration.  The adjusted
	// line-buffer gate supplies only the trailing edge.  Feeding that gate
	// directly to VGA_DE makes MiSTer's scaler treat a live H-size change as
	// multiple horizontal slices even though the buffered pixels are ordered.
	reg vz_de_d = 0;
	wire vz_de_rise = vz_de && !vz_de_d;
	always @(posedge clk) begin
		if (reset)
			vz_de_d <= 0;
		else if (vz_ce)
			vz_de_d <= vz_de;
	end

	wire adjusted_active = ~adjusted_hblank;
	reg adjusted_active_d = 0;
	wire adjusted_active_fall = adjusted_active_d && !adjusted_active;
	always @(posedge clk) begin
		if (reset)
			adjusted_active_d <= 0;
		else if (rd_ce)
			adjusted_active_d <= adjusted_active;
	end

	reg de_osd = 0;
	always @(posedge clk) begin
		if (reset || !pipeline_on)
			de_osd <= 0;
		else if (vz_de_rise)
			de_osd <= 1;
		else if (adjusted_active_fall)
			de_osd <= 0;
	end

	// The upstream stages are registered. Bypass them at the wrapper boundary
	// so Off retains AM2R's original clock, RGB, blanking, sync and latency.
	assign ce_pix_out  = pipeline_on ? rd_ce             : ce_pix_in;
	assign r_out       = pipeline_on ? adjusted_r        : r_in;
	assign g_out       = pipeline_on ? adjusted_g        : g_in;
	assign b_out       = pipeline_on ? adjusted_b        : b_in;
	assign hs_out      = pipeline_on ? adjusted_hs       : hs_in;
	// The union retains crt_vsize's final buffered row: that row can become
	// active downstream before the upstream DE edge used to open de_osd.
	assign de_out      = pipeline_on ? (de_osd | adjusted_active)
	                                  : ~(hblank_in | vblank_in);
	assign hblank_out  = pipeline_on ? adjusted_hblank   : hblank_in;
	assign vs_out      = pipeline_on ? adjusted_vs       : vs_in;
	assign vblank_out  = pipeline_on ? adjusted_vblank   : vblank_in;

endmodule
