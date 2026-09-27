`timescale 1ns/1ps
// Captured-job differential harness. Pixel expectations live in an independent
// Python renderer, not in this testbench. No game data is embedded here.
// SPDX-License-Identifier: GPL-2.0-or-later
module am2r_gpu_replay_tb;
    parameter integer MEMORY_WORDS = 131072;
    parameter integer MAX_CYCLES = 20000000;
    parameter integer READ_LATENCY = 2;
    parameter integer STALL_PERIOD = 11;
    parameter integer RESPONSE_GAP = 0;
    localparam integer MAX_REGIONS = 4096;
    localparam integer FRAME_WORDS = 38400;
    localparam [28:0] CONTROL_WORD = 32'h23ff0000 >> 3;
    localparam [28:0] COMMAND_WORD = 32'h23fe0000 >> 3;
    localparam [31:0] NATIVE_BASE = 32'h3a000100;
    reg clk = 0, reset = 1;
    always #10 clk = ~clk;
    reg ddram_busy = 0, ddram_dout_ready = 0;
    reg [63:0] ddram_dout = 0;
    wire [7:0] ddram_burstcnt, ddram_be;
    wire [28:0] ddram_addr;
    wire [63:0] ddram_din;
    wire ddram_rd, ddram_we;
    wire [31:0] native_frame;
    wire [1:0] native_buffer;
    reg [63:0] memory_data [0:MEMORY_WORDS-1];
    reg [63:0] control [0:10];
    reg [28:0] region_base [0:MAX_REGIONS-1];
    integer region_words [0:MAX_REGIONS-1];
    integer region_offset [0:MAX_REGIONS-1];
    reg [31:0] export_base [0:1023];
    integer export_bytes [0:1023];
    integer region_count, export_count, command_count, prior_native, has_initial, will_present;
    reg [63:0] initial_frame [0:FRAME_WORDS-1];
    integer cycles = 0, n, fd, status, address_tmp, words_tmp, offset_tmp;
    integer output_fd, export_index;
    string output_name;
    reg pending_read = 0, pending_write = 0;
    reg [28:0] read_address, write_address;
    integer read_remaining, write_remaining, read_delay;
    integer response_counter = 0;
    reg started = 0;
    reg [28:0] completed_base;
    integer capability_stall_cycles = 0;

    am2r_gpu dut (
        .clk(clk), .reset(reset), .ddram_busy(ddram_busy),
        .ddram_burstcnt(ddram_burstcnt), .ddram_addr(ddram_addr),
        .ddram_dout(ddram_dout), .ddram_dout_ready(ddram_dout_ready),
        .ddram_rd(ddram_rd), .ddram_din(ddram_din), .ddram_be(ddram_be),
        .ddram_we(ddram_we), .scan_buffer_valid(1'b0), .scan_buffer(2'b0),
        .scan_underflow_toggle(1'b0), .native_frame(native_frame),
        .native_buffer(native_buffer)
    );

    function automatic integer memory_index(input [28:0] address);
        integer i;
        begin
            memory_index = -1;
            for (i = 0; i < region_count; i = i + 1)
                if (address >= region_base[i] && address < region_base[i] + region_words[i])
                    memory_index = region_offset[i] + address - region_base[i];
        end
    endfunction

    function automatic [63:0] read_memory(input [28:0] address);
        integer index;
        begin
            if ((address >= CONTROL_WORD && address < CONTROL_WORD + 4) || address == CONTROL_WORD + 10)
                read_memory = control[address - CONTROL_WORD];
            else begin
                index = memory_index(address);
                if (index < 0 || index >= MEMORY_WORDS)
                    $fatal(1, "Uncaptured DDR read %h", {address, 3'b0});
                read_memory = memory_data[index];
            end
        end
    endfunction

    task automatic write_memory(input [28:0] address, input [63:0] data, input [7:0] enable);
        integer index, lane, i;
        reg permitted;
        begin
            if ((address >= CONTROL_WORD && address < CONTROL_WORD + 4) || address == CONTROL_WORD + 10) begin
                if (address == CONTROL_WORD + 3 && control[10] !== {dut.CAPABILITY_FEATURES, 32'h43473241})
                    $fatal(1, "Completion accepted before capability publication");
                if (address == CONTROL_WORD + 10 && (data !== {dut.CAPABILITY_FEATURES, 32'h43473241} || enable !== 8'hff))
                    $fatal(1, "Invalid capability publication");
                for (lane = 0; lane < 8; lane = lane + 1)
                    if (enable[lane]) control[address - CONTROL_WORD][lane*8 +: 8] = data[lane*8 +: 8];
            end else begin
                index = memory_index(address);
                if (index < 0 || index >= MEMORY_WORDS)
                    $fatal(1, "Unexpected DDR write %h", {address, 3'b0});
                for (lane = 0; lane < 8; lane = lane + 1) begin
                    if (enable[lane]) begin
                        permitted = will_present != 0 && address >= (NATIVE_BASE >> 3) &&
                            address < (NATIVE_BASE >> 3) + 3 * FRAME_WORDS;
                        for (i = 0; i < export_count; i = i + 1)
                            if ({address, 3'b0} + lane >= export_base[i] &&
                                {address, 3'b0} + lane < export_base[i] + export_bytes[i])
                                permitted = 1;
                        if (!permitted)
                            $fatal(1, "Unexpected DDR byte write %h lane%0d", {address, 3'b0}, lane);
                        memory_data[index][lane*8 +: 8] = data[lane*8 +: 8];
                    end
                end
            end
        end
    endtask

    // Independent Avalon memory service with configurable accepted-request
    // latency, request backpressure, and read-response gaps. Addresses/byte
    // enables are checked instead of silently returning zero for missing data.
    always @(posedge clk) begin
        cycles <= cycles + 1;
        if (STALL_PERIOD > 0) ddram_busy <= ((cycles % STALL_PERIOD) == STALL_PERIOD - 1);
        else ddram_busy <= 0;
        // Deliberately stall the new feature write, checking its accepted-
        // transaction contract independently of the regular periodic stalls.
        if (dut.state == dut.ST_CAPABILITY_WRITE) ddram_busy <= 1;
        if (dut.state == dut.ST_CAPABILITY_ACCEPT) begin
            capability_stall_cycles <= capability_stall_cycles + 1;
            ddram_busy <= capability_stall_cycles < 3;
            if (!ddram_we || ddram_addr != CONTROL_WORD + 10 ||
                ddram_din !== {dut.CAPABILITY_FEATURES, 32'h43473241} || ddram_be !== 8'hff || ddram_burstcnt != 1)
                $fatal(1, "Capability request changed under backpressure");
        end
        ddram_dout_ready <= 0;
        if (cycles > MAX_CYCLES) $fatal(1, "Replay cycle limit exceeded");
        if (ddram_we && !ddram_busy) begin
            if (ddram_burstcnt == 0 || ddram_burstcnt > 128)
                $fatal(1, "Invalid DDR write burst length");
            if (pending_write) begin
                write_memory(write_address, ddram_din, ddram_be);
                write_address <= write_address + 1;
                write_remaining <= write_remaining - 1;
                if (write_remaining == 1) pending_write <= 0;
            end else begin
                write_memory(ddram_addr, ddram_din, ddram_be);
                if (ddram_burstcnt > 1) begin
                    pending_write <= 1;
                    write_address <= ddram_addr + 1;
                    write_remaining <= ddram_burstcnt - 1;
                end
            end
        end
        if (ddram_rd && !ddram_busy && !pending_read) begin
            if (ddram_burstcnt == 0 || ddram_burstcnt > 128)
                $fatal(1, "Invalid DDR read burst length");
            pending_read <= 1;
            read_address <= ddram_addr;
            read_remaining <= ddram_burstcnt;
            read_delay <= READ_LATENCY;
            response_counter <= 0;
        end else if (pending_read) begin
            if (read_delay != 0) read_delay <= read_delay - 1;
            else if (RESPONSE_GAP != 0 && response_counter == RESPONSE_GAP) response_counter <= 0;
            else begin
                response_counter <= response_counter + 1;
                ddram_dout <= read_memory(read_address);
                ddram_dout_ready <= 1;
                read_address <= read_address + 1;
                read_remaining <= read_remaining - 1;
                if (read_remaining == 1) pending_read <= 0;
            end
        end
    end

    initial begin
        if (MEMORY_WORDS <= 0 || MEMORY_WORDS > 18000000 || MAX_CYCLES <= 0 ||
            READ_LATENCY < 0 || STALL_PERIOD < 0 || RESPONSE_GAP < 0)
            $fatal(1, "Invalid bounded replay parameters");
        fd = $fopen("mapping.txt", "r");
        if (!fd) $fatal(1, "Cannot open mapping.txt");
        status = $fscanf(fd, "%d %d %d %d %d %d\n", region_count, export_count, command_count, prior_native, has_initial, will_present);
        if (status != 6 || region_count < 1 || region_count > MAX_REGIONS ||
            export_count < 0 || export_count > 1024 || command_count < 1 || command_count > 1024 ||
            prior_native < 0 || prior_native > 2 || has_initial < 0 || has_initial > 1 ||
            will_present < 0 || will_present > 1)
            $fatal(1, "Invalid mapping header");
        for (n = 0; n < region_count; n = n + 1) begin
            status = $fscanf(fd, "%h %d %d\n", address_tmp, words_tmp, offset_tmp);
            if (status != 3 || words_tmp <= 0 || offset_tmp < 0 || offset_tmp + words_tmp > MEMORY_WORDS)
                $fatal(1, "Invalid memory mapping");
            region_base[n] = address_tmp;
            region_words[n] = words_tmp;
            region_offset[n] = offset_tmp;
        end
        for (n = 0; n < export_count; n = n + 1) begin
            status = $fscanf(fd, "%h %d\n", address_tmp, words_tmp);
            if (status != 2 || words_tmp <= 0 || words_tmp > MEMORY_WORDS * 8 ||
                (address_tmp & 3) != 0 || (words_tmp & 3) != 0)
                $fatal(1, "Invalid export mapping");
            export_base[n] = address_tmp;
            export_bytes[n] = words_tmp;
        end
        $fclose(fd);
        $readmemh("memory.hex", memory_data);
        for (n = 0; n < 11; n = n + 1) control[n] = 0;
        repeat (8) @(negedge clk);
        reset = 0;
        repeat (8) @(negedge clk);
        // Seed capture-time state deliberately. A cold reset's buffer zero is
        // not necessarily the source of opcode8 in a captured running job.
        dut.native_buffer = prior_native[1:0];
        dut.native_frame = 32'd100;
        if (has_initial != 0) begin
            $readmemh("initial.hex", initial_frame);
            for (n = 0; n < FRAME_WORDS; n = n + 1) begin
                dut.fb_even[n] = initial_frame[n][31:0];
                dut.fb_odd[n] = initial_frame[n][63:32];
            end
        end
        control[1] = {32'(command_count), 32'h23fe0000};
        control[2] = 0;
        control[0] = {32'd1, 32'h50473241};
        started = 1;
        wait (control[3][31:0] == 1);
        @(negedge clk);
        if (pending_write || native_frame != 100 + will_present)
            $fatal(1, "Incorrect frame publication count");
        if (!will_present && native_buffer != prior_native)
            $fatal(1, "No-present completion changed the native buffer");
        if (capability_stall_cycles < 3) $fatal(1, "Capability backpressure not exercised");
        completed_base = (NATIVE_BASE >> 3) + native_buffer * FRAME_WORDS;
        output_fd = $fopen("rtl-native.hex", "w");
        if (!output_fd) $fatal(1, "Cannot create native replay output");
        for (n = 0; n < FRAME_WORDS; n = n + 1)
            $fdisplay(output_fd, "%016h", read_memory(completed_base + n));
        $fclose(output_fd);
        output_fd = $fopen("rtl-bram.hex", "w");
        if (!output_fd) $fatal(1, "Cannot create BRAM replay output");
        for (n = 0; n < FRAME_WORDS; n = n + 1)
            $fdisplay(output_fd, "%016h", {dut.fb_odd[n], dut.fb_even[n]});
        $fclose(output_fd);
        for (export_index = 0; export_index < export_count; export_index = export_index + 1) begin
            output_name = $sformatf("rtl-export-%0d.hex", export_index);
            output_fd = $fopen(output_name, "w");
            if (!output_fd) $fatal(1, "Cannot create export replay output");
            for (n = 0; n < (export_bytes[export_index] + export_base[export_index][2:0] + 7) / 8; n = n + 1)
                $fdisplay(output_fd, "%016h", read_memory((export_base[export_index] >> 3) + n));
            $fclose(output_fd);
        end
        output_fd = $fopen("rtl-status.txt", "w");
        $fdisplay(output_fd, "%0d %0d %0d", cycles, control[3][63:32] & 32'h1fffffff, native_buffer);
        $fclose(output_fd);
        $display("PASS: captured GPU job completed; compare pixels with scalar reference");
        $finish;
    end
endmodule
