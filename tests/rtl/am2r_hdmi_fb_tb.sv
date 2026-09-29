`timescale 1ns/1ps

module am2r_hdmi_fb_tb;
	reg clk = 0;
	reg reset = 1;
	reg fb_vbl = 0;
	reg [31:0] native_frame = 0;
	reg [1:0] native_buffer = 0;
	wire [31:0] fb_base;
	wire fb_force_blank;
	wire [3:0] protect;
	integer errors = 0;

	am2r_hdmi_fb dut(
		.clk(clk), .reset(reset), .fb_vbl(fb_vbl),
		.native_frame(native_frame), .native_buffer(native_buffer),
		.fb_base(fb_base), .fb_force_blank(fb_force_blank), .protect(protect)
	);

	always #5 clk = ~clk;

	task hdmi_vblank_start; begin fb_vbl = 1; repeat (8) @(posedge clk); end endtask
	task hdmi_vblank_end; begin fb_vbl = 0; repeat (8) @(posedge clk); end endtask

	task expect_state(input [31:0] base, input blank, input [3:0] held, input [255:0] what);
	begin
		if (fb_base !== base || fb_force_blank !== blank || protect !== held) begin
			$display("%0s: base=%h blank=%0d protect=%b, expected base=%h blank=%0d protect=%b",
			         what, fb_base, fb_force_blank, protect, base, blank, held);
			errors = errors + 1;
		end
	end
	endtask

	initial begin
		repeat (4) @(posedge clk);
		reset = 0;
		repeat (4) @(posedge clk);

		// Nothing published: HDMI stays blank and nothing is protected.
		hdmi_vblank_start();
		hdmi_vblank_end();
		expect_state(32'h3a000100, 1, 4'b0000, "before first frame");

		// First publication in buffer 2 is selected at the next vblank and
		// shown once ascal has latched it at the end of that vblank.
		native_frame = 1; native_buffer = 2;
		repeat (4) @(posedge clk);
		expect_state(32'h3a000100, 1, 4'b0000, "publication between vblanks");
		hdmi_vblank_start();
		expect_state(32'h3a096100, 1, 4'b0100, "first selection in vblank");
		hdmi_vblank_end();
		expect_state(32'h3a096100, 0, 4'b0100, "first frame shown");

		// A newer frame in buffer 3: during vblank both the old and new
		// selections are protected; afterwards only the new one.
		native_frame = 2; native_buffer = 3;
		hdmi_vblank_start();
		expect_state(32'h3a0e1100, 0, 4'b1100, "change of frame in vblank");
		hdmi_vblank_end();
		expect_state(32'h3a0e1100, 0, 4'b1000, "new frame active");

		// No publication: the same buffer stays selected.
		hdmi_vblank_start();
		expect_state(32'h3a0e1100, 0, 4'b1000, "repeated frame in vblank");
		hdmi_vblank_end();

		// Buffers 0 and 1 map to their bases.
		native_frame = 3; native_buffer = 0;
		hdmi_vblank_start(); hdmi_vblank_end();
		expect_state(32'h3a000100, 0, 4'b0001, "buffer 0");
		native_frame = 4; native_buffer = 1;
		hdmi_vblank_start(); hdmi_vblank_end();
		expect_state(32'h3a04b100, 0, 4'b0010, "buffer 1");

		if (errors == 0) $display("PASS: HDMI framebuffer selects published frames at vblank and protects held buffers");
		else $fatal(1, "FAIL: %0d HDMI framebuffer errors", errors);
		$finish;
	end
endmodule
