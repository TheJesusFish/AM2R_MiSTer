// Dedicated AM2R framework video clock.
// Generated-style wrapper kept separate from the renderer PLL so Quartus and
// the MiSTer clock-switch network treat CLK_VIDEO as a proper PLL clock.
module pll_video (
	input  wire refclk,
	input  wire rst,
	output wire outclk_0,
	output wire locked
);
	pll_video_0002 pll_video_inst (
		.refclk(refclk),
		.rst(rst),
		.outclk_0(outclk_0),
		.locked(locked)
	);
endmodule
