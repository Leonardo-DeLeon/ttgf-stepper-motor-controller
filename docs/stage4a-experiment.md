# Stage 4A · C01 combined physical experiment

C01 combines the A01 Post-GRT design repair with the B01 `clkbuf_8` CTS root. Its parent is B01 `e79558f6d899503e067e4ce8cb5de719aa1fe46d`. The reference is V1.0 `96ff2025df99f416a7e48a39f40d3ddc8ced6c3b`. The GF180D implementation retains the official 1×1 tile, the 1 MHz clock, the 10% GRT slew/cap margins, and the nine STA corners. The input manifest records the RTL, tests, flow revisions, and reference signoff constraints.

Run the RTL test workflow on the C01 commit. Dispatch **gds** manually on `experiment/stage4a-electrical`. The C01 run records its commit, run attempt, tool revisions, routed clock tree, DEF/GDSII cell area, and physical and timing checks. The GDS job checks 3 ns slew, 200 fF capacitance, 10 fanout, zero setup/hold violations, and the 11,200 µm² Stage 4A functional/auxiliary budget. The 9,600 µm² goal and the area including taps and endcaps are reported separately.

If GDS passes, review the official Precheck and Gate Level artifacts for 12/12 and at least 2/2 passing checks, respectively. Record the test run ID, GDS run ID and attempt, and the tested commit. If a check fails, retain `stage4a-provenance`, the job logs, and `GDS_logs` or `stage4a-failed-flow`. Keep artifacts in the local experiment archive.

Compare the nine routed STA corners with the archived A01 and B01 runs before interpreting the combined result. In A01, five slew observations in max SS were on `_167_`, driven by `_354_/ZN` (`nor2_1`). Follow the driver and its four loads in the routed netlist if instance or net labels change. Check the corresponding pins in the three SS `checks.rpt` reports and correlate with RCX. C01 leaves that gate at its reference sizing. The combined electrical and area results are pending.
