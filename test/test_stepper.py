"""Exercise the public Tiny Tapeout interface in RTL and gate-level runs."""
import cocotb
from cocotb.clock import Clock
from cocotb.triggers import ClockCycles, FallingEdge, Timer

BUSY = 0x10
DONE = 0x20
RUN = 0x01
STEP_CYCLES = 4000  # 4 ms at 1 MHz


def check_outputs(dut, expected):
    observed = int(dut.uo_out.value), int(dut.uio_out.value), int(dut.uio_oe.value)
    assert observed == (expected, 0, 0), f"uo_out/uio_out/uio_oe = {observed}, expected {(expected, 0, 0)}"


async def cycles(dut, count):
    # The falling edge samples outputs after the positive-edge logic settles.
    await ClockCycles(dut.clk, count, FallingEdge)


async def reset(dut, command):
    dut.rst_n.value = 0
    dut.ena.value = 1
    dut.ui_in.value = command
    dut.uio_in.value = 0xFF
    Clock(dut.clk, 1, unit="us").start()
    await cycles(dut, 3)
    check_outputs(dut, 0)
    dut.rst_n.value = 1
    await cycles(dut, 8)


@cocotb.test()
async def finite_full_step(dut):
    command = 0x38  # 64 steps, 4 ms, clockwise full-step.
    phases = (0x03, 0x06, 0x0C, 0x09)  # Bit zero is IN1.
    await reset(dut, command)
    check_outputs(dut, 0)
    dut.ui_in.value = command | RUN
    await cycles(dut, 2)
    check_outputs(dut, 0)
    await cycles(dut, 1)
    check_outputs(dut, BUSY | phases[0])

    for step in range(1, 65):
        await cycles(dut, STEP_CYCLES - 1)
        check_outputs(dut, BUSY | phases[(step - 1) % 4])
        await cycles(dut, 1)
        check_outputs(dut, BUSY | phases[step % 4])

    await cycles(dut, STEP_CYCLES - 1)
    check_outputs(dut, BUSY | phases[0])
    await cycles(dut, 1)
    check_outputs(dut, DONE)
    await cycles(dut, 5)
    check_outputs(dut, DONE)
    dut.ui_in.value = command
    await cycles(dut, 3)
    check_outputs(dut, 0)


@cocotb.test()
async def disable_reset_and_rearm(dut):
    command = 0x1E  # Continuous, 4 ms, counterclockwise half-step.
    await reset(dut, command | RUN)
    check_outputs(dut, 0)
    dut.ui_in.value = command
    await cycles(dut, 6)
    dut.ui_in.value = command | RUN
    await cycles(dut, 3)
    check_outputs(dut, BUSY | 0x01)
    await cycles(dut, STEP_CYCLES)
    check_outputs(dut, BUSY | 0x09)

    dut.ena.value = 0
    await Timer(200, unit="ns")  # Check assertion before the next rising clock edge.
    check_outputs(dut, 0)
    await cycles(dut, 4)
    dut.ena.value = 1
    await cycles(dut, 12)
    check_outputs(dut, 0)

    dut.ui_in.value = command
    await cycles(dut, 6)
    dut.ui_in.value = command | RUN
    await cycles(dut, 3)
    check_outputs(dut, BUSY | 0x01)
    dut.rst_n.value = 0
    await Timer(200, unit="ns")
    check_outputs(dut, 0)
