# Adapted from the Tiny Tapeout GF180 Verilog template.
SIM ?= icarus
FST ?= -fst
TOPLEVEL_LANG = verilog
SRC_DIR := $(abspath ../src)
PROJECT_SOURCES = tt_um_ieee_bootcamp_stepper.v clock_enable.v step_counter.v \
                  stepper_fsm.v stepper_controller.v

ifeq ($(GATES),yes)
SIM_BUILD = sim_build/gl
COMPILE_ARGS += -DGL_TEST -DFUNCTIONAL -DUSE_POWER_PINS -DSIM -DUNIT_DELAY=\#1
GF180_CELLS := $(PDK_ROOT)/gf180mcuD/libs.ref/gf180mcu_fd_sc_mcu7t5v0/verilog
VERILOG_SOURCES += $(GF180_CELLS)/primitives.v
VERILOG_SOURCES += $(GF180_CELLS)/gf180mcu_fd_sc_mcu7t5v0.v
VERILOG_SOURCES += $(CURDIR)/gate_level_netlist.v
else
SIM_BUILD = sim_build/rtl
VERILOG_SOURCES += $(addprefix $(SRC_DIR)/,$(PROJECT_SOURCES))
endif

VERILOG_SOURCES += $(CURDIR)/tb.v
TOPLEVEL = tb
COCOTB_TEST_MODULES = test_stepper

include $(shell cocotb-config --makefiles)/Makefile.sim
