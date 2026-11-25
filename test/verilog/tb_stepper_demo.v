`timescale 1ns/1ps
`default_nettype none

module tb_stepper_demo;
    reg clk = 0;
    reg reset_n = 0;
    reg run = 0, dir = 0, mode = 0;
    reg [1:0] speed = 0;
    reg [2:0] distance = 0;
    wire in1, in2, in3, in4, busy, done;
    wire [3:0] phases = {in1, in2, in3, in4};

    integer profile, step, elapsed, length, index, period_cycles;
    integer passed = 0, failed = 0, mismatches;
    reg [3:0] full_cycle [0:3];
    reg [3:0] half_cycle [0:7];
    reg [3:0] expected_phase;

    always #500 clk = !clk;
    stepper_controller #(.CLK_HZ(1000000)) dut (
        .clk(clk), .reset_n(reset_n), .run(run), .dir(dir), .mode(mode),
        .speed(speed), .distance(distance), .in1(in1), .in2(in2),
        .in3(in3), .in4(in4), .busy(busy), .done(done)
    );

    task check;
        input b, d;
        input [3:0] p;
        begin
            if ({busy, done, phases} !== {b, d, p}) begin
                if (mismatches == 0)
                    $display("ERROR demo profile=%0d step=%0d time=%0t expected=%b actual=%b",
                             profile, step, $time, {b, d, p}, {busy, done, phases});
                mismatches = mismatches + 1;
            end
        end
    endtask

    initial begin
`ifdef IVERILOG
        $dumpfile("../waveforms/stepper_demo.vcd");
        $dumpvars(0, reset_n, run, dir, mode, speed, distance, in1, in2, in3, in4, busy, done);
`endif
        full_cycle[0] = 4'hc; full_cycle[1] = 4'h6;
        full_cycle[2] = 4'h3; full_cycle[3] = 4'h9;
        half_cycle[0] = 4'h8; half_cycle[1] = 4'hc;
        half_cycle[2] = 4'h4; half_cycle[3] = 4'h6;
        half_cycle[4] = 4'h2; half_cycle[5] = 4'h3;
        half_cycle[6] = 4'h1; half_cycle[7] = 4'h9;

        repeat (8) @(negedge clk);
        reset_n = 1;
        for (profile = 0; profile < 5; profile = profile + 1) begin
            mismatches = 0;
            step = 0;
            dir = profile[0];
            mode = profile[1];
            speed = (profile == 4) ? 2'd3 : profile[1:0];
            distance = (profile == 4) ? 3'd1 : 3'd0;
            length = mode ? 8 : 4;
            period_cycles = 10000 - 2000 * speed;
            expected_phase = mode ? half_cycle[0] : full_cycle[0];
            repeat (1000) @(negedge clk);
            run = 1;
            repeat (2) begin
                @(negedge clk);
                check(0, 0, 0);
            end
            @(negedge clk);
            check(1, 0, expected_phase);

            for (step = 1; step <= (profile == 4 ? 64 : length + 1); step = step + 1) begin
                for (elapsed = 1; elapsed < period_cycles; elapsed = elapsed + 1) begin
                    @(negedge clk);
                    check(1, 0, expected_phase);
                end
                index = (dir ? length - (step % length) : step) % length;
                expected_phase = mode ? half_cycle[index] : full_cycle[index];
                @(negedge clk);
                check(1, 0, expected_phase);
            end

            if (profile == 4) begin
                repeat (period_cycles - 1) begin
                    @(negedge clk);
                    check(1, 0, expected_phase);
                end
                repeat (8000) begin
                    @(negedge clk);
                    check(0, 1, 0);
                end
            end
            run = 0;
            repeat (3) @(negedge clk);
            check(0, 0, 0);
            if (mismatches == 0) begin
                passed = passed + 1;
                $display("COVER demo profile=%0d interval_us=%0d mode=%b dir=%b distance=%b",
                         profile, period_cycles, mode, dir, distance);
            end else
                failed = failed + 1;
        end
        $display("RESULT tb_stepper_demo passed=%0d failed=%0d", passed, failed);
        $finish;
    end

    initial begin
        #800000000;
        $display("RESULT tb_stepper_demo passed=0 failed=1");
        $finish;
    end
endmodule

`default_nettype wire
