#!/usr/bin/env python3
"""Verify Stage 4A inputs and collect signoff metadata from LibreLane 3.0.14."""
import argparse
from hashlib import sha256
import json
import os
from pathlib import Path
import re

ROOT = Path(__file__).resolve().parents[2]
RUN = ROOT / "runs/wokwi"
MANIFEST = Path(__file__).with_name("manifest.json")
COUNT_METRICS = {
    "slew_violations": "design__max_slew_violation__count",
    "cap_violations": "design__max_cap_violation__count",
    "fanout_violations": "design__max_fanout_violation__count",
    "setup_violations": "timing__setup_vio__count",
    "hold_violations": "timing__hold_vio__count",
}


def read(path):
    return json.loads(path.read_text(encoding="utf-8"))


def match(label, actual, expected):
    if actual != expected:
        raise ValueError(f"{label}: expected {expected!r}, got {actual!r}")


def verify_inputs(manifest):
    for relative, digest in manifest["input_sha256"].items():
        actual = sha256((ROOT / relative).read_bytes()).hexdigest()
        match(relative, actual, digest)


def identify(manifest, tools_sha):
    if not re.fullmatch(manifest["tools_sha_prefix"] + r"[0-9a-f]{32}", tools_sha):
        raise ValueError("Unexpected tt-support-tools revision")
    return {
        "variant": manifest["variant"],
        "reference_commit": manifest["reference_commit"],
        "commit": os.environ["GITHUB_SHA"],
        "run_id": os.environ["GITHUB_RUN_ID"],
        "run_attempt": os.environ["GITHUB_RUN_ATTEMPT"],
        "ref": os.environ["GITHUB_REF"],
        "config_sha256": manifest["input_sha256"]["src/config.json"],
        "action": manifest["action"],
        "tools_sha": tools_sha,
        "run_url": f"{os.environ['GITHUB_SERVER_URL']}/{os.environ['GITHUB_REPOSITORY']}/actions/runs/{os.environ['GITHUB_RUN_ID']}",
    }


def single_step(suffix):
    steps = sorted(RUN.glob("*-" + suffix))
    if len(steps) != 1:
        raise ValueError(f"Expected one {suffix} step, found {len(steps)}")
    return steps[0]


def verify_toolchain(manifest, outcome):
    resolved = read(RUN / "resolved.json")
    for key, value in manifest["expected_resolved"].items():
        match(key, resolved[key], value)
    pdk = manifest["pdk"]
    match("LibreLane version", resolved["meta"]["librelane_version"], pdk["FLOW_VERSION"])
    if not resolved["FP_DEF_TEMPLATE"].endswith(manifest["def_template_suffix"]):
        raise ValueError("Unexpected Tiny Tapeout DEF template")
    for corner, paths in resolved["LIB"].items():
        for path in paths:
            if pdk["PDK_VERSION"] not in Path(path).parts:
                raise ValueError(f"Unexpected Liberty PDK version for {corner}")
    if (RUN / "pdk.json").is_file() or outcome == "success":
        match("PDK metadata", read(RUN / "pdk.json"), pdk)
    merged = read(ROOT / "src/config_merged.json")
    for key, value in manifest["config_changes"].items():
        match("merged " + key, merged[key], value)
    if not resolved["RUN_POST_GRT_DESIGN_REPAIR"]:
        return None
    repair = single_step("openroad-repairdesignpostgrt")
    if not (repair / "state_out.json").is_file():
        raise ValueError("Post-global-routing design repair did not complete")
    return repair.name


def worst_violators(report):
    values = {"max slew": [], "max capacitance": []}
    section = ""
    for line in report.splitlines():
        heading = line.strip()
        if heading in values:
            section = heading
        elif heading.startswith(("=", "max fanout")):
            section = ""
        elif section and heading.endswith("(VIOLATED)"):
            values[section].append(float(heading.split()[2]))
    return {
        "worst_violated_slew_ns": max(values["max slew"], default=None),
        "worst_violated_cap_pf": max(values["max capacitance"], default=None),
    }


def collected_metrics(manifest, outcome):
    sta = single_step("openroad-stapostpnr")
    state = read(sta / "state_out.json")
    sdc_path = Path(state["sdc"])
    sdc = (RUN / sdc_path.parent.name / sdc_path.name).read_text(encoding="utf-8")
    expected = manifest["expected_resolved"]
    sdc_checks = {
        r"^create_clock\b[^\n]* -period\s+([\d.]+)": expected["CLOCK_PERIOD"],
        r"^set_max_transition\s+([\d.]+) \[current_design\]": expected["MAX_TRANSITION_CONSTRAINT"],
        r"^set_max_capacitance\s+([\d.]+) \[current_design\]": expected["MAX_CAPACITANCE_CONSTRAINT"],
        r"^set_max_fanout\s+([\d.]+) \[current_design\]": expected["MAX_FANOUT_CONSTRAINT"],
    }
    for pattern, limit in sdc_checks.items():
        match("routed SDC " + pattern, [float(value) for value in re.findall(pattern, sdc, re.M)], [limit])
    metrics = state["metrics"]
    corners = manifest["expected_resolved"]["STA_CORNERS"]
    counts = {}
    for corner in corners:
        row = {label: metrics[f"{key}__corner:{corner}"] for label, key in COUNT_METRICS.items()}
        for slack in ("setup", "hold"):
            row[f"{slack}_ws_ns"] = metrics[f"timing__{slack}__ws__corner:{corner}"]
        report = (sta / corner / "checks.rpt").read_text(encoding="utf-8")
        row.update(worst_violators(report))
        counts[corner] = row
    spef_step = single_step("openroad-rcx")
    for model in ("min", "nom", "max"):
        spef = spef_step / model / Path(state["spef"][f"{model}_*"]).name
        with spef.open(encoding="utf-8") as stream:
            header = "".join(stream.readline() for _ in range(10))
        match(f"OpenROAD extractor {model}", re.search(r'\*VERSION "([^"]+)"', header)[1],
              manifest["openroad_rcx_sha"])
    routing = read(single_step("openroad-detailedrouting") / "or_metrics_out.json")
    area = routing["design__instance__area__stdcell"] - sum(
        routing[f"design__instance__area__class:{group}_cell"] for group in ("tap", "endcap")
    )
    final_metrics_path = RUN / "final/metrics.json"
    if not final_metrics_path.is_file():
        steps = sorted(RUN.glob("[0-9]*-*/state_out.json"))
        physical_metrics = read(steps[-1])["metrics"]
    else:
        physical_metrics = read(final_metrics_path)
    cts = single_step("openroad-cts") / "cts.rpt"
    if outcome == "success":
        identity = read(RUN / "final/commit_id.json")
        recorded = read(ROOT / "stage4a-run.json")
        match("artifact commit", identity["commit"], recorded["commit"])
        match("artifact run URL", identity["workflow_url"], recorded["run_url"])
    return {
        "area_functional_aux_um2_approx": round(area, 4),
        "routed_wirelength_um": routing.get("route__wirelength"),
        "route_drc_errors": routing.get("route__drc_errors"),
        "magic_drc_errors": physical_metrics.get("magic__drc_error__count"),
        "lvs_errors": physical_metrics.get("design__lvs_error__count"),
        "antenna_nets": physical_metrics.get("antenna__violating__nets"),
        "antenna_pins": physical_metrics.get("antenna__violating__pins"),
        "cts_summary": cts.read_text(encoding="utf-8"),
        "corners": counts,
    }


def verify_run(manifest, outcome):
    verify_inputs(manifest)
    result = {"variant": manifest["variant"], "official_build_outcome": outcome}
    result["postgrt_step"] = verify_toolchain(manifest, outcome)
    result.update(collected_metrics(manifest, outcome))
    (ROOT / "stage4a-physical.json").write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    for corner, row in result["corners"].items():
        print(corner, json.dumps(row))
    if any(row[key] for row in result["corners"].values() for key in COUNT_METRICS):
        raise ValueError("Electrical or timing violations in post-route signoff")
    if outcome != "success":
        raise ValueError(f"Official GDS build reported {outcome}")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    subcommands = parser.add_subparsers(dest="command", required=True)
    subcommands.add_parser("inputs")
    metadata = subcommands.add_parser("identify")
    metadata.add_argument("tools_sha")
    run = subcommands.add_parser("run")
    run.add_argument("outcome", choices=("success", "failure", "cancelled", "skipped"))
    args = parser.parse_args()
    manifest = read(MANIFEST)
    try:
        if args.command == "inputs":
            verify_inputs(manifest)
            print(f"{manifest['variant']}: verified {len(manifest['input_sha256'])} input hashes")
        elif args.command == "identify":
            print(json.dumps(identify(manifest, args.tools_sha), indent=2))
        else:
            verify_run(manifest, args.outcome)
    except (KeyError, OSError, ValueError, TypeError) as exc:
        parser.exit(1, f"Stage 4A verification failed: {exc}\n")


if __name__ == "__main__":
    main()
