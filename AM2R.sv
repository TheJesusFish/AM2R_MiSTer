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
// 0: NTSC, 1: PAL60, 2: PAL. NTSC and PAL60 share the 60 Hz raster; the HPS
// frontend selects their composite/S-Video subcarrier.
wire [1:0] video_standard = status[28:27];
wire [2:0] hdmi_scale = status[31:29];
wire [2:0] sd_fx = status[34:32];
wire [2:0] scanlines = sd_fx ? sd_fx - 1'd1 : 3'd0;

`include "build_id.v"
localparam CONF_STR = {
	"AM2R;;",
	"-;",
	"O[5:4],Aspect ratio,Original,Full Screen,[ARC1],[ARC2];",
	"O[31:29],Scale,Normal,V-Integer,Narrower HV-Integer,Wider HV-Integer,HV-Integer;",
	"O[28:27],Video Standard,NTSC,PAL60,PAL;",
	"H0O[34:32],Scandoubler Fx,None,HQ2x,CRT 25%,CRT 50%,CRT 75%;",
	"P1,CRT Adjustments;",
	"P1O[13:10],Analog H Position,0,1,2,3,4,5,6,7,-8,-7,-6,-5,-4,-3,-2,-1;",
	"P1O[17:14],Analog V Position,0,1,2,3,4,5,6,7,-8,-7,-6,-5,-4,-3,-2,-1;",
	"P1O[18],Analog H Scaler,Off,On;",
	"P1O[23:19],Analog H Scale,100%,102%,103%,105%,106%,108%,109%,111%,113%,114%,116%,117%,119%,120%,122%,123%,75%,77%,78%,80%,81%,83%,84%,86%,88%,89%,91%,92%,94%,95%,97%,98%;",
	"P1O[26:24],CRT UI V Inset,Off,2px,4px,6px,8px,10px,12px,14px;",
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
wire  [21:0] gamma_bus;
wire   [1:0] buttons;
wire [127:0] status;

hps_io #(.CONF_STR(CONF_STR)) hps_io
(
	.clk_sys(clk_sys),
	.HPS_BUS(HPS_BUS),
	.EXT_BUS(),
	.gamma_bus(gamma_bus),
	.status_menumask({15'd0, ~forced_scandoubler}),
	.forced_scandoubler(forced_scandoubler),
	.buttons(buttons),
	.status(status)
);

wire clk_sys;
wire clk_gpu;
wire pll_locked;
wire clk_video;
wire pll_video_locked;
wire clk_sys_unused;
pll pll
(
	.refclk(CLK_50M),
	.rst(0),
	.outclk_0(clk_sys_unused),
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

// Keep the framework's system and native-video domains on the same 25 MHz
// core PLL. Besides being ample for hps_io, this lets the untouched upstream
// OSD infer its normal block RAM rather than becoming a mixed-clock register
// array. The GPU remains on its independent 88 MHz PLL output.
assign clk_sys = clk_video;

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

// The framework and native video run at 25 MHz while the renderer remains at
// 88 MHz. Keep reset deassertion synchronous to scanout.
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
wire       pace_tick;
wire       native_pal;

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
wire [3:0] hdmi_protect;

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
	.hdmi_protect(hdmi_protect),
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
wire [31:0] scanout_frame_number;
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
	.pal(native_pal),
	.new_frame(new_frame),
	.new_line(new_line),
	.source_frame(native_frame_number),
	.source_buffer(native_frame_buffer),
	.frame_ready(native_frame_ready),
	.scanout_frame(scanout_frame_number),
	.buffer_in_use_valid(scan_buffer_valid),
	.buffer_in_use(scan_buffer_in_use),
	.r_out(native_r),
	.g_out(native_g),
	.b_out(native_b),
	.underflow_toggle(scan_underflow_toggle)
);

// HDMI reads the latest published native frame directly from DDR, so the
// analog-only CRT position and horizontal-scale controls never reach it.
am2r_hdmi_fb hdmi_fb
(
	.clk(clk_gpu),
	.reset(gpu_reset),
	.fb_vbl(FB_VBL),
	.native_frame(native_frame_number),
	.native_buffer(native_frame_buffer),
	.fb_base(FB_BASE),
	.fb_force_blank(FB_FORCE_BLANK),
	.protect(hdmi_protect)
);
assign FB_EN     = 1;
assign FB_FORMAT = 5'b10110;   // 32bpp, B,G,R,X byte order (XRGB8888 words)
assign FB_WIDTH  = 12'd320;
assign FB_HEIGHT = 12'd240;
assign FB_STRIDE = 14'd1280;

assign DDRAM_CLK = clk_gpu;
am2r_ddr_arbiter ddr_arbiter
(
	.clk(clk_gpu),
	.reset(gpu_reset),
	.frame_tick(pace_tick),
	.display_tick(new_frame),
	.native_frame(native_frame_number),
	.scanout_frame(scanout_frame_number),
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
	.standard(video_standard),
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
	.pace_tick(pace_tick),
	.pal(native_pal),
	.r(video_r),
	.g(video_g),
	.b(video_b)
);

wire       crt_ce_pix;
wire       crt_hblank;
wire       crt_hsync;
wire       crt_vblank;
wire       crt_vsync;
wire [7:0] crt_r;
wire [7:0] crt_g;
wire [7:0] crt_b;
wire       crt_hscale_active;

am2r_crt_video crt_video
(
	.clk(clk_video),
	.reset(video_reset),
	.ce_pix_in(ce_pix),
	.r_in(video_r),
	.g_in(video_g),
	.b_in(video_b),
	.hs_in(hsync),
	.hblank_in(hblank),
	.vs_in(vsync),
	.vblank_in(vblank),
	.h_position($signed({status[13], status[13:10]})),
	.v_position($signed({status[17], status[17:14]})),
	// The line scaler emits one pixel per video clock, which the 2x
	// scandoubler cannot double. It exists for 15 kHz displays only.
	.hscale_enable(status[18] & ~forced_scandoubler),
	.hscale($signed(status[23:19])),
	.ce_pix_out(crt_ce_pix),
	.r_out(crt_r),
	.g_out(crt_g),
	.b_out(crt_b),
	.hs_out(crt_hsync),
	.hblank_out(crt_hblank),
	.vs_out(crt_vsync),
	.vblank_out(crt_vblank),
	.hscale_active(crt_hscale_active)
);

assign CLK_VIDEO = clk_video;

// Framework gamma and, when MiSTer.ini forces it for 31 kHz displays, the
// scandoubler with optional HQ2x. Without forced_scandoubler this is a
// registered pass-through of the native or CRT-adjusted 15 kHz raster. The
// gamma stage accepts the H Scaler's continuous pixel enable as well as the
// native pulsed one; see rtl/am2r_video_out.sv.
wire vga_de;
am2r_video_out #(.LINE_LENGTH(320)) video_out
(
	.clk(CLK_VIDEO),
	.ce_pix(crt_ce_pix),
	.scandoubler(forced_scandoubler),
	.hq2x(sd_fx == 3'd1),
	.freeze(HDMI_FREEZE),
	.gamma_bus(gamma_bus),
	.r(crt_r),
	.g(crt_g),
	.b(crt_b),
	.hsync(crt_hsync),
	.vsync(crt_vsync),
	.hblank(crt_hblank),
	.vblank(crt_vblank),
	.ce_pix_out(CE_PIXEL),
	.vga_r(VGA_R),
	.vga_g(VGA_G),
	.vga_b(VGA_B),
	.vga_hs(VGA_HS),
	.vga_vs(VGA_VS),
	.vga_de(vga_de)
);

// Scanline effects apply only to the scandoubled 31 kHz output.
assign VGA_SL = forced_scandoubler ? scanlines[1:0] : 2'd0;

assign VGA_DE = vga_de;

// HDMI aspect ratio and integer scaling. HDMI shows the 320x240 native
// framebuffer, so measure the unadjusted native raster rather than the
// analog output, whose width the CRT horizontal scaler can change.
video_freak video_freak
(
	.CLK_VIDEO(CLK_VIDEO),
	.CE_PIXEL(ce_pix),
	.VGA_VS(vsync),
	.HDMI_WIDTH(HDMI_WIDTH),
	.HDMI_HEIGHT(HDMI_HEIGHT),
	.VGA_DE(),
	.VIDEO_ARX(VIDEO_ARX),
	.VIDEO_ARY(VIDEO_ARY),
	.VGA_DE_IN(~(hblank | vblank)),
	.ARX((!ar) ? 12'd4 : (ar - 1'd1)),
	.ARY((!ar) ? 12'd3 : 12'd0),
	.CROP_SIZE(12'd0),
	.CROP_OFF(5'd0),
	.SCALE(hdmi_scale)
);

reg [26:0] activity_counter = 0;
always @(posedge clk_sys) activity_counter <= activity_counter + 1'd1;
assign LED_USER = activity_counter[26];

endmodule
