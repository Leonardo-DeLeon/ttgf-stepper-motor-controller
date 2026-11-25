`timescale 1ns/1ps
`default_nettype none

module tb_step_counter;
    reg clk = 0;
    reg reset_n = 0;
    reg load = 0;
    reg [2:0] distance = 0;
    reg advance = 0;
    wire last;

    integer selection, count, step, mismatches, passed, failed;

    always #5 clk = !clk;
    step_counter dut (
        .clk(clk), .reset_n(reset_n), .load(load), .distance(distance),
        .advance(advance), .last(last)
    );

    initial begin
        passed = 0;
        failed = 0;
        @(negedge clk);
        if (last === 1'b0) passed = passed + 1;
        else failed = failed + 1;
        reset_n = 1;

        for (selection = 0; selection < 8; selection = selection + 1) begin
            mismatches = 0;
            count = (selection == 0) ? 4200 : (32 << selection);
            distance = selection[2:0];
            load = 1;
            @(negedge clk);
            load = 0;
            distance = ~distance;

            for (step = 1; step <= count; step = step + 1) begin
                if ((step % 13) == 0) begin
                    advance = 0;
                    @(negedge clk);
                end
                advance = 1;
                @(posedge clk);
                if (last !== ((selection != 0) && (step == count))) begin
                    if (mismatches == 0)
                        $display("ERROR step_counter distance=%0d step=%0d last=%b",
                                 selection, step, last);
                    mismatches = mismatches + 1;
                end
                @(negedge clk);
            end

            if (last !== (selection != 0)) mismatches = mismatches + 1;
            advance = 0;
            if (mismatches == 0) begin
                passed = passed + 1;
                $display("COVER counter distance=%0d transitions_checked=%0d",
                         selection, count);
            end else
                failed = failed + 1;
        end

        reset_n = 0;
        #1;
        if (last === 1'b0) passed = passed + 1;
        else failed = failed + 1;

        $display("RESULT tb_step_counter passed=%0d failed=%0d", passed, failed);
        $finish;
    end
endmodule

`default_nettype wire
