`timescale 1ns/1ps

module am2r_video_test_tb;

reg clk = 0;
reg reset = 1;
reg scandouble = 0;
reg [1:0] pattern = 0;

wire ce_pix;
wire hvcnt_atzero;
wire hblank;
wire hsync;
wire vblank;
wire vsync;
wire [7:0] r;
wire [7:0] g;
wire [7:0] b;

am2r_video_test dut
(
	.clk(clk),
	.reset(reset),
	.scandouble(scandouble),
	.pattern(pattern),
	.ce_pix(ce_pix),
	.hvcnt_atzero(hvcnt_atzero),
	.hblank(hblank),
	.hsync(hsync),
	.vblank(vblank),
	.vsync(vsync),
	.r(r),
	.g(g),
	.b(b)
);

always #25 clk = ~clk; // 20 MHz PLL output

integer pixel_steps = 0;
integer frame_wraps = 0;
integer aligned = 0;
reg [9:0] previous_h;
reg [9:0] previous_v;

initial begin
	repeat(4) @(posedge clk);
	reset = 0;
	previous_h = dut.h_count;
	previous_v = dut.v_count;

	while(frame_wraps < 1) begin
		@(posedge clk);
		#1;

		if((dut.h_count != previous_h) || (dut.v_count != previous_v)) begin
			if(aligned) pixel_steps = pixel_steps + 1;

			if(hblank !== (dut.h_count >= 530))
				$fatal(1, "HBlank mismatch at h=%0d", dut.h_count);
			if(hsync !== ((dut.h_count >= 544) && (dut.h_count < 590)))
				$fatal(1, "HSync mismatch at h=%0d", dut.h_count);
			if(vblank !== (dut.v_count >= 240))
				$fatal(1, "VBlank mismatch at v=%0d", dut.v_count);
			if(vsync !== ((dut.v_count >= 245) && (dut.v_count < 248)))
				$fatal(1, "VSync mismatch at v=%0d", dut.v_count);
			if((previous_h == 637) && (previous_v == 261)) begin
				if((dut.h_count != 0) || (dut.v_count != 0))
					$fatal(1, "Frame counters did not wrap to zero");
				if(aligned) frame_wraps = frame_wraps + 1;
				else begin
					aligned = 1;
					pixel_steps = 0;
				end
			end

			previous_h = dut.h_count;
			previous_v = dut.v_count;
		end
	end

	if(pixel_steps != (638 * 262))
		$fatal(1, "Expected %0d pixel steps, observed %0d", 638 * 262, pixel_steps);

	$display("PASS: 638x262 raster, 530x240 active, 46-sample HSync, 3-line VSync");
	$finish;
end

endmodule
