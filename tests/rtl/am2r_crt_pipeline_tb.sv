`timescale 1ns/1ps

module am2r_crt_pipeline_tb;
	reg clk = 0;
	always #10 clk = ~clk;

	reg reset = 1;
	reg active = 0;
	reg signed [4:0] hsize = 0;
	reg signed [8:0] hposition = 0;
	reg signed [5:0] vshift = 0;
	reg signed [5:0] vsize = 0;
	reg cabinet_mode = 1;

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
		.pattern(2'd2),
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
	wire output_de;

	am2r_crt_pipeline dut(
		.clk(clk),
		.reset(reset),
		.active(active),
		.hsize(hsize),
		.hposition(hposition),
		.vshift(vshift),
		.vsize(vsize),
		.cabinet_mode(cabinet_mode),
		.ce_pix_in(ce_pix),
		.r_in(source_r),
		.g_in(source_g),
		.b_in(source_b),
		.hs_in(hsync),
		.hblank_in(hblank),
		.vs_in(vsync),
		.vblank_in(vblank),
		.ce_pix_out(output_ce),
		.r_out(output_r),
		.g_out(output_g),
		.b_out(output_b),
		.hs_out(output_hs),
		.de_out(output_de),
		.hblank_out(output_hblank),
		.vs_out(output_vs),
		.vblank_out(output_vblank)
	);

	always @(posedge clk) begin
		#1;
		if (!reset && !active) begin
			if (output_ce !== ce_pix)
				$fatal(1, "disabled CRT pipeline changed CE");
			if ({output_r, output_g, output_b} !== {source_r, source_g, source_b})
				$fatal(1, "disabled CRT pipeline changed RGB");
			if ({output_hs, output_hblank, output_vs, output_vblank} !==
				{hsync, hblank, vsync, vblank})
				$fatal(1, "disabled CRT pipeline changed native timing");
			if (output_de !== ~(hblank | vblank))
				$fatal(1, "disabled CRT pipeline changed native DE");
		end
	end

	task wait_source_frames(input integer count);
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

	task wait_source_lines(input integer count);
		integer seen;
		begin
			seen = 0;
			while (seen < count) begin
				@(posedge clk);
				#1;
				if (new_line)
					seen = seen + 1;
			end
		end
	endtask

	task measure_active_clocks(output integer clocks);
		reg previous;
		begin
			previous = output_de;
			forever begin
				@(posedge clk);
				#1;
				if (output_de && !previous) begin
					clocks = 1;
					previous = output_de;
					while (output_de) begin
						@(posedge clk);
						#1;
						if (output_de)
							clocks = clocks + 1;
					end
					// A mode/frame boundary can expose a short pulse or a
					// multi-line warm-up window while the upstream buffers align.
					// Measure the next ordinary active row.
					if (clocks >= 1000 && clocks <= 3500)
						return;
				end
				previous = output_de;
			end
		end
	endtask

	task measure_active_pixel_sequence(
		output integer samples,
		output integer discontinuities
	);
		reg previous_de;
		reg [7:0] previous_r;
		reg have_previous;
		begin
			samples = 0;
			discontinuities = 0;
			have_previous = 0;
			previous_de = output_de;
			forever begin
				@(posedge clk);
				#1;
				if (output_ce && dut.adjusted_active) begin
					if (have_previous && output_r !== (previous_r + 8'd1))
						discontinuities = discontinuities + 1;
					previous_r = output_r;
					have_previous = 1;
					samples = samples + 1;
				end
				if (previous_de && !output_de && samples != 0)
					return;
				previous_de = output_de;
			end
		end
	endtask

	task next_hsync_position(output integer pixel);
		reg previous;
		begin
			previous = output_hs;
			forever begin
				@(posedge clk);
				#1;
				if (output_hs && !previous) begin
					pixel = source.h_count;
					return;
				end
				previous = output_hs;
			end
		end
	endtask

	task next_vsync_position(output integer line);
		reg previous;
		begin
			previous = output_vs;
			forever begin
				@(posedge clk);
				#1;
				if (output_vs && !previous) begin
					line = source.v_count;
					return;
				end
				previous = output_vs;
			end
		end
	endtask

	task measure_output_frame(
		output integer line_count,
		output integer active_line_count,
		output integer frame_clocks,
		output integer first_line_clocks
	);
		reg previous_hs;
		reg previous_vs;
		reg previous_de;
		integer line_clocks;
		integer hsyncs_seen;
		begin
			previous_vs = output_vs;
			while (1) begin
				@(posedge clk);
				#1;
				if (output_vs && !previous_vs)
					break;
				previous_vs = output_vs;
			end

			line_count = 0;
			active_line_count = 0;
			frame_clocks = 0;
			first_line_clocks = 0;
			line_clocks = 0;
			hsyncs_seen = 0;
			previous_hs = output_hs;
			previous_vs = output_vs;
			previous_de = output_de;
			forever begin
				@(posedge clk);
				#1;
				frame_clocks = frame_clocks + 1;
				line_clocks = line_clocks + 1;
				if (output_hs && !previous_hs) begin
					line_count = line_count + 1;
					hsyncs_seen = hsyncs_seen + 1;
					if (hsyncs_seen == 2)
						first_line_clocks = line_clocks;
					line_clocks = 0;
				end
				if (output_de && !previous_de)
					active_line_count = active_line_count + 1;
				if (output_vs && !previous_vs)
					return;
				previous_hs = output_hs;
				previous_vs = output_vs;
				previous_de = output_de;
			end
		end
	endtask

	integer width_native;
	integer width_shrunk;
	integer width_widened;
	integer hsync_native;
	integer hsync_shifted;
	integer vsync_native;
	integer vsync_shifted;
	integer output_lines;
	integer output_active_lines;
	integer output_frame_clocks;
	integer output_line_clocks;
	integer output_pixel_samples;
	integer output_pixel_discontinuities;

	initial begin
		repeat (16) @(posedge clk);
		reset = 0;

		// Off is a direct, zero-latency bypass. The assertion above checks every
		// clock while a complete native frame passes.
		wait_source_frames(1);

		active = 1;
		wait_source_frames(2);
		measure_active_clocks(width_native);
		if (width_native < 2558 || width_native > 2562)
			$fatal(1, "zero H-size active width was %0d clocks, expected 2560", width_native);

		// At eight clocks/pixel, each signed H-size step is one quarter-clock.
		hsize = -5'sd8;
		wait_source_lines(8);
		measure_active_clocks(width_shrunk);
		if (width_shrunk < 1919 || width_shrunk > 1923)
			$fatal(1, "-8 H-size width was %0d clocks, expected about 1921", width_shrunk);

		hsize = 5'sd8;
		wait_source_lines(8);
		measure_active_clocks(width_widened);
		// Enlargement is porch-limited on this raster. It must still be wider
		// than native without consuming the complete 398-pixel line.
		if (width_widened <= width_native || width_widened >= 3184)
			$fatal(1, "+8 H-size width was %0d clocks, outside physical bounds", width_widened);
		measure_active_pixel_sequence(output_pixel_samples, output_pixel_discontinuities);
		if (output_pixel_discontinuities > 2)
			$fatal(1, "+8 H-size reordered the active source stream (%0d discontinuities in %0d samples)",
				output_pixel_discontinuities, output_pixel_samples);

		hsize = 0;
		hposition = 0;
		wait_source_lines(8);
		next_hsync_position(hsync_native);
		hposition = 9'sd8;
		wait_source_lines(8);
		next_hsync_position(hsync_shifted);
		if (hsync_shifted != ((hsync_native + 8) % 398))
			$fatal(1, "H-position moved HSync %0d -> %0d, expected +8 pixels",
				hsync_native, hsync_shifted);

		hposition = 0;
		vshift = 0;
		wait_source_frames(2);
		next_vsync_position(vsync_native);
		vshift = 6'sd3;
		wait_source_frames(2);
		next_vsync_position(vsync_shifted);
		if (vsync_shifted != ((vsync_native + 3) % 262))
			$fatal(1, "V-shift moved VSync %0d -> %0d, expected +3 lines",
				vsync_native, vsync_shifted);

		// Cabinet mode must retain native line/frame cadence while adding three
		// displayed rows for a taller image.
		vshift = 0;
		cabinet_mode = 1;
		vsize = -6'sd3;
		wait_source_frames(6);
		measure_output_frame(output_lines, output_active_lines,
			output_frame_clocks, output_line_clocks);
		if (output_frame_clocks < 834206 || output_frame_clocks > 834210)
			$fatal(1, "Cabinet frame period changed to %0d clocks", output_frame_clocks);
		if (output_line_clocks < 3182 || output_line_clocks > 3186)
			$fatal(1, "Cabinet line period changed to %0d clocks", output_line_clocks);
		if (output_active_lines != 243)
			$fatal(1, "Cabinet active height was %0d lines, expected 243", output_active_lines);

		// PVM mode preserves every source row and changes total lines instead.
		cabinet_mode = 0;
		wait_source_frames(3);
		measure_output_frame(output_lines, output_active_lines,
			output_frame_clocks, output_line_clocks);
		if (output_frame_clocks < 834206 || output_frame_clocks > 834210)
			$fatal(1, "PVM frame period changed to %0d clocks", output_frame_clocks);
		if (output_lines != 259)
			$fatal(1, "PVM frame had %0d lines, expected 259", output_lines);

		$display("PASS: CRT-Adjust bypass, H-size, H-position, V-shift, Cabinet and PVM V-size");
		$finish;
	end

endmodule
