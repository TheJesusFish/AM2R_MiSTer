//============================================================================
// AM2R CRT sync-position adjustment
//
// Adapted from jtframe_resync by Jose Tejada Gomez.
// Upstream: https://github.com/jotego/jtcores
// SPDX-FileCopyrightText: 2026 Jose Tejada Gomez
// SPDX-License-Identifier: GPL-3.0-or-later
//============================================================================

module am2r_crt_resync #(
	parameter integer BITS = 5,
	parameter integer CNTW = 10
)(
	input                    clk,
	input                    reset,
	input                    ce_pix,
	input                    hs_in,
	input                    vs_in,
	input                    hvisible,
	input                    vvisible,
	input       [BITS-1:0]   h_offset,
	input       [BITS-1:0]   v_offset,
	output reg               hs_out = 0,
	output reg               vs_out = 0
);

	reg [CNTW-1:0] hs_pos [0:1];
	reg [CNTW-1:0] vs_hpos[0:1];
	reg [CNTW-1:0] vs_vpos[0:1];
	reg [CNTW-1:0] hs_len [0:1];
	reg [CNTW-1:0] vs_len [0:1];
	reg [CNTW-1:0] hs_count = 0;
	reg [CNTW-1:0] vs_count = 0;
	reg [CNTW-1:0] hs_hold = 0;
	reg [CNTW-1:0] vs_hold = 0;
	reg last_hvisible = 0;
	reg last_vvisible = 0;
	reg last_hs = 0;
	reg last_vs = 0;
	reg field = 0;

	wire hblank_edge = hvisible && !last_hvisible;
	wire vblank_edge = vvisible && !last_vvisible;
	wire hs_edge = hs_in && !last_hs;
	wire hs_end_edge = !hs_in && last_hs;
	wire vs_edge = vs_in && !last_vs;
	wire vs_end_edge = !vs_in && last_vs;

	wire [CNTW-1:0] h_delta =
		{{(CNTW-BITS){h_offset[BITS-1]}}, h_offset};
	wire [CNTW-1:0] v_delta =
		{{(CNTW-BITS){v_offset[BITS-1]}}, v_offset};
	wire [CNTW-1:0] hs_trip = hs_pos[field] + h_delta;
	wire [CNTW-1:0] vs_htrip = vs_hpos[field] + h_delta;
	wire [CNTW-1:0] vs_vtrip = vs_vpos[field] + v_delta;

	always @(posedge clk) begin
		if (reset) begin
			hs_pos[0] <= 0;
			hs_pos[1] <= 0;
			vs_hpos[0] <= 0;
			vs_hpos[1] <= 0;
			vs_vpos[0] <= 0;
			vs_vpos[1] <= 0;
			hs_len[0] <= 0;
			hs_len[1] <= 0;
			vs_len[0] <= 0;
			vs_len[1] <= 0;
			hs_count <= 0;
			vs_count <= 0;
			hs_hold <= 0;
			vs_hold <= 0;
			last_hvisible <= 0;
			last_vvisible <= 0;
			last_hs <= 0;
			last_vs <= 0;
			field <= 0;
			hs_out <= 0;
			vs_out <= 0;
		end else if (ce_pix) begin
			last_hvisible <= hvisible;
			last_vvisible <= vvisible;
			last_hs <= hs_in;
			last_vs <= vs_in;

			hs_count <= hblank_edge ? 0 : hs_count + 1'b1;
			if (vblank_edge) begin
				vs_count <= 0;
				field <= ~field;
			end else if (hblank_edge) begin
				vs_count <= vs_count + 1'b1;
			end

			if (hs_edge)
				hs_pos[field] <= hs_count;
			if (hs_end_edge)
				hs_len[field] <= hs_count - hs_pos[field];

			if (hs_count == hs_trip) begin
				hs_out <= 1;
				hs_hold <= hs_len[field] - 1'b1;
			end else begin
				if (|hs_hold)
					hs_hold <= hs_hold - 1'b1;
				if (hs_hold == 0)
					hs_out <= 0;
			end

			if (vs_edge) begin
				vs_hpos[field] <= hs_count;
				vs_vpos[field] <= vs_count;
			end
			if (vs_end_edge)
				vs_len[field] <= vs_count - vs_vpos[field];

			if (hs_count == vs_htrip) begin
				if (vs_count == vs_vtrip) begin
					vs_hold <= vs_len[field] - 1'b1;
					vs_out <= 1;
				end else begin
					if (|vs_hold)
						vs_hold <= vs_hold - 1'b1;
					if (vs_hold == 0)
						vs_out <= 0;
				end
			end
		end
	end

endmodule
