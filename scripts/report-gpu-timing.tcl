project_open AM2R -revision AM2R
create_timing_netlist
read_sdc
update_timing_netlist
report_timing -setup -npaths 20 -detail full_path -panel_name GPU_Setup -file output_files/AM2R.gpu_setup.rpt
delete_timing_netlist
project_close
