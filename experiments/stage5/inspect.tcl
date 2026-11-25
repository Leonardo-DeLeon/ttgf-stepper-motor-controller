# Read-only inspection of a specific physical state.
read_db $::env(E01_ODB)
set report $::env(E01_REPORT)
set block [ord::get_db_block]
write_def $report/from-odb.def
set cells [open $report/instances.tsv w]
set pg [open $report/pg.tsv w]
set ports [open $report/ports.tsv w]

foreach inst [$block getInsts] {
    set master [$inst getMaster]
    set bbox [$inst getBBox]
    puts $cells [join [list [$inst getName] [$master getName] [$inst getPlacementStatus] \
        [$bbox xMin] [$bbox yMin] [$bbox xMax] [$bbox yMax]] "\t"]
    foreach term [$inst getITerms] {
        set pin [$term getMTerm]
        set type [$pin getSigType]
        if {$type ni {POWER GROUND}} {
            continue
        }
        set net [$term getNet]
        set name ""
        if {$net ne "NULL"} {
            set name [$net getName]
        }
        puts $pg [join [list [$inst getName] [$pin getName] $type $name] "\t"]
    }
}
foreach term [$block getBTerms] {
    set net [$term getNet]
    set count [llength [$net getITerms]]
    foreach pin [$term getBPins] {
        foreach box [$pin getBoxes] {
            set layer [$box getTechLayer]
            puts $ports [join [list [$term getName] [$term getIoType] [$term getSigType] \
                [$net getName] $count [$layer getName] \
                [$box xMin] [$box yMin] [$box xMax] [$box yMax]] "\t"]
        }
    }
}
close $cells
close $pg
close $ports

set placement [check_placement -verbose]
if {$placement ne "" && $placement ne "0"} {
    error "Placement check returned $placement"
}
puts "E01 placement checked"
foreach net {VPWR VGND} {
    set connected [check_power_grid -net $net -error_file $report/$net-errors.rpt]
    if {$connected eq "0"} {
        error "Disconnected grid for $net"
    }
    puts "E01 power grid checked $net"
}
