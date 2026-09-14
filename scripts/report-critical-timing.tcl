project_open AM2R -revision AM2R
create_timing_netlist
read_sdc
update_timing_netlist

report_timing -setup -npaths 40 -detail full_path \
    -file output_files/AM2R.setup-paths.rpt
report_timing -hold -npaths 20 -detail full_path \
    -file output_files/AM2R.hold-paths.rpt

set core_clock [get_clocks {*|pll|pll_inst|altera_pll_i|general[0].gpll~PLL_OUTPUT_COUNTER|divclk}]
set gpu_clock [get_clocks {*|pll|pll_inst|altera_pll_i|general[1].gpll~PLL_OUTPUT_COUNTER|divclk}]
set hdmi_clock [get_clocks {pll_hdmi|pll_hdmi_inst|*|*[0].*|divclk}]
report_timing -setup -npaths 30 -detail full_path \
    -from_clock $core_clock -to_clock $core_clock \
    -file output_files/AM2R.setup-core-to-core.rpt
report_timing -setup -npaths 30 -detail full_path \
    -from_clock $gpu_clock -to_clock $gpu_clock \
    -file output_files/AM2R.setup-gpu-to-gpu.rpt
report_timing -setup -npaths 30 -detail full_path \
    -from_clock $core_clock -to_clock $gpu_clock \
    -file output_files/AM2R.setup-core-to-gpu.rpt
report_timing -setup -npaths 30 -detail full_path \
    -from_clock $gpu_clock -to_clock $core_clock \
    -file output_files/AM2R.setup-gpu-to-core.rpt
report_timing -setup -npaths 30 -detail full_path \
    -from_clock $hdmi_clock -to_clock $hdmi_clock \
    -file output_files/AM2R.setup-hdmi-to-hdmi.rpt

delete_timing_netlist
project_close
