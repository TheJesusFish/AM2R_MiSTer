`timescale 1ns/1ps

// General render-target DMA contract. This is deliberately independent of
// the scene renderer: compare every BRAM pixel and every DDR byte, including
// odd source lanes, odd local x, row padding, and untouched edge lanes.
module am2r_gpu_surface_tb;
	localparam [28:0] CONTROL_WORD = 32'h23ff0000 >> 3;
	localparam [28:0] COMMAND_WORD = 32'h23fe0000 >> 3;
	localparam [31:0] SOURCE_BASE = 32'h28000000;
	localparam [31:0] DEST_BASE = 32'h29000000;
	localparam [31:0] PACKET_BASE = 32'h2a000000;
	localparam integer FB_WORDS = 320 * 240 / 2;
	localparam integer DDR_WORDS = 65536;
	reg clk = 0, reset = 1;
	reg force_busy = 0, scan_underflow_toggle = 0;
	reg ddram_busy = 0, ddram_dout_ready = 0;
	reg [63:0] ddram_dout = 0;
	wire [7:0] ddram_burstcnt, ddram_be;
	wire [28:0] ddram_addr;
	wire [63:0] ddram_din;
	wire ddram_rd, ddram_we;
	wire [31:0] native_frame;
	wire [1:0] native_buffer;
	reg [63:0] control [0:10];
	reg [63:0] commands [0:31];
	reg [63:0] packet [0:63];
	reg [63:0] source [0:DDR_WORDS-1];
	reg [63:0] destination [0:DDR_WORDS-1];
	reg [63:0] expected_destination [0:DDR_WORDS-1];
	reg [31:0] expected_fb [0:76799];
	reg pending_read = 0, pending_write = 0;
	reg [28:0] read_address, write_address;
	reg [7:0] read_remaining, write_remaining;
	integer read_delay = 0;
	integer cycles = 0, errors = 0, cases_run = 0, schedule = 0;
	integer source_reads = 0, target_writes = 0;
	integer rect_clear_cycles = 0;
	integer axis_stream_beats = 0, axis_stream_streak = 0, axis_stream_max_streak = 0;
	integer gather_pairs = 0, gather_singles = 0, gather_last_issue = -100, gather_shortest_gap = 100;
	integer cross_line_prefetches = 0;
	reg check_cross_line_prefetch = 0;
	reg [15:0] cross_even_word, cross_odd_word;
	integer native_writes = 0;
	reg allow_present = 0;
	reg [31:0] expected_native_frame = 0;
	reg [1:0] expected_native_buffer = 0;
	reg [31:0] sequence_number = 0;
	reg previous_stalled_read = 0, previous_stalled_write = 0;
	reg [28:0] stalled_address;
	reg [7:0] stalled_burst, stalled_be;
	reg [63:0] stalled_data;
	integer widths [0:10];
	integer i, j, w, lane, local_odd, width, local_x;

	am2r_gpu dut (
		.clk(clk), .reset(reset), .ddram_busy(ddram_busy),
		.ddram_burstcnt(ddram_burstcnt), .ddram_addr(ddram_addr),
		.ddram_dout(ddram_dout), .ddram_dout_ready(ddram_dout_ready),
		.ddram_rd(ddram_rd), .ddram_din(ddram_din), .ddram_be(ddram_be),
		.ddram_we(ddram_we), .scan_buffer_valid(1'b0), .scan_buffer(2'b0), .hdmi_protect(4'b0),
		.scan_underflow_toggle(scan_underflow_toggle), .native_frame(native_frame),
		.native_buffer(native_buffer)
	);
	always #5 clk = ~clk;

	function automatic [31:0] source_pixel(input integer index);
		source_pixel = 32'h379abcde ^ (index * 32'h01030507);
	endfunction
	function automatic [31:0] untouched_pixel(input integer index);
		untouched_pixel = 32'h63857291 ^ (index * 32'h00010305);
	endfunction
	function automatic [63:0] read_memory(input [28:0] address);
		begin
			if (address >= CONTROL_WORD && address <= CONTROL_WORD + 10)
				read_memory = control[address - CONTROL_WORD];
			else if (address >= COMMAND_WORD && address < COMMAND_WORD + 32)
				read_memory = commands[address - COMMAND_WORD];
			else if (address >= (SOURCE_BASE >> 3) && address < (SOURCE_BASE >> 3) + DDR_WORDS)
				read_memory = source[address - (SOURCE_BASE >> 3)];
			else if (address >= (DEST_BASE >> 3) && address < (DEST_BASE >> 3) + DDR_WORDS)
				read_memory = destination[address - (DEST_BASE >> 3)];
			else if (address >= (PACKET_BASE >> 3) && address < (PACKET_BASE >> 3) + 64)
				read_memory = packet[address - (PACKET_BASE >> 3)];
			else begin
				$fatal(1, "Unexpected DDR read %08h", address << 3);
				read_memory = 0;
			end
		end
	endfunction
	task automatic write_memory(input [28:0] address, input [63:0] data, input [7:0] enables);
		integer byte_index, index;
		begin
			if (address == CONTROL_WORD + 3 || address == CONTROL_WORD + 10) begin
				if (address == CONTROL_WORD + 3 && control[10] !== 64'h0000001f_43473241)
					$fatal(1, "Completion preceded capabilities");
				for (byte_index = 0; byte_index < 8; byte_index = byte_index + 1)
					if (enables[byte_index]) control[address - CONTROL_WORD][byte_index*8 +: 8] = data[byte_index*8 +: 8];
			end else if (address >= (DEST_BASE >> 3) && address < (DEST_BASE >> 3) + DDR_WORDS) begin
				index = address - (DEST_BASE >> 3);
				for (byte_index = 0; byte_index < 8; byte_index = byte_index + 1)
					if (enables[byte_index]) destination[index][byte_index*8 +: 8] = data[byte_index*8 +: 8];
				target_writes = target_writes + 1;
			end else if (allow_present && address >= (32'h3a000100 >> 3) &&
			             address < (32'h3a000100 >> 3) + FB_WORDS) begin
				native_writes = native_writes + 1;
			end else $fatal(1, "Unexpected DDR write/presentation %08h", address << 3);
		end
	endtask

	always @(posedge clk) begin
		cycles <= cycles + 1;
		if (check_cross_line_prefetch) begin
			if (dut.fb_even_read_address !== cross_even_word || dut.fb_odd_read_address !== cross_odd_word || dut.pair_mode)
				$fatal(1,"Source-line crossing failed destination-only prefetch or enabled a texture pair");
			cross_line_prefetches = cross_line_prefetches + 1;
		end
		check_cross_line_prefetch <= 0;
		if (dut.state == dut.ST_BLIT_PIXEL && !dut.solid_mode &&
		    dut.u_step == 32'sh00010000 && dut.axis_stream_setup != 0 &&
		    !dut.axis_stream_eligible && dut.axis_pair_geometry_eligible && !dut.axis_pair_eligible &&
		    dut.axis_destination_x >= 0 && dut.axis_destination_x < 320 &&
		    ($signed(dut.dst_y) + $signed({1'b0,dut.blit_y})) >= 0 &&
		    ($signed(dut.dst_y) + $signed({1'b0,dut.blit_y})) < 240) begin
			check_cross_line_prefetch <= 1;
			cross_even_word <= dut.destination_linear_calc[16:1] + dut.destination_linear_calc[0];
			cross_odd_word <= dut.destination_linear_calc[16:1];
		end
		if (dut.state == dut.ST_RECT_CLEAR) rect_clear_cycles = rect_clear_cycles + 1;
		if (dut.axis_stream_mode && dut.state == dut.ST_WATER_SOURCE_DATA && ddram_dout_ready) begin
			axis_stream_beats = axis_stream_beats + 1;
			axis_stream_streak = axis_stream_streak + 1;
			if (axis_stream_streak > axis_stream_max_streak) axis_stream_max_streak = axis_stream_streak;
		end else axis_stream_streak = 0;
		if (dut.axis_gather_mode && dut.state == dut.ST_WATER_SOURCE_DATA &&
		    !dut.gather_refilling && !dut.gather_lookup && dut.gather_pending != 0) begin
			if(dut.gather_pending==3) gather_pairs=gather_pairs+1;
			else if(dut.gather_pending==1) gather_singles=gather_singles+1;
			else $fatal(1,"Gather issued second lane without first");
			if(cycles-gather_last_issue<2)$fatal(1,"Gather source register phase overlapped");
			if(cycles-gather_last_issue<gather_shortest_gap)gather_shortest_gap=cycles-gather_last_issue;
			gather_last_issue=cycles;
		end
		ddram_busy <= force_busy ? 1'b1 : schedule == 0 ? ((cycles % 11) == 7) :
			(((cycles % 53) < 11) || ((cycles % 7) == 2));
		ddram_dout_ready <= 0;
		if (!reset && previous_stalled_read &&
		    (!ddram_rd || ddram_addr !== stalled_address || ddram_burstcnt !== stalled_burst))
			$fatal(1, "Read request changed under backpressure");
		if (!reset && previous_stalled_write &&
		    (!ddram_we || ddram_addr !== stalled_address || ddram_burstcnt !== stalled_burst ||
		     ddram_din !== stalled_data || ddram_be !== stalled_be))
			$fatal(1, "Write request changed under backpressure state%0d address%h/%h burst%h/%h data%h/%h be%h/%h",
				dut.state, ddram_addr, stalled_address, ddram_burstcnt, stalled_burst,
				ddram_din, stalled_data, ddram_be, stalled_be);
		previous_stalled_read <= ddram_rd && ddram_busy;
		previous_stalled_write <= ddram_we && ddram_busy;
		if ((ddram_rd || ddram_we) && ddram_busy) begin
			stalled_address <= ddram_addr;
			stalled_burst <= ddram_burstcnt;
			stalled_data <= ddram_din;
			stalled_be <= ddram_be;
		end
		if (ddram_rd && ddram_we) $fatal(1, "Concurrent DDR read and write");
		if ((ddram_rd || ddram_we) && (ddram_burstcnt == 0 || ddram_burstcnt > 128))
			$fatal(1, "Invalid DDR burst %0d", ddram_burstcnt);
		if (ddram_we && !ddram_busy) begin
			if (pending_write) begin
				write_memory(write_address, ddram_din, ddram_be);
				write_address <= write_address + 1'b1;
				write_remaining <= write_remaining - 1'b1;
				if (write_remaining == 1) pending_write <= 0;
			end else begin
				write_memory(ddram_addr, ddram_din, ddram_be);
				if (ddram_burstcnt > 1) begin
					pending_write <= 1;
					write_address <= ddram_addr + 1'b1;
					write_remaining <= ddram_burstcnt - 1'b1;
				end
			end
		end
		if (ddram_rd && !ddram_busy && !pending_read) begin
			pending_read <= 1;
			read_address <= ddram_addr;
			read_remaining <= ddram_burstcnt;
			read_delay <= schedule == 0 ? 2 : 7;
		end else if (pending_read) begin
			if (read_delay != 0) read_delay <= read_delay - 1;
			else if (schedule == 0 || (cycles % 5) != 1) begin
				ddram_dout <= read_memory(read_address);
				ddram_dout_ready <= 1;
				if (read_address >= (SOURCE_BASE >> 3) && read_address < (SOURCE_BASE >> 3) + DDR_WORDS)
					source_reads = source_reads + 1;
				read_address <= read_address + 1'b1;
				read_remaining <= read_remaining - 1'b1;
				if (read_remaining == 1) pending_read <= 0;
			end
		end
		if (dut.state == dut.ST_SURFACE_STORE_STREAM && dut.framebuffer_fifo_count == 0)
			$fatal(1, "Surface write FIFO underflow");
		if (dut.framebuffer_fifo_count > 8 &&
		    (dut.state == dut.ST_SURFACE_STORE_STREAM || dut.state == dut.ST_SURFACE_STORE_FILL))
			$fatal(1, "Surface write FIFO overflow");
	end

	task automatic rectangle(input integer command_index, input integer opcode,
		input integer rect_width, rect_height, input [31:0] address,
		input integer stride, x, y);
		begin
			commands[command_index*8] = (64'(rect_height) << 32) | (64'(rect_width) << 16) | opcode;
			commands[command_index*8 + 1] = {32'(stride), address};
			commands[command_index*8 + 2] = (64'(y) << 16) | x;
		end
	endtask
	task automatic submit(input integer command_count);
		integer start_cycle;
		begin
			@(negedge clk);
			sequence_number = sequence_number + 1'b1;
			control[1] = (64'(command_count) << 32) | 32'h23fe0000;
			control[0] = {sequence_number, 32'h50473241};
			start_cycle = cycles;
			while (control[3][31:0] !== sequence_number) begin
				@(negedge clk);
				if (cycles - start_cycle > 2000000) $fatal(1, "Job timeout state%0d", dut.state);
			end
			if (native_frame !== expected_native_frame || native_buffer !== expected_native_buffer)
				$fatal(1, "No-present job changed native publication");
			if (pending_write) $fatal(1, "Completion during unfinished DDR burst");
		end
	endtask
	task automatic prepare;
		integer index;
		begin
			for (index = 0; index < 32; index = index + 1) commands[index] = 0;
			for (index = 0; index < FB_WORDS; index = index + 1) begin
				dut.fb_even[index] = untouched_pixel(index*2);
				dut.fb_odd[index] = untouched_pixel(index*2 + 1);
				expected_fb[index*2] = untouched_pixel(index*2);
				expected_fb[index*2 + 1] = untouched_pixel(index*2 + 1);
			end
			for (index = 0; index < DDR_WORDS; index = index + 1) begin
				destination[index] = 64'h01234567_89abcdef ^ index;
				expected_destination[index] = destination[index];
			end
		end
	endtask
	task automatic compare_everything;
		integer index;
		begin
			for (index = 0; index < FB_WORDS; index = index + 1) begin
				if (dut.fb_even[index] !== expected_fb[index*2] || dut.fb_odd[index] !== expected_fb[index*2 + 1]) begin
					if (errors < 8) $display("BRAM mismatch case%0d word%0d got%08h_%08h expected%08h_%08h",
						cases_run, index, dut.fb_odd[index], dut.fb_even[index], expected_fb[index*2+1], expected_fb[index*2]);
					errors = errors + 1;
				end
			end
			for (index = 0; index < DDR_WORDS; index = index + 1)
				if (destination[index] !== expected_destination[index]) begin
					if (errors < 8) $display("DDR mismatch case%0d word%0d got%016h expected%016h",
						cases_run, index, destination[index], expected_destination[index]);
					errors = errors + 1;
				end
			if (errors) $fatal(1, "%0d mismatches", errors);
		end
	endtask
	task automatic transfer_case(input integer rect_width, rect_height, src_lane, dst_lane, x, y);
		integer px, py, source_index, destination_index;
		reg [31:0] pixel;
		begin
			prepare();
			for (py = 0; py < rect_height; py = py + 1)
				for (px = 0; px < rect_width; px = px + 1) begin
					source_index = 32 + src_lane + py * 512 + px;
					destination_index = 36 + dst_lane + py * 512 + px;
					pixel = source_pixel(source_index);
					expected_fb[(y + py)*320 + x + px] = pixel;
					if (destination_index & 1) expected_destination[destination_index >> 1][63:32] = pixel;
					else expected_destination[destination_index >> 1][31:0] = pixel;
				end
			rectangle(0, 10, rect_width, rect_height, SOURCE_BASE + 128 + src_lane*4, 2048, x, y);
			commands[8] = 12;
			submit(2);
			// A separate non-presenting job must retain all BRAM contents.
			for (px = 0; px < 32; px = px + 1) commands[px] = 0;
			rectangle(0, 11, rect_width, rect_height, DEST_BASE + 144 + dst_lane*4, 2048, x, y);
			commands[8] = 12;
			submit(2);
			if (dut.texture_cache_valid) $fatal(1, "Store retained stale texture cache");
			compare_everything();
			cases_run = cases_run + 1;
		end
	endtask

	task automatic replace_case(input integer rect_width, rect_height, x, y, input [31:0] rgba);
		integer px, py, old_reads, old_writes, old_clear_cycles;
		begin
			prepare();
			for (py=0;py<rect_height;py=py+1)
				for (px=0;px<rect_width;px=px+1) expected_fb[(y+py)*320+x+px]=rgba;
			rectangle(0,14,rect_width,rect_height,rgba,0,x,y);
			commands[8]=12;
			old_reads=source_reads; old_writes=target_writes; old_clear_cycles=rect_clear_cycles;
			submit(2);
			if(source_reads!=old_reads || target_writes!=old_writes)
				$fatal(1,"Raw replacement performed DDR data transfer");
			if(rect_clear_cycles-old_clear_cycles!=rect_height*((rect_width+(x&1)+1)/2))
				$fatal(1,"Raw replacement did not sustain one aligned pair per clock");
			compare_everything();
			cases_run=cases_run+1;
		end
	endtask

	task automatic axis_stream_case(input integer rect_width, x, mode, floor_mode);
		integer px, py, channel, source_index, at, sa, sc, dc, result;
		integer expected_beats, old_beats, old_reads, old_writes;
		reg [31:0] s, tinted, tint;
		begin
			prepare();
			tint = 32'h81b37d41;
			expected_beats = 0;
			for (py=0;py<7;py=py+1) begin
				expected_beats = expected_beats + (rect_width + ((1+py*321)&1) + 1)/2;
				for (px=0;px<rect_width;px=px+1) begin
					source_index = 1+py*321+px;
					s=source_pixel(source_index);
					for(channel=0;channel<4;channel=channel+1)
						tinted[channel*8 +: 8]=(int'(s[channel*8 +: 8])*int'(tint[channel*8 +: 8])+
							(floor_mode ? 0 : 127))/255;
					at=(4+py)*320+x+px;
					sa=tinted[31:24];
					for(channel=0;channel<4;channel=channel+1) begin
						sc=tinted[channel*8 +: 8]; dc=expected_fb[at][channel*8 +: 8];
						case(mode)
							0: result=channel==3 ? sa+dc*(255-sa)/255 : (sc*sa+dc*(255-sa))/255;
							1: result=channel==3 ? sa+dc : dc+sc*sa/255;
							2: result=dc*(255-sc)/255;
						endcase
						expected_fb[at][channel*8 +: 8]=result>255 ? 255 : result;
					end
				end
			end
			rectangle(0,2,rect_width,7,SOURCE_BASE+4,1284,x,4);
			commands[0][8]=mode==1; commands[0][9]=mode==2; commands[0][10]=floor_mode!=0;
			commands[4]=64'h00000000_00004000;
			commands[5]=64'h00010000_00010000;
			commands[6]=tint;
			commands[8]=12;
			old_beats=axis_stream_beats; old_reads=source_reads; old_writes=target_writes;
			axis_stream_max_streak=0;
			submit(2);
			if(axis_stream_beats-old_beats!=expected_beats || source_reads-old_reads!=expected_beats)
				$fatal(1,"Axis stream issued wrong source footprint or fell back");
			if(target_writes!=old_writes)$fatal(1,"Axis stream unexpectedly wrote DDR");
			if(schedule==0 && axis_stream_max_streak<16)$fatal(1,"Axis stream did not sustain one pair/clock");
			if(control[3][60:32]>7*500)$fatal(1,"Axis stream cycle count regressed: %0d",control[3][60:32]);
			compare_everything();
			cases_run=cases_run+1;
		end
	endtask

	task automatic axis_setup_case(input integer left_clip);
		integer px, channel, source_index, at, dc, sc, old_beats, x, count;
		reg [31:0] s;
		begin
			prepare();
			x=left_clip ? -5 : 7;
			count=left_clip==2 ? 327 : left_clip ? 69 : 64;
			for(px=0;px<count;px=px+1)if(x+px>=0 && x+px<320)begin
				source_index=32+(left_clip ? 0 : -6)+px;
				s=source_pixel(source_index);
				at=6*320+x+px;
				for(channel=0;channel<4;channel=channel+1)begin
					sc=s[channel*8 +: 8]; dc=expected_fb[at][channel*8 +: 8];
					expected_fb[at][channel*8 +: 8]=dc*(255-sc)/255;
				end
			end
			rectangle(0,2,count,1,SOURCE_BASE+128,1284,0,6);
			commands[2][15:0]=16'(x);
			commands[0][9]=1;
			commands[4]=left_clip ? 64'h00008000 : 64'hfffa8000;
			commands[5]=64'h00010000_00010000;
			commands[6]=32'hffffffff;
			commands[8]=12;
			old_beats=axis_stream_beats;
			submit(2);
			if(axis_stream_beats-old_beats!=(left_clip==2 ? 161 : left_clip ? 33 : 0))
				$fatal(1,"Staged eligibility retried rejected UV or lost left-clipped stream");
			compare_everything();
			cases_run=cases_run+1;
		end
	endtask

	task automatic axis_cross_line_prefetch_case(input integer x, mode, floor_mode);
		integer px, channel, at, sc, dc, sa, result, old_reads, old_prefetches;
		reg [31:0] s, tint, tinted;
		begin
			prepare();
			tint=32'h81b37d41;
			for(px=0;px<3;px=px+1)if(x+px<320)begin
				s=source_pixel(31+px);
				for(channel=0;channel<4;channel=channel+1)
					tinted[channel*8 +: 8]=(int'(s[channel*8 +: 8])*int'(tint[channel*8 +: 8])+
						(floor_mode ? 0 : 127))/255;
				at=239*320+x+px;sa=tinted[31:24];
				for(channel=0;channel<4;channel=channel+1)begin
					sc=tinted[channel*8 +: 8];dc=expected_fb[at][channel*8 +: 8];
					case(mode)
						0:result=channel==3 ? sa+dc*(255-sa)/255 : (sc*sa+dc*(255-sa))/255;
						1:result=channel==3 ? sa+dc : dc+sc*sa/255;
						2:result=dc*(255-sc)/255;
					endcase
					expected_fb[at][channel*8 +: 8]=result>255 ? 255 : result;
				end
			end
			rectangle(0,2,3,1,SOURCE_BASE,1280,x,239);
			commands[0][8]=mode==1;commands[0][9]=mode==2;commands[0][10]=floor_mode!=0;
			commands[4]=64'h00000000_001f3039;
			commands[5]=64'h00010000_00010000;
			commands[6]=tint;commands[8]=12;
			old_reads=source_reads;old_prefetches=cross_line_prefetches;
			submit(2);
			if(source_reads-old_reads!=(x==319 ? 16 : 32))$fatal(1,"BRAM prefetch changed source DDR footprint");
			if(cross_line_prefetches-old_prefetches!=(x==319 ? 0 : 1))
				$fatal(1,"Source-line crossing prefetch regression was not exercised");
			compare_everything();cases_run=cases_run+1;
		end
	endtask

	task automatic axis_gather_case(input integer variant,mode,floor_mode);
		integer px,py,channel,source_index,next_index,at,sa,sc,dc,result;
		integer old_reads,old_writes,old_pairs,old_singles,expected_pairs,expected_singles;
		integer expected_reads,cache_line,x,count,stride,base_index,height;
		reg signed [31:0] u,du,uv,next_uv;
		reg [31:0] s,tinted,tint;
		begin
			prepare(); x=1; count=319; stride=1025; base_index=1; height=7;
			case(variant)
				0: begin u=32'sh013f4000; du=-65536; end
				1: begin u=32'sh0000c000; du=32768; end
				2: begin u=32'sh00648000; du=100824; end
				3: begin u=32'sh031f4000; du=-163839; end
				4: begin u=32'sh001fc000; du=0; x=-5; count=327; end
				5: begin u=32'sh7fff8000; du=131072; stride=4; base_index=32768; count=67; end
			endcase
			tint=32'h81b37d41;
			expected_pairs=0;expected_singles=0;expected_reads=0;cache_line=-1;
			for(py=0;py<height;py=py+1)begin
				px=x<0 ? -x : 0;
				while(px<count && x+px<320)begin
					uv=u+32'(px)*du;
					source_index=base_index+py*stride+(uv>>>16);
					if((source_index/32)!=cache_line)begin
						cache_line=source_index/32;expected_reads=expected_reads+16;
					end
					next_uv=uv+du;next_index=base_index+py*stride+(next_uv>>>16);
					if(px+1<count && x+px+1<320 && source_index/32==next_index/32)begin
						expected_pairs=expected_pairs+1;px=px+2;
					end else begin expected_singles=expected_singles+1;px=px+1;end
				end
				for(px=0;px<count;px=px+1)if(x+px>=0 && x+px<320)begin
					uv=u+32'(px)*du; source_index=base_index+py*stride+(uv>>>16);
					s=source_pixel(source_index);
					for(channel=0;channel<4;channel=channel+1)
						tinted[channel*8 +: 8]=(int'(s[channel*8 +: 8])*int'(tint[channel*8 +: 8])+
							(floor_mode ? 0 : 127))/255;
					at=(4+py)*320+x+px;sa=tinted[31:24];
					for(channel=0;channel<4;channel=channel+1)begin
						sc=tinted[channel*8 +: 8];dc=expected_fb[at][channel*8 +: 8];
						case(mode)
							0: result=channel==3 ? sa+dc*(255-sa)/255 : (sc*sa+dc*(255-sa))/255;
							1: result=channel==3 ? sa+dc : dc+sc*sa/255;
							2: result=dc*(255-sc)/255;
						endcase
						expected_fb[at][channel*8 +: 8]=result>255 ? 255 : result;
					end
				end
			end
			rectangle(0,2,count,height,SOURCE_BASE+32'(base_index*4),stride*4,0,4);
			commands[2][15:0]=16'(x);
			commands[0][8]=mode==1;commands[0][9]=mode==2;commands[0][10]=floor_mode!=0;
			commands[4]={32'd0,u};commands[5]={32'd65536,du};commands[6]=tint;commands[8]=12;
			old_reads=source_reads;old_writes=target_writes;old_pairs=gather_pairs;old_singles=gather_singles;
			gather_shortest_gap=100;
			submit(2);
			if(source_reads-old_reads!=expected_reads || gather_pairs-old_pairs!=expected_pairs ||
			   gather_singles-old_singles!=expected_singles)
				$fatal(1,"Gather footprint variant%0d reads%0d/%0d pairs%0d/%0d singles%0d/%0d",variant,
					source_reads-old_reads,expected_reads,gather_pairs-old_pairs,expected_pairs,
					gather_singles-old_singles,expected_singles);
			if(target_writes!=old_writes)$fatal(1,"Gather unexpectedly wrote DDR");
			if(gather_shortest_gap!=2)$fatal(1,"Gather did not sustain one pair/two clocks");
			if(control[3][60:32]>14000)$fatal(1,"Gather cycle count regressed: %0d",control[3][60:32]);
			compare_everything();cases_run=cases_run+1;
		end
	endtask

	// Mathematical specification, deliberately not the DUT's functions:
	// signed Q32 planes, clamp then full-width multiply, byte-domain blends.
	function automatic integer reference_factor(input integer factor, channel,
		input [31:0] s, d);
		integer sc, dc, sa, da;
		begin
			sc=s[channel*8 +: 8]; dc=d[channel*8 +: 8]; sa=s[31:24]; da=d[31:24];
			case (factor)
				1: reference_factor=0;
				2: reference_factor=255;
				3: reference_factor=sc;
				4: reference_factor=255-sc;
				5: reference_factor=sa;
				6: reference_factor=255-sa;
				7: reference_factor=da;
				8: reference_factor=255-da;
				9: reference_factor=dc;
				10: reference_factor=255-dc;
				11: reference_factor=channel==3 ? 255 : (sa<255-da ? sa : 255-da);
				default: $fatal(1,"Invalid factor in reference");
			endcase
		end
	endfunction
	function automatic [31:0] reference_blend(input [31:0] source_rgba, destination_rgba);
		integer channel, sc, dc, sa, da, result, mode, sf, df;
		reg [31:0] s;
		begin
			s=source_rgba;
			if (packet[2][57]) s[23:0]=packet[3][55:32];
			sa=s[31:24]; da=destination_rgba[31:24]; mode=packet[2][39:32];
			reference_blend=destination_rgba;
			if (!packet[2][56] || sa>=packet[2][55:48])
				for (channel=0;channel<4;channel=channel+1) begin
					sc=s[channel*8 +: 8]; dc=destination_rgba[channel*8 +: 8];
					if (!packet[2][58]) result=sc;
					else case(mode)
						0: result=channel==3 ? sa+da*(255-sa)/255 : (sc*sa+dc*(255-sa))/255;
						1: result=channel==3 ? sa+da : dc+sc*sa/255;
						2: result=sc>dc ? sc : dc;
						3: result=dc*(255-sc)/255;
						4: result=sc<dc ? sc : dc;
						5: result=channel==3 ? sa : sc*sa/255-dc;
						6: begin
							sf=channel==3 ? packet[3][23:16] : packet[3][7:0];
							df=channel==3 ? packet[3][31:24] : packet[3][15:8];
							result=(sc*reference_factor(sf,channel,s,destination_rgba)+
								dc*reference_factor(df,channel,s,destination_rgba))/255;
						end
						default: $fatal(1,"Invalid blend in reference");
					endcase
					if (result<0) result=0;
					if (result>255) result=255;
					if (packet[2][40+channel]) reference_blend[channel*8 +: 8]=8'(result);
				end
		end
	endfunction
	task automatic reference_primitive(input integer rect_width,rect_height,x,y);
		integer px,py,channel,tx,ty,linear;
		reg covered;
		reg signed [63:0] value, color;
		reg [31:0] texel, tint;
		reg [71:0] product;
		begin
			for(py=0;py<rect_height;py=py+1)
				for(px=0;px<rect_width;px=px+1) begin
					covered=1;
					if(packet[0][33])
						for(channel=0;channel<3;channel=channel+1) begin
							value=$signed(packet[8+channel*3])+$signed(packet[9+channel*3])*px+$signed(packet[10+channel*3])*py;
							if(value<0) covered=0;
						end
					if(covered) begin
						texel=32'hffffffff;
						if(packet[0][32]) begin
							value=$signed(packet[17])+$signed(packet[18])*px+$signed(packet[19])*py;
							tx=value>>>32; if(tx<0)tx=0; if(tx>=packet[2][15:0])tx=packet[2][15:0]-1;
							value=$signed(packet[20])+$signed(packet[21])*px+$signed(packet[22])*py;
							ty=value>>>32; if(ty<0)ty=0; if(ty>=packet[2][31:16])ty=packet[2][31:16]-1;
							linear=(packet[1][31:0]-SOURCE_BASE)/4+ty*(packet[1][63:32]/4)+tx;
							texel=linear[0] ? source[linear>>1][63:32] : source[linear>>1][31:0];
						end
						for(channel=0;channel<4;channel=channel+1) begin
							color=$signed(packet[23+channel*4])+$signed(packet[24+channel*4])*px+
								$signed(packet[25+channel*4])*py+$signed(packet[26+channel*4])*px*py;
							if(color<0)color=0; if(color>64'sh100000000)color=64'sh100000000;
							product=72'(texel[channel*8 +: 8])*72'(color);
							tint[channel*8 +: 8]=product>>32;
						end
						linear=(y+py)*320+x+px;
						expected_fb[linear]=reference_blend(tint,expected_fb[linear]);
					end
				end
		end
	endtask
	task automatic generic_case(input integer rect_width,rect_height,mode,sf,df,saf,daf,
		input textured,triangle,enabled,alpha_test,fog,transparent,
		input [3:0] write_mask);
		integer index, x,y;
		begin
			prepare();
			for(index=0;index<64;index=index+1)packet[index]=0;
			packet[0]=64'h00000000_31504741 | (64'(textured)<<32) | (64'(triangle)<<33);
			packet[1]={32'd32,SOURCE_BASE};
			packet[2]=64'(8) | (64'(8)<<16) | (64'(mode)<<32) | (64'(write_mask)<<40) |
				(64'(128)<<48) | (64'(alpha_test)<<56) | (64'(fog)<<57) | (64'(enabled)<<58);
			packet[3]={32'h0037ae21,8'(daf),8'(saf),8'(df),8'(sf)};
			packet[8]=-64'sh100000000; packet[9]=64'h100000000;
			packet[11]=-64'sh100000000; packet[13]=64'h100000000;
			packet[14]=64'hb00000000; packet[15]=-64'sh100000000; packet[16]=-64'sh100000000;
			packet[17]=-64'sh80000000; packet[18]=64'h180000000; packet[19]=64'h40000000;
			packet[20]=-64'sh100000000; packet[21]=64'h40000000; packet[22]=64'h180000000;
			packet[23]=64'h80000000; packet[24]=64'h10000000; packet[25]=64'h08000000; packet[26]=64'h02000000;
			packet[27]=64'hc0000000; packet[28]=-64'sh08000000; packet[29]=64'h04000000; packet[30]=-64'sh01000000;
			packet[31]=64'h100000000; packet[33]=-64'sh10000000; packet[34]=64'h02000000;
			packet[35]=transparent ? 0 : 64'h80000000;
			if(!transparent)begin packet[36]=64'h10000000; packet[37]=-64'sh08000000; end
			x=rect_width==320 ? 0 : 3; y=rect_height==240 ? 0 : 5;
			reference_primitive(rect_width,rect_height,x,y);
			commands[0]=(64'(rect_height)<<32) | (64'(rect_width)<<16) | 13;
			commands[1]=PACKET_BASE;
			commands[2]=(64'(y)<<16) | x;
			commands[8]=12;
			submit(2);
			compare_everything();
			cases_run=cases_run+1;
			if(rect_width==320 && rect_height==240)
				$display("Generic fullscreen gradient: %0d GPU cycles (%0f ms at88MHz)",control[3][60:32],real'(control[3][60:32])/88000.0);
		end
	endtask

	task automatic completion_underflow_case;
		begin
			commands[0]=12;
			fork
				begin submit(1); end
				begin
					wait(dut.state==dut.ST_COMPLETE);
					@(negedge clk); force_busy=1;
					repeat(2) @(negedge clk);
					if(!ddram_we || !ddram_busy)$fatal(1,"Completion stall fixture failed");
					scan_underflow_toggle=~scan_underflow_toggle;
					repeat(5) @(negedge clk);
					force_busy=0;
				end
			join
			if(control[3][61]!==0)$fatal(1,"Latched completion changed its underflow report");
			submit(1);
			if(control[3][61]!==1)$fatal(1,"Underflow during stalled completion was lost");
			submit(1);
			if(control[3][61]!==0)$fatal(1,"Underflow event reported more than once");
			cases_run=cases_run+1;
		end
	endtask

	initial begin
		widths[0]=1; widths[1]=2; widths[2]=3; widths[3]=31; widths[4]=32;
		widths[5]=63; widths[6]=127; widths[7]=128; widths[8]=129;
		widths[9]=319; widths[10]=320;
		for (i=0; i<11; i=i+1) control[i]=0;
		for (i=0; i<32; i=i+1) commands[i]=0;
		for (i=0; i<DDR_WORDS; i=i+1) source[i]={source_pixel(i*2+1),source_pixel(i*2)};
		repeat(6) @(negedge clk);
		reset=0;
		repeat(6) @(negedge clk);
		for (schedule=0; schedule<2; schedule=schedule+1) begin
			for(i=0;i<3;i=i+1)for(j=0;j<2;j=j+1)begin
				axis_cross_line_prefetch_case(0,i,j);
				axis_cross_line_prefetch_case(1,i,j);
				axis_cross_line_prefetch_case(317,i,j);
				axis_cross_line_prefetch_case(318,i,j);
				axis_cross_line_prefetch_case(319,i,j);
			end
			for(w=0;w<6;w=w+1)for(i=0;i<3;i=i+1)for(j=0;j<2;j=j+1)
				axis_gather_case(w,i,j);
			axis_setup_case(0);
			axis_setup_case(1);
			axis_setup_case(2);
			for(i=0;i<3;i=i+1)for(j=0;j<2;j=j+1) begin
				axis_stream_case(320,0,i,j);
				axis_stream_case(319,1,i,j);
				axis_stream_case(32,7,i,j);
			end
			for (w=0; w<11; w=w+1)
				for (lane=0; lane<2; lane=lane+1)
					for (local_odd=0; local_odd<2; local_odd=local_odd+1) begin
						local_x = widths[w] == 320 ? 0 : local_odd;
						transfer_case(widths[w], 3, lane, 1-lane, local_x, 7);
					end
			transfer_case(320, 240, 1, 1, 0, 0);
			transfer_case(1, 1, 1, 0, 319, 239);
			for(w=0;w<11;w=w+1)
				for(local_odd=0;local_odd<2;local_odd=local_odd+1)
					replace_case(widths[w],3,widths[w]==320 ? 0 : local_odd,17,32'h00123456 ^ 32'(w));
			replace_case(320,240,0,0,32'h7f123456);
			replace_case(1,1,319,239,32'hffabcdef);
			replace_case(319,239,1,1,32'h00000000);
			replace_case(64,64,0,0,32'h00ff0000);
			replace_case(320,16,0,224,32'hffffffff);
			replace_case(192,16,128,224,32'ha555aa11);
			// Zero/unsafe geometry and every reserved field must reject without
			// touching a neighbour. A following valid command must still execute.
			for(j=0;j<28;j=j+1) begin
				prepare();
				rectangle(0,14,2,2,32'h01234567,0,0,0);
				case(j)
					0: commands[0][31:16]=0;
					1: commands[0][47:32]=0;
					2: commands[0][31:16]=321;
					3: commands[0][47:32]=241;
					4: commands[2][15:0]=319;
					5: commands[2][31:16]=239;
					6: commands[2][15:0]=16'hffff;
					7: commands[2][31:16]=16'hffff;
					8: commands[0][8]=1;
					9: commands[0][15]=1;
					10: commands[0][48]=1;
					11: commands[0][63]=1;
					12: commands[1][32]=1;
					13: commands[1][63]=1;
					14: commands[2][32]=1;
					15: commands[2][63]=1;
					26: commands[0][31:16]=16'hffff;
					27: commands[0][47:32]=16'hffff;
					default: commands[3+(j-16)/2][(j&1) ? 63 : 0]=1;
				endcase
				rectangle(1,14,1,1,32'h00112233,0,318,238);
				expected_fb[238*320+318]=32'h00112233;
				commands[16]=12;
				w=source_reads;
				submit(3);
				if(source_reads!=w)$fatal(1,"Rejected replacement read texture data");
				compare_everything();
				cases_run=cases_run+1;
			end
			// Preserve full-frame legacy clear despite nonzero unused geometry;
			// overlapping raw replacements must retain alpha-zero pixels exactly.
			prepare();
			rectangle(0,1,1,1,32'h80aabbcc,0,7,9);
			for(i=0;i<76800;i=i+1)expected_fb[i]=32'h80aabbcc;
			rectangle(1,14,5,2,32'h00010203,0,1,3);
			for(i=3;i<5;i=i+1)for(j=1;j<6;j=j+1)expected_fb[i*320+j]=32'h00010203;
			rectangle(2,14,4,3,32'h12345678,0,2,4);
			for(i=4;i<7;i=i+1)for(j=2;j<6;j=j+1)expected_fb[i*320+j]=32'h12345678;
			commands[24]=12;
			submit(4);
			compare_everything();
			cases_run=cases_run+1;
			// Every invalid geometry/alignment is a no-op, never a presentation.
			for (j=0; j<7; j=j+1) begin
				prepare();
				case (j)
					0: rectangle(0, 10, 0, 3, SOURCE_BASE, 2048, 0, 0);
					1: rectangle(0, 11, 321, 3, DEST_BASE, 2048, 0, 0);
					2: rectangle(0, 10, 2, 3, SOURCE_BASE, 2048, 319, 0);
					3: rectangle(0, 11, 2, 3, DEST_BASE, 2048, 0, 239);
					4: rectangle(0, 10, 2, 3, SOURCE_BASE+1, 2048, 0, 0);
					5: rectangle(0, 11, 2, 3, DEST_BASE, 2047, 0, 0);
					6: rectangle(0, 10, 3, 3, SOURCE_BASE, 8, 0, 0);
				endcase
				commands[8]=12;
				submit(2);
				compare_everything();
				cases_run=cases_run+1;
			end
			prepare();
			commands[0]=255;
			submit(1);
			compare_everything();
			cases_run=cases_run+1;
			prepare();
			commands[0]=64'h00000000_00000305;
			commands[1]=64'h00000000_12345678;
			commands[8]=12;
			submit(2);
			if (dut.affine_tint_config !== 32'h12345678 || !dut.affine_additive_config || !dut.affine_subtract_config)
				$fatal(1, "Affine configuration fixture failed");
			commands[0]=12;
			submit(1);
			if (dut.affine_tint_config !== 32'hffffffff || dut.affine_additive_config || dut.affine_subtract_config)
				$fatal(1, "Affine configuration leaked across no-present jobs");
			compare_everything();
			cases_run=cases_run+1;
			for (j=0;j<7;j=j+1) begin
				generic_case(11,7,j,2,2,2,2,1'b1,1'b0,1'b1,1'b0,1'b0,1'b0,4'hf);
				generic_case(11,7,j,2,2,2,2,1'b0,1'b1,1'b1,1'b0,1'b0,1'b1,4'hf);
			end
			for (i=1;i<=11;i=i+1)
				for (j=1;j<=11;j=j+1)
					generic_case(2,2,6,i,j,(i+3)%11+1,(j+5)%11+1,1'b1,1'b0,1'b1,1'b0,1'b0,1'b0,4'hf);
			for(j=0;j<16;j=j+1)
				generic_case(11,7,0,2,2,2,2,1'b1,1'b0,1'b1,1'b1,1'b1,1'b0,4'(j));
			generic_case(11,7,0,2,2,2,2,1'b0,1'b0,1'b0,1'b0,1'b0,1'b1,4'hf);
			generic_case(320,240,0,2,2,2,2,1'b0,1'b0,1'b1,1'b0,1'b0,1'b0,4'hf);
			for(j=0;j<12;j=j+1) begin
				prepare();
				for(i=0;i<64;i=i+1)packet[i]=0;
				packet[0]=64'h00000001_31504741;
				packet[1]={32'd32,SOURCE_BASE};
				packet[2]=64'h04000f00_00080008;
				packet[3]=64'h00000000_02020202;
				packet[23]=64'h100000000; packet[27]=64'h100000000;
				packet[31]=64'h100000000; packet[35]=64'h100000000;
				commands[0]=64'h00000002_0002000d;
				commands[1]=PACKET_BASE;
				commands[8]=12;
				case(j)
					0: packet[0][31:0]=32'hbad0bad0;
					1: packet[0][34]=1;
					2: packet[63]=1;
					3: packet[4]=1;
					4: packet[2][39:32]=7;
					5: packet[2][15:0]=0;
					6: begin packet[2][39:32]=6; packet[3][7:0]=0; end
					7: begin packet[2][39:32]=6; packet[3][31:24]=12; end
					8: packet[1][63:32]=31;
					9: packet[1][31:0]=SOURCE_BASE+1;
					10: commands[2]=319;
					11: commands[1]=PACKET_BASE+4;
				endcase
				w=source_reads;
				submit(2);
				if(source_reads!=w)$fatal(1,"Rejected primitive sampled a texture");
				compare_everything();
				cases_run=cases_run+1;
			end
		end
		// A fence following a real presentation must leave the current native
		// frame/buffer and the next presentation-buffer ownership unchanged.
		prepare();
		allow_present=1;
		expected_native_frame=1;
		commands[0]=0;
		submit(1);
		allow_present=0;
		if (native_writes != FB_WORDS) $fatal(1, "Presentation fixture did not write a full frame");
		i=dut.present_buffer;
		commands[0]=12;
		submit(1);
		if (dut.present_buffer !== 2'(i)) $fatal(1, "Fence changed next native buffer ownership");
		compare_everything();
		cases_run=cases_run+1;
		completion_underflow_case();
		$display("PASS surface DMA: %0d cases, two DDR schedules, %0d source beats, %0d target writes, fence publication/configuration invariants", cases_run, source_reads, target_writes);
		$finish;
	end
endmodule
