`timescale 1ns/1ps

module am2r_crt_video_tb;
	reg clk = 0;
	always #20 clk = ~clk;

	reg reset = 1;
	reg signed [4:0] h_position = 0;
	reg signed [4:0] v_position = 0;
	reg hscale_enable = 0;
	reg signed [4:0] hscale = 0;

	wire ce_pix;
	wire hblank;
	wire hsync;
	wire vblank;
	wire vsync;
	wire new_frame;
	wire new_line;
	wire [7:0] source_r;
	wire [7:0] source_g;
	wire [7:0] source_b;

	am2r_native_video source(
		.clk(clk),
		.reset(reset),
		.diagnostic(1'b1),
		.pattern(2'd1),
		.frame_ready(1'b0),
		.frame_r(8'd0),
		.frame_g(8'd0),
		.frame_b(8'd0),
		.ce_pix(ce_pix),
		.hblank(hblank),
		.hsync(hsync),
		.vblank(vblank),
		.vsync(vsync),
		.new_frame(new_frame),
		.new_line(new_line),
		.r(source_r),
		.g(source_g),
		.b(source_b)
	);

	wire output_ce;
	wire [7:0] output_r;
	wire [7:0] output_g;
	wire [7:0] output_b;
	wire output_hs;
	wire output_hblank;
	wire output_vs;
	wire output_vblank;
	wire hscale_active;

	am2r_crt_video dut(
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
		.h_position(h_position),
		.v_position(v_position),
		.hscale_enable(hscale_enable),
		.hscale(hscale),
		.ce_pix_out(output_ce),
		.r_out(output_r),
		.g_out(output_g),
		.b_out(output_b),
		.hs_out(output_hs),
		.hblank_out(output_hblank),
		.vs_out(output_vs),
		.vblank_out(output_vblank),
		.hscale_active(hscale_active)
	);

	task wait_frames(input integer count);
		integer seen;
		begin
			seen = 0;
			while (seen < count) begin
				@(posedge clk);
				#1;
				if (new_frame)
					seen = seen + 1;
			end
		end
	endtask

	task next_output_hsync(input integer skip, output integer pixel);
		integer edges;
		reg previous;
		begin
			edges = 0;
			previous = output_hs;
			while (edges <= skip) begin
				@(posedge clk);
				#1;
				if (ce_pix) begin
					if (output_hs && !previous) begin
						pixel = source.h_count;
						edges = edges + 1;
					end
					previous = output_hs;
				end
			end
		end
	endtask

	task next_output_vsync(output integer line);
		reg previous;
		begin
			previous = output_vs;
			forever begin
				@(posedge clk);
				#1;
				if (ce_pix) begin
					if (output_vs && !previous) begin
						line = source.v_count;
						return;
					end
					previous = output_vs;
				end
			end
		end
	endtask

	integer baseline_hsync;
	integer shifted_hsync;
	integer baseline_vsync;
	integer shifted_vsync;
	integer active_clocks;
	integer period_clocks;
	integer active_lines_checked;
	integer line_periods_checked;
	integer expected_active_clocks;
	reg previous_hblank;
	reg have_active_start;
	reg have_rise;
	reg count_scaled_lines;

	always @(posedge clk) begin
		#1;
		if (!hscale_active) begin
			if (output_ce !== ce_pix)
				$fatal(1, "bypass CE mismatch");
			if ({output_r, output_g, output_b} !== {source_r, source_g, source_b})
				$fatal(1, "bypass RGB mismatch");
			if (output_hblank !== hblank || output_vblank !== vblank)
				$fatal(1, "bypass blanking mismatch");
			if (!dut.position_active &&
				(output_hs !== hsync || output_vs !== vsync))
				$fatal(1, "zero-adjustment sync bypass mismatch");
		end else begin
			if (output_ce !== 1'b1)
				$fatal(1, "scaled output must advance every video clock");
			if (count_scaled_lines) begin
				period_clocks = period_clocks + 1;
				if (!output_hblank)
					active_clocks = active_clocks + 1;

				if (previous_hblank && !output_hblank) begin
					active_clocks = 1;
					have_active_start = 1;
				end
				if (!previous_hblank && output_hblank && have_active_start) begin
					if (!output_vblank) begin
						if (active_clocks != expected_active_clocks)
							$fatal(1, "scaled active width was %0d clocks, expected %0d",
								active_clocks, expected_active_clocks);
						active_lines_checked = active_lines_checked + 1;
					end
					have_active_start = 0;
					if (have_rise) begin
						if (period_clocks != 1592)
							$fatal(1, "scaled line period was %0d clocks, expected 1592", period_clocks);
						line_periods_checked = line_periods_checked + 1;
					end
					period_clocks = 0;
					have_rise = 1;
				end
			end
		end
		previous_hblank = output_hblank;
	end

	task check_scale(input signed [4:0] selected_scale, input integer expected_width);
		begin
			count_scaled_lines = 0;
			hscale = selected_scale;
			// The setting latches at vblank; skip a complete frame boundary so
			// no line from the previous geometry enters the measurement.
			wait_frames(2);
			wait (output_vblank);
			wait (!output_vblank);
			expected_active_clocks = expected_width;
			active_clocks = 0;
			period_clocks = 0;
			active_lines_checked = 0;
			line_periods_checked = 0;
			previous_hblank = output_hblank;
			have_active_start = 0;
			have_rise = 0;
			count_scaled_lines = 1;
			wait (active_lines_checked >= 8 && line_periods_checked >= 8);
		end
	endtask

	initial begin
		active_clocks = 0;
		period_clocks = 0;
		active_lines_checked = 0;
		line_periods_checked = 0;
		expected_active_clocks = 0;
		previous_hblank = 1;
		have_active_start = 0;
		have_rise = 0;
		count_scaled_lines = 0;

		repeat (8) @(posedge clk);
		reset = 0;

		// Allow both JTFrame field-history slots to learn the native sync.
		wait_frames(4);
		next_output_hsync(3, baseline_hsync);
		next_output_vsync(baseline_vsync);

		h_position = 5'sd3;
		v_position = -5'sd2;
		wait_frames(4);
		next_output_hsync(3, shifted_hsync);
		next_output_vsync(shifted_vsync);
		if (shifted_hsync != ((baseline_hsync + 398 - 3) % 398))
			$fatal(1, "H position shift was %0d -> %0d, expected three pixels earlier",
				baseline_hsync, shifted_hsync);
		if (shifted_vsync != ((baseline_vsync + 262 + 2) % 262))
			$fatal(1, "V position shift was %0d -> %0d, expected two lines later",
				baseline_vsync, shifted_vsync);

		// Check representative range limits and the intended slight shrink.
		// At four clocks/source pixel the widths are scale/64 of 1280 clocks.
		h_position = 0;
		v_position = 0;
		hscale_enable = 1;
		wait (hscale_active);
		check_scale(-5'sd16, 960);
		check_scale(-5'sd3, 1220);
		check_scale(5'sd15, 1580);

		if (dut.horizontal_scale.debug_underrun)
			$fatal(1, "horizontal scaler line-buffer underrun");
		if (dut.horizontal_scale.debug_overflow)
			$fatal(1, "horizontal scaler line-buffer overflow");

		$display("PASS: CRT positioning and horizontal-scale range checks");
		$finish;
	end

endmodule
