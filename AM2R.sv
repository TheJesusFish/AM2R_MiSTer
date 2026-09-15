//============================================================================
// AM2R hybrid MiSTer core -- ARM framebuffer shell
//
// Copyright (C) 2026 AM2R MiSTer contributors
// SPDX-License-Identifier: GPL-2.0-or-later
//============================================================================

module emu
(
	`include "sys/emu_ports.vh"
);

// Safe defaults for interfaces not used during the framework/video bring-up.
assign ADC_BUS  = 'Z;
assign USER_OUT = '1;
assign {UART_RTS, UART_TXD, UART_DTR} = 0;
assign {SD_SCK, SD_MOSI, SD_CS} = 'Z;
assign SDRAM_CLK  = 0;
assign SDRAM_CKE  = 0;
assign SDRAM_A    = 0;
assign SDRAM_BA   = 0;
assign SDRAM_DQ   = 'Z;
assign SDRAM_DQML = 1;
assign SDRAM_DQMH = 1;
assign SDRAM_nCS  = 1;
assign SDRAM_nWE  = 1;
assign SDRAM_nRAS = 1;
assign SDRAM_nCAS = 1;

assign VGA_SL         = 0;
assign VGA_F1         = 0;
// Keep analog output on the core-owned native 240p raster. VGA_SCALER would
// route the HDMI/ascal result to VGA and can therefore turn CRT output into
// a 31 kHz signal.
assign VGA_SCALER     = 0;
assign VGA_DISABLE    = 0;
assign HDMI_FREEZE    = 0;
assign HDMI_BLACKOUT  = 0;
assign HDMI_BOB_DEINT = 0;

assign AUDIO_S   = 0;
assign AUDIO_L   = 0;
assign AUDIO_R   = 0;
assign AUDIO_MIX = 0;

assign LED_DISK  = 0;
assign LED_POWER = 0;
assign BUTTONS   = 0;

wire [1:0] ar = status[5:4];
assign VIDEO_ARX = (!ar) ? 12'd4 : (ar - 1'd1);
assign VIDEO_ARY = (!ar) ? 12'd3 : 12'd0;

`include "build_id.v"
localparam CONF_STR = {
	"AM2R;;",
	"-;",
	"O[5:4],Aspect ratio,Original,Full Screen,[ARC1],[ARC2];",
	"O[1],Video source,ARM Framebuffer,Diagnostic;",
	"O[3:2],Diagnostic pattern,Color Bars,Grid,Gradient,Black;",
	"P1,CRT Adjust;",
	"P1O[101],CRT Adjust,Off,On;",
	"H1P1O[100:96],CRT H-Size,0,+1,+2,+3,+4,+5,+6,+7,+8,+9,+10,+11,+12,+13,+14,+15,-16,-15,-14,-13,-12,-11,-10,-9,-8,-7,-6,-5,-4,-3,-2,-1;",
	"H1P1O[85:79],CRT H-Position,0,+1,+2,+3,+4,+5,+6,+7,+8,+9,+10,+11,+12,+13,+14,+15,+16,+17,+18,+19,+20,+21,+22,+23,+24,+25,+26,+27,+28,+29,+30,+31,+32,+33,+34,+35,+36,+37,+38,+39,+40,+41,+42,+43,+44,+45,+46,+47,+48,-48,-47,-46,-45,-44,-43,-42,-41,-40,-39,-38,-37,-36,-35,-34,-33,-32,-31,-30,-29,-28,-27,-26,-25,-24,-23,-22,-21,-20,-19,-18,-17,-16,-15,-14,-13,-12,-11,-10,-9,-8,-7,-6,-5,-4,-3,-2,-1;",
	"H1P1O[78:74],CRT V-Shift,0,+1,+2,+3,+4,+5,+6,+7,+8,+9,+10,+11,+12,+13,+14,+15,-16,-15,-14,-13,-12,-11,-10,-9,-8,-7,-6,-5,-4,-3,-2,-1;",
	"H1P1O[107:104],CRT V-Size,0,+1,+2,+3,+4,+5,+6,+7,-7,-6,-5,-4,-3,-2,-1;",
	// Cabinet mode is the safe default for consumer CRTs because it retains
	// native sync. PVM mode preserves exact source lines but retimes HSync.
	"H1P1O[108],CRT V-Size Mode,Cabinet,PVM;",
	"-;",
	"O[7:6],Savestate slot,1,2,3,4;",
	"T[8],Save state;",
	"T[9],Load state;",
	"-;",
	"R[0],Reset;",
	// Keep the mapper in gameplay-action order. jn supplies the requested
	// MiSTer defaults for four face buttons, two shoulders, Select, and Start;
	// Morph and the bindable save-state action are deliberately unbound.
	"J1,Fire,Jump,Missiles,Walk,Aim Up,Aim Down,Weapon Select,Start,Morph,Save State;",
	"jn,X,A,Y,B,R,L,Select,Start,,,;",
	"v,0;",
	"V,v",`BUILD_DATE
};

wire         forced_scandoubler;
wire   [1:0] buttons;
wire [127:0] status;

hps_io #(.CONF_STR(CONF_STR)) hps_io
(
	.clk_sys(clk_sys),
	.HPS_BUS(HPS_BUS),
	.EXT_BUS(),
	.gamma_bus(),
	.forced_scandoubler(forced_scandoubler),
	.buttons(buttons),
	.status_menumask({14'd0, ~status[101], 1'b0}),
	.status(status)
);

wire clk_sys;
wire clk_gpu;
wire pll_locked;
wire clk_video;
wire pll_video_locked;
pll pll
(
	.refclk(CLK_50M),
	.rst(0),
	.outclk_0(clk_sys),
	.outclk_1(clk_gpu),
	.locked(pll_locked)
);

// MiSTer's video clock switch accepts a PLL output, not a raw oscillator pin.
// Keeping video on its own PLL also makes the renderer/video CDC explicit.
pll_video pll_vid
(
	.refclk(CLK_50M),
	.rst(0),
	.outclk_0(clk_video),
	.locked(pll_video_locked)
);

// Keep the framework on its conventional system clock. CRT-Adjust needs the
// independent 50 MHz video clock for its Cabinet-mode subpixel phases, while
// the explicit dual-clock OSD RAM safely bridges the two domains. The GPU
// remains on its independent 88 MHz output.

wire reset = RESET | status[0] | buttons[1] | ~pll_locked | ~pll_video_locked;

// Every stateful block consumes reset in its own clock domain. The HPS status
// bits are generated on clk_sys, while the renderer and DDR service run at
// clk_gpu, so feeding the combined reset directly into those blocks creates a
// real recovery/removal hazard (and an impossible 20 -> 88 MHz timing path).
reg gpu_reset_meta = 1;
reg gpu_reset = 1;
always @(posedge clk_gpu) begin
	gpu_reset_meta <= reset;
	gpu_reset <= gpu_reset_meta;
end

// Native video runs at 50 MHz while the renderer remains at 88 MHz. The
// native pixel enable remains 6.25 MHz; the eight-cycle ratio
// ratio supplies CRT V-Size's Cabinet pipeline with its required sub-phases.
// Keep reset deassertion synchronous to scanout.
reg video_reset_meta = 1;
reg video_reset = 1;
always @(posedge clk_video) begin
	video_reset_meta <= reset;
	video_reset <= video_reset_meta;
end

wire       hblank;
wire       hsync;
wire       vblank;
wire       vsync;
wire       ce_pix;
wire       new_frame;
wire       new_line;

wire [7:0] gpu_ddr_burstcnt;
wire [28:0] gpu_ddr_addr;
wire [63:0] gpu_ddr_dout;
wire gpu_ddr_dout_ready;
wire gpu_ddr_busy;
wire gpu_ddr_rd;
wire [63:0] gpu_ddr_din;
wire [7:0] gpu_ddr_be;
wire gpu_ddr_we;
wire [31:0] native_frame_number;
wire [1:0] native_frame_buffer;
wire scan_buffer_valid;
wire [1:0] scan_buffer_in_use;
wire scan_underflow_toggle;

am2r_gpu gpu
(
	.clk(clk_gpu),
	.reset(gpu_reset),
	.ddram_busy(gpu_ddr_busy),
	.ddram_burstcnt(gpu_ddr_burstcnt),
	.ddram_addr(gpu_ddr_addr),
	.ddram_dout(gpu_ddr_dout),
	.ddram_dout_ready(gpu_ddr_dout_ready),
	.ddram_rd(gpu_ddr_rd),
	.ddram_din(gpu_ddr_din),
	.ddram_be(gpu_ddr_be),
	.ddram_we(gpu_ddr_we),
	.scan_buffer_valid(scan_buffer_valid),
	.scan_buffer(scan_buffer_in_use),
	.scan_underflow_toggle(scan_underflow_toggle),
	.native_frame(native_frame_number),
	.native_buffer(native_frame_buffer)
);

wire [7:0] vid_ddr_burstcnt;
wire [28:0] vid_ddr_addr;
wire [63:0] vid_ddr_dout;
wire vid_ddr_dout_ready;
wire vid_ddr_busy;
wire vid_ddr_rd;
wire native_frame_ready;
wire [7:0] native_r;
wire [7:0] native_g;
wire [7:0] native_b;

am2r_native_reader native_reader
(
	.ddr_clk(clk_gpu),
	.reset(gpu_reset),
	.ddr_busy(vid_ddr_busy),
	.ddr_burstcnt(vid_ddr_burstcnt),
	.ddr_addr(vid_ddr_addr),
	.ddr_dout(vid_ddr_dout),
	.ddr_dout_ready(vid_ddr_dout_ready),
	.ddr_rd(vid_ddr_rd),
	.clk_vid(clk_video),
	.ce_pix(ce_pix),
	.de(~(hblank | vblank)),
	.vblank(vblank),
	.new_frame(new_frame),
	.new_line(new_line),
	.source_frame(native_frame_number),
	.source_buffer(native_frame_buffer),
	.frame_ready(native_frame_ready),
	.buffer_in_use_valid(scan_buffer_valid),
	.buffer_in_use(scan_buffer_in_use),
	.r_out(native_r),
	.g_out(native_g),
	.b_out(native_b),
	.underflow_toggle(scan_underflow_toggle)
);

assign DDRAM_CLK = clk_gpu;
am2r_ddr_arbiter ddr_arbiter
(
	.clk(clk_gpu),
	.reset(gpu_reset),
	.gpu_burstcnt(gpu_ddr_burstcnt), .gpu_addr(gpu_ddr_addr),
	.gpu_din(gpu_ddr_din), .gpu_be(gpu_ddr_be),
	.gpu_rd(gpu_ddr_rd), .gpu_we(gpu_ddr_we), .gpu_busy(gpu_ddr_busy),
	.gpu_dout(gpu_ddr_dout), .gpu_dout_ready(gpu_ddr_dout_ready),
	.vid_burstcnt(vid_ddr_burstcnt), .vid_addr(vid_ddr_addr),
	.vid_rd(vid_ddr_rd), .vid_busy(vid_ddr_busy),
	.vid_dout(vid_ddr_dout), .vid_dout_ready(vid_ddr_dout_ready),
	.ddram_busy(DDRAM_BUSY), .ddram_burstcnt(DDRAM_BURSTCNT),
	.ddram_addr(DDRAM_ADDR), .ddram_dout(DDRAM_DOUT),
	.ddram_dout_ready(DDRAM_DOUT_READY), .ddram_rd(DDRAM_RD),
	.ddram_din(DDRAM_DIN), .ddram_be(DDRAM_BE), .ddram_we(DDRAM_WE)
);

wire [7:0] video_r;
wire [7:0] video_g;
wire [7:0] video_b;

am2r_native_video native_video
(
	.clk(clk_video),
	.reset(video_reset),
	.diagnostic(status[1]),
	.pattern(status[3:2]),
	.frame_ready(native_frame_ready),
	.frame_r(native_r),
	.frame_g(native_g),
	.frame_b(native_b),
	.ce_pix(ce_pix),
	.hblank(hblank),
	.hsync(hsync),
	.vblank(vblank),
	.vsync(vsync),
	.new_frame(new_frame),
	.new_line(new_line),
	.r(video_r),
	.g(video_g),
	.b(video_b)
);

wire       crt_ce_pix;
wire       crt_hblank;
wire       crt_hsync;
wire       crt_vblank;
wire       crt_vsync;
wire       crt_de;
wire [7:0] crt_r;
wire [7:0] crt_g;
wire [7:0] crt_b;
reg crt_on = 0;
reg signed [4:0] crt_hsize = 0;
reg signed [8:0] crt_hposition = 0;
reg signed [5:0] crt_vshift = 0;
reg signed [5:0] crt_vsize = 0;
reg crt_cabinet_mode = 1;

wire [6:0] crt_hposition_menu = status[85:79];
wire signed [8:0] crt_hposition_decoded =
	(crt_hposition_menu <= 7'd48) ? $signed({2'b0, crt_hposition_menu}) :
	(crt_hposition_menu <= 7'd96) ? $signed({2'b0, crt_hposition_menu}) - 9'sd97 :
	9'sd0;
wire [3:0] crt_vsize_menu = status[107:104];
wire signed [5:0] crt_vsize_step =
	(crt_vsize_menu <= 4'd7) ? $signed({2'b0, crt_vsize_menu}) :
	$signed({2'b0, crt_vsize_menu}) - 6'sd15;

always @(posedge clk_video) begin
	if (video_reset) begin
		crt_on <= 0;
		crt_hsize <= 0;
		crt_hposition <= 0;
		crt_vshift <= 0;
		crt_vsize <= 0;
		crt_cabinet_mode <= 1;
	end else if (ce_pix) begin
		crt_on <= status[101];
		crt_hsize <= $signed(status[100:96]);
		crt_hposition <= crt_hposition_decoded;
		crt_vshift <= $signed(status[78:74]);
		crt_vsize <= -(crt_vsize_step + (crt_vsize_step <<< 1));
		crt_cabinet_mode <= ~status[108];
	end
end

am2r_crt_pipeline crt_video
(
	.clk(clk_video),
	.reset(video_reset),
	.active(crt_on),
	.hsize(crt_hsize),
	.hposition(crt_hposition),
	.vshift(crt_vshift),
	.vsize(crt_vsize),
	.cabinet_mode(crt_cabinet_mode),
	.ce_pix_in(ce_pix),
	.r_in(video_r),
	.g_in(video_g),
	.b_in(video_b),
	.hs_in(hsync),
	.hblank_in(hblank),
	.vs_in(vsync),
	.vblank_in(vblank),
	.ce_pix_out(crt_ce_pix),
	.r_out(crt_r),
	.g_out(crt_g),
	.b_out(crt_b),
	.hs_out(crt_hsync),
	.de_out(crt_de),
	.hblank_out(crt_hblank),
	.vs_out(crt_vsync),
	.vblank_out(crt_vblank)
);

assign CLK_VIDEO = clk_video;
assign CE_PIXEL  = crt_ce_pix;
assign VGA_DE    = crt_de;
assign VGA_HS    = crt_hsync;
assign VGA_VS    = crt_vsync;
assign VGA_R     = crt_r;
assign VGA_G     = crt_g;
assign VGA_B     = crt_b;

reg [26:0] activity_counter = 0;
always @(posedge clk_sys) activity_counter <= activity_counter + 1'd1;
assign LED_USER = activity_counter[26];

endmodule
