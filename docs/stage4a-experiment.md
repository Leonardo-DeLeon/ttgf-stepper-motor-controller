# GF180 Stage 4A · Post-global-routing design repair

`a01-postgrt` uses the RTL and tests from V1.0, commit `96ff2025df99f416a7e48a39f40d3ddc8ced6c3b`, with the official TTGF26c 1×1 floorplan, GF180D and LibreLane 3.0.14. The clock period is 1000 ns. The reference for measurement is Actions run `45544273443`.

`RUN_POST_GRT_DESIGN_REPAIR=true` enables OpenROAD design repair using global-routing estimates before final extraction. Setup, hold, slew and capacitance checks apply to all nine STA corners. The limits remain 3 ns, 0.200 pF and fanout 10.

The GDS workflow is manual. Select the `experiment/stage4a-electrical` ref in **Actions → gds → Run workflow**. Match the run's commit and ID with `stage4a-provenance`. On failure, `stage4a-failed-flow` preserves available inputs and reports. Precheck and Gate Level run after a successful GDS job.

Compare the physical result with V1.0 across SPEF corners and inspect the repair and CTS reports. V1.0 measured 4.178255 ns slew, 204.701 fF in the clock root, +790.325111 ns minimum setup, +0.160541 ns minimum hold, and 10 523.7888 µm² of routed functional and auxiliary cells. Variant results are pending. The experiment targets data-net slew. Clock-root capacitance is measured independently.

[Reference workflow run](https://github.com/Leonardo-DeLeon/ttgf-stepper-motor-controller/actions/runs/45544273443)
