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
	// First active pixel, after the native reader's normal late-latch window.
	// Diagnostic only: never drives the pacing heartbeat or buffer ownership.
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
	localparam [2:0] IDLE = 0, GPU_READ = 1, VID_READ = 2, GPU_WRITE = 3, PRESENT_STATUS = 4;
	localparam [28:0] FRAME_STATUS_ADDR = 29'h047fe008; // 0x23ff0040 >> 3
	localparam [28:0] FRAME_DETAIL_ADDR = 29'h047fe009; // 0x23ff0048 >> 3
	localparam [31:0] FRAME_STATUS_MAGIC = 32'h56424c4b; // "VBLK"
	// Words22..27 are a read-only native presentation seqlock. Existing GPU,
	// capability and heartbeat words0..21 retain their layout and behavior.
	localparam [28:0] PRESENT_HEADER_ADDR = 29'h047fe00b; // 0x23ff0058 >> 3
	localparam [31:0] PRESENT_MAGIC = 32'h5352504e; // "NPRS" in little endian
	reg [2:0] owner = IDLE;
	reg [7:0] beats_left = 0;
	reg [2:0] frame_tick_sync = 0;
	reg [2:0] display_tick_sync = 0;
	reg [31:0] frame_counter = 0;
	reg frame_status_pending = 0;
	reg frame_detail_pending = 0;
	reg [63:0] frame_detail = 0;
	reg [31:0] presentation_rasters = 0, presentation_repeats = 0;
	reg [31:0] presentation_skips = 0, previous_scanout = 0;
	reg previous_scanout_valid = 0;
	reg presentation_pending = 0;
	reg [31:0] presentation_generation = 0;
	reg [1:0] presentation_write_stage = 0;
	reg [31:0] record_generation = 0, record_rasters = 0;
	reg [31:0] record_scanout = 0, record_repeats = 0, record_skips = 0;
	wire [31:0] scanout_delta = scanout_frame - previous_scanout;
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
				end else if (presentation_pending) begin
					// One reservation cycle to freeze the record before issuing
					// its first write. Do not accidentally grant a GPU transaction
					// while the sequential owner moves to PRESENT_STATUS.
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
			PRESENT_STATUS: begin
				// Freeze the complete four-beat record, including the odd/even
				// generation, under DDR backpressure. At most four single writes
				// per raster; an in-flight record never changes at a newer edge.
				ddram_be = 8'hff;
				ddram_we = 1;
				case (presentation_write_stage)
					0: begin
						ddram_addr = PRESENT_HEADER_ADDR;
						ddram_din = {record_generation - 32'd1, PRESENT_MAGIC};
					end
					1: begin
						ddram_addr = PRESENT_HEADER_ADDR + 29'd1;
						ddram_din = {record_scanout, record_rasters};
					end
					2: begin
						ddram_addr = PRESENT_HEADER_ADDR + 29'd2;
						ddram_din = {record_skips, record_repeats};
					end
					3: begin
						ddram_addr = PRESENT_HEADER_ADDR;
						ddram_din = {record_generation, PRESENT_MAGIC};
					end
				endcase
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
			presentation_rasters <= 0;
			presentation_repeats <= 0;
			presentation_skips <= 0;
			previous_scanout <= 0;
			previous_scanout_valid <= 0;
			presentation_pending <= 0;
			presentation_generation <= 0;
			presentation_write_stage <= 0;
			record_generation <= 0;
			record_rasters <= 0;
			record_scanout <= 0;
			record_repeats <= 0;
			record_skips <= 0;
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
				presentation_rasters <= presentation_rasters + 1'b1;
				presentation_pending <= 1;
				previous_scanout <= scanout_frame;
				previous_scanout_valid <= scanout_frame != 0;
				if (previous_scanout_valid && scanout_frame != 0) begin
					if (scanout_delta == 0)
						presentation_repeats <= presentation_repeats + 1'b1;
					else if (scanout_delta > 1)
						presentation_skips <= presentation_skips + scanout_delta - 1'b1;
				end
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
					end else if (presentation_pending && !vid_rd && !frame_status_pending &&
					             !frame_detail_pending) begin
						// Coalesce delayed snapshots, not events: the counters above
						// still account for every active raster while DDR is occupied.
						owner <= PRESENT_STATUS;
						presentation_write_stage <= 0;
						presentation_generation <= presentation_generation + 32'd2;
						record_generation <= presentation_generation + 32'd2;
						record_rasters <= presentation_rasters;
						record_scanout <= previous_scanout;
						record_repeats <= presentation_repeats;
						record_skips <= presentation_skips;
						presentation_pending <= display_tick_clk;
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
				PRESENT_STATUS: if (!ddram_busy) begin
					if (presentation_write_stage == 3) begin
						presentation_write_stage <= 0;
						owner <= IDLE;
					end else begin
						presentation_write_stage <= presentation_write_stage + 1'b1;
					end
				end
				default: owner <= IDLE;
			endcase
		end
	end
endmodule
