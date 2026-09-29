//============================================================================
// AM2R core video output: framework gamma, then video_mixer
//
// The framework gamma_corr inside video_mixer latches a pixel only on a
// rising edge of ce_pix and needs four clocks per pixel. The analog H Scaler
// emits one pixel per video clock with ce_pix held high, so that stage would
// never advance and would freeze both the picture and the sync signals, even
// with gamma disabled. gamma_fast samples on ce_pix itself, so it works for
// both the pulsed native enable and the continuous H Scaler enable. It runs
// here, ahead of a GAMMA=0 video_mixer, keeping the upstream sys/ tree
// untouched.
//
// SPDX-License-Identifier: GPL-3.0-or-later
//============================================================================

module am2r_video_out
#(
	parameter LINE_LENGTH = 320
)
(
	input             clk,
	input             ce_pix,
	input             scandoubler,
	input             hq2x,
	input             freeze,
	inout      [21:0] gamma_bus,

	input       [7:0] r,
	input       [7:0] g,
	input       [7:0] b,
	input             hsync,
	input             vsync,
	input             hblank,
	input             vblank,

	output            ce_pix_out,
	output      [7:0] vga_r,
	output      [7:0] vga_g,
	output      [7:0] vga_b,
	output            vga_hs,
	output            vga_vs,
	output            vga_de
);

	wire [7:0] gamma_r, gamma_g, gamma_b;
	wire gamma_hs, gamma_vs, gamma_hb, gamma_vb;

	gamma_fast gamma
	(
		.clk_vid(clk),
		.ce_pix(ce_pix),
		.gamma_bus(gamma_bus),
		.HSync(hsync),
		.VSync(vsync),
		.HBlank(hblank),
		.VBlank(vblank),
		.DE(~(hblank | vblank)),
		.RGB_in({r, g, b}),
		.HSync_out(gamma_hs),
		.VSync_out(gamma_vs),
		.HBlank_out(gamma_hb),
		.VBlank_out(gamma_vb),
		.DE_out(),
		.RGB_out({gamma_r, gamma_g, gamma_b})
	);

	// With GAMMA=0 the mixer only drives bit 21 of this local bus; the real
	// gamma_bus belongs to gamma_fast above.
	wire [21:0] mixer_gamma_bus;

	video_mixer #(.LINE_LENGTH(LINE_LENGTH), .HALF_DEPTH(0), .GAMMA(0)) mixer
	(
		.CLK_VIDEO(clk),
		.CE_PIXEL(ce_pix_out),
		.ce_pix(ce_pix),
		.scandoubler(scandoubler),
		.hq2x(hq2x),
		.gamma_bus(mixer_gamma_bus),
		.R(gamma_r),
		.G(gamma_g),
		.B(gamma_b),
		.HSync(gamma_hs),
		.VSync(gamma_vs),
		.HBlank(gamma_hb),
		.VBlank(gamma_vb),
		.HDMI_FREEZE(freeze),
		.freeze_sync(),
		.VGA_R(vga_r),
		.VGA_G(vga_g),
		.VGA_B(vga_b),
		.VGA_VS(vga_vs),
		.VGA_HS(vga_hs),
		.VGA_DE(vga_de)
	);

endmodule
