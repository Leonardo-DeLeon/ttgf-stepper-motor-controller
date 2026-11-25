#!/usr/bin/env bash
# Run the compact macro deck outside the Tiny Tapeout harness.
set -euo pipefail
export QT_QPA_PLATFORM=offscreen

[[ $# = 3 ]] || { echo "usage: $0 GDS PINNED_PDK_ROOT REPORT_DIRECTORY" >&2; exit 2; }
gds=$(realpath "$1")
pdk=$(realpath "$2")
out=$(realpath -m "$3")
status=0
top=tt_um_ieee_bootcamp_stepper
revision=$(python3 "$(dirname "$0")/prepare.py" field klayout_deck_revision)
[[ "$pdk" = */versions/"$revision"/gf180mcuD ]]
deck=$pdk/libs.tech/klayout/tech/drc/gf180mcu.drc
[[ -s "$gds" && -s "$deck" ]]
mkdir "$out"
klayout -v | tee "$out/klayout-version.txt"
grep -Eq '(^|[^0-9])0\.30\.14([^0-9]|$)' "$out/klayout-version.txt"
sha256sum "$deck" "$gds" > "$out/drivers.sha256"
(cd "$(dirname "$deck")" && find . -type f -print0 | sort -z | xargs -0 sha256sum) > "$out/deck-files.sha256"
printf '%s\n' "$revision" > "$out/deck-revision.txt"

for item in 'geometry:all,-density,-antenna' 'antenna:antenna' 'density:density'; do
    label=${item%%:*}
    mode=${item#*:}
    printf '%s\t%s\n' "$label" "$mode" >> "$out/deck-modes.tsv"
    if klayout -b -r "$deck" \
        -rd "input=$gds" -rd "topcell=$top" -rd variant=gf180mcuD \
        -rd run_mode=deep -rd threads=1 -rd "decks=$mode" -rd "report=$out/$label.lyrdb" \
        2>&1 | tee "$out/$label.log" && [[ -s "$out/$label.lyrdb" ]]; then
        sha256sum "$out/$label.lyrdb" > "$out/$label.sha256"
        printf '0\n' > "$out/$label.status"
    else
        printf '1\n' > "$out/$label.status"
        status=1
    fi
done
exit "$status"
