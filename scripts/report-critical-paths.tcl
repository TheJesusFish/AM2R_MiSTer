project_open AM2R -revision AM2R
create_timing_netlist
read_sdc
update_timing_netlist
report_timing -setup -npaths 10 -detail full_path -file output_files/AM2R.critical-paths.rpt
delete_timing_netlist
project_close
