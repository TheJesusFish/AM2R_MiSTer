//============================================================================
// AM2R fixed-function GPU
//
// Implements the subset which dominates AM2R: clear, axis-aligned nearest-
// neighbour RGBA blits, normal/additive alpha blending, and framebuffer
// presentation. Descriptor bit 8 selects additive blending for blit/fill.
// Axis-blit bit 10 selects exact floor tint for CPU offscreen pixel parity.
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
	output      [7:0]  ddram_be,
	output reg         ddram_we,
	input              scan_buffer_valid,
	input       [1:0]  scan_buffer,
	// Native buffers the HDMI framebuffer reader may still be reading.
	input       [3:0]  hdmi_protect,
	input              scan_underflow_toggle,
	output reg  [31:0] native_frame,
	output reg  [1:0]  native_buffer
);

	localparam [31:0] CONTROL_ADDR = 32'h23ff_0000;
	localparam [28:0] CONTROL_WORD_ADDR = CONTROL_ADDR[31:3];
	localparam [31:0] CONTROL_MAGIC = 32'h5047_3241; // "A2GP" in memory
	localparam [31:0] CAPABILITY_MAGIC = 32'h4347_3241; // "A2GC" in memory
	localparam [31:0] CAPABILITY_FEATURES = 32'h0000_001f; // floor tint, RGBA DMA, fence, generic primitives, bounded RGBA replace
	localparam [28:0] NATIVE_BUF0 = 29'h0740_0020; // 0x3a000100 >> 3
	localparam [28:0] NATIVE_BUF1 = 29'h0740_9620; // 0x3a04b100 >> 3
	localparam [28:0] NATIVE_BUF2 = 29'h0741_2c20; // 0x3a096100 >> 3
	localparam [28:0] NATIVE_BUF3 = 29'h0741_c220; // 0x3a0e1100 >> 3
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
		ST_TILED_SOURCE_ACCEPT = 49, ST_TILED_SOURCE_DATA = 50,
		ST_SUBTRACT_PRODUCT = 51, ST_SUBTRACT_RESULT = 52,
		ST_CAPABILITY_WRITE = 53, ST_CAPABILITY_ACCEPT = 54,
		ST_SURFACE_BURST = 55, ST_SURFACE_LOAD_ACCEPT = 56,
		ST_SURFACE_LOAD_DATA = 57, ST_SURFACE_STORE_FILL = 58,
		ST_SURFACE_STORE_STREAM = 59, ST_SURFACE_NEXT_ROW = 60,
		ST_GENERIC = 61, ST_BLEND_CALCULATE = 62, ST_RECT_CLEAR = 63;
	localparam [3:0] GP_PACKET_ACCEPT = 0, GP_PACKET_DATA = 1,
		GP_INIT = 2, GP_PIXEL = 3, GP_ADDRESS = 4, GP_TEXTURE = 5,
		GP_TEXTURE_ACCEPT = 6, GP_TEXTURE_DATA = 7, GP_TINT = 8,
		GP_TINT_RESULT = 9, GP_FACTORS = 10, GP_PRODUCTS = 11,
		GP_RESULT = 12, GP_COMMIT = 13;

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
	reg signed [31:0] u_current, u_next, v_current;
	reg signed [31:0] u_min, u_max, v_min, v_max;
	// Keep the completed row address at this pipeline boundary. Retiming it
	// into the row multiply joins its base add to the next pixel/cache lookup
	// and DDR request enable, exceeding the GPU clock budget.
	(* preserve *) reg [31:0] source_row_addr;
	reg [31:0] source_byte_addr, source_byte_addr_1, tint_color;
	// Split the row multiply across the existing setup/address boundary.
	// Independent 16x16 products avoid a serial two-DSP multiply plus base add
	// in one GPU clock. Preserve their output registers against retiming.
	(* preserve *) reg [31:0] source_row_product_low;
	(* preserve *) reg [15:0] source_row_product_high;
	reg [31:0] tint_color_stage;
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
	(* preserve *) reg [31:0] blend_destination0, blend_destination1;
	// The existing CALCULATE/COMMIT stages split DSP products from the
	// weighted sum, /255 and saturation. No extra pixel or pair cycle.
	(* preserve *) reg [47:0] blend_source_product0, blend_source_product1;
	(* preserve *) reg [63:0] blend_destination_product0, blend_destination_product1;
	reg [15:0] tint_product [0:7];
	reg [31:0] subtract_destination0, subtract_destination1;
	reg [31:0] subtract_factor0, subtract_factor1;
	reg [15:0] subtract_product [0:7];
	reg solid_mode;
	reg additive_mode;
	reg subtract_mode;
	reg floor_tint_mode;
	reg affine_mode;
	reg [31:0] affine_tint_config;
	reg affine_additive_config;
	reg affine_subtract_config;
	reg [16:0] destination_linear;
	reg [15:0] destination_word;
	reg destination_odd;
	reg pair_mode;
	reg contiguous_pair_mode;
	reg [63:0] ddram_din_control;
	reg [7:0] ddram_be_control;
	// The existing 320x240 BRAM target is also a tile workspace. Rectangular
	// raw-RGBA DMA preserves every pixel outside the rectangle (including the
	// unused lane of a partial DDR word), without allocating another target.
	reg surface_store;
	reg [31:0] surface_row_addr, surface_stride;
	reg [15:0] surface_width, surface_height, surface_row;
	reg [16:0] surface_row_linear;
	reg [8:0] surface_row_words, surface_word_index;
	reg surface_first_lane, surface_pixel_odd;
	reg [7:0] surface_be_pipe0, surface_be_pipe1;
	reg [7:0] surface_fifo_be [0:WRITE_FIFO_DEPTH-1];
	// Generic primitive packet AGP1: 64 qwords fetched once, 39 defined.
	// All planes are signed Q31.32, already evaluated at the first clipped
	// pixel centre by the host. The FPGA alone walks pixels and rows.
	reg [3:0] generic_state;
	reg [5:0] generic_packet_word;
	reg generic_packet_invalid;
	reg [63:0] generic_packet [0:38];
	reg [15:0] generic_width, generic_height, generic_x, generic_y;
	reg [16:0] generic_row_linear, generic_linear;
	reg signed [63:0] generic_edge_row [0:2], generic_edge [0:2];
	reg signed [63:0] generic_uv_row [0:1], generic_uv [0:1];
	reg signed [63:0] generic_color_row [0:3], generic_color [0:3];
	reg signed [63:0] generic_color_dx [0:3];
	reg [15:0] generic_texture_width, generic_texture_height, generic_texture_x;
	reg generic_textured, generic_triangle, generic_alpha_test, generic_fog, generic_blend;
	reg [7:0] generic_blend_mode, generic_alpha_ref;
	reg [3:0] generic_write_mask;
	reg [31:0] generic_fog_color, generic_factor_ids;
	reg [40:0] generic_tint_product [0:3];
	reg [31:0] generic_destination, generic_result;
	reg [7:0] generic_source_factor [0:3], generic_destination_factor [0:3];
	(* multstyle = "logic" *) reg [15:0] generic_source_product [0:3], generic_destination_product [0:3];
	integer generic_channel;
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
	// General axis rectangles share the two-pixel tint/blend pipeline. Unit-U
	// rows stream DDR directly; scaled/mirrored rows gather the existing cache.
	reg axis_stream_mode, axis_gather_mode, water_subtract_mode;
	reg [31:0] axis_stream_row_address;
	reg [16:0] axis_stream_width_stage;
	reg gather_lookup, gather_refilling;
	reg [8:0] gather_remaining;
	reg [1:0] gather_pending;
	reg [16:0] gather_destination, gather_pair_destination;
	// 0=first visible sample needs setup,1=decision ready,2=legacy rest of row.
	reg [1:0] axis_stream_setup;
	reg [7:0] water_tint_alpha;
	reg [15:0] tiled_x;
	reg [4:0] tiled_phase;
	reg [31:0] tiled_solid_pixel;
	reg [1:0] water_pipe0_valid, water_pipe1_valid, water_pipe2_valid;
	reg [1:0] water_pipe3_valid, water_pipe4_valid;
	reg [31:0] water_pipe0_pixel0, water_pipe0_pixel1;
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
	reg completion_underflow_snapshot;
	wire [16:0] destination_linear_calc =
		($signed(dst_y) + $signed({1'b0, blit_y})) * FB_WIDTH +
		$signed(dst_x) + $signed({1'b0, blit_x});
	// Maintain the next U accumulator in parallel with the current one. This
	// avoids a dependent U-add/address-add/cache-compare chain in pixel setup.
	// Both addresses retain the descriptor ABI's signed 16.16 and 32-bit wrap.
	wire [31:0] axis_source_addr0 = source_row_addr +
		{{14{u_current[31]}}, u_current[31:16], 2'b00};
	wire [31:0] axis_source_addr1 = source_row_addr +
		{{14{u_next[31]}}, u_next[31:16], 2'b00};
	wire axis_pair_geometry_eligible = !affine_mode && blit_x + 1'b1 < blit_width &&
		($signed(dst_x) + $signed({1'b0, blit_x}) + 1) < FB_WIDTH;
	wire axis_pair_eligible = axis_pair_geometry_eligible &&
		(solid_mode || axis_source_addr0[31:7] == axis_source_addr1[31:7]);
	// Fix the signed sum's width before unsigned clipping subtraction: otherwise
	// a negative16-bit x can zero-extend when the surrounding17-bit expression
	// changes its signedness, turning a two-sided clipped row into an overrun.
	wire signed [16:0] axis_destination_x = $signed(dst_x) + $signed({1'b0, blit_x});
	wire [16:0] axis_visible_right = 17'd320 - axis_destination_x;
	wire [16:0] axis_remaining_width = {1'b0, blit_width} - {1'b0, blit_x};
	wire [16:0] axis_stream_width = axis_remaining_width < axis_visible_right ?
		axis_remaining_width : axis_visible_right;
	// Tested after destination clipping. Positive unit U preserves fractional
	// nearest-neighbour starts, but a signed16.16 wrap is not contiguous DDR.
	wire axis_stream_eligible = axis_stream_setup == 2'd1 &&
		axis_stream_width_stage >= 17'd32 && axis_stream_width_stage <= 17'd320 &&
		axis_source_addr0[1:0] == 0 && !u_current[31] &&
		{1'b0, u_current[31:16]} + axis_stream_width_stage <= 17'd32768;
	wire [31:0] water_pipeline_tint = (axis_stream_mode || axis_gather_mode) ? tint_color :
		{water_tint_alpha, 24'hffffff};
	wire water_pipeline_floor = (axis_stream_mode || axis_gather_mode) && floor_tint_mode;
	wire gather_pair = gather_remaining > 9'd1 &&
		source_byte_addr[31:7] == source_byte_addr_1[31:7];
	wire [16:0] gather_pair_destination1 = gather_pair_destination + 17'd1;
	wire [8:0] water_source_x0 = {water_source_word, 1'b0};
	wire [8:0] water_source_x1 = {water_source_word, 1'b0} + 1'b1;
	wire [16:0] water_source_end = water_row_source_x + water_row_width;
	wire [7:0] water_source_words = axis_stream_mode ?
		((water_source_end + 17'd1) >> 1) : 8'd160;
	wire [7:0] water_words_remaining = water_source_words - water_source_word;
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
	wire [9:0] surface_load_pixel = {surface_word_index, 1'b0};
	wire surface_load_valid0 = !(surface_word_index == 0 && surface_first_lane);
	wire surface_load_valid1 = surface_load_pixel + 10'd1 <
		{1'b0, surface_width[8:0]} + {9'b0, surface_first_lane};
	wire [16:0] surface_load_linear = surface_row_linear +
		{7'b0, surface_load_pixel} - {16'b0, surface_first_lane};
	wire [8:0] surface_issue_word = surface_word_index + {1'b0, framebuffer_reads_issued};
	wire [9:0] surface_issue_pixel = {surface_issue_word, 1'b0};
	wire [16:0] surface_issue_linear = surface_row_linear +
		{7'b0, surface_issue_pixel} - {16'b0, surface_first_lane};
	wire surface_issue_valid0 = !(surface_issue_word == 0 && surface_first_lane);
	wire surface_issue_valid1 = surface_issue_pixel + 10'd1 <
		{1'b0, surface_width[8:0]} + {9'b0, surface_first_lane};
	wire [8:0] surface_words_remaining = surface_row_words - surface_word_index;
	wire [7:0] surface_burst_words = surface_words_remaining > 9'd64 ?
		8'd64 : surface_words_remaining[7:0];
	wire [31:0] generic_source_address = source_row_addr + {14'b0, generic_texture_x, 2'b0};
	// Keep the original unsigned16 truncation of signed 16.16 UV coordinates.
	// The generic path instead clamps its signed 32.32 coordinate first.
	wire [15:0] source_row_index_now = state == ST_GENERIC ?
		generic_clamp_texel(generic_uv[1], generic_texture_height) :
		(state == ST_BLIT_PIXEL ? v_current[31:16] : v_start[31:16]);
	wire [31:0] source_row_address_next = src_base + source_row_product_low +
		{source_row_product_high, 16'd0};

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
		reg [7:0] high_byte, low_byte;
		reg [1:0] bump;
		begin
			// Exact low byte of (value + 1 + (value >> 8)) >> 8.
			// Fold the correction into a byte comparison instead of two
			// wide carry chains. Keep bit 16, including overflow cases.
			high_byte = value[15:8];
			low_byte = value[7:0];
			bump = {1'b0, value[16]} + {1'b0, (low_byte >= ~high_byte)};
			div255_floor = high_byte + {6'b0, bump};
		end
	endfunction

	function automatic [7:0] tint_mul255_product;
		input [15:0] product;
		input floor_mode;
		begin
			tint_mul255_product = floor_mode ? div255_floor({1'b0, product}) :
				round_mul255_product(product);
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
				2'd3: native_buffer_address = NATIVE_BUF3;
				default: native_buffer_address = NATIVE_BUF0;
			endcase
		end
	endfunction

	// Keep the current completed buffer and the buffer being scanned immutable.
	// Four presentation buffers: the latest published frame, the frame the
	// native reader has latched, and up to two frames the HDMI framebuffer
	// reader may hold can all differ, and the next job still needs one more
	// only while the HDMI reader changes frames. ST_PRESENT_WAIT picks the
	// first free buffer, starting after the one just published.
	reg [3:0] present_busy;
	always @(*) begin
		present_busy = hdmi_protect;
		if (scan_buffer_valid) present_busy[scan_buffer] = 1'b1;
		if (native_frame != 0) present_busy[native_buffer] = 1'b1;
	end
	wire [1:0] present_try0 = present_buffer;
	wire [1:0] present_try1 = present_buffer + 2'd1;
	wire [1:0] present_try2 = present_buffer + 2'd2;
	wire [1:0] present_try3 = present_buffer + 2'd3;
	wire present_free = ~&present_busy;
	wire [1:0] present_choice =
		!present_busy[present_try0] ? present_try0 :
		!present_busy[present_try1] ? present_try1 :
		!present_busy[present_try2] ? present_try2 : present_try3;

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

	// GameMaker 1.x bm_subtract uses ZERO / INVSRCCOLOR blend factors,
	// not reverse subtraction. Alpha does not weight the RGB channels.
	// Apply the same inverse-source factor independently to all RGBA lanes.
	function automatic [31:0] subtract_blend;
		input [31:0] src, dst;
		reg [7:0] ir, ig, ib, ia;
		begin
			ir = 8'd255 - src[7:0];
			ig = 8'd255 - src[15:8];
			ib = 8'd255 - src[23:16];
			ia = 8'd255 - src[31:24];
			subtract_blend[7:0] = div255_floor(dst[7:0] * ir);
			subtract_blend[15:8] = div255_floor(dst[15:8] * ig);
			subtract_blend[23:16] = div255_floor(dst[23:16] * ib);
			subtract_blend[31:24] = div255_floor(dst[31:24] * ia);
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

	function automatic [32:0] generic_clamp_color;
		input signed [63:0] value;
		begin
			if (value[63]) generic_clamp_color = 33'd0;
			else if (|value[62:32]) generic_clamp_color = 33'h1_0000_0000;
			else generic_clamp_color = {1'b0, value[31:0]};
		end
	endfunction

	function automatic [15:0] generic_clamp_texel;
		input signed [63:0] value;
		input [15:0] extent;
		begin
			if (value[63]) generic_clamp_texel = 0;
			else if (value[63:32] >= {16'd0, extent}) generic_clamp_texel = extent - 1'b1;
			else generic_clamp_texel = value[47:32];
		end
	endfunction

	function automatic [7:0] generic_factor;
		input [7:0] factor;
		input [31:0] source_rgba, destination_rgba;
		input integer channel;
		reg [7:0] s, d;
		begin
			s = source_rgba[channel*8 +: 8];
			d = destination_rgba[channel*8 +: 8];
			case (factor)
				8'd1: generic_factor = 0;
				8'd2: generic_factor = 255;
				8'd3: generic_factor = s;
				8'd4: generic_factor = 255 - s;
				8'd5: generic_factor = source_rgba[31:24];
				8'd6: generic_factor = 255 - source_rgba[31:24];
				8'd7: generic_factor = destination_rgba[31:24];
				8'd8: generic_factor = 255 - destination_rgba[31:24];
				8'd9: generic_factor = d;
				8'd10: generic_factor = 255 - d;
				8'd11: generic_factor = channel == 3 ? 8'd255 :
					(source_rgba[31:24] < 8'd255 - destination_rgba[31:24] ?
					 source_rgba[31:24] : 8'd255 - destination_rgba[31:24]);
				default: generic_factor = 0;
			endcase
		end
	endfunction

	function automatic [7:0] generic_named_factor;
		input [7:0] mode;
		input source_side, alpha_channel;
		begin
			case (mode)
				8'd0: generic_named_factor = source_side ? (alpha_channel ? 8'd2 : 8'd5) : 8'd6;
				8'd1: generic_named_factor = source_side ? (alpha_channel ? 8'd2 : 8'd5) : 8'd2;
				8'd3: generic_named_factor = source_side ? 8'd1 : 8'd4;
				8'd5: generic_named_factor = source_side ? 8'd5 : 8'd2;
				default: generic_named_factor = 8'd2;
			endcase
		end
	endfunction

	function automatic [7:0] generic_blend_channel;
		input [7:0] source_byte, destination_byte, mode;
		input [15:0] source_product, destination_product;
		input enabled, alpha_channel;
		reg [16:0] sum;
		reg [7:0] scaled_source;
		begin
			sum = {1'b0, source_product} + {1'b0, destination_product};
			scaled_source = div255_floor({1'b0, source_product});
			if (!enabled) generic_blend_channel = source_byte;
			else case (mode)
				8'd2: generic_blend_channel = source_byte > destination_byte ? source_byte : destination_byte;
				8'd4: generic_blend_channel = source_byte < destination_byte ? source_byte : destination_byte;
				8'd5: generic_blend_channel = alpha_channel ? source_byte :
					(scaled_source > destination_byte ? scaled_source - destination_byte : 8'd0);
				default: generic_blend_channel = sum >= 17'd65025 ? 8'd255 : div255_floor(sum);
			endcase
		end
	endfunction

	// A small skid FIFO decouples synchronous M10K reads from Avalon write
	// back-pressure. This keeps burst data stable during stalls while still
	// sustaining one framebuffer pair per accepted DDR clock.
	wire [63:0] framebuffer_fifo_output =
		framebuffer_fifo[framebuffer_fifo_read_ptr];
	assign ddram_din = state == ST_SURFACE_STORE_STREAM ? framebuffer_fifo_output :
		state == ST_FRAMEBUFFER_WRITE_STREAM ?
		(framebuffer_write_present ?
			{rgba_to_xrgb(framebuffer_fifo_output[63:32]),
			 rgba_to_xrgb(framebuffer_fifo_output[31:0])} :
			 framebuffer_fifo_output) : ddram_din_control;
	assign ddram_be = state == ST_SURFACE_STORE_STREAM ?
		surface_fifo_be[framebuffer_fifo_read_ptr] : ddram_be_control;

	function automatic [31:0] blended_pixel_products;
		input [31:0] src, dst;
		input [47:0] source_products;
		input [63:0] destination_products;
		input add_mode;
		begin
			// Inverse-source modulation has its own registered pipeline below.
			if (src[31:24] == 0) blended_pixel_products = dst;
			else if (add_mode) begin
				blended_pixel_products[7:0] = saturating_add(dst[7:0], div255_floor({1'b0, source_products[15:0]}));
				blended_pixel_products[15:8] = saturating_add(dst[15:8], div255_floor({1'b0, source_products[31:16]}));
				blended_pixel_products[23:16] = saturating_add(dst[23:16], div255_floor({1'b0, source_products[47:32]}));
				blended_pixel_products[31:24] = saturating_add(dst[31:24], src[31:24]);
			end else if (src[31:24] == 8'hff) blended_pixel_products = src;
			else begin
				blended_pixel_products[7:0] = div255_floor({1'b0, source_products[15:0]} + {1'b0, destination_products[15:0]});
				blended_pixel_products[15:8] = div255_floor({1'b0, source_products[31:16]} + {1'b0, destination_products[31:16]});
				blended_pixel_products[23:16] = div255_floor({1'b0, source_products[47:32]} + {1'b0, destination_products[47:32]});
				blended_pixel_products[31:24] = src[31:24] + div255_floor({1'b0, destination_products[63:48]});
			end
		end
	endfunction

	wire [31:0] blend_commit0 = subtract_mode ? blended_pixel_result0 :
		blended_pixel_products(tinted_pixel, blend_destination0, blend_source_product0, blend_destination_product0, additive_mode);
	wire [31:0] blend_commit1 = subtract_mode ? blended_pixel_result1 :
		blended_pixel_products(tinted_pixel_1, blend_destination1, blend_source_product1, blend_destination_product1, additive_mode);

	// Keep the wide M10K bank-output mux out of the normal/add blend DSP path.
	// The extra operand register costs one clock per blended scalar/pair only;
	// opaque/transparent direct writes keep their existing fast paths.
	task automatic stage_blend;
		begin
			blend_destination0 <= destination_odd ? fb_odd_q : fb_even_q;
			blend_destination1 <= destination_odd ? fb_even_q : fb_odd_q;
			state <= ST_BLEND_CALCULATE;
		end
	endtask

	// The M10K output selection is a wide mux. Capture it before inverse-
	// source products, then register products before /255 so RAM mux, DSP and
	// division never share one critical path. Metadata stays stable until the
	// existing single/pair commit advances the blit.
	task automatic stage_subtract;
		begin
			subtract_destination0 <= destination_odd ? fb_odd_q : fb_even_q;
			subtract_destination1 <= destination_odd ? fb_even_q : fb_odd_q;
			subtract_factor0 <= ~tinted_pixel;
			subtract_factor1 <= ~tinted_pixel_1;
			state <= ST_SUBTRACT_PRODUCT;
		end
	endtask

	task automatic stage_source_row;
		begin
			source_row_product_low <= source_row_index_now * src_stride[15:0];
			// Only these low16 bits contribute to the final modulo-32 address.
			source_row_product_high <= source_row_index_now * src_stride[31:16];
		end
	endtask

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

	task automatic next_command;
		begin
			command_index <= command_index + 1'b1;
			command_word <= 0;
			start_read(command_addr + ((command_index + 1'b1) << 6), 8);
			state <= ST_CMD_ACCEPT;
		end
	endtask

	// Start one synchronous BRAM read pair. Invalid edge lanes use address0;
	// their byte enables are zero, so no out-of-range RAM access is necessary.
	task automatic issue_surface_pair;
		begin
			if (surface_pixel_odd) begin
				fb_odd_read_address <= surface_issue_valid0 ? surface_issue_linear[16:1] : 16'd0;
				fb_even_read_address <= surface_issue_valid1 ?
					(surface_issue_linear[16:1] + 16'd1) : 16'd0;
			end else begin
				fb_even_read_address <= surface_issue_valid0 ? surface_issue_linear[16:1] : 16'd0;
				fb_odd_read_address <= surface_issue_valid1 ? surface_issue_linear[16:1] : 16'd0;
			end
			surface_be_pipe0 <= {{4{surface_issue_valid1}}, {4{surface_issue_valid0}}};
			framebuffer_reads_issued <= framebuffer_reads_issued + 1'b1;
			framebuffer_read_valid[0] <= 1;
		end
	endtask

	task automatic advance_generic_pixel;
		integer index;
		begin
			index = 0;
			if (generic_x + 1'b1 >= generic_width) begin
				if (generic_y + 1'b1 >= generic_height) next_command();
				else begin
					generic_x <= 0;
					generic_y <= generic_y + 1'b1;
					generic_row_linear <= generic_row_linear + FB_WIDTH;
					generic_linear <= generic_row_linear + FB_WIDTH;
					for (index = 0; index < 3; index = index + 1) begin
						generic_edge_row[index] <= generic_edge_row[index] + $signed(generic_packet[10+index*3]);
						generic_edge[index] <= generic_edge_row[index] + $signed(generic_packet[10+index*3]);
					end
					for (index = 0; index < 2; index = index + 1) begin
						generic_uv_row[index] <= generic_uv_row[index] + $signed(generic_packet[19+index*3]);
						generic_uv[index] <= generic_uv_row[index] + $signed(generic_packet[19+index*3]);
					end
					for (index = 0; index < 4; index = index + 1) begin
						generic_color_row[index] <= generic_color_row[index] + $signed(generic_packet[25+index*4]);
						generic_color[index] <= generic_color_row[index] + $signed(generic_packet[25+index*4]);
						generic_color_dx[index] <= generic_color_dx[index] + $signed(generic_packet[26+index*4]);
					end
					generic_state <= GP_PIXEL;
				end
			end else begin
				generic_x <= generic_x + 1'b1;
				generic_linear <= generic_linear + 1'b1;
				for (index = 0; index < 3; index = index + 1)
					generic_edge[index] <= generic_edge[index] + $signed(generic_packet[9+index*3]);
				for (index = 0; index < 2; index = index + 1)
					generic_uv[index] <= generic_uv[index] + $signed(generic_packet[18+index*3]);
				for (index = 0; index < 4; index = index + 1)
					generic_color[index] <= generic_color[index] + generic_color_dx[index];
				generic_state <= GP_PIXEL;
			end
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
				u_next <= u_next + u_step;
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
				u_next <= u_next + (u_step <<< 1);
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
			ddram_be_control <= 0;
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
			axis_stream_mode <= 0;
			axis_gather_mode <= 0;
			gather_pending <= 0;
			gather_refilling <= 0;
			gather_lookup <= 0;
			axis_stream_setup <= 0;
			water_subtract_mode <= 0;
			water_tint_alpha <= 8'hff;
			fb_even_we <= 0;
			fb_odd_we <= 0;
			present_buffer <= 0;
			scan_underflow_seen <= 0;
			completion_underflow_snapshot <= 0;
			affine_tint_config <= 32'hffff_ffff;
			affine_additive_config <= 0;
			affine_subtract_config <= 0;
			additive_mode <= 0;
			subtract_mode <= 0;
			floor_tint_mode <= 0;
			contiguous_pair_mode <= 0;
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
					affine_tint_config <= 32'hffff_ffff;
					affine_additive_config <= 0;
					affine_subtract_config <= 0;
					texture_cache_valid <= 0;
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
					// This is per-descriptor state, never inherited by fill, affine,
					// water, or a subsequent ordinary axis blit.
					floor_tint_mode <= descriptor[0][7:0] == 8'd2 && descriptor[0][10];
					axis_stream_mode <= 0;
					water_subtract_mode <= 0;
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
							subtract_mode <= descriptor[0][9];
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
							if (descriptor[0][8] && !descriptor[0][9] &&
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
								subtract_mode <= descriptor[0][9];
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
							subtract_mode <= affine_subtract_config;
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
							affine_subtract_config <= 0;
							state <= ST_BLIT_ROW;
						end
						8'd5: begin
							// One-shot state for the following affine command. Keeping it
							// separate leaves all 256 coordinate bits available.
							affine_tint_config <= descriptor[1][31:0];
							affine_additive_config <= descriptor[0][8];
							affine_subtract_config <= descriptor[0][9];
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
						8'd10, 8'd11: begin
							// D0: width[31:16],height[47:32]. D1: DDR first-pixel
							// address[31:0], stride[63:32]. D2: local x/y[31:0].
							// The HPS validates allocation bounds and address overflow;
							// the RTL independently rejects unsafe tile geometry.
							if (descriptor[0][31:16] == 0 || descriptor[0][47:32] == 0 ||
							    descriptor[0][31:16] > FB_WIDTH || descriptor[0][47:32] > FB_HEIGHT ||
							    {1'b0, descriptor[2][15:0]} + {1'b0, descriptor[0][31:16]} > FB_WIDTH ||
							    {1'b0, descriptor[2][31:16]} + {1'b0, descriptor[0][47:32]} > FB_HEIGHT ||
							    descriptor[1][1:0] != 0 || descriptor[1][34:32] != 0 ||
							    descriptor[1][63:32] < {14'b0, descriptor[0][31:16], 2'b0}) begin
								next_command();
							end else begin
								surface_store <= descriptor[0][7:0] == 8'd11;
								surface_row_addr <= descriptor[1][31:0];
								surface_stride <= descriptor[1][63:32];
								surface_width <= descriptor[0][31:16];
								surface_height <= descriptor[0][47:32];
								surface_row <= 0;
								surface_row_linear <= descriptor[2][31:16] * FB_WIDTH + descriptor[2][15:0];
								surface_row_words <= ({1'b0, descriptor[0][24:16]} +
									{9'b0, descriptor[1][2]} + 10'd1) >> 1;
								surface_word_index <= 0;
								surface_first_lane <= descriptor[1][2];
								surface_pixel_odd <= descriptor[2][0] ^ descriptor[1][2];
								texture_cache_valid <= 0;
								state <= ST_SURFACE_BURST;
							end
						end
						8'd12: state <= ST_CAPABILITY_WRITE;
						8'd13: begin
							if (descriptor[0][31:16] == 0 || descriptor[0][47:32] == 0 ||
							    descriptor[0][31:16] > FB_WIDTH || descriptor[0][47:32] > FB_HEIGHT ||
							    {1'b0, descriptor[2][15:0]} + {1'b0, descriptor[0][31:16]} > FB_WIDTH ||
							    {1'b0, descriptor[2][31:16]} + {1'b0, descriptor[0][47:32]} > FB_HEIGHT ||
							    descriptor[1][2:0] != 0) next_command();
							else begin
								generic_width <= descriptor[0][31:16];
								generic_height <= descriptor[0][47:32];
								generic_row_linear <= descriptor[2][31:16] * FB_WIDTH + descriptor[2][15:0];
								generic_linear <= descriptor[2][31:16] * FB_WIDTH + descriptor[2][15:0];
								generic_packet_word <= 0;
								generic_packet_invalid <= 0;
								start_read(descriptor[1][31:0], 8'd64);
								generic_state <= GP_PACKET_ACCEPT;
								state <= ST_GENERIC;
							end
						end
						8'd14: begin
							// Raw RGBA replacement, including alpha zero. Same bounded
							// geometry as DMA: D0 width/height, D1 low32 colour, D2 x/y.
							// No clipping or blend state; every other descriptor bit is
							// reserved. Invalid commands leave BRAM and DDR untouched.
							if (descriptor[0][31:16] == 0 || descriptor[0][47:32] == 0 ||
							    descriptor[0][31:16] > FB_WIDTH || descriptor[0][47:32] > FB_HEIGHT ||
							    {1'b0, descriptor[2][15:0]} + {1'b0, descriptor[0][31:16]} > FB_WIDTH ||
							    {1'b0, descriptor[2][31:16]} + {1'b0, descriptor[0][47:32]} > FB_HEIGHT ||
							    descriptor[0][15:8] != 0 || descriptor[0][63:48] != 0 ||
							    descriptor[1][63:32] != 0 || descriptor[2][63:32] != 0 ||
							    descriptor[3] != 0 || descriptor[4] != 0 || descriptor[5] != 0 ||
							    descriptor[6] != 0 || descriptor[7] != 0) next_command();
							else begin
								clear_color <= descriptor[1][31:0];
								surface_width <= descriptor[0][31:16];
								surface_height <= descriptor[0][47:32];
								surface_row <= 0;
								surface_row_linear <= descriptor[2][31:16] * FB_WIDTH + descriptor[2][15:0];
								surface_row_words <= ({1'b0, descriptor[0][24:16]} +
									{9'b0, descriptor[2][0]} + 10'd1) >> 1;
								surface_word_index <= 0;
								surface_first_lane <= descriptor[2][0];
								state <= ST_RECT_CLEAR;
							end
						end
						default: begin
							// An unknown opcode must not expose a partially rendered
							// tile as a native frame. End this job without presenting.
							state <= ST_CAPABILITY_WRITE;
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
				ST_RECT_CLEAR: begin
					// Align the pair to the two BRAM banks. Partial head/tail
					// lanes are disabled, so odd x/width never alter a neighbour.
					fb_even_write_address <= surface_load_linear[16:1];
					fb_odd_write_address <= surface_load_linear[16:1];
					fb_even_write_data <= clear_color;
					fb_odd_write_data <= clear_color;
					fb_even_we <= surface_load_valid0;
					fb_odd_we <= surface_load_valid1;
					if (surface_word_index + 9'd1 >= surface_row_words) begin
						if (surface_row + 16'd1 >= surface_height) next_command();
						else begin
							surface_row <= surface_row + 16'd1;
							surface_row_linear <= surface_row_linear + 17'd320;
							surface_word_index <= 0;
						end
					end else surface_word_index <= surface_word_index + 9'd1;
				end
				ST_BLIT_ROW: begin
					axis_stream_setup <= 0;
					stage_source_row();
					if (blit_y >= blit_height) begin
						command_index <= command_index + 1'b1;
						command_word <= 0;
						start_read(command_addr + ((command_index + 1'b1) << 6), 8);
						state <= ST_CMD_ACCEPT;
					end else begin
						u_current <= u_start;
						u_next <= u_start + u_step;
						v_current <= v_start;
						tint_color <= {clamp_tint(tint_a_current), clamp_tint(tint_b_current),
						               clamp_tint(tint_g_current), clamp_tint(tint_r_current)};
						state <= ST_ROW_SOURCE_ADDRESS;
					end
				end
				ST_ROW_SOURCE_ADDRESS: begin
					source_row_addr <= source_row_address_next;
					state <= ST_BLIT_PIXEL;
				end
				ST_BLIT_PIXEL: begin
					destination_linear <= destination_linear_calc;
					// Speculate outside clipping/mode guards so their predicates do
					// not enter a multiplier enable. Only affine setup consumes it.
					stage_source_row();
					// Speculatively stage the solid color before clipping/pair tests.
					// Only the solid-pair shortcut consumes it directly; texture and
					// affine paths replace it before blending. Keep coordinate logic
					// out of the tint register/DSP enable without adding a clock.
					tinted_pixel <= tint_color;
					tinted_pixel_1 <= tint_color;
					if (!affine_mode) begin
						source_byte_addr <= axis_source_addr0;
						source_byte_addr_1 <= axis_source_addr1;
					end
					if (($signed(dst_x) + $signed({1'b0, blit_x})) < 0 ||
					    ($signed(dst_x) + $signed({1'b0, blit_x})) >= FB_WIDTH ||
					    ($signed(dst_y) + $signed({1'b0, blit_y})) < 0 ||
					    ($signed(dst_y) + $signed({1'b0, blit_y})) >= FB_HEIGHT ||
					    (affine_mode && (u_current < u_min || u_current >= u_max ||
					                     v_current < v_min || v_current >= v_max))) begin
						advance_blit_pixel();
					end else if (!solid_mode && !affine_mode && u_step != 32'sh0001_0000) begin
						// Cache-gather setup is separate from both texture selection and
						// BRAM enables. Hold each pair's addresses for a registered hit/
						// select stage; no source arithmetic feeds a destination port.
						axis_gather_mode <= 1;
						axis_stream_mode <= 0;
						water_subtract_mode <= subtract_mode;
						water_additive_mode <= additive_mode;
						water_native_source <= 0;
						tiled_mode <= 0;
						gather_remaining <= axis_stream_width[8:0];
						gather_destination <= destination_linear_calc;
						gather_pending <= 0;
						gather_refilling <= 0;
						gather_lookup <= 0;
						water_source_done <= 0;
						water_pipe0_valid <= 0;
						water_pipe1_valid <= 0;
						water_pipe2_valid <= 0;
						water_pipe3_valid <= 0;
						water_pipe4_valid <= 0;
						state <= ST_WATER_SOURCE_DATA;
					end else if (axis_stream_setup == 0 && !solid_mode && !affine_mode &&
					             u_step == 32'sh0001_0000) begin
						// Register clipped width before the no-wrap/eligibility test.
						// The former combined add/min/add/compare path fed the legacy
						// BRAM read enable and missed88MHz timing. Hold this sample
						// for one clock, once per visible unit-U row, even if x<0 was
						// clipped by earlier advance_blit_pixel calls.
						axis_stream_width_stage <= axis_stream_width;
						axis_stream_setup <= 1;
					end else if (axis_stream_eligible) begin
						// The host descriptor remains unchanged: stream this clipped
						// row through the common tint/blend pipeline. Lane masks are
						// independent of destination parity and preserve neighbours.
						axis_stream_mode <= 1;
						water_subtract_mode <= subtract_mode;
						water_additive_mode <= additive_mode;
						water_native_source <= 0;
						tiled_mode <= 0;
						axis_stream_row_address <= axis_source_addr0 & 32'hffff_fff8;
						water_row_source_x <= {15'b0, axis_source_addr0[2]};
						water_row_width <= axis_stream_width_stage[15:0];
						water_destination_base <= destination_linear_calc;
						water_source_word <= 0;
						water_source_done <= 0;
						water_pipe0_valid <= 0;
						water_pipe1_valid <= 0;
						water_pipe2_valid <= 0;
						water_pipe3_valid <= 0;
						water_pipe4_valid <= 0;
						state <= ST_WATER_SOURCE_ADDRESS;
					end else begin
						// Do not retry eligibility after advancing UV or x: the staged
						// width belongs to the first visible sample of this row only.
						axis_stream_setup <= 2;
						// Any two axis samples may share the loaded 128-byte line:
						// mirrored, repeated, fractional, and skipped texels each retain
						// their own address and lane. A line crossing stays scalar.
						pair_mode <= axis_pair_eligible;
						contiguous_pair_mode <= u_step == 32'sh0001_0000 && !axis_source_addr0[2];
						// Pre-read both legal destination pixels independently of the
						// texture cache-line decision. A source-line crossing consumes
						// only one pixel and harmlessly discards this extra BRAM read.
						// Keeping source-address add/compare out of the address-enable
						// path repairs the measured88MHz critical path without another
						// pipeline clock or changing which texels are actually fetched.
						if (axis_pair_geometry_eligible) begin
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
						             (additive_mode || subtract_mode ||
						              (tint_color[31:24] != 0 && tint_color[31:24] != 8'hff))) begin
							// The read above supplies ST_PAIR_BLEND_WRITE after the
							// existing one-cycle M10K latency.
							state <= ST_PAIR_BLEND_READ;
						end else if (solid_mode) begin
							state <= ST_SOLID_READY;
						end else if (texture_cache_valid &&
						    texture_cache_addr == (axis_source_addr0[31:3] & 29'h1fff_fff0)) begin
							state <= ST_TEX_READY;
						end else begin
							texture_cache_fill <= 0;
							start_read(axis_source_addr0 & 32'hffff_ff80, 16);
							state <= ST_TEX_ACCEPT;
						end
					end
				end
				ST_AFFINE_ROW_ADDRESS: begin
					source_row_addr <= source_row_address_next;
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
						if (pair_mode && !additive_mode && !subtract_mode &&
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
						if (contiguous_pair_mode && tint_color == 32'hffff_ffff && !additive_mode && !subtract_mode &&
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
							// Keep unit-step pairs inside the current 128-byte texture
							// cache line streaming through this state. The normal path
							// spent an address/setup clock between every pair even though
							// both the next source address and framebuffer word are known.
							// Re-enter address setup at cache-line and scanline boundaries.
							// A positive signed-U wrap jumps backwards by 262144 bytes;
							// the next physical pair is not necessarily address + 8 even
							// when that value remains inside this cached line.
							if (blit_x + 3 < blit_width &&
							    u_current[31:16] != 16'h7ffe &&
							    ($signed(dst_x) + $signed({1'b0, blit_x}) + 3) < FB_WIDTH &&
							    texture_cache_addr ==
							        ((((source_byte_addr + 32'd8) >> 3)) & 29'h1fff_fff0)) begin
								blit_x <= blit_x + 2;
								u_current <= u_current + (u_step <<< 1);
								u_next <= u_next + (u_step <<< 1);
								v_current <= v_current + (v_x_step <<< 1);
								destination_linear <= destination_linear + 2;
								source_byte_addr <= source_byte_addr + 8;
								source_byte_addr_1 <= source_byte_addr_1 + 8;
								state <= ST_TEX_READY;
							end else begin
								advance_blit_pair();
							end
						end else if (contiguous_pair_mode && !additive_mode && !subtract_mode && tint_color[23:0] == 24'hffffff &&
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
							source_pixel <= source_byte_addr[2] ? texture_cache_data[source_byte_addr[6:3]][63:32] :
								texture_cache_data[source_byte_addr[6:3]][31:0];
							source_pixel_1 <= source_byte_addr_1[2] ? texture_cache_data[source_byte_addr_1[6:3]][63:32] :
								texture_cache_data[source_byte_addr_1[6:3]][31:0];
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
						tint_mul255_product(tint_product[0], floor_tint_mode),
						tint_mul255_product(tint_product[1], floor_tint_mode),
						tint_mul255_product(tint_product[2], floor_tint_mode),
						tint_mul255_product(tint_product[3], floor_tint_mode)};
					if (pair_mode) begin
						tinted_pixel_1 <= {
							tint_mul255_product(tint_product[4], floor_tint_mode),
							tint_mul255_product(tint_product[5], floor_tint_mode),
							tint_mul255_product(tint_product[6], floor_tint_mode),
							tint_mul255_product(tint_product[7], floor_tint_mode)};
						state <= ST_PAIR_BLEND_READ;
					end else state <= ST_BLEND_READ;
				end
				ST_BLEND_READ: begin
					if (!subtract_mode && tinted_pixel[31:24] == 0) advance_blit_pixel();
					else if (!additive_mode && !subtract_mode && tinted_pixel[31:24] == 8'hff) begin
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
					if (subtract_mode) stage_subtract();
					else stage_blend();
				end
				ST_BLEND_CALCULATE: begin
					blend_source_product0[15:0] <= tinted_pixel[7:0] * tinted_pixel[31:24];
					blend_source_product0[31:16] <= tinted_pixel[15:8] * tinted_pixel[31:24];
					blend_source_product0[47:32] <= tinted_pixel[23:16] * tinted_pixel[31:24];
					blend_destination_product0[15:0] <= blend_destination0[7:0] * (8'd255 - tinted_pixel[31:24]);
					blend_destination_product0[31:16] <= blend_destination0[15:8] * (8'd255 - tinted_pixel[31:24]);
					blend_destination_product0[47:32] <= blend_destination0[23:16] * (8'd255 - tinted_pixel[31:24]);
					blend_destination_product0[63:48] <= blend_destination0[31:24] * (8'd255 - tinted_pixel[31:24]);
					blend_source_product1[15:0] <= tinted_pixel_1[7:0] * tinted_pixel_1[31:24];
					blend_source_product1[31:16] <= tinted_pixel_1[15:8] * tinted_pixel_1[31:24];
					blend_source_product1[47:32] <= tinted_pixel_1[23:16] * tinted_pixel_1[31:24];
					blend_destination_product1[15:0] <= blend_destination1[7:0] * (8'd255 - tinted_pixel_1[31:24]);
					blend_destination_product1[31:16] <= blend_destination1[15:8] * (8'd255 - tinted_pixel_1[31:24]);
					blend_destination_product1[47:32] <= blend_destination1[23:16] * (8'd255 - tinted_pixel_1[31:24]);
					blend_destination_product1[63:48] <= blend_destination1[31:24] * (8'd255 - tinted_pixel_1[31:24]);
					state <= pair_mode ? ST_PAIR_BLEND_COMMIT : ST_BLEND_COMMIT;
				end
				ST_BLEND_COMMIT: begin
					fb_even_write_address <= destination_word;
					fb_odd_write_address <= destination_word;
					if (destination_odd) begin
						fb_odd_write_data <= blend_commit0;
						fb_odd_we <= 1;
					end else begin
						fb_even_write_data <= blend_commit0;
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
					if (!additive_mode && !subtract_mode &&
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
					end else if (subtract_mode) begin
						stage_subtract();
					end else begin
						stage_blend();
					end
				end
				ST_PAIR_BLEND_WRITE: begin
					if (subtract_mode) stage_subtract();
					else stage_blend();
				end
				ST_SUBTRACT_PRODUCT: begin
					subtract_product[0] <= subtract_destination0[7:0] * subtract_factor0[7:0];
					subtract_product[1] <= subtract_destination0[15:8] * subtract_factor0[15:8];
					subtract_product[2] <= subtract_destination0[23:16] * subtract_factor0[23:16];
					subtract_product[3] <= subtract_destination0[31:24] * subtract_factor0[31:24];
					subtract_product[4] <= subtract_destination1[7:0] * subtract_factor1[7:0];
					subtract_product[5] <= subtract_destination1[15:8] * subtract_factor1[15:8];
					subtract_product[6] <= subtract_destination1[23:16] * subtract_factor1[23:16];
					subtract_product[7] <= subtract_destination1[31:24] * subtract_factor1[31:24];
					state <= ST_SUBTRACT_RESULT;
				end
				ST_SUBTRACT_RESULT: begin
					blended_pixel_result0 <= {
						div255_floor({1'b0, subtract_product[3]}), div255_floor({1'b0, subtract_product[2]}),
						div255_floor({1'b0, subtract_product[1]}), div255_floor({1'b0, subtract_product[0]})};
					blended_pixel_result1 <= {
						div255_floor({1'b0, subtract_product[7]}), div255_floor({1'b0, subtract_product[6]}),
						div255_floor({1'b0, subtract_product[5]}), div255_floor({1'b0, subtract_product[4]})};
					state <= pair_mode ? ST_PAIR_BLEND_COMMIT : ST_BLEND_COMMIT;
				end
				ST_PAIR_BLEND_COMMIT: begin
					if (destination_odd) begin
						fb_odd_write_address <= destination_word;
						fb_odd_write_data <= blend_commit0;
						fb_even_write_address <= destination_word + 1'b1;
						fb_even_write_data <= blend_commit1;
					end else begin
						fb_even_write_address <= destination_word;
						fb_even_write_data <= blend_commit0;
						fb_odd_write_address <= destination_word;
						fb_odd_write_data <= blend_commit1;
					end
					fb_even_we <= 1;
					fb_odd_we <= 1;
					advance_blit_pair();
				end
				ST_GENERIC: begin
					case (generic_state)
						GP_PACKET_ACCEPT: if (!ddram_busy) begin
							ddram_rd <= 0;
							generic_state <= GP_PACKET_DATA;
						end
						GP_PACKET_DATA: if (ddram_dout_ready) begin
							if (generic_packet_word < 39) generic_packet[generic_packet_word] <= ddram_dout;
							if ((generic_packet_word >= 39 ||
							     (generic_packet_word >= 4 && generic_packet_word <= 7)) && ddram_dout != 0)
								generic_packet_invalid <= 1;
							if (generic_packet_word == 63) generic_state <= GP_INIT;
							else generic_packet_word <= generic_packet_word + 1'b1;
						end
						GP_INIT: begin
							if (generic_packet_invalid || generic_packet[0][31:0] != 32'h31504741 ||
							    generic_packet[0][63:34] != 0 || generic_packet[2][63:59] != 0 ||
							    generic_packet[2][47:44] != 0 || generic_packet[2][39:32] > 8'd6 ||
							    (generic_packet[0][32] && (generic_packet[2][15:0] == 0 ||
							     generic_packet[2][31:16] == 0 || generic_packet[1][1:0] != 0 ||
							     generic_packet[1][33:32] != 0 || generic_packet[1][63:32] <
							     {14'd0, generic_packet[2][15:0], 2'b0})) ||
							    (generic_packet[2][39:32] == 8'd6 &&
							     (generic_packet[3][7:0] < 1 || generic_packet[3][7:0] > 11 ||
							      generic_packet[3][15:8] < 1 || generic_packet[3][15:8] > 11 ||
							      generic_packet[3][23:16] < 1 || generic_packet[3][23:16] > 11 ||
							      generic_packet[3][31:24] < 1 || generic_packet[3][31:24] > 11))) next_command();
							else begin
								generic_textured <= generic_packet[0][32];
								generic_triangle <= generic_packet[0][33];
								src_base <= generic_packet[1][31:0];
								src_stride <= generic_packet[1][63:32];
								generic_texture_width <= generic_packet[2][15:0];
								generic_texture_height <= generic_packet[2][31:16];
								generic_blend_mode <= generic_packet[2][39:32];
								generic_write_mask <= generic_packet[2][43:40];
								generic_alpha_ref <= generic_packet[2][55:48];
								generic_alpha_test <= generic_packet[2][56];
								generic_fog <= generic_packet[2][57];
								generic_blend <= generic_packet[2][58];
								generic_factor_ids <= generic_packet[3][31:0];
								generic_fog_color <= generic_packet[3][63:32];
								generic_x <= 0;
								generic_y <= 0;
								for (generic_channel = 0; generic_channel < 3; generic_channel = generic_channel + 1) begin
									generic_edge_row[generic_channel] <= generic_packet[8+generic_channel*3];
									generic_edge[generic_channel] <= generic_packet[8+generic_channel*3];
								end
								for (generic_channel = 0; generic_channel < 2; generic_channel = generic_channel + 1) begin
									generic_uv_row[generic_channel] <= generic_packet[17+generic_channel*3];
									generic_uv[generic_channel] <= generic_packet[17+generic_channel*3];
								end
								for (generic_channel = 0; generic_channel < 4; generic_channel = generic_channel + 1) begin
									generic_color_row[generic_channel] <= generic_packet[23+generic_channel*4];
									generic_color[generic_channel] <= generic_packet[23+generic_channel*4];
									generic_color_dx[generic_channel] <= generic_packet[24+generic_channel*4];
								end
								texture_cache_valid <= 0;
								generic_state <= GP_PIXEL;
							end
						end
						GP_PIXEL: begin
							stage_source_row();
							if (generic_triangle && (generic_edge[0][63] || generic_edge[1][63] || generic_edge[2][63]))
								advance_generic_pixel();
							else begin
								fb_even_read_address <= generic_linear[16:1];
								fb_odd_read_address <= generic_linear[16:1];
								if (generic_textured) begin
									generic_texture_x <= generic_clamp_texel(generic_uv[0], generic_texture_width);
									generic_state <= GP_ADDRESS;
								end else begin
									source_pixel <= 32'hffffffff;
									generic_state <= GP_TINT;
								end
							end
						end
						GP_ADDRESS: begin
								source_row_addr <= source_row_address_next;
							generic_state <= GP_TEXTURE;
						end
						GP_TEXTURE: begin
							if (texture_cache_valid && texture_cache_addr == {generic_source_address[31:7], 4'b0}) begin
								source_pixel <= generic_source_address[2] ?
									texture_cache_data[generic_source_address[6:3]][63:32] :
									texture_cache_data[generic_source_address[6:3]][31:0];
								generic_state <= GP_TINT;
							end else begin
								texture_cache_valid <= 0;
								texture_cache_addr <= {generic_source_address[31:7], 4'b0};
								texture_cache_fill <= 0;
								start_read({generic_source_address[31:7], 7'b0}, 8'd16);
								generic_state <= GP_TEXTURE_ACCEPT;
							end
						end
						GP_TEXTURE_ACCEPT: if (!ddram_busy) begin
							ddram_rd <= 0;
							generic_state <= GP_TEXTURE_DATA;
						end
						GP_TEXTURE_DATA: if (ddram_dout_ready) begin
							texture_cache_data[texture_cache_fill] <= ddram_dout;
							if (texture_cache_fill == 15) begin
								texture_cache_valid <= 1;
								generic_state <= GP_TEXTURE;
							end else texture_cache_fill <= texture_cache_fill + 1'b1;
						end
						GP_TINT: begin
							for (generic_channel = 0; generic_channel < 4; generic_channel = generic_channel + 1)
								generic_tint_product[generic_channel] <= source_pixel[generic_channel*8 +: 8] *
									generic_clamp_color(generic_color[generic_channel]);
							generic_state <= GP_TINT_RESULT;
						end
						GP_TINT_RESULT: begin
							// Stage tint independently of alpha rejection. Rejected
							// pixels skip its consumers, so the comparison need not
							// be on the shared tint/DSP register-enable path.
							tinted_pixel <= {generic_tint_product[3][39:32],
								generic_fog ? generic_fog_color[23:0] :
								{generic_tint_product[2][39:32], generic_tint_product[1][39:32], generic_tint_product[0][39:32]}};
							if (generic_alpha_test && generic_tint_product[3][39:32] < generic_alpha_ref)
								advance_generic_pixel();
							else begin
								generic_state <= GP_FACTORS;
							end
						end
						GP_FACTORS: begin
							generic_destination <= generic_linear[0] ? fb_odd_q : fb_even_q;
							for (generic_channel = 0; generic_channel < 4; generic_channel = generic_channel + 1) begin
								generic_source_factor[generic_channel] <= generic_factor(
									generic_blend_mode == 6 ? (generic_channel == 3 ? generic_factor_ids[23:16] : generic_factor_ids[7:0]) :
									generic_named_factor(generic_blend_mode, 1'b1, generic_channel == 3),
									tinted_pixel, generic_linear[0] ? fb_odd_q : fb_even_q, generic_channel);
								generic_destination_factor[generic_channel] <= generic_factor(
									generic_blend_mode == 6 ? (generic_channel == 3 ? generic_factor_ids[31:24] : generic_factor_ids[15:8]) :
									generic_named_factor(generic_blend_mode, 1'b0, generic_channel == 3),
									tinted_pixel, generic_linear[0] ? fb_odd_q : fb_even_q, generic_channel);
							end
							generic_state <= GP_PRODUCTS;
						end
						GP_PRODUCTS: begin
							for (generic_channel = 0; generic_channel < 4; generic_channel = generic_channel + 1) begin
								generic_source_product[generic_channel] <= tinted_pixel[generic_channel*8 +: 8] * generic_source_factor[generic_channel];
								generic_destination_product[generic_channel] <= generic_destination[generic_channel*8 +: 8] * generic_destination_factor[generic_channel];
							end
							generic_state <= GP_RESULT;
						end
						GP_RESULT: begin
							for (generic_channel = 0; generic_channel < 4; generic_channel = generic_channel + 1)
								generic_result[generic_channel*8 +: 8] <= generic_write_mask[generic_channel] ?
									generic_blend_channel(tinted_pixel[generic_channel*8 +: 8], generic_destination[generic_channel*8 +: 8],
										generic_blend_mode, generic_source_product[generic_channel], generic_destination_product[generic_channel],
										generic_blend, generic_channel == 3) : generic_destination[generic_channel*8 +: 8];
							generic_state <= GP_COMMIT;
						end
						GP_COMMIT: begin
							if (generic_linear[0]) begin
								fb_odd_write_address <= generic_linear[16:1];
								fb_odd_write_data <= generic_result;
								fb_odd_we <= 1;
							end else begin
								fb_even_write_address <= generic_linear[16:1];
								fb_even_write_data <= generic_result;
								fb_even_we <= 1;
							end
							advance_generic_pixel();
						end
						default: next_command();
					endcase
				end
				ST_SURFACE_BURST: begin
					framebuffer_write_burst_length <= surface_burst_words;
					framebuffer_write_beats_left <= surface_burst_words;
					if (surface_store) begin
						ddram_addr <= surface_row_addr[31:3] + surface_word_index;
						ddram_burstcnt <= surface_burst_words;
						ddram_we <= 0;
						ddram_rd <= 0;
						framebuffer_reads_issued <= 0;
						framebuffer_read_valid <= 0;
						framebuffer_fifo_read_ptr <= 0;
						framebuffer_fifo_write_ptr <= 0;
						framebuffer_fifo_count <= 0;
						state <= ST_SURFACE_STORE_FILL;
					end else begin
						start_read({surface_row_addr[31:3], 3'b0} +
							{20'b0, surface_word_index, 3'b0}, surface_burst_words);
						state <= ST_SURFACE_LOAD_ACCEPT;
					end
				end
				ST_SURFACE_LOAD_ACCEPT: if (!ddram_busy) begin
					ddram_rd <= 0;
					state <= ST_SURFACE_LOAD_DATA;
				end
				ST_SURFACE_LOAD_DATA: if (ddram_dout_ready) begin
					if (surface_pixel_odd) begin
						fb_odd_write_address <= surface_load_linear[16:1];
						fb_odd_write_data <= ddram_dout[31:0];
						fb_odd_we <= surface_load_valid0;
						fb_even_write_address <= surface_load_linear[16:1] + 16'd1;
						fb_even_write_data <= ddram_dout[63:32];
						fb_even_we <= surface_load_valid1;
					end else begin
						fb_even_write_address <= surface_load_linear[16:1];
						fb_even_write_data <= ddram_dout[31:0];
						fb_even_we <= surface_load_valid0;
						fb_odd_write_address <= surface_load_linear[16:1];
						fb_odd_write_data <= ddram_dout[63:32];
						fb_odd_we <= surface_load_valid1;
					end
					surface_word_index <= surface_word_index + 1'b1;
					if (framebuffer_write_beats_left == 1) begin
						if (surface_word_index + 1'b1 >= surface_row_words)
							state <= ST_SURFACE_NEXT_ROW;
						else state <= ST_SURFACE_BURST;
					end else framebuffer_write_beats_left <= framebuffer_write_beats_left - 1'b1;
				end
				ST_SURFACE_STORE_FILL: begin
					framebuffer_read_valid[1] <= framebuffer_read_valid[0];
					framebuffer_read_valid[0] <= 0;
					surface_be_pipe1 <= surface_be_pipe0;
					if (framebuffer_read_valid[1]) begin
						framebuffer_fifo[framebuffer_fifo_write_ptr] <= surface_pixel_odd ?
							{fb_even_q, fb_odd_q} : {fb_odd_q, fb_even_q};
						surface_fifo_be[framebuffer_fifo_write_ptr] <= surface_be_pipe1;
						framebuffer_fifo_write_ptr <= framebuffer_fifo_write_ptr + 1'b1;
						framebuffer_fifo_count <= framebuffer_fifo_count + 1'b1;
					end
					if (framebuffer_reads_issued < framebuffer_write_burst_length &&
					    framebuffer_fifo_count + framebuffer_read_valid[0] +
					    framebuffer_read_valid[1] < WRITE_FIFO_DEPTH) issue_surface_pair();
					if (framebuffer_fifo_count >= 4 ||
					    (framebuffer_reads_issued == framebuffer_write_burst_length &&
					     framebuffer_read_valid == 0 && framebuffer_fifo_count != 0)) begin
						ddram_we <= 1;
						state <= ST_SURFACE_STORE_STREAM;
					end
				end
				ST_SURFACE_STORE_STREAM: begin
					framebuffer_read_valid[1] <= framebuffer_read_valid[0];
					framebuffer_read_valid[0] <= 0;
					surface_be_pipe1 <= surface_be_pipe0;
					if (framebuffer_read_valid[1]) begin
						framebuffer_fifo[framebuffer_fifo_write_ptr] <= surface_pixel_odd ?
							{fb_even_q, fb_odd_q} : {fb_odd_q, fb_even_q};
						surface_fifo_be[framebuffer_fifo_write_ptr] <= surface_be_pipe1;
						framebuffer_fifo_write_ptr <= framebuffer_fifo_write_ptr + 1'b1;
					end
					if (framebuffer_reads_issued < framebuffer_write_burst_length &&
					    framebuffer_fifo_count + framebuffer_read_valid[0] +
					    framebuffer_read_valid[1] -
					    ((ddram_we && !ddram_busy) ? 1'b1 : 1'b0) < WRITE_FIFO_DEPTH)
						issue_surface_pair();
					case ({framebuffer_read_valid[1], ddram_we && !ddram_busy})
						2'b10: framebuffer_fifo_count <= framebuffer_fifo_count + 1'b1;
						2'b01: framebuffer_fifo_count <= framebuffer_fifo_count - 1'b1;
						default: framebuffer_fifo_count <= framebuffer_fifo_count;
					endcase
					if (ddram_we && !ddram_busy) begin
						framebuffer_fifo_read_ptr <= framebuffer_fifo_read_ptr + 1'b1;
						if (framebuffer_write_beats_left == 1) begin
							ddram_we <= 0;
							framebuffer_read_valid <= 0;
							surface_word_index <= surface_word_index + framebuffer_write_burst_length;
							if (surface_word_index + framebuffer_write_burst_length >= surface_row_words)
								state <= ST_SURFACE_NEXT_ROW;
							else state <= ST_SURFACE_BURST;
						end else framebuffer_write_beats_left <= framebuffer_write_beats_left - 1'b1;
					end
				end
				ST_SURFACE_NEXT_ROW: begin
					if (surface_row + 1'b1 >= surface_height) begin
						// Texture reads following a store must see the completed DDR
						// writes, not a cache line retained from an earlier sampling.
						texture_cache_valid <= 0;
						next_command();
					end else begin
						surface_row <= surface_row + 1'b1;
						surface_row_addr <= surface_row_addr + surface_stride;
						surface_row_linear <= surface_row_linear + FB_WIDTH;
						surface_word_index <= 0;
						state <= ST_SURFACE_BURST;
					end
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
					ddram_be_control <= 8'hff;
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
									present_buffer <= present_buffer + 2'd1;
									state <= ST_CAPABILITY_WRITE;
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
					stage_source_row();
					water_destination_base <=
						((water_destination_start_y + water_row_index) << 8) +
						((water_destination_start_y + water_row_index) << 6) +
						water_row_destination_x;
					state <= ST_TILED_SOURCE_ADDRESS;
				end
				ST_TILED_SOURCE_ADDRESS: begin
					source_row_addr <= source_row_address_next;
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
				ST_WATER_SOURCE_ADDRESS, ST_WATER_SOURCE_ACCEPT,
				ST_WATER_SOURCE_DATA: begin
					if (state == ST_WATER_SOURCE_ADDRESS) begin
					if (axis_gather_mode) begin
						texture_cache_fill <= 0;
						start_read(source_byte_addr & 32'hffff_ff80, 16);
					end else begin
					// The ARM recognizer admits only 1,280-byte source rows. Build
					// that offset from shifts after registering the DDR table word;
					// this keeps the HPS DDR output off the address critical path.
					if (water_words_remaining > GPU_READ_BURST_WORDS)
						water_source_burst_left <= GPU_READ_BURST_WORDS;
					else water_source_burst_left <= water_words_remaining;
					start_read(axis_stream_mode ?
						axis_stream_row_address + ({24'b0, water_source_word} << 3) :
						(water_native_source ?
						{native_buffer_address(native_buffer), 3'b000} : src_base) +
						({16'b0, water_row_source_y} << 10) +
						({16'b0, water_row_source_y} << 8) +
						({21'b0, water_source_word} << 3),
						(water_words_remaining > GPU_READ_BURST_WORDS) ?
							GPU_READ_BURST_WORDS : water_words_remaining);
					end
					state <= ST_WATER_SOURCE_ACCEPT;
					end else if (state == ST_WATER_SOURCE_ACCEPT && !ddram_busy) begin
						ddram_rd <= 0;
						state <= ST_WATER_SOURCE_DATA;
					end
					// Destination RAM is synchronous. Pipeline each accepted DDR
					// pair through address setup and one registered read stage;
					// once full, the path sustains two blended pixels per clock.
					// Advance during request/accept gaps too: BRAM q never stops
					// clocking, so freezing metadata here corrupts burst boundaries.
					water_pipe1_valid <= water_pipe0_valid;
					// Share the existing eight tint multipliers with the scalar
					// renderer; only one command engine can be active at a time.
					tint_product[0] <= water_pipe0_pixel0[31:24] * water_pipeline_tint[31:24];
					tint_product[1] <= water_pipe0_pixel0[23:16] * water_pipeline_tint[23:16];
					tint_product[2] <= water_pipe0_pixel0[15:8] * water_pipeline_tint[15:8];
					tint_product[3] <= water_pipe0_pixel0[7:0] * water_pipeline_tint[7:0];
					tint_product[4] <= water_pipe0_pixel1[31:24] * water_pipeline_tint[31:24];
					tint_product[5] <= water_pipe0_pixel1[23:16] * water_pipeline_tint[23:16];
					tint_product[6] <= water_pipe0_pixel1[15:8] * water_pipeline_tint[15:8];
					tint_product[7] <= water_pipe0_pixel1[7:0] * water_pipeline_tint[7:0];
					water_pipe1_destination0 <= water_pipe0_destination0;
					water_pipe1_destination1 <= water_pipe0_destination1;
					water_pipe1_odd0 <= water_pipe0_odd0;
					water_pipe1_odd1 <= water_pipe0_odd1;
					water_pipe2_valid <= water_pipe1_valid;
					water_pipe2_pixel0 <= {
						tint_mul255_product(tint_product[0], water_pipeline_floor),
						tint_mul255_product(tint_product[1], water_pipeline_floor),
						tint_mul255_product(tint_product[2], water_pipeline_floor),
						tint_mul255_product(tint_product[3], water_pipeline_floor)};
					water_pipe2_pixel1 <= {
						tint_mul255_product(tint_product[4], water_pipeline_floor),
						tint_mul255_product(tint_product[5], water_pipeline_floor),
						tint_mul255_product(tint_product[6], water_pipeline_floor),
						tint_mul255_product(tint_product[7], water_pipeline_floor)};
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
						(8'd255 - (water_subtract_mode ? water_pipe2_pixel0[7:0] : water_pipe2_pixel0[31:24]));
					water_pipe3_destination_g[0] <= water_pipe2_background0[15:8] *
						(8'd255 - (water_subtract_mode ? water_pipe2_pixel0[15:8] : water_pipe2_pixel0[31:24]));
					water_pipe3_destination_b[0] <= water_pipe2_background0[23:16] *
						(8'd255 - (water_subtract_mode ? water_pipe2_pixel0[23:16] : water_pipe2_pixel0[31:24]));
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
						(8'd255 - (water_subtract_mode ? water_pipe2_pixel1[7:0] : water_pipe2_pixel1[31:24]));
					water_pipe3_destination_g[1] <= water_pipe2_background1[15:8] *
						(8'd255 - (water_subtract_mode ? water_pipe2_pixel1[15:8] : water_pipe2_pixel1[31:24]));
					water_pipe3_destination_b[1] <= water_pipe2_background1[23:16] *
						(8'd255 - (water_subtract_mode ? water_pipe2_pixel1[23:16] : water_pipe2_pixel1[31:24]));
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
						(water_subtract_mode || water_pipe3_source_a[0] != 0);
					water_pipe4_pixel[0] <= water_subtract_mode ? {
						div255_floor({1'b0, water_pipe3_destination_a[0]}),
						div255_floor({1'b0, water_pipe3_destination_b[0]}),
						div255_floor({1'b0, water_pipe3_destination_g[0]}),
						div255_floor({1'b0, water_pipe3_destination_r[0]})} : water_additive_mode ? {
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
						(water_subtract_mode || water_pipe3_source_a[1] != 0);
					water_pipe4_pixel[1] <= water_subtract_mode ? {
						div255_floor({1'b0, water_pipe3_destination_a[1]}),
						div255_floor({1'b0, water_pipe3_destination_b[1]}),
						div255_floor({1'b0, water_pipe3_destination_g[1]}),
						div255_floor({1'b0, water_pipe3_destination_r[1]})} : water_additive_mode ? {
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

					if (state == ST_WATER_SOURCE_DATA && axis_gather_mode && !water_source_done) begin
						if (gather_refilling) begin
							if (ddram_dout_ready) begin
								texture_cache_data[texture_cache_fill] <= ddram_dout;
								if (texture_cache_fill == 15) begin
									texture_cache_valid <= 1;
									texture_cache_addr <= source_byte_addr[31:3] & 29'h1fff_fff0;
									gather_refilling <= 0;
									gather_lookup <= 1;
								end else texture_cache_fill <= texture_cache_fill + 1'b1;
							end
						end else if (!gather_lookup) begin
							// Launch the preceding registered texels and synchronous BRAM
							// reads together, while preparing the next pair's addresses.
							water_pipe0_valid <= gather_pending;
							water_pipe0_pixel0 <= source_pixel;
							water_pipe0_pixel1 <= source_pixel_1;
							water_pipe0_destination0 <= gather_pair_destination[16:1];
							water_pipe0_destination1 <= gather_pair_destination1[16:1];
							water_pipe0_odd0 <= gather_pair_destination[0];
							water_pipe0_odd1 <= gather_pair_destination1[0];
							if (gather_pending[0]) begin
								if (gather_pair_destination[0]) fb_odd_read_address <= gather_pair_destination[16:1];
								else fb_even_read_address <= gather_pair_destination[16:1];
							end
							if (gather_pending[1]) begin
								if (gather_pair_destination1[0]) fb_odd_read_address <= gather_pair_destination1[16:1];
								else fb_even_read_address <= gather_pair_destination1[16:1];
							end
							gather_pending <= 0;
							if (gather_remaining == 0) water_source_done <= 1;
							else begin
								source_byte_addr <= axis_source_addr0;
								source_byte_addr_1 <= axis_source_addr1;
								gather_lookup <= 1;
							end
						end else if (texture_cache_valid &&
						             texture_cache_addr == (source_byte_addr[31:3] & 29'h1fff_fff0)) begin
							// Both texels must belong to this cache line. A crossing emits
							// one lane and retries the next pixel; no speculative DDR read.
							source_pixel <= source_byte_addr[2] ? texture_cache_data[source_byte_addr[6:3]][63:32] :
								texture_cache_data[source_byte_addr[6:3]][31:0];
							source_pixel_1 <= source_byte_addr_1[2] ? texture_cache_data[source_byte_addr_1[6:3]][63:32] :
								texture_cache_data[source_byte_addr_1[6:3]][31:0];
							gather_pending <= {gather_pair, 1'b1};
							gather_pair_destination <= gather_destination;
							gather_destination <= gather_destination + (gather_pair ? 17'd2 : 17'd1);
							gather_remaining <= gather_remaining - (gather_pair ? 9'd2 : 9'd1);
							u_current <= u_current + (gather_pair ? (u_step <<< 1) : u_step);
							u_next <= u_next + (gather_pair ? (u_step <<< 1) : u_step);
							gather_lookup <= 0;
						end else begin
							gather_refilling <= 1;
							state <= ST_WATER_SOURCE_ADDRESS;
						end
					end else if (state == ST_WATER_SOURCE_DATA && tiled_mode && !water_source_done) begin
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
					end else if (state == ST_WATER_SOURCE_DATA && !tiled_mode && !axis_gather_mode && ddram_dout_ready) begin
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
						if (water_source_word + 8'd1 >= water_source_words) begin
							water_source_done <= 1;
						end else begin
							water_source_word <= water_source_word + 1'b1;
							if (water_source_burst_left == 1)
								state <= ST_WATER_SOURCE_ADDRESS;
						end
					end

					if (state == ST_WATER_SOURCE_DATA && water_source_done &&
					    (tiled_mode || !ddram_dout_ready) &&
					    water_pipe0_valid == 0 && water_pipe1_valid == 0 &&
					    water_pipe2_valid == 0 && water_pipe3_valid == 0 &&
					    water_pipe4_valid == 0) begin
						if (axis_stream_mode || axis_gather_mode) begin
							// This row's visible suffix is complete, including clipped
							// right-edge pixels. Preserve the old per-row UV/tint math.
							axis_stream_mode <= 0;
							axis_gather_mode <= 0;
							water_subtract_mode <= 0;
							water_additive_mode <= 0;
							blit_x <= 0;
							blit_y <= blit_y + 1'b1;
							u_start <= u_start + u_y_step;
							v_start <= v_start + v_step;
							tint_r_current <= tint_r_current + ($signed({{9{tint_r_step[15]}}, tint_r_step}) <<< 8);
							tint_g_current <= tint_g_current + ($signed({{9{tint_g_step[15]}}, tint_g_step}) <<< 8);
							tint_b_current <= tint_b_current + ($signed({{9{tint_b_step[15]}}, tint_b_step}) <<< 8);
							tint_a_current <= tint_a_current + ($signed({{9{tint_a_step[15]}}, tint_a_step}) <<< 8);
							state <= ST_BLIT_ROW;
						end else if (water_row_index + 1'b1 >= water_row_count) begin
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
					// Never overwrite the latest published frame or a buffer either
					// video reader holds. Fast/simple game frames may otherwise lap
					// vblank. Readers only ever move to the latest published
					// frame, so the chosen buffer stays free until it is written.
					if (present_free) begin
						present_buffer <= present_choice;
						framebuffer_write_base <= native_buffer_address(present_choice);
						state <= ST_FRAMEBUFFER_WRITE_START;
					end
				end
				ST_CAPABILITY_WRITE: begin
					// Publish features before the matching completion sequence. A
					// runner clears these words before probing an unknown RBF.
					ddram_addr <= CONTROL_WORD_ADDR + 29'd10;
					ddram_burstcnt <= 1;
					ddram_din_control <= {CAPABILITY_FEATURES, CAPABILITY_MAGIC};
					ddram_be_control <= 8'hff;
					ddram_we <= 1;
					state <= ST_CAPABILITY_ACCEPT;
				end
				ST_CAPABILITY_ACCEPT: if (!ddram_busy) begin
					// Keep the request and payload unchanged throughout stalls.
					ddram_we <= 0;
					state <= ST_COMPLETE;
				end
				ST_COMPLETE: begin
					// The completion word reports which native DDR buffer now
					// contains the finished frame. The HPS renderer uses these bits
					// for exact mid-frame readback; cycle counts remain independent.
					// Bit 29 reports a scanout FIFO underflow since the preceding
					// completed job. Twenty-nine cycle bits still cover over six
					// seconds at 88 MHz.
					// Latch the whole completion payload only once. In particular,
					// operation_cycles must not change while Avalon is stalled.
					if (!ddram_we) begin
						ddram_addr <= (CONTROL_ADDR + 24) >> 3;
						ddram_burstcnt <= 1;
						ddram_din_control <= {{native_buffer,
						               scan_underflow_sync[1] != scan_underflow_seen,
						               operation_cycles[28:0]}, active_sequence};
						completion_underflow_snapshot <= scan_underflow_sync[1];
						ddram_be_control <= 8'hff;
						ddram_we <= 1;
					end else if (!ddram_busy) begin
						ddram_we <= 0;
						// A toggle arriving while this payload is stalled belongs to
						// the next job; consume only the toggle captured above.
						scan_underflow_seen <= completion_underflow_snapshot;
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
