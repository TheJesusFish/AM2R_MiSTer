// SPDX-License-Identifier: GPL-2.0-or-later
// AM2R-local copy of MiSTer's OSD with only its storage implementation
// changed to an explicit dual-clock M10K. Quartus 17 otherwise expands both
// 32-Kbit OSD buffers into more than 66,000 registers once CRT V-Size is
// present. The public interface and drawing logic remain framework-identical.
// The upstream sys/osd.v file is deliberately left untouched.

module osd
(
	input         clk_sys,
	input         io_osd,
	input         io_strobe,
	input  [15:0] io_din,

	input         clk_video,
	input  [23:0] din,
	input         de_in,
	input         vs_in,
	input         hs_in,
	output [23:0] dout,
	output reg    de_out,
	output reg    vs_out,
	output reg    hs_out,

	output reg    osd_status
);

parameter  OSD_COLOR    =  3'd4;

localparam OSD_WIDTH    = 12'd256;
localparam OSD_HEIGHT   = 12'd64;

`ifdef MENU_CORE
localparam OSD_HDR      = 12'd24;
`else
localparam OSD_HDR      = 12'd0;
`endif

localparam OSD_BUF_DEPTH = OSD_HDR ? 5120 : 4096;
localparam OSD_BUF_AW    = OSD_HDR ? 13   : 12;

reg        osd_enable;

// Promote the write-side locals so they can feed the explicit RAM port.
reg [12:0] bcnt;
reg  [7:0] cmd;
reg        has_cmd;
reg        old_strobe;
reg        highres = 0;
(* direct_enable *) reg ce_pix;

wire       osd_buf_we = io_osd && !old_strobe && io_strobe && has_cmd && (cmd[7:5] == 3'b001);
wire [7:0] osd_buf_q;
wire [OSD_BUF_AW-1:0] osd_buf_raddr;

altsyncram #(
	.operation_mode                    ("DUAL_PORT"),
	.width_a                           (8),
	.widthad_a                         (OSD_BUF_AW),
	.numwords_a                        (OSD_BUF_DEPTH),
	.width_b                           (8),
	.widthad_b                         (OSD_BUF_AW),
	.numwords_b                        (OSD_BUF_DEPTH),
	.outdata_reg_b                     ("CLOCK1"),
	.ram_block_type                    ("M10K"),
	.read_during_write_mode_mixed_ports("DONT_CARE"),
	.lpm_type                          ("altsyncram"),
	.intended_device_family            ("Cyclone V")
) osd_buf_ram (
	.clock0         (clk_sys),
	.clock1         (clk_video),
	.wren_a         (osd_buf_we),
	.address_a      (bcnt[OSD_BUF_AW-1:0]),
	.data_a         (io_din[7:0]),
	.clocken1       (ce_pix),
	.address_b      (osd_buf_raddr),
	.q_b            (osd_buf_q),
	.wren_b         (1'b0),
	.rden_a         (1'b1),
	.rden_b         (1'b1),
	.clocken0       (1'b1),
	.aclr0          (1'b0),
	.aclr1          (1'b0),
	.byteena_a      (1'b1),
	.byteena_b      (1'b1),
	.addressstall_a (1'b0),
	.addressstall_b (1'b0),
	.data_b         (8'h00),
	.address2_a     (),
	.address2_b     (),
	.eccstatus      ()
);

reg        info = 0;
reg  [8:0] infoh;
reg  [8:0] infow;
reg [21:0] infox;
reg [21:0] infoy;
reg [21:0] osd_h;
reg [21:0] osd_t;
reg [21:0] osd_w;

reg  [1:0] rot = 0;

always@(posedge clk_sys) begin
	osd_t <= rot[0] ? OSD_WIDTH : (OSD_HEIGHT<<1);
	osd_h <= rot[0] ? (info ? infow : OSD_WIDTH) : info ? infoh : (OSD_HEIGHT<<highres);
	osd_w <= rot[0] ? (info ? infoh : (OSD_HEIGHT<<highres)) : (info ? infow : OSD_WIDTH);

	old_strobe <= io_strobe;

	if(~io_osd) begin
		bcnt <= 0;
		has_cmd <= 0;
		cmd <= 0;
		if(cmd[7:4] == 4) osd_enable <= cmd[0];
	end else begin
		if(~old_strobe & io_strobe) begin
			if(!has_cmd) begin
				has_cmd <= 1;
				cmd <= io_din[7:0];
				// command 0x40: OSDCMDENABLE, OSDCMDDISABLE
				if(io_din[7:4] == 4) begin
					if(!io_din[0]) {osd_status,highres} <= 0;
					else {osd_status,info} <= {~io_din[2] & ~io_din[3],io_din[2]};
					bcnt  <= 0;
				end
				// command 0x20: OSDCMDWRITE
				if(io_din[7:5] == 'b001) begin
					if(io_din[3]) highres <= 1;
					bcnt <= {io_din[4:0], 8'h00};
				end
			end else begin
				// command 0x40: OSDCMDENABLE, OSDCMDDISABLE
				if(cmd[7:4] == 4) begin
					if(bcnt == 0) infox <= io_din[11:0];
					if(bcnt == 1) infoy <= io_din[11:0];
					if(bcnt == 2) infow <= {io_din[5:0], 3'b000};
					if(bcnt == 3) infoh <= {io_din[5:0], 3'b000};
					if(bcnt == 4) rot   <= io_din[1:0];
				end

				// command 0x20: OSDCMDWRITE
				bcnt <= bcnt + 1'd1;
			end
		end
	end
end

always @(posedge clk_video) begin
	reg [21:0] cnt = 0;
	reg [21:0] pixsz, pixcnt;
	reg deD;

	cnt <= cnt + 1'd1;
	deD <= de_in;

	pixcnt <= pixcnt + 1'd1;
	if(pixcnt == pixsz) pixcnt <= 0;
	ce_pix <= !pixcnt;

	if(~deD && de_in) cnt <= 0;

	if(deD && ~de_in) begin
		pixsz  <= (((cnt+1'b1) >> (9-rot[0])) > 1) ? (((cnt+1'b1) >> (9-rot[0])) - 1'd1) : 22'd0;
		pixcnt <= 0;
	end
end

reg  [2:0] osd_de;
reg        osd_pixel;
reg [21:0] v_cnt;
reg [21:0] osd_vcnt;
reg [21:0] osd_hcnt;
reg [21:0] osd_hcnt2;
reg        v_cnt_h, v_cnt_1, v_cnt_2, v_cnt_3, v_cnt_4;
reg [21:0] v_osd_start_h, v_osd_start_1, v_osd_start_2, v_osd_start_3, v_osd_start_4, v_osd_start_5;
reg [21:0] v_info_start_h, v_info_start_1, v_info_start_2, v_info_start_3, v_info_start_4, v_info_start_5;

wire [21:0] osd_h_hdr = (info || rot) ? osd_h : (osd_h + OSD_HDR);
assign osd_buf_raddr = rot[0]
	? ({osd_hcnt2[6:3], osd_vcnt[7:0]} ^ {{4{~rot[1]}}, {8{rot[1]}}})
	: {osd_vcnt[7:3], osd_hcnt[7:0]};

// pipeline the comparisons a bit
always @(posedge clk_video) if(ce_pix) begin
	v_cnt_h <= v_cnt <= osd_t;
	v_cnt_1 <= v_cnt < 320;
	v_cnt_2 <= v_cnt < 640;
	v_cnt_3 <= v_cnt < 960;
	v_cnt_4 <= v_cnt < 1280;

	v_osd_start_h <= (v_cnt-(osd_h_hdr>>1))>>1;
	v_osd_start_1 <= (v_cnt-osd_h_hdr)>>1;
	v_osd_start_2 <= (v_cnt-(osd_h_hdr<<1))>>1;
	v_osd_start_3 <= (v_cnt-(osd_h_hdr + (osd_h_hdr<<1)))>>1;
	v_osd_start_4 <= (v_cnt-(osd_h_hdr<<2))>>1;
	v_osd_start_5 <= (v_cnt-(osd_h_hdr + (osd_h_hdr<<2)))>>1;

	v_info_start_h <= rot[0] ? infox : infoy;
	v_info_start_1 <= rot[0] ? infox : infoy;
	v_info_start_2 <= rot[0] ? (infox<<1) : (infoy<<1);
	v_info_start_3 <= rot[0] ? (infox + (infox << 1)) : (infoy + (infoy << 1));
	v_info_start_4 <= rot[0] ? (infox << 2) : (infoy << 2);
	v_info_start_5 <= rot[0] ? (infox + (infox << 2)) : (infoy + (infoy << 2));
end

always @(posedge clk_video) begin
	reg        deD;
	reg  [2:0] osd_div;
	reg  [2:0] multiscan;
	reg [23:0] h_cnt;
	reg [21:0] dsp_width;
	reg [21:0] h_osd_start;
	reg [21:0] v_osd_start;
	reg        osd_de1,osd_de2;
	reg  [1:0] osd_en;
	reg        f1;
	reg        half;

	if(ce_pix) begin

		deD <= de_in;
		if(~&h_cnt) h_cnt <= h_cnt + 1'd1;

		if(~&osd_hcnt)  osd_hcnt  <= osd_hcnt + 1'd1;
		if(~&osd_hcnt2) osd_hcnt2 <= osd_hcnt2 + 1'd1;

		if (h_cnt == h_osd_start) begin
			osd_de[0] <= osd_en[1] && osd_h && (
		                  osd_vcnt[11] ? (osd_vcnt[7] && (osd_vcnt[6:0] >= 4) && (osd_vcnt[6:0] < 19)) :
								(info && (rot == 3)) ? !osd_vcnt[21:8] :
			               (osd_vcnt < osd_h)
								);
			osd_hcnt <= 0;
			osd_hcnt2 <= 0;
			if(info && rot == 1) osd_hcnt2 <= 22'd128-infoh;
		end
		if (osd_hcnt+1 == osd_w) osd_de[0] <= 0;

		// falling edge of de
		if(!de_in && deD) dsp_width <= h_cnt[21:0];

		// rising edge of de
		if(de_in && !deD) begin
			h_cnt <= 0;
			v_cnt <= v_cnt + 1'd1;
			h_osd_start <= info ? (rot[0] ? infoy : infox) : (((dsp_width - osd_w)>>1) - 2'd2);

			if(h_cnt > {dsp_width, 2'b00}) begin
				v_cnt <= 1;
				f1 <= ~f1; // skip every other frame for interlace compatibility.
				if(~f1) begin

					osd_en <= (osd_en << 1) | osd_enable;
					if(~osd_enable) osd_en <= 0;

					half <= 0;
					if(v_cnt_h) begin
						multiscan <= 0;
						v_osd_start <= info ? v_info_start_h : v_osd_start_h;
						half <= 1;
					end
					else if(v_cnt_1 | (rot[0] & v_cnt_2)) begin
						multiscan <= 0;
						v_osd_start <= info ? v_info_start_1 : v_osd_start_1;
					end
					else if(rot[0] ? v_cnt_3 : v_cnt_2) begin
						multiscan <= 1;
						v_osd_start <= info ? v_info_start_2 : v_osd_start_2;
					end
					else if(rot[0] ? v_cnt_4 : v_cnt_3) begin
						multiscan <= 2;
						v_osd_start <= info ? v_info_start_3 : v_osd_start_3;
					end
					else if(rot[0] | v_cnt_4) begin
						multiscan <= 3;
						v_osd_start <= info ? v_info_start_4 : v_osd_start_4;
					end
					else begin
						multiscan <= 4;
						v_osd_start <= info ? v_info_start_5 : v_osd_start_5;
					end
				end
			end

			osd_div <= osd_div + 1'd1;
			if(osd_div == multiscan) begin
				osd_div <= 0;
				if(~osd_vcnt[10]) osd_vcnt <= osd_vcnt + 1'd1 + half;
				if(osd_vcnt == 'b100010011111 && ~info) osd_vcnt <= 0;
			end
			if(v_osd_start == v_cnt) begin
				{osd_div,osd_vcnt} <= 0;
				if(info && rot == 3) osd_vcnt <= 22'd256-infow;
				else if(OSD_HDR && !rot) osd_vcnt <= {~info, 3'b000, ~info, 7'b0000000};
			end
		end

		osd_pixel <= osd_buf_q[rot[0] ? ((osd_hcnt2[2:0]-1'd1) ^ {3{~rot[1]}}) : osd_vcnt[2:0]];
		osd_de[2:1] <= osd_de[1:0];
	end
end

reg [23:0] rdout;
assign dout = rdout;

always @(posedge clk_video) begin
	reg [23:0] ordout1, nrdout1, rdout2, rdout3;
	reg de1,de2,de3;
	reg osd_mux;
	reg vs1,vs2,vs3;
	reg hs1,hs2,hs3;

	nrdout1 <= din;
	ordout1 <= {{osd_pixel, osd_pixel, OSD_COLOR[2], din[23:19]},// 23:16
	            {osd_pixel, osd_pixel, OSD_COLOR[1], din[15:11]},// 15:8
	            {osd_pixel, osd_pixel, OSD_COLOR[0], din[7:3]}}; //  7:0

	osd_mux <= ~osd_de[2];
	rdout2  <= osd_mux ? nrdout1 : ordout1;
	rdout3  <= rdout2;

	de1 <= de_in; de2 <= de1; de3 <= de2;
	hs1 <= hs_in; hs2 <= hs1; hs3 <= hs2;
	vs1 <= vs_in; vs2 <= vs1; vs3 <= vs2;

	rdout   <= rdout3;
	de_out  <= de3;
	hs_out  <= hs3;
	vs_out  <= vs3;
end

endmodule
