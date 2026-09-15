`timescale 1ns/1ps

module am2r_native_video_tb;
	reg clk = 0;
	reg reset = 1;
	reg frame_ready = 1;
	reg [7:0] frame_r = 8'hff;
	reg [7:0] frame_g = 0;
	reg [7:0] frame_b = 0;
	wire ce_pix, hblank, hsync, vblank, vsync;
	wire new_frame, new_line;
	wire [7:0] r, g, b;
	integer pixels = 0;
	integer lines = 0;
	integer active_pixels = 0;
	integer active_lines = 0;
	integer hsync_pixels = 0;
	integer vsync_lines = 0;
	integer clocks_since_ce = 0;
	integer errors = 0;
	reg seen_ce = 0;

	am2r_native_video dut(
		.clk(clk), .reset(reset),
		.frame_ready(frame_ready), .frame_r(frame_r), .frame_g(frame_g), .frame_b(frame_b),
		.ce_pix(ce_pix), .hblank(hblank), .hsync(hsync), .vblank(vblank),
		.vsync(vsync), .new_frame(new_frame), .new_line(new_line), .r(r), .g(g), .b(b)
	);

	always #5 clk = ~clk;

	always @(posedge clk) begin
		if (reset) begin
			clocks_since_ce <= 0;
		end else begin
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
				if ((pixels % 398) == 0) begin
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
		wait (lines == 262);
		if (pixels != 398 * 262) begin
			$display("Raster size %0d pixels", pixels);
			errors = errors + 1;
		end
		if (active_pixels != 320 * 240) begin
			$display("Active area %0d pixels", active_pixels);
			errors = errors + 1;
		end
		if (active_lines != 240 || hsync_pixels != 30 * 262 || vsync_lines != 3) begin
			$display("Timing mismatch active_lines=%0d hsync_pixels=%0d vsync_lines=%0d",
				active_lines, hsync_pixels, vsync_lines);
			errors = errors + 1;
		end
		if (errors == 0)
			$display("PASS: 320x240 active, 398x262 total, CE /4, 15.704 kHz and 59.94 Hz timing");
		else
			$fatal(1, "FAIL: %0d errors", errors);
		$finish;
	end
endmodule
