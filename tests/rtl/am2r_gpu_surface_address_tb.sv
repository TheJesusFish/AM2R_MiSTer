`timescale 1ns/1ps

// Directed entry-state test of the production surface-store address path.
// The oracle retains the former unsigned17 pixel arithmetic and unsigned9
// issue-word truncation. No address result or transition is forced on a clock.
module am2r_gpu_surface_address_tb;
    reg clk = 0, reset = 1;
    always #5 clk = ~clk;
    wire [7:0] ddram_burstcnt, ddram_be;
    wire [28:0] ddram_addr;
    wire ddram_rd, ddram_we;
    wire [63:0] ddram_din;
    wire [31:0] native_frame;
    wire [1:0] native_buffer;
    am2r_gpu dut(
        .clk(clk), .reset(reset), .ddram_busy(1'b1),
        .ddram_dout(64'd0), .ddram_dout_ready(1'b0),
        .scan_buffer_valid(1'b0), .scan_buffer(2'd0),
        .hdmi_protect(4'd0), .scan_underflow_toggle(1'b0),
        .ddram_burstcnt(ddram_burstcnt), .ddram_addr(ddram_addr),
        .ddram_rd(ddram_rd), .ddram_din(ddram_din), .ddram_be(ddram_be),
        .ddram_we(ddram_we), .native_frame(native_frame), .native_buffer(native_buffer));

    integer cases_run = 0, stages = 0, blocked = 0, wraps = 0, resets = 0;
    integer guards[0:3], modes[0:3], branches[0:1];
    integer row_case, first, odd, width_case, word_index, read_case, n;
    reg [16:0] rows[0:9];
    reg [15:0] widths[0:7];
    reg [7:0] reads[0:3];

    task automatic stage_burst;
        input [16:0] row;
        input first_lane, pixel_odd;
        input [8:0] word;
        input [15:0] width;
        reg [16:0] origin;
        reg [15:0] base, base_plus1;
        begin
            origin = row - {16'd0, first_lane};
            base = origin[16:1];
            base_plus1 = base + 16'd1;
            @(negedge clk);
            dut.state = dut.ST_SURFACE_BURST;
            dut.surface_store = 1;
            dut.surface_row_linear = row;
            dut.surface_first_lane = first_lane;
            dut.surface_pixel_odd = pixel_odd;
            dut.surface_word_index = word;
            dut.surface_width = width;
            dut.surface_row_words = 9'd161;
            dut.surface_row_addr = 32'h29000004;
            dut.framebuffer_reads_issued = 8'ha5;
            dut.framebuffer_read_valid = 2'b11;
            dut.framebuffer_fifo_count = 4'd7;
            dut.framebuffer_fifo_read_ptr = 3'd5;
            dut.framebuffer_fifo_write_ptr = 3'd6;
            dut.fb_even_we = 0;
            dut.fb_odd_we = 0;
            dut.fb_even_read_address = 16'h1234;
            dut.fb_odd_read_address = 16'h5678;
            dut.ddram_rd = 1;
            dut.ddram_we = 1;
            @(posedge clk); #1;
            if (dut.state !== dut.ST_SURFACE_STORE_FILL ||
                dut.framebuffer_reads_issued !== 8'd0 ||
                dut.framebuffer_read_valid !== 2'd0 ||
                dut.framebuffer_fifo_count !== 4'd0 ||
                dut.framebuffer_fifo_read_ptr !== 3'd0 ||
                dut.framebuffer_fifo_write_ptr !== 3'd0 || ddram_rd || ddram_we)
                $fatal(1,"SURFACE_ADDRESS burst staging/control mismatch");
            if (dut.fb_even_read_address !== 16'h1234 || dut.fb_odd_read_address !== 16'h5678)
                $fatal(1,"SURFACE_ADDRESS issued a BRAM read during staging");
            if (dut.surface_read_base !== base || dut.surface_read_base_plus1 !== base_plus1)
                $fatal(1,"SURFACE_ADDRESS staged row bases mismatch");
            stages = stages + 1;
        end
    endtask

    task automatic check_issue;
        input [16:0] row;
        input first_lane, pixel_odd;
        input [8:0] word;
        input [7:0] issued;
        input [15:0] width;
        input stream, stop_issue;
        reg [8:0] old_word;
        reg [9:0] old_pixel, old_limit;
        reg [16:0] old_linear;
        reg [15:0] old_base, expected_even, expected_odd;
        reg valid0, valid1;
        begin
            old_word = word + {1'b0, issued};
            old_pixel = {old_word, 1'b0};
            old_limit = {1'b0, width[8:0]} + {9'd0, first_lane};
            old_linear = row + {7'd0, old_pixel} - {16'd0, first_lane};
            old_base = old_linear[16:1];
            valid0 = !(old_word == 0 && first_lane);
            valid1 = old_pixel + 10'd1 < old_limit;
            expected_even = pixel_odd ? (valid1 ? old_base + 16'd1 : 16'd0) :
                                       (valid0 ? old_base : 16'd0);
            expected_odd = pixel_odd ? (valid0 ? old_base : 16'd0) :
                                      (valid1 ? old_base : 16'd0);
            stage_burst(row, first_lane, pixel_odd, word, width);
            @(negedge clk);
            dut.state = stream ? dut.ST_SURFACE_STORE_STREAM : dut.ST_SURFACE_STORE_FILL;
            dut.framebuffer_reads_issued = issued;
            // The directed wrap cases intentionally exceed valid descriptor
            // geometry, proving that the original expression widths survive.
            dut.framebuffer_write_burst_length = stop_issue ? issued : 8'hff;
            dut.framebuffer_write_beats_left = 8'd2;
            dut.framebuffer_fifo_count = 0;
            dut.framebuffer_read_valid = 0;
            dut.surface_be_pipe0 = 8'ha5;
            @(posedge clk); #1;
            if (stop_issue) begin
                if (dut.framebuffer_reads_issued !== issued ||
                    dut.framebuffer_read_valid[0] !== 1'b0 ||
                    dut.fb_even_read_address !== 16'h1234 || dut.fb_odd_read_address !== 16'h5678)
                    $fatal(1,"SURFACE_ADDRESS issued beyond the burst guard");
                blocked = blocked + 1;
            end else begin
                if (dut.fb_even_read_address !== expected_even || dut.fb_odd_read_address !== expected_odd)
                    $fatal(1,"SURFACE_ADDRESS bank mismatch row=%h first=%0d odd=%0d word=%h issued=%h width=%h got=%h/%h expected=%h/%h",
                        row,first_lane,pixel_odd,word,issued,width,dut.fb_even_read_address,dut.fb_odd_read_address,
                        expected_even,expected_odd);
                if (dut.framebuffer_reads_issued !== issued + 8'd1 ||
                    dut.framebuffer_read_valid[0] !== 1'b1 ||
                    dut.surface_be_pipe0 !== {{4{valid1}}, {4{valid0}}})
                    $fatal(1,"SURFACE_ADDRESS issue count or lane-mask mismatch");
                guards[{valid1,valid0}] = guards[{valid1,valid0}] + 1;
                modes[{first_lane,pixel_odd}] = modes[{first_lane,pixel_odd}] + 1;
                branches[stream] = branches[stream] + 1;
                if ({1'b0,word} + {2'b0,issued} >= 10'd512) wraps = wraps + 1;
            end
            if (dut.fb_even_we || dut.fb_odd_we || ddram_rd)
                $fatal(1,"SURFACE_ADDRESS unexpected write/read side effect");
            cases_run = cases_run + 1;
        end
    endtask

    initial begin
        for (n=0;n<4;n=n+1) begin guards[n]=0; modes[n]=0; end
        branches[0]=0; branches[1]=0;
        rows[0]=17'd0; rows[1]=17'd1; rows[2]=17'd2; rows[3]=17'd319;
        rows[4]=17'd320; rows[5]=17'd76799; rows[6]=17'h0ffff;
        rows[7]=17'h10000; rows[8]=17'h1fffe; rows[9]=17'h1ffff;
        widths[0]=16'd0; widths[1]=16'd1; widths[2]=16'd2; widths[3]=16'd319;
        widths[4]=16'd320; widths[5]=16'd511; widths[6]=16'd512; widths[7]=16'hffff;
        reads[0]=8'd0; reads[1]=8'd1; reads[2]=8'd63; reads[3]=8'd254;
        repeat(4) @(posedge clk);
        @(negedge clk); reset=0;
        repeat(4) @(posedge clk);
        // Full unsigned9 issue-index coverage, both lane/parity decisions,
        // borrow/overflow row boundaries and width truncation boundaries.
        for(row_case=0;row_case<10;row_case=row_case+1)
            for(first=0;first<2;first=first+1)
                for(odd=0;odd<2;odd=odd+1)
                    for(word_index=0;word_index<512;word_index=word_index+1)
                        for(read_case=0;read_case<4;read_case=read_case+1) begin
                            width_case=(word_index+read_case+row_case)%8;
                            check_issue(rows[row_case],first[0],odd[0],word_index[8:0],reads[read_case],
                                widths[width_case],word_index[0],1'b0);
                        end
        for(first=0;first<2;first=first+1)
            for(odd=0;odd<2;odd=odd+1)
                for(width_case=0;width_case<8;width_case=width_case+1) begin
                    check_issue(17'd0,first[0],odd[0],9'd0,8'd0,widths[width_case],1'b0,1'b0);
                    check_issue(17'h1ffff,first[0],odd[0],9'd511,8'd1,widths[width_case],1'b1,1'b0);
                    check_issue(17'd321,first[0],odd[0],9'd64,8'd63,widths[width_case],1'b0,1'b1);
                    check_issue(17'd322,first[0],odd[0],9'd128,8'd0,widths[width_case],1'b1,1'b1);
                end
        // Reset an in-flight staged burst, then use a different row/lane base.
        // The real reset synchronizer is allowed to finish in-flight clocks.
        stage_burst(17'h1ffff,1'b1,1'b1,9'd511,16'd320);
        @(negedge clk); reset=1;
        repeat(4) @(posedge clk); #1;
        if (dut.state !== dut.ST_RESET || ddram_rd || ddram_we || dut.fb_even_we || dut.fb_odd_we)
            $fatal(1,"SURFACE_ADDRESS reset did not abort the burst");
        @(negedge clk); reset=0;
        repeat(4) @(posedge clk);
        check_issue(17'd320,1'b0,1'b0,9'd0,8'd0,16'd319,1'b0,1'b0);
        check_issue(17'd1,1'b1,1'b0,9'd64,8'd1,16'd320,1'b1,1'b0);
        resets=resets+1;
        for(n=0;n<4;n=n+1)
            if(!guards[n] || !modes[n]) $fatal(1,"SURFACE_ADDRESS missing lane/parity coverage %0d",n);
        if(!branches[0] || !branches[1] || !blocked || !wraps || !resets || stages != cases_run+1)
            $fatal(1,"SURFACE_ADDRESS incomplete staging/guard/wrap/reset coverage");
        $display("SURFACE_ADDRESS_PASS cases=%0d stages=%0d fill=%0d stream=%0d blocked=%0d wraps=%0d resets=%0d masks00=%0d masks01=%0d masks10=%0d masks11=%0d",
            cases_run,stages,branches[0],branches[1],blocked,wraps,resets,guards[0],guards[1],guards[2],guards[3]);
        $finish;
    end
    initial begin #10000000; $fatal(1,"SURFACE_ADDRESS timeout"); end
endmodule
