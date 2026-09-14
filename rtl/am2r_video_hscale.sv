//============================================================================
// AM2R analog CRT horizontal scaler
//
// Adapted from video_hscale in Arcade-IGSPGM_MiSTer.
// Upstream: https://github.com/MiSTer-devel/Arcade-IGSPGM_MiSTer
// SPDX-FileCopyrightText: 2026 Martin Donlon
// SPDX-License-Identifier: GPL-3.0-or-later
//============================================================================

module am2r_video_hscale(
	input                    clk,
	input                    reset,
	input                    enable,
	input signed [4:0]       scale,
	input signed [4:0]       offset,
	output reg               enabled = 0,
	input                    ce_pix_in,
	input       [7:0]        r_in,
	input       [7:0]        g_in,
	input       [7:0]        b_in,
	input                    hs_in,
	input                    hblank_in,
	input                    vblank_in,
	input                    vs_in,
	output reg  [7:0]        r_out = 0,
	output reg  [7:0]        g_out = 0,
	output reg  [7:0]        b_out = 0,
	output reg               hs_out = 0,
	output reg               hblank_out = 1,
	output reg               vs_out = 0,
	output reg               vblank_out = 1,
	output reg               debug_underrun = 0,
	output reg               debug_overflow = 0
);

	localparam [11:0] READ_LEAD = 12'd16;

	reg hblank_in_d = 0;
	reg vblank_in_d = 0;
	reg hs_in_d = 0;
	wire line_start = hblank_in && !hblank_in_d;

	reg [11:0] line_clock = 0;
	reg [11:0] measured_total = 0;
	reg [11:0] measured_hblank = 0;
	reg [9:0] measured_active_pixels = 0;
	reg [11:0] measured_hsync_start = 0;
	reg [11:0] measured_hsync_width = 0;
	reg [3:0] measured_clocks_per_pixel = 0;
	reg [11:0] measure_clock = 0;
	reg [9:0] measure_active_pixels = 0;
	reg [11:0] measure_hsync_start = 0;
	reg [3:0] measure_pixel_clock = 0;
	reg measure_pixel_seen = 0;

	reg [7:0] step = 0;
	reg [12:0] active_length = 0;
	reg [11:0] read_start = 0;
	reg [11:0] hsync_start = 0;
	reg [11:0] hsync_end = 0;
	reg [11:0] blank_clock = 0;
	reg [11:0] fixed_total = 0;

	wire signed [5:0] scale_signed = {scale[4], scale};
	wire [5:0] scale_magnitude = scale[4] ? -scale_signed : scale_signed;
	wire signed [9:0] step_signed =
		$signed({1'b0, measured_clocks_per_pixel, 4'b0}) +
		$signed({{4{scale_signed[5]}}, scale_signed});
	wire [7:0] step_next = step_signed[7:0];
	wire [17:0] active_product = measured_active_pixels * step_next;
	wire [15:0] scale_product = measured_active_pixels *
		{10'b0, scale_magnitude};
	wire signed [12:0] sync_position =
		$signed({1'b0, measured_hsync_start}) +
		$signed({2'b0, scale_product[15:5]}) +
		$signed({1'b0, measured_clocks_per_pixel}) * $signed(offset);

	always @(posedge clk) begin
		if (reset) begin
			vblank_in_d <= 0;
			enabled <= 0;
			step <= 0;
			active_length <= 0;
			read_start <= 0;
			hsync_start <= 0;
			hsync_end <= 0;
			blank_clock <= 0;
			fixed_total <= 0;
		end else begin
			vblank_in_d <= vblank_in;
			if (vblank_in && !vblank_in_d) begin
				enabled <= enable;
				fixed_total <= measured_total;
				step <= step_next;
				active_length <= active_product[16:4];
				read_start <= measured_hblank + READ_LEAD +
					(scale[4] ? scale_product[15:4] : 12'd0);
				hsync_start <= sync_position[11:0];
				hsync_end <= sync_position[11:0] + measured_hsync_width;
				blank_clock <= READ_LEAD +
					(scale[4] ? 12'd0 : scale_product[15:4]);
			end
		end
	end

	reg vblank_line = 1;
	reg vsync_line = 0;
	reg [8:0] write_index = 0;
	wire write_enable = ce_pix_in && !hblank_in;
	reg [12:0] active_count = 0;
	reg [8:0] read_index = 0;
	reg [7:0] accumulator = 0;
	reg reading_current_line = 0;
	wire read_active = |active_count;
	wire read_load = (line_clock == read_start) && !vblank_line;
	wire [23:0] read_pixel;

	am2r_video_line_ram #(.WIDTH(24), .WIDTHAD(7)) line_buffer(
		.clock(clk),
		.write_enable(write_enable),
		.write_address(write_index[6:0]),
		.write_data({r_in, g_in, b_in}),
		.read_address(read_index[6:0]),
		.read_data(read_pixel)
	);

	reg read_active_d = 0;
	reg hsync_d1 = 0;
	reg hsync_d2 = 0;

	always @(posedge clk) begin
		if (reset) begin
			hblank_in_d <= 0;
			hs_in_d <= 0;
			line_clock <= 0;
			measured_total <= 0;
			measured_hblank <= 0;
			measured_active_pixels <= 0;
			measured_hsync_start <= 0;
			measured_hsync_width <= 0;
			measured_clocks_per_pixel <= 0;
			measure_clock <= 0;
			measure_active_pixels <= 0;
			measure_hsync_start <= 0;
			measure_pixel_clock <= 0;
			measure_pixel_seen <= 0;
			vblank_line <= 1;
			vsync_line <= 0;
			write_index <= 0;
			active_count <= 0;
			read_index <= 0;
			accumulator <= 0;
			reading_current_line <= 0;
			read_active_d <= 0;
			hsync_d1 <= 0;
			hsync_d2 <= 0;
			r_out <= 0;
			g_out <= 0;
			b_out <= 0;
			hs_out <= 0;
			hblank_out <= 1;
			vs_out <= 0;
			vblank_out <= 1;
			debug_underrun <= 0;
			debug_overflow <= 0;
		end else begin
			hblank_in_d <= hblank_in;
			hs_in_d <= hs_in;

			if (line_start) begin
				measure_clock <= 0;
				measured_total <= measure_clock + 1'b1;
				measured_active_pixels <= measure_active_pixels;
				measure_active_pixels <= 0;
			end else begin
				measure_clock <= measure_clock + 1'b1;
				if (write_enable)
					measure_active_pixels <= measure_active_pixels + 1'b1;
			end
			if (hblank_in_d && !hblank_in)
				measured_hblank <= measure_clock;
			if (hs_in && !hs_in_d) begin
				measured_hsync_start <= measure_clock;
				measure_hsync_start <= measure_clock;
			end
			if (!hs_in && hs_in_d)
				measured_hsync_width <= measure_clock - measure_hsync_start;
			if (ce_pix_in) begin
				if (measure_pixel_seen)
					measured_clocks_per_pixel <= measure_pixel_clock + 1'b1;
				measure_pixel_clock <= 0;
				measure_pixel_seen <= 1;
			end else begin
				measure_pixel_clock <= measure_pixel_clock + 1'b1;
			end

			if (line_start) begin
				line_clock <= 0;
				write_index <= 0;
				reading_current_line <= 0;
				vblank_line <= vblank_in;
				vsync_line <= vs_in;
			end else begin
				line_clock <= (fixed_total && line_clock >= fixed_total - 1'b1) ?
					0 : line_clock + 1'b1;
			end

			if (line_clock == blank_clock) begin
				vblank_out <= vblank_line;
				vs_out <= vsync_line;
			end

			if (write_enable)
				write_index <= write_index + 1'b1;

			if (read_load) begin
				active_count <= active_length;
				read_index <= 0;
				accumulator <= 0;
				reading_current_line <= 1;
			end else if (read_active) begin
				active_count <= active_count - 1'b1;
				if (accumulator + 8'd16 >= step) begin
					accumulator <= accumulator + 8'd16 - step;
					read_index <= read_index + 1'b1;
				end else begin
					accumulator <= accumulator + 8'd16;
				end
			end

			if (enabled && reading_current_line && read_active && read_index >= write_index)
				debug_underrun <= 1;
			if (enabled && reading_current_line && read_active &&
				write_index >= read_index && ((write_index - read_index) >= 9'd128))
				debug_overflow <= 1;

			read_active_d <= read_active;
			hsync_d1 <= line_clock >= hsync_start && line_clock < hsync_end;
			hsync_d2 <= hsync_d1;
			{r_out, g_out, b_out} <= read_pixel;
			hblank_out <= !read_active_d;
			hs_out <= hsync_d2;
		end
	end

endmodule
