`timescale 1ns/1ps

module am2r_hps_video_measurement_tb;
    parameter OLD_MAPPING = 0;
    parameter MISSING_NOTIFICATION = 0;
    reg clk_video = 0;
    reg clk_100 = 0;
    always #20 clk_video = ~clk_video;
    always #5 clk_100 = ~clk_100;
    reg reset = 1;
    reg [1:0] standard = 0;
    reg signed [4:0] h_position = 0, v_position = 0, hscale = 0;
    reg hscale_enable = 0;
    wire ce_pix, hblank, vblank, hsync, vsync, native_pal, new_frame;
    wire [7:0] r, g, b;
    am2r_native_video native_source(
        .clk(clk_video), .reset(reset), .standard(standard), .frame_ready(1'b0),
        .frame_r(8'd0), .frame_g(8'd0), .frame_b(8'd0),
        .ce_pix(ce_pix), .hblank(hblank), .vblank(vblank), .hsync(hsync), .vsync(vsync),
        .new_frame(new_frame), .new_line(), .pace_tick(), .pal(native_pal),
        .r(r), .g(g), .b(b));
    wire raw_ce, raw_hblank, raw_vblank, raw_hsync, raw_vsync;
    am2r_crt_video crt(
        .clk(clk_video), .reset(reset), .ce_pix_in(ce_pix),
        .r_in(r), .g_in(g), .b_in(b), .hs_in(hsync), .hblank_in(hblank),
        .vs_in(vsync), .vblank_in(vblank), .h_position(h_position), .v_position(v_position),
        .hscale_enable(hscale_enable), .hscale(hscale),
        .ce_pix_out(raw_ce), .r_out(), .g_out(), .b_out(),
        .hs_out(raw_hsync), .hblank_out(raw_hblank), .vs_out(raw_vsync), .vblank_out(raw_vblank),
        .hscale_active());

    // The framework forwards the post-CRT timing. Gamma/scanline latency is
    // irrelevant to the native bypass; direct-video identity is tested on the
    // actual bus bits, not on any assumed relation to the native raster.
    tri [45:0] HPS_BUS;
    reg hdmi_vs = 0, field = 0;
    reg [2:0] host_control = 0;
    reg [15:0] host_data = 0, probe_reply = 0;
    reg probe_wait = 0, probe_wide = 0, direct_video = 0;
    reg [4:0] par_num = 0;
    wire [15:0] dout;
    wire [18:0] probe_command;
    wire [7:0] measured_bundle;
    assign HPS_BUS[45:38] = {field, hdmi_vs, clk_100, clk_video,
                            raw_ce, ~(raw_hblank | raw_vblank), raw_hsync, raw_vsync};
    assign HPS_BUS[35:33] = host_control;
    assign HPS_BUS[31:16] = host_data;
    am2r_hps_measurement_fixture #(.OLD_MAPPING(OLD_MAPPING), .MISSING_NOTIFICATION(MISSING_NOTIFICATION)) dut(
        .HPS_BUS(HPS_BUS), .clk_video(clk_video), .ce_pix(ce_pix),
        .hblank(hblank), .vblank(vblank), .hsync(hsync), .vsync(vsync), .native_pal(native_pal),
        .sim_direct_video(direct_video), .par_num(par_num), .dout(dout),
        .probe_reply(probe_reply), .probe_wait(probe_wait), .probe_wide(probe_wide),
        .probe_command(probe_command), .measured_bundle(measured_bundle));

    integer identity_checks = 0;
    always @(negedge clk_video) begin
        #1;
        if (!OLD_MAPPING) begin
            if (measured_bundle[7:4] !== HPS_BUS[45:42])
                $fatal(1, "measurement changed framework clocks/HDMI VS/field bits");
            if (direct_video) begin
                if (measured_bundle[3:0] !== HPS_BUS[41:38])
                    $fatal(1, "direct-video measurement bits differ from framework bus");
            end else if (measured_bundle[3:0] !== {ce_pix, ~(hblank | vblank), hsync, vsync})
                $fatal(1, "native measurement bits are not isolated");
            identity_checks = identity_checks + 1;
        end
        if (probe_command !== {host_control, host_data})
            $fatal(1, "host-to-core command/control direction broken");
        if ({HPS_BUS[37:36], HPS_BUS[32], HPS_BUS[15:0]} !== {probe_wait, clk_video, probe_wide, probe_reply})
            $fatal(1, "core-to-host reply/wait/clock/width direction broken");
    end

    task wait_frames(input integer count);
        integer seen;
        begin
            seen = 0;
            while (seen < count) begin
                @(posedge clk_video); #1;
                if (new_frame) seen = seen + 1;
            end
        end
    endtask

    task word(input integer index, output reg [15:0] value);
        begin
            @(negedge clk_video); par_num = index;
            repeat (2) @(posedge clk_video);
            #1; value = dout;
        end
    endtask
    task dword(input integer index, output reg [31:0] value);
        reg [15:0] low, high;
        begin word(index, low); word(index+1, high); value = {high, low}; end
    endtask
    task expect_native(input integer pal_mode);
        reg [31:0] width, height, hperiod, vperiod, frame_clocks;
        reg [15:0] repeat_count;
        integer expected_h, expected_v;
        begin
            expected_h = 4 * (pal_mode ? 429 : 427);
            expected_v = expected_h * (pal_mode ? 312 : 262);
            dword(2, width); dword(4, height);
            dword(6, hperiod); dword(8, vperiod); dword(19, frame_clocks);
            word(16, repeat_count);
            if (width !== 320 || height !== 240)
                $fatal(1, "native active dimensions: got %0dx%0d, expected 320x240", width, height);
            if (frame_clocks !== expected_v)
                $fatal(1, "native frame clocks got %0d expected %0d", frame_clocks, expected_v);
            // 25 MHz simulation video clock / 100 MHz reference, so the
            // framework's elapsed counters contain four ticks per video edge.
            if (hperiod < expected_h*4-2 || hperiod > expected_h*4 ||
                vperiod < expected_v*4-2 || vperiod > expected_v*4)
                $fatal(1, "native periods changed: H=%0d V=%0d, expected near %0d/%0d", hperiod, vperiod, expected_h*4, expected_v*4);
            if (repeat_count !== 4) $fatal(1, "native pixel repeat changed: %0d", repeat_count);
            $display("CHECK: native standard=%0d position=(%0d,%0d) HScale=%0d/%0d dimensions=%0dx%0d frame_clocks=%0d",
                standard, h_position, v_position, hscale_enable, hscale, width, height, frame_clocks);
        end
    endtask
    task adjustments(input integer pal_mode);
        begin
            h_position = -8; v_position = 7; hscale_enable = 0;
            wait_frames(3); expect_native(pal_mode);
            h_position = 7; v_position = -8;
            wait_frames(3); expect_native(pal_mode);
            h_position = -8; v_position = 7; hscale_enable = 1; hscale = -16;
            wait_frames(3); expect_native(pal_mode);
            h_position = 7; v_position = -8; hscale = 15;
            wait_frames(3); expect_native(pal_mode);
            h_position = 0; v_position = 0; hscale_enable = 0;
            wait_frames(3); expect_native(pal_mode);
        end
    endtask

    integer i;
    reg [15:0] initial_notification, current_notification;
    initial begin
        repeat (20) @(negedge clk_video);
        reset = 0;
        // Exercise both bus directions, both field/HDMI signals, then restore
        // progressive field=0 before collecting timing measurements.
        for (i = 0; i < 64; i = i + 1) begin
            @(posedge clk_video); #2;
            host_control = i; host_data = 16'h8143 ^ (i * 1237);
            probe_reply = 16'hc52a ^ (i * 751); probe_wait = i[0]; probe_wide = i[1];
            field = i[2]; hdmi_vs = i[3];
        end
        field = 0; hdmi_vs = 0;
        wait_frames(20); expect_native(0); word(1, initial_notification);
        if (!MISSING_NOTIFICATION) begin
            adjustments(0);
            standard = 1; // PAL60: same raster, no spurious resolution change.
            wait_frames(4); expect_native(0); word(1, current_notification);
            if (current_notification !== initial_notification)
                $fatal(1, "PAL60 caused a spurious resolution notification");
            adjustments(0);
        end
        standard = 2;
        wait_frames(20);
        word(1, current_notification);
        if (current_notification[7:0] == initial_notification[7:0])
            $fatal(1, "PAL transition notification missing despite different native periods");
        expect_native(1);
        initial_notification = current_notification;
        adjustments(1);
        standard = 0;
        wait_frames(20); word(1, current_notification);
        if (current_notification[7:0] == initial_notification[7:0])
            $fatal(1, "NTSC transition notification missing");
        expect_native(0);
        // The real timing inputs must survive both directions of the mode mux,
        // including the HScale continuous CE and its shifted sync/blanking.
        h_position = -8; v_position = 7; hscale_enable = 1; hscale = -16;
        wait_frames(3);
        for (i = 0; i < 128; i = i + 1) begin
            @(posedge clk_video); #2;
            direct_video = i[0]; hdmi_vs = i[1]; field = i[2];
        end
        direct_video = 1;
        wait_frames(2);
        hscale = 15; h_position = 7; v_position = -8;
        wait_frames(3);
        direct_video = 0; field = 0; hdmi_vs = 0;
        $display("PASS: HPS measurement native NTSC/PAL60/PAL, CRT position/HScale extremes, live notifications, direct-video identity and command directions (%0d identity checks)", identity_checks);
        $finish;
    end
    initial begin
        #(64'd3000000000);
        $fatal(1, "HPS measurement test timeout");
    end
endmodule
