project_open AM2R -revision AM2R
create_timing_netlist
read_sdc
update_timing_netlist
report_timing -setup -npaths 40 -detail full_path -file data/artifacts/worst-setup-paths.txt
delete_timing_netlist
project_close
