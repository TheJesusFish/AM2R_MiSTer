# Invoked by check-fpga-timing.ps1 after a completed fit, with no competing
# AM2R Quartus process. Arguments: project directory, NEW report directory.
# This opens the post-fit netlist without changing assignments, running a flow,
# saving the report database, or replacing the normal compilation reports.
# Quartus may update internal delay caches in db/. Use an isolated project copy
# instead if byte-for-byte preservation of the fitted database is required.
#
# Quartus 17 Tcl help: get_available_operating_conditions -all enumerates the
# device's installed conditions; set_operating_conditions + update_timing_netlist
# selects each. report_timing -multi_corner has NO effect with -file/-stdout,
# so an explicit loop is essential. report_timing returns {path_count slack};
# get_min_pulse_width returns rows beginning with slack. Pulse reporting also
# checks minimum period. No speed, grade, temperature, clock or SDC overrides.

proc timing_tsv {handle fields} {
    set clean {}
    foreach field $fields {
        lappend clean [string map [list "\t" " " "\r" " " "\n" " "] $field]
    }
    puts $handle [join $clean "\t"]
    flush $handle
}

proc timing_require_report {path} {
    if {![file isfile $path] || [file size $path] == 0} {
        error "Missing or empty timing report: $path"
    }
}

proc timing_status {count slack label} {
    global timing_missing timing_violations
    if {![string is integer -strict $count] || $count < 0} {
        error "Invalid path count at $label: '$count'"
    }
    if {$count == 0} {
        incr timing_missing
        return MISSING_CHECK
    }
    # Reject NaN/infinities as well as malformed text; only finite decimal
    # nanoseconds constitute a usable timing result.
    if {![regexp {^[+-]?[0-9]+(\.[0-9]+)?([eE][+-]?[0-9]+)?$} $slack]} {
        error "Invalid slack at $label: '$slack'"
    }
    if {$slack < 0.0} {
        incr timing_violations
        return FAIL
    }
    return PASS
}

if {![info exists quartus(args)] || [llength $quartus(args)] != 2} {
    error "Use check-fpga-timing.ps1; expected project and NEW report directories."
}
set timing_project [file normalize [lindex $quartus(args) 0]]
set timing_output [file normalize [lindex $quartus(args) 1]]
foreach required {AM2R.qpf AM2R.qsf AM2R.sdc AM2R.sv files.qip db output_files/AM2R.fit.summary output_files/AM2R.rbf} {
    if {![file exists [file join $timing_project $required]]} {
        error "Incomplete post-fit project: missing $required"
    }
}
if {[file exists $timing_output]} { error "Refusing to replace existing timing reports: $timing_output" }
file mkdir $timing_output
set timing_started [clock seconds]
set timing_project_open 0
set timing_netlist_open 0
set timing_handles {}
set timing_violations 0
set timing_missing 0
set timing_corners 0

set timing_error [catch {
    cd $timing_project
    project_open AM2R -revision AM2R
    set timing_project_open 1
    set workers [get_global_assignment -name NUM_PARALLEL_PROCESSORS]
    # Worker policy belongs to the QSF/build host, not the timing acceptance
    # criteria. Record it without rewriting or rejecting the builder's choice.
    if {$workers eq ""} { set workers "tool default (no assignment)" }
    set metadata [open [file join $timing_output metadata.txt] w]
    lappend timing_handles $metadata
    puts $metadata "Quartus: $quartus(version)"
    puts $metadata "Project: $timing_project"
    puts $metadata "Device: [get_global_assignment -name DEVICE]"
    puts $metadata "Processors: $workers"
    puts $metadata "Started UTC: [clock format $timing_started -gmt 1 -format {%Y-%m-%dT%H:%M:%SZ}]"
    puts $metadata "All installed operating conditions; no speed/grade/temperature overrides."
    puts $metadata "A timing pass does not establish that every path is constrained."
    flush $metadata

    create_timing_netlist
    set timing_netlist_open 1
    read_sdc
    update_timing_netlist
    report_clocks -file [file join $timing_output clocks.rpt]
    report_sdc -file [file join $timing_output constraints.rpt]
    report_sdc -ignored -file [file join $timing_output ignored-constraints.rpt]
    report_ucp -file [file join $timing_output unconstrained-paths.rpt]
    check_timing -include {no_clock multiple_clock generated_clock loops latches no_input_delay no_output_delay partial_input_delay partial_output_delay uncertainty} \
        -file [file join $timing_output constraint-checks.rpt]
    foreach name {clocks.rpt constraints.rpt ignored-constraints.rpt unconstrained-paths.rpt constraint-checks.rpt} {
        timing_require_report [file join $timing_output $name]
    }

    set configured_conditions {}
    foreach_in_collection condition [get_available_operating_conditions] {
        lappend configured_conditions [get_operating_conditions_info $condition -name]
    }
    if {[llength $configured_conditions] == 0} { error "No configured operating conditions were found." }
    set corner_table [open [file join $timing_output operating-conditions.tsv] w]
    lappend timing_handles $corner_table
    # The default collection selects endpoint models. It does not list every
    # installed temperature that numerically lies within the configured range.
    timing_tsv $corner_table {index object display_name model voltage_mV temperature_C speed grade selected_by_default}
    set slack_table [open [file join $timing_output worst-slack.tsv] w]
    lappend timing_handles $slack_table
    timing_tsv $slack_table {corner selected_by_default check paths_reported worst_slack_ns status}
    set seen_conditions {}
    foreach_in_collection condition [get_available_operating_conditions -all] {
        incr timing_corners
        set name [get_operating_conditions_info $condition -name]
        if {[lsearch -exact $seen_conditions $name] >= 0} { error "Duplicate operating condition: $name" }
        lappend seen_conditions $name
        set display [get_operating_conditions_info $condition -display_name]
        set configured [expr {[lsearch -exact $configured_conditions $name] >= 0}]
        timing_tsv $corner_table [list $timing_corners $name $display \
            [get_operating_conditions_info $condition -model] \
            [get_operating_conditions_info $condition -voltage] \
            [get_operating_conditions_info $condition -temperature] \
            [get_operating_conditions_info $condition -speed] \
            [get_operating_conditions_info $condition -grade] $configured]
        puts "TIMING corner $timing_corners: $display (selected by default: $configured)"
        set_operating_conditions $condition
        update_timing_netlist
        set stem [format "corner-%02d" $timing_corners]
        foreach kind {setup hold recovery removal} {
            set report [file join $timing_output "$stem-$kind.rpt"]
            set result [report_timing -$kind -npaths 10 -detail full_path -file $report]
            timing_require_report $report
            set count [lindex $result 0]
            set slack [lindex $result 1]
            set status [timing_status $count $slack "$display/$kind"]
            timing_tsv $slack_table [list $timing_corners $configured $kind $count $slack $status]
        }
        set report [file join $timing_output "$stem-pulse-width.rpt"]
        report_min_pulse_width -nworst 10 -detail full_path -file $report
        timing_require_report $report
        set pulse_checks [get_min_pulse_width -nworst 1]
        set count [llength $pulse_checks]
        set slack [lindex [lindex $pulse_checks 0] 0]
        set status [timing_status $count $slack "$display/pulse_width"]
        timing_tsv $slack_table [list $timing_corners $configured pulse_width $count $slack $status]
        set report [file join $timing_output "$stem-fmax.rpt"]
        report_clock_fmax_summary -file $report
        timing_require_report $report
    }
    if {$timing_corners == 0} { error "No operating conditions were enumerated." }
    foreach name $configured_conditions {
        if {[lsearch -exact $seen_conditions $name] < 0} { error "Configured corner not audited: $name" }
    }
    puts $metadata "Completed corners: $timing_corners"
    puts $metadata "Negative checks across all available corners: $timing_violations"
    puts $metadata "Missing checks across all available corners: $timing_missing"
    puts $metadata "Elapsed seconds: [expr {[clock seconds] - $timing_started}]"
    puts $metadata "REPORTS_COMPLETE; constraint review remains separate and mandatory."
} timing_message]

foreach handle $timing_handles { catch {close $handle} }
if {$timing_netlist_open} {
    if {[catch {delete_timing_netlist} close_message] && !$timing_error} {
        set timing_error 1
        set timing_message $close_message
    }
}
if {$timing_project_open} {
    if {[catch {project_close -dont_export_assignments} close_message] && !$timing_error} {
        set timing_error 1
        set timing_message $close_message
    }
}
if {$timing_error} {
    puts stderr "TIMING INCOMPLETE: $timing_message"
    exit 1
}
if {$timing_violations || $timing_missing} {
    puts stderr "TIMING FAILED: negative slack or missing checks; inspect worst-slack.tsv."
    exit 2
}
puts "ALL-CORNER TIMING PASS: all five checks passed at $timing_corners conditions. Constraint review is separate."
exit 0
