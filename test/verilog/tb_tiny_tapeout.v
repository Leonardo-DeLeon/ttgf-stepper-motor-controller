`timescale 1ns/1ps
`default_nettype none

module tb_tiny_tapeout;
    reg clk = 0;
    reg rst_n = 0;
    reg ena = 1;
    reg [7:0] ui_in = 0;
    reg [7:0] uio_in = 0;
    wire [7:0] uo_out, uio_out, uio_oe;
    integer passed = 0, failed = 0, errors = 0, cycles;

    always #500 clk = !clk;

    tt_um_ieee_bootcamp_stepper dut (
        .clk(clk), .rst_n(rst_n), .ena(ena), .ui_in(ui_in), .uo_out(uo_out),
        .uio_in(uio_in), .uio_out(uio_out), .uio_oe(uio_oe)
    );

    task check_outputs;
        input [7:0] expected;
        begin
            if ({uo_out, uio_out, uio_oe} !== {expected, 16'd0}) begin
                if (errors == 0)
                    $display("ERROR wrapper time=%0t expected=%h actual=%h",
                             $time, {expected, 16'd0}, {uo_out, uio_out, uio_oe});
                errors = errors + 1;
            end
        end
    endtask

    task check_group;
        begin
            if (errors != 0) failed = failed + 1;
            else passed = passed + 1;
            errors = 0;
        end
    endtask

    initial begin
        repeat (3) @(negedge clk);
        check_outputs(8'h00);
        rst_n = 1;
        // Distance 64, speed 11, half-step, reverse, RUN low.
        ui_in = 8'h3e;
        uio_in = 8'hff;
        repeat (8) @(negedge clk);
        check_outputs(8'h00);
        ui_in[0] = 1;
        repeat (3) @(negedge clk);
        check_outputs(8'h11);
        for (cycles = 1; cycles < 4000; cycles = cycles + 1) begin
            @(negedge clk);
            check_outputs(8'h11);
        end
        @(negedge clk);
        check_outputs(8'h19);
        check_group;

        #25 ena = 0;
        #1 check_outputs(8'h00);
        repeat (8) @(negedge clk);
        check_outputs(8'h00);
        ena = 1;
        repeat (12) begin
            @(negedge clk);
            check_outputs(8'h00);
        end
        check_group;

        ui_in[0] = 0;
        repeat (6) @(negedge clk);
        ui_in[0] = 1;
        repeat (3) @(negedge clk);
        check_outputs(8'h11);
        #25 rst_n = 0;
        #1 check_outputs(8'h00);
        check_group;

        $display("RESULT tb_tiny_tapeout passed=%0d failed=%0d", passed, failed);
        $finish;
    end
endmodule

`default_nettype wire
