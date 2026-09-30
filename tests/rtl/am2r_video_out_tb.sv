`timescale 1ns/1ps

// Drives the native raster through the CRT pipeline and the core video
// output (gamma + video_mixer). With the analog H Scaler on, ce_pix is held
// high; the output must keep producing sync, DE and gamma-corrected pixels.
module am2r_video_out_tb;
	parameter HSCALE = 0;
	parameter GAMMA_EN = 0;
	parameter integer HSCALE_VALUE = 4;

	reg clk = 0;
	always #20 clk = ~clk;
	reg clk_sys = 0;
	always #10 clk_sys = ~clk_sys;

	reg reset = 1;

	wire ce_pix, hblank, hsync, vblank, vsync, new_frame;
	wire [7:0] source_r, source_g, source_b;

	am2r_native_video source(
		.clk(clk),
		.reset(reset),
		.standard(2'd0),
		.frame_ready(1'b1),
		.frame_r(8'h40),
		.frame_g(8'h80),
		.frame_b(8'hc0),
		.ce_pix(ce_pix),
		.hblank(hblank),
		.hsync(hsync),
		.vblank(vblank),
		.vsync(vsync),
		.new_frame(new_frame),
		.new_line(),
		.pace_tick(),
		.pal(),
		.r(source_r),
		.g(source_g),
		.b(source_b)
	);

	wire crt_ce, crt_hs, crt_hb, crt_vs, crt_vb;
	wire [7:0] crt_r, crt_g, crt_b;

	am2r_crt_video crt(
		.clk(clk),
		.reset(reset),
		.ce_pix_in(ce_pix),
		.r_in(source_r),
		.g_in(source_g),
		.b_in(source_b),
		.hs_in(hsync),
		.hblank_in(hblank),
		.vs_in(vsync),
		.vblank_in(vblank),
		.h_position(5'sd0),
		.v_position(5'sd0),
		.hscale_enable(HSCALE != 0),
		.hscale(HSCALE_VALUE[4:0]),
		.ce_pix_out(crt_ce),
		.r_out(crt_r),
		.g_out(crt_g),
		.b_out(crt_b),
		.hs_out(crt_hs),
		.hblank_out(crt_hb),
		.vs_out(crt_vs),
		.vblank_out(crt_vb),
		.hscale_active()
	);

	// hps_io drives bits [20:0]; the gamma stage drives bit 21.
	reg gamma_wr = 0;
	reg [9:0] gamma_wr_addr = 0;
	reg [7:0] gamma_value = 0;
	wire [21:0] gamma_bus;
	assign gamma_bus[20:0] = {clk_sys, GAMMA_EN != 0, gamma_wr, gamma_wr_addr, gamma_value};

	wire out_ce, out_hs, out_vs, out_de;
	wire [7:0] out_r, out_g, out_b;

	am2r_video_out dut(
		.clk(clk),
		.ce_pix(crt_ce),
		.scandoubler(1'b0),
		.hq2x(1'b0),
		.freeze(1'b0),
		.gamma_bus(gamma_bus),
		.r(crt_r),
		.g(crt_g),
		.b(crt_b),
		.hsync(crt_hs),
		.vsync(crt_vs),
		.hblank(crt_hb),
		.vblank(crt_vb),
		.ce_pix_out(out_ce),
		.vga_r(out_r),
		.vga_g(out_g),
		.vga_b(out_b),
		.vga_hs(out_hs),
		.vga_vs(out_vs),
		.vga_de(out_de)
	);

	task wait_frames(input integer count);
		integer seen;
		begin
			seen = 0;
			while (seen < count) begin
				@(posedge clk);
				#1;
				if (new_frame) seen = seen + 1;
			end
		end
	endtask

	integer i;
	integer hs_edges, vs_edges, de_edges, de_pixels, good_pixels;
	reg prev_hs, prev_vs, prev_de;
	reg [7:0] expect_r, expect_g, expect_b;

	initial begin
		if (HSCALE_VALUE < -16 || HSCALE_VALUE > 15)
			$fatal(1, "H Scale setting is outside the signed five-bit menu range");
		// Inverting curve: output = 255 - input on every channel.
		for (i = 0; i < 768; i = i + 1) begin
			@(posedge clk_sys);
			gamma_wr <= 1;
			gamma_wr_addr <= i[9:0];
			gamma_value <= 8'hff - i[7:0];
		end
		@(posedge clk_sys);
		gamma_wr <= 0;

		repeat (8) @(posedge clk);
		reset = 0;
		wait_frames(3);

		expect_r = GAMMA_EN ? 8'hbf : 8'h40;
		expect_g = GAMMA_EN ? 8'h7f : 8'h80;
		expect_b = GAMMA_EN ? 8'h3f : 8'hc0;
		hs_edges = 0; vs_edges = 0; de_edges = 0; de_pixels = 0; good_pixels = 0;
		prev_hs = out_hs; prev_vs = out_vs; prev_de = out_de;
		while (vs_edges < 3) begin
			@(posedge clk);
			#1;
			if (out_hs && !prev_hs) hs_edges = hs_edges + 1;
			if (out_vs && !prev_vs) vs_edges = vs_edges + 1;
			if (out_de && !prev_de) de_edges = de_edges + 1;
			if (out_ce && out_de) begin
				de_pixels = de_pixels + 1;
				if (out_r == expect_r && out_g == expect_g && out_b == expect_b)
					good_pixels = good_pixels + 1;
			end
			prev_hs = out_hs; prev_vs = out_vs; prev_de = out_de;
			if (hs_edges > 2000) $fatal(1, "VSync stopped: %0d lines without three VSyncs", hs_edges);
		end

		// Counting starts mid-frame, so two to three frames elapse before the
		// third VSync.
		if (hs_edges < 2 * 262 - 2 || hs_edges > 3 * 262)
			$fatal(1, "HSync count %0d over two frames", hs_edges);
		if (de_edges < 2 * 240 - 2)
			$fatal(1, "DE produced only %0d active lines", de_edges);
		if (HSCALE == 0 && (de_pixels < 2 * 240 * 318 || de_pixels > 3 * 240 * 320))
			$fatal(1, "Unexpected active pixel count %0d", de_pixels);
		if (HSCALE != 0 && de_pixels < 2 * 240 * 320 * 3)
			$fatal(1, "H Scaler output has only %0d active pixels", de_pixels);
		if (good_pixels * 10 < de_pixels * 9)
			$fatal(1, "Only %0d of %0d active pixels carry the expected colour", good_pixels, de_pixels);

		$display("PASS am2r_video_out_tb HSCALE=%0d GAMMA_EN=%0d HSCALE_VALUE=%0d lines=%0d active=%0d pixels=%0d (scandoubler disabled)",
			HSCALE, GAMMA_EN, HSCALE_VALUE, hs_edges, de_edges, de_pixels);
		$finish;
	end

	initial begin
		#400_000_000;
		$fatal(1, "Timeout");
	end
endmodule
