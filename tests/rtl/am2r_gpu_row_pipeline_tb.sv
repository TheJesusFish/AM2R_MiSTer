`timescale 1ns/1ps

// Runs unchanged against both the old operand registers and new partial
// products. Hierarchical setup isolates the existing pipeline entry states;
// full descriptor/DDR coverage is provided by the optional dual-DUT runner.
module am2r_gpu_row_pipeline_tb;
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

    integer cases_run = 0, consumed_cycles = 0, skipped_cases = 0;
    integer seen[0:3], bypass_seen[0:3];
    integer i, path, variant, row, stride_case;
    reg [31:0] strides[0:11];
    reg [31:0] random_state = 32'h1369bdf1;
    reg [31:0] sample_stride, sample_base, sample_uv;
    reg [63:0] sample_generic_uv;
    reg [15:0] sample_extent;

    function automatic [31:0] old_row_address;
        input [15:0] index;
        input [31:0] stride, base;
        reg [63:0] wide;
        begin
            // The OLD pipeline stores UV >>>16 in an UNSIGNED16 register.
            // Negative native UV must retain that truncation, not sign extend.
            wide = {48'd0,index} * {32'd0,stride} + {32'd0,base};
            old_row_address = wide[31:0];
        end
    endfunction

    function automatic [15:0] old_clamped_index;
        input [63:0] uv;
        input [15:0] extent;
        begin
            if (uv[63]) old_clamped_index = 0;
            else if (uv[63:32] >= {16'd0,extent}) old_clamped_index = extent - 16'd1;
            else old_clamped_index = uv[47:32];
        end
    endfunction

    task automatic check_path;
        input integer selected_path, branch;
        input [31:0] uv, stride, base;
        input [63:0] generic_uv;
        input [15:0] extent;
        reg [15:0] index;
        reg [31:0] expected;
        reg [5:0] address_state, next_state;
        integer n;
        begin
            index = selected_path == 2 ? old_clamped_index(generic_uv,extent) : uv[31:16];
            expected = old_row_address(index,stride,base);
            @(negedge clk);
            dut.src_base = base;
            dut.src_stride = stride;
            dut.v_start = selected_path == 1 ? ~uv : uv;
            dut.v_current = selected_path == 1 ? uv : ~uv;
            dut.u_start = 0;
            dut.u_current = 0;
            dut.u_next = 32'h10000;
            dut.u_step = 32'h10000;
            dut.v_step = 0;
            dut.u_y_step = 0;
            dut.v_x_step = 0;
            dut.u_min = 32'h80000000;
            dut.u_max = 32'h7fffffff;
            dut.v_min = 32'h80000000;
            dut.v_max = 32'h7fffffff;
            dut.blit_width = 4;
            dut.blit_height = 4;
            dut.blit_x = 0;
            dut.blit_y = (selected_path == 0 && branch != 0) ? 4 : 0;
            dut.dst_x = (selected_path == 1 && branch == 1) ? -1 : 0;
            dut.dst_y = 0;
            dut.solid_mode = 0;
            dut.affine_mode = selected_path == 1;
            dut.additive_mode = 0;
            dut.subtract_mode = 0;
            dut.axis_stream_setup = 2;
            dut.texture_cache_valid = 0;
            dut.source_row_addr = 32'hdbadc0de;
            dut.source_byte_addr = 0;
            dut.source_byte_addr_1 = 0;
            dut.fb_even_we = 0;
            dut.fb_odd_we = 0;
            dut.fb_even_read_address = 0;
            dut.fb_odd_read_address = 0;
            dut.ddram_rd = 0;
            dut.ddram_we = 0;
            dut.command_index = 0;
            dut.command_addr = 32'h23fe0000;
            dut.tint_r_current = 0;
            dut.tint_g_current = 0;
            dut.tint_b_current = 0;
            dut.tint_a_current = 0;
            dut.tint_r_step = 0;
            dut.tint_g_step = 0;
            dut.tint_b_step = 0;
            dut.tint_a_step = 0;
            dut.water_destination_start_y = 0;
            dut.water_row_index = 0;
            dut.water_row_destination_x = 0;
            dut.generic_textured = branch != 1;
            dut.generic_triangle = branch == 2;
            dut.generic_texture_width = 16'd8;
            dut.generic_texture_height = extent;
            dut.generic_texture_x = 0;
            dut.generic_uv[0] = 0;
            dut.generic_uv[1] = generic_uv;
            dut.generic_width = 4;
            dut.generic_height = 2;
            dut.generic_x = 0;
            dut.generic_y = 0;
            dut.generic_linear = 0;
            dut.generic_row_linear = 0;
            for (n=0;n<3;n=n+1) begin
                dut.generic_edge[n] = (n==0 && branch==2) ? -64'sd1 : 64'sd0;
                dut.generic_edge_row[n] = 0;
            end
            for (n=0;n<2;n=n+1) begin
                dut.generic_uv_row[n] = 0;
            end
            for (n=0;n<4;n=n+1) begin
                dut.generic_color[n] = 0;
                dut.generic_color_row[n] = 0;
                dut.generic_color_dx[n] = 0;
            end
            for (n=0;n<39;n=n+1) begin
                dut.generic_packet[n] = 0;
            end
            if (selected_path == 1 && branch == 2) begin
                dut.v_min = 32'h7fffffff;
            end
            case (selected_path)
                0: begin
                    dut.state = dut.ST_BLIT_ROW;
                    address_state = dut.ST_ROW_SOURCE_ADDRESS;
                    next_state = dut.ST_BLIT_PIXEL;
                end
                1: begin
                    dut.state = dut.ST_BLIT_PIXEL;
                    address_state = dut.ST_AFFINE_ROW_ADDRESS;
                    next_state = dut.ST_AFFINE_SOURCE_ADDRESS;
                end
                2: begin
                    dut.state = dut.ST_GENERIC;
                    dut.generic_state = dut.GP_PIXEL;
                    address_state = dut.ST_GENERIC;
                    next_state = dut.ST_GENERIC;
                end
                3: begin
                    dut.state = dut.ST_TILED_ROW_ADDRESS;
                    address_state = dut.ST_TILED_SOURCE_ADDRESS;
                    next_state = dut.ST_TILED_SOURCE_ACCEPT;
                end
            endcase
            @(posedge clk); #1;
            if (dut.source_row_addr !== 32'hdbadc0de)
                $fatal(1,"ROW_PIPELINE wrote row address one cycle early path=%0d",selected_path);
            if (branch != 0) begin
                if (selected_path==0 && dut.state !== dut.ST_CMD_ACCEPT)
                    $fatal(1,"ROW_PIPELINE finished-row branch changed");
                if (selected_path==1 && (dut.state !== dut.ST_BLIT_PIXEL || dut.blit_x !== 1))
                    $fatal(1,"ROW_PIPELINE clipped native branch changed");
                if (selected_path==2 && (dut.state !== dut.ST_GENERIC ||
                    dut.generic_state !== (branch==1 ? dut.GP_TINT : dut.GP_PIXEL)))
                    $fatal(1,"ROW_PIPELINE untextured/outside generic branch changed");
                bypass_seen[selected_path] = bypass_seen[selected_path]+1;
                skipped_cases = skipped_cases+1;
                consumed_cycles = consumed_cycles+1;
            end else begin
                if (dut.state !== address_state ||
                    (selected_path==2 && dut.generic_state !== dut.GP_ADDRESS))
                    $fatal(1,"ROW_PIPELINE setup cycle changed path=%0d state=%0d",selected_path,dut.state);
                @(posedge clk); #1;
                if (dut.source_row_addr !== expected)
                    $fatal(1,"ROW_PIPELINE row mismatch path=%0d index=%h stride=%h base=%h got=%h expected=%h",
                        selected_path,index,stride,base,dut.source_row_addr,expected);
                if (dut.state !== next_state ||
                    (selected_path==2 && dut.generic_state !== dut.GP_TEXTURE))
                    $fatal(1,"ROW_PIPELINE address cycle changed path=%0d",selected_path);
                seen[selected_path] = seen[selected_path]+1;
                consumed_cycles = consumed_cycles+2;
            end
            cases_run = cases_run+1;
        end
    endtask

    initial begin
        for(i=0;i<4;i=i+1) begin seen[i]=0; bypass_seen[i]=0; end
        strides[0]=32'h00010000; strides[1]=32'hffffffff;
        strides[2]=32'h80000000; strides[3]=32'hffff0000;
        strides[4]=32'h0000ffff; strides[5]=32'h00000000;
        strides[6]=32'h00000001; strides[7]=32'h00000004;
        strides[8]=32'h00000500; strides[9]=32'h12345678;
        strides[10]=32'h80000001; strides[11]=32'hffff0001;
        repeat(3) @(posedge clk);
        @(negedge clk); reset=0;
        repeat(4) @(posedge clk);
        // Every unsigned16 row, including the high half representing negative
        // 16.16 UV, with carry/overflow/high-stride boundary patterns.
        for(stride_case=0;stride_case<12;stride_case=stride_case+1) begin
            for(row=0;row<65536;row=row+1)
                check_path(0,0,{row[15:0],16'h7fff},strides[stride_case],
                    32'hffff0123+row,64'd0,16'd1);
        end
        // All four actual entry/address state pairs. Distinct v_start and
        // v_current catch selectors accidentally using the wrong native UV.
        for(path=0;path<4;path=path+1) begin
            for(i=0;i<512;i=i+1) begin
                random_state=random_state*32'd1664525+32'd1013904223; sample_stride=random_state;
                random_state=random_state*32'd1664525+32'd1013904223; sample_base=random_state;
                random_state=random_state*32'd1664525+32'd1013904223; sample_uv=random_state;
                sample_extent=i[15:0]+16'd1;
                case(i%5)
                    0: sample_generic_uv=64'hffffffffffffffff;
                    1: sample_generic_uv={32'h0001ffff,sample_uv};
                    2: sample_generic_uv={16'd0,sample_extent,sample_uv};
                    3: sample_generic_uv={32'd0,sample_uv};
                    4: sample_generic_uv={16'd0,sample_extent-16'd1,sample_uv};
                endcase
                check_path(path,0,sample_uv,sample_stride,sample_base,sample_generic_uv,sample_extent);
            end
        end
        // Zero extent follows the exact old arithmetic too, although packet
        // validation rejects it during normal descriptor submission.
        check_path(2,0,0,32'hffff0001,32'hffffffff,64'd0,16'd0);
        for(i=0;i<32;i=i+1) begin
            check_path(0,1,32'hffff0000,strides[i%12],32'hfedcba98,64'd0,16'd8);
            check_path(1,1,32'h00010000,strides[i%12],32'hfedcba98,64'd0,16'd8);
            check_path(1,2,32'h00010000,strides[i%12],32'hfedcba98,64'd0,16'd8);
            check_path(2,1,0,strides[i%12],32'hfedcba98,64'h0001000000000000,16'd8);
            check_path(2,2,0,strides[i%12],32'hfedcba98,64'h0001000000000000,16'd8);
            // A following producer must replace the speculative unused row.
            check_path(3,0,32'h76543210,strides[i%12],32'h01020304,64'd0,16'd8);
        end
        check_path(2,1,0,32'hffff0001,32'hffffffff,64'd0,16'd0);
        // Assert the real external reset after setup. Its existing internal
        // synchronizer may allow the in-flight row to commit before reset;
        // neither implementation may carry stale products into the next job.
        @(negedge clk);
        dut.state = dut.ST_BLIT_ROW;
        dut.blit_y = 0;
        dut.src_base = 32'h76543210;
        dut.src_stride = 32'hffff0001;
        dut.v_start = 32'hffff0000;
        @(posedge clk); #1;
        @(negedge clk); reset=1;
        repeat(4) @(posedge clk);
        @(negedge clk); reset=0;
        repeat(4) @(posedge clk);
        check_path(3,0,32'h80000000,32'h80000001,32'hffffffff,64'd0,16'd8);
        for(i=0;i<4;i=i+1)
            if(!seen[i] || (i<3 && !bypass_seen[i])) $fatal(1,"ROW_PIPELINE missing path/bypass coverage%0d",i);
        if(consumed_cycles !== 2*cases_run-skipped_cases) $fatal(1,"ROW_PIPELINE cycle count changed");
        $display("ROW_PIPELINE_PASS cases=%0d consumed_cycles=%0d skipped=%0d axis=%0d affine=%0d generic=%0d tiled=%0d",
            cases_run,consumed_cycles,skipped_cases,seen[0],seen[1],seen[2],seen[3]);
        $finish;
    end
    initial begin #100000000; $fatal(1,"ROW_PIPELINE timeout"); end
endmodule
