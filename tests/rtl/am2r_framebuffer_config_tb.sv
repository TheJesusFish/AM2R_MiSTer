`timescale 1ns/1ps

module am2r_framebuffer_config_tb;
	wire fb_en;
	wire [4:0] fb_format;
	wire [11:0] fb_width;
	wire [11:0] fb_height;
	wire [31:0] fb_base;
	wire [13:0] fb_stride;
	wire fb_force_blank;

	am2r_framebuffer_config dut
	(
		.fb_en(fb_en),
		.fb_format(fb_format),
		.fb_width(fb_width),
		.fb_height(fb_height),
		.fb_base(fb_base),
		.fb_stride(fb_stride),
		.fb_force_blank(fb_force_blank)
	);

	initial begin
		#1;
		if (fb_en || fb_format !== 5'b10110 || fb_width !== 320 ||
			fb_height !== 240 || fb_base !== 32'h22001000 ||
			fb_stride !== 1280 || fb_force_blank)
			$fatal(1, "Framebuffer contract mismatch");
		$display("PASS: HPS buffer metadata retained while FPGA owns scanout");
		$finish;
	end
endmodule
