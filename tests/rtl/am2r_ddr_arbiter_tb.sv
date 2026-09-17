`timescale 1ns/1ps

module am2r_ddr_arbiter_tb;
	reg clk = 0;
	reg reset = 1;
	reg frame_tick = 0;
	reg display_tick = 0;
	reg [31:0] native_frame = 32'h12345678;
	reg [31:0] scanout_frame = 32'h89abcdef;
	reg [7:0] gpu_burstcnt = 2;
	reg [28:0] gpu_addr = 29'h100;
	reg [63:0] gpu_din = 64'h1122334455667788;
	reg [7:0] gpu_be = 8'hff;
	reg gpu_rd = 0, gpu_we = 0;
	wire gpu_busy, gpu_ready;
	wire [63:0] gpu_dout;
	reg [7:0] vid_burstcnt = 3;
	reg [28:0] vid_addr = 29'h200;
	reg vid_rd = 0;
	wire vid_busy, vid_ready;
	wire [63:0] vid_dout;
	reg ddram_busy = 0;
	reg [63:0] ddram_dout = 0;
	reg ddram_ready = 0;
	wire [7:0] ddram_burstcnt;
	wire [28:0] ddram_addr;
	wire ddram_rd, ddram_we;
	wire [63:0] ddram_din;
	wire [7:0] ddram_be;
	integer errors = 0;
	integer gpu_beats = 0, vid_beats = 0;
	integer read_accepts = 0;
	integer write_accepts = 0;
	reg [28:0] first_read_addr = 0, second_read_addr = 0;
	reg write_seen = 0;
	reg write_interleaved = 0;
	integer frame_status_writes = 0;
	integer frame_detail_writes = 0;
	reg [63:0] last_frame_status = 0;
	reg [63:0] last_frame_detail = 0;

	am2r_ddr_arbiter dut(
		.clk(clk), .reset(reset),
		.frame_tick(frame_tick),
		.display_tick(display_tick),
		.native_frame(native_frame), .scanout_frame(scanout_frame),
		.gpu_burstcnt(gpu_burstcnt), .gpu_addr(gpu_addr), .gpu_din(gpu_din),
		.gpu_be(gpu_be), .gpu_rd(gpu_rd), .gpu_we(gpu_we),
		.gpu_busy(gpu_busy), .gpu_dout(gpu_dout), .gpu_dout_ready(gpu_ready),
		.vid_burstcnt(vid_burstcnt), .vid_addr(vid_addr), .vid_rd(vid_rd),
		.vid_busy(vid_busy), .vid_dout(vid_dout), .vid_dout_ready(vid_ready),
		.ddram_busy(ddram_busy), .ddram_burstcnt(ddram_burstcnt),
		.ddram_addr(ddram_addr), .ddram_dout(ddram_dout),
		.ddram_dout_ready(ddram_ready), .ddram_rd(ddram_rd),
		.ddram_din(ddram_din), .ddram_be(ddram_be), .ddram_we(ddram_we)
	);

	always #5 clk = ~clk;
	always @(posedge clk) begin
		if (gpu_ready) gpu_beats <= gpu_beats + 1;
		if (vid_ready) vid_beats <= vid_beats + 1;
		if (ddram_rd && !ddram_busy) begin
			if (read_accepts == 0) first_read_addr <= ddram_addr;
			if (read_accepts == 1) second_read_addr <= ddram_addr;
			read_accepts <= read_accepts + 1;
		end
		if (ddram_we && !ddram_busy && ddram_addr == gpu_addr &&
		    ddram_din == gpu_din && ddram_be == gpu_be) begin
			write_seen <= 1;
			write_accepts <= write_accepts + 1;
		end
		if (ddram_we && !ddram_busy && ddram_addr == 29'h047fe008) begin
			frame_status_writes <= frame_status_writes + 1;
			last_frame_status <= ddram_din;
		end
		if (ddram_we && !ddram_busy && ddram_addr == 29'h047fe009) begin
			frame_detail_writes <= frame_detail_writes + 1;
			last_frame_detail <= ddram_din;
		end
		if (dut.owner == 3 && ddram_rd)
			write_interleaved <= 1;
	end

	task return_beat(input [63:0] value);
		begin
			@(negedge clk); ddram_dout = value; ddram_ready = 1;
			@(negedge clk); ddram_ready = 0;
		end
	endtask

	initial begin
		repeat (3) @(posedge clk);
		reset = 0;
		@(negedge clk); gpu_rd = 1; vid_rd = 1;
		@(posedge clk);
		@(negedge clk); vid_rd = 0;
		return_beat(64'h1); return_beat(64'h2); return_beat(64'h3);
		wait (!gpu_busy);
		@(posedge clk); @(negedge clk); gpu_rd = 0;
		return_beat(64'h4); return_beat(64'h5);
		repeat (2) @(posedge clk);
		if (vid_beats != 3 || gpu_beats != 2 || read_accepts != 2 ||
		    first_read_addr != vid_addr || second_read_addr != gpu_addr)
			errors = errors + 1;

		// A three-beat GPU write must remain atomic even when native scanout
		// requests a read after the first accepted beat.
		gpu_burstcnt = 3;
		@(negedge clk); gpu_we = 1;
		@(posedge clk);
		@(negedge clk); vid_rd = 1;
		repeat (2) @(posedge clk);
		@(negedge clk); gpu_we = 0;
		@(posedge clk);
		if (!write_seen || write_accepts != 3 || write_interleaved)
			errors = errors + 1;
		// The waiting video request is granted immediately after the burst.
		@(posedge clk); @(negedge clk); vid_rd = 0;
		return_beat(64'h6); return_beat(64'h7); return_beat(64'h8);

		// The early pacing pulse publishes the heartbeat without prematurely
		// sampling presentation diagnostics.
		@(negedge clk); frame_tick = 1;
		repeat (4) @(negedge clk);
		frame_tick = 0;
		wait (frame_status_writes == 1);
		repeat (2) @(posedge clk);
		if (frame_detail_writes != 0)
			errors = errors + 1;

		// The real display boundary independently captures the native and scanout
		// frame IDs, even though the ARM pacing edge led it in time.
		@(negedge clk); display_tick = 1;
		repeat (4) @(negedge clk);
		display_tick = 0;
		wait (frame_detail_writes == 1);
		repeat (2) @(posedge clk);
		if (last_frame_status != 64'h56424c4b00000001)
			errors = errors + 1;
		if (last_frame_detail != 64'h1234567889abcdef)
			errors = errors + 1;

		if (errors == 0) $display("PASS: DDR arbiter priority, burst ownership, response routing, writes, and vblank heartbeat");
		else $fatal(1, "FAIL: %0d DDR arbiter errors", errors);
		$finish;
	end
endmodule
