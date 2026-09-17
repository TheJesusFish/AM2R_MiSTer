// Two-master arbiter for MiSTer's 64-bit DDRAM port. Native scanout has
// priority when both masters request at once; a read grant remains owned until
// every beat of that burst has returned.
// SPDX-License-Identifier: GPL-2.0-or-later
module am2r_ddr_arbiter (
	input              clk,
	input              reset,
	// One pulse per native raster in the video clock domain. The arbiter
	// synchronizes it and publishes a monotonically increasing heartbeat in
	// shared DDR so the ARM runner can phase-lock game ticks to scanout without
	// modifying MiSTer's sys/ framework.
	input              frame_tick,
	// A separate pulse at the real raster boundary. The pacing heartbeat may
	// intentionally lead vblank, but diagnostics must sample the frame selected
	// for scanout at the actual presentation edge.
	input              display_tick,
	input      [31:0]  native_frame,
	input      [31:0]  scanout_frame,

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
	localparam [28:0] FRAME_STATUS_ADDR = 29'h047fe008; // 0x23ff0040 >> 3
	localparam [28:0] FRAME_DETAIL_ADDR = 29'h047fe009; // 0x23ff0048 >> 3
	localparam [31:0] FRAME_STATUS_MAGIC = 32'h56424c4b; // "VBLK"
	reg [1:0] owner = IDLE;
	reg [7:0] beats_left = 0;
	reg [2:0] frame_tick_sync = 0;
	reg [2:0] display_tick_sync = 0;
	reg [31:0] frame_counter = 0;
	reg frame_status_pending = 0;
	reg frame_detail_pending = 0;
	reg [63:0] frame_detail = 0;
	wire frame_tick_clk = frame_tick_sync[1] & ~frame_tick_sync[2];
	wire display_tick_clk = display_tick_sync[1] & ~display_tick_sync[2];

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
				end else if (frame_status_pending) begin
					// Native reads retain first priority. The single-beat heartbeat
					// then wins the next free slot so it reaches the HPS shortly after
					// vblank without splitting a GPU burst.
					ddram_addr = FRAME_STATUS_ADDR;
					ddram_din = {FRAME_STATUS_MAGIC, frame_counter};
					ddram_be = 8'hff;
					ddram_we = 1;
				end else if (frame_detail_pending) begin
					// Expose both sides of the presentation boundary for pacing
					// diagnostics. This remains core-local and costs one single DDR
					// beat per raster after the latency-sensitive heartbeat.
					ddram_addr = FRAME_DETAIL_ADDR;
					ddram_din = frame_detail;
					ddram_be = 8'hff;
					ddram_we = 1;
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
			frame_tick_sync <= 0;
			display_tick_sync <= 0;
			frame_counter <= 0;
			frame_status_pending <= 0;
			frame_detail_pending <= 0;
			frame_detail <= 0;
		end else begin
			frame_tick_sync <= {frame_tick_sync[1:0], frame_tick};
			display_tick_sync <= {display_tick_sync[1:0], display_tick};
			if (frame_tick_clk) begin
				frame_counter <= frame_counter + 1'b1;
				frame_status_pending <= 1;
			end
			if (display_tick_clk) begin
				frame_detail_pending <= 1;
				frame_detail <= {native_frame, scanout_frame};
			end
			case (owner)
				IDLE: begin
					if (vid_rd && !ddram_busy) begin
						owner <= VID_READ;
						beats_left <= vid_burstcnt;
					end else if (frame_status_pending && !ddram_busy) begin
						// If a new edge lands while the prior heartbeat is accepted,
						// retain a request for the newly incremented value.
						frame_status_pending <= frame_tick_clk;
					end else if (frame_detail_pending && !ddram_busy) begin
						frame_detail_pending <= display_tick_clk;
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
