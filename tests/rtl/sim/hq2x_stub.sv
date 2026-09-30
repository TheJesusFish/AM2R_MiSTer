// Simulation-only stand-in for sys/hq2x.sv, whose Quartus-specific constructs
// open-source simulators reject. Testbenches that keep the scandoubler off
// never read its output.
module Hq2x #(parameter LENGTH = 320, parameter HALF_DEPTH = 0)
(
	input             clk,
	input             ce_in,
	input  [(HALF_DEPTH ? 11 : 23):0] inputpixel,
	input             mono,
	input             disable_hq2x,
	input             reset_frame,
	input             reset_line,
	input             ce_out,
	input       [1:0] read_y,
	input             hblank,
	output [(HALF_DEPTH ? 11 : 23):0] outpixel
);
	assign outpixel = '0;
endmodule
