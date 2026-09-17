`timescale 1ns/1ps

module am2r_native_reader_tb;
	reg ddr_clk = 0;
	reg clk_vid = 0;
	reg reset = 1;
	reg ddr_busy = 0;
	wire [7:0] ddr_burstcnt;
	wire [28:0] ddr_addr;
	reg [63:0] ddr_dout = 0;
	reg ddr_dout_ready = 0;
	wire ddr_rd;
	wire ce_pix, hblank, hsync, vblank, vsync, new_frame, new_line;
	wire frame_ready;
	wire [31:0] scanout_frame;
	wire buffer_in_use_valid;
	wire [1:0] buffer_in_use;
	wire underflow_toggle;
	wire [7:0] r, g, b;
	reg pending = 0;
	reg [7:0] remaining = 0;
	reg [16:0] delay_count = 0;
	reg [28:0] response_addr = 0;
	reg stall_injected = 0;
	integer samples = 0;
	integer errors = 0;
	integer bursts_seen = 0;
	integer request_offset;
	reg [7:0] word_id;

	am2r_native_video timing(
		.clk(clk_vid), .reset(reset),
		.frame_ready(frame_ready), .frame_r(r), .frame_g(g), .frame_b(b),
		.ce_pix(ce_pix), .hblank(hblank), .hsync(hsync), .vblank(vblank),
		.vsync(vsync), .new_frame(new_frame), .new_line(new_line),
		.pace_tick(),
		.r(), .g(), .b()
	);

	am2r_native_reader dut(
		.ddr_clk(ddr_clk), .reset(reset), .ddr_busy(ddr_busy),
		.ddr_burstcnt(ddr_burstcnt), .ddr_addr(ddr_addr), .ddr_dout(ddr_dout),
		.ddr_dout_ready(ddr_dout_ready), .ddr_rd(ddr_rd),
		.clk_vid(clk_vid), .ce_pix(ce_pix), .de(~(hblank | vblank)),
		.vblank(vblank), .new_frame(new_frame), .new_line(new_line),
		.source_frame(32'd1), .source_buffer(2'd0), .frame_ready(frame_ready),
		.scanout_frame(scanout_frame),
		.buffer_in_use_valid(buffer_in_use_valid), .buffer_in_use(buffer_in_use),
		.r_out(r), .g_out(g), .b_out(b), .underflow_toggle(underflow_toggle)
	);

	always #5 ddr_clk = ~ddr_clk;
	always #20 clk_vid = ~clk_vid;

	// Give every DDR word a unique identity. A constant red/green pattern
	// cannot detect a show-ahead FIFO word being duplicated or skipped.
	always @(posedge ddr_clk) begin
		ddr_dout_ready <= 0;
		if (ddr_rd && !ddr_busy && !pending) begin
			bursts_seen = bursts_seen + 1;
			if (ddr_burstcnt > 8'd128)
				$fatal(1, "FAIL: DDR burst %0d exceeds MiSTer maximum of 128", ddr_burstcnt);
			request_offset = (ddr_addr - 29'h07400020) % 160;
			if (request_offset == 0 && ddr_burstcnt != 8'd96)
				$fatal(1, "FAIL: first line chunk is %0d words, expected 96", ddr_burstcnt);
			if (request_offset == 96 && ddr_burstcnt != 8'd64)
				$fatal(1, "FAIL: final line chunk is %0d words, expected 64", ddr_burstcnt);
			if (request_offset != 0 && request_offset != 96)
				$fatal(1, "FAIL: unexpected line chunk offset %0d", request_offset);
			pending <= 1;
			remaining <= ddr_burstcnt;
			// Delay the line-32 refill by almost 19 complete scanlines.
			// A shallow FIFO drains and loses alignment here; the production
			// reader's vblank preload must absorb the transient stall.
			if (!stall_injected && ddr_addr == 29'h07400020 + 29'd5120) begin
				delay_count <= 17'd120000;
				stall_injected <= 1;
			end else begin
				delay_count <= 17'd3;
			end
			response_addr <= ddr_addr;
		end else if (pending) begin
			if (delay_count != 0) delay_count <= delay_count - 1'b1;
			else begin
				ddr_dout <= {8'h00, 8'h33, response_addr[7:0], 8'h44,
				             8'h00, response_addr[7:0], 8'h11, 8'h22};
				ddr_dout_ready <= 1;
				response_addr <= response_addr + 1'b1;
				if (remaining == 1) pending <= 0;
				else remaining <= remaining - 1'b1;
			end
		end
	end

	always @(posedge clk_vid) begin
		if (!reset && ce_pix && !(hblank | vblank) && frame_ready && samples < 320 * 240) begin
			#1;
			word_id = 8'h20 + (samples >> 1);
			if (samples[0] == 0) begin
				if ({r,g,b} !== {word_id, 8'h11, 8'h22}) begin
					if (errors < 16)
						$display("pixel %0d got %h expected %h1122", samples, {r,g,b}, word_id);
					errors = errors + 1;
				end
			end else if ({r,g,b} !== {8'h33, word_id, 8'h44}) begin
				if (errors < 16)
					$display("pixel %0d got %h expected 33%h44", samples, {r,g,b}, word_id);
				errors = errors + 1;
			end
			samples = samples + 1;
		end
	end

	initial begin
		repeat (8) @(posedge ddr_clk);
		reset = 0;
		wait (samples == 320 * 240);
		if (!buffer_in_use_valid || buffer_in_use != 0) errors = errors + 1;
		if (scanout_frame != 1) errors = errors + 1;
		if (!stall_injected) errors = errors + 1;
		if (bursts_seen != 240 * 2) errors = errors + 1;
		if (underflow_toggle != 0) errors = errors + 1;
		if (errors == 0) $display("PASS: split DDR bursts absorb a nineteen-line stall and preserve XRGB8888 scanout");
		else $fatal(1, "FAIL: %0d native-reader pixel errors", errors);
		$finish;
	end

	initial begin
		#50000000;
		$fatal(1, "FAIL: native reader timeout samples=%0d", samples);
	end
endmodule
