//============================================================================
// AM2R fixed-function GPU
//
// Implements the subset which dominates AM2R: clear, axis-aligned nearest-
// neighbour RGBA blits, normal/additive alpha blending, and framebuffer
// presentation. Descriptor bit 8 selects additive blending for blit/fill.
// Commands and textures reside in uncached HPS DDR. The working framebuffer
// resides in M10K RAM so destination reads don't consume DDR bandwidth.
//
// SPDX-License-Identifier: GPL-2.0-or-later
//============================================================================

module am2r_gpu
(
	input              clk,
	input              reset,
	input              ddram_busy,
	output reg  [7:0]  ddram_burstcnt,
	output reg  [28:0] ddram_addr,
	input       [63:0] ddram_dout,
	input              ddram_dout_ready,
	output reg         ddram_rd,
	output      [63:0] ddram_din,
	output reg  [7:0]  ddram_be,
	output reg         ddram_we,
	input              scan_buffer_valid,
	input       [1:0]  scan_buffer,
	input              scan_underflow_toggle,
	output reg  [31:0] native_frame,
	output reg  [1:0]  native_buffer
);

	localparam [31:0] CONTROL_ADDR = 32'h23ff_0000;
	localparam [31:0] CONTROL_MAGIC = 32'h5047_3241; // "A2GP" in memory
	localparam [28:0] NATIVE_BUF0 = 29'h0740_0020; // 0x3a000100 >> 3
	localparam [28:0] NATIVE_BUF1 = 29'h0740_9620; // 0x3a04b100 >> 3
	localparam [28:0] NATIVE_BUF2 = 29'h0741_2c20; // 0x3a096100 >> 3
	localparam integer FB_WIDTH = 320;
	localparam integer FB_HEIGHT = 240;
	localparam integer FB_WORDS = (FB_WIDTH * FB_HEIGHT) / 2;
	// Keep exports burst-efficient, but retain single-beat native-buffer writes.
	// The latter is the framework's established framebuffer write pattern and
	// avoids long write ownership while the same DDR port services scanout.
	localparam integer EXPORT_WRITE_BURST_WORDS = 64;
	localparam integer PRESENT_WRITE_BURST_WORDS = 64;
	// MiSTer's DDRAM contract caps bursts at 128 beats. Match the native
	// reader's field-proven 96-beat request size and split larger transfers.
	localparam [7:0] GPU_READ_BURST_WORDS = 8'd96;
	localparam integer WRITE_FIFO_DEPTH = 8;

	localparam [5:0]
		ST_RESET = 0, ST_POLL_DELAY = 1, ST_POLL_ACCEPT = 2,
		ST_POLL_DATA = 3, ST_CFG1_ACCEPT = 4, ST_CFG1_DATA = 5,
		ST_CFG2_ACCEPT = 6, ST_CFG2_DATA = 7, ST_CMD_ACCEPT = 8,
		ST_CMD_DATA = 9, ST_CMD_DECODE = 10, ST_CLEAR = 11,
		ST_BLIT_ROW = 12, ST_BLIT_PIXEL = 13, ST_TEX_ACCEPT = 14,
		ST_TEX_DATA = 15, ST_PIXEL_READY = 16, ST_BLEND_READ = 17,
		ST_BLEND_WRITE = 18, ST_PRESENT_READ = 19,
		ST_PRESENT_WRITE = 20, ST_COMPLETE = 21,
		ST_PAIR_BLEND_READ = 22, ST_PAIR_BLEND_WRITE = 23,
		ST_TEX_READY = 24, ST_SOLID_READY = 25,
		ST_AFFINE_ADDRESS = 26, ST_PRESENT_WAIT = 27,
		ST_PAIR_TINT_READY = 28, ST_EXPORT_READ = 29,
		ST_EXPORT_WRITE = 30, ST_WATER_TABLE_ACCEPT = 31,
		ST_WATER_TABLE_DATA = 32, ST_WATER_SOURCE_ACCEPT = 33,
		ST_WATER_SOURCE_DATA = 34, ST_WATER_SOURCE_ADDRESS = 35;
	localparam [5:0] ST_AFFINE_SOURCE_ADDRESS = 36,
		ST_ROW_SOURCE_ADDRESS = 37, ST_AFFINE_ROW_ADDRESS = 38,
		ST_TINT_PRODUCT_READY = 39, ST_FRAMEBUFFER_WRITE_START = 40,
		ST_FRAMEBUFFER_WRITE_FILL = 41, ST_FRAMEBUFFER_WRITE_STREAM = 42,
		ST_WATER_ROW_CACHE_ADDRESS = 43, ST_WATER_ROW_CACHE_DATA = 44,
		ST_BLEND_COMMIT = 45, ST_PAIR_BLEND_COMMIT = 46,
		ST_TILED_ROW_ADDRESS = 47, ST_TILED_SOURCE_ADDRESS = 48,
		ST_TILED_SOURCE_ACCEPT = 49, ST_TILED_SOURCE_DATA = 50;

	(* ramstyle = "M10K, no_rw_check" *) reg [31:0] fb_even [0:FB_WORDS-1];
	(* ramstyle = "M10K, no_rw_check" *) reg [31:0] fb_odd  [0:FB_WORDS-1];

	reg [5:0] state;
	reg [15:0] poll_delay;
	reg [31:0] last_sequence, active_sequence;
	reg [31:0] command_addr, framebuffer_addr;
	reg [15:0] command_count, command_index;
	reg [3:0] command_word;
	reg [63:0] descriptor [0:7];

	reg [15:0] clear_index;
	reg [31:0] clear_color;
	reg [31:0] src_base, src_stride;
	reg signed [15:0] dst_x, dst_y;
	reg [15:0] blit_width, blit_height, blit_x, blit_y;
	reg signed [31:0] u_start, v_start, u_step, v_step;
	reg signed [31:0] u_y_step, v_x_step;
	reg signed [31:0] u_current, v_current;
	reg signed [31:0] u_min, u_max, v_min, v_max;
	reg [31:0] source_row_addr, source_byte_addr, tint_color;
	reg [15:0] source_row_index_stage;
	reg [31:0] source_stride_stage, tint_color_stage;
	reg signed [24:0] tint_r_current, tint_g_current, tint_b_current, tint_a_current;
	reg signed [15:0] tint_r_step, tint_g_step, tint_b_step, tint_a_step;
	reg texture_cache_valid;
	reg [28:0] texture_cache_addr;
	// Cache one 128-byte sequential texture span per miss. Most AM2R draws are
	// unit-step scanlines; a wider line amortizes MiSTer DDR request latency over
	// 32 pixels instead of refetching every eight pixels.
	reg [63:0] texture_cache_data [0:15];
	reg [3:0] texture_cache_fill;
	reg [31:0] source_pixel, source_pixel_1, tinted_pixel;
	reg [31:0] tinted_pixel_1;
	reg [31:0] blended_pixel_result0, blended_pixel_result1;
	reg [15:0] tint_product [0:7];
	reg solid_mode;
	reg additive_mode;
	reg affine_mode;
	reg [31:0] affine_tint_config;
	reg affine_additive_config;
	reg [16:0] destination_linear;
	reg [15:0] destination_word;
	reg destination_odd;
	reg pair_mode;
	reg [63:0] ddram_din_control;
	reg framebuffer_write_present;
	reg [28:0] framebuffer_write_base;
	reg [15:0] framebuffer_write_index;
	reg [15:0] framebuffer_write_burst_start;
	reg [7:0] framebuffer_write_burst_length;
	reg [7:0] framebuffer_write_beats_left;
	reg [7:0] framebuffer_reads_issued;
	reg [1:0] framebuffer_read_valid;
	reg [2:0] framebuffer_fifo_read_ptr, framebuffer_fifo_write_ptr;
	reg [3:0] framebuffer_fifo_count;
	reg [63:0] framebuffer_fifo [0:WRITE_FIFO_DEPTH-1];
	reg [31:0] water_table_base;
	reg [15:0] water_row_count, water_row_index;
	reg [7:0] water_table_load_index;
	reg [7:0] water_table_burst_left;
	reg [63:0] water_table_cache_q;
	(* ramstyle = "M10K, no_rw_check" *) reg [63:0] water_table_cache [0:239];
	reg [15:0] water_destination_start_y;
	reg [15:0] water_row_destination_x, water_row_width;
	reg [15:0] water_row_source_x, water_row_source_y;
	reg [16:0] water_destination_base;
	reg [7:0] water_source_word;
	reg [7:0] water_source_burst_left;
	reg water_source_done;
	reg water_native_source;
	reg tiled_mode, tiled_solid_mode, water_additive_mode;
	reg [7:0] water_tint_alpha;
	reg [15:0] tiled_x;
	reg [4:0] tiled_phase;
	reg [31:0] tiled_solid_pixel;
	reg [1:0] water_pipe0_valid, water_pipe1_valid, water_pipe2_valid;
	reg [1:0] water_pipe3_valid, water_pipe4_valid;
	reg [31:0] water_pipe0_pixel0, water_pipe0_pixel1;
	reg [31:0] water_pipe1_pixel0, water_pipe1_pixel1;
	reg [31:0] water_pipe2_pixel0, water_pipe2_pixel1;
	reg [31:0] water_pipe2_background0, water_pipe2_background1;
	reg [15:0] water_pipe0_destination0, water_pipe0_destination1;
	reg [15:0] water_pipe1_destination0, water_pipe1_destination1;
	reg [15:0] water_pipe2_destination0, water_pipe2_destination1;
	reg water_pipe0_odd0, water_pipe0_odd1;
	reg water_pipe1_odd0, water_pipe1_odd1;
	reg water_pipe2_odd0, water_pipe2_odd1;
	reg [15:0] water_pipe3_source_r [0:1];
	reg [15:0] water_pipe3_source_g [0:1];
	reg [15:0] water_pipe3_source_b [0:1];
	reg [15:0] water_pipe3_destination_r [0:1];
	reg [15:0] water_pipe3_destination_g [0:1];
	reg [15:0] water_pipe3_destination_b [0:1];
	reg [15:0] water_pipe3_destination_a [0:1];
	reg [7:0] water_pipe3_source_a [0:1];
	reg [15:0] water_pipe3_destination [0:1];
	reg water_pipe3_odd [0:1];
	reg [31:0] water_pipe3_background [0:1];
	reg [31:0] water_pipe4_pixel [0:1];
	reg [15:0] water_pipe4_destination [0:1];
	reg water_pipe4_odd [0:1];
	reg [31:0] operation_cycles;
	reg reset_meta, reset_sync;
	reg [15:0] fb_even_read_address, fb_odd_read_address;
	reg [15:0] fb_even_write_address, fb_odd_write_address;
	reg [31:0] fb_even_q, fb_odd_q;
	reg [31:0] fb_even_write_data, fb_odd_write_data;
	reg fb_even_we, fb_odd_we;
	reg [1:0] present_buffer;
	reg [1:0] scan_underflow_sync;
	reg scan_underflow_seen;
	wire [16:0] destination_linear_calc =
		($signed(dst_y) + $signed({1'b0, blit_y})) * FB_WIDTH +
		$signed(dst_x) + $signed({1'b0, blit_x});
	wire [8:0] water_source_x0 = {water_source_word, 1'b0};
	wire [8:0] water_source_x1 = {water_source_word, 1'b0} + 1'b1;
	wire [16:0] water_source_end = water_row_source_x + water_row_width;
	wire water_source_valid0 = water_source_x0 >= water_row_source_x &&
		water_source_x0 < water_source_end;
	wire water_source_valid1 = water_source_x1 >= water_row_source_x &&
		water_source_x1 < water_source_end;
	wire [16:0] water_destination_linear0 = water_destination_base +
		water_source_x0 - water_row_source_x;
	wire [16:0] water_destination_linear1 = water_destination_base +
		water_source_x1 - water_row_source_x;
	wire [4:0] tiled_source_index0 = tiled_phase + tiled_x[4:0];
	wire [4:0] tiled_source_index1 = tiled_source_index0 + 1'b1;
	wire [31:0] tiled_source_pixel0 = tiled_solid_mode ? tiled_solid_pixel :
		(tiled_source_index0[0] ?
			texture_cache_data[tiled_source_index0[4:1]][63:32] :
			texture_cache_data[tiled_source_index0[4:1]][31:0]);
	wire [31:0] tiled_source_pixel1 = tiled_solid_mode ? tiled_solid_pixel :
		(tiled_source_index1[0] ?
			texture_cache_data[tiled_source_index1[4:1]][63:32] :
			texture_cache_data[tiled_source_index1[4:1]][31:0]);
	wire [16:0] tiled_destination_linear0 = water_destination_base + tiled_x;
	wire [16:0] tiled_destination_linear1 = water_destination_base + tiled_x + 1'b1;

	always @(posedge clk) begin
		reset_meta <= reset;
		reset_sync <= reset_meta;
		if (reset)
			scan_underflow_sync <= 0;
		else
			scan_underflow_sync <= {scan_underflow_sync[0], scan_underflow_toggle};
	end

	always @(posedge clk) begin
		if (fb_even_we) fb_even[fb_even_write_address] <= fb_even_write_data;
		if (fb_odd_we) fb_odd[fb_odd_write_address] <= fb_odd_write_data;
		fb_even_q <= fb_even[fb_even_read_address];
		fb_odd_q <= fb_odd[fb_odd_read_address];
	end

	function automatic [7:0] mul255;
		input [7:0] a, b;
		reg [15:0] product;
		begin
			product = a * b;
			mul255 = (product + 16'd128 + ((product + 16'd128) >> 8)) >> 8;
		end
	endfunction

	function automatic [7:0] round_mul255_product;
		input [15:0] product;
		reg [15:0] rounded;
		begin
			rounded = product + 16'd128;
			round_mul255_product = (rounded + (rounded >> 8)) >> 8;
		end
	endfunction

	function automatic [7:0] div255_floor;
		input [16:0] value;
		begin
			div255_floor = (value + 17'd1 + (value >> 8)) >> 8;
		end
	endfunction

	function automatic [31:0] apply_tint;
		input [31:0] pixel, tint;
		begin
			apply_tint = {mul255(pixel[31:24], tint[31:24]),
			              mul255(pixel[23:16], tint[23:16]),
			              mul255(pixel[15:8], tint[15:8]),
			              mul255(pixel[7:0], tint[7:0])};
		end
	endfunction

	function automatic [7:0] clamp_tint;
		input signed [24:0] value;
		begin
			if (value < 0) clamp_tint = 0;
			else if (value > 25'sh0ff_0000) clamp_tint = 8'd255;
			else clamp_tint = value[23:16];
		end
	endfunction

	function automatic [31:0] alpha_blend;
		input [31:0] src, dst;
		reg [7:0] sa, ia;
		begin
			sa = src[31:24];
			ia = 8'd255 - sa;
			alpha_blend[7:0] = div255_floor(src[7:0] * sa + dst[7:0] * ia);
			alpha_blend[15:8] = div255_floor(src[15:8] * sa + dst[15:8] * ia);
			alpha_blend[23:16] = div255_floor(src[23:16] * sa + dst[23:16] * ia);
			alpha_blend[31:24] = sa + div255_floor(dst[31:24] * ia);
		end
	endfunction

	function automatic [7:0] saturating_add;
		input [7:0] a, b;
		reg [8:0] sum;
		begin
			sum = a + b;
			saturating_add = sum[8] ? 8'hff : sum[7:0];
		end
	endfunction

	function automatic [28:0] native_buffer_address;
		input [1:0] buffer_index;
		begin
			case (buffer_index)
				2'd1: native_buffer_address = NATIVE_BUF1;
				2'd2: native_buffer_address = NATIVE_BUF2;
				default: native_buffer_address = NATIVE_BUF0;
			endcase
		end
	endfunction

	// Keep the current completed buffer and the buffer being scanned immutable.
	// With three presentation buffers, the next job can still choose the third
	// buffer instead of waiting a complete raster for scanout to release one.
	function automatic [1:0] next_present_buffer;
		input [1:0] completed_buffer;
		input scan_valid;
		input [1:0] scan_buffer_index;
		reg [1:0] candidate;
		begin
			case (completed_buffer)
				2'd0: candidate = 2'd1;
				2'd1: candidate = 2'd2;
				default: candidate = 2'd0;
			endcase
			if (scan_valid && candidate == scan_buffer_index) begin
				case (candidate)
					2'd0: candidate = 2'd1;
					2'd1: candidate = 2'd2;
					default: candidate = 2'd0;
				endcase
			end
			next_present_buffer = candidate;
		end
	endfunction

	function automatic [31:0] additive_blend;
		input [31:0] src, dst;
		reg [7:0] sa;
		begin
			sa = src[31:24];
			additive_blend[7:0] = saturating_add(dst[7:0], div255_floor(src[7:0] * sa));
			additive_blend[15:8] = saturating_add(dst[15:8], div255_floor(src[15:8] * sa));
			additive_blend[23:16] = saturating_add(dst[23:16], div255_floor(src[23:16] * sa));
			additive_blend[31:24] = saturating_add(dst[31:24], sa);
		end
	endfunction

	function automatic [31:0] rgba_to_xrgb;
		input [31:0] rgba;
		begin
			rgba_to_xrgb = {8'h00, rgba[7:0], rgba[15:8], rgba[23:16]};
		end
	endfunction

	function automatic [31:0] xrgb_to_opaque_rgba;
		input [31:0] xrgb;
		begin
			xrgb_to_opaque_rgba = {8'hff, xrgb[7:0], xrgb[15:8], xrgb[23:16]};
		end
	endfunction

	// A small skid FIFO decouples synchronous M10K reads from Avalon write
	// back-pressure. This keeps burst data stable during stalls while still
	// sustaining one framebuffer pair per accepted DDR clock.
	wire [63:0] framebuffer_fifo_output =
		framebuffer_fifo[framebuffer_fifo_read_ptr];
	assign ddram_din = state == ST_FRAMEBUFFER_WRITE_STREAM ?
		(framebuffer_write_present ?
			{rgba_to_xrgb(framebuffer_fifo_output[63:32]),
			 rgba_to_xrgb(framebuffer_fifo_output[31:0])} :
			 framebuffer_fifo_output) : ddram_din_control;

	function automatic [31:0] blended_pixel;
		input [31:0] src, dst;
		begin
			if (src[31:24] == 0) blended_pixel = dst;
			else if (additive_mode) blended_pixel = additive_blend(src, dst);
			else if (src[31:24] == 8'hff) blended_pixel = src;
			else blended_pixel = alpha_blend(src, dst);
		end
	endfunction

	task automatic start_read;
		input [31:0] byte_address;
		input [7:0] burst_count;
		begin
			ddram_addr <= byte_address[31:3];
			ddram_burstcnt <= burst_count;
			ddram_rd <= 1'b1;
			ddram_we <= 1'b0;
		end
	endtask

	task automatic advance_blit_pixel;
		begin
			if (blit_x + 1'b1 >= blit_width) begin
				blit_x <= 0;
				blit_y <= blit_y + 1'b1;
				u_start <= u_start + u_y_step;
				v_start <= v_start + v_step;
				tint_r_current <= tint_r_current + ($signed({{9{tint_r_step[15]}}, tint_r_step}) <<< 8);
				tint_g_current <= tint_g_current + ($signed({{9{tint_g_step[15]}}, tint_g_step}) <<< 8);
				tint_b_current <= tint_b_current + ($signed({{9{tint_b_step[15]}}, tint_b_step}) <<< 8);
				tint_a_current <= tint_a_current + ($signed({{9{tint_a_step[15]}}, tint_a_step}) <<< 8);
				state <= ST_BLIT_ROW;
			end else begin
				blit_x <= blit_x + 1'b1;
				u_current <= u_current + u_step;
				v_current <= v_current + v_x_step;
				state <= ST_BLIT_PIXEL;
			end
		end
	endtask

	task automatic advance_blit_pair;
		begin
			if (blit_x + 2 >= blit_width) begin
				blit_x <= 0;
				blit_y <= blit_y + 1'b1;
				u_start <= u_start + u_y_step;
				v_start <= v_start + v_step;
				tint_r_current <= tint_r_current + ($signed({{9{tint_r_step[15]}}, tint_r_step}) <<< 8);
				tint_g_current <= tint_g_current + ($signed({{9{tint_g_step[15]}}, tint_g_step}) <<< 8);
				tint_b_current <= tint_b_current + ($signed({{9{tint_b_step[15]}}, tint_b_step}) <<< 8);
				tint_a_current <= tint_a_current + ($signed({{9{tint_a_step[15]}}, tint_a_step}) <<< 8);
				state <= ST_BLIT_ROW;
			end else begin
				blit_x <= blit_x + 2;
				u_current <= u_current + (u_step <<< 1);
				v_current <= v_current + (v_x_step <<< 1);
				state <= ST_BLIT_PIXEL;
			end
		end
	endtask

	always @(posedge clk) begin
		if (reset_sync) begin
			state <= ST_RESET;
			ddram_rd <= 0;
			ddram_we <= 0;
			ddram_burstcnt <= 1;
			ddram_addr <= 0;
			ddram_din_control <= 0;
			ddram_be <= 0;
			last_sequence <= 0;
			poll_delay <= 0;
			operation_cycles <= 0;
			texture_cache_valid <= 0;
			water_pipe0_valid <= 0;
			water_pipe1_valid <= 0;
			water_pipe2_valid <= 0;
			water_pipe3_valid <= 0;
			water_pipe4_valid <= 0;
			water_native_source <= 0;
			tiled_mode <= 0;
			tiled_solid_mode <= 0;
			water_additive_mode <= 0;
			water_tint_alpha <= 8'hff;
			fb_even_we <= 0;
			fb_odd_we <= 0;
			present_buffer <= 0;
			scan_underflow_seen <= 0;
			affine_tint_config <= 32'hffff_ffff;
			affine_additive_config <= 0;
			native_frame <= 0;
			native_buffer <= 0;
		end else begin
			operation_cycles <= operation_cycles + 1'b1;
			fb_even_we <= 0;
			fb_odd_we <= 0;
			case (state)
				ST_RESET: begin
					ddram_rd <= 0;
					ddram_we <= 0;
					poll_delay <= 0;
					state <= ST_POLL_DELAY;
				end
				ST_POLL_DELAY: begin
					if (poll_delay == 0) begin
						start_read(CONTROL_ADDR, 1);
						state <= ST_POLL_ACCEPT;
					end else poll_delay <= poll_delay - 1'b1;
				end
				ST_POLL_ACCEPT: if (!ddram_busy) begin
					ddram_rd <= 0;
					state <= ST_POLL_DATA;
				end
				ST_POLL_DATA: if (ddram_dout_ready) begin
					if (ddram_dout[31:0] == CONTROL_MAGIC &&
					    ddram_dout[63:32] != last_sequence) begin
						active_sequence <= ddram_dout[63:32];
						operation_cycles <= 0;
						start_read(CONTROL_ADDR + 8, 1);
						state <= ST_CFG1_ACCEPT;
					end else begin
						poll_delay <= 16'd4095;
						state <= ST_POLL_DELAY;
					end
				end
				ST_CFG1_ACCEPT: if (!ddram_busy) begin
					ddram_rd <= 0;
					state <= ST_CFG1_DATA;
				end
				ST_CFG1_DATA: if (ddram_dout_ready) begin
					command_addr <= ddram_dout[31:0];
					command_count <= ddram_dout[47:32];
					start_read(CONTROL_ADDR + 16, 1);
					state <= ST_CFG2_ACCEPT;
				end
				ST_CFG2_ACCEPT: if (!ddram_busy) begin
					ddram_rd <= 0;
					state <= ST_CFG2_DATA;
				end
				ST_CFG2_DATA: if (ddram_dout_ready) begin
					framebuffer_addr <= ddram_dout[31:0];
					command_index <= 0;
					command_word <= 0;
					start_read(command_addr, 8);
					state <= ST_CMD_ACCEPT;
				end
				ST_CMD_ACCEPT: if (!ddram_busy) begin
					ddram_rd <= 0;
					state <= ST_CMD_DATA;
				end
				ST_CMD_DATA: if (ddram_dout_ready) begin
					descriptor[command_word] <= ddram_dout;
					if (command_word == 7) state <= ST_CMD_DECODE;
					else command_word <= command_word + 1'b1;
				end
				ST_CMD_DECODE: begin
					case (descriptor[0][7:0])
						8'd0: begin
							framebuffer_write_present <= 1;
							framebuffer_write_base <= native_buffer_address(present_buffer);
							framebuffer_write_index <= 0;
							state <= ST_PRESENT_WAIT;
						end
						8'd1: begin
							clear_color <= descriptor[1][31:0];
							clear_index <= 0;
							state <= ST_CLEAR;
						end
						8'd2: begin
							solid_mode <= 0;
							additive_mode <= descriptor[0][8];
							affine_mode <= 0;
							src_base <= descriptor[1][31:0];
							src_stride <= descriptor[1][63:32];
							dst_x <= descriptor[2][15:0];
							dst_y <= descriptor[2][31:16];
							blit_width <= descriptor[0][31:16];
							blit_height <= descriptor[0][47:32];
							u_start <= descriptor[4][31:0];
							u_step <= descriptor[5][31:0];
							v_step <= descriptor[5][63:32];
							u_y_step <= 0;
							v_x_step <= 0;
							tint_color <= descriptor[6][31:0];
							tint_r_current <= {1'b0, descriptor[6][7:0], 16'b0};
							tint_g_current <= {1'b0, descriptor[6][15:8], 16'b0};
							tint_b_current <= {1'b0, descriptor[6][23:16], 16'b0};
							tint_a_current <= {1'b0, descriptor[6][31:24], 16'b0};
							tint_r_step <= descriptor[7][15:0];
							tint_g_step <= descriptor[7][31:16];
							tint_b_step <= descriptor[7][47:32];
							tint_a_step <= descriptor[7][63:48];
							blit_x <= 0;
							blit_y <= 0;
							v_start <= descriptor[4][63:32];
							v_current <= descriptor[4][63:32];
							texture_cache_valid <= 0;
							state <= ST_BLIT_ROW;
						end
						8'd3: begin
							if (descriptor[0][8] &&
							    descriptor[1][31:24] == 8'hff &&
							    descriptor[2][31:0] == 0 &&
							    descriptor[0][31:16] == FB_WIDTH &&
							    descriptor[0][47:32] == FB_HEIGHT &&
							    descriptor[7] == 0) begin
								// AM2R's underwater colour wash is a full-screen opaque
								// additive fill. Stream it through both framebuffer banks
								// at one pair per clock instead of re-entering the generic
								// four-state read/modify/write loop for every pair.
								tiled_mode <= 1;
								tiled_solid_mode <= 1;
								water_additive_mode <= 1;
								water_row_index <= 0;
								water_row_count <= FB_HEIGHT;
								water_destination_start_y <= 0;
								water_row_destination_x <= 0;
								water_row_width <= FB_WIDTH;
								water_destination_base <= 0;
								tiled_x <= 0;
								tiled_phase <= 0;
								tiled_solid_pixel <= descriptor[1][31:0];
								tint_color <= 32'hffff_ffff;
								water_tint_alpha <= 8'hff;
								water_source_done <= 0;
								water_pipe0_valid <= 0;
								water_pipe1_valid <= 0;
								water_pipe2_valid <= 0;
								water_pipe3_valid <= 0;
								water_pipe4_valid <= 0;
								state <= ST_WATER_SOURCE_DATA;
							end else begin
								solid_mode <= 1;
								additive_mode <= descriptor[0][8];
								affine_mode <= 0;
								tint_color <= descriptor[1][31:0];
								tint_r_current <= {1'b0, descriptor[1][7:0], 16'b0};
								tint_g_current <= {1'b0, descriptor[1][15:8], 16'b0};
								tint_b_current <= {1'b0, descriptor[1][23:16], 16'b0};
								tint_a_current <= {1'b0, descriptor[1][31:24], 16'b0};
								tint_r_step <= descriptor[7][15:0];
								tint_g_step <= descriptor[7][31:16];
								tint_b_step <= descriptor[7][47:32];
								tint_a_step <= descriptor[7][63:48];
								dst_x <= descriptor[2][15:0];
								dst_y <= descriptor[2][31:16];
								blit_width <= descriptor[0][31:16];
								blit_height <= descriptor[0][47:32];
								u_start <= 0;
								v_start <= 0;
								u_step <= 0;
								v_step <= 0;
								u_y_step <= 0;
								v_x_step <= 0;
								blit_x <= 0;
								blit_y <= 0;
								v_current <= 0;
								state <= ST_BLIT_ROW;
							end
						end
						8'd4: begin
							// Affine nearest-neighbour blit. The HPS supplies a clipped
							// bounding box, source bounds, and 16.16 UV derivatives.
							solid_mode <= 0;
							additive_mode <= affine_additive_config;
							affine_mode <= 1;
							src_base <= descriptor[1][31:0];
							src_stride <= descriptor[1][63:32];
							dst_x <= descriptor[2][15:0];
							dst_y <= descriptor[2][31:16];
							blit_width <= descriptor[0][31:16];
							blit_height <= descriptor[0][47:32];
							u_min <= descriptor[3][31:0];
							u_max <= descriptor[3][63:32];
							v_min <= descriptor[4][31:0];
							v_max <= descriptor[4][63:32];
							u_start <= descriptor[5][31:0];
							v_start <= descriptor[5][63:32];
							u_step <= descriptor[6][31:0];
							v_x_step <= descriptor[6][63:32];
							u_y_step <= descriptor[7][31:0];
							v_step <= descriptor[7][63:32];
							tint_color <= affine_tint_config;
							tint_r_current <= {1'b0, affine_tint_config[7:0], 16'b0};
							tint_g_current <= {1'b0, affine_tint_config[15:8], 16'b0};
							tint_b_current <= {1'b0, affine_tint_config[23:16], 16'b0};
							tint_a_current <= {1'b0, affine_tint_config[31:24], 16'b0};
							tint_r_step <= 0;
							tint_g_step <= 0;
							tint_b_step <= 0;
							tint_a_step <= 0;
							blit_x <= 0;
							blit_y <= 0;
							texture_cache_valid <= 0;
							affine_tint_config <= 32'hffff_ffff;
							affine_additive_config <= 0;
							state <= ST_BLIT_ROW;
						end
						8'd5: begin
							// One-shot state for the following affine command. Keeping it
							// separate leaves all 256 coordinate bits available.
							affine_tint_config <= descriptor[1][31:0];
							affine_additive_config <= descriptor[0][8];
							command_index <= command_index + 1'b1;
							command_word <= 0;
							start_read(command_addr + ((command_index + 1'b1) << 6), 8);
							state <= ST_CMD_ACCEPT;
						end
						8'd6: begin
							// Snapshot the live M10K render target into an RGBA DDR
							// texture. AM2R uses surface_copy(application_surface,
							// effect_surface) every frame in several effect rooms;
							// keeping that copy on the FPGA avoids a synchronous HPS
							// readback through uncached /dev/mem.
							framebuffer_write_present <= 0;
							framebuffer_write_base <= descriptor[1][31:3];
							framebuffer_write_index <= 0;
							state <= ST_FRAMEBUFFER_WRITE_START;
						end
						8'd7, 8'd8: begin
							// Batched full-width-source scanlines for AM2R's water
							// distortion. Each 64-bit table row contains destination x,
							// width, source x, and source y. The source row is streamed
							// once and blended into both M10K banks at one pair per beat.
							// Opcode 8 reads the immutable prior native XRGB buffer.
							src_base <= descriptor[1][31:0];
							src_stride <= descriptor[1][63:32];
							water_native_source <= descriptor[0][7:0] == 8'd8;
							tiled_mode <= 0;
							tiled_solid_mode <= 0;
							water_additive_mode <= 0;
							water_table_base <= descriptor[2][31:0];
							water_destination_start_y <= descriptor[2][47:32];
							water_row_count <= descriptor[0][31:16];
							water_row_index <= 0;
							tint_color <= descriptor[6][31:0];
							water_tint_alpha <= descriptor[6][31:24];
							water_pipe0_valid <= 0;
							water_pipe1_valid <= 0;
							water_pipe2_valid <= 0;
							water_pipe3_valid <= 0;
							water_pipe4_valid <= 0;
							water_table_load_index <= 0;
							// Fetch the compact row table as one DDR burst. Issuing a
							// separate one-beat transaction before every source row costs
							// more than the 240-row effect itself on real MiSTer DDR.
							if (descriptor[0][23:16] > GPU_READ_BURST_WORDS) begin
								water_table_burst_left <= GPU_READ_BURST_WORDS;
								start_read(descriptor[2][31:0], GPU_READ_BURST_WORDS);
							end else begin
								water_table_burst_left <= descriptor[0][23:16];
								start_read(descriptor[2][31:0], descriptor[0][23:16]);
							end
							state <= ST_WATER_TABLE_ACCEPT;
						end
						8'd9: begin
							// Repeated 32-pixel additive tile. The HPS emits this only
							// after proving that a clipped strip sequence is exactly one
							// contiguous 320-pixel repetition of the same source tile.
							src_base <= descriptor[1][31:0];
							src_stride <= descriptor[1][63:32];
							u_start <= descriptor[4][31:0];
							v_start <= descriptor[4][63:32];
							v_step <= descriptor[5][63:32];
							tint_color <= descriptor[6][31:0];
							water_tint_alpha <= descriptor[6][31:24];
							water_native_source <= 0;
							tiled_mode <= 1;
							tiled_solid_mode <= 0;
							water_additive_mode <= 1;
							water_row_index <= 0;
							water_row_count <= descriptor[0][47:32];
							water_destination_start_y <= descriptor[2][31:16];
							water_row_destination_x <= descriptor[2][15:0];
							water_row_width <= descriptor[0][31:16];
							tiled_phase <= descriptor[3][4:0];
							water_source_done <= 0;
							water_pipe0_valid <= 0;
							water_pipe1_valid <= 0;
							water_pipe2_valid <= 0;
							water_pipe3_valid <= 0;
							water_pipe4_valid <= 0;
							state <= ST_TILED_ROW_ADDRESS;
						end
						default: begin
							framebuffer_write_present <= 1;
							framebuffer_write_base <= native_buffer_address(present_buffer);
							framebuffer_write_index <= 0;
							state <= ST_PRESENT_WAIT;
						end
					endcase
				end
				ST_CLEAR: begin
					fb_even_write_address <= clear_index;
					fb_odd_write_address <= clear_index;
					fb_even_write_data <= clear_color;
					fb_odd_write_data <= clear_color;
					fb_even_we <= 1;
					fb_odd_we <= 1;
					if (clear_index == FB_WORDS-1) begin
						command_index <= command_index + 1'b1;
						command_word <= 0;
						start_read(command_addr + ((command_index + 1'b1) << 6), 8);
						state <= ST_CMD_ACCEPT;
					end else clear_index <= clear_index + 1'b1;
				end
				ST_BLIT_ROW: begin
					if (blit_y >= blit_height) begin
						command_index <= command_index + 1'b1;
						command_word <= 0;
						start_read(command_addr + ((command_index + 1'b1) << 6), 8);
						state <= ST_CMD_ACCEPT;
					end else begin
						u_current <= u_start;
						v_current <= v_start;
						tint_color <= {clamp_tint(tint_a_current), clamp_tint(tint_b_current),
						               clamp_tint(tint_g_current), clamp_tint(tint_r_current)};
						// Register both multiplier operands before calculating the row
						// address. This isolates the high-fanout UV/descriptor registers
						// from the DSP input path at the GPU clock.
						source_row_index_stage <= v_start >>> 16;
						source_stride_stage <= src_stride;
						state <= ST_ROW_SOURCE_ADDRESS;
					end
				end
				ST_ROW_SOURCE_ADDRESS: begin
					source_row_addr <= src_base + source_row_index_stage * source_stride_stage;
					state <= ST_BLIT_PIXEL;
				end
				ST_BLIT_PIXEL: begin
					destination_linear <= destination_linear_calc;
					if (affine_mode) begin
						source_row_index_stage <= v_current >>> 16;
						source_stride_stage <= src_stride;
					end else
						source_byte_addr <= source_row_addr + (u_current >>> 16) * 4;
					if (($signed(dst_x) + $signed({1'b0, blit_x})) < 0 ||
					    ($signed(dst_x) + $signed({1'b0, blit_x})) >= FB_WIDTH ||
					    ($signed(dst_y) + $signed({1'b0, blit_y})) < 0 ||
					    ($signed(dst_y) + $signed({1'b0, blit_y})) >= FB_HEIGHT ||
					    (affine_mode && (u_current < u_min || u_current >= u_max ||
					                     v_current < v_min || v_current >= v_max))) begin
						advance_blit_pixel();
					end else begin
						pair_mode <= !affine_mode && (solid_mode || u_step == 32'sh0001_0000) &&
						             blit_x + 1'b1 < blit_width &&
						             ($signed(dst_x) + $signed({1'b0, blit_x}) + 1) < FB_WIDTH &&
						             (solid_mode || !((source_row_addr + (u_current >>> 16) * 4) & 4));
						// Start the destination M10K read while an eligible pair's
						// texture/solid colour is prepared.  Partial-alpha solid fills
						// otherwise lose a full clock to address setup for every pair.
						if (!affine_mode &&
						    (solid_mode || u_step == 32'sh0001_0000) &&
						    blit_x + 1'b1 < blit_width &&
						    ($signed(dst_x) + $signed({1'b0, blit_x}) + 1) < FB_WIDTH &&
						    (solid_mode || !((source_row_addr + (u_current >>> 16) * 4) & 4))) begin
							destination_word <= destination_linear_calc[16:1];
							destination_odd <= destination_linear_calc[0];
							if (destination_linear_calc[0]) begin
								fb_odd_read_address <= destination_linear_calc[16:1];
								fb_even_read_address <= destination_linear_calc[16:1] + 1'b1;
							end else begin
								fb_even_read_address <= destination_linear_calc[16:1];
								fb_odd_read_address <= destination_linear_calc[16:1];
							end
						end
						if (affine_mode) begin
							state <= ST_AFFINE_ROW_ADDRESS;
						end else if (solid_mode &&
						             blit_x + 1'b1 < blit_width &&
						             ($signed(dst_x) + $signed({1'b0, blit_x}) + 1) < FB_WIDTH &&
						             (additive_mode ||
						              (tint_color[31:24] != 0 && tint_color[31:24] != 8'hff))) begin
							// The read above supplies ST_PAIR_BLEND_WRITE after the
							// existing one-cycle M10K latency.
							tinted_pixel <= tint_color;
							tinted_pixel_1 <= tint_color;
							state <= ST_PAIR_BLEND_READ;
						end else if (solid_mode) begin
							state <= ST_SOLID_READY;
						end else if (texture_cache_valid &&
						    texture_cache_addr == (((source_row_addr + (u_current >>> 16) * 4) >> 3) & 29'h1fff_fff0)) begin
							state <= ST_TEX_READY;
						end else begin
							texture_cache_fill <= 0;
							start_read((source_row_addr + (u_current >>> 16) * 4) & 32'hffff_ff80, 16);
							state <= ST_TEX_ACCEPT;
						end
					end
				end
				ST_AFFINE_ROW_ADDRESS: begin
					source_row_addr <= src_base + source_row_index_stage * source_stride_stage;
					state <= ST_AFFINE_SOURCE_ADDRESS;
				end
				ST_AFFINE_SOURCE_ADDRESS: begin
					source_byte_addr <= source_row_addr + (u_current >>> 16) * 4;
					state <= ST_AFFINE_ADDRESS;
				end
				ST_AFFINE_ADDRESS: begin
					if (texture_cache_valid &&
					    texture_cache_addr == ((source_byte_addr >> 3) & 29'h1fff_fff0)) begin
						state <= ST_TEX_READY;
					end else begin
						texture_cache_fill <= 0;
						start_read(source_byte_addr & 32'hffff_ff80, 16);
						state <= ST_TEX_ACCEPT;
					end
				end
				ST_TEX_ACCEPT: if (!ddram_busy) begin
					ddram_rd <= 0;
					state <= ST_TEX_DATA;
				end
				ST_TEX_DATA: if (ddram_dout_ready) begin
					texture_cache_data[texture_cache_fill] <= ddram_dout;
					if (texture_cache_fill == 15) begin
						texture_cache_valid <= 1;
						texture_cache_addr <= source_byte_addr[31:3] & 29'h1fff_fff0;
						state <= ST_TEX_READY;
					end else texture_cache_fill <= texture_cache_fill + 1'b1;
					end
					ST_SOLID_READY: begin
						destination_word <= destination_linear[16:1];
						destination_odd <= destination_linear[0];
						if (pair_mode && !additive_mode &&
						    (tint_color[31:24] == 0 || tint_color[31:24] == 8'hff)) begin
							// Opaque/transparent solid pairs need neither the tint staging
							// register nor an M10K destination read. Commit both lanes here.
							if (destination_linear[0]) begin
								fb_odd_write_address <= destination_linear[16:1];
								fb_odd_write_data <= tint_color;
								fb_odd_we <= tint_color[31:24] == 8'hff;
								fb_even_write_address <= destination_linear[16:1] + 1'b1;
								fb_even_write_data <= tint_color;
								fb_even_we <= tint_color[31:24] == 8'hff;
							end else begin
								fb_even_write_address <= destination_linear[16:1];
								fb_even_write_data <= tint_color;
								fb_even_we <= tint_color[31:24] == 8'hff;
								fb_odd_write_address <= destination_linear[16:1];
								fb_odd_write_data <= tint_color;
								fb_odd_we <= tint_color[31:24] == 8'hff;
							end
							advance_blit_pair();
						end else begin
							tinted_pixel <= tint_color;
							tinted_pixel_1 <= tint_color;
							if (pair_mode) begin
							if (destination_linear[0]) begin
								fb_odd_read_address <= destination_linear[16:1];
								fb_even_read_address <= destination_linear[16:1] + 1'b1;
							end else begin
								fb_even_read_address <= destination_linear[16:1];
								fb_odd_read_address <= destination_linear[16:1];
							end
							state <= ST_PAIR_BLEND_READ;
						end else begin
							fb_even_read_address <= destination_linear[16:1];
							fb_odd_read_address <= destination_linear[16:1];
							state <= ST_BLEND_READ;
							end
						end
					end
				ST_TEX_READY: begin
					if (pair_mode) begin
						destination_word <= destination_linear[16:1];
						destination_odd <= destination_linear[0];
						if (tint_color == 32'hffff_ffff && !additive_mode &&
						    (texture_cache_data[source_byte_addr[6:3]][31:24] == 0 ||
						     texture_cache_data[source_byte_addr[6:3]][31:24] == 8'hff) &&
						    (texture_cache_data[source_byte_addr[6:3]][63:56] == 0 ||
						     texture_cache_data[source_byte_addr[6:3]][63:56] == 8'hff)) begin
							// The common untinted sprite/background pair can bypass both
							// staging states and the destination read. Pairing is only enabled
							// for an aligned source double-pixel, so low/high lane order is fixed.
							if (destination_linear[0]) begin
								fb_odd_write_address <= destination_linear[16:1];
								fb_odd_write_data <= texture_cache_data[source_byte_addr[6:3]][31:0];
								fb_odd_we <= texture_cache_data[source_byte_addr[6:3]][31:24] == 8'hff;
								fb_even_write_address <= destination_linear[16:1] + 1'b1;
								fb_even_write_data <= texture_cache_data[source_byte_addr[6:3]][63:32];
								fb_even_we <= texture_cache_data[source_byte_addr[6:3]][63:56] == 8'hff;
							end else begin
								fb_even_write_address <= destination_linear[16:1];
								fb_even_write_data <= texture_cache_data[source_byte_addr[6:3]][31:0];
								fb_even_we <= texture_cache_data[source_byte_addr[6:3]][31:24] == 8'hff;
								fb_odd_write_address <= destination_linear[16:1];
								fb_odd_write_data <= texture_cache_data[source_byte_addr[6:3]][63:32];
								fb_odd_we <= texture_cache_data[source_byte_addr[6:3]][63:56] == 8'hff;
							end
							// Keep unit-step pairs inside the current 32-byte texture
							// cache line streaming through this state. The normal path
							// spent an address/setup clock between every pair even though
							// both the next source address and framebuffer word are known.
							// Re-enter address setup at cache-line and scanline boundaries.
							if (blit_x + 3 < blit_width &&
							    ($signed(dst_x) + $signed({1'b0, blit_x}) + 3) < FB_WIDTH &&
							    texture_cache_addr ==
							        ((((source_byte_addr + 32'd8) >> 3)) & 29'h1fff_fff0)) begin
								blit_x <= blit_x + 2;
								u_current <= u_current + (u_step <<< 1);
								v_current <= v_current + (v_x_step <<< 1);
								destination_linear <= destination_linear + 2;
								source_byte_addr <= source_byte_addr + 8;
								state <= ST_TEX_READY;
							end else begin
								advance_blit_pair();
							end
						end else if (!additive_mode && tint_color[23:0] == 24'hffffff &&
						             (texture_cache_data[source_byte_addr[6:3]][31:24] == 0 ||
						              texture_cache_data[source_byte_addr[6:3]][31:24] == 8'hff) &&
						             (texture_cache_data[source_byte_addr[6:3]][63:56] == 0 ||
						              texture_cache_data[source_byte_addr[6:3]][63:56] == 8'hff)) begin
							// AM2R's water distortion redraws opaque surface rows with
							// a white, partial-alpha tint.  With binary source alpha the
							// tint is just an alpha replacement, so the prefetched M10K
							// destination can be blended without the DSP tint stage.
							tinted_pixel <= {
								texture_cache_data[source_byte_addr[6:3]][31:24] == 8'hff ?
									tint_color[31:24] : 8'h00,
								texture_cache_data[source_byte_addr[6:3]][23:0]};
							tinted_pixel_1 <= {
								texture_cache_data[source_byte_addr[6:3]][63:56] == 8'hff ?
									tint_color[31:24] : 8'h00,
								texture_cache_data[source_byte_addr[6:3]][55:32]};
							state <= ST_PAIR_BLEND_READ;
						end else begin
							// Register cache selection before the optional four-channel tint.
							// Keeping the mux and DSP multiplies in one cycle has no route margin.
							source_pixel <= texture_cache_data[source_byte_addr[6:3]][31:0];
							source_pixel_1 <= texture_cache_data[source_byte_addr[6:3]][63:32];
							tint_color_stage <= tint_color;
							if (destination_linear[0]) begin
								fb_odd_read_address <= destination_linear[16:1];
								fb_even_read_address <= destination_linear[16:1] + 1'b1;
							end else begin
								fb_even_read_address <= destination_linear[16:1];
								fb_odd_read_address <= destination_linear[16:1];
							end
							state <= ST_PAIR_TINT_READY;
						end
					end else begin
						source_pixel <= source_byte_addr[2] ? texture_cache_data[source_byte_addr[6:3]][63:32] :
							texture_cache_data[source_byte_addr[6:3]][31:0];
						tint_color_stage <= tint_color;
						state <= ST_PIXEL_READY;
					end
				end
				ST_PAIR_TINT_READY: begin
					if (tint_color_stage == 32'hffff_ffff) begin
						tinted_pixel <= source_pixel;
						tinted_pixel_1 <= source_pixel_1;
						state <= ST_PAIR_BLEND_READ;
					end else begin
						// Register the eight DSP products separately from the exact
						// divide-by-255 rounding network. This provides comfortable
						// timing margin for generic tinted sprites.
						tint_product[0] <= source_pixel[31:24] * tint_color_stage[31:24];
						tint_product[1] <= source_pixel[23:16] * tint_color_stage[23:16];
						tint_product[2] <= source_pixel[15:8] * tint_color_stage[15:8];
						tint_product[3] <= source_pixel[7:0] * tint_color_stage[7:0];
						tint_product[4] <= source_pixel_1[31:24] * tint_color_stage[31:24];
						tint_product[5] <= source_pixel_1[23:16] * tint_color_stage[23:16];
						tint_product[6] <= source_pixel_1[15:8] * tint_color_stage[15:8];
						tint_product[7] <= source_pixel_1[7:0] * tint_color_stage[7:0];
						state <= ST_TINT_PRODUCT_READY;
					end
				end
				ST_PIXEL_READY: begin
					destination_word <= destination_linear[16:1];
					destination_odd <= destination_linear[0];
					fb_even_read_address <= destination_linear[16:1];
					fb_odd_read_address <= destination_linear[16:1];
					if (tint_color_stage == 32'hffff_ffff) begin
						tinted_pixel <= source_pixel;
						state <= ST_BLEND_READ;
					end else begin
						tint_product[0] <= source_pixel[31:24] * tint_color_stage[31:24];
						tint_product[1] <= source_pixel[23:16] * tint_color_stage[23:16];
						tint_product[2] <= source_pixel[15:8] * tint_color_stage[15:8];
						tint_product[3] <= source_pixel[7:0] * tint_color_stage[7:0];
						state <= ST_TINT_PRODUCT_READY;
					end
				end
				ST_TINT_PRODUCT_READY: begin
					tinted_pixel <= {
						round_mul255_product(tint_product[0]),
						round_mul255_product(tint_product[1]),
						round_mul255_product(tint_product[2]),
						round_mul255_product(tint_product[3])};
					if (pair_mode) begin
						tinted_pixel_1 <= {
							round_mul255_product(tint_product[4]),
							round_mul255_product(tint_product[5]),
							round_mul255_product(tint_product[6]),
							round_mul255_product(tint_product[7])};
						state <= ST_PAIR_BLEND_READ;
					end else state <= ST_BLEND_READ;
				end
				ST_BLEND_READ: begin
					if (tinted_pixel[31:24] == 0) advance_blit_pixel();
					else if (!additive_mode && tinted_pixel[31:24] == 8'hff) begin
						fb_even_write_address <= destination_word;
						fb_odd_write_address <= destination_word;
						if (destination_odd) begin
							fb_odd_write_data <= tinted_pixel;
							fb_odd_we <= 1;
						end else begin
							fb_even_write_data <= tinted_pixel;
							fb_even_we <= 1;
						end
						advance_blit_pixel();
					end else begin
						state <= ST_BLEND_WRITE;
					end
				end
				ST_BLEND_WRITE: begin
					if (destination_odd) begin
						blended_pixel_result0 <= blended_pixel(tinted_pixel, fb_odd_q);
					end else begin
						blended_pixel_result0 <= blended_pixel(tinted_pixel, fb_even_q);
					end
					state <= ST_BLEND_COMMIT;
				end
				ST_BLEND_COMMIT: begin
					fb_even_write_address <= destination_word;
					fb_odd_write_address <= destination_word;
					if (destination_odd) begin
						fb_odd_write_data <= blended_pixel_result0;
						fb_odd_we <= 1;
					end else begin
						fb_even_write_data <= blended_pixel_result0;
						fb_even_we <= 1;
					end
					advance_blit_pixel();
				end
				ST_PAIR_BLEND_READ: begin
					// The overwhelmingly common sprite/background pair is fully
					// opaque or transparent. It needs no destination read or blend:
					// write opaque lanes directly, skip transparent lanes, and save
					// one GPU clock per pair. Partial-alpha and additive pairs retain
					// the existing read/modify/write path below.
					if (!additive_mode &&
					    (tinted_pixel[31:24] == 0 || tinted_pixel[31:24] == 8'hff) &&
					    (tinted_pixel_1[31:24] == 0 || tinted_pixel_1[31:24] == 8'hff)) begin
						if (destination_odd) begin
							fb_odd_write_address <= destination_word;
							fb_odd_write_data <= tinted_pixel;
							fb_odd_we <= tinted_pixel[31:24] == 8'hff;
							fb_even_write_address <= destination_word + 1'b1;
							fb_even_write_data <= tinted_pixel_1;
							fb_even_we <= tinted_pixel_1[31:24] == 8'hff;
						end else begin
							fb_even_write_address <= destination_word;
							fb_even_write_data <= tinted_pixel;
							fb_even_we <= tinted_pixel[31:24] == 8'hff;
							fb_odd_write_address <= destination_word;
							fb_odd_write_data <= tinted_pixel_1;
							fb_odd_we <= tinted_pixel_1[31:24] == 8'hff;
						end
						advance_blit_pair();
					end else if (solid_mode) begin
						// ST_SOLID_READY launches the M10K read one state later than the
						// texture path, so partial-alpha solid pairs still need this delay.
						state <= ST_PAIR_BLEND_WRITE;
					end else begin
						// The destination data and both tinted source pixels have already
						// crossed their register stages. Register the blend before the M10K
						// write so the DSP/divide network cannot become a RAM input path.
						if (destination_odd) begin
							blended_pixel_result0 <= blended_pixel(tinted_pixel, fb_odd_q);
							blended_pixel_result1 <= blended_pixel(tinted_pixel_1, fb_even_q);
						end else begin
							blended_pixel_result0 <= blended_pixel(tinted_pixel, fb_even_q);
							blended_pixel_result1 <= blended_pixel(tinted_pixel_1, fb_odd_q);
						end
						state <= ST_PAIR_BLEND_COMMIT;
					end
				end
				ST_PAIR_BLEND_WRITE: begin
					if (destination_odd) begin
						blended_pixel_result0 <= blended_pixel(tinted_pixel, fb_odd_q);
						blended_pixel_result1 <= blended_pixel(tinted_pixel_1, fb_even_q);
					end else begin
						blended_pixel_result0 <= blended_pixel(tinted_pixel, fb_even_q);
						blended_pixel_result1 <= blended_pixel(tinted_pixel_1, fb_odd_q);
					end
					state <= ST_PAIR_BLEND_COMMIT;
				end
				ST_PAIR_BLEND_COMMIT: begin
					if (destination_odd) begin
						fb_odd_write_address <= destination_word;
						fb_odd_write_data <= blended_pixel_result0;
						fb_even_write_address <= destination_word + 1'b1;
						fb_even_write_data <= blended_pixel_result1;
					end else begin
						fb_even_write_address <= destination_word;
						fb_even_write_data <= blended_pixel_result0;
						fb_odd_write_address <= destination_word;
						fb_odd_write_data <= blended_pixel_result1;
					end
					fb_even_we <= 1;
					fb_odd_we <= 1;
					advance_blit_pair();
				end
				ST_FRAMEBUFFER_WRITE_START: begin
					ddram_addr <= framebuffer_write_base + framebuffer_write_index;
					framebuffer_write_burst_start <= framebuffer_write_index;
					if (FB_WORDS - framebuffer_write_index >
					    (framebuffer_write_present ? PRESENT_WRITE_BURST_WORDS :
					                                 EXPORT_WRITE_BURST_WORDS)) begin
						ddram_burstcnt <= framebuffer_write_present ?
							PRESENT_WRITE_BURST_WORDS : EXPORT_WRITE_BURST_WORDS;
						framebuffer_write_burst_length <= framebuffer_write_present ?
							PRESENT_WRITE_BURST_WORDS : EXPORT_WRITE_BURST_WORDS;
						framebuffer_write_beats_left <= framebuffer_write_present ?
							PRESENT_WRITE_BURST_WORDS : EXPORT_WRITE_BURST_WORDS;
					end else begin
						ddram_burstcnt <= FB_WORDS - framebuffer_write_index;
						framebuffer_write_burst_length <= FB_WORDS - framebuffer_write_index;
						framebuffer_write_beats_left <= FB_WORDS - framebuffer_write_index;
					end
					ddram_be <= 8'hff;
					ddram_we <= 0;
					framebuffer_reads_issued <= 0;
					framebuffer_read_valid <= 0;
					framebuffer_fifo_read_ptr <= 0;
					framebuffer_fifo_write_ptr <= 0;
					framebuffer_fifo_count <= 0;
					state <= ST_FRAMEBUFFER_WRITE_FILL;
				end
				ST_FRAMEBUFFER_WRITE_FILL: begin
					framebuffer_read_valid[1] <= framebuffer_read_valid[0];
					framebuffer_read_valid[0] <= 0;
					if (framebuffer_read_valid[1]) begin
						framebuffer_fifo[framebuffer_fifo_write_ptr] <= {fb_odd_q, fb_even_q};
						framebuffer_fifo_write_ptr <= framebuffer_fifo_write_ptr + 1'b1;
						framebuffer_fifo_count <= framebuffer_fifo_count + 1'b1;
					end
					if (framebuffer_reads_issued < framebuffer_write_burst_length &&
					    framebuffer_fifo_count + framebuffer_read_valid[0] +
					    framebuffer_read_valid[1] < WRITE_FIFO_DEPTH) begin
						fb_even_read_address <= framebuffer_write_burst_start + framebuffer_reads_issued;
						fb_odd_read_address <= framebuffer_write_burst_start + framebuffer_reads_issued;
						framebuffer_reads_issued <= framebuffer_reads_issued + 1'b1;
						framebuffer_read_valid[0] <= 1;
					end
					if (framebuffer_fifo_count >= 4 ||
					    (framebuffer_reads_issued == framebuffer_write_burst_length &&
					     framebuffer_read_valid == 0 && framebuffer_fifo_count != 0)) begin
						ddram_we <= 1;
						state <= ST_FRAMEBUFFER_WRITE_STREAM;
					end
				end
				ST_FRAMEBUFFER_WRITE_STREAM: begin
					framebuffer_read_valid[1] <= framebuffer_read_valid[0];
					framebuffer_read_valid[0] <= 0;
					if (framebuffer_read_valid[1]) begin
						framebuffer_fifo[framebuffer_fifo_write_ptr] <= {fb_odd_q, fb_even_q};
						framebuffer_fifo_write_ptr <= framebuffer_fifo_write_ptr + 1'b1;
					end

					if (framebuffer_reads_issued < framebuffer_write_burst_length &&
					    framebuffer_fifo_count + framebuffer_read_valid[0] +
					    framebuffer_read_valid[1] -
					    ((ddram_we && !ddram_busy) ? 1'b1 : 1'b0) < WRITE_FIFO_DEPTH) begin
						fb_even_read_address <= framebuffer_write_burst_start + framebuffer_reads_issued;
						fb_odd_read_address <= framebuffer_write_burst_start + framebuffer_reads_issued;
						framebuffer_reads_issued <= framebuffer_reads_issued + 1'b1;
						framebuffer_read_valid[0] <= 1;
					end

					case ({framebuffer_read_valid[1], ddram_we && !ddram_busy})
						2'b10: framebuffer_fifo_count <= framebuffer_fifo_count + 1'b1;
						2'b01: framebuffer_fifo_count <= framebuffer_fifo_count - 1'b1;
						default: framebuffer_fifo_count <= framebuffer_fifo_count;
					endcase

					if (ddram_we && !ddram_busy) begin
						framebuffer_fifo_read_ptr <= framebuffer_fifo_read_ptr + 1'b1;
						framebuffer_write_index <= framebuffer_write_index + 1'b1;
						if (framebuffer_write_beats_left == 1) begin
							ddram_we <= 0;
							framebuffer_read_valid <= 0;
							if (framebuffer_write_index == FB_WORDS-1) begin
								if (framebuffer_write_present) begin
									native_buffer <= present_buffer;
									native_frame <= native_frame + 1'b1;
									present_buffer <= next_present_buffer(
										present_buffer, scan_buffer_valid, scan_buffer);
									state <= ST_COMPLETE;
								end else begin
									command_index <= command_index + 1'b1;
									command_word <= 0;
									start_read(command_addr + ((command_index + 1'b1) << 6), 8);
									state <= ST_CMD_ACCEPT;
								end
							end else state <= ST_FRAMEBUFFER_WRITE_START;
						end else begin
							framebuffer_write_beats_left <= framebuffer_write_beats_left - 1'b1;
						end
					end
				end
				ST_TILED_ROW_ADDRESS: begin
					// Register the row multiply separately from the DDR address.
					source_row_index_stage <= v_start >>> 16;
					source_stride_stage <= src_stride;
					water_destination_base <=
						((water_destination_start_y + water_row_index) << 8) +
						((water_destination_start_y + water_row_index) << 6) +
						water_row_destination_x;
					state <= ST_TILED_SOURCE_ADDRESS;
				end
				ST_TILED_SOURCE_ADDRESS: begin
					source_row_addr <= src_base +
						source_row_index_stage * source_stride_stage;
					state <= ST_TILED_SOURCE_ACCEPT;
				end
				ST_TILED_SOURCE_ACCEPT: begin
					texture_cache_fill <= 0;
					start_read(source_row_addr + (u_start >>> 16) * 4, 16);
					state <= ST_TILED_SOURCE_DATA;
				end
				ST_TILED_SOURCE_DATA: begin
					if (!ddram_busy && ddram_rd) ddram_rd <= 0;
					if (ddram_dout_ready) begin
						texture_cache_data[texture_cache_fill] <= ddram_dout;
						if (texture_cache_fill == 15) begin
							tiled_x <= 0;
							water_source_done <= 0;
							water_pipe0_valid <= 0;
							water_pipe1_valid <= 0;
							water_pipe2_valid <= 0;
							water_pipe3_valid <= 0;
							water_pipe4_valid <= 0;
							state <= ST_WATER_SOURCE_DATA;
						end else texture_cache_fill <= texture_cache_fill + 1'b1;
					end
				end
				ST_WATER_TABLE_ACCEPT: if (!ddram_busy) begin
					ddram_rd <= 0;
					state <= ST_WATER_TABLE_DATA;
				end
				ST_WATER_TABLE_DATA: if (ddram_dout_ready) begin
					water_table_cache[water_table_load_index] <= ddram_dout;
					water_table_burst_left <= water_table_burst_left - 1'b1;
					if (water_table_load_index + 1'b1 >= water_row_count[7:0]) begin
						water_row_index <= 0;
						state <= ST_WATER_ROW_CACHE_ADDRESS;
					end else begin
						water_table_load_index <= water_table_load_index + 1'b1;
						if (water_table_burst_left == 1) begin
							if (water_row_count[7:0] - water_table_load_index - 1'b1 >
							    GPU_READ_BURST_WORDS) begin
								water_table_burst_left <= GPU_READ_BURST_WORDS;
								start_read(water_table_base +
									((water_table_load_index + 1'b1) << 3),
									GPU_READ_BURST_WORDS);
							end else begin
								water_table_burst_left <= water_row_count[7:0] -
									water_table_load_index - 1'b1;
								start_read(water_table_base +
									((water_table_load_index + 1'b1) << 3),
									water_row_count[7:0] -
									water_table_load_index - 1'b1);
							end
							state <= ST_WATER_TABLE_ACCEPT;
						end
					end
				end
				ST_WATER_ROW_CACHE_ADDRESS: begin
					water_table_cache_q <= water_table_cache[water_row_index[7:0]];
					state <= ST_WATER_ROW_CACHE_DATA;
				end
				ST_WATER_ROW_CACHE_DATA: begin
					water_row_destination_x <= water_table_cache_q[15:0];
					water_row_width <= water_table_cache_q[31:16];
					water_row_source_x <= water_table_cache_q[47:32];
					water_row_source_y <= water_table_cache_q[63:48];
					water_destination_base <=
						((water_destination_start_y + water_row_index) << 8) +
						((water_destination_start_y + water_row_index) << 6) +
						water_table_cache_q[15:0];
					water_source_word <= 0;
					water_source_done <= 0;
					water_pipe0_valid <= 0;
					water_pipe1_valid <= 0;
					water_pipe2_valid <= 0;
					water_pipe3_valid <= 0;
					water_pipe4_valid <= 0;
					state <= ST_WATER_SOURCE_ADDRESS;
				end
				ST_WATER_SOURCE_ADDRESS: begin
					// The ARM recognizer admits only 1,280-byte source rows. Build
					// that offset from shifts after registering the DDR table word;
					// this keeps the HPS DDR output off the address critical path.
					if (8'd160 - water_source_word > GPU_READ_BURST_WORDS)
						water_source_burst_left <= GPU_READ_BURST_WORDS;
					else water_source_burst_left <= 8'd160 - water_source_word;
					start_read((water_native_source ?
						{native_buffer_address(native_buffer), 3'b000} : src_base) +
						({16'b0, water_row_source_y} << 10) +
						({16'b0, water_row_source_y} << 8) +
						({21'b0, water_source_word} << 3),
						(8'd160 - water_source_word > GPU_READ_BURST_WORDS) ?
							GPU_READ_BURST_WORDS : 8'd160 - water_source_word);
					state <= ST_WATER_SOURCE_ACCEPT;
				end
				ST_WATER_SOURCE_ACCEPT: begin
					if (!ddram_busy) begin
						ddram_rd <= 0;
						state <= ST_WATER_SOURCE_DATA;
					end
				end
				ST_WATER_SOURCE_DATA: begin
					// Destination RAM is synchronous. Pipeline each accepted DDR
					// pair through address setup and one registered read stage;
					// once full, the path sustains two blended pixels per clock.
					water_pipe1_valid <= water_pipe0_valid;
					water_pipe1_pixel0 <= water_pipe0_pixel0;
					water_pipe1_pixel1 <= water_pipe0_pixel1;
					water_pipe1_destination0 <= water_pipe0_destination0;
					water_pipe1_destination1 <= water_pipe0_destination1;
					water_pipe1_odd0 <= water_pipe0_odd0;
					water_pipe1_odd1 <= water_pipe0_odd1;
					water_pipe2_valid <= water_pipe1_valid;
					water_pipe2_pixel0 <= {
						mul255(water_pipe1_pixel0[31:24], water_tint_alpha),
						water_pipe1_pixel0[23:0]};
					water_pipe2_pixel1 <= {
						mul255(water_pipe1_pixel1[31:24], water_tint_alpha),
						water_pipe1_pixel1[23:0]};
					water_pipe2_background0 <= water_pipe1_odd0 ? fb_odd_q : fb_even_q;
					water_pipe2_background1 <= water_pipe1_odd1 ? fb_odd_q : fb_even_q;
					water_pipe2_destination0 <= water_pipe1_destination0;
					water_pipe2_destination1 <= water_pipe1_destination1;
					water_pipe2_odd0 <= water_pipe1_odd0;
					water_pipe2_odd1 <= water_pipe1_odd1;
					water_pipe3_valid <= water_pipe2_valid;
					water_pipe3_source_r[0] <=
						water_pipe2_pixel0[7:0] * water_pipe2_pixel0[31:24];
					water_pipe3_source_g[0] <=
						water_pipe2_pixel0[15:8] * water_pipe2_pixel0[31:24];
					water_pipe3_source_b[0] <=
						water_pipe2_pixel0[23:16] * water_pipe2_pixel0[31:24];
					water_pipe3_destination_r[0] <= water_pipe2_background0[7:0] *
						(8'd255 - water_pipe2_pixel0[31:24]);
					water_pipe3_destination_g[0] <= water_pipe2_background0[15:8] *
						(8'd255 - water_pipe2_pixel0[31:24]);
					water_pipe3_destination_b[0] <= water_pipe2_background0[23:16] *
						(8'd255 - water_pipe2_pixel0[31:24]);
					water_pipe3_destination_a[0] <= water_pipe2_background0[31:24] *
						(8'd255 - water_pipe2_pixel0[31:24]);
					water_pipe3_source_a[0] <= water_pipe2_pixel0[31:24];
					water_pipe3_destination[0] <= water_pipe2_destination0;
					water_pipe3_odd[0] <= water_pipe2_odd0;
					water_pipe3_background[0] <= water_pipe2_background0;
					water_pipe3_source_r[1] <=
						water_pipe2_pixel1[7:0] * water_pipe2_pixel1[31:24];
					water_pipe3_source_g[1] <=
						water_pipe2_pixel1[15:8] * water_pipe2_pixel1[31:24];
					water_pipe3_source_b[1] <=
						water_pipe2_pixel1[23:16] * water_pipe2_pixel1[31:24];
					water_pipe3_destination_r[1] <= water_pipe2_background1[7:0] *
						(8'd255 - water_pipe2_pixel1[31:24]);
					water_pipe3_destination_g[1] <= water_pipe2_background1[15:8] *
						(8'd255 - water_pipe2_pixel1[31:24]);
					water_pipe3_destination_b[1] <= water_pipe2_background1[23:16] *
						(8'd255 - water_pipe2_pixel1[31:24]);
					water_pipe3_destination_a[1] <= water_pipe2_background1[31:24] *
						(8'd255 - water_pipe2_pixel1[31:24]);
					water_pipe3_source_a[1] <= water_pipe2_pixel1[31:24];
					water_pipe3_destination[1] <= water_pipe2_destination1;
					water_pipe3_odd[1] <= water_pipe2_odd1;
					water_pipe3_background[1] <= water_pipe2_background1;
					// Register the divide-by-255 result separately from both the DSP
					// products and the framebuffer write port.  This keeps the exact
					// GameMaker floor blend while sustaining one pixel pair per clock.
					water_pipe4_valid[0] <= water_pipe3_valid[0] &&
						water_pipe3_source_a[0] != 0;
					water_pipe4_pixel[0] <= water_additive_mode ? {
						saturating_add(water_pipe3_background[0][31:24],
							water_pipe3_source_a[0]),
						saturating_add(water_pipe3_background[0][23:16],
							div255_floor({1'b0, water_pipe3_source_b[0]})),
						saturating_add(water_pipe3_background[0][15:8],
							div255_floor({1'b0, water_pipe3_source_g[0]})),
						saturating_add(water_pipe3_background[0][7:0],
							div255_floor({1'b0, water_pipe3_source_r[0]}))} : {
						water_pipe3_source_a[0] +
							div255_floor({1'b0, water_pipe3_destination_a[0]}),
						div255_floor({1'b0, water_pipe3_source_b[0]} +
							{1'b0, water_pipe3_destination_b[0]}),
						div255_floor({1'b0, water_pipe3_source_g[0]} +
							{1'b0, water_pipe3_destination_g[0]}),
						div255_floor({1'b0, water_pipe3_source_r[0]} +
							{1'b0, water_pipe3_destination_r[0]})};
					water_pipe4_destination[0] <= water_pipe3_destination[0];
					water_pipe4_odd[0] <= water_pipe3_odd[0];
					water_pipe4_valid[1] <= water_pipe3_valid[1] &&
						water_pipe3_source_a[1] != 0;
					water_pipe4_pixel[1] <= water_additive_mode ? {
						saturating_add(water_pipe3_background[1][31:24],
							water_pipe3_source_a[1]),
						saturating_add(water_pipe3_background[1][23:16],
							div255_floor({1'b0, water_pipe3_source_b[1]})),
						saturating_add(water_pipe3_background[1][15:8],
							div255_floor({1'b0, water_pipe3_source_g[1]})),
						saturating_add(water_pipe3_background[1][7:0],
							div255_floor({1'b0, water_pipe3_source_r[1]}))} : {
						water_pipe3_source_a[1] +
							div255_floor({1'b0, water_pipe3_destination_a[1]}),
						div255_floor({1'b0, water_pipe3_source_b[1]} +
							{1'b0, water_pipe3_destination_b[1]}),
						div255_floor({1'b0, water_pipe3_source_g[1]} +
							{1'b0, water_pipe3_destination_g[1]}),
						div255_floor({1'b0, water_pipe3_source_r[1]} +
							{1'b0, water_pipe3_destination_r[1]})};
					water_pipe4_destination[1] <= water_pipe3_destination[1];
					water_pipe4_odd[1] <= water_pipe3_odd[1];
					water_pipe0_valid <= 0;

					if (water_pipe4_valid[0]) begin
						if (water_pipe4_odd[0]) begin
							fb_odd_write_address <= water_pipe4_destination[0];
							fb_odd_write_data <= water_pipe4_pixel[0];
							fb_odd_we <= 1;
						end else begin
							fb_even_write_address <= water_pipe4_destination[0];
							fb_even_write_data <= water_pipe4_pixel[0];
							fb_even_we <= 1;
						end
					end
					if (water_pipe4_valid[1]) begin
						if (water_pipe4_odd[1]) begin
							fb_odd_write_address <= water_pipe4_destination[1];
							fb_odd_write_data <= water_pipe4_pixel[1];
							fb_odd_we <= 1;
						end else begin
							fb_even_write_address <= water_pipe4_destination[1];
							fb_even_write_data <= water_pipe4_pixel[1];
							fb_even_we <= 1;
						end
					end

					if (tiled_mode && !water_source_done) begin
						water_pipe0_valid <= {
							tiled_x + 1'b1 < water_row_width,
							tiled_x < water_row_width};
						water_pipe0_pixel0 <= tiled_source_pixel0;
						water_pipe0_pixel1 <= tiled_source_pixel1;
						water_pipe0_destination0 <= tiled_destination_linear0[16:1];
						water_pipe0_destination1 <= tiled_destination_linear1[16:1];
						water_pipe0_odd0 <= tiled_destination_linear0[0];
						water_pipe0_odd1 <= tiled_destination_linear1[0];
						if (tiled_x < water_row_width) begin
							if (tiled_destination_linear0[0])
								fb_odd_read_address <= tiled_destination_linear0[16:1];
							else fb_even_read_address <= tiled_destination_linear0[16:1];
						end
						if (tiled_x + 1'b1 < water_row_width) begin
							if (tiled_destination_linear1[0])
								fb_odd_read_address <= tiled_destination_linear1[16:1];
							else fb_even_read_address <= tiled_destination_linear1[16:1];
						end
						if (tiled_x + 2 >= water_row_width)
							water_source_done <= 1;
						else tiled_x <= tiled_x + 2;
					end else if (!tiled_mode && ddram_dout_ready) begin
						water_source_burst_left <= water_source_burst_left - 1'b1;
						water_pipe0_valid <= {water_source_valid1, water_source_valid0};
						water_pipe0_pixel0 <= water_native_source ?
							xrgb_to_opaque_rgba(ddram_dout[31:0]) : ddram_dout[31:0];
						water_pipe0_pixel1 <= water_native_source ?
							xrgb_to_opaque_rgba(ddram_dout[63:32]) : ddram_dout[63:32];
						water_pipe0_destination0 <= water_destination_linear0[16:1];
						water_pipe0_destination1 <= water_destination_linear1[16:1];
						water_pipe0_odd0 <= water_destination_linear0[0];
						water_pipe0_odd1 <= water_destination_linear1[0];
						if (water_source_valid0) begin
							if (water_destination_linear0[0])
								fb_odd_read_address <= water_destination_linear0[16:1];
							else fb_even_read_address <= water_destination_linear0[16:1];
						end
						if (water_source_valid1) begin
							if (water_destination_linear1[0])
								fb_odd_read_address <= water_destination_linear1[16:1];
							else fb_even_read_address <= water_destination_linear1[16:1];
						end
						if (water_source_word == 8'd159) begin
							water_source_done <= 1;
						end else begin
							water_source_word <= water_source_word + 1'b1;
							if (water_source_burst_left == 1)
								state <= ST_WATER_SOURCE_ADDRESS;
						end
					end

					if (water_source_done &&
					    (tiled_mode || !ddram_dout_ready) &&
					    water_pipe0_valid == 0 && water_pipe1_valid == 0 &&
					    water_pipe2_valid == 0 && water_pipe3_valid == 0 &&
					    water_pipe4_valid == 0) begin
						if (water_row_index + 1'b1 >= water_row_count) begin
							tiled_mode <= 0;
							tiled_solid_mode <= 0;
							water_additive_mode <= 0;
							command_index <= command_index + 1'b1;
							command_word <= 0;
							start_read(command_addr + ((command_index + 1'b1) << 6), 8);
							state <= ST_CMD_ACCEPT;
						end else begin
							water_row_index <= water_row_index + 1'b1;
							if (tiled_mode) begin
								tiled_x <= 0;
								water_source_done <= 0;
								if (tiled_solid_mode) begin
									water_destination_base <= water_destination_base + FB_WIDTH;
									state <= ST_WATER_SOURCE_DATA;
								end else begin
									v_start <= v_start + v_step;
									state <= ST_TILED_ROW_ADDRESS;
								end
							end else state <= ST_WATER_ROW_CACHE_ADDRESS;
						end
					end
				end
				ST_PRESENT_WAIT: begin
					// Never overwrite the buffer the video reader has latched for
					// this raster. Fast/simple game frames may otherwise lap vblank.
					if (!scan_buffer_valid || present_buffer != scan_buffer) begin
						state <= ST_FRAMEBUFFER_WRITE_START;
					end
				end
				ST_COMPLETE: begin
					ddram_addr <= (CONTROL_ADDR + 24) >> 3;
					ddram_burstcnt <= 1;
					// The completion word reports which native DDR buffer now
					// contains the finished frame. The HPS renderer uses these bits
					// for exact mid-frame readback; cycle counts remain independent.
					// Bit 29 reports a scanout FIFO underflow since the preceding
					// completed job. Twenty-nine cycle bits still cover over six
					// seconds at 88 MHz.
					ddram_din_control <= {{native_buffer,
					               scan_underflow_sync[1] != scan_underflow_seen,
					               operation_cycles[28:0]}, active_sequence};
					ddram_be <= 8'hff;
					ddram_we <= 1;
					if (ddram_we && !ddram_busy) begin
						ddram_we <= 0;
						scan_underflow_seen <= scan_underflow_sync[1];
						last_sequence <= active_sequence;
						poll_delay <= 16'd4095;
						state <= ST_POLL_DELAY;
					end
				end
				default: state <= ST_RESET;
			endcase
		end
	end

endmodule
