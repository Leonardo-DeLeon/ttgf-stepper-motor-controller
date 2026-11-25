#!/usr/bin/env bash
set -euo pipefail
export PYTHONDONTWRITEBYTECODE=1

package=$(realpath "$(dirname "$0")")
action=$1
root=$(realpath "$2")
payload=$root/work
out=$root/signoff
top=tt_um_ieee_bootcamp_stepper

field() {
    python3 "$package/recover_signoff.py" field "$1"
}

case "$action" in
    recover)
        : "${GH_TOKEN:?Actions token required}"
        repo=$(git rev-parse --show-toplevel)
        python3 "$package/recover_signoff.py" checkout "$repo"
        mkdir -p "$root"/actions "$root"/downloads
        for item in checkpoint continuation rtl; do
            if [[ "$item" == checkpoint ]]; then
                run=45665082920
                artifact=15047420988
            else
                run=$(field "$item.run_id")
                artifact=$(field "$item.artifact_id")
            fi
            endpoint="repos/$GITHUB_REPOSITORY/actions"
            gh api "$endpoint/runs/$run/attempts/1" > "$root/actions/$item-attempt.json"
            gh api "$endpoint/runs/$run/artifacts" > "$root/actions/$item-artifacts.json"
            gh api "$endpoint/runs/$run/attempts/1/jobs" > "$root/actions/$item-jobs.json"
            gh api "$endpoint/artifacts/$artifact/zip" > "$root/downloads/$item.zip"
        done
        python3 "$package/resume.py" recover "$root/downloads/checkpoint.zip" "$root/checkpoint" "$repo" \
            "$root/actions/checkpoint-attempt.json" "$root/actions/checkpoint-artifacts.json"
        python3 "$package/recover_signoff.py" extract "$root/downloads/continuation.zip" "$root/outer" \
            "$root/continuation" "$root/actions/continuation-attempt.json" "$root/actions/continuation-artifacts.json" \
            "$root/actions/continuation-jobs.json"
        python3 "$package/recover_signoff.py" rtl "$root/downloads/rtl.zip" "$root/rtl" \
            "$root/actions/rtl-artifacts.json" "$root/actions/rtl-jobs.json"
        python3 "$package/recover_signoff.py" reconstruct "$root/checkpoint/payload" "$root/continuation" "$payload"
        mkdir "$out"
        cp -r "$root/rtl" "$out/rtl"
        cp "$root/downloads/rtl.zip" "$out/rtl.zip"
        cp "$root/actions/"*.json "$out/"
        python3 "$package/prepare.py" seal "$payload"
        python3 "$package/signoff.py" "$payload" e01-postgrt-reviewed "$root/physical-report.json" --physical-only
        ;;
    drc)
        gds=$payload/design/runs/e01-postgrt-reviewed/final/gds/$top.gds
        expected=$(field "continuation.artifact_sha256")
        printf '%s  %s\n' "$expected" "$root/downloads/continuation.zip" | sha256sum -c -
        python3 "$package/signoff.py" "$payload" e01-postgrt-reviewed "$root/physical-report-drc.json" \
            --physical-only
        bash "$package/klayout_signoff.sh" "$gds" "$3" "$out/drc"
        ;;
    oasis)
        gds=$payload/design/runs/e01-postgrt-reviewed/final/gds/$top.gds
        python3 "$package/signoff.py" "$payload" e01-postgrt-reviewed "$root/physical-report-oasis.json" \
            --physical-only
        python3 - "$gds" "$out/$top.oas" <<'PY'
from importlib.metadata import version
from pathlib import Path
import sys

import klayout.db as kdb

if version("klayout") != "0.30.14" or Path(sys.argv[2]).exists():
    raise SystemExit("Expected KLayout 0.30.14 and a fresh OASIS path")
layout = kdb.Layout()
layout.read(sys.argv[1])
layout.write(sys.argv[2])
PY
        ;;
    gl)
        pdk_root=$(realpath "$3")
        pdk=$(realpath "$pdk_root/gf180mcuD")
        revision=$(python3 "$package/prepare.py" field pdk_revision)
        [[ "$pdk" == */versions/"$revision"/gf180mcuD ]]
        library=$(python3 "$package/prepare.py" field scl)
        models=$pdk/libs.ref/$library/verilog
        pnl=$payload/design/runs/e01-postgrt-reviewed/final/pnl/$top.pnl.v
        python3 "$package/signoff.py" "$payload" e01-postgrt-reviewed "$root/physical-report-gl.json" \
            --physical-only
        mkdir "$out/gl"
        iverilog -V > "$out/gl/iverilog-version.txt" 2>&1
        grep -E '^Icarus Verilog version 13\.0 ' "$out/gl/iverilog-version.txt"
        cocotb-config --version | tee "$out/gl/cocotb-version.txt"
        grep -Fx 2.1.0 "$out/gl/cocotb-version.txt"
        sha256sum "$models/primitives.v" "$models/$library.v" "$pnl" > "$out/gl/inputs.sha256"
        cp -r test "$root/gl-test"
        cp "$pnl" "$root/gl-test/gate_level_netlist.v"
        export COCOTB_RANDOM_SEED=1
        status=0
        date -u +%FT%T.%NZ > "$out/gl/execution-window.txt"
        make -C "$root/gl-test" SIM=icarus GATES=yes PDK_ROOT="$pdk_root" \
            COCOTB_RESULTS_FILE="$out/gl/results.xml" 2>&1 | tee "$out/gl/run.log" || status=$?
        date -u +%FT%T.%NZ >> "$out/gl/execution-window.txt"
        printf '%s\n' "$status" > "$out/gl/status.txt"
        if [[ -f "$out/gl/results.xml" ]]; then sha256sum "$out/gl/results.xml" > "$out/gl/results.sha256"; fi
        if [[ -f "$root/gl-test/tb.fst" ]]; then cp "$root/gl-test/tb.fst" "$out/gl/"; fi
        exit "$status"
        ;;
    finalize)
        test -d "$out"
        python3 "$package/signoff.py" "$payload" e01-postgrt-reviewed "$root/physical-report-final.json" \
            --physical-only
        python3 - "$package" "$out" <<'PYTHON'
import os
from pathlib import Path
import sys

sys.path.insert(0, sys.argv[1])
from prepare import read_json, write_json
from recover_signoff import IDENTITY

folder = Path(sys.argv[2])
manifest = read_json(IDENTITY)
write_json(folder / "provenance.json", {
    "continuation": manifest["continuation"],
    "rtl": manifest["rtl"],
    "execution": {key: os.environ[key] for key in ("GITHUB_SHA", "GITHUB_REF",
        "GITHUB_RUN_ID", "GITHUB_RUN_ATTEMPT", "GITHUB_WORKFLOW_REF")},
})
PYTHON
        cp -r "$out" "$payload/signoff"
        python3 "$package/prepare.py" seal "$payload"
        cp "$payload/SHA256SUMS" "$root/reconstructed.SHA256SUMS"
        python3 "$package/signoff.py" "$payload" e01-postgrt-reviewed "$root/final-report.json"
        ;;
    *) exit 2 ;;
esac
