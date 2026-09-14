`timescale 1ns/1ps

module am2r_native_reader_late_frame_tb;
	localparam [28:0] BUF0_ADDR = 29'h07400020;
	localparam [28:0] BUF1_ADDR = 29'h07409620;
	localparam [28:0] BUF2_ADDR = 29'h07412c20;

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
	wire buffer_in_use_valid;
	wire [1:0] buffer_in_use;
	wire underflow_toggle;
	wire [7:0] r, g, b;
	reg [31:0] source_frame = 1;
	reg [1:0] source_buffer = 0;

	reg pending = 0;
	reg [7:0] remaining = 0;
	reg [3:0] delay_count = 0;
	reg [28:0] response_addr = 0;
	reg [7:0] response_tag;
	reg [1:0] expected_buffer = 0;
	reg checking = 1;
	integer samples = 0;
	integer errors = 0;
	reg [7:0] expected_tag;

	am2r_native_video timing(
		.clk(clk_vid), .reset(reset), .diagnostic(1'b0), .pattern(2'b0),
		.frame_ready(frame_ready), .frame_r(r), .frame_g(g), .frame_b(b),
		.ce_pix(ce_pix), .hblank(hblank), .hsync(hsync), .vblank(vblank),
		.vsync(vsync), .new_frame(new_frame), .new_line(new_line),
		.r(), .g(), .b()
	);

	am2r_native_reader dut(
		.ddr_clk(ddr_clk), .reset(reset), .ddr_busy(ddr_busy),
		.ddr_burstcnt(ddr_burstcnt), .ddr_addr(ddr_addr), .ddr_dout(ddr_dout),
		.ddr_dout_ready(ddr_dout_ready), .ddr_rd(ddr_rd),
		.clk_vid(clk_vid), .ce_pix(ce_pix), .de(~(hblank | vblank)),
		.vblank(vblank), .new_frame(new_frame), .new_line(new_line),
		.source_frame(source_frame), .source_buffer(source_buffer),
		.frame_ready(frame_ready), .buffer_in_use_valid(buffer_in_use_valid),
		.buffer_in_use(buffer_in_use), .r_out(r), .g_out(g), .b_out(b),
		.underflow_toggle(underflow_toggle)
	);

	always #5 ddr_clk = ~ddr_clk;
	always #20 clk_vid = ~clk_vid;

	// Distinguish the three physical presentation buffers in every returned
	// word.  This makes even a single stale or mixed-buffer line observable.
	always @(posedge ddr_clk) begin
		ddr_dout_ready <= 0;
		if (ddr_rd && !ddr_busy && !pending) begin
			if (ddr_burstcnt > 8'd128)
				$fatal(1, "FAIL: illegal DDR burst %0d", ddr_burstcnt);
			pending <= 1;
			remaining <= ddr_burstcnt;
			delay_count <= 2;
			response_addr <= ddr_addr;
		end else if (pending) begin
			if (delay_count != 0) delay_count <= delay_count - 1'b1;
			else begin
				response_tag = response_addr[7:0] +
				               ((response_addr >= BUF2_ADDR) ? 8'h80 :
				                (response_addr >= BUF1_ADDR) ? 8'h40 : 8'h00);
				ddr_dout <= {8'h00, 8'h33, response_tag, 8'h44,
				             8'h00, response_tag, 8'h11, 8'h22};
				ddr_dout_ready <= 1;
				response_addr <= response_addr + 1'b1;
				if (remaining == 1) pending <= 0;
				else remaining <= remaining - 1'b1;
			end
		end
	end

	always @(posedge clk_vid) begin
		if (!reset && checking && ce_pix && !(hblank | vblank) && frame_ready &&
		    samples < 320 * 240) begin
			#1;
			expected_tag = 8'h20 + (samples >> 1) +
			               (expected_buffer == 2 ? 8'h80 :
			                expected_buffer == 1 ? 8'h40 : 8'h00);
			if (samples[0] == 0) begin
				if ({r,g,b} !== {expected_tag, 8'h11, 8'h22}) begin
					if (errors < 12)
						$display("pixel %0d buffer %0d got %h expected %h1122",
						         samples, expected_buffer, {r,g,b}, expected_tag);
					errors = errors + 1;
				end
			end else if ({r,g,b} !== {8'h33, expected_tag, 8'h44}) begin
				if (errors < 12)
					$display("pixel %0d buffer %0d got %h expected 33%h44",
					         samples, expected_buffer, {r,g,b}, expected_tag);
				errors = errors + 1;
			end
			samples = samples + 1;
		end
	end

	task wait_for_new_frame;
	begin
		while (!new_frame) @(posedge clk_vid);
		while (new_frame) @(posedge clk_vid);
	end
	endtask

	task wait_blank_lines(input integer count);
		integer seen;
	begin
		seen = 0;
		while (seen < count) begin
			@(posedge clk_vid);
			if (new_line && vblank) seen = seen + 1;
		end
	end
	endtask

	initial begin
		repeat (8) @(posedge ddr_clk);
		reset = 0;

		// Establish the ordinary frame-start latch on buffer zero.
		wait (samples == 320 * 240);
		checking = 0;

		// Publish buffer one two blank lines after the next frame edge.  The
		// same upcoming raster must use the new buffer from its first pixel.
		wait_for_new_frame();
		wait_blank_lines(2);
		source_buffer = 2'd1;
		source_frame = 2;
		expected_buffer = 2'd1;
		samples = 0;
		checking = 1;
		wait (samples == 320 * 240);
		checking = 0;
		if (!buffer_in_use_valid || buffer_in_use != 1) errors = errors + 1;

		// A publication after the four-line cutoff must remain queued for the
		// following raster, avoiding a risky late FIFO reset.
		wait_for_new_frame();
		wait_blank_lines(6);
		source_buffer = 2'd2;
		source_frame = 3;
		expected_buffer = 2'd1;
		samples = 0;
		checking = 1;
		wait (samples == 320 * 240);
		checking = 0;
		if (buffer_in_use != 2'd1) errors = errors + 1;

		// The queued buffer becomes active normally at the next frame edge.
		wait_for_new_frame();
		expected_buffer = 2'd2;
		samples = 0;
		checking = 1;
		wait (samples == 320 * 240);
		checking = 0;
		if (buffer_in_use != 2'd2) errors = errors + 1;
		if (underflow_toggle != 0) errors = errors + 1;

		if (errors == 0)
			$display("PASS: early-vblank publication is adopted atomically and late publication waits one raster");
		else $fatal(1, "FAIL: %0d late-frame reader errors", errors);
		$finish;
	end

	initial begin
		#100000000;
		$fatal(1, "FAIL: late-frame reader timeout samples=%0d", samples);
	end
endmodule
