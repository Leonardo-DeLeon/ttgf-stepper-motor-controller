# GF180 Stage 4A · Independent clock-tree root experiment

`b01-cts-root8` compares against the physical configuration of V1.0 at commit `96ff2025df99f416a7e48a39f40d3ddc8ced6c3b`, Actions run `45544273443`. It uses the same RTL, tests, 1000 ns period, GF180D, Liberty and official 1×1 tile. The physical change sets `CTS_ROOT_BUFFER=gf180mcu_fd_sc_mcu7t5v0__clkbuf_8`. Post-GRT design repair uses the V1.0 setting of false. Strict setup, hold, slew and capacitance checks cover nine corners with 3 ns, 0.200 pF and fanout 10.

Start **Actions → gds → Run workflow** on `experiment/stage4a-electrical`. Use the commit and run ID in `stage4a-provenance` to match the outputs. The verifier requires the routed netlist to show the selected root master and 61 clocked flip-flops. It lists the actual branch and dummy masters. The archived STA and SPEF reports are needed to quantify effective loading, clock slew, latency and skew. The branch footprint and number of loads determine the output capacitance the root sees. A smaller root alone can increase transition time.

V1.0 had one `clkbuf_16` root, eight `clkbuf_16` branches, seven dummy loads and 204.701 fF at the worst corner. B01 results are pending. A01 remains an independent result in commit `8e0d233bd01e48f4ff688ef5451f53dc9cab3958`. Failed runs preserve available evidence in `stage4a-failed-flow`.
