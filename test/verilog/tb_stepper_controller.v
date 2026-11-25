`timescale 1ns/1ps
`default_nettype none

module tb_stepper_controller;
    reg clk = 0;
    reg reset_n = 0;
    reg run = 0, dir = 0, mode = 0;
    reg [1:0] speed = 0;
    reg [2:0] distance = 0;
    wire [3:0] coils;
    wire busy, done;

    integer dir_index, mode_index, speed_index, distance_index, phase_index;
    integer passed = 0, failed = 0, mismatches = 0, checks = 0;
    integer case_start_checks;
    reg [3:0] full_seq [0:3];
    reg [3:0] half_seq [0:7];
    reg [8*32-1:0] label;

    always #5 clk = !clk;
    stepper_controller #(.CLK_HZ(1000)) dut (
        .clk(clk), .reset_n(reset_n), .run(run), .dir(dir), .mode(mode),
        .speed(speed), .distance(distance), .in1(coils[3]), .in2(coils[2]),
        .in3(coils[1]), .in4(coils[0]), .busy(busy), .done(done)
    );

    function [3:0] expected_coils;
        input integer half_mode;
        input integer reverse;
        input integer step;
        integer length, index;
        begin
            length = half_mode ? 8 : 4;
            index = step % length;
            if (reverse)
                index = (length - index) % length;
            expected_coils = half_mode ? half_seq[index] : full_seq[index];
        end
    endfunction

    task check_outputs;
        input expected_busy;
        input expected_done;
        input [3:0] expected_pattern;
        begin
            checks = checks + 1;
            if ({busy, done, coils} !== {expected_busy, expected_done, expected_pattern}) begin
                if (mismatches == 0)
                    $display("ERROR %0s time=%0t expected=%b actual=%b",
                             label, $time,
                             {expected_busy, expected_done, expected_pattern},
                             {busy, done, coils});
                mismatches = mismatches + 1;
            end
        end
    endtask

    task begin_case;
        input [8*32-1:0] name;
        begin
            label = name;
            mismatches = 0;
            case_start_checks = checks;
        end
    endtask

    task end_case;
        begin
            if (mismatches == 0) passed = passed + 1;
            else failed = failed + 1;
            $display("CASE %0s checks=%0d mismatches=%0d",
                     label, checks - case_start_checks, mismatches);
        end
    endtask

    task arm_and_start;
        input integer reverse;
        input integer half_mode;
        input integer rate;
        input integer travel;
        begin
            run = 0;
            dir = reverse[0];
            mode = half_mode[0];
            speed = rate[1:0];
            distance = travel[2:0];
            repeat (6) begin
                @(negedge clk);
                check_outputs(0, 0, 0);
            end
            run = 1;
            repeat (2) begin
                @(negedge clk);
                check_outputs(0, 0, 0);
            end
            @(negedge clk);
            check_outputs(1, 0, expected_coils(half_mode, reverse, 0));
        end
    endtask

    task next_step;
        input integer period_cycles;
        input [3:0] previous;
        input [3:0] expected;
        integer cycle;
        begin
            for (cycle = 1; cycle < period_cycles; cycle = cycle + 1) begin
                @(negedge clk);
                check_outputs(1, 0, previous);
            end
            @(negedge clk);
            check_outputs(1, 0, expected);
        end
    endtask

    task run_matrix_case;
        input integer reverse;
        input integer half_mode;
        input integer rate;
        input integer travel;
        integer number_steps, step, period_cycles;
        begin
            begin_case("matrix");
            $display("PROFILE matrix dir=%0d mode=%0d speed=%0d distance=%0d",
                     reverse, half_mode, rate, travel);
            period_cycles = 10 - (2 * rate);
            number_steps = (travel == 0) ? 19 : (32 << travel);
            arm_and_start(reverse, half_mode, rate, travel);

            // Change every configuration field after its required hold time.
            @(negedge clk);
            check_outputs(1, 0, expected_coils(half_mode, reverse, 0));
            dir = !dir; mode = !mode; speed = ~speed; distance = ~distance;
            next_step(period_cycles - 1, expected_coils(half_mode, reverse, 0),
                      expected_coils(half_mode, reverse, 1));
            for (step = 2; step <= number_steps; step = step + 1)
                next_step(period_cycles, expected_coils(half_mode, reverse, step - 1),
                          expected_coils(half_mode, reverse, step));

            if (travel != 0) begin
                repeat (period_cycles - 1) begin
                    @(negedge clk);
                    check_outputs(1, 0, expected_coils(half_mode, reverse, number_steps));
                end
                repeat (2 * period_cycles + 1) begin
                    @(negedge clk);
                    check_outputs(0, 1, 0);
                end
                run = 0;
                repeat (2) begin
                    @(negedge clk);
                    check_outputs(0, 1, 0);
                end
                @(negedge clk);
                check_outputs(0, 0, 0);
            end else begin
                run = 0;
                repeat (2) begin
                    @(negedge clk);
                    check_outputs(1, 0, expected_coils(half_mode, reverse, number_steps));
                end
                @(negedge clk);
                check_outputs(0, 0, 0);
            end

            if (mismatches == 0)
                $display("COVER matrix dir=%0d mode=%0d speed=%0d distance=%0d steps=%0d",
                         reverse, half_mode, rate, travel, number_steps);
            end_case;
        end
    endtask

    task stop_at_phase;
        input integer reverse;
        input integer half_mode;
        input integer target;
        integer step;
        begin
            begin_case("stop_at_phase");
            arm_and_start(reverse, half_mode, 3, 0);
            for (step = 1; step <= target; step = step + 1)
                next_step(4, expected_coils(half_mode, reverse, step - 1),
                          expected_coils(half_mode, reverse, step));
            // Assert stop so it is consumed on the edge due to advance.
            @(negedge clk);
            check_outputs(1, 0, expected_coils(half_mode, reverse, target));
            run = 0;
            repeat (2) begin
                @(negedge clk);
                check_outputs(1, 0, expected_coils(half_mode, reverse, target));
            end
            @(negedge clk);
            check_outputs(0, 0, 0);
            if (mismatches == 0)
                $display("COVER stop dir=%0d mode=%0d phase_index=%0d", reverse, half_mode, target);
            end_case;
        end
    endtask

    initial begin
        full_seq[0] = 4'hc; full_seq[1] = 4'h6;
        full_seq[2] = 4'h3; full_seq[3] = 4'h9;
        half_seq[0] = 4'h8; half_seq[1] = 4'hc;
        half_seq[2] = 4'h4; half_seq[3] = 4'h6;
        half_seq[4] = 4'h2; half_seq[5] = 4'h3;
        half_seq[6] = 4'h1; half_seq[7] = 4'h9;

        begin_case("reset_idle");
        repeat (3) begin
            @(negedge clk);
            check_outputs(0, 0, 0);
        end
        reset_n = 1;
        repeat (6) begin
            @(negedge clk);
            check_outputs(0, 0, 0);
        end
        end_case;

        for (dir_index = 0; dir_index < 2; dir_index = dir_index + 1)
            for (mode_index = 0; mode_index < 2; mode_index = mode_index + 1)
                for (speed_index = 0; speed_index < 4; speed_index = speed_index + 1)
                    for (distance_index = 0; distance_index < 8;
                         distance_index = distance_index + 1)
                        run_matrix_case(dir_index, mode_index, speed_index, distance_index);

        for (dir_index = 0; dir_index < 2; dir_index = dir_index + 1)
            for (mode_index = 0; mode_index < 2; mode_index = mode_index + 1)
                for (phase_index = 0; phase_index < (mode_index ? 8 : 4);
                     phase_index = phase_index + 1)
                    stop_at_phase(dir_index, mode_index, phase_index);

        begin_case("stop_during_final_hold");
        arm_and_start(0, 0, 3, 1);
        for (phase_index = 1; phase_index <= 64; phase_index = phase_index + 1)
            next_step(4, expected_coils(0, 0, phase_index - 1),
                      expected_coils(0, 0, phase_index));
        run = 0;
        repeat (2) begin
            @(negedge clk);
            check_outputs(1, 0, 4'hc);
        end
        repeat (10) begin
            @(negedge clk);
            check_outputs(0, 0, 0);
        end
        end_case;

        begin_case("reset_moving_and_run_high");
        arm_and_start(1, 1, 0, 7);
        next_step(10, 4'h8, 4'h9);
        #2 reset_n = 0;
        #1 check_outputs(0, 0, 0);
        repeat (3) @(negedge clk);
        reset_n = 1;
        repeat (40) begin
            @(negedge clk);
            check_outputs(0, 0, 0);
        end
        arm_and_start(0, 0, 3, 0);
        next_step(4, 4'hc, 4'h6);
        end_case;

        $display("CHECKS tb_stepper_controller count=%0d", checks);
        $display("RESULT tb_stepper_controller passed=%0d failed=%0d", passed, failed);
        $finish;
    end

    initial begin
        #20000000;
        $display("RESULT tb_stepper_controller passed=0 failed=1");
        $finish;
    end
endmodule

`default_nettype wire
