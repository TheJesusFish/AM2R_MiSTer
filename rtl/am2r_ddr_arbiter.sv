// Two-master arbiter for MiSTer's 64-bit DDRAM port. Native scanout has
// priority when both masters request at once; a read grant remains owned until
// every beat of that burst has returned.
// SPDX-License-Identifier: GPL-2.0-or-later
module am2r_ddr_arbiter (
	input              clk,
	input              reset,

	input       [7:0]  gpu_burstcnt,
	input      [28:0]  gpu_addr,
	input      [63:0]  gpu_din,
	input       [7:0]  gpu_be,
	input              gpu_rd,
	input              gpu_we,
	output reg         gpu_busy,
	output     [63:0]  gpu_dout,
	output reg         gpu_dout_ready,

	input       [7:0]  vid_burstcnt,
	input      [28:0]  vid_addr,
	input              vid_rd,
	output reg         vid_busy,
	output     [63:0]  vid_dout,
	output reg         vid_dout_ready,

	input              ddram_busy,
	output reg  [7:0]  ddram_burstcnt,
	output reg [28:0]  ddram_addr,
	input      [63:0]  ddram_dout,
	input              ddram_dout_ready,
	output reg         ddram_rd,
	output reg [63:0]  ddram_din,
	output reg  [7:0]  ddram_be,
	output reg         ddram_we
);
	localparam [1:0] IDLE = 0, GPU_READ = 1, VID_READ = 2, GPU_WRITE = 3;
	reg [1:0] owner = IDLE;
	reg [7:0] beats_left = 0;

	assign gpu_dout = ddram_dout;
	assign vid_dout = ddram_dout;

	always @(*) begin
		ddram_burstcnt = 1;
		ddram_addr = 0;
		ddram_din = 0;
		ddram_be = 0;
		ddram_rd = 0;
		ddram_we = 0;
		gpu_busy = 1;
		vid_busy = 1;
		gpu_dout_ready = 0;
		vid_dout_ready = 0;

		case (owner)
			IDLE: begin
				if (vid_rd) begin
					ddram_burstcnt = vid_burstcnt;
					ddram_addr = vid_addr;
					ddram_rd = 1;
					vid_busy = ddram_busy;
				end else if (gpu_rd || gpu_we) begin
					ddram_burstcnt = gpu_burstcnt;
					ddram_addr = gpu_addr;
					ddram_din = gpu_din;
					ddram_be = gpu_be;
					ddram_rd = gpu_rd;
					ddram_we = gpu_we;
					gpu_busy = ddram_busy;
				end else begin
					gpu_busy = ddram_busy;
					vid_busy = ddram_busy;
				end
			end
			GPU_READ: gpu_dout_ready = ddram_dout_ready;
			VID_READ: vid_dout_ready = ddram_dout_ready;
			GPU_WRITE: begin
				// Avalon burst writes carry one accepted data beat per cycle while
				// keeping address and burst count fixed.  Retain ownership until the
				// final beat so a native-video read cannot split the transaction.
				ddram_burstcnt = gpu_burstcnt;
				ddram_addr = gpu_addr;
				ddram_din = gpu_din;
				ddram_be = gpu_be;
				ddram_we = gpu_we;
				gpu_busy = ddram_busy;
			end
			default: begin end
		endcase
	end

	always @(posedge clk) begin
		if (reset) begin
			owner <= IDLE;
			beats_left <= 0;
		end else begin
			case (owner)
				IDLE: begin
					if (vid_rd && !ddram_busy) begin
						owner <= VID_READ;
						beats_left <= vid_burstcnt;
					end else if (gpu_rd && !ddram_busy) begin
						owner <= GPU_READ;
						beats_left <= gpu_burstcnt;
					end else if (gpu_we && !ddram_busy && gpu_burstcnt > 1) begin
						// The first write beat is accepted while still in IDLE.
						owner <= GPU_WRITE;
						beats_left <= gpu_burstcnt - 1'b1;
					end
				end
				GPU_READ, VID_READ: if (ddram_dout_ready) begin
					if (beats_left == 1) begin
						beats_left <= 0;
						owner <= IDLE;
					end else begin
						beats_left <= beats_left - 1'b1;
					end
				end
				GPU_WRITE: if (gpu_we && !ddram_busy) begin
					if (beats_left == 1) begin
						beats_left <= 0;
						owner <= IDLE;
					end else begin
						beats_left <= beats_left - 1'b1;
					end
				end
				default: owner <= IDLE;
			endcase
		end
	end
endmodule
