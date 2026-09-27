`timescale 1ns/1ps

module am2r_native_video_tb #(parameter integer STANDARD = 0);
	localparam integer PAL = STANDARD >= 2;
	localparam integer H_TOTAL = PAL ? 400 : 398;
	localparam integer V_TOTAL = PAL ? 312 : 262;
	localparam integer V_SYNC_START = PAL ? 270 : 245;
	localparam integer PACE_PERIOD = 4 * 398 * 262;
	reg clk = 0;
	reg reset = 1;
	reg frame_ready = 1;
	reg [7:0] frame_r = 8'hff;
	reg [7:0] frame_g = 0;
	reg [7:0] frame_b = 0;
	wire ce_pix, hblank, hsync, vblank, vsync;
	wire new_frame, new_line, pace_tick, pal;
	wire [7:0] r, g, b;
	integer pixels = 0;
	integer lines = 0;
	integer active_pixels = 0;
	integer active_lines = 0;
	integer hsync_pixels = 0;
	integer vsync_lines = 0;
	integer clocks_since_ce = 0;
	integer errors = 0;
	integer pace_ticks = 0;
	integer pace_line = -1;
	integer clock_count = 0;
	integer last_pace_clock = -1;
	integer vsync_first_line = -1;
	reg seen_ce = 0;

	am2r_native_video dut(
		.clk(clk), .reset(reset), .standard(STANDARD[1:0]),
		.frame_ready(frame_ready), .frame_r(frame_r), .frame_g(frame_g), .frame_b(frame_b),
		.ce_pix(ce_pix), .hblank(hblank), .hsync(hsync), .vblank(vblank),
		.vsync(vsync), .new_frame(new_frame), .new_line(new_line),
		.pace_tick(pace_tick), .pal(pal), .r(r), .g(g), .b(b)
	);

	always #5 clk = ~clk;

	always @(posedge clk) begin
		if (reset) begin
			clocks_since_ce <= 0;
		end else begin
			clock_count = clock_count + 1;
			if (pace_tick) begin
				pace_ticks = pace_ticks + 1;
				pace_line = lines;
				if (PAL && last_pace_clock >= 0 &&
				    clock_count - last_pace_clock != PACE_PERIOD) begin
					$display("PAL pacing period was %0d clocks, expected %0d",
					         clock_count - last_pace_clock, PACE_PERIOD);
					errors = errors + 1;
				end
				last_pace_clock = clock_count;
			end
			if (!PAL && new_frame && lines - pace_line != 48) begin
				$display("Pacing edge was %0d lines before vblank, expected 48",
				         lines - pace_line);
				errors = errors + 1;
			end
			clocks_since_ce <= clocks_since_ce + 1;
			if (ce_pix) begin
				if (seen_ce && clocks_since_ce != 3) begin
					$display("CE spacing was %0d clocks, expected 4", clocks_since_ce + 1);
					errors = errors + 1;
				end
				seen_ce <= 1;
				clocks_since_ce <= 0;
				pixels = pixels + 1;
				if (!hblank && !vblank) active_pixels = active_pixels + 1;
				if (hsync) hsync_pixels = hsync_pixels + 1;
				if (vsync && vsync_first_line < 0) vsync_first_line = lines;
				if ((pixels % H_TOTAL) == 0) begin
					lines = lines + 1;
					if (!vblank) active_lines = active_lines + 1;
					if (vsync) vsync_lines = vsync_lines + 1;
				end
			end
		end
	end

	initial begin
		repeat (4) @(posedge clk);
		reset <= 0;
		wait (lines == V_TOTAL);
		if (pal != PAL) begin
			$display("pal output %0d, expected %0d", pal, PAL);
			errors = errors + 1;
		end
		if (vsync_first_line != V_SYNC_START) begin
			$display("VSync started on line %0d, expected %0d", vsync_first_line, V_SYNC_START);
			errors = errors + 1;
		end
		if (pixels != H_TOTAL * V_TOTAL) begin
			$display("Raster size %0d pixels", pixels);
			errors = errors + 1;
		end
		if (active_pixels != 320 * 240) begin
			$display("Active area %0d pixels", active_pixels);
			errors = errors + 1;
		end
		if (active_lines != 240 || hsync_pixels != 30 * V_TOTAL || vsync_lines != 3) begin
			$display("Timing mismatch active_lines=%0d hsync_pixels=%0d vsync_lines=%0d",
				active_lines, hsync_pixels, vsync_lines);
			errors = errors + 1;
		end
		if (!PAL && pace_ticks != 1) begin
			$display("Pacing pulse count was %0d, expected 1", pace_ticks);
			errors = errors + 1;
		end
		if (PAL) begin
			// Six 50 Hz rasters span 7.18 heartbeat periods.
			wait (lines == 6 * V_TOTAL);
			if (pace_ticks < 7 || pace_ticks > 8) begin
				$display("PAL heartbeat count %0d over six rasters", pace_ticks);
				errors = errors + 1;
			end
		end
		if (errors == 0)
			$display("PASS: standard %0d, 320x240 active, %0dx%0d total, CE /4, heartbeat %0d",
			         STANDARD, H_TOTAL, V_TOTAL, pace_ticks);
		else
			$fatal(1, "FAIL: %0d errors", errors);
		$finish;
	end
endmodule
