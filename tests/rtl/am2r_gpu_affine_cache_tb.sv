`timescale 1ns/1ps

// Synthetic, command-driven affine-cache regression. The independent scalar
// oracle checks all RGBA lanes and untouched BRAM/DDR, not just visible RGB.
// The same bench also runs against pre-cache RTL with EXPECT_CACHE=0.
module am2r_gpu_affine_cache_tb;
    parameter integer EXPECT_CACHE = 1;
    parameter integer AFFINE_LINE_BEATS = 8;
    localparam [28:0] CONTROL = 32'h23ff0000 >> 3;
    localparam [28:0] COMMAND = 32'h23fe0000 >> 3;
    localparam [31:0] SOURCE = 32'h24000000;
    localparam [31:0] PACKET = 32'h26000000;
    localparam integer WORDS = 8192;
    reg clk = 0, reset = 1, ddram_busy = 0, ddram_dout_ready = 0;
    reg [63:0] ddram_dout = 0;
    wire [7:0] ddram_burstcnt, ddram_be;
    wire [28:0] ddram_addr;
    wire [63:0] ddram_din;
    wire ddram_rd, ddram_we;
    wire [31:0] native_frame;
    wire [1:0] native_buffer;
    reg [63:0] control [0:10], commands [0:127], packet [0:63];
    reg [63:0] source [0:WORDS-1], expected_source [0:WORDS-1];
    reg [31:0] expected_fb [0:76799];
    reg pending_read = 0, pending_write = 0;
    reg [28:0] read_address, write_address;
    integer read_remaining, write_remaining, read_delay;
    reg stalled_read = 0, stalled_write = 0;
    reg [28:0] held_address;
    reg [7:0] held_count, held_be;
    reg [63:0] held_data;
    reg [31:0] sequence_number = 0;
    integer cycles = 0, schedule = 0, cases_run = 0;
    integer affine_requests = 0, affine_beats = 0, affine_samples = 0;
    integer read_stalls = 0, response_gaps = 0, low_lanes = 0, high_lanes = 0;
    integer generic_reads = 0, axis_reads = 0, reset_checks = 0;
    integer pair_advances = 0, pair_row_advances = 0, pair_cache_advances = 0;
    integer flow_reads = 0, flow_pixels = 0, flow_low = 0, flow_high = 0;
    reg [3:0] binary_pair_kinds = 0;
    reg binary_axis_active = 0;
    integer last_job_cycles, last_requests, last_beats, last_samples;
    integer perf_cycles [0:1], perf_requests [0:1], perf_beats [0:1];
    integer i, kind, mode, tinted, pass;
    reg read_is_affine = 0;
    reg affine_refill_in_progress = 0;

    am2r_gpu dut (
        .clk(clk), .reset(reset), .ddram_busy(ddram_busy),
        .ddram_burstcnt(ddram_burstcnt), .ddram_addr(ddram_addr),
        .ddram_dout(ddram_dout), .ddram_dout_ready(ddram_dout_ready),
        .ddram_rd(ddram_rd), .ddram_din(ddram_din), .ddram_be(ddram_be),
        .ddram_we(ddram_we), .scan_buffer_valid(1'b0), .scan_buffer(2'b0),
        .hdmi_protect(4'b0), .scan_underflow_toggle(1'b0),
        .native_frame(native_frame), .native_buffer(native_buffer));
    always #5 clk = ~clk;

    function automatic [31:0] seed_pixel(input integer index);
        integer alpha;
        begin
            case (index % 6)
                0: alpha=0; 1: alpha=1; 2: alpha=127;
                3: alpha=128; 4: alpha=254; default: alpha=255;
            endcase
            seed_pixel={8'(alpha),8'(index*71+19),8'(index*13+37),8'(index*29+53)};
        end
    endfunction
    function automatic [31:0] background(input integer index);
        background=32'h63857291 ^ (index*32'h00010305);
    endfunction
    function automatic [63:0] read_memory(input [28:0] address);
        begin
            if (address>=CONTROL && address<=CONTROL+10) read_memory=control[address-CONTROL];
            else if (address>=COMMAND && address<COMMAND+128) read_memory=commands[address-COMMAND];
            else if (address>=(SOURCE>>3) && address<(SOURCE>>3)+WORDS) read_memory=source[address-(SOURCE>>3)];
            else if (address>=(PACKET>>3) && address<(PACKET>>3)+64) read_memory=packet[address-(PACKET>>3)];
            else begin $fatal(1,"Unexpected read %08h",address<<3); read_memory=0; end
        end
    endfunction
    task automatic write_memory(input [28:0] address, input [63:0] data, input [7:0] enables);
        integer b;
        begin
            if (address==CONTROL+3 || address==CONTROL+10) begin
                if(address==CONTROL+3 && control[10]!==64'h0000001f_43473241)
                    $fatal(1,"Completion preceded capabilities");
                for(b=0;b<8;b=b+1) if(enables[b]) control[address-CONTROL][b*8+:8]=data[b*8+:8];
            end else if(address>=(SOURCE>>3) && address<(SOURCE>>3)+WORDS) begin
                for(b=0;b<8;b=b+1) if(enables[b]) source[address-(SOURCE>>3)][b*8+:8]=data[b*8+:8];
            end else $fatal(1,"Unexpected write/presentation %08h",address<<3);
        end
    endtask

    always @(posedge clk) begin
        cycles <= cycles+1;
        ddram_dout_ready <= 0;
        ddram_busy <= schedule==0 ? (cycles%11==7) : ((cycles%53<11)||(cycles%7==2));
        if(reset) begin
            pending_read<=0; pending_write<=0; stalled_read<=0; stalled_write<=0;
            affine_refill_in_progress<=0;
        end else begin
            if(stalled_read && (!ddram_rd || ddram_addr!==held_address || ddram_burstcnt!==held_count))
                $fatal(1,"Read request changed under backpressure");
            if(stalled_write && (!ddram_we || ddram_addr!==held_address || ddram_burstcnt!==held_count ||
                               ddram_din!==held_data || ddram_be!==held_be))
                $fatal(1,"Write request changed under backpressure");
            stalled_read<=ddram_rd && ddram_busy;
            stalled_write<=ddram_we && ddram_busy;
            if((ddram_rd || ddram_we) && ddram_busy) begin
                held_address<=ddram_addr; held_count<=ddram_burstcnt;
                held_data<=ddram_din; held_be<=ddram_be;
                if(ddram_rd) read_stalls=read_stalls+1;
            end
            if(dut.state==dut.ST_AFFINE_ADDRESS) affine_samples=affine_samples+1;
`ifdef AFFINE_FLOW_PRESENT
`ifdef AFFINE_ADVANCE_TUPLE_PRESENT
            if(dut.state==dut.ST_AFFINE_FLOW && dut.affine_flow_phase==dut.AF_ROW &&
               dut.u_next !== 32'(dut.u_current + dut.u_step))
                $fatal(1,"Affine next-U invariant");
            if(dut.state==dut.ST_AFFINE_FLOW && dut.affine_flow_phase==dut.AF_EMIT &&
               (dut.affine_u_lookahead !== 32'(dut.u_current + dut.u_step) ||
                dut.affine_v_lookahead !== 32'(dut.v_current + dut.v_x_step) ||
                dut.affine_u_next_lookahead !== 32'(dut.u_next + dut.u_step) ||
                dut.affine_x_lookahead !== 16'(dut.blit_x + 1'b1) ||
                dut.affine_last_lookahead !== (dut.blit_x + 1'b1 >= dut.blit_width) ||
                dut.affine_destination_lookahead !== 17'(dut.destination_linear + 1'b1)))
                $fatal(1,"Affine advance tuple mismatch");
`endif
            if(dut.state==dut.ST_AFFINE_FLOW && dut.affine_flow_phase==dut.AF_ACCEPT && !ddram_busy)
                affine_refill_in_progress<=1;
            if(dut.state==dut.ST_AFFINE_FLOW && dut.affine_flow_phase==dut.AF_DATA &&
               ddram_dout_ready && dut.affine_line_fill==AFFINE_LINE_BEATS-1)
                affine_refill_in_progress<=0;
            if(affine_refill_in_progress &&
               dut.affine_line_valid[dut.affine_line_way][dut.affine_line_set])
                $fatal(1,"Affine line became valid before final beat");
            if(dut.state==dut.ST_AFFINE_FLOW && dut.affine_flow_phase==dut.AF_EMIT && dut.affine_line_hit) begin
                flow_pixels=flow_pixels+1;
                if(dut.source_byte_addr[2]) flow_high=flow_high+1;
                else flow_low=flow_low+1;
            end
`endif
            if(binary_axis_active && dut.state==dut.ST_TEX_READY && dut.pair_mode) begin
                if(!dut.contiguous_pair_mode || dut.affine_mode || dut.tint_color!==32'hffffffff)
                    $fatal(1,"Binary axis fixture missed contiguous untinted path");
                binary_pair_kinds[{dut.texture_cache_data[dut.source_byte_addr[6:3]][63:56]==8'hff,
                                   dut.texture_cache_data[dut.source_byte_addr[6:3]][31:24]==8'hff}]=1;
            end
`ifdef TEX_PAIR_ADVANCE_PRESENT
            if(dut.state==dut.ST_TEX_PAIR_ADVANCE) begin
                pair_advances=pair_advances+1;
                if(dut.affine_mode || !dut.pair_mode || !dut.contiguous_pair_mode)
                    $fatal(1,"Pair advancement inherited invalid mode");
                if(dut.blit_x+2>=dut.blit_width) pair_row_advances=pair_row_advances+1;
                else if(dut.texture_cache_addr!=(((dut.source_byte_addr+32'd8)>>3)&29'h1fff_fff0))
                    pair_cache_advances=pair_cache_advances+1;
            end
`endif
            if(dut.state==dut.ST_TEX_READY && dut.affine_mode) begin
                if(dut.pair_mode) $fatal(1,"Affine unexpectedly used axis pair path");
                if(dut.source_byte_addr[2]) high_lanes=high_lanes+1;
                else low_lanes=low_lanes+1;
`ifdef AFFINE_CACHE_PRESENT
                if(!dut.affine_cache_valid[dut.affine_cache_index] ||
                   dut.affine_cache_tag[dut.affine_cache_index]!==dut.source_byte_addr[31:3])
                    $fatal(1,"Affine selected invalid/wrong cache tag");
`endif
            end
`ifdef AFFINE_CACHE_PRESENT
            if(dut.state==dut.ST_TEX_DATA && dut.affine_mode &&
               dut.affine_cache_valid[dut.affine_cache_index])
                $fatal(1,"Affine cache became valid before response consumption");
`endif
            if(ddram_we && !ddram_busy) begin
                if(pending_write) begin
                    write_memory(write_address,ddram_din,ddram_be);
                    write_address<=write_address+1'b1; write_remaining<=write_remaining-1;
                    if(write_remaining==1) pending_write<=0;
                end else begin
                    write_memory(ddram_addr,ddram_din,ddram_be);
                    if(ddram_burstcnt>1) begin
                        pending_write<=1; write_address<=ddram_addr+1'b1; write_remaining<=ddram_burstcnt-1;
                    end
                end
            end
            if(ddram_rd && !ddram_busy && !pending_read) begin
                if(ddram_burstcnt==0 || ddram_burstcnt>128) $fatal(1,"Invalid read burst");
                pending_read<=1; read_address<=ddram_addr; read_remaining<=ddram_burstcnt;
                read_delay<=schedule==0 ? 2 : 7;
                read_is_affine<=dut.state==dut.ST_TEX_ACCEPT && dut.affine_mode;
                if(dut.state==dut.ST_TEX_ACCEPT && dut.affine_mode) begin
                    affine_requests=affine_requests+1;
                    if(ddram_burstcnt!=(EXPECT_CACHE ? 1 : 16))
                        $fatal(1,"Wrong affine burst length: %0d",ddram_burstcnt);
                end
`ifdef AFFINE_FLOW_PRESENT
                if(dut.state==dut.ST_AFFINE_FLOW && dut.affine_flow_phase==dut.AF_ACCEPT) begin
                    flow_reads=flow_reads+1;
                    if(ddram_burstcnt!=AFFINE_LINE_BEATS || (ddram_addr & (AFFINE_LINE_BEATS-1))!=0)
                        $fatal(1,"Invalid affine line-fill alignment/length");
                end
`endif
                if(dut.state==dut.ST_GENERIC && dut.generic_state==dut.GP_TEXTURE_ACCEPT) begin
                    generic_reads=generic_reads+1;
                    if(ddram_burstcnt!=16) $fatal(1,"Generic cache burst changed");
                end
                if(dut.state==dut.ST_TEX_ACCEPT && !dut.affine_mode) begin
                    axis_reads=axis_reads+1;
                    if(ddram_burstcnt!=16) $fatal(1,"Axis cache burst changed");
                end
            end else if(pending_read) begin
                if(read_delay!=0) read_delay<=read_delay-1;
                else if(schedule==1 && cycles%5==1) response_gaps=response_gaps+1;
                else begin
                    ddram_dout<=read_memory(read_address); ddram_dout_ready<=1;
                    if(read_is_affine) affine_beats=affine_beats+1;
                    read_address<=read_address+1'b1; read_remaining<=read_remaining-1;
                    if(read_remaining==1) pending_read<=0;
                end
            end
        end
        if(cycles>20000000) $fatal(1,"Global cycle limit");
    end

    function automatic [31:0] reference_texel(input [31:0] address);
        integer index;
        begin
            if(address<SOURCE || address>=SOURCE+WORDS*8 || address[1:0]!=0)
                $fatal(1,"Oracle source outside memory %08h",address);
            index=(address-SOURCE)>>3;
            reference_texel=address[2] ? expected_source[index][63:32] : expected_source[index][31:0];
        end
    endfunction
    function automatic [31:0] tint_pixel(input [31:0] pixel,tint);
        integer c,product;
        begin
            for(c=0;c<4;c=c+1) begin
                product=pixel[c*8+:8]*tint[c*8+:8];
                tint_pixel[c*8+:8]=(product+127)/255;
            end
        end
    endfunction
    function automatic [31:0] blend_pixel(input [31:0] src,dst,input integer blend);
        integer c,s,d,a,value;
        begin
            a=src[31:24];
            for(c=0;c<4;c=c+1) begin
                s=src[c*8+:8]; d=dst[c*8+:8];
                if(blend==2) value=d*(255-s)/255;
                else if(blend==1) value=c==3 ? a+d : s*a/255+d;
                else value=c==3 ? a+d*(255-a)/255 : (s*a+d*(255-a))/255;
                blend_pixel[c*8+:8]=value>255 ? 255 : value;
            end
        end
    endfunction
    task automatic rectangle(input integer n,op,w,h,input [31:0] address,input integer stride,x,y);
        begin
            commands[n*8]=(64'(h)<<32)|(64'(w)<<16)|op;
            commands[n*8+1]={32'(stride),address};
            commands[n*8+2]={32'd0,16'(y),16'(x)};
        end
    endtask
    task automatic affine(input integer n,w,h,x,y,input [31:0] base,input integer stride,
        input integer u0,v0,ux,vx,uy,vy,input [31:0] tint,input integer blend);
        integer px,py,at;
        reg signed [31:0] u,v;
        reg [31:0] address,pixel;
        begin
            commands[n*8]=5|(blend==1 ? 256 : blend==2 ? 512 : 0);
            commands[n*8+1]=tint;
            rectangle(n+1,4,w,h,base,stride,x,y);
            commands[(n+1)*8+3]={32'h00400000,32'd0};
            commands[(n+1)*8+4]={32'h00400000,32'd0};
            commands[(n+1)*8+5]={32'(v0),32'(u0)};
            commands[(n+1)*8+6]={32'(vx),32'(ux)};
            commands[(n+1)*8+7]={32'(vy),32'(uy)};
            for(py=0;py<h;py=py+1) for(px=0;px<w;px=px+1) begin
                u=u0+px*ux+py*uy; v=v0+px*vx+py*vy;
                if(x+px>=0 && x+px<320 && y+py>=0 && y+py<240 &&
                   u>=0 && u<4194304 && v>=0 && v<4194304) begin
                    address=base+(v>>>16)*stride+(u>>>16)*4;
                    pixel=tint_pixel(reference_texel(address),tint); at=(y+py)*320+x+px;
                    expected_fb[at]=blend_pixel(pixel,expected_fb[at],blend);
                end
            end
        end
    endtask
    task automatic prepare;
        integer n;
        begin
            @(negedge clk);
            for(n=0;n<128;n=n+1) commands[n]=0;
            for(n=0;n<64;n=n+1) packet[n]=0;
            for(n=0;n<38400;n=n+1) begin
                dut.fb_even[n]=background(n*2); dut.fb_odd[n]=background(n*2+1);
                expected_fb[n*2]=background(n*2); expected_fb[n*2+1]=background(n*2+1);
            end
            for(n=0;n<WORDS;n=n+1) begin
                source[n]={seed_pixel(n*2+1),seed_pixel(n*2)}; expected_source[n]=source[n];
            end
        end
    endtask
    task automatic compare_everything;
        integer n;
        begin
            for(n=0;n<38400;n=n+1)
                if(dut.fb_even[n]!==expected_fb[n*2] || dut.fb_odd[n]!==expected_fb[n*2+1])
                    $fatal(1,"RGBA mismatch case%0d schedule%0d word%0d got%h_%h expected%h_%h",
                        cases_run,schedule,n,dut.fb_odd[n],dut.fb_even[n],expected_fb[n*2+1],expected_fb[n*2]);
            for(n=0;n<WORDS;n=n+1) if(source[n]!==expected_source[n])
                $fatal(1,"DDR mismatch case%0d word%0d",cases_run,n);
        end
    endtask
    task automatic submit(input integer count);
        integer start_cycle,r,b,s;
        begin
            @(negedge clk); r=affine_requests; b=affine_beats; s=affine_samples;
            sequence_number=sequence_number+1; control[1]={32'(count),32'h23fe0000};
            control[0]={sequence_number,32'h50473241}; start_cycle=cycles;
            while(control[3][31:0]!==sequence_number) begin
                @(negedge clk);
                if(cycles-start_cycle>1000000) $fatal(1,"Submission timeout state%0d",dut.state);
            end
            if(native_frame!==0 || pending_write) $fatal(1,"Unexpected publication/unfinished write");
            last_job_cycles=control[3][60:32]; last_requests=affine_requests-r;
            last_beats=affine_beats-b; last_samples=affine_samples-s;
            compare_everything(); cases_run=cases_run+1;
        end
    endtask
    task automatic reset_core;
        integer n;
        begin
            @(negedge clk); reset=1;
            for(n=0;n<11;n=n+1) control[n]=0;
            sequence_number=0;
            repeat(8) @(negedge clk);
`ifdef AFFINE_CACHE_PRESENT
            if(dut.affine_cache_valid!==0) $fatal(1,"Reset retained affine cache entries");
            reset_checks=reset_checks+1;
`endif
`ifdef AFFINE_FLOW_PRESENT
            for(n=0;n<8;n=n+1)
                if(dut.affine_line_valid[n]!==0) $fatal(1,"Reset retained affine line entries");
`endif
            reset=0; repeat(8) @(negedge clk);
        end
    endtask

`ifdef AFFINE_FLOW_PRESENT
    task automatic flow_reset_witness(input integer phase);
        integer started;
        begin
            prepare();
            affine(0,65,65,15,15,SOURCE+4,260,32768,32768,65536,0,0,65536,32'h91abcdef,1);
            commands[16]=12;
            @(negedge clk); sequence_number=sequence_number+1;
            control[1]={32'd3,32'h23fe0000}; control[0]={sequence_number,32'h50473241}; started=cycles;
            while(!(dut.state==dut.ST_AFFINE_FLOW && dut.affine_flow_phase==phase)) begin
                @(negedge clk);
                if(cycles-started>1000000) $fatal(1,"Reset witness phase not reached");
            end
            reset_core();
            flow_geometry(5,1,1);
        end
    endtask
`endif

    task automatic geometry_case(input integer which,blend,with_tint);
        integer w,h,x,y,u,v,ux,vx,uy,vy;
        reg [31:0] tint;
        begin
            prepare(); w=35;h=13;x=7+(which&1);y=9;
            u=16*65536;v=8*65536;ux=65536;vx=0;uy=0;vy=65536;
            case(which)
                0: begin w=8;h=8;u=16384;v=16384;ux=16384;vx=32768;vy=0;end
                1: begin w=44;h=44;v=-8*65536;ux=46341;vx=46341;uy=-46341;vy=46341;end
                2: begin u=40*65536+49152;v=20*65536+32768;ux=-73728;vx=32768;uy=32768;vy=-49152;end
                3: begin w=17;h=7;x=-3;y=-2;u=-32768;v=-65536;vx=32768;uy=16384;end
                4: begin h=5;u=31*65536+49152;v=49152;end
                5: begin w=64;h=4;u=32768;v=32768;vy=0;end
                6: begin w=66;h=2;u=-1;v=64*65536-1;vy=-1;end
                7: begin w=1;h=1;u=64*65536;v=-1;end
            endcase
            tint=with_tint ? 32'h81bfd731 : 32'hffffffff;
            affine(0,w,h,x,y,SOURCE+4,260,u,v,ux,vx,uy,vy,tint,blend);
            commands[16]=12; submit(3);
            if(which==7 && last_requests!=0) $fatal(1,"Rejected UV performed a texture read");
        end
    endtask
    task automatic reuse_case(input integer width);
        begin
            prepare();
            affine(0,width,2,20,20,SOURCE,256,32768,32768,65536,0,0,0,32'hffffffff,0);
            commands[16]=12; submit(3);
            if(EXPECT_CACHE && last_requests!=(width==32 ? 16 : 34))
                $fatal(1,"Reuse/eviction request count width%0d got%0d",width,last_requests);
            if(EXPECT_CACHE && last_beats!=last_requests) $fatal(1,"Multiple beats per affine miss");
        end
    endtask

`ifdef AFFINE_FLOW_PRESENT
    task automatic flow_geometry(input integer which,blend,with_tint);
        integer x,y,u,v,ux,vx,uy,vy,n,stride;
        reg [31:0] base;
        begin
            prepare(); x=13+(which&1); y=11;
            base=SOURCE+4; stride=260;
            u=32768; v=32768; ux=65536; vx=0; uy=0; vy=65536;
            case(which)
                0: begin u=63*65536;ux=0;vx=65536;uy=-65536;vy=0;end
                1: begin v=63*65536;ux=0;vx=-65536;uy=65536;vy=0;end
                2: begin u=12*65536;v=-9*65536;ux=46341;vx=46341;uy=-46341;vy=46341;end
                3: begin x=-7;y=-3;u=48*65536;v=40*65536;ux=-73728;vx=32768;uy=-16384;vy=-49152;end
                4: begin x=303;y=223;u=-32768;v=-32768;ux=81920;vx=16384;uy=16384;vy=73728;end
                5: begin u=32768;v=32768;ux=8192;vx=32768;uy=16384;vy=8192;end
                // Exercise nonzero upper stride bits and modulo32 carry.
                // A -8-byte stride overlaps rows but keeps all samples mapped.
                6: begin base=SOURCE+63*8+4;stride=-8;u=0;v=63*65536;ux=0;vx=-65536;uy=65536;vy=0;end
            endcase
            affine(0,65,65,x,y,base,stride,u,v,ux,vx,uy,vy,
                   with_tint ? 32'h81bfd731 : 32'hffffffff,blend);
            commands[16]=12; submit(3);
            // A following command sees updated bytes at identical addresses,
            // not lines retained from the preceding affine draw.
            for(n=0;n<WORDS;n=n+1) begin
                source[n]=source[n]^64'hff173951_ff3ba7c9; expected_source[n]=source[n];
            end
            affine(0,65,65,x,y,base,stride,u,v,ux,vx,uy,vy,
                   with_tint ? 32'h81bfd731 : 32'hffffffff,blend);
            submit(3);
        end
    endtask
    task automatic flow_threshold(input integer width,height);
        integer before_pixels;
        begin
            prepare(); before_pixels=flow_pixels;
            affine(0,width,height,13,11,SOURCE+4,260,0,0,65536,0,0,65536,32'h81bfd731,2);
            commands[16]=12; submit(3);
            if(width>=32 && height>=32) begin
                if(flow_pixels==before_pixels) $fatal(1,"Large-affine threshold missed flow path");
            end else if(flow_pixels!=before_pixels)
                $fatal(1,"Small affine incorrectly entered flow path");
        end
    endtask

    // Negative coordinates are actually sampled here, not merely rejected.
    // The second case deliberately maps unsigned16 negative V rows back into
    // the bounded source fixture through an offset base.
    task automatic flow_signed_samples(input integer which,blend,with_tint,large_path);
        integer px,py,at,stride,u0,v0,ux,vx,uy,vy,utexel,vrow,width;
        reg signed [31:0] u,v;
        reg [31:0] base,address,tint,pixel;
        begin
            prepare();
            width=large_path ? 33 : 31;
            tint=with_tint ? 32'h81bfd731 : 32'hffffffff;
            base=SOURCE+128; stride=260;
            u0=-16*65536+32768; v0=32768;
            ux=65536; vx=0; uy=0; vy=65536;
            if(which==1) begin
                base=SOURCE+128-65504*8; stride=8;
                v0=-32*65536+32768; ux=0; vx=65536; uy=65536; vy=0;
            end
            commands[0]=5|(blend==1 ? 256 : blend==2 ? 512 : 0);
            commands[1]=tint;
            rectangle(1,4,width,33,base,stride,20,20);
            commands[11]={32'(32*65536),32'(-32*65536)};
            commands[12]={32'(64*65536),32'd0};
            if(which==1) commands[12]={32'd0,32'(-32*65536)};
            commands[13]={32'(v0),32'(u0)};
            commands[14]={32'(vx),32'(ux)};
            commands[15]={32'(vy),32'(uy)};
            for(py=0;py<33;py=py+1) for(px=0;px<width;px=px+1) begin
                u=u0+px*ux+py*uy; v=v0+px*vx+py*vy;
                at=(20+py)*320+20+px;
                expected_fb[at]=background(at);
                if(u>=-32*65536 && u<32*65536 &&
                   (which==0 ? v>=0 && v<64*65536 : v>=-32*65536 && v<0)) begin
                    utexel=u>>>16; vrow={16'd0,v[31:16]};
                    address=base+vrow*stride+utexel*4;
                    pixel=tint_pixel(reference_texel(address),tint);
                    expected_fb[at]=blend_pixel(pixel,expected_fb[at],blend);
                end
            end
            commands[16]=12; submit(3);
        end
    endtask
`endif
    task automatic mutation_and_followers;
        integer px,py,at,index;
        reg [31:0] address,pixel;
        begin
            prepare();
            affine(0,6,3,30,30,SOURCE+4,264,0,0,65536,0,0,65536,32'hffffffff,0);
            // Export unrelated nonopaque BRAM pixels over the just-cached source
            // in THIS job. Source lanes and row padding must remain correct.
            rectangle(2,11,6,3,SOURCE+4,264,100,100);
            for(py=0;py<3;py=py+1) for(px=0;px<6;px=px+1) begin
                address=SOURCE+4+py*264+px*4; index=(address-SOURCE)>>3;
                pixel=expected_fb[(100+py)*320+100+px];
                if(address[2]) expected_source[index][63:32]=pixel;
                else expected_source[index][31:0]=pixel;
            end
            affine(3,6,3,40,30,SOURCE+4,264,0,0,65536,0,0,65536,32'hffffffff,0);
            // Ordinary axis after affine: deliberately short to use its cache.
            rectangle(5,2,7,3,SOURCE+4,264,53,30);
            commands[5*8+4]=0; commands[5*8+5]=64'h00010000_00010000;
            commands[5*8+6]=32'hffffffff;
            for(py=0;py<3;py=py+1) for(px=0;px<7;px=px+1) begin
                at=(30+py)*320+53+px; pixel=reference_texel(SOURCE+4+py*264+px*4);
                expected_fb[at]=blend_pixel(pixel,expected_fb[at],0);
            end
            // Generic raw replacement must retain source RGB behind alpha zero.
            rectangle(6,13,7,3,PACKET,0,65,30);
            packet[0]=64'h00000001_31504741; packet[1]={32'd264,SOURCE+4};
            packet[2]=(64'd15<<40)|(64'd64<<16)|64'd64;
            packet[18]=64'h100000000;packet[22]=64'h100000000;
            for(index=0;index<4;index=index+1) packet[23+index*4]=64'h100000000;
            for(py=0;py<3;py=py+1) for(px=0;px<7;px=px+1)
                expected_fb[(30+py)*320+65+px]=reference_texel(SOURCE+4+py*264+px*4);
            affine(7,6,3,75,30,SOURCE+4,264,5*65536,2*65536,-65536,0,0,-65536,32'h91abcdef,1);
            commands[9*8]=12; submit(10);
        end
    endtask
`ifdef AFFINE_FLOW_PRESENT
    task automatic flow_mutation_and_followers;
        integer px,py,index;
        reg [31:0] address,pixel;
        begin
            prepare();
            affine(0,33,33,20,20,SOURCE+4,264,0,0,65536,0,0,65536,32'hffffffff,1);
            rectangle(2,11,33,33,SOURCE+4,264,100,100);
            for(py=0;py<33;py=py+1) for(px=0;px<33;px=px+1) begin
                address=SOURCE+4+py*264+px*4; index=(address-SOURCE)>>3;
                pixel=expected_fb[(100+py)*320+100+px];
                if(address[2]) expected_source[index][63:32]=pixel;
                else expected_source[index][31:0]=pixel;
            end
            affine(3,33,33,60,20,SOURCE+4,264,0,0,65536,0,0,65536,32'h81bfd731,2);
            rectangle(5,2,7,3,SOURCE+4,264,95,20);
            commands[5*8+4]=0; commands[5*8+5]=64'h00010000_00010000;
            commands[5*8+6]=32'hffffffff;
            for(py=0;py<3;py=py+1) for(px=0;px<7;px=px+1) begin
                pixel=reference_texel(SOURCE+4+py*264+px*4);
                expected_fb[(20+py)*320+95+px]=blend_pixel(pixel,expected_fb[(20+py)*320+95+px],0);
            end
            rectangle(6,13,7,3,PACKET,0,110,20);
            packet[0]=64'h00000001_31504741; packet[1]={32'd264,SOURCE+4};
            packet[2]=(64'd15<<40)|(64'd64<<16)|64'd64;
            packet[18]=64'h100000000;packet[22]=64'h100000000;
            for(index=0;index<4;index=index+1) packet[23+index*4]=64'h100000000;
            for(py=0;py<3;py=py+1) for(px=0;px<7;px=px+1)
                expected_fb[(20+py)*320+110+px]=reference_texel(SOURCE+4+py*264+px*4);
            affine(7,33,33,150,20,SOURCE+4,264,32*65536,32*65536,-65536,0,0,-65536,32'h91abcdef,1);
            commands[9*8]=12; submit(10);
        end
    endtask
`endif
    task automatic binary_axis_boundaries(input integer odd_destination,odd_width,start_u);
        integer n,px,py,at,width,start_advance,start_row,start_cache;
        reg [31:0] low,high,pixel;
        begin
            prepare(); width=6+odd_width;
            // All four binary-alpha lane combinations, with distinct hidden
            // RGB in transparent lanes. A stride below 256 keeps this legacy
            // cache fixture outside guarded narrow DMA. Starting at texel28/30
            // crosses a128-byte texture line;
            // even/odd widths additionally exercise row ends and scalar tails.
            for(n=0;n<WORDS;n=n+1) begin
                low=seed_pixel(n*2); high=seed_pixel(n*2+1);
                low[31:24]=(n%4==0 || n%4==2) ? 255 : 0;
                high[31:24]=(n%4==0 || n%4==3) ? 255 : 0;
                source[n]={high,low}; expected_source[n]=source[n];
            end
            rectangle(0,2,width,4,SOURCE,248,8+odd_destination,170);
            commands[4]={32'd0,32'(start_u*65536)};
            commands[5]=64'h00010000_00010000; commands[6]=32'hffffffff;
            for(py=0;py<4;py=py+1) for(px=0;px<width;px=px+1) begin
                at=(170+py)*320+8+odd_destination+px;
                pixel=reference_texel(SOURCE+py*248+(start_u+px)*4);
                expected_fb[at]=blend_pixel(pixel,expected_fb[at],0);
            end
            commands[8]=12;
            start_advance=pair_advances; start_row=pair_row_advances; start_cache=pair_cache_advances;
            binary_axis_active=1; submit(2); binary_axis_active=0;
`ifdef TEX_PAIR_ADVANCE_PRESENT
            if(pair_advances==start_advance || pair_cache_advances==start_cache ||
               (!odd_width && pair_row_advances==start_row))
                $fatal(1,"Binary axis fixture missed pair cache/row advancement");
`endif
        end
    endtask
    initial begin
`ifdef TEX_PAIR_ADVANCE_PRESENT
        // Constant/time-zero guard: widening the state register alone does not
        // prevent a6-bit localparam from silently truncating64 to ST_RESET.
        if($bits(dut.state)<7 || $bits(dut.ST_TEX_PAIR_ADVANCE)<7 ||
           dut.ST_TEX_PAIR_ADVANCE!==7'd64 || dut.ST_TEX_PAIR_ADVANCE===dut.ST_RESET)
            $fatal(1,"Pair advancement state aliases/truncates RESET");
`endif
        for(i=0;i<11;i=i+1) control[i]=0;
        reset_core();
        for(schedule=0;schedule<2;schedule=schedule+1) begin
`ifdef AFFINE_FLOW_PRESENT
            flow_mutation_and_followers();
            flow_reset_witness(6); flow_reset_witness(7);
            flow_reset_witness(5); flow_reset_witness(8);
            flow_threshold(31,32); flow_threshold(32,31);
            flow_threshold(32,32); flow_threshold(33,32);
            for(kind=0;kind<2;kind=kind+1)
                for(mode=0;mode<3;mode=mode+1)
                    for(tinted=0;tinted<2;tinted=tinted+1) begin
                        flow_signed_samples(kind,mode,tinted,0);
                        flow_signed_samples(kind,mode,tinted,1);
                    end
            for(kind=0;kind<7;kind=kind+1)
                for(mode=0;mode<3;mode=mode+1)
                    for(tinted=0;tinted<2;tinted=tinted+1) flow_geometry(kind,mode,tinted);
`endif
            for(kind=0;kind<8;kind=kind+1)
                for(mode=0;mode<3;mode=mode+1)
                    for(tinted=0;tinted<2;tinted=tinted+1) geometry_case(kind,mode,tinted);
            mutation_and_followers(); reuse_case(32); reuse_case(34);
            for(kind=0;kind<2;kind=kind+1)
                for(mode=0;mode<2;mode=mode+1) begin
                    binary_axis_boundaries(kind,mode,28);
                    binary_axis_boundaries(kind,mode,30);
                end
            prepare();
            // Rotated/repeated short-row accesses thrash the old 128B cache,
            // but fit in the affine beat cache. Compare both RTL revisions.
            affine(0,8,32,120,50,SOURCE+4,260,16384,16384,16384,32768,0,0,32'hffffffff,0);
            commands[16]=12; submit(3);
            perf_cycles[schedule]=last_job_cycles; perf_requests[schedule]=last_requests; perf_beats[schedule]=last_beats;
            if(EXPECT_CACHE && !(last_requests<last_samples/4)) $fatal(1,"Reuse did not reduce reads");
`ifdef AFFINE_CACHE_PRESENT
            if(dut.affine_cache_valid==0) $fatal(1,"Reset test did not start with populated cache");
`endif
            reset_core();
            prepare();
            // Changed bytes at the same addresses after reset; first sample
            // must be fetched anew, never inherited from the prior command.
            for(i=0;i<WORDS;i=i+1) begin
                source[i]=source[i]^64'hff193751_ffa7c3e9; expected_source[i]=source[i];
            end
            affine(0,8,3,7,11,SOURCE+4,260,16384,16384,16384,32768,0,0,32'hffffffff,0);
            commands[16]=12; submit(3);
            if(last_requests==0) $fatal(1,"Post-reset draw performed no reads");
        end
        if(read_stalls==0 || response_gaps==0 || low_lanes==0 || high_lanes==0 || generic_reads==0 || axis_reads==0)
            $fatal(1,"Required coverage absent stalls%0d gaps%0d lanes%0d/%0d generic%0d axis%0d",
                read_stalls,response_gaps,low_lanes,high_lanes,generic_reads,axis_reads);
        if(binary_pair_kinds!==4'b1111) $fatal(1,"Missing binary-alpha lane combination");
`ifdef AFFINE_FLOW_PRESENT
        if(flow_reads==0 || flow_pixels==0 || flow_low==0 || flow_high==0)
            $fatal(1,"Affine flow coverage absent");
        $display("AFFINE_FLOW_PASS reads=%0d pixels=%0d low=%0d high=%0d",flow_reads,flow_pixels,flow_low,flow_high);
`endif
        $display("AFFINE_CACHE_PASS cases=%0d requests=%0d beats=%0d samples=%0d stalls=%0d gaps=%0d low=%0d high=%0d generic=%0d axis=%0d resets=%0d pair_advance=%0d pair_rows=%0d pair_cache=%0d binary_kinds=%0d",
            cases_run,affine_requests,affine_beats,affine_samples,read_stalls,response_gaps,low_lanes,high_lanes,generic_reads,axis_reads,reset_checks,
            pair_advances,pair_row_advances,pair_cache_advances,binary_pair_kinds);
        for(pass=0;pass<2;pass=pass+1)
            $display("AFFINE_CACHE_PERF schedule=%0d cycles=%0d requests=%0d beats=%0d",pass,perf_cycles[pass],perf_requests[pass],perf_beats[pass]);
        $finish;
    end
endmodule
