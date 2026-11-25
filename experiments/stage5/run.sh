#!/usr/bin/env bash
set -euo pipefail

package=experiments/stage5
output=$PWD/e01-output
ciel_root=$RUNNER_TEMP/e01-ciel
python3 "$package/prepare.py" stage "$output"
mkdir -p "$ciel_root"

image=$(python3 "$package/prepare.py" field image)
pdk=$(python3 "$package/prepare.py" field pdk)
scl=$(python3 "$package/prepare.py" field scl)
[[ "$(uname -m)" = x86_64 ]]
docker pull --platform linux/amd64 "$image"
docker image inspect "$image" > "$output/container-inspect.json"
python3 "$package/prepare.py" image "$output/container-inspect.json"

# Stable paths are part of the checkpoint transfer contract.
runtime=(docker run --rm --platform linux/amd64 --user "$(id -u):$(id -g)"
    --env HOME=/e01-pdk --workdir /e01/design
    --mount "type=bind,src=$output,dst=/e01"
    --mount "type=bind,src=$ciel_root,dst=/e01-pdk"
    --mount "type=bind,src=$PWD/$package,dst=/e01-src,readonly"
    --entrypoint bash "$image")
"${runtime[@]}" -euc '
    set -o pipefail
    printf "librelane: "; librelane --version
    printf "openroad: "; openroad -version
    printf "opensta: "; sta -version
    printf "yosys: "; yosys -V
    printf "netgen: "; netgen -batch <<< quit
    printf "magic: "; magic --version
    printf "ciel: "; python3 -c "from importlib.metadata import version; print(version(\"ciel\"))"
    printf "architecture: "; uname -m
    printf "open_pdks: "; python3 -c "from librelane.common import get_pdk_hash; print(get_pdk_hash(\"gf180mcuD\"))"
    librelane --help > /e01/librelane-help.txt
' 2>&1 | tee "$output/tool-versions.txt"
python3 "$package/prepare.py" versions "$output/tool-versions.txt"

flow_status=0
"${runtime[@]}" -euc '
    exec librelane --pdk "$1" --scl "$2" --pdk-root /e01-pdk \
        --run-tag e01-pre-eco --to OpenROAD.RepairDesignPostGRT config.json
' e01 "$pdk" "$scl" 2>&1 | tee "$output/librelane.log" || flow_status=$?

run=$output/design/runs/e01-pre-eco
shopt -s nullglob
steps=("$run"/*-openroad-repairdesignpostgrt)
inspection_status=0
if [[ ${#steps[@]} = 1 && -s "${steps[0]}/state_out.json" ]]; then
    odb=("${steps[0]}"/*.odb)
    if [[ ${#odb[@]} = 1 ]]; then
        "${runtime[@]}" -euc '
            export E01_ODB="$1" E01_REPORT=/e01/native
            mkdir -p "$E01_REPORT"
            exec openroad -exit -no_init -no_splash /e01-src/inspect.tcl
        ' e01 "/e01/${odb[0]#"$output/"}" 2>&1 | tee "$output/native.log" || inspection_status=$?
    fi
fi
verification_status=0
python3 "$package/verify.py" checkpoint "$output" --flow-status "$flow_status"     --inspection-status "$inspection_status" || verification_status=$?
[[ "$flow_status" = 0 && "$inspection_status" = 0 && "$verification_status" = 0 ]]
