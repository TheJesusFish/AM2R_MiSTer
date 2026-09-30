`timescale 1ns/1ps

// This bench also runs unchanged against the pre-pipeline GPU. It verifies
// identical two-clock CALCULATE/COMMIT latency and native integer pixels.
// The optional exhaustive check calls the production product combiner for
// every 8-bit source/destination/alpha combination in both normal/add modes.
module am2r_gpu_blend_pipeline_tb;
	reg clk = 0, reset = 1;
	am2r_gpu dut (
		.clk(clk), .reset(reset), .ddram_busy(1'b1),
		.ddram_dout(64'd0), .ddram_dout_ready(1'b0),
		.scan_buffer_valid(1'b0), .scan_buffer(2'd0),
		.hdmi_protect(4'd0), .scan_underflow_toggle(1'b0),
		.ddram_burstcnt(), .ddram_addr(), .ddram_rd(), .ddram_din(),
		.ddram_be(), .ddram_we(), .native_frame(), .native_buffer()
	);
	always #5 clk = ~clk;
	integer cases_run = 0;
	integer mode, lane, a, b, s, d, i, channel;
	integer divisor_input;
	reg [16:0] divisor_reference;
	reg [31:0] random_state = 32'h461fee29;
	reg [31:0] source0, source1, destination0, destination1;
	reg [31:0] got, expected;
	reg [7:0] alphas [0:7];
	reg [15:0] source_product, destination_product;
	reg [47:0] source_products;
	reg [63:0] destination_products;

	function automatic [31:0] reference_pixel;
		input [31:0] src, dst;
		input integer blend;
		integer c, sa, sum;
		begin
			sa = src[31:24];
			for (c = 0; c < 4; c = c + 1) begin
				if (blend == 2)
					sum = (dst[c*8 +: 8] * (255 - src[c*8 +: 8])) / 255;
				else if (blend == 1) begin
					sum = dst[c*8 +: 8] + (c == 3 ? sa : (src[c*8 +: 8] * sa) / 255);
					if (sum > 255) sum = 255;
				end else if (c == 3) sum = sa + (dst[31:24] * (255 - sa)) / 255;
				else sum = (src[c*8 +: 8] * sa + dst[c*8 +: 8] * (255 - sa)) / 255;
				reference_pixel[c*8 +: 8] = sum[7:0];
			end
		end
	endfunction

	task automatic check_pipeline;
		input [31:0] src0, src1, dst0, dst1;
		input integer blend;
		input paired, odd_lane;
		reg [31:0] expected0, expected1;
		begin
			expected0 = reference_pixel(src0, dst0, blend);
			expected1 = reference_pixel(src1, dst1, blend);
			@(negedge clk);
			reset = 0;
			dut.state = 6'd62; // Existing ST_BLEND_CALCULATE, not a new stage.
			dut.tinted_pixel = src0;
			dut.tinted_pixel_1 = src1;
			dut.blend_destination0 = dst0;
			dut.blend_destination1 = dst1;
			dut.additive_mode = blend == 1;
			dut.subtract_mode = 0;
			dut.pair_mode = paired;
			dut.destination_odd = odd_lane;
			dut.destination_word = 16'd32;
			dut.fb_even_we = 0;
			dut.fb_odd_we = 0;
			@(posedge clk); #1;
			if (dut.state !== (paired ? 6'd46 : 6'd45) || dut.fb_even_we || dut.fb_odd_we)
				$fatal(1, "Blend calculation changed latency or wrote early");
			@(posedge clk); #1;
			if (dut.fb_even_we !== (paired || !odd_lane) || dut.fb_odd_we !== (paired || odd_lane))
				$fatal(1, "Blend commit lane enables changed");
			if (odd_lane) begin
				if (dut.fb_odd_write_data !== expected0 || dut.fb_odd_write_address !== 16'd32)
					$fatal(1, "Odd scalar result mismatch: src=%h dst=%h mode=%0d got=%h expected=%h", src0, dst0, blend, dut.fb_odd_write_data, expected0);
				if (paired && (dut.fb_even_write_data !== expected1 || dut.fb_even_write_address !== 16'd33))
					$fatal(1, "Odd pair second lane mismatch");
			end else begin
				if (dut.fb_even_write_data !== expected0 || dut.fb_even_write_address !== 16'd32)
					$fatal(1, "Even scalar result mismatch: src=%h dst=%h mode=%0d got=%h expected=%h", src0, dst0, blend, dut.fb_even_write_data, expected0);
				if (paired && (dut.fb_odd_write_data !== expected1 || dut.fb_odd_write_address !== 16'd32))
					$fatal(1, "Even pair second lane mismatch");
			end
			cases_run = cases_run + 1;
		end
	endtask

	initial begin
		// Preserve the previous shift/add expression over its ENTIRE input
		// domain, including bit16 and wraparound outside legal blend sums.
		// The divider's public return value is the low byte after shifting.
		for (divisor_input = 0; divisor_input < 131072; divisor_input = divisor_input + 1) begin
			divisor_reference = 17'(divisor_input) + 17'd1 + (17'(divisor_input) >> 8);
			if (dut.div255_floor(17'(divisor_input)) !== divisor_reference[15:8])
				$fatal(1, "DIV255 exact 17-bit mismatch at input %0d", divisor_input);
			if (divisor_input <= 65025 && dut.div255_floor(17'(divisor_input)) != divisor_input / 255)
				$fatal(1, "DIV255 native floor mismatch at input %0d", divisor_input);
		end
		$display("PASS DIV255: all 131072 inputs preserve exact 17-bit arithmetic; legal blend range matches integer floor");
		alphas[0] = 0; alphas[1] = 1; alphas[2] = 2; alphas[3] = 127;
		alphas[4] = 128; alphas[5] = 253; alphas[6] = 254; alphas[7] = 255;
		repeat (3) @(posedge clk);
		@(negedge clk); reset = 0;
		repeat (4) @(posedge clk); // Allow the internal reset synchronizer to drain.
		for (mode = 0; mode < 2; mode = mode + 1)
			for (lane = 0; lane < 4; lane = lane + 1)
				for (a = 0; a < 8; a = a + 1)
					for (b = 0; b < 8; b = b + 1)
						check_pipeline({alphas[a], 24'h00ff7f}, {alphas[b], 24'hff0080},
							32'hfffd0180, 32'h7f0200ff, mode, lane[1], lane[0]);
		for (i = 0; i < 8192; i = i + 1) begin
			random_state = random_state * 32'd1664525 + 32'd1013904223; source0 = random_state;
			random_state = random_state * 32'd1664525 + 32'd1013904223; source1 = random_state;
			random_state = random_state * 32'd1664525 + 32'd1013904223; destination0 = random_state;
			random_state = random_state * 32'd1664525 + 32'd1013904223; destination1 = random_state;
			check_pipeline(source0, source1, destination0, destination1, i % 2, i[1], i[2]);
		end
		$display("PASS blend pipeline: %0d scalar/pair cases, exactly 2 clocks each (CALCULATE + COMMIT)", cases_run);

`ifdef CHECK_BLEND_PRODUCTS_EXHAUSTIVE
		// Zero-delay exhaustive arithmetic; the stateful checks above already
		// exercise the actual registers and byte/lane mapping.
		for (mode = 0; mode < 2; mode = mode + 1) begin
			dut.additive_mode = mode == 1;
			for (a = 0; a < 256; a = a + 1)
				for (s = 0; s < 256; s = s + 1)
					for (d = 0; d < 256; d = d + 1) begin
						source0 = {a[7:0], s[7:0], s[7:0], s[7:0]};
						destination0 = {4{d[7:0]}};
						source_product = s * a;
						destination_product = d * (255 - a);
						source_products = {3{source_product}};
						destination_products = {4{destination_product}};
						got = dut.blended_pixel_products(source0, destination0, source_products, destination_products, mode == 1);
						expected = reference_pixel(source0, destination0, mode);
						if (got !== expected)
							$fatal(1, "Exhaustive blend mismatch mode=%0d a=%0d s=%0d d=%0d got=%h expected=%h", mode, a, s, d, got, expected);
					end
			$display("PASS exhaustive blend mode %0d: 16777216 source/destination/alpha combinations", mode);
		end
`endif
		$finish;
	end
endmodule
