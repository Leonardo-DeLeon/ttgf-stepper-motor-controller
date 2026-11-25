`timescale 1ns/1ps
`default_nettype none

module stepper_fsm (
    input  wire       clk,
    input  wire       reset_n,
    input  wire       start,
    input  wire       advance,
    input  wire       release_coils,
    input  wire       half_step,
    input  wire       reverse,
    output reg  [3:0] coils
);
    reg [2:0] phase;
    wire [2:0] first_phase = {2'b00, !half_step};
    wire [2:0] delta = half_step ? (reverse ? 3'd7 : 3'd1)
                                : (reverse ? 3'd6 : 3'd2);
    wire [2:0] next_phase = phase + delta;

    function [3:0] phase_pattern;
        input [2:0] index;
        begin
            case (index)
                3'd0: phase_pattern = 4'b1000;
                3'd1: phase_pattern = 4'b1100;
                3'd2: phase_pattern = 4'b0100;
                3'd3: phase_pattern = 4'b0110;
                3'd4: phase_pattern = 4'b0010;
                3'd5: phase_pattern = 4'b0011;
                3'd6: phase_pattern = 4'b0001;
                3'd7: phase_pattern = 4'b1001;
                default: phase_pattern = 4'b0000;
            endcase
        end
    endfunction

    always @(posedge clk or negedge reset_n) begin
        if (!reset_n) begin
            phase <= 3'd0;
            coils <= 4'b0000;
        end else if (release_coils) begin
            coils <= 4'b0000;
        end else if (start) begin
            phase <= first_phase;
            coils <= phase_pattern(first_phase);
        end else if (advance) begin
            phase <= next_phase;
            coils <= phase_pattern(next_phase);
        end
    end
endmodule

`default_nettype wire
