`timescale 1ns/1ps
`default_nettype none

module clock_enable #(
    parameter integer CLK_HZ = 50000000
) (
    input  wire       clk,
    input  wire       reset_n,
    input  wire       load,
    input  wire       active,
    input  wire [1:0] speed,
    output wire       tick
);
    // Round to the nearest cycle and clamp the shortest interval to one.
    function integer interval_cycles;
        input integer milliseconds;
        reg [63:0] rounded;
        begin
            rounded = (64'd1 * CLK_HZ * milliseconds + 64'd500) / 64'd1000;
            interval_cycles = (rounded == 0) ? 1 : rounded[31:0];
        end
    endfunction

    function integer counter_width;
        input integer max_value;
        begin
            counter_width = 1;
            while (max_value > 1) begin
                max_value = max_value >> 1;
                counter_width = counter_width + 1;
            end
        end
    endfunction

    localparam integer WIDTH = counter_width(interval_cycles(10) - 1);
    localparam [WIDTH-1:0] RELOAD_10MS = interval_cycles(10) - 1;
    localparam [WIDTH-1:0] RELOAD_8MS  = interval_cycles(8) - 1;
    localparam [WIDTH-1:0] RELOAD_6MS  = interval_cycles(6) - 1;
    localparam [WIDTH-1:0] RELOAD_4MS  = interval_cycles(4) - 1;

    reg [WIDTH-1:0] remaining;
    reg [WIDTH-1:0] reload_value;

    always @* begin
        case (speed)
            2'b00: reload_value = RELOAD_10MS;
            2'b01: reload_value = RELOAD_8MS;
            2'b10: reload_value = RELOAD_6MS;
            2'b11: reload_value = RELOAD_4MS;
            default: reload_value = RELOAD_10MS;
        endcase
    end

    assign tick = reset_n && active && !load && (remaining == 0);

    always @(posedge clk or negedge reset_n) begin
        if (!reset_n)
            remaining <= {WIDTH{1'b0}};
        else if (load || tick)
            remaining <= reload_value;
        else if (active)
            remaining <= remaining - 1'b1;
    end
endmodule

`default_nettype wire
