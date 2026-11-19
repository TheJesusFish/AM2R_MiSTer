`timescale 1ns/1ps
// Constant additive-fill entry, independent RGBA oracle, and row drain.
// No game data, inferred pixel masks, or production blend helpers in the oracle.
// SPDX-License-Identifier: GPL-2.0-or-later
module am2r_gpu_solid_stream_tb;
    parameter integer EXPECT_FAST = 1;
    reg clk = 0, reset = 1;
    always #5 clk = ~clk;
    am2r_gpu dut (
        .clk(clk), .reset(reset), .ddram_busy(1'b1),
        .ddram_dout(64'd0), .ddram_dout_ready(1'b0),
        .scan_buffer_valid(1'b0), .scan_buffer(2'd0),
        .hdmi_protect(4'd0), .scan_underflow_toggle(1'b0),
        .ddram_burstcnt(), .ddram_addr(), .ddram_rd(), .ddram_din(),
        .ddram_be(), .ddram_we(), .native_frame(), .native_buffer());
    reg [31:0] expected_even[0:38399], expected_odd[0:38399];
    integer alpha, variant, i, n, rows, cycles, total_cycles = 0;
    integer cases_run = 0, routing_cases = 0, writes, expected_writes;
    integer full_frame_cycles = 0;
    reg [31:0] source;
    reg [63:0] flags;

    function automatic [31:0] pattern(input integer at, input integer salt);
        pattern = {8'((at * 11 + salt) % 256), 8'((at * 29 + salt) % 256),
                   8'((at * 73 + salt) % 256), 8'((at * 37 + salt) % 256)};
    endfunction

    function automatic [31:0] additive(input [31:0] src, input [31:0] dst);
        integer c, value;
        begin
            for (c = 0; c < 4; c = c + 1) begin
                value = dst[c*8 +: 8] + (c == 3 ? src[31:24] :
                        (src[c*8 +: 8] * src[31:24]) / 255);
                additive[c*8 +: 8] = value > 255 ? 8'hff : value[7:0];
            end
        end
    endfunction

    task automatic enter_fill(input [31:0] pixel, input [63:0] w0,
                              input [31:0] xy, input [63:0] gradient);
        begin
            @(negedge clk);
            dut.fb_even_we = 0;
            dut.fb_odd_we = 0;
            dut.ddram_rd = 0;
            dut.ddram_we = 0;
            for (n = 0; n < 8; n = n + 1) dut.descriptor[n] = 0;
            dut.descriptor[0] = w0;
            dut.descriptor[1] = {32'd0, pixel};
            dut.descriptor[2] = {32'd0, xy};
            dut.descriptor[7] = gradient;
            dut.command_index = 0;
            dut.command_addr = 32'h23fe0000;
            dut.state = dut.ST_CMD_DECODE;
            @(posedge clk); #1;
        end
    endtask

    task automatic check_route(input [63:0] w0, input [31:0] xy,
                               input [63:0] gradient, input fast);
        begin
            enter_fill(32'h8c3b719d, w0, xy, gradient);
            if (dut.state !== (fast ? dut.ST_WATER_SOURCE_DATA : dut.ST_BLIT_ROW))
                $fatal(1, "SOLID_STREAM eligibility mismatch w0=%h xy=%h gradient=%h", w0, xy, gradient);
            routing_cases = routing_cases + 1;
            @(negedge clk); dut.state = dut.ST_POLL_DELAY;
        end
    endtask

    task automatic run_fill(input [31:0] pixel, input integer height, input initialize);
        reg fast;
        integer at, timeout_cycles;
        begin
            // Full descriptor eligibility is tested before shortening most
            // fixtures to three rows. Alpha140/edge cases use all240 rows.
            @(negedge clk);
            if (initialize) begin
                for (at = 0; at < 38400; at = at + 1) begin
                    expected_even[at] = pattern(at * 2, pixel[31:24]);
                    expected_odd[at] = pattern(at * 2 + 1, pixel[7:0]);
                    dut.fb_even[at] = expected_even[at];
                    dut.fb_odd[at] = expected_odd[at];
                end
            end
            for (at = 0; at < height * 160; at = at + 1) begin
                expected_even[at] = additive(pixel, expected_even[at]);
                expected_odd[at] = additive(pixel, expected_odd[at]);
            end
            enter_fill(pixel, 64'd3 | 64'h100 | (64'd320 << 16) | (64'd240 << 32), 0, 0);
            fast = EXPECT_FAST != 0 || pixel[31:24] == 255;
            if (dut.state !== (fast ? dut.ST_WATER_SOURCE_DATA : dut.ST_BLIT_ROW))
                $fatal(1, "SOLID_STREAM alpha routing mismatch alpha=%0d", pixel[31:24]);
            @(negedge clk);
            if (fast) dut.water_row_count = height;
            else dut.blit_height = height;
            cycles = 0; writes = 0;
            timeout_cycles = height * 1000 + 20;
            while (dut.state != dut.ST_CMD_ACCEPT) begin
                @(posedge clk);
                if (dut.fb_even_we) begin
                    if (dut.fb_even_write_address >= height * 160)
                        $fatal(1, "SOLID_STREAM even write out of bounds");
                    writes = writes + 1;
                end
                if (dut.fb_odd_we) begin
                    if (dut.fb_odd_write_address >= height * 160)
                        $fatal(1, "SOLID_STREAM odd write out of bounds");
                    writes = writes + 1;
                end
                #1; cycles = cycles + 1;
                if (cycles > timeout_cycles) $fatal(1, "SOLID_STREAM completion timeout");
            end
            // Completion must be AFTER the final physical BRAM write, not
            // just after the last input pair was issued into the pipeline.
            for (at = 0; at < 38400; at = at + 1)
                if (dut.fb_even[at] !== expected_even[at] || dut.fb_odd[at] !== expected_odd[at])
                    $fatal(1, "SOLID_STREAM RGBA mismatch alpha=%0d at=%0d even=%h/%h odd=%h/%h",
                        pixel[31:24], at, dut.fb_even[at], expected_even[at], dut.fb_odd[at], expected_odd[at]);
            expected_writes = pixel[31:24] == 0 ? 0 : height * 320;
            if (writes != expected_writes) $fatal(1, "SOLID_STREAM write count mismatch %0d/%0d", writes, expected_writes);
            if (fast && cycles > height * 169 + 1)
                $fatal(1, "SOLID_STREAM pipeline performance regression %0d", cycles);
            if (height == 240 && pixel[31:24] == 140) full_frame_cycles = cycles;
            total_cycles = total_cycles + cycles;
            cases_run = cases_run + 1;
        end
    endtask

    initial begin
        repeat (3) @(posedge clk);
        @(negedge clk); reset = 0;
        repeat (4) @(posedge clk);
        flags = 64'd3 | 64'h100 | (64'd320 << 16) | (64'd240 << 32);
        // Negative controls retain scalar semantics for geometry, gradients,
        // normal/subtractive blending and invalid combined blend flags.
        check_route(flags, 1, 0, 0);
        check_route(flags, 32'h00010000, 0, 0);
        check_route(flags, 32'h0000ffff, 0, 0);
        check_route(flags, 32'hffff0000, 0, 0);
        check_route(flags - (64'd1 << 16), 0, 0, 0);
        check_route(flags - (64'd1 << 32), 0, 0, 0);
        check_route(flags + (64'd1 << 16), 0, 0, 0);
        check_route(flags + (64'd1 << 32), 0, 0, 0);
        check_route(flags & ~(64'hffff << 16), 0, 0, 0);
        check_route(flags & ~(64'hffff << 32), 0, 0, 0);
        for (i = 0; i < 4; i = i + 1) check_route(flags, 0, 64'd1 << (16*i), 0);
        check_route(flags & ~64'h100, 0, 0, 0);
        check_route((flags & ~64'h100) | 64'h200, 0, 0, 0);
        check_route(flags | 64'h200, 0, 0, 0);
        for (alpha = 0; alpha < 256; alpha = alpha + 1)
            for (variant = 0; variant < 2; variant = variant + 1) begin
                source = {alpha[7:0], variant ? 24'h3b719d : 24'hfefd01};
                rows = (alpha == 0 || alpha == 1 || alpha == 140 || alpha == 255) && variant == 0 ? 240 : 3;
                run_fill(source, rows, 1);
            end
        // Sequential low-alpha overlapping fills expose stale background,
        // stale mode, premature command completion and hidden alpha errors
        // without relying on eight white flashes saturating the final output.
        for (i = 0; i < 16; i = i + 1)
            run_fill({8'(i % 2 ? 7 : 11), 24'h174169}, 3, i == 0);
        // Reset during an active pipeline must discard in-flight ownership.
        enter_fill(32'h8cffffff, flags, 0, 0);
        repeat (21) @(posedge clk);
        @(negedge clk); reset = 1;
        repeat (4) @(posedge clk);
        @(negedge clk); reset = 0;
        repeat (4) @(posedge clk);
        run_fill(32'h0164cdef, 240, 1);
        $display("SOLID_STREAM_PASS cases=%0d routing=%0d cycles=%0d full_alpha140=%0d reset=1", cases_run, routing_cases, total_cycles, full_frame_cycles);
        $finish;
    end
    initial begin #100000000; $fatal(1, "SOLID_STREAM wall timeout"); end
endmodule
