// Adapted from the Tiny Tapeout GF180 Verilog template.
`timescale 1ns/1ps
`default_nettype none

module tb;
    reg clk, rst_n, ena;
    reg [7:0] ui_in, uio_in;
    wire [7:0] uo_out, uio_out, uio_oe;
`ifdef GL_TEST
    supply1 VPWR;
    supply0 VGND;
`endif

    initial begin
        $dumpfile("tb.fst");
        // Record the module interface without dumping internal gate nets.
        $dumpvars(1, tb);
    end

    tt_um_ieee_bootcamp_stepper user_project (
`ifdef GL_TEST
        .VPWR(VPWR),
        .VGND(VGND),
`endif
        .clk(clk), .rst_n(rst_n), .ena(ena),
        .ui_in(ui_in), .uo_out(uo_out), .uio_in(uio_in),
        .uio_out(uio_out), .uio_oe(uio_oe)
    );
endmodule

`default_nettype wire
