`timescale 1ns/1ps
`default_nettype none

module tb_stepper_fsm;
    reg clk = 0;
    reg reset_n = 0;
    reg start = 0;
    reg advance = 0;
    reg release_coils = 0;
    reg half_step = 0;
    reg reverse = 0;
    wire [3:0] coils;

    integer profile, index, length, step, mismatches, passed, failed;
    reg [3:0] full_cycle [0:3];
    reg [3:0] half_cycle [0:7];

    always #5 clk = !clk;
    stepper_fsm dut (
        .clk(clk), .reset_n(reset_n), .start(start), .advance(advance),
        .release_coils(release_coils), .half_step(half_step), .reverse(reverse),
        .coils(coils)
    );

    task compare_coils;
        input [3:0] expected;
        begin
            if (coils !== expected) begin
                $display("ERROR phase profile=%0d step=%0d expected=%b actual=%b",
                         profile, step, expected, coils);
                mismatches = mismatches + 1;
            end
        end
    endtask

    initial begin
        full_cycle[0] = 4'hc; full_cycle[1] = 4'h6;
        full_cycle[2] = 4'h3; full_cycle[3] = 4'h9;
        half_cycle[0] = 4'h8; half_cycle[1] = 4'hc;
        half_cycle[2] = 4'h4; half_cycle[3] = 4'h6;
        half_cycle[4] = 4'h2; half_cycle[5] = 4'h3;
        half_cycle[6] = 4'h1; half_cycle[7] = 4'h9;
        passed = 0;
        failed = 0;

        @(negedge clk);
        if (coils === 4'b0) passed = passed + 1;
        else failed = failed + 1;
        reset_n = 1;

        for (profile = 0; profile < 4; profile = profile + 1) begin
            half_step = profile[1];
            reverse = profile[0];
            length = half_step ? 8 : 4;
            index = 0;
            mismatches = 0;
            start = 1;
            @(negedge clk);
            start = 0;
            for (step = 0; step <= 2 * length; step = step + 1) begin
                compare_coils(half_step ? half_cycle[index] : full_cycle[index]);
                @(negedge clk);
                compare_coils(half_step ? half_cycle[index] : full_cycle[index]);
                if (step < 2 * length) begin
                    advance = 1;
                    @(negedge clk);
                    advance = 0;
                    index = (index + (reverse ? length - 1 : 1)) % length;
                end
            end
            release_coils = 1;
            advance = 1;
            @(negedge clk);
            compare_coils(4'b0);
            advance = 0;
            release_coils = 0;
            if (mismatches == 0) begin
                passed = passed + 1;
                $display("COVER phase mode=%b dir=%b", half_step, reverse);
            end else
                failed = failed + 1;
        end

        $display("RESULT tb_stepper_fsm passed=%0d failed=%0d", passed, failed);
        $finish;
    end
endmodule

`default_nettype wire
