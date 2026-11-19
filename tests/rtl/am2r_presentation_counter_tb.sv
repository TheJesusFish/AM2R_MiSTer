`timescale 1ns/1ps
// Core-local telemetry: every active edge counted, coherent seqlock payload,
// immutable four-write record under stalls, and GPU/native grant exclusion.
module am2r_presentation_counter_tb;
    reg clk=0, reset=1, display_tick=0, frame_tick=0;
    reg [31:0] native_frame=0, scanout_frame=0;
    reg busy=0, vid_rd=0, gpu_rd=0, ready=0;
    wire gpu_busy, vid_busy, rd, we;
    wire [28:0] addr;
    wire [63:0] data;
    wire [7:0] be, count;
    reg [63:0] header=0, ids=0, counters=0;
    integer commits=0, reads=0, held_cycles=0;
    reg held=0;
    reg [28:0] held_addr;
    reg [63:0] held_data;
    reg [7:0] held_be;
    reg saw_odd=0, saw_ids=0, saw_counters=0;
    am2r_ddr_arbiter dut (
        .clk(clk),.reset(reset),.frame_tick(frame_tick),.display_tick(display_tick),
        .native_frame(native_frame),.scanout_frame(scanout_frame),
        .gpu_burstcnt(8'd2),.gpu_addr(29'h123),.gpu_din(64'd0),.gpu_be(8'hff),
        .gpu_rd(gpu_rd),.gpu_we(1'b0),.gpu_busy(gpu_busy),.gpu_dout(),.gpu_dout_ready(),
        .vid_burstcnt(8'd1),.vid_addr(29'h456),.vid_rd(vid_rd),
        .vid_busy(vid_busy),.vid_dout(),.vid_dout_ready(),
        .ddram_busy(busy),.ddram_burstcnt(count),.ddram_addr(addr),
        .ddram_dout(64'd0),.ddram_dout_ready(ready),.ddram_rd(rd),
        .ddram_din(data),.ddram_be(be),.ddram_we(we));
    always #5 clk=~clk;
    always @(posedge clk) begin
        if(reset) begin
            held<=0; saw_odd=0; saw_ids=0; saw_counters=0;
        end else begin
            if(held && (!we || addr!==held_addr || data!==held_data || be!==held_be || count!==1))
                $fatal(1,"presentation record changed under backpressure");
            held<=dut.owner==dut.PRESENT_STATUS && busy;
            if(dut.owner==dut.PRESENT_STATUS && busy) begin
                held_addr<=addr; held_data<=data; held_be<=be;
                held_cycles=held_cycles+1;
            end
            if(rd && !busy) begin
                reads=reads+1;
                if(dut.owner==dut.PRESENT_STATUS) $fatal(1,"read split presentation record");
                if(addr==29'h123 && dut.presentation_pending && dut.owner==dut.IDLE)
                    $fatal(1,"GPU granted during presentation reservation");
            end
            if(we && !busy) case(addr)
                29'h047fe00b: begin
                    if(data[31:0]!==32'h5352504e) $fatal(1,"presentation magic");
                    header=data;
                    if(data[32]) begin
                        saw_odd=1; saw_ids=0; saw_counters=0;
                    end else begin
                        if(!saw_odd || !saw_ids || !saw_counters)
                            $fatal(1,"even header before complete payload");
                        commits=commits+1;
                    end
                end
                29'h047fe00c: begin
                    if(!header[32] || !saw_odd) $fatal(1,"ID payload without odd header");
                    ids=data; saw_ids=1;
                end
                29'h047fe00d: begin
                    if(!header[32] || !saw_ids) $fatal(1,"counter payload without IDs");
                    counters=data; saw_counters=1;
                end
                default: begin end
            endcase
        end
    end
    task edge_id(input [31:0] id);
        begin
            @(negedge clk); scanout_frame=id; native_frame=id; display_tick=1;
            repeat(4) @(negedge clk); display_tick=0;
            repeat(4) @(negedge clk);
        end
    endtask
    task drain;
        begin
            wait(dut.owner==dut.IDLE && !dut.presentation_pending && !dut.frame_detail_pending);
            repeat(2) @(negedge clk);
        end
    endtask
    task check(input [31:0] rasters,id,repeats,skips);
        begin
            drain();
            if(header[32] || ids!=={id,rasters} || counters!=={skips,repeats})
                $fatal(1,"counter mismatch: IDs=%h counters=%h expected %h %h",ids,counters,{id,rasters},{skips,repeats});
        end
    endtask
    integer stage, old_commits;
    initial begin
        repeat(4) @(negedge clk); reset=0;
        edge_id(0); edge_id(0); check(2,0,0,0);
        edge_id(1); edge_id(2); edge_id(2); edge_id(5); check(6,5,1,2);
        // Stall every record beat; new edges must advance cumulative counters
        // without changing the already-frozen record or splitting transactions.
        for(stage=0;stage<4;stage=stage+1) begin
            fork
                edge_id(6+stage*3);
                begin
                    wait(dut.owner==dut.PRESENT_STATUS && dut.presentation_write_stage==stage);
                    @(negedge clk); busy=1; gpu_rd=1; vid_rd=1;
                    edge_id(7+stage*3); edge_id(8+stage*3);
                    @(negedge clk); busy=0;
                    wait(rd && !busy);
                    if(addr!==29'h456) $fatal(1,"native read lost priority after record");
					@(posedge clk);
                    @(negedge clk); vid_rd=0; gpu_rd=0; ready=1;
                    @(negedge clk); ready=0;
                end
            join
            drain();
            check(9+stage*3,8+stage*3,1,2);
        end
        // ID wrap is modulo32: ffffffff -> 1 skips only publication0.
        @(negedge clk); reset=1;
        repeat(4) @(negedge clk); reset=0;
        edge_id(32'hfffffffe); edge_id(32'hffffffff); edge_id(1); check(3,1,0,1);
        // Core reset during an odd record discards transaction state and old
        // counter baseline; the next new record replaces the interrupted one.
        fork
            edge_id(2);
            begin
                wait(dut.owner==dut.PRESENT_STATUS && dut.presentation_write_stage==2);
                @(negedge clk); busy=1; reset=1;
                repeat(4) @(negedge clk); busy=0; reset=0;
            end
        join
        edge_id(9); check(1,9,0,0);
		// A waiting GPU-only read must not be accepted in the IDLE
		// reservation bubble (the owner moves to the diagnostic writer).
		fork
			edge_id(10);
			begin
				wait(dut.frame_detail_pending);
				@(negedge clk); gpu_rd=1;
				wait(dut.owner==dut.PRESENT_STATUS);
				@(negedge clk); gpu_rd=0;
			end
		join
		check(2,10,0,0);
        if(held_cycles<16 || reads<4 || commits<7) $fatal(1,"coverage missing");
        $display("PASS: native presentation counters, startup/wrap/reset, coherent seqlock, all4 DDR stalls and grants");
        $finish;
    end
    initial begin #200000; $fatal(1,"presentation test timeout"); end
endmodule
