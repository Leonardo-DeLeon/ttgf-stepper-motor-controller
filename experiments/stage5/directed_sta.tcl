# Use the same final corner views as STAPostPNR, one process per corner.
set sta_report_default_digits 6
define_corners $::env(E01_CORNER)
read_liberty -corner $::env(E01_CORNER) $::env(E01_LIB)
read_verilog $::env(E01_NL)
link_design tt_um_ieee_bootcamp_stepper
read_sdc $::env(E01_SDC)
set_propagated_clock [all_clocks]
read_spef -corner $::env(E01_CORNER) $::env(E01_SPEF)
report_parasitic_annotation -report_unannotated

set target [get_pins $::env(E01_DRIVER)]
if {[llength $target] != 1} { error "Missing unique ECO driver $::env(E01_DRIVER)" }
puts "E01 extracted corner $::env(E01_CORNER)"
foreach net [concat [list $::env(E01_NET)] $::env(E01_INPUT_NETS)] {
    report_net -digits 6 $net
}
foreach pin [concat [list $::env(E01_DRIVER)] $::env(E01_LOADS) $::env(E01_INPUT_PINS)] {
    set selected [get_pins $pin]
    if {[llength $selected] != 1} { error "Missing unique checkpoint cone pin $pin" }
    puts "E01 pin $pin"
    report_slews $pin
}
foreach pin $::env(E01_LOADS) {
    foreach delay {min max} {
        puts "E01 path $delay through $pin"
        report_checks -path_delay $delay -through $target -through [get_pins $pin] \
            -fields {slew capacitance fanout} -digits 6
    }
}
report_check_types -max_slew -max_capacitance -max_fanout -recovery -removal -min_pulse_width -violators
puts "E01 directed STA complete"
