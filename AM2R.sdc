derive_pll_clocks
derive_clock_uncertainty

# core specific constraints
# The combined framework reset is generated in clk_sys and is intentionally
# sampled by the first stage of the clk_gpu reset synchronizer. Timing that
# metastability-catching stage as a 20 -> 88 MHz functional path is invalid;
# the second stage remains fully timed in the destination domain.
set_false_path -to [get_registers {*|gpu_reset_meta}]

# The core adds pll_vid beyond the PLLs known by the pristine Template SDC.
# Repeat its framework clock grouping with that video clock included so
# TimeQuest does not time the intentional video/HDMI, video/audio, or
# video/HPS crossings as synchronous paths. CDC is handled by the framework
# and the explicit core synchronizers/FIFOs.
set_clock_groups -exclusive \
	-group [get_clocks { *|pll_vid|pll_video_inst|altera_pll_i|*[0].*|divclk}] \
    -group [get_clocks { *|pll|pll_inst|altera_pll_i|*[*].*|divclk}] \
	-group [get_clocks { pll_hdmi|pll_hdmi_inst|altera_pll_i|*[0].*|divclk}] \
	-group [get_clocks { pll_audio|pll_audio_inst|altera_pll_i|*[0].*|divclk}] \
	-group [get_clocks { spi_sck}] \
	-group [get_clocks { hdmi_sck}] \
	-group [get_clocks { *|h2f_user0_clk}] \
	-group [get_clocks { FPGA_CLK1_50 }] \
	-group [get_clocks { FPGA_CLK2_50 }] \
	-group [get_clocks { FPGA_CLK3_50 }]
