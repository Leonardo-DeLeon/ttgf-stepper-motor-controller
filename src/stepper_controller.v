`timescale 1ns/1ps
`default_nettype none

module stepper_controller #(
    parameter integer CLK_HZ = 50000000
) (
    input  wire       clk,
    input  wire       reset_n,
    input  wire       run,
    input  wire       dir,
    input  wire       mode,
    input  wire [1:0] speed,
    input  wire [2:0] distance,
    output wire       in1,
    output wire       in2,
    output wire       in3,
    output wire       in4,
    output wire       busy,
    output wire       done
);
    localparam [2:0] WAIT_LOW = 3'd0, IDLE = 3'd1, MOVE = 3'd2,
                     HOLD_LAST = 3'd3, COMPLETE = 3'd4;

    reg reset_meta, reset_sync_n;
    reg run_meta, run_sync;
    reg [6:0] config_meta, config_sync;
    reg dir_latched, mode_latched;
    reg [1:0] speed_latched;
    reg [2:0] state;

    // Asynchronous assertion and two-clock release.
    always @(posedge clk or negedge reset_n) begin
        if (!reset_n) begin
            reset_meta <= 1'b0;
            reset_sync_n <= 1'b0;
        end else begin
            reset_meta <= 1'b1;
            reset_sync_n <= reset_meta;
        end
    end

    // Initial ones prevent the synchronizer from supplying a fictitious RUN low.
    always @(posedge clk or negedge reset_sync_n) begin
        if (!reset_sync_n) begin
            run_meta <= 1'b1;
            run_sync <= 1'b1;
        end else begin
            run_meta <= run;
            run_sync <= run_meta;
        end
    end

    // Keep the external configuration stable around the RUN handshake.
    always @(posedge clk) begin
        config_meta <= {dir, mode, speed, distance};
        config_sync <= config_meta;
    end

    wire start = (state == IDLE) && run_sync;
    wire timer_active = ((state == MOVE) || (state == HOLD_LAST)) && run_sync;
    wire [1:0] timer_speed = start ? config_sync[4:3] : speed_latched;
    wire phase_mode = start ? config_sync[5] : mode_latched;
    wire tick, last;
    wire advance = (state == MOVE) && tick;
    wire release_coils = busy && (!run_sync || ((state == HOLD_LAST) && tick));

    assign busy = (state == MOVE) || (state == HOLD_LAST);
    assign done = (state == COMPLETE);

    always @(posedge clk or negedge reset_sync_n) begin
        if (!reset_sync_n) begin
            state <= WAIT_LOW;
            dir_latched <= 1'b0;
            mode_latched <= 1'b0;
            speed_latched <= 2'd0;
        end else begin
            case (state)
                WAIT_LOW: if (!run_sync) state <= IDLE;
                IDLE: if (start) begin
                    dir_latched <= config_sync[6];
                    mode_latched <= config_sync[5];
                    speed_latched <= config_sync[4:3];
                    state <= MOVE;
                end
                MOVE: begin
                    if (!run_sync)
                        state <= IDLE;
                    else if (tick && last)
                        state <= HOLD_LAST;
                end
                HOLD_LAST: begin
                    if (!run_sync)
                        state <= IDLE;
                    else if (tick)
                        state <= COMPLETE;
                end
                COMPLETE: if (!run_sync) state <= IDLE;
                default: state <= WAIT_LOW;
            endcase
        end
    end

    clock_enable #(.CLK_HZ(CLK_HZ)) timer (
        .clk(clk), .reset_n(reset_sync_n), .load(start), .active(timer_active),
        .speed(timer_speed), .tick(tick)
    );

    step_counter steps (
        .clk(clk), .reset_n(reset_sync_n), .load(start), .distance(config_sync[2:0]),
        .advance(advance), .last(last)
    );

    stepper_fsm phases (
        .clk(clk), .reset_n(reset_sync_n), .start(start), .advance(advance),
        .release_coils(release_coils), .half_step(phase_mode), .reverse(dir_latched),
        .coils({in1, in2, in3, in4})
    );
endmodule

`default_nettype wire
