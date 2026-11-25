`timescale 1ns/1ps
`default_nettype none

module tt_um_ieee_bootcamp_stepper (
    input  wire [7:0] ui_in,
    output wire [7:0] uo_out,
    input  wire [7:0] uio_in,
    output wire [7:0] uio_out,
    output wire [7:0] uio_oe,
    input  wire       ena,
    input  wire       clk,
    input  wire       rst_n
);
    // Disable aborts the command with the same restart interlock as reset.
    wire reset_enabled_n = rst_n & ena;

    stepper_controller #(.CLK_HZ(1000000)) controller (
        .clk(clk), .reset_n(reset_enabled_n), .run(ui_in[0]),
        .dir(ui_in[1]), .mode(ui_in[2]), .speed(ui_in[4:3]), .distance(ui_in[7:5]),
        .in1(uo_out[0]), .in2(uo_out[1]), .in3(uo_out[2]), .in4(uo_out[3]),
        .busy(uo_out[4]), .done(uo_out[5])
    );

    assign uo_out[7:6] = 2'b00;
    assign uio_oe = 8'h00;
    assign uio_out = 8'h00;
endmodule

`default_nettype wire
