`timescale 1ns/1ps
`default_nettype none

module clock_enable_check #(
    parameter integer CLOCK_HZ = 1000,
    parameter integer HALF_NS = 500000,
    parameter integer CYCLES_0 = 10,
    parameter integer CYCLES_1 = 8,
    parameter integer CYCLES_2 = 6,
    parameter integer CYCLES_3 = 4
) (
    output reg done,
    output integer passed,
    output integer failed
);
    reg clk = 0;
    reg reset_n = 0;
    reg load = 0;
    reg active = 0;
    reg [1:0] speed = 0;
    wire tick;
    integer selection, period_cycles, cycle, mismatches;

    always #(HALF_NS) clk = !clk;

    clock_enable #(.CLK_HZ(CLOCK_HZ)) dut (
        .clk(clk), .reset_n(reset_n), .load(load), .active(active),
        .speed(speed), .tick(tick)
    );

    initial begin
        done = 0;
        passed = 0;
        failed = 0;
        @(negedge clk);
        reset_n = 1;

        for (selection = 0; selection < 4; selection = selection + 1) begin
            speed = selection[1:0];
            case (selection)
                0: period_cycles = CYCLES_0;
                1: period_cycles = CYCLES_1;
                2: period_cycles = CYCLES_2;
                3: period_cycles = CYCLES_3;
            endcase

            mismatches = 0;
            active = 1;
            load = 1;
            @(posedge clk);
            if (tick !== 1'b0) mismatches = mismatches + 1;
            @(negedge clk);
            load = 0;
            for (cycle = 1; cycle <= 3 * period_cycles; cycle = cycle + 1) begin
                @(posedge clk);
                if (tick !== ((cycle % period_cycles) == 0)) begin
                    if (mismatches == 0)
                        $display("ERROR timer hz=%0d speed=%0d cycle=%0d tick=%b",
                                 CLOCK_HZ, selection, cycle, tick);
                    mismatches = mismatches + 1;
                end
            end
            @(negedge clk);
            active = 0;
            repeat (3) begin
                @(posedge clk);
                if (tick !== 1'b0) mismatches = mismatches + 1;
            end
            @(negedge clk);
            if (mismatches == 0) begin
                passed = passed + 1;
                $display("COVER timer hz=%0d speed=%0d interval_cycles=%0d",
                         CLOCK_HZ, selection, period_cycles);
            end else begin
                failed = failed + 1;
                $display("ERROR timer hz=%0d speed=%0d mismatches=%0d",
                         CLOCK_HZ, selection, mismatches);
            end
        end
        done = 1;
    end
endmodule

module tb_clock_enable;
    wire [4:0] done;
    wire signed [31:0] good0, good1, good2, good3, good4;
    wire signed [31:0] bad0, bad1, bad2, bad3, bad4;

    clock_enable_check reduced(done[0], good0, bad0);
    clock_enable_check #(1000000, 500, 10000, 8000, 6000, 4000)
        tiny_tapeout(done[1], good1, bad1);
    clock_enable_check #(50000000, 10, 500000, 400000, 300000, 200000)
        fpga(done[2], good2, bad2);
    clock_enable_check #(32768, 15259, 328, 262, 197, 131)
        rounding(done[3], good3, bad3);
    clock_enable_check #(50, 10000000, 1, 1, 1, 1)
        minimum_interval(done[4], good4, bad4);

    initial begin
        wait (&done);
        $display("RESULT tb_clock_enable passed=%0d failed=%0d",
                 good0 + good1 + good2 + good3 + good4,
                 bad0 + bad1 + bad2 + bad3 + bad4);
        $finish;
    end

    initial begin
        #2000000000;
        $display("RESULT tb_clock_enable passed=0 failed=1");
        $finish;
    end
endmodule

`default_nettype wire
