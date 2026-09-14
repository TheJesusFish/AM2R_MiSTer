//============================================================================
// AM2R CRT horizontal-scaler line memory
//
// The synthesis implementation is derived from Arcade-IGSPGM_MiSTer's
// dualport_ram_unreg by Martin Donlon.
// Upstream: https://github.com/MiSTer-devel/Arcade-IGSPGM_MiSTer
// SPDX-FileCopyrightText: 2026 Martin Donlon
// SPDX-License-Identifier: GPL-3.0-or-later
//============================================================================

module am2r_video_line_ram #(
	parameter integer WIDTH = 24,
	parameter integer WIDTHAD = 7
)(
	input                       clock,
	input                       write_enable,
	input       [WIDTHAD-1:0]   write_address,
	input       [WIDTH-1:0]     write_data,
	input       [WIDTHAD-1:0]   read_address,
	output      [WIDTH-1:0]     read_data
);

`ifndef SYNTHESIS
	reg [WIDTH-1:0] memory[0:(1<<WIDTHAD)-1] /* verilator public_flat */;
	reg [WIDTH-1:0] read_q = 0;
	assign read_data = read_q;
	always @(posedge clock) begin
		if (write_enable)
			memory[write_address] <= write_data;
		read_q <= memory[read_address];
	end
`else
	altsyncram line_memory (
		.address_a(write_address),
		.address_b(read_address),
		.clock0(clock),
		.clock1(clock),
		.data_a(write_data),
		.data_b({WIDTH{1'b0}}),
		.wren_a(write_enable),
		.wren_b(1'b0),
		.q_a(),
		.q_b(read_data),
		.aclr0(1'b0),
		.aclr1(1'b0),
		.addressstall_a(1'b0),
		.addressstall_b(1'b0),
		.byteena_a(1'b1),
		.byteena_b(1'b1),
		.clocken0(1'b1),
		.clocken1(1'b1),
		.clocken2(1'b1),
		.clocken3(1'b1),
		.eccstatus(),
		.rden_a(1'b1),
		.rden_b(1'b1)
	);
	defparam
		line_memory.address_reg_b = "CLOCK1",
		line_memory.clock_enable_input_a = "BYPASS",
		line_memory.clock_enable_input_b = "BYPASS",
		line_memory.clock_enable_output_a = "BYPASS",
		line_memory.clock_enable_output_b = "BYPASS",
		line_memory.indata_reg_b = "CLOCK1",
		line_memory.intended_device_family = "Cyclone V",
		line_memory.lpm_type = "altsyncram",
		line_memory.numwords_a = (1 << WIDTHAD),
		line_memory.numwords_b = (1 << WIDTHAD),
		line_memory.operation_mode = "BIDIR_DUAL_PORT",
		line_memory.outdata_aclr_a = "NONE",
		line_memory.outdata_aclr_b = "NONE",
		line_memory.outdata_reg_a = "UNREGISTERED",
		line_memory.outdata_reg_b = "UNREGISTERED",
		line_memory.power_up_uninitialized = "FALSE",
		line_memory.read_during_write_mode_port_a = "NEW_DATA_NO_NBE_READ",
		line_memory.read_during_write_mode_port_b = "NEW_DATA_NO_NBE_READ",
		line_memory.widthad_a = WIDTHAD,
		line_memory.widthad_b = WIDTHAD,
		line_memory.width_a = WIDTH,
		line_memory.width_b = WIDTH,
		line_memory.width_byteena_a = 1,
		line_memory.width_byteena_b = 1,
		line_memory.wrcontrol_wraddress_reg_b = "CLOCK1";
`endif

endmodule
