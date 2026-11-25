#!/usr/bin/env bash
set -euo pipefail
export PYTHONDONTWRITEBYTECODE=1

package=experiments/stage5
work=$(realpath "$1")
payload=$work/payload
results=$(realpath "$2")
ciel_root=$RUNNER_TEMP/e01-resume-ciel
mkdir -p "$ciel_root"
get_plan() {
    python3 -c 'import json, sys; print(json.load(open(sys.argv[1]))[sys.argv[2]][sys.argv[3]])' \
        "$package/continuation.json" "$1" "$2"
}
state=$(get_plan checkpoint state_path)
tag=$(get_plan continuation run_tag)
start=$(get_plan continuation start_step)
image=$(python3 "$package/prepare.py" field image)
pdk=$(python3 "$package/prepare.py" field pdk)
scl=$(python3 "$package/prepare.py" field scl)

[[ "$(uname -m)" = x86_64 ]]
docker pull --platform linux/amd64 "$image"
docker image inspect "$image" > "$results/container-inspect.json"
python3 "$package/prepare.py" image "$results/container-inspect.json"

runtime=(docker run --rm --platform linux/amd64 --user "$(id -u):$(id -g)"
    --env HOME=/e01-pdk --workdir /e01/design
    --mount "type=bind,src=$payload,dst=/e01"
    --mount "type=bind,src=$ciel_root,dst=/e01-pdk"
    --mount "type=bind,src=$PWD/$package,dst=/e01-src,readonly"
    --mount "type=bind,src=$results,dst=/e01-review"
    --entrypoint bash "$image")
"${runtime[@]}" -euc '
    printf "librelane: "; librelane --version
    printf "openroad: "; openroad -version
    printf "opensta: "; sta -version
    printf "architecture: "; uname -m
    printf "open_pdks: "; python3 -c "from librelane.common import get_pdk_hash; print(get_pdk_hash(\"gf180mcuD\"))"
' 2>&1 | tee "$results/tool-versions.txt"
python3 "$package/prepare.py" versions "$results/tool-versions.txt"
for tool in librelane openroad opensta architecture open_pdks; do
    cmp <(grep "^$tool: " "$results/tool-versions.txt") <(grep "^$tool: " "$payload/tool-versions.txt")
done

# Check serialized guides in a fresh OpenROAD process before consuming the state.
cat > "$results/check-guides.tcl" <<'TCL'
read_db $::env(E01_CHECKPOINT_ODB)
write_guides /e01-review/odb-reopened.guide
TCL
odb="$(dirname "$state")/tt_um_ieee_bootcamp_stepper.odb"
"${runtime[@]}" -euc '
    E01_CHECKPOINT_ODB=$1 openroad -exit -no_init -no_splash /e01-review/check-guides.tcl
' e01 "/e01/$odb" 2>&1 | tee "$results/check-guides.log"
for guide in "$payload/$(dirname "$state")/after_grt.guide" "$results/odb-reopened.guide"; do
    awk '$0 == "(" {print previous} {previous=$0}' "$guide" |
        LC_ALL=C sort -u > "$results/$(basename "$guide").nets"
done
test -s "$results/after_grt.guide.nets"
cmp "$results/after_grt.guide.nets" "$results/odb-reopened.guide.nets"

cp "$work/restored-state.json" "$work/checkpoint-replay.json" "$payload/"
python3 - "$package" "$results" <<'PY'
import os
from pathlib import Path
import sys

sys.path.insert(0, sys.argv[1])
from prepare import read_json, write_json

package, results = map(Path, sys.argv[1:])
plan = read_json(package / "continuation.json")
write_json(results / "continuation-provenance.json", {
    "checkpoint": plan["checkpoint"],
    "continuation": plan["continuation"],
    "execution": {key: os.environ[key] for key in ("GITHUB_SHA", "GITHUB_REF",
        "GITHUB_RUN_ID", "GITHUB_RUN_ATTEMPT", "GITHUB_WORKFLOW_REF")},
})
PY

# Ciel provisions the effective version under the same absolute path as E01.
# Use the original compact config and begin at the first step after Post-GRT.
flow_status=0
"${runtime[@]}" -euc '
    exec librelane --pdk "$1" --scl "$2" --pdk-root /e01-pdk --run-tag "$3" \
        --with-initial-state /e01/restored-state.json --from "$4" config.json
' e01 "$pdk" "$scl" "$tag" "$start" 2>&1 | tee "$results/librelane.log" || flow_status=$?
printf '%s\n' "$flow_status" > "$results/flow-status.txt"
python3 "$package/resume.py" bridge "$payload" "$work/restored-state.json" | tee "$results/bridge.log"

if python3 "$package/resume.py" sta-inputs "$payload" > "$results/directed-sta-inputs.tsv"; then
    cp "$payload/directed-signature.json" "$results/"
    "${runtime[@]}" -euc '
        mkdir /e01-review/directed-sta
        while IFS=$'"'"'\t'"'"' read -r E01_CORNER E01_LIB E01_NL E01_SDC E01_SPEF E01_NET E01_DRIVER E01_LOADS E01_INPUT_NETS E01_INPUT_PINS; do
            export E01_CORNER E01_LIB E01_NL E01_SDC E01_SPEF E01_NET E01_DRIVER E01_LOADS E01_INPUT_NETS E01_INPUT_PINS
            sta -no_init -exit /e01-src/directed_sta.tcl > "/e01-review/directed-sta/$E01_CORNER.rpt" 2>&1
            grep -Fx "E01 extracted corner $E01_CORNER" "/e01-review/directed-sta/$E01_CORNER.rpt"
            grep -Fx "E01 directed STA complete" "/e01-review/directed-sta/$E01_CORNER.rpt"
        done < /e01-review/directed-sta-inputs.tsv
    ' 2>&1 | tee "$results/directed-sta.log"
else
    if [[ "$flow_status" = 0 ]]; then exit 1; fi
fi

run=$payload/design/runs/$tag
shopt -s nullglob
odb=("$run"/final/odb/*.odb)
if [[ ${#odb[@]} = 1 ]]; then
    "${runtime[@]}" -euc '
        export E01_ODB="$1" E01_REPORT=/e01/final-native
        mkdir "$E01_REPORT"
        exec openroad -exit -no_init -no_splash /e01-src/inspect.tcl
    ' e01 "/e01/${odb[0]#"$payload/"}" 2>&1 | tee "$payload/final-native.log"
fi
exit "$flow_status"
