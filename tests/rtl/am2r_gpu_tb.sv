`timescale 1ns/1ps

module am2r_gpu_tb;
	localparam [28:0] CONTROL_WORD = 32'h23ff0000 >> 3;
	localparam [28:0] COMMAND_WORD = 32'h23fe0000 >> 3;
	localparam [28:0] TEXTURE_WORD = 32'h24000000 >> 3;
	localparam [28:0] WATER_TABLE_WORD = 32'h26000000 >> 3;
	localparam [28:0] EXPORT_WORD = 32'h25000000 >> 3;
	localparam [28:0] NATIVE_BUF0_WORD = 32'h3a000100 >> 3;
	localparam [28:0] NATIVE_BUF1_WORD = 32'h3a04b100 >> 3;
	localparam [28:0] NATIVE_BUF2_WORD = 32'h3a096100 >> 3;
	localparam integer FB_WORDS = 320 * 240 / 2;

	reg clk = 0;
	reg reset = 1;
	reg ddram_busy = 0;
	reg [63:0] ddram_dout = 0;
	reg ddram_dout_ready = 0;
	wire [7:0] ddram_burstcnt;
	wire [28:0] ddram_addr;
	wire ddram_rd;
	wire [63:0] ddram_din;
	wire [7:0] ddram_be;
	wire ddram_we;
	wire [31:0] native_frame;
	wire [1:0] native_buffer;
	reg scan_buffer_valid = 0;
	reg [1:0] scan_buffer = 0;
	reg scan_underflow_toggle = 0;

	reg [63:0] control [0:3];
	reg [63:0] commands [0:111];
	reg [63:0] textures [0:319];
	reg [63:0] water_table [0:2];
	reg [63:0] captured_frame [0:FB_WORDS-1];
	reg [63:0] exported_frame [0:FB_WORDS-1];
	reg pending_read = 0;
	reg [28:0] read_address;
	reg [7:0] read_remaining;
	reg [2:0] read_delay;
	reg pending_write_burst = 0;
	reg [28:0] write_address;
	reg [7:0] write_remaining;
	integer cycles = 0;
	integer errors = 0;
	integer streamed_pairs = 0;
	integer prefetched_solid_pairs = 0;
	integer alpha_white_pairs = 0;
	reg [5:0] previous_gpu_state = 0;

	am2r_gpu dut
	(
		.clk(clk), .reset(reset),
		.ddram_busy(ddram_busy), .ddram_burstcnt(ddram_burstcnt),
		.ddram_addr(ddram_addr), .ddram_dout(ddram_dout),
		.ddram_dout_ready(ddram_dout_ready), .ddram_rd(ddram_rd),
		.ddram_din(ddram_din), .ddram_be(ddram_be), .ddram_we(ddram_we),
		.scan_buffer_valid(scan_buffer_valid), .scan_buffer(scan_buffer),
		.scan_underflow_toggle(scan_underflow_toggle),
		.native_frame(native_frame), .native_buffer(native_buffer)
	);

	always #10 clk = ~clk;

	function automatic [63:0] read_memory;
		input [28:0] address;
		integer index;
		begin
			read_memory = 64'h0;
			if (address >= CONTROL_WORD && address < CONTROL_WORD + 4)
				read_memory = control[address - CONTROL_WORD];
			else if (address >= COMMAND_WORD && address < COMMAND_WORD + 112)
				read_memory = commands[address - COMMAND_WORD];
			else if (address >= TEXTURE_WORD && address < TEXTURE_WORD + 320)
				read_memory = textures[address - TEXTURE_WORD];
			else if (address >= WATER_TABLE_WORD && address < WATER_TABLE_WORD + 3)
				read_memory = water_table[address - WATER_TABLE_WORD];
			else if (address >= NATIVE_BUF0_WORD && address < NATIVE_BUF0_WORD + FB_WORDS)
				read_memory = captured_frame[address - NATIVE_BUF0_WORD];
			else if (address >= NATIVE_BUF1_WORD && address < NATIVE_BUF1_WORD + FB_WORDS)
				read_memory = captured_frame[address - NATIVE_BUF1_WORD];
			else if (address >= NATIVE_BUF2_WORD && address < NATIVE_BUF2_WORD + FB_WORDS)
				read_memory = captured_frame[address - NATIVE_BUF2_WORD];
		end
	endfunction

	task automatic write_memory;
		input [28:0] address;
		input [63:0] data;
		input [7:0] byte_enable;
		integer index;
		begin
			if (address >= CONTROL_WORD && address < CONTROL_WORD + 4) begin
				index = address - CONTROL_WORD;
				if (byte_enable[0]) control[index][7:0] = data[7:0];
				if (byte_enable[1]) control[index][15:8] = data[15:8];
				if (byte_enable[2]) control[index][23:16] = data[23:16];
				if (byte_enable[3]) control[index][31:24] = data[31:24];
				if (byte_enable[4]) control[index][39:32] = data[39:32];
				if (byte_enable[5]) control[index][47:40] = data[47:40];
				if (byte_enable[6]) control[index][55:48] = data[55:48];
				if (byte_enable[7]) control[index][63:56] = data[63:56];
			end else if (address >= NATIVE_BUF0_WORD && address < NATIVE_BUF0_WORD + FB_WORDS) begin
				captured_frame[address - NATIVE_BUF0_WORD] = data;
			end else if (address >= NATIVE_BUF1_WORD && address < NATIVE_BUF1_WORD + FB_WORDS) begin
				captured_frame[address - NATIVE_BUF1_WORD] = data;
			end else if (address >= NATIVE_BUF2_WORD && address < NATIVE_BUF2_WORD + FB_WORDS) begin
				captured_frame[address - NATIVE_BUF2_WORD] = data;
			end else if (address >= EXPORT_WORD && address < EXPORT_WORD + FB_WORDS) begin
				exported_frame[address - EXPORT_WORD] = data;
			end else begin
				$display("Unexpected write address %h", address);
				errors = errors + 1;
			end
		end
	endtask

	task automatic expect_export_pixel;
		input integer x, y;
		input [31:0] expected;
		integer linear;
		reg [31:0] actual;
		begin
			linear = y * 320 + x;
			actual = linear[0] ? exported_frame[linear >> 1][63:32] : exported_frame[linear >> 1][31:0];
			if (actual !== expected) begin
				$display("Export pixel (%0d,%0d): got %08h expected %08h", x, y, actual, expected);
				errors = errors + 1;
			end
		end
	endtask

	function automatic [31:0] expected_xrgb;
		input [31:0] rgba;
		begin
			expected_xrgb = {8'h00, rgba[7:0], rgba[15:8], rgba[23:16]};
		end
	endfunction

	task automatic expect_full_frame_pattern;
		integer word_index;
		reg [31:0] even_pixel, odd_pixel;
		begin
			for (word_index = 0; word_index < FB_WORDS; word_index = word_index + 1) begin
				even_pixel = 32'hff000000 | word_index;
				odd_pixel = 32'hff800000 | word_index;
				if (exported_frame[word_index] !== {odd_pixel, even_pixel}) begin
					$display("Export pattern word %0d: got %016h expected %016h",
					         word_index, exported_frame[word_index], {odd_pixel, even_pixel});
					errors = errors + 1;
				end
				if (captured_frame[word_index] !==
				    {expected_xrgb(odd_pixel), expected_xrgb(even_pixel)}) begin
					$display("Present pattern word %0d: got %016h expected %016h",
					         word_index, captured_frame[word_index],
					         {expected_xrgb(odd_pixel), expected_xrgb(even_pixel)});
					errors = errors + 1;
				end
			end
		end
	endtask

	// Avalon DDR model: two-cycle read latency and periodic back-pressure.
	always @(posedge clk) begin
		cycles <= cycles + 1;
		if (dut.state == 6'd24 && previous_gpu_state == 6'd24)
			streamed_pairs <= streamed_pairs + 1;
		if (dut.state == 6'd22 && previous_gpu_state == 6'd13 && dut.solid_mode)
			prefetched_solid_pairs <= prefetched_solid_pairs + 1;
		if (dut.state == 6'd22 && previous_gpu_state == 6'd24 &&
		    dut.tint_color[23:0] == 24'hffffff && dut.tint_color[31:24] != 8'hff)
			alpha_white_pairs <= alpha_white_pairs + 1;
		previous_gpu_state <= dut.state;
		ddram_busy <= ((cycles % 11) == 7);
		ddram_dout_ready <= 0;
		if (ddram_we && !ddram_busy) begin
			if (ddram_burstcnt > 8'd128)
				$fatal(1, "FAIL: GPU DDR burst %0d exceeds MiSTer maximum", ddram_burstcnt);
			if (pending_write_burst) begin
				write_memory(write_address, ddram_din, ddram_be);
				write_address <= write_address + 1'b1;
				if (write_remaining == 1) begin
					pending_write_burst <= 0;
					write_remaining <= 0;
				end else write_remaining <= write_remaining - 1'b1;
			end else begin
				write_memory(ddram_addr, ddram_din, ddram_be);
				if (ddram_burstcnt > 1) begin
					pending_write_burst <= 1;
					write_address <= ddram_addr + 1'b1;
					write_remaining <= ddram_burstcnt - 1'b1;
				end
			end
		end

		if (ddram_rd && !ddram_busy && !pending_read) begin
			if (ddram_burstcnt > 8'd128)
				$fatal(1, "FAIL: GPU DDR read burst %0d exceeds MiSTer maximum", ddram_burstcnt);
			pending_read <= 1;
			read_address <= ddram_addr;
			read_remaining <= ddram_burstcnt;
			read_delay <= 2;
		end else if (pending_read) begin
			if (read_delay != 0) read_delay <= read_delay - 1'b1;
			else begin
				ddram_dout <= read_memory(read_address);
				ddram_dout_ready <= 1;
				read_address <= read_address + 1'b1;
				if (read_remaining == 1) pending_read <= 0;
				else read_remaining <= read_remaining - 1'b1;
			end
		end
	end

	function automatic [7:0] blend_component;
		input [7:0] src, dst, alpha;
		integer value;
		begin
			value = src * alpha + dst * (255 - alpha);
			blend_component = value / 255;
		end
	endfunction

	task automatic expect_pixel;
		input integer x, y;
		input [31:0] expected;
		integer linear;
		reg [31:0] actual;
		begin
			linear = y * 320 + x;
			actual = linear[0] ? captured_frame[linear >> 1][63:32] : captured_frame[linear >> 1][31:0];
			if (actual !== expected) begin
				$display("Pixel (%0d,%0d): got %08h expected %08h", x, y, actual, expected);
				errors = errors + 1;
			end
		end
	endtask

	integer n;
	reg [7:0] expected_r, expected_g, expected_b;
	initial begin
		for (n = 0; n < 4; n = n + 1) control[n] = 0;
		for (n = 0; n < 112; n = n + 1) commands[n] = 0;
		for (n = 0; n < 320; n = n + 1) textures[n] = 0;
		for (n = 0; n < FB_WORDS; n = n + 1) begin
			captured_frame[n] = 64'hx;
			exported_frame[n] = 64'hx;
		end
		// Opcode 8 sources the immutable prior native XRGB frame. Give row 1
		// two known pixels so the test also proves conversion back to opaque
		// renderer RGBA before the water alpha blend.
		captured_frame[160] = {32'h00ff0000, 32'h0000ff00};

		// Four RGBA pixels per row, packed two per DDR word.
		textures[0] = {32'hff00ff00, 32'hff0000ff}; // green, red
		textures[1] = {32'h80ff0000, 32'h00000000}; // 50% blue, transparent
		textures[2] = {32'hffffffff, 32'hff00ffff}; // white, yellow
		textures[3] = {32'hffff00ff, 32'hff000000}; // magenta, black
		textures[160] = {32'hffffffff, 32'hff00ffff}; // white, yellow
		textures[161] = {32'hffff00ff, 32'hff000000}; // magenta, black
		// Two 32-pixel rows for the repeated-tile command. Row zero ramps
		// red and row one ramps green, making phase and row stepping visible.
		for (n = 0; n < 16; n = n + 1) begin
			textures[192 + n] = {
				32'hff000000 | ((2 * n + 1) * 8),
				32'hff000000 | ((2 * n) * 8)};
			textures[208 + n] = {
				32'hff000000 | (((2 * n + 1) * 8) << 8),
				32'hff000000 | (((2 * n) * 8) << 8)};
		end
		// Two fused water rows. The first begins at source x=1 and the second
		// shifts destination parity, exercising both cross-bank arrangements.
		water_table[0] = (64'd0 << 48) | (64'd1 << 32) |
		                 (64'd3 << 16) | 64'd60;
		water_table[1] = (64'd1 << 48) | (64'd0 << 32) |
		                 (64'd2 << 16) | 64'd61;
		water_table[2] = (64'd1 << 48) | (64'd1 << 32) |
		                 (64'd1 << 16) | 64'd100;

		// CLEAR to RGBA(8,16,32,255).
		commands[0] = 64'h0000000000000001;
		commands[1] = 64'h00000000ff201008;
		// BLIT the 4x2 texture to (1,1), unit nearest-neighbour steps.
		commands[8] = (64'd2) | (64'd4 << 16) | (64'd2 << 32);
		commands[9] = (64'd16 << 32) | 64'h24000000;
		commands[10] = (64'd1 << 16) | 64'd1;
		commands[12] = 0;
		commands[13] = (64'h00010000 << 32) | 64'h00010000;
		commands[14] = 64'h00000000ffffffff;
		// FILL a three-pixel span from odd x=5 with 50% green. This exercises
		// both the paired and single-pixel solid blend paths.
		commands[16] = (64'd3) | (64'd3 << 16) | (64'd1 << 32);
		commands[17] = 64'h000000008000ff00;
		commands[18] = (64'd2 << 16) | 64'd5;
		// BLIT one red texel through a vertical alpha gradient: 255,191,127.
		commands[24] = (64'd2) | (64'd1 << 16) | (64'd3 << 32);
		commands[25] = (64'd16 << 32) | 64'h24000000;
		commands[26] = (64'd1 << 16) | 64'd9;
		commands[28] = 0;
		commands[29] = 0;
		commands[30] = 64'h00000000ffffffff;
		commands[31] = 64'hc000000000000000;
		// Additively blend 50%-alpha blue over the clear colour. Bit 8 selects
		// bm_add while retaining the same texture/tint path.
		commands[32] = (64'd2) | (64'd1 << 8) | (64'd1 << 16) | (64'd1 << 32);
		commands[33] = (64'd16 << 32) | 64'h24000000;
		commands[34] = (64'd1 << 16) | 64'd11;
		commands[36] = 64'd3 << 16;
		commands[37] = 0;
		commands[38] = 64'h00000000ffffffff;
		// One-shot 50%-alpha additive state for the following affine blit.
		commands[40] = 64'd5 | (64'd1 << 8);
		commands[41] = 64'h0000000080ffffff;
		// AFFINE BLIT with both cross-axis derivatives. This shears the 4x2
		// source into a 3x2 box and rejects samples at the exclusive vMax edge.
		commands[48] = (64'd4) | (64'd3 << 16) | (64'd2 << 32);
		commands[49] = (64'd16 << 32) | 64'h24000000;
		commands[50] = (64'd1 << 16) | 64'd20;
		commands[51] = 64'd4 << 48;
		commands[52] = 64'd2 << 48;
		commands[53] = 0;
		commands[54] = (64'h00010000 << 32) | 64'h00010000;
		commands[55] = 64'h0000000000010000;
		// Paired BLIT with a 50% RGB tint. This covers the registered cache
		// selection stage before both parallel tint multipliers.
		commands[56] = (64'd2) | (64'd2 << 16) | (64'd1 << 32);
		commands[57] = (64'd16 << 32) | 64'h24000000;
		commands[58] = (64'd1 << 16) | 64'd30;
		commands[60] = 0;
		commands[61] = (64'h00010000 << 32) | 64'h00010000;
		commands[62] = 64'h00000000ff808080;
		// Solid vertical gradient using the fill descriptor's row deltas. This
		// is the operation used by AM2R's dark-room overlay in the slot-4 repro.
		commands[64] = (64'd3) | (64'd2 << 16) | (64'd3 << 32);
		commands[65] = 64'h00000000ff0000ff;
		commands[66] = (64'd1 << 16) | 64'd40;
		commands[71] = 64'hc000000040008000;
		// Pair of opaque texels with a white 50%-alpha tint. This is the
		// accelerated blend used for AM2R's full-width water distortion rows.
		commands[72] = (64'd2) | (64'd2 << 16) | (64'd1 << 32);
		commands[73] = (64'd16 << 32) | 64'h24000000;
		commands[74] = (64'd1 << 16) | 64'd50;
		commands[76] = 0;
		commands[77] = (64'h00010000 << 32) | 64'h00010000;
		commands[78] = 64'h0000000080ffffff;
		// Batched water rows with a white 50%-alpha tint.
		commands[80] = 64'd7 | (64'd2 << 16);
		commands[81] = (64'd1280 << 32) | 64'h24000000;
		commands[82] = (64'd5 << 32) | 64'h26000000;
		commands[86] = 64'h0000000033ffffff;
		// Water alias sourced from the prior native frame, then export the live
		// RGBA render target to a texture and END/present.
		commands[88] = 64'd8 | (64'd1 << 16);
		commands[90] = (64'd10 << 32) | 64'h26000010;
		commands[94] = 64'h0000000033ffffff;
		commands[96] = 64'd6;
		commands[97] = 64'h0000000025000000;
		commands[104] = 0;

		control[1] = (64'd14 << 32) | 64'h23fe0000;
		control[2] = 64'h0000000022001000;
		control[3] = 0;

		repeat (8) @(posedge clk);
		reset <= 0;
		repeat (8) @(posedge clk);
		control[0] = (64'd1 << 32) | 64'h50473241;

		wait (control[3][31:0] == 1 || cycles > 1000000);
		if (cycles > 1000000) begin
			$display("Timeout waiting for completion");
			errors = errors + 1;
		end

		// Untouched clear pixel and four opaque/transparent cases.
		expect_pixel(0, 0, 32'h00081020);
		expect_pixel(1, 1, 32'h00ff0000);
		expect_pixel(2, 1, 32'h0000ff00);
		expect_pixel(3, 1, 32'h00081020);

		expected_r = blend_component(0, 8, 128);
		expected_g = blend_component(0, 16, 128);
		expected_b = blend_component(255, 32, 128);
		expect_pixel(4, 1, {8'h00, expected_r, expected_g, expected_b});
		expect_pixel(1, 2, 32'h00ffff00);
		expect_pixel(2, 2, 32'h00ffffff);
		expect_pixel(3, 2, 32'h00000000);
		expect_pixel(4, 2, 32'h00ff00ff);
		expected_r = blend_component(0, 8, 128);
		expected_g = blend_component(255, 16, 128);
		expected_b = blend_component(0, 32, 128);
		expect_pixel(5, 2, {8'h00, expected_r, expected_g, expected_b});
		expect_pixel(6, 2, {8'h00, expected_r, expected_g, expected_b});
		expect_pixel(7, 2, {8'h00, expected_r, expected_g, expected_b});
		expect_pixel(8, 2, 32'h00081020);
		expect_pixel(9, 1, 32'h00ff0000);
		expected_r = blend_component(255, 8, 191);
		expected_g = blend_component(0, 16, 191);
		expected_b = blend_component(0, 32, 191);
		expect_pixel(9, 2, {8'h00, expected_r, expected_g, expected_b});
		expected_r = blend_component(255, 8, 127);
		expected_g = blend_component(0, 16, 127);
		expected_b = blend_component(0, 32, 127);
		expect_pixel(9, 3, {8'h00, expected_r, expected_g, expected_b});
		expect_pixel(11, 1, 32'h000810a0);
		expect_pixel(20, 1, 32'h00881020);
		expect_pixel(21, 1, 32'h008890a0);
		expect_pixel(22, 1, 32'h00081020);
		expect_pixel(20, 2, 32'h00089020);
		expect_pixel(21, 2, 32'h00081020);
		expect_pixel(22, 2, 32'h00081020);
		expect_pixel(30, 1, 32'h00800000);
		expect_pixel(31, 1, 32'h00008000);
		expect_pixel(40, 1, 32'h00ff0000);
		expected_r = blend_component(127, 8, 191);
		expected_g = blend_component(64, 16, 191);
		expected_b = blend_component(0, 32, 191);
		expect_pixel(40, 2, {8'h00, expected_r, expected_g, expected_b});
		expected_r = blend_component(0, 8, 127);
		expected_g = blend_component(128, 16, 127);
		expected_b = blend_component(0, 32, 127);
		expect_pixel(41, 3, {8'h00, expected_r, expected_g, expected_b});
		expected_r = blend_component(255, 8, 128);
		expected_g = blend_component(0, 16, 128);
		expected_b = blend_component(0, 32, 128);
		expect_pixel(50, 1, {8'h00, expected_r, expected_g, expected_b});
		expected_r = blend_component(0, 8, 128);
		expected_g = blend_component(255, 16, 128);
		expected_b = blend_component(0, 32, 128);
		expect_pixel(51, 1, {8'h00, expected_r, expected_g, expected_b});
		expected_r = blend_component(0, 8, 51);
		expected_g = blend_component(255, 16, 51);
		expected_b = blend_component(0, 32, 51);
		expect_pixel(60, 5, {8'h00, expected_r, expected_g, expected_b});
		expect_pixel(61, 5, 32'h00081020);
		expected_r = blend_component(0, 8, 26);
		expected_g = blend_component(0, 16, 26);
		expected_b = blend_component(255, 32, 26);
		expect_pixel(62, 5, {8'h00, expected_r, expected_g, expected_b});
		expected_r = blend_component(255, 8, 51);
		expected_g = blend_component(0, 16, 51);
		expected_b = blend_component(0, 32, 51);
		expect_pixel(100, 10, {8'h00, expected_r, expected_g, expected_b});
		expected_r = blend_component(255, 8, 51);
		expected_g = blend_component(255, 16, 51);
		expected_b = blend_component(0, 32, 51);
		expect_pixel(61, 6, {8'h00, expected_r, expected_g, expected_b});
		expected_r = blend_component(255, 8, 51);
		expected_g = blend_component(255, 16, 51);
		expected_b = blend_component(255, 32, 51);
		expect_pixel(62, 6, {8'h00, expected_r, expected_g, expected_b});
		expect_pixel(319, 239, 32'h00081020);
		// Export preserves the renderer's RGBA byte order and includes all
		// commands preceding the snapshot, while native present converts RGB.
		expect_export_pixel(0, 0, 32'hff201008);
		expect_export_pixel(1, 1, 32'hff0000ff);
		expect_export_pixel(319, 239, 32'hff201008);
		if (native_frame != 1 || native_buffer != 0) begin
			$display("Native frame publication mismatch frame=%0d buffer=%0d", native_frame, native_buffer);
			errors = errors + 1;
		end
		if (control[3][63:32] == 0) begin
			$display("GPU operation cycle counter was zero");
			errors = errors + 1;
		end
		if (streamed_pairs == 0) begin
			$display("Opaque unit-step pairs did not exercise cache-line streaming");
			errors = errors + 1;
		end
		if (prefetched_solid_pairs == 0) begin
			$display("Partial-alpha solid pairs did not exercise M10K prefetch");
			errors = errors + 1;
		end
		if (alpha_white_pairs == 0) begin
			$display("White alpha-tint pairs did not exercise the water fast path");
			errors = errors + 1;
		end
		if (control[3][63:62] !== 2'd0) begin
			$display("Completion metadata reported the wrong first native buffer");
			errors = errors + 1;
		end

		// Fill every M10K location with a unique pair before the second job. The
		// sparse rendering checks above cannot detect a dropped/duplicated word at
		// a 128-beat boundary because most pixels share the clear colour.
		for (n = 0; n < FB_WORDS; n = n + 1) begin
			dut.fb_even[n] = 32'hff000000 | n;
			dut.fb_odd[n] = 32'hff800000 | n;
			exported_frame[n] = 64'hx;
			captured_frame[n] = 64'hx;
		end
		for (n = 0; n < 112; n = n + 1) commands[n] = 0;
		commands[0] = 64'd6;
		commands[1] = 64'h0000000025000000;
		commands[8] = 0;
		control[1] = (64'd2 << 32) | 64'h23fe0000;

		// Once scanout has latched buffer 0, the next frame may publish buffer
		// 1. A third frame must use buffer 2 immediately rather than overwrite
		// buffer 0 or wait a complete raster. Further jobs may alternate the two
		// free buffers while scanout remains on buffer 0.
		scan_buffer_valid = 1;
		scan_buffer = 2'd0;
		control[0] = (64'd2 << 32) | 64'h50473241;
		wait (control[3][31:0] == 2);
		if (native_frame != 2 || native_buffer != 2'd1) errors = errors + 1;
		if (control[3][63:62] !== 2'd1) begin
			$display("Completion metadata did not report native buffer 1");
			errors = errors + 1;
		end
		expect_full_frame_pattern();
		control[0] = (64'd3 << 32) | 64'h50473241;
		wait (control[3][31:0] == 3);
		if (native_frame != 3 || native_buffer != 2'd2) begin
			$display("GPU did not publish through the third free buffer");
			errors = errors + 1;
		end
		if (control[3][63:62] !== 2'd2) begin
			$display("Completion metadata did not report native buffer 2");
			errors = errors + 1;
		end
		control[0] = (64'd4 << 32) | 64'h50473241;
		wait (control[3][31:0] == 4);
		if (native_frame != 4 || native_buffer != 2'd1) begin
			$display("GPU did not keep the active scanout buffer immutable");
			errors = errors + 1;
		end

		// A scanout underflow crosses from the video clock as a toggle. The
		// completion word must report it once without contaminating the 29-bit
		// renderer cycle count used by the HPS telemetry.
		scan_underflow_toggle = 1;
		repeat (4) @(posedge clk);
		control[0] = (64'd5 << 32) | 64'h50473241;
		wait (control[3][31:0] == 5);
		if (control[3][61] !== 1'b1 || control[3][60:32] == 0) begin
			$display("Completion metadata did not report scanout underflow");
			errors = errors + 1;
		end
		control[0] = (64'd6 << 32) | 64'h50473241;
		wait (control[3][31:0] == 6);
		if (control[3][61] !== 1'b0) begin
			$display("Scanout underflow marker was not one-shot");
			errors = errors + 1;
		end

		// A subsequent opcode-8 water row must source the immutable prior
		// native frame in HPS DDR and convert XRGB back to renderer RGBA.
		for (n = 0; n < 112; n = n + 1) commands[n] = 0;
		commands[0] = 64'd1;
		commands[1] = 64'h00000000ff000000;
		commands[8] = 64'd8 | (64'd1 << 16);
		commands[9] = 64'd1280 << 32;
		commands[10] = 64'h0000000026000000;
		commands[14] = 64'h0000000033ffffff;
		commands[16] = 0;
		water_table[0] = (64'd1 << 48) | (64'd0 << 32) |
		                 (64'd2 << 16) | 64'd0;
		control[1] = (64'd3 << 32) | 64'h23fe0000;
		control[0] = (64'd7 << 32) | 64'h50473241;
		wait (control[3][31:0] == 7);
		if (!dut.water_native_source) begin
			$display("Opcode 8 did not select the prior native frame");
			errors = errors + 1;
		end
		expect_pixel(0, 0, 32'h00200000);
		expect_pixel(1, 0, 32'h00200019);
		expect_pixel(2, 0, 32'h00000000);

		// A full-screen opaque additive fill must use the one-pair-per-clock
		// pipeline and preserve exact saturating-add semantics.
		for (n = 0; n < FB_WORDS; n = n + 1) begin
			dut.fb_even[n] = 32'hff102030;
			dut.fb_odd[n] = 32'hff102030;
		end
		for (n = 0; n < 112; n = n + 1) commands[n] = 0;
		commands[0] = 64'd3 | (64'd1 << 8) |
		              (64'd320 << 16) | (64'd240 << 32);
		commands[1] = 64'h00000000ff010203;
		commands[8] = 0;
		control[1] = (64'd2 << 32) | 64'h23fe0000;
		control[0] = (64'd8 << 32) | 64'h50473241;
		wait (control[3][31:0] == 8);
		expect_pixel(0, 0, 32'h00332211);
		expect_pixel(319, 239, 32'h00332211);
		if (control[3][60:32] >= 100000) begin
			$display("Fast additive fill took %0d cycles", control[3][60:32]);
			errors = errors + 1;
		end

		// Opcode 9 repeats a phase-rotated 32-pixel source row across the
		// destination. The second output row must advance the source row.
		for (n = 0; n < FB_WORDS; n = n + 1) begin
			dut.fb_even[n] = 32'hff0a141e;
			dut.fb_odd[n] = 32'hff0a141e;
		end
		for (n = 0; n < 112; n = n + 1) commands[n] = 0;
		commands[0] = 64'd9 | (64'd1 << 8) |
		              (64'd64 << 16) | (64'd2 << 32);
		commands[1] = (64'd128 << 32) | 64'h24000600;
		commands[2] = (64'd20 << 16);
		commands[3] = 64'd3;
		commands[4] = 0;
		commands[5] = (64'h00010000 << 32) | 64'h00010000;
		commands[6] = 64'h0000000080ffffff;
		commands[8] = 0;
		control[1] = (64'd2 << 32) | 64'h23fe0000;
		control[0] = (64'd9 << 32) | 64'h50473241;
		wait (control[3][31:0] == 9);
		expect_pixel(0, 20, 32'h002a140a);
		expect_pixel(28, 20, 32'h009a140a);
		expect_pixel(29, 20, 32'h001e140a);
		expect_pixel(32, 20, 32'h002a140a);
		expect_pixel(0, 21, 32'h001e200a);
		expect_pixel(64, 20, 32'h001e140a);

		if (errors == 0) begin
			$display("PASS: GPU rendering, fast overlays, native-frame water, and tear-free DDR publication");
			$finish;
		end else begin
			$fatal(1, "FAIL: %0d errors", errors);
		end
	end
endmodule
