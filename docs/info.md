<!-- Stage 6C adds the E01 implementation summary and publication attribution. -->

## How it works

This programmable stepper motor controller generates four logic signals for an external driver and a 5 V 28BYJ-48 unipolar motor. It runs from a 1 MHz clock on `clk`. On a rising RUN command, the controller captures direction, stepping mode, speed, and distance. It applies the initial coil pattern and advances through the selected sequence at regular intervals.

| Signal | Tiny Tapeout port | Function |
| --- | --- | --- |
| RUN | `ui_in[0]` | Raise to start after the controller has observed RUN low. Lower to stop or rearm. |
| DIR | `ui_in[1]` | 0 selects the forward (nominal CW) sequence. 1 selects the reverse (nominal CCW) sequence. Shaft direction depends on wiring and viewing orientation. |
| MODE | `ui_in[2]` | 0 selects two-phase full-step. 1 selects half-step, alternating one and two energized phases. |
| SPEED | `ui_in[4:3]` | Step intervals of 10, 8, 6, or 4 ms for codes `00`, `01`, `10`, or `11`, respectively. |
| DISTANCE | `ui_in[7:5]` | `000` runs continuously. Codes `001` through `111` select 64, 128, 256, 512, 1024, 2048, or 4096 steps, respectively. |
| IN1–IN4 | `uo_out[0]`–`uo_out[3]` | Active-high logic outputs to the corresponding driver inputs. |
| BUSY | `uo_out[4]` | High during motion and the final hold interval. |
| DONE | `uo_out[5]` | High after a finite move completes. Clears after RUN goes low and the controller sees it. |

At the end of a finite move, the controller holds the last coil pattern for one more step interval, releases the coils, clears BUSY, and raises DONE. Continuous motion runs until RUN goes low. Stopping a move early releases the coils without asserting DONE. Pulling `rst_n` low or taking `ena` low through the project select logic clears the coils and both status outputs. After reset or re-enable, RUN must be observed low before a new command can start. Reserved outputs `uo_out[7:6]` are tied low. All eight bidirectional pins are unused and remain in input mode.

## Compact implementation

| Item | ASIC V2.0 Compact (E01) |
| --- | --- |
| Technology | GF180D (180 nm) |
| Clock frequency | 1 MHz |
| Implementation dimensions | 160 × 100 µm |
| Implementation area | 16,000 µm² |
| Functional and auxiliary cell area | 9,988.16 µm² |
| RTL verification | 198/198 tests passed |
| Gate-level verification | 2/2 tests passed |
| Timing analysis | Nine timing corners verified |
| Physical checks | Geometrical DRC, LVS, and antenna checks passed |
| Pending review | Upper-level integration and density review, including M2.4, M3.4, M4.4, M5.4, and MT.3 |

The `tiles: "1x1"` metadata value identifies the standard GF180D tile in the documentation schema. Acceptance of the 160 × 100 µm compact outline requires separate confirmation. The collective credit “IEEE Open Silicon Bootcamp 2026 Team” is provisional and awaiting confirmation.

## How to test

The following bench procedure describes the expected results. RTL and gate-level simulation evidence is available for the compact implementation. Motor and breakout tests are pending.

1. Start with the motor power off and observe IN1–IN4, BUSY, and DONE with a logic analyzer or equivalent. Select the project so `ena` is high, apply a 1 MHz clock, hold `rst_n` low for at least two clock cycles, and hold RUN low. Verify that all six observed outputs are low.
2. Release `rst_n` and keep RUN low for at least five rising clock edges. Set the command bits and hold them stable for at least three clock periods before and three after raising RUN.
3. Set DIR = 0, MODE = 0, SPEED = `11`, and DISTANCE = `001`. At BUSY assertion, expect `1100` in IN1, IN2, IN3, IN4 order. The first transitions are `0110`, `0011`, `1001`, and `1100`, spaced 4 ms apart. Count 64 transitions after the initial pattern. Expect the last transition 256 ms after BUSY rises, and BUSY low, DONE high, and the coils off at 260 ms. Lower RUN and check that DONE clears after synchronization.
4. Repeat with MODE = 1 and DIR = 0. Expect the initial pattern `1000`, followed by `1100`, `0100`, `0110`, `0010`, `0011`, `0001`, and `1001`. After rearming, repeat with DIR = 1 and verify the reverse sequence. Check the other three SPEED codes by measuring the interval between transitions.
5. Select DISTANCE = `000` and hold RUN high. Verify that BUSY remains high and DONE stays low while the phases repeat. Lower RUN and verify that the phases and BUSY clear. During a separate move, assert reset or disable the project while RUN is high. Check that all outputs clear and that motion stays blocked after re-enable until RUN is observed low and raised again.
6. After confirming logic-level compatibility and the wiring below, repeat at a slow interval with the driver and motor powered. Check physical direction, reliable motion, and coil release. The controller counts electrical step transitions in open loop, so checking shaft displacement requires a separate measurement.

## External hardware

- Use a ULN2003 or ULN2003A driver and a 5 V 28BYJ-48 unipolar stepper motor. Connect controller outputs IN1–IN4 to ULN2003 inputs 1–4. Connect the corresponding ULN2003 collector outputs to the four motor winding ends. Verify the module and motor connector pinouts before power-up.
- Connect the motor common lead and ULN2003 clamp-diode COM to the external +5 V motor supply. Tie the driver ground, motor supply return, and controller or breakout ground together. Use a regulated motor supply sized for two energized windings. Keep the motor supply off the logic pins.
- Power the ASIC core and I/O rails as specified for the actual GF180 breakout. Confirm I/O voltage, output drive, and the driver module's input thresholds and current, including any indicator LEDs. Use a suitable buffer or level interface if required and verify its idle state during reset and power-up. [ULN2003A datasheet](https://www.ti.com/lit/ds/symlink/uln2003a.pdf)
