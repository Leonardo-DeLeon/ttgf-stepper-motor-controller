#!/usr/bin/env python3
"""Verify Stage 4A inputs and collect signoff metadata from LibreLane 3.0.14."""
import argparse
from collections import Counter, defaultdict
from decimal import Decimal
from fnmatch import fnmatchcase
from hashlib import sha256
import json
import math
import os
from pathlib import Path
import re
import struct
import subprocess

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
DRV_NAMES = {"slew_violations": "max slew", "cap_violations": "max cap",
             "fanout_violations": "max fanout"}
PHYSICAL_ZERO = {
    "route_drc_errors": "route__drc_errors",
    "magic_drc_errors": "magic__drc_error__count",
    "lvs_errors": "design__lvs_error__count",
    "antenna_nets": "antenna__violating__nets",
    "antenna_pins": "antenna__violating__pins",
    "critical_disconnected_pins": "design__critical_disconnected_pin__count",
}
LIBRARY = "gf180mcu_fd_sc_mcu7t5v0__"
ECO = "INSERT_ECO_BUFFERS"


def read(path):
    return json.loads(path.read_text(encoding="utf-8"))


def match(label, actual, expected):
    if actual != expected:
        raise ValueError(f"{label}: expected {expected!r}, got {actual!r}")


def json_digest(value):
    return sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def verify_revision(manifest):
    def git(*args):
        return subprocess.check_output(["git", "-C", str(ROOT), *args], text=True).strip()

    match("checkout commit", git("rev-parse", "HEAD"), os.environ["GITHUB_SHA"])
    match("direct C01 parent", git("show", "-s", "--format=%P", "HEAD"), manifest["parent_commit"])
    match("workflow branch", os.environ["GITHUB_REF"], "refs/heads/experiment/stage4a-electrical")
    match("changed paths since C01",
          sorted(git("diff", "--no-renames", "--name-only", manifest["parent_commit"], "HEAD").splitlines()),
          manifest["publish_paths"])


def verify_inputs(manifest):
    match("RTL file inventory", sorted(p.relative_to(ROOT).as_posix() for p in (ROOT / "src").rglob("*.v")),
          sorted(p for p in manifest["input_sha256"] if p.startswith("src/") and p.endswith(".v")))
    for relative, digest in manifest["input_sha256"].items():
        actual = sha256((ROOT / relative).read_bytes()).hexdigest()
        match(relative, actual, digest)
    return len(manifest["input_sha256"])


def resolved_fingerprint(resolved):
    # Ignore runner-specific absolute prefixes while preserving path suffixes.
    src = Path(resolved["DESIGN_DIR"])
    roots = ((resolved["PDK_ROOT"], "$PDK_ROOT"), (str(src.parent), "$PROJECT"))

    def normalize(value):
        if isinstance(value, dict):
            return {key: normalize(item) for key, item in value.items()}
        if isinstance(value, list):
            return [normalize(item) for item in value]
        if isinstance(value, str):
            for root, token in roots:
                if value == root or value.startswith(root + "/"):
                    return token + value[len(root):]
        return value

    baseline = {key: val for key, val in resolved.items() if key != ECO}
    baseline["meta"] = {**resolved["meta"], "flow": None, "substituting_steps": None}
    return json_digest(normalize(baseline))


def identify(manifest, tools_sha):
    match("tt-support-tools revision", tools_sha, manifest["tools_sha"])
    return {
        "variant": manifest["variant"],
        "reference_commit": manifest["reference_commit"],
        "parent_commit": manifest["parent_commit"],
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


def verify_toolchain(manifest, outcome, tools_sha):
    match("checked-out tt-support-tools revision", tools_sha, manifest["tools_sha"])
    resolved = read(RUN / "resolved.json")
    for key, value in manifest["expected_resolved"].items():
        match(key, resolved[key], value)
    match("resolved flow", resolved["meta"]["flow"], manifest["eco_meta"]["flow"])
    match("resolved Classic step insertion", resolved["meta"]["substituting_steps"],
          manifest["eco_meta"]["substituting_steps"])
    match("resolved settings other than the ECO step", resolved_fingerprint(resolved),
          manifest["c01"]["resolved_fingerprint"])
    pdk = manifest["pdk"]
    match("LibreLane version", resolved["meta"]["librelane_version"], pdk["FLOW_VERSION"])
    if not resolved["FP_DEF_TEMPLATE"].endswith(manifest["def_template_suffix"]):
        raise ValueError("Unexpected Tiny Tapeout DEF template")
    for corner in manifest["expected_resolved"]["STA_CORNERS"]:
        match(f"Liberty match for {corner}", sum(fnmatchcase(corner, pattern) for pattern in resolved["LIB"]), 1)
    for corner, paths in resolved["LIB"].items():
        if not paths:
            raise ValueError(f"No Liberty for {corner}")
        for path in paths:
            if pdk["PDK_VERSION"] not in Path(path).parts:
                raise ValueError(f"Unexpected Liberty PDK version for {corner}")
    if (RUN / "pdk.json").is_file() or outcome == "success":
        match("PDK metadata", read(RUN / "pdk.json"), pdk)
    merged = read(ROOT / "src/config_merged.json")
    for key, value in manifest["config_changes"].items():
        match("merged " + key, merged[key], value)
    rtl = sorted(str(ROOT / name) for name in manifest["input_sha256"]
                 if name.startswith("src/") and name.endswith(".v"))
    match("effective RTL paths", sorted(resolved["VERILOG_FILES"]), rtl)
    return {"librelane": pdk["FLOW_VERSION"], "pdk": pdk, "tools_sha": tools_sha}


def verify_repair(manifest):
    repair = single_step("openroad-repairdesignpostgrt")
    global_route = single_step("openroad-globalrouting")
    detailed_route = single_step("openroad-detailedrouting")
    state = read(repair / "state_out.json")
    for view in ("odb", "def", "nl"):
        path = routed_view(state[view])
        if path.parent != repair or not path.is_file():
            raise ValueError(f"Post-GRT output {view} was not produced by {repair.name}")
    log = (repair / "openroad-repairdesignpostgrt.log").read_text(encoding="utf-8")
    command = re.findall(r"^\+ repair_design\b[^\n]*", log, re.M)
    match("executed Post-GRT repair_design commands", len(command), 1)
    expected = manifest["expected_resolved"]
    for option in ("slew", "cap"):
        margin = re.search(rf" -{option}_margin (\d+(?:\.\d+)?)\b", command[0])
        match(f"executed Post-GRT {option} margin", float(margin[1]) if margin else None,
              expected[f"GRT_DESIGN_REPAIR_MAX_{option.upper()}_PCT"])
    if not global_route.name < repair.name < detailed_route.name:
        raise ValueError("Post-GRT repair is outside the global-to-detailed routing sequence")
    sequence = re.findall(r"^\+ (global_route|repair_design|detailed_placement)\b", log, re.M)
    match("Post-GRT repair, legalization and reroute sequence", sequence,
          ["global_route", "repair_design", "detailed_placement", "global_route"])
    previous = read(global_route / "state_out.json")["odb"]
    chain = []
    sta = single_step("openroad-stapostpnr")
    for step in sorted(RUN.glob("[0-9]*-*/state_in.json")):
        if not repair.name <= step.parent.name <= sta.name:
            continue
        match(f"{step.parent.name} input ODB", read(step)["odb"], previous)
        previous = read(step.with_name("state_out.json"))["odb"]
        if not routed_view(previous).is_file():
            raise ValueError(f"Missing ODB from {step.parent.name}")
        chain.append(step.parent.name)
    rcx = single_step("openroad-rcx")
    if not detailed_route.name < rcx.name < sta.name:
        raise ValueError("Expected detailed routing, extraction and final STA in order")
    match("ODB shared by final extraction and STA",
          read(rcx / "state_in.json")["odb"], read(sta / "state_in.json")["odb"])
    return {"step": repair.name, "command": command[0], "odb_chain": chain}


def routed_view(path):
    _, partition, suffix = path.rpartition("/runs/wokwi/")
    if not partition or not suffix or ".." in Path(suffix).parts or Path(suffix).is_absolute():
        raise ValueError(f"Unexpected view path: {path}")
    return RUN / suffix


def def_section(text, name):
    header = re.search(rf"^{name}\s+(\d+)\s*;\s*\n(.*?)^END {name}\b", text, re.M | re.S)
    entries = re.findall(r"^\s*-\s+(\S+)\s+(.*?)\s*;", header[2], re.M | re.S)
    match(f"DEF {name} count", len(entries), int(header[1]))
    result = dict(entries)
    match(f"unique DEF {name}", len(result), len(entries))
    return result


def read_instances(netlist):
    inst_re = rf"^\s*({LIBRARY}\w+)\s+(\S+)\s*\((.*?)\);"
    terminal_re = r"\.(\w+)\s*\(\s*(.*?)\s*\)"
    return {instance: (master, dict(re.findall(terminal_re, body, re.S)))
            for master, instance, body in re.findall(inst_re, netlist.read_text(encoding="utf-8"), re.M | re.S)}


def clock_topology(netlist, def_text, cells, expected_ff_count):
    """Trace actual clocks from the top clock port through the routed netlist."""
    loads = defaultdict(list)
    drivers = defaultdict(list)
    netlist_ffs = set()
    for instance, (master, terminals) in read_instances(netlist).items():
        out = next(((pin, terminals[pin]) for pin in ("Z", "ZN") if terminals.get(pin)), None)
        if master.startswith(LIBRARY + "dff"):
            netlist_ffs.add(instance)
        for pin, net in terminals.items():
            if net:
                table = drivers if pin in {"Z", "ZN", "Q", "QN"} else loads
                table[net].append((instance, master, pin, out))
    match("netlist flip-flops", len(netlist_ffs), expected_ff_count)
    match("DEF flip-flops", {n for n, m in cells.items() if m.startswith(LIBRARY + "dff")}, netlist_ffs)
    nets = def_section(def_text, "NETS")

    def verify_net(net, expected):
        connections = set(re.findall(r"\(\s+(\S+)\s+(\S+)\s+\)", nets[net].split("+", 1)[0]))
        match(f"DEF/netlist clock connections for {net}", sorted(connections), sorted(expected))

    roots = loads["clk"]
    match("number of loads on the input clock", len(roots), 1)
    root, master, pin, out = roots[0]
    match("clock port internal drivers", drivers["clk"], [])
    verify_net("clk", {("PIN", "clk"), (root, pin)})
    if pin != "I" or not out:
        raise ValueError("Clock input is not attached to a driving buffer")
    tree, pending, visited, all_ffs = [], [(out, root, master, 1)], set(), set()
    while pending:
        (output_pin, net), driver, cell, depth = pending.pop(0)
        if net in visited or not loads[net]:
            raise ValueError(f"Unexpected or undriven clock subtree at {net}")
        visited.add(net)
        match(f"only driver for {net}", [(n, p) for n, _, p, _ in drivers[net]], [(driver, output_pin)])
        match(f"DEF master of {driver}", cells[driver], cell)
        verify_net(net, {(driver, output_pin), *((n, p) for n, _, p, _ in loads[net])})
        branches, flip_flops, dummy = [], [], []
        for instance, receiver_master, pin, out in loads[net]:
            match(f"DEF master of {instance}", cells[instance], receiver_master)
            receiver = {"instance": instance, "master": receiver_master, "pin": pin}
            if pin == "CLK" and instance in netlist_ffs:
                flip_flops.append(receiver)
                if instance in all_ffs:
                    raise ValueError(f"Flip-flop reached twice: {instance}")
                all_ffs.add(instance)
            elif pin != "I" or not receiver_master.startswith(tuple(
                LIBRARY + family for family in ("clkbuf_", "clkinv_", "buf_")
            )):
                raise ValueError(f"Unexpected clock load {instance}/{pin}: {receiver_master}")
            elif out:
                branches.append(receiver)
                pending.append((out, instance, receiver_master, depth + 1))
            else:
                dummy.append(receiver)
        tree.append({"net": net, "driver": driver, "master": cell, "depth": depth,
                     "direct_loads": len(loads[net]), "branches": branches,
                     "flip_flops": flip_flops, "dummy_loads": dummy})
    match("FFs reached by CTS", sorted(all_ffs), sorted(netlist_ffs))
    return tree


def checked_instances(state):
    instances = read_instances(routed_view(state["nl"]))
    def_text = routed_view(state["def"]).read_text(encoding="utf-8")
    cells = {name: body.split()[0] for name, body in def_section(def_text, "COMPONENTS").items()}

    def auxiliary_or_logic(name, master):
        return not name.startswith("FILLER_") and master not in {LIBRARY + "filltie", LIBRARY + "endcap"}

    functional = {name: entry for name, entry in instances.items() if auxiliary_or_logic(name, entry[0])}
    match("DEF/netlist functional and auxiliary masters",
          {name: master for name, master in cells.items() if auxiliary_or_logic(name, master)},
          {name: master for name, (master, _) in functional.items()})
    return functional, def_text


def compare_c01(state, manifest):
    instances, def_text = checked_instances(state)
    reference = manifest["c01"]
    baseline = reference["logical_masters"]
    actual = {name: master[len(LIBRARY):] for name, (master, _) in instances.items()}
    resized = {name: [baseline[name], actual[name]]
               for name in sorted(baseline.keys() & actual.keys()) if baseline[name] != actual[name]}
    nets = def_section(def_text, "NETS")
    target_pins = {}
    for port in reference["focus_pins"]:
        name, pin = port.split("/")
        master, terminals = instances[name]
        net = terminals[pin]
        pins = re.findall(r"\(\s+(\S+)\s+(\S+)\s+\)", nets[net].split("+", 1)[0])
        if (name, pin) not in pins:
            raise ValueError(f"DEF/netlist connection disagrees for {port}")
        target_pins[port] = {"master": master, "net": net, "other_terminals": len(pins) - 1}
    return {"resized_vs_c01": resized, "added_vs_c01": sorted(actual.keys() - baseline.keys()),
            "removed_vs_c01": sorted(baseline.keys() - actual.keys()),
            "netlist_changed_vs_c01": json_digest(instances) != reference["postgrt_netlist_fingerprint"],
            "target_pins": target_pins}


def compare_repair(manifest):
    repair = single_step("openroad-repairdesignpostgrt")
    before, _ = checked_instances(read(repair / "state_in.json"))
    match("pre-repair C01 netlist", json_digest(before), manifest["c01"]["pregrt_netlist_fingerprint"])
    post, _ = checked_instances(read(repair / "state_out.json"))
    match("pre-ECO Post-GRT C01 netlist", json_digest(post), manifest["c01"]["postgrt_netlist_fingerprint"])
    return compare_c01(read(repair / "state_out.json"), manifest)


def eco_def(state, manifest, inserted):
    def_text = routed_view(state["def"]).read_text(encoding="utf-8")
    components = def_section(def_text, "COMPONENTS")
    nets = def_section(def_text, "NETS")
    spec = manifest["eco_signature"]
    driver = tuple(spec["driver"].split("/"))
    loads = {tuple(pin.split("/")) for pin in spec["receivers"]}
    name, split_net = spec["instance"], spec["split_net"]

    def connections(net):
        return set(re.findall(r"\(\s+(\S+)\s+(\S+)\s+\)", nets[net].split("+", 1)[0]))

    match("target DEF master", components[driver[0]].split()[0], spec["driver_master"])
    for pin, net in spec["driver_inputs"].items():
        if (driver[0], pin) not in connections(net):
            raise ValueError(f"ECO target input mismatch at {driver[0]}/{pin}")
    inputs = {pin: connections(net) for pin, net in spec["driver_inputs"].items()}
    targets = [cell for cell, body in components.items() if body.split()[0] == spec["driver_master"]
               and all((cell, pin) in members for pin, members in inputs.items())]
    driver_net = split_net if inserted else spec["net"]
    match("unique driver candidate for the output connection",
          [cell for cell in targets if (cell, driver[1]) in connections(driver_net)],
          [driver[0]])
    if not inserted:
        match("original DEF target and receivers", connections(spec["net"]), {driver, *loads})
        match("ECO name available", name in components or split_net in nets, False)
        return
    buffer_master = manifest["expected_resolved"][ECO][0]["buffer"]
    entry = components[name]
    match("ECO DEF buffer master", entry.split()[0], buffer_master)
    if not re.search(r"\+ PLACED \( -?\d+ -?\d+ \) \w+", entry):
        raise ValueError("Missing legal-placement output for the ECO instance")
    match("ECO NOR-to-buffer DEF connections", connections(split_net), {driver, (name, "I")})
    match("ECO buffer-to-original-sinks DEF connections", connections(spec["net"]), {(name, "Z"), *loads})
    return {"instance": name, "master": buffer_master, "driver_net": split_net, "load_net": spec["net"]}


def verify_eco(manifest):
    repair = single_step("openroad-repairdesignpostgrt")
    eco = single_step("odb-insertecobuffers")
    detailed = single_step("openroad-detailedrouting")
    match("ECO immediately after Post-GRT", int(eco.name.split("-")[0]), int(repair.name.split("-")[0]) + 1)
    if not eco.name < detailed.name:
        raise ValueError("ECO must precede detailed routing")
    input_state = read(eco / "state_in.json")
    match("ECO input from Post-GRT", input_state["odb"], read(repair / "state_out.json")["odb"])
    eco_def(input_state, manifest, inserted=False)
    config = read(eco / "config.json")
    match("ECO step target and master in subprocess config", config[ECO], manifest["expected_resolved"][ECO])
    command = (eco / "COMMANDS").read_text(encoding="utf-8")
    for token in ("eco_buffer.py", "--step-config", "--output-def", "--output-odb", input_state["odb"]):
        if token not in command:
            raise ValueError(f"ECO subprocess invocation missing {token}")
    log = (eco / "odb-insertecobuffers.log").read_text(encoding="utf-8")
    if not any(int(count) > 0 for count in re.findall(r"\brerouting (\d+) nets\.", log, re.I)):
        raise ValueError("No incremental ECO reroute in the execution log")
    state = read(eco / "state_out.json")
    for view in ("odb", "def"):
        path = routed_view(state[view])
        if path.parent != eco or not path.is_file():
            raise ValueError(f"ECO did not emit its own {view}")
    result = eco_def(state, manifest, inserted=True)

    def masters(phase):
        text = routed_view(phase["def"]).read_text(encoding="utf-8")
        return {name: body.split()[0] for name, body in def_section(text, "COMPONENTS").items()}

    match("single physical addition in the ECO step", masters(state),
          {**masters(input_state), result["instance"]: result["master"]})
    return {"step": eco.name, **result}


def verify_routed_eco(state, manifest):
    result = eco_def(state, manifest, inserted=True)
    instances = read_instances(routed_view(state["nl"]))
    powered = read_instances(routed_view(state["pnl"]))
    spec = manifest["eco_signature"]
    name = result["instance"]
    master = result["master"]
    signal_pins = {"I": spec["split_net"], "Z": spec["net"]}
    match("routed ECO logical netlist cell", instances[name], (master, signal_pins))
    match("routed ECO powered netlist cell", powered[name],
          (master, {**signal_pins, "VDD": "VPWR", "VSS": "VGND"}))
    return result


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


def gds_areas(path, top):
    """Read GF180 (0, 0) PR boundaries from the signoff stream, as in the local audit."""
    data = path.read_bytes()
    offset = 0
    cell, kind, layer, purpose, scale = None, None, None, None, None
    points = defaultdict(list)
    references = Counter()
    while offset < len(data):
        length, record, _ = struct.unpack_from(">HBB", data, offset)
        if length < 4 or offset + length > len(data):
            raise ValueError(f"Invalid GDSII record in {path}")
        payload = data[offset + 4:offset + length]
        offset += length
        if record == 3:
            encoded = payload[8:16]
            scale = int.from_bytes(encoded[1:], "big") / 2**56 * 16**(encoded[0] - 64) * 1e6
        elif record == 6:
            cell = payload.rstrip(b"\0").decode("ascii")
        elif record in (8, 10, 12, 9, 11, 21, 45):
            kind, layer, purpose = record, None, None
        elif record == 13:
            layer = int.from_bytes(payload, "big")
        elif record == 14:
            purpose = int.from_bytes(payload, "big")
        elif record == 16 and (kind, layer, purpose) == (8, 0, 0):
            points[cell].extend(struct.iter_unpack(">ii", payload))
        elif record == 18 and cell == top:
            if kind != 10:
                raise ValueError("Expected individual SREFs in the Magic stream")
            references[payload.rstrip(b"\0").decode("ascii")] += 1
    areas = {}
    for name, coordinates in points.items():
        x, y = zip(*coordinates)
        width, height = (Decimal(str(round((max(axis) - min(axis)) * scale, 6))) for axis in (x, y))
        areas[name] = width * height
    return areas, references


def routed_geometry(def_text, gds, manifest):
    units = int(re.search(r"^UNITS DISTANCE MICRONS (\d+) ;", def_text, re.M)[1])
    dbu = re.search(r"^DIEAREA \( (-?\d+) (-?\d+) \) \( (-?\d+) (-?\d+) \) ;", def_text, re.M)
    match("routed DEF die area", [int(value) / units for value in dbu.groups()],
          manifest["expected_resolved"]["DIE_AREA"])
    pins = def_section(def_text, "PINS")
    canonical_pins = json.dumps(sorted((name, " ".join(body.split())) for name, body in pins.items()))
    match("routed DEF pinout SHA-256", sha256(canonical_pins.encode()).hexdigest(),
          manifest["def_pins_sha256"])
    cells = {name: entry.split()[0] for name, entry in def_section(def_text, "COMPONENTS").items()}
    areas, references = gds_areas(gds, manifest["expected_resolved"]["DESIGN_NAME"])
    quantities = Counter(cells.values())
    match("DEF/GDSII standard cell inventory", quantities, references)
    per_master = {master: areas[master] * count for master, count in quantities.items()}
    if not per_master or min(per_master.values()) <= 0:
        raise ValueError("Missing positive master areas in routed DEF/GDSII")
    fill = sum(areas[master] for name, master in cells.items() if name.startswith("FILLER_"))
    taps = sum(per_master.get(LIBRARY + group, 0) for group in ("filltie", "endcap"))
    total = sum(per_master.values())
    return cells, {"area_functional_aux_um2": float(total - fill - taps),
                   "area_with_tap_endcap_um2": float(total - fill), "area_fill_um2": float(fill),
                   "area_total_um2": float(total), "def_instances": len(cells),
                   "area_def": "post-fill DEF used by STA",
                   "area_gds": str(gds.relative_to(RUN))}


def signoff_corners(sta, state, manifest):
    expected = manifest["expected_resolved"]["STA_CORNERS"]
    match("final STA corner count", len(expected), 9)
    match("final STA corners", sorted(path.parent.name for path in sta.glob("*/checks.rpt")), sorted(expected))
    metrics = state["metrics"]
    rows = {}
    for corner in expected:
        folder = sta / corner
        row = {label: metrics[f"{key}__corner:{corner}"] for label, key in COUNT_METRICS.items()}
        checks = (folder / "checks.rpt").read_text(encoding="utf-8")
        match("checks.rpt corner", set(re.findall(r"^=+ (\w+) Corner ", checks, re.M)), {corner})
        for label, report_name in DRV_NAMES.items():
            count = re.findall(rf"^{report_name} violation count (\d+)\s*$", checks, re.M)
            match(f"{corner} {report_name} checks.rpt count", [int(value) for value in count], [row[label]])
        for label, delay in (("setup", "max"), ("hold", "min")):
            slack = metrics[f"timing__{label}__ws__corner:{corner}"]
            report = (folder / f"ws.{delay}.rpt").read_text(encoding="utf-8")
            value = float(re.search(rf"^{corner}: (\S+)\s*$", report, re.M)[1])
            if not math.isfinite(slack):
                raise ValueError(f"Invalid {label} slack at {corner}")
            match(f"{corner} {label} worst slack", slack, value)
            row[f"{label}_ws_ns"] = slack
        row["unannotated_filtered_nets"] = metrics[f"timing__unannotated_net_filtered__count__corner:{corner}"]
        annotation = read(folder / "filter_unannotated_metrics.json")
        match(f"{corner} filtered annotation count", row["unannotated_filtered_nets"],
              annotation[f"timing__unannotated_net_filtered__count__corner:{corner}"])
        row.update(worst_violators(checks))
        rows[corner] = row
    return rows


def verify_sdc(sta, manifest):
    state = read(sta / "state_in.json")
    path = routed_view(state["sdc"])
    match("effective post-route SDC SHA-256", sha256(path.read_bytes()).hexdigest(), manifest["sdc_sha256"])
    return str(path.relative_to(RUN))


def verify_extraction(sta, manifest):
    state = read(sta / "state_in.json")
    for model in ("min", "nom", "max"):
        spef = single_step("openroad-rcx") / model / Path(state["spef"][f"{model}_*"]).name
        with spef.open(encoding="utf-8") as stream:
            header = "".join(stream.readline() for _ in range(10))
        match(f"OpenROAD extractor {model}", re.search(r'^\*VERSION "([^"]+)"', header, re.M)[1],
              manifest["openroad_rcx_sha"])
        if not re.search(r"^\*D_NET ", spef.read_text(encoding="utf-8"), re.M):
            raise ValueError(f"No extracted networks for {model}")
    return manifest["openroad_rcx_sha"]


def target_evidence(sta, target_pins, corners):
    nets = {target["net"] for target in target_pins.values()}
    state = read(sta / "state_in.json")
    extracted = {}
    for model in ("min", "nom", "max"):
        spef = routed_view(state["spef"][f"{model}_*"]).read_text(encoding="utf-8")
        names = dict(re.findall(r"^(\*\d+)\s+(\S+)$", spef, re.M))
        caps = {names.get(net, net): float(cap)
                for net, cap in re.findall(r"^\*D_NET\s+(\S+)\s+(\S+)", spef, re.M)}
        extracted[model] = {net: caps[net] for net in sorted(nets)}
    samples = {}
    for corner in corners:
        report = (sta / corner / "max.rpt").read_text(encoding="utf-8")
        matches = re.findall(
            r"^\s*(?:\d+\s+[\d.]+\s+)?([\d.]+)\s+[-\d.]+\s+[-\d.]+\s+[\^v]\s+(\S+/\w+)\s+\(",
            report, re.M,
        )
        values = defaultdict(list)
        for value, pin in matches:
            if pin in target_pins:
                values[pin].append(float(value))
        samples[corner] = {pin: max(values[pin], default=None) for pin in target_pins}
    return {"spef_cap_pf_by_rc": extracted, "max_path_slew_samples_ns_by_corner": samples}


def physical_metrics(outcome):
    final_metrics = RUN / "final/metrics.json"
    if final_metrics.is_file():
        physical = read(final_metrics)
    else:
        steps = sorted(RUN.glob("[0-9]*-*/state_out.json"))
        physical = read(steps[-1])["metrics"]
    result = {label: physical.get(key) for label, key in PHYSICAL_ZERO.items()}
    ir_drop = single_step("openroad-irdropreport") / "irdrop.rpt"
    power_log = ir_drop.read_text(encoding="utf-8")
    result["power_connected"] = all(f"All shapes on net {net} are connected." in power_log for net in ("VPWR", "VGND"))
    lvs = single_step("netgen-lvs") / "reports/lvs.netgen.rpt"
    result["lvs_unique_match"] = "Final result: Circuits match uniquely." in lvs.read_text(encoding="utf-8")
    if outcome == "success":
        identity = read(RUN / "final/commit_id.json")
        recorded = read(ROOT / "stage4a-run.json")
        match("artifact commit", identity["commit"], recorded["commit"])
        match("artifact run URL", identity["workflow_url"], recorded["run_url"])
    return result


def inspect_clock(state, manifest):
    def_text = routed_view(state["def"]).read_text(encoding="utf-8")
    cells = {name: body.split()[0] for name, body in def_section(def_text, "COMPONENTS").items()}
    return clock_topology(routed_view(state["nl"]), def_text, cells, manifest["clock_ff_count"])


def inspect_area(state, manifest):
    top = manifest["expected_resolved"]["DESIGN_NAME"]
    gds = RUN / "final/gds" / (top + ".gds")
    if not gds.is_file():
        gds = single_step("magic-streamout") / (top + ".gds")
    def_text = routed_view(state["def"]).read_text(encoding="utf-8")
    cells, area = routed_geometry(def_text, gds, manifest)
    physical = Counter(master[len(LIBRARY):] for master in cells.values())
    reference = manifest["c01"]["routed_master_counts"]
    area["master_count_delta_vs_c01"] = {master: physical[master] - reference.get(master, 0)
                                         for master in sorted(physical.keys() | reference.keys())
                                         if physical[master] != reference.get(master, 0)}
    area["meets_final_area_goal"] = area["area_functional_aux_um2"] <= manifest["area_goal_um2"]
    area["def_source"] = str(routed_view(state["def"]).relative_to(RUN))
    return area


def routed_metrics(manifest):
    routing = read(single_step("openroad-detailedrouting") / "or_metrics_out.json")
    result = {key: routing[key] for key in ("route__wirelength", "route__vias", "design__instance__area__stdcell")}
    result["wirelength_delta_vs_c01_um"] = result["route__wirelength"] - manifest["c01"]["routed_wirelength_um"]
    result["vias_delta_vs_c01"] = result["route__vias"] - manifest["c01"]["routed_vias"]
    return result


def verify_run(manifest, outcome, tools_sha):
    result = {"variant": manifest["variant"], "official_build_outcome": outcome, "errors": []}
    errors = result["errors"]

    def record(key, check, *args):
        try:
            result[key] = check(*args)
        except (KeyError, OSError, ValueError, TypeError, IndexError, StopIteration, struct.error) as exc:
            errors.append(f"{key}: {exc}")
        return result.get(key)

    record("input_hashes", verify_inputs, manifest)
    record("toolchain", verify_toolchain, manifest, outcome, tools_sha)
    record("repair", verify_repair, manifest)
    record("postgrt_vs_c01", compare_repair, manifest)
    record("eco_step", verify_eco, manifest)
    physical = record("physical", physical_metrics, outcome)
    if physical and (any(physical[key] != 0 for key in PHYSICAL_ZERO)
                     or not physical["power_connected"] or not physical["lvs_unique_match"]):
        errors.append("Physical verification, power or pin connectivity criteria failed")
    routing = record("routing", routed_metrics, manifest)
    sta = record("sta_step", lambda: single_step("openroad-stapostpnr").name)
    if sta:
        sta = RUN / sta
        record("effective_sdc", verify_sdc, sta, manifest)
        record("openroad_rcx", verify_extraction, sta, manifest)
        state = record("sta_state", lambda: read(sta / "state_out.json"))
        if state:
            result.pop("sta_state")
            corners = record("corners", signoff_corners, sta, state, manifest)
            for corner, row in (corners or {}).items():
                print(corner, json.dumps(row))
                if (any(row[key] != 0 for key in (*COUNT_METRICS, "unannotated_filtered_nets"))
                        or min(row["setup_ws_ns"], row["hold_ws_ns"]) < 0):
                    errors.append(f"{corner}: electric, timing or parasitic annotation criteria failed")
            tree = record("clock_tree", inspect_clock, state, manifest)
            if tree and tree[0]["master"] != manifest["expected_resolved"]["CTS_ROOT_BUFFER"]:
                errors.append(f"Routed clock root master: {tree[0]['master']}")
            area = record("geometry", inspect_area, state, manifest)
            routed_change = record("routed_vs_c01", compare_c01, state, manifest)
            if routed_change and not routed_change["netlist_changed_vs_c01"]:
                errors.append("Routed netlist has no remaining repair relative to C01")
            routed_eco = record("routed_eco", verify_routed_eco, state, manifest)
            if routed_change:
                target_pins = routed_change["target_pins"]
                if routed_eco:
                    buffer, master = routed_eco["instance"], routed_eco["master"]
                    target_pins = {**target_pins,
                                   f"{buffer}/I": {"master": master, "net": routed_eco["driver_net"]},
                                   f"{buffer}/Z": {"master": master, "net": routed_eco["load_net"]}}
                evidence = record("target_rc_and_samples", target_evidence, sta, target_pins,
                                  manifest["expected_resolved"]["STA_CORNERS"])
                if evidence and any(value is not None and value > manifest["expected_resolved"]["MAX_TRANSITION_CONSTRAINT"]
                                    for samples in evidence["max_path_slew_samples_ns_by_corner"].values()
                                    for value in samples.values()):
                    errors.append("Final STA paths still sample a target slew above the signoff limit")
            if area:
                area["delta_vs_c01_um2"] = round(
                    area["area_functional_aux_um2"] - manifest["c01"]["functional_aux_um2"], 4)
            if area and not 0 < area["area_functional_aux_um2"] <= manifest["area_limit_um2"]:
                errors.append("Functional and auxiliary area exceeds the Stage 4A budget")
            if area and routing and abs(area["area_with_tap_endcap_um2"] -
                                        routing["design__instance__area__stdcell"]) > 0.1:
                errors.append("DEF/GDS and OpenROAD area differ by more than 0.1 um²")
    if outcome != "success":
        errors.append(f"Official GDS build reported {outcome}")
    (ROOT / "stage4a-physical.json").write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    if errors:
        raise ValueError("; ".join(errors))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    subcommands = parser.add_subparsers(dest="command", required=True)
    subcommands.add_parser("inputs")
    metadata = subcommands.add_parser("identify")
    metadata.add_argument("tools_sha")
    run = subcommands.add_parser("run")
    run.add_argument("outcome", choices=("success", "failure", "cancelled", "skipped"))
    run.add_argument("tools_sha")
    args = parser.parse_args()
    manifest = read(MANIFEST)
    try:
        if args.command == "inputs":
            verify_inputs(manifest)
            verify_revision(manifest)
            print(f"{manifest['variant']}: verified {len(manifest['input_sha256'])} input hashes")
        elif args.command == "identify":
            print(json.dumps(identify(manifest, args.tools_sha), indent=2))
        else:
            verify_run(manifest, args.outcome, args.tools_sha)
    except (KeyError, OSError, ValueError, TypeError, subprocess.CalledProcessError) as exc:
        parser.exit(1, f"Stage 4A verification failed: {exc}\n")


if __name__ == "__main__":
    main()
