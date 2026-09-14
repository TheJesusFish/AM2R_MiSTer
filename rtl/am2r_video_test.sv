// Native 240-line boot/test raster for the AM2R framework bring-up shell.
// This is not the game renderer. HPS local-framebuffer output can supersede
// this raster through the unmodified standard MiSTer framework.
// SPDX-License-Identifier: GPL-2.0-or-later

module am2r_video_test
(
	input        clk,
	input        reset,
	input        scandouble,
	input  [1:0] pattern,

	output reg   ce_pix = 0,
	output       hvcnt_atzero,
	output       hblank,
	output       hsync,
	output       vblank,
	output       vsync,
	output reg [7:0] r,
	output reg [7:0] g,
	output reg [7:0] b
);

localparam [9:0] H_TOTAL = 10'd638;
localparam [9:0] H_ACTIVE = 10'd530;
localparam [9:0] H_SYNC_START = 10'd544;
localparam [9:0] H_SYNC_END = 10'd590;

reg [9:0] h_count = 0;
reg [9:0] v_count = 0;
reg [1:0] pattern_latched = 0;

wire [9:0] v_total = scandouble ? 10'd524 : 10'd262;
wire [9:0] active_lines = scandouble ? 10'd480 : 10'd240;
wire [9:0] sync_start = scandouble ? 10'd490 : 10'd245;
wire [9:0] sync_end = scandouble ? 10'd496 : 10'd248;
wire [8:0] source_y = scandouble ? v_count[9:1] : v_count[8:0];
assign hvcnt_atzero = ce_pix && (h_count == 0) && (v_count == 0);
assign hblank = (h_count >= H_ACTIVE);
assign hsync = (h_count >= H_SYNC_START) && (h_count < H_SYNC_END);
assign vblank = (v_count >= active_lines);
assign vsync = (v_count >= sync_start) && (v_count < sync_end);

always @(posedge clk) begin
	ce_pix <= scandouble ? 1'b1 : ~ce_pix;
	if(ce_pix) begin
		if(h_count == H_TOTAL - 1'd1) begin
			h_count <= 0;
			if(v_count == v_total - 1'd1) v_count <= 0;
			else v_count <= v_count + 1'd1;
		end else begin
			h_count <= h_count + 1'd1;
		end
	end

	// Reset affects only core-owned visual state. Timing never stops, keeping
	// sync valid for the framework and analog output throughout reset.
	if(ce_pix && (h_count == 0) && (v_count == 0)) begin
		pattern_latched <= reset ? 2'd0 : pattern;
	end
end

always @(*) begin
	r = 0;
	g = 0;
	b = 0;

	if(!hblank && !vblank) begin
		case(pattern_latched)
			2'd0: begin
				if(h_count < 10'd66)       {r,g,b} = 24'hFFFFFF;
				else if(h_count < 10'd132) {r,g,b} = 24'hFFFF00;
				else if(h_count < 10'd198) {r,g,b} = 24'h00FFFF;
				else if(h_count < 10'd264) {r,g,b} = 24'h00FF00;
				else if(h_count < 10'd330) {r,g,b} = 24'hFF00FF;
				else if(h_count < 10'd396) {r,g,b} = 24'hFF0000;
				else if(h_count < 10'd462) {r,g,b} = 24'h0000FF;
				else                         {r,g,b} = 24'h101010;
			end

			2'd1: begin
				{r,g,b} = ((h_count[4:0] == 0) || (source_y[4:0] == 0))
					? 24'h406080 : 24'h081018;
				if((h_count == 10'd264) || (source_y == 9'd120))
					{r,g,b} = 24'hFFFFFF;
			end

			2'd2: begin
				r = h_count[8:1];
				g = source_y[7:0];
				b = h_count[7:0] ^ source_y[7:0];
			end

			default: {r,g,b} = 24'h000000;
		endcase
	end
end

endmodule
