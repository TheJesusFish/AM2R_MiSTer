`timescale 1ns/1ps
// DDR outlives an RBF and an HPS runner. This tests the host's ownership
// contract, not an implicit FPGA "already completed" check: valid stale jobs
// DO replay after FPGA reset; retired jobs do not; fresh sequences still run.
module am2r_gpu_restart_tb #(
    parameter integer READ_LATENCY=2, STALL_PERIOD=0
);
    localparam [28:0] CONTROL_WORD=32'h23ff0000>>3;
    localparam [28:0] COMMAND_WORD=32'h23fe0000>>3;
    reg clk=0, reset=1, ready=0, busy=0;
    reg [63:0] dout=0;
    wire [7:0] burst, be;
    wire [28:0] address;
    wire [63:0] din;
    wire rd, wr;
    wire [31:0] frame;
    wire [1:0] buffer;
    reg [63:0] control[0:10], commands[0:15];
    reg pending=0;
    reg [28:0] read_address;
    integer left=0, delay=0, completions=0, decodes=0, cycles=0, i;

    am2r_gpu dut(.clk(clk),.reset(reset),.ddram_busy(busy),
        .ddram_burstcnt(burst),.ddram_addr(address),.ddram_dout(dout),
        .ddram_dout_ready(ready),.ddram_rd(rd),.ddram_din(din),.ddram_be(be),
        .ddram_we(wr),.scan_buffer_valid(1'b0),.scan_buffer(2'b0),.hdmi_protect(4'b0),
        .scan_underflow_toggle(1'b0),.native_frame(frame),.native_buffer(buffer));
    always #5 clk=~clk;
    always @(posedge clk) begin
        cycles=cycles+1;
        busy<=STALL_PERIOD>0 ? cycles%STALL_PERIOD==1 : 0;
        ready<=0;
        if(dut.state==dut.ST_CMD_DECODE) decodes=decodes+1;
        if(wr && !busy) begin
            if(address<CONTROL_WORD || address>CONTROL_WORD+10)
                $fatal(1,"Unexpected write during no-present restart test");
            if(be!==8'hff || burst!=1)$fatal(1,"Invalid mailbox write");
            control[address-CONTROL_WORD]<=din;
            if(address==CONTROL_WORD+3) completions=completions+1;
        end
        if(rd && !busy && !pending) begin
            pending<=1; read_address<=address; left<=burst; delay<=READ_LATENCY;
        end else if(pending) begin
            if(delay) delay<=delay-1;
            else begin
                if(read_address>=CONTROL_WORD && read_address<=CONTROL_WORD+10)
                    dout<=control[read_address-CONTROL_WORD];
                else if(read_address>=COMMAND_WORD && read_address<COMMAND_WORD+16)
                    dout<=commands[read_address-COMMAND_WORD];
                else $fatal(1,"Unexpected DDR read");
                ready<=1; read_address<=read_address+1; left<=left-1;
                if(left==1)pending<=0;
            end
        end
    end

    task automatic pulse_fpga_reset;
        begin
            @(negedge clk); reset=1;
            repeat(8)@(negedge clk);
            reset=0;
        end
    endtask
    task automatic expect_completion(input integer count, decode_count, sequence_value);
        begin
            wait(completions==count);
            repeat(16)@(negedge clk);
            if(control[3][31:0]!=sequence_value || decodes!=decode_count)
                $fatal(1,"Incorrect completion/descriptor count");
            if(frame!=0 || buffer!=0)$fatal(1,"No-present restart changed publication");
        end
    endtask
    task automatic publish_next;
        reg [31:0] next_sequence;
        begin
            // Simulate a fresh runner: retain DDR completion, discard all
            // process-local sequence state, then publish completion+1.
            next_sequence=control[3][31:0]+1;
            @(negedge clk);
            control[0][31:0]=0;
            control[3]=0;
            control[0][63:32]=next_sequence;
            @(negedge clk);
            control[0][31:0]=32'h50473241;
        end
    endtask

    initial begin
        for(i=0;i<11;i=i+1)control[i]=0;
        for(i=0;i<16;i=i+1)commands[i]=0;
        commands[0]=64'h00000001_0001000e;
        commands[1]=32'h00517395;
        commands[8]=12;
        control[1]={32'd2,32'h23fe0000};
        control[0]={32'd7,32'h50473241};
        repeat(8)@(negedge clk);
        reset=0;
        expect_completion(1,2,7);

        // DDR survives FPGA replacement. The protocol intentionally accepts
        // this retained command again because reset last_sequence is zero.
        pulse_fpga_reset();
        expect_completion(2,4,7);

        // Host retirement happens only after producer stopped and equality
        // submitted==completed. Sequence/completion are deliberately retained.
        control[0][31:0]=0;
        pulse_fpga_reset();
        repeat(9000)@(negedge clk);
        if(completions!=2 || decodes!=4 || control[3][31:0]!=7)
            $fatal(1,"Retired mailbox replayed or lost its sequence");
        publish_next();
        expect_completion(3,6,8);

        // HPS-only restart: do not pulse FPGA reset. Same retired mailbox and
        // completion+1 rule must work with a nonzero FPGA last_sequence.
        control[0][31:0]=0;
        repeat(9000)@(negedge clk);
        if(completions!=3 || decodes!=6)$fatal(1,"HPS-only retirement replayed");
        publish_next();
        expect_completion(4,8,9);
        repeat(9000)@(negedge clk);
        if(completions!=4 || decodes!=8)$fatal(1,"Completed live sequence replayed");
        $display("PASS GPU restart: stale replay characterized; retired mailbox survives FPGA/HPS resets; fresh sequences execute");
        $finish;
    end
    initial begin #2000000; $fatal(1,"Restart probe timeout"); end
endmodule
