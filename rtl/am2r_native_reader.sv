// DDR-backed native scanout, adapted to AM2R from the line-on-demand design
// used by 3s-mister-arm. The GPU publishes complete XRGB8888 frames into one
// of three DDR buffers. This reader preloads 32 lines at vblank and keeps that
// lead topped up through a dual-clock FIFO. Refilling from FIFO occupancy,
// rather than from one-shot line pulses, also lets scanout catch up after a
// delayed DDR burst instead of permanently losing its prefetch margin.
//
// Copyright (C) 2026 3S-ARM Project
// Copyright (C) 2026 AM2R MiSTer contributors
// SPDX-License-Identifier: GPL-2.0-or-later
module am2r_native_reader (
	input              ddr_clk,
	input              reset,
	input              ddr_busy,
	output reg  [7:0]  ddr_burstcnt,
	output reg [28:0]  ddr_addr,
	input      [63:0]  ddr_dout,
	input              ddr_dout_ready,
	output reg         ddr_rd,

	input              clk_vid,
	input              ce_pix,
	input              de,
	input              vblank,
	input              new_frame,
	input              new_line,

	input      [31:0]  source_frame,
	input       [1:0]  source_buffer,
	output             frame_ready,
	output     [31:0]  scanout_frame,
	output reg         buffer_in_use_valid,
	output reg  [1:0]  buffer_in_use,
	output reg  [7:0]  r_out,
	output reg  [7:0]  g_out,
	output reg  [7:0]  b_out,
	output reg         underflow_toggle
);
	localparam [28:0] BUF0_ADDR = 29'h07400020; // 0x3a000100 >> 3
	localparam [28:0] BUF1_ADDR = 29'h07409620; // 0x3a04b100 >> 3
	localparam [28:0] BUF2_ADDR = 29'h07412c20; // 0x3a096100 >> 3
	localparam [7:0] LINE_WORDS = 8'd160;
	// The MiSTer DDRAM top-level contract caps a request at 128 words.
	// Match 3s-mister-arm's known-good 96-word native-video bursts, then
	// finish each XRGB8888 line with a 64-word request.
	localparam [7:0] MAX_BURST = 8'd96;
	localparam [28:0] LINE_STRIDE = 29'd160;
	localparam [8:0] V_ACTIVE = 9'd240;
	localparam integer FIFO_WORDS = 8192;
	localparam [8:0] PRELOAD_LINES = 9'd32;
	localparam [12:0] REFILL_THRESHOLD = PRELOAD_LINES * LINE_WORDS - LINE_WORDS;
	// A completed GPU frame that arrives during vertical blank should not wait
	// an additional 16.7 ms before scanout.  Sixteen blank lines cover the
	// measured publication jitter while still leaving six complete scanlines
	// (about 382 us) to discard the speculative FIFO contents and fetch the
	// 32-line preload from the newly published buffer before active video.
	localparam [4:0] LATE_LATCH_LINES = 5'd16;

	reg [1:0] new_frame_sync = 0;
	reg [1:0] new_line_sync = 0;
	reg [1:0] vblank_sync = 0;
	always @(posedge ddr_clk) begin
		if (reset) begin
			new_frame_sync <= 0;
			new_line_sync <= 0;
			vblank_sync <= 0;
		end else begin
			new_frame_sync <= {new_frame_sync[0], new_frame};
			new_line_sync <= {new_line_sync[0], new_line};
			vblank_sync <= {vblank_sync[0], vblank};
		end
	end
	wire new_frame_ddr = new_frame_sync[0] & ~new_frame_sync[1];
	wire new_line_ddr = new_line_sync[0] & ~new_line_sync[1];
	wire vblank_ddr = vblank_sync[1];

	reg [1:0] reset_vid_sync = 2'b11;
	always @(posedge clk_vid or posedge reset)
		if (reset) reset_vid_sync <= 2'b11;
		else reset_vid_sync <= {reset_vid_sync[0], 1'b0};
	wire reset_vid = reset_vid_sync[1];

	reg frame_ready_ddr = 0;
	reg [1:0] frame_ready_sync = 0;
	always @(posedge clk_vid) begin
		if (reset_vid) frame_ready_sync <= 0;
		else frame_ready_sync <= {frame_ready_sync[0], frame_ready_ddr};
	end
	assign frame_ready = frame_ready_sync[1];

	localparam [2:0] IDLE = 0, READ_LINE = 1, WAIT_LINE = 2,
	                 LINE_DONE = 3, WAIT_DISPLAY = 4;
	reg [2:0] state = IDLE;
	reg [28:0] buffer_base = BUF0_ADDR;
	reg [8:0] current_line = 0;
	reg [7:0] beat_count = 0;
	reg [7:0] line_word_offset = 0;
	reg [7:0] burst_words = 0;
	reg preloading = 0;
	reg [31:0] latched_source_frame = 0;
	reg [4:0] vblank_line_count = 0;
	reg restart_pending = 0;
	reg [31:0] pending_source_frame = 0;
	reg [1:0] pending_source_buffer = 0;
	assign scanout_frame = latched_source_frame;

	function automatic [28:0] buffer_address;
		input [1:0] buffer_index;
		begin
			case (buffer_index)
				2'd1: buffer_address = BUF1_ADDR;
				2'd2: buffer_address = BUF2_ADDR;
				default: buffer_address = BUF0_ADDR;
			endcase
		end
	endfunction

	reg fifo_wr = 0;
	reg [63:0] fifo_wr_data = 0;
	wire fifo_full;
	wire [12:0] fifo_wrusedw;
	reg [3:0] fifo_clear_count = 0;
	wire fifo_aclr = reset | (fifo_clear_count != 0);
	wire early_source_update = vblank_ddr &&
	                           vblank_line_count < LATE_LATCH_LINES &&
	                           source_frame != 0 &&
	                           source_frame != latched_source_frame;

	always @(posedge ddr_clk) begin
		if (reset) begin
			state <= IDLE;
			ddr_burstcnt <= 1;
			ddr_addr <= 0;
			ddr_rd <= 0;
			buffer_base <= BUF0_ADDR;
			current_line <= 0;
			beat_count <= 0;
			line_word_offset <= 0;
			burst_words <= 0;
			preloading <= 0;
			latched_source_frame <= 0;
			vblank_line_count <= 0;
			restart_pending <= 0;
			pending_source_frame <= 0;
			pending_source_buffer <= 0;
			frame_ready_ddr <= 0;
			buffer_in_use_valid <= 0;
			buffer_in_use <= 0;
			fifo_wr <= 0;
			fifo_wr_data <= 0;
			fifo_clear_count <= 0;
		end else begin
			fifo_wr <= 0;
			if (fifo_clear_count != 0)
				fifo_clear_count <= fifo_clear_count - 1'b1;
			if (new_frame_ddr)
				vblank_line_count <= 0;
			else if (new_line_ddr && vblank_ddr &&
			         vblank_line_count < LATE_LATCH_LINES)
				vblank_line_count <= vblank_line_count + 1'b1;
			if (!ddr_busy)
				ddr_rd <= 0;

			if (state == WAIT_LINE && ddr_dout_ready) begin
				if (!fifo_full) begin
					fifo_wr <= 1;
					fifo_wr_data <= ddr_dout;
				end
				beat_count <= beat_count + 1'b1;
			end

			// An accepted DDR burst cannot be cancelled.  If a frame is published
			// during one, remember the newest buffer and restart as soon as the
			// final response beat arrives.  No mixed-buffer word can survive the
			// FIFO clear below.
			if (restart_pending) begin
				if (early_source_update) begin
					pending_source_frame <= source_frame;
					pending_source_buffer <= source_buffer;
				end
				if (state != WAIT_LINE || beat_count == burst_words) begin
					buffer_base <= buffer_address(early_source_update ? source_buffer :
					                             pending_source_buffer);
					buffer_in_use_valid <= 1;
					buffer_in_use <= early_source_update ? source_buffer :
					                 pending_source_buffer;
					latched_source_frame <= early_source_update ? source_frame :
					                        pending_source_frame;
					current_line <= 0;
					line_word_offset <= 0;
					preloading <= 1;
					frame_ready_ddr <= 0;
					fifo_clear_count <= 8;
					restart_pending <= 0;
					ddr_rd <= 0;
					state <= READ_LINE;
				end
			end else if (early_source_update &&
			             !(new_frame_ddr && state == IDLE)) begin
				if (state == WAIT_LINE && beat_count != burst_words) begin
					restart_pending <= 1;
					pending_source_frame <= source_frame;
					pending_source_buffer <= source_buffer;
				end else begin
					buffer_base <= buffer_address(source_buffer);
					buffer_in_use_valid <= 1;
					buffer_in_use <= source_buffer;
					latched_source_frame <= source_frame;
					current_line <= 0;
					line_word_offset <= 0;
					preloading <= 1;
					frame_ready_ddr <= 0;
					fifo_clear_count <= 8;
					ddr_rd <= 0;
					state <= READ_LINE;
				end
			end else case (state)
				IDLE: if (new_frame_ddr && source_frame != 0) begin
					buffer_base <= buffer_address(source_buffer);
					buffer_in_use_valid <= 1;
					buffer_in_use <= source_buffer;
					latched_source_frame <= source_frame;
					current_line <= 0;
					line_word_offset <= 0;
					preloading <= 1;
					frame_ready_ddr <= 0;
					fifo_clear_count <= 8;
					state <= READ_LINE;
				end
				READ_LINE: if (!ddr_busy && fifo_clear_count == 0) begin
					ddr_addr <= buffer_base + current_line * LINE_STRIDE + line_word_offset;
					if (LINE_WORDS - line_word_offset > MAX_BURST) begin
						ddr_burstcnt <= MAX_BURST;
						burst_words <= MAX_BURST;
					end else begin
						ddr_burstcnt <= LINE_WORDS - line_word_offset;
						burst_words <= LINE_WORDS - line_word_offset;
					end
					ddr_rd <= 1;
					beat_count <= 0;
					state <= WAIT_LINE;
				end
				WAIT_LINE: if (beat_count == burst_words) begin
					if (line_word_offset + burst_words < LINE_WORDS) begin
						line_word_offset <= line_word_offset + burst_words;
						state <= READ_LINE;
					end else begin
						line_word_offset <= 0;
						state <= LINE_DONE;
					end
				end
				LINE_DONE: begin
					current_line <= current_line + 1'b1;
					if (current_line == V_ACTIVE - 1'b1) begin
						state <= IDLE;
					end else if (preloading && current_line < PRELOAD_LINES - 1'b1) begin
						state <= READ_LINE;
					end else begin
						preloading <= 0;
						frame_ready_ddr <= 1;
						state <= WAIT_DISPLAY;
					end
				end
				// The write-domain FIFO count includes a synchronized read pointer.
				// Start with room for a complete 160-word line, and immediately issue
				// additional lines after a delayed burst until the normal lead returns.
				WAIT_DISPLAY: if (!vblank_ddr && current_line < V_ACTIVE &&
				                     fifo_wrusedw <= REFILL_THRESHOLD)
					state <= READ_LINE;
				default: state <= IDLE;
			endcase
		end
	end

	wire [63:0] fifo_q;
	wire fifo_empty;
	reg fifo_rd = 0;
	dcfifo #(
		.intended_device_family("Cyclone V"),
		.lpm_numwords(FIFO_WORDS),
		.lpm_showahead("ON"),
		.lpm_type("dcfifo"),
		.lpm_width(64),
		.lpm_widthu(13),
		.overflow_checking("ON"),
		.rdsync_delaypipe(4),
		.underflow_checking("ON"),
		.use_eab("ON"),
		.wrsync_delaypipe(4)
	) line_fifo (
		.aclr(fifo_aclr), .data(fifo_wr_data), .rdclk(clk_vid),
		.rdreq(fifo_rd), .wrclk(ddr_clk), .wrreq(fifo_wr), .q(fifo_q),
		.rdempty(fifo_empty), .wrfull(fifo_full), .eccstatus(), .rdfull(),
		.rdusedw(), .wrempty(), .wrusedw(fifo_wrusedw)
	);

	reg pixel_odd = 0;
	reg underflow_latched = 0;
	wire [31:0] pixel = pixel_odd ? fifo_q[63:32] : fifo_q[31:0];

	always @(posedge clk_vid) begin
		if (reset_vid) begin
			fifo_rd <= 0;
			pixel_odd <= 0;
			underflow_toggle <= 0;
			underflow_latched <= 0;
			r_out <= 0;
			g_out <= 0;
			b_out <= 0;
		end else begin
			fifo_rd <= 0;
			if (new_frame)
				underflow_latched <= 0;
			if (ce_pix) begin
				if (de && frame_ready) begin
					if (!fifo_empty) begin
						r_out <= pixel[23:16];
						g_out <= pixel[15:8];
						b_out <= pixel[7:0];
						if (pixel_odd) begin
							// dcfifo is show-ahead: consume the current word only
							// after displaying its high pixel. The registered pulse
							// reaches dcfifo on the following clk_vid edge, well
							// before the next ce_pix edge four cycles later.
							pixel_odd <= 0;
							fifo_rd <= 1;
						end else begin
							pixel_odd <= 1;
						end
					end else begin
						if (!underflow_latched) begin
							underflow_toggle <= ~underflow_toggle;
							underflow_latched <= 1;
						end
						r_out <= 0;
						g_out <= 0;
						b_out <= 0;
					end
				end else begin
					r_out <= 0;
					g_out <= 0;
					b_out <= 0;
					pixel_odd <= 0;
				end
			end
		end
	end
endmodule
