//============================================================================
// AM2R core-local CRT adjustment pipeline
//
// Sync positioning follows JTFrame; horizontal scaling follows the analog
// H-Scale path in Arcade-IGSPGM_MiSTer. The upstream MiSTer sys/ tree remains
// untouched. Adjustments latch only at vertical blank boundaries.
// SPDX-License-Identifier: GPL-3.0-or-later
//============================================================================

module am2r_crt_video(
	input                    clk,
	input                    reset,
	input                    ce_pix_in,
	input       [7:0]        r_in,
	input       [7:0]        g_in,
	input       [7:0]        b_in,
	input                    hs_in,
	input                    hblank_in,
	input                    vs_in,
	input                    vblank_in,
	input signed [4:0]       h_position,
	input signed [4:0]       v_position,
	input                    hscale_enable,
	input signed [4:0]       hscale,
	output                   ce_pix_out,
	output      [7:0]        r_out,
	output      [7:0]        g_out,
	output      [7:0]        b_out,
	output                   hs_out,
	output                   hblank_out,
	output                   vs_out,
	output                   vblank_out,
	output                   hscale_active
);

	reg vblank_d = 0;
	reg signed [4:0] h_position_latched = 0;
	reg signed [4:0] v_position_latched = 0;
	always @(posedge clk) begin
		if (reset) begin
			vblank_d <= 0;
			h_position_latched <= 0;
			v_position_latched <= 0;
		end else begin
			vblank_d <= vblank_in;
			if (vblank_in && !vblank_d) begin
				h_position_latched <= h_position;
				v_position_latched <= v_position;
			end
		end
	end

	wire positioned_hs;
	wire positioned_vs;
	// jtframe_resync regenerates HSync one pixel later and VSync one line
	// earlier than this core's direct registered pulses at zero offset. Fold
	// those fixed pipeline phases into the requested signed displacement so
	// each menu step moves the output by exactly one source pixel/line.
	wire signed [4:0] resync_h_offset = -h_position_latched - 5'sd1;
	wire signed [4:0] resync_v_offset = -v_position_latched + 5'sd1;
	am2r_crt_resync #(.BITS(5), .CNTW(10)) position_adjust(
		.clk(clk),
		.reset(reset),
		.ce_pix(ce_pix_in),
		.hs_in(hs_in),
		.vs_in(vs_in),
		.hvisible(!hblank_in),
		.vvisible(!vblank_in),
		.h_offset(resync_h_offset),
		.v_offset(resync_v_offset),
		.hs_out(positioned_hs),
		.vs_out(positioned_vs)
	);

	wire [7:0] scaled_r;
	wire [7:0] scaled_g;
	wire [7:0] scaled_b;
	wire scaled_hs;
	wire scaled_hblank;
	wire scaled_vs;
	wire scaled_vblank;
	wire position_active = (h_position_latched != 0) || (v_position_latched != 0);
	am2r_video_hscale horizontal_scale(
		.clk(clk),
		.reset(reset),
		.enable(hscale_enable),
		.scale(hscale),
		.offset(-h_position_latched),
		.enabled(hscale_active),
		.ce_pix_in(ce_pix_in),
		.r_in(r_in),
		.g_in(g_in),
		.b_in(b_in),
		.hs_in(hs_in),
		.hblank_in(hblank_in),
		.vblank_in(vblank_in),
		.vs_in(positioned_vs),
		.r_out(scaled_r),
		.g_out(scaled_g),
		.b_out(scaled_b),
		.hs_out(scaled_hs),
		.hblank_out(scaled_hblank),
		.vs_out(scaled_vs),
		.vblank_out(scaled_vblank),
		.debug_underrun(),
		.debug_overflow()
	);

	assign ce_pix_out = hscale_active ? 1'b1 : ce_pix_in;
	assign r_out = hscale_active ? scaled_r : r_in;
	assign g_out = hscale_active ? scaled_g : g_in;
	assign b_out = hscale_active ? scaled_b : b_in;
	// Preserve the exact native timing waveform when every CRT control is at
	// its default. This is the normal HDMI/scaler input and the zero-cost
	// analog bypass.
	assign hs_out = hscale_active ? scaled_hs :
		(position_active ? positioned_hs : hs_in);
	assign hblank_out = hscale_active ? scaled_hblank : hblank_in;
	assign vs_out = hscale_active ? scaled_vs :
		(position_active ? positioned_vs : vs_in);
	assign vblank_out = hscale_active ? scaled_vblank : vblank_in;

endmodule
