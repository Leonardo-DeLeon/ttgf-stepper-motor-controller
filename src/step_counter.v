`timescale 1ns/1ps
`default_nettype none

module step_counter (
    input  wire       clk,
    input  wire       reset_n,
    input  wire       load,
    input  wire [2:0] distance,
    input  wire       advance,
    output wire       last
);
    reg finite_move;
    reg [11:0] remaining_minus_one;
    reg [11:0] initial_count;

    always @* begin
        case (distance)
            3'd1: initial_count = 12'd63;
            3'd2: initial_count = 12'd127;
            3'd3: initial_count = 12'd255;
            3'd4: initial_count = 12'd511;
            3'd5: initial_count = 12'd1023;
            3'd6: initial_count = 12'd2047;
            3'd7: initial_count = 12'd4095;
            default: initial_count = 12'd0;
        endcase
    end

    assign last = finite_move && (remaining_minus_one == 0);

    always @(posedge clk or negedge reset_n) begin
        if (!reset_n) begin
            finite_move <= 1'b0;
            remaining_minus_one <= 12'd0;
        end else if (load) begin
            finite_move <= (distance != 0);
            remaining_minus_one <= initial_count;
        end else if (advance && finite_move && !last) begin
            remaining_minus_one <= remaining_minus_one - 1'b1;
        end
    end
endmodule

`default_nettype wire
