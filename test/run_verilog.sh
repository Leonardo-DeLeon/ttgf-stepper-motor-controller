#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"
mkdir -p output waveforms
for source in verilog/tb_*.v; do
    bench=${source##*/}
    bench=${bench%.v}
    iverilog -g2001 -Wall -DIVERILOG -s "$bench" -o "output/$bench.vvp" \
        ../src/*.v "$source" > "output/$bench.compile.log" 2>&1
    (cd output && vvp "$bench.vvp") | tee "output/$bench.log"
    rm "output/$bench.vvp"
done
mv waveforms/stepper_demo.vcd output/
python3 summarize_sim.py output
