"""Verify E01 checkpoint evidence with the compact macro's own geometry and pin map."""

import argparse
from collections import Counter, defaultdict
from decimal import Decimal as D
from hashlib import sha256
import json
import re
from pathlib import Path

from eco_signature import Netlist, PORTS, INPUT_PORTS, OUTPUT_PORTS, PG, LIBRARY, function
from prepare import (FALLBACK, MANIFEST, WORKFLOW, digest, match, read_json, read_state, require,
                     verify_image, verify_integrity, verify_versions, write_json)


TOP = "tt_um_ieee_bootcamp_stepper"
SITE = "GF018hv5v_mcu_sc7"
CORE = tuple(map(D, ("3.36", "3.92", "156.24", "94.08")))
DIE = (0, 0, 160, 100)
PG_PORTS = {"VPWR": "POWER", "VGND": "GROUND"}
PIN_LAYERS = {"N": "Metal2", "S": "Metal2", "E": "Metal3", "W": "Metal3"}


def step(run, suffix):
    found = list(run.glob(f"[0-9]*-{suffix}"))
    match(f"{suffix} step count", len(found), 1)
    require(found[0].is_dir(), f"Missing step directory: {found[0]}")
    return found[0]


def text(path):
    return path.read_text(encoding="utf-8")


def name(value):
    return value.replace("\\", "")


def file_nonempty(path):
    require(path.is_file() and path.stat().st_size > 0, f"Missing or empty evidence: {path}")
    return path


def def_section(document, entry):
    section = re.search(rf"^{entry}\s+(\d+)\s*;\s*\n(.*?)^END {entry}\b", document, re.M | re.S)
    require(section, f"Missing DEF {entry}")
    items = re.findall(r"^\s*-\s+(\S+)\s+(.*?)\s*;", section[2], re.M | re.S)
    result = {name(pin): " ".join(body.split()) for pin, body in items}
    match(f"DEF {entry} count", (len(items), len(result)), (int(section[1]), int(section[1])))
    return result


def tsv(path, columns):
    rows = [line.split("\t") for line in text(path).splitlines()]
    require(rows and all(len(row) == columns for row in rows), f"Invalid TSV: {path}")
    return rows


def sdc_digest(path):
    lines = [" ".join(line.split()) for line in text(path).splitlines()
             if line.strip() and not line.lstrip().startswith("#")]
    return sha256(("\n".join(sorted(lines)) + "\n").encode()).hexdigest()


def verify_provenance(output, manifest, original=None, identity=None):
    actual = read_json(output / "provenance.json")
    match("archived manifest", digest(output / "manifest.json"), digest(MANIFEST))
    for key in ("variant", "base_commit", "repository", "ref", "image"):
        match(key, actual[key], manifest[key])
    match("dispatch event", actual["event"], "workflow_dispatch")
    match("phase", actual["phase"], "post_grt_checkpoint")
    match("stable mounts", actual["mounts"], {"output": "/e01", "ciel": "/e01-pdk", "scripts": "/e01-src"})
    match("support tools reference", actual["tt_support_tools_reference"], manifest["tt_support_tools"])
    for key, pattern in {"commit": "[0-9a-f]{40}", "run_id": "[1-9][0-9]*",
                         "attempt": "[1-9][0-9]*"}.items():
        require(re.fullmatch(pattern, actual[key]), f"Invalid provenance field {key}")
    publication = set(manifest["publication_paths"])
    if FALLBACK in actual["publication"]:
        publication.add(FALLBACK)
        match("fallback bytes", actual["publication"][FALLBACK], actual["publication"][WORKFLOW])
    match("publication inventory", set(actual["publication"]), publication)
    checkout = MANIFEST.parents[2]
    published = original if original is not None else checkout
    if identity is not None:
        for key in ("commit", "run_id", "attempt", "workflow_ref"):
            match("checkpoint " + key, actual[key], identity[key])
    for path in sorted(publication):
        match("publication " + path, actual["publication"][path], digest(published / path))
    workflow_refs = {f"{actual['repository']}/{path}@{actual['ref']}"
                     for path in publication if path.startswith(".github/workflows/")}
    require(actual["workflow_ref"] in workflow_refs, "Unexpected workflow source ref")
    match("inherited inputs", actual["inputs"], manifest["baseline_inputs"])
    for path, expected in actual["inputs"].items():
        match("original " + path, digest(published / path), expected)
        match("checkout " + path, digest(checkout / path), expected)
        if path.startswith("src/") and path.endswith(".v"):
            match("staged " + path, digest(output / "design/rtl" / Path(path).name), expected)
    for file, expected in manifest["configuration_hashes"].items():
        match(file, digest(output / "design" / file), expected)
        match(file + " publication", actual["publication"]["experiments/stage5/" + file], expected)
    verify_image(output / "container-inspect.json", manifest)
    verify_versions(output / "tool-versions.txt", manifest)
    return actual


def verify_resolved(run, config, manifest):
    resolved = read_json(run / "resolved.json")
    # The standalone CLI records the effective revision in the Ciel path.
    # pdk.json is supplied by the Tiny Tapeout flow, not by this E01 run.
    match("effective PDK", resolved["PDK"], manifest["pdk"])
    version_root = f"/e01-pdk/ciel/gf180mcu/versions/{manifest['pdk_revision']}"
    match("effective PDK revision root", resolved["PDK_ROOT"], version_root)
    match("resolved LibreLane version", resolved["meta"]["librelane_version"], manifest["librelane"])
    pdk = {"PDK": resolved["PDK"], "PDK_ROOT": resolved["PDK_ROOT"],
           "PDK_VERSION": manifest["pdk_revision"], "FLOW_VERSION": manifest["librelane"]}
    match("library", resolved["STD_CELL_LIBRARY"], manifest["scl"])
    match("power pins", resolved["SCL_POWER_PINS"], ["VDD", "VNW"])
    match("ground pins", resolved["SCL_GROUND_PINS"], ["VSS", "VPW"])
    for key, value in config.items():
        if key == "meta":
            continue
        if key in {"IO_PIN_ORDER_CFG", "VERILOG_FILES"}:
            base = "/e01/design/"
            rewrite = lambda path: path.replace("dir::", base, 1)
            value = [rewrite(path) for path in value] if isinstance(value, list) else rewrite(value)
        if key in {"LIB", "RCX_RULESETS"}:
            base = f"/e01-pdk/ciel/gf180mcu/versions/{manifest['pdk_revision']}/{manifest['pdk']}/"
            actual = resolved[key]
            match(f"{key} key set", set(actual), set(value))
            for corner, entries in value.items():
                values = entries if isinstance(entries, list) else [entries]
                paths = actual[corner] if isinstance(actual[corner], list) else [actual[corner]]
                expected = [path.replace("pdk_dir::", base, 1) for path in values]
                # Ciel may return the active root symlink or the immutable version path.
                aliases = [path.replace(base, f"/e01-pdk/{manifest['pdk']}/", 1)
                           for path in expected]
                require(paths in (expected, aliases), f"Unexpected {key} {corner}: {paths}")
            continue
        match(f"resolved {key}", resolved[key], value)
    meta = resolved["meta"]
    match("flow", meta["flow"], "Classic")
    require(not meta.get("substituting_steps") and not resolved.get("INSERT_ECO_BUFFERS"),
            "Unexpected ECO in Post-GRT checkpoint configuration")
    return {"pdk": pdk, "corners": resolved["STA_CORNERS"],
            "io_load_ff": resolved["OUTPUT_CAP_LOAD"]}


def geometry(document):
    match("DEF DESIGN", re.search(r"^DESIGN (\S+)\s*;", document, re.M)[1], TOP)
    units = int(re.search(r"^UNITS DISTANCE MICRONS (\d+)\s*;", document, re.M)[1])
    die = tuple(int(i) for i in re.search(
        r"^DIEAREA \( (-?\d+) (-?\d+) \) \( (-?\d+) (-?\d+) \)\s*;", document, re.M).groups())
    match("DIEAREA (µm)", tuple(D(i) / units for i in die), DIE)
    rows = re.findall(r"^ROW \S+ (\S+) (\d+) (\d+) (\S+) DO (\d+) BY (\d+) STEP (\d+) (\d+)\s*;",
                      document, re.M)
    expected = [(SITE, int(CORE[0] * units), int((CORE[1] + D("3.92") * i) * units),
                 "N" if i % 2 == 0 else "FS", 273, 1, int(D("0.56") * units), 0)
                for i in range(23)]
    actual = [(site, int(x), int(y), orient, *map(int, rest)) for site, x, y, orient, *rest in rows]
    match("full aligned rows", sorted(actual, key=lambda row: row[2]), expected)
    return units, die


def verify_placement(native, document, units):
    components = def_section(document, "COMPONENTS")
    inventory = Counter()
    areas = defaultdict(int)
    seen = set()
    row_bboxes = defaultdict(list)
    core = tuple(int(value * units) for value in CORE)
    for instance, master, status, *raw in tsv(native / "instances.tsv", 7):
        instance = name(instance)
        require(instance not in seen, f"Duplicate ODB instance: {instance}")
        seen.add(instance)
        match(instance + " DEF master", components[instance].split()[0], master)
        match(instance + " library", master[:len(LIBRARY)], LIBRARY)
        require(status in {"PLACED", "FIRM", "LOCKED", "FIXED", "COVER"}, f"Unplaced cell {instance}")
        x1, y1, x2, y2 = map(int, raw)
        require(core[0] <= x1 < x2 <= core[2] and core[1] <= y1 < y2 <= core[3],
                f"{instance} outside core")
        require(D(y2 - y1) / units == D("3.92")
                and (D(y1 - core[1]) / units) % D("3.92") == 0
                and (D(x1 - core[0]) / units) % D("0.56") == 0,
                f"{instance} off site or row")
        inventory[master] += 1
        areas[master] += (x2 - x1) * (y2 - y1)
        row_bboxes[y1].append((x1, x2, instance))
    match("DEF/ODB instance set", seen, set(components))
    for intervals in row_bboxes.values():
        positions = sorted(intervals)
        for first, second in zip(positions, positions[1:]):
            require(first[1] <= second[0], f"Overlap {first[2]} and {second[2]}")
    match("netlist FF count from placement", sum(n for cell, n in inventory.items()
                                                if function(cell).startswith("dff")), 61)
    counts = {name: inventory[LIBRARY + name] for name in ("filltie", "endcap")}
    require(min(counts.values()) > 0, "Tap or endcap insertion missing")
    fill = sum(n for master, n in areas.items() if function(master) in {"fill", "fillcap", "decap"})
    taps = sum(areas[LIBRARY + item] for item in counts)
    total = sum(areas.values())
    return {
        "die_um": [160, 100], "die_um2": 16000,
        "core_bbox_um": [float(i) for i in CORE], "core_um2": 13783.6608,
        "rows": 23, "sites_per_row": 273, "counts_by_master": dict(inventory),
        "taps_endcaps": counts, "instances": sum(inventory.values()),
        "area_functional_aux_um2": (total - fill - taps) / units**2,
        "area_before_fill_um2": (total - fill) / units**2,
        "area_including_fill_um2": total / units**2,
        "occupancy_before_fill_pct": 100 * (total - fill) / units**2 / 13783.6608,
    }


def verify_ports(native, document, units, die):
    ports = defaultdict(list)
    for pin, direction, use, net, count, layer, *rect in tsv(native / "ports.tsv", 10):
        ports[name(pin)].append((direction, use, name(net), int(count), layer, *map(int, rect)))
    expected = INPUT_PORTS | OUTPUT_PORTS | PG_PORTS.keys()
    match("DEF pin names", set(def_section(document, "PINS")), expected)
    match("ODB pin names", set(ports), expected)
    for side, names in PORTS.items():
        centers = []
        for pin in names:
            match(f"{pin} shapes", len(ports[pin]), 1)
            direction, use, _, count, layer, x1, y1, x2, y2 = ports[pin][0]
            match(f"{pin} direction", direction, "INPUT" if pin in INPUT_PORTS else "OUTPUT")
            match(f"{pin} use/layer", (use, layer), ("SIGNAL", PIN_LAYERS[side]))
            match(f"{pin} deliberate load exception", count == 0, pin in PORTS["S"][:8])
            require(die[0] <= x1 < x2 <= die[2] and die[1] <= y1 < y2 <= die[3],
                    f"{pin} outside die")
            if side in ("N", "S"):
                match(f"{pin} length and edge", (y2 - y1, y2 if side == "N" else y1),
                      (2 * units, die[3] if side == "N" else die[1]))
                centers.append(D(x1 + x2) / (2 * units))
            else:
                match(f"{pin} length and edge", (x2 - x1, x2 if side == "E" else x1),
                      (2 * units, die[2] if side == "E" else die[0]))
                centers.append(D(y1 + y2) / (2 * units))
        for center in centers:
            match(f"{side} pin track", (center - D("0.28")) % D("0.56"), D(0))
        gaps = [right - left for left, right in zip(centers, centers[1:])]
        require(min(gaps) >= D("1.68"), f"{side}: order or spacing incorrect: {centers}")
    for pin, use in PG_PORTS.items():
        stripes = []
        for direction, ptype, net, count, layer, x1, y1, x2, y2 in ports[pin]:
            match(pin, (direction, ptype, net, layer), ("INOUT", use, pin, "Metal4"))
            require(count > 0 and die[0] <= x1 < x2 <= die[2] and die[1] <= y1 < y2 <= die[3],
                    f"Missing/outside PG access: {pin}")
            match(f"{pin} strap width", D(x2 - x1) / units, D("1.6"))
            stripes.append(D(x1 + x2) / (2 * units))
        require(len(stripes) >= 2, f"Missing multiple access stripes: {pin}")
        positions = sorted(stripes)
        match(f"{pin} first strap center", positions[0],
              D("19.68") if pin == "VPWR" else D("22.98"))
        for left, right in zip(positions, positions[1:]):
            match(f"{pin} same-net pitch", right - left, D("38.87"))
    return {pin: [list(row) for row in geometry] for pin, geometry in sorted(ports.items())}


def verify_power(native, document, units, log, design):
    markers = ["E01 placement checked", "E01 power grid checked VPWR", "E01 power grid checked VGND"]
    for marker in markers:
        require(marker in log, f"Missing native OpenROAD evidence: {marker}")
    require(log.count("All shapes on net VPWR are connected.") == 1 and
            log.count("All shapes on net VGND are connected.") == 1, "Missing native PG continuity")
    by_instance = defaultdict(dict)
    for instance, pin, use, net in tsv(native / "pg.tsv", 4):
        instance = name(instance)
        match(f"{instance}/{pin} PG net", net, PG[pin])
        match(f"{instance}/{pin} PG use", use, PG_PORTS[net])
        by_instance[instance][pin] = net
    match("PG coverage", set(by_instance), set(def_section(document, "COMPONENTS")))
    for instance, (master, pins) in design.cells.items():
        mapping = {pin: value for pin, value in pins.items() if pin in PG}
        match(f"{instance} PNL vs ODB PG", mapping, by_instance[instance])
        expected = {"VDD": "VPWR", "VSS": "VGND"} if function(master) in {"endcap", "filltie"} else PG
        match(f"{instance} PG mapping", mapping, expected)
    pg_nets = def_section(document, "SPECIALNETS")
    match("DEF special nets", set(pg_nets), set(PG_PORTS))
    rails = {}
    vias = {}
    for net, use in PG_PORTS.items():
        require(f"+ USE {use}" in pg_nets[net], f"Wrong purpose for {net}")
        elements = re.findall(r"(?:ROUTED|NEW) (Metal\d) (\d+) \+ SHAPE (FOLLOWPIN|STRIPE)"
                              r" \( (-?\d+) (-?\d+) \) \( (-?\d+) (-?\d+) \)", pg_nets[net])
        rails[net] = []
        types = set()
        for layer, width, kind, x1, y1, x2, y2 in elements:
            types.add((layer, kind))
            half = D(width) / 2
            x1, y1, x2, y2 = map(int, (x1, y1, x2, y2))
            if kind == "FOLLOWPIN":
                match("rail shape", (layer, y1, D(width) / units), ("Metal1", y2, D("0.6")))
                box = min(x1, x2), y1 - half, max(x1, x2), y1 + half
            else:
                match("stripe shape", (layer, x1, D(width) / units), ("Metal4", x2, D("1.6")))
                box = x1 - half, min(y1, y2), x1 + half, max(y1, y2)
            rails[net].append((layer, box))
        match(f"{net} rails and stripes", types, {("Metal1", "FOLLOWPIN"), ("Metal4", "STRIPE")})
        vias[net] = defaultdict(set)
        for layer, x, y in re.findall(
                r"(?:ROUTED|NEW) (Metal[123]) 0 \+ SHAPE STRIPE \( (-?\d+) (-?\d+) \) \S+", pg_nets[net]):
            vias[net][(x, y)].add(layer)
        require(any(stack == {"Metal1", "Metal2", "Metal3"} for stack in vias[net].values()),
                f"No Metal1–Metal4 via stack for {net}")
    require(not (vias["VPWR"].keys() & vias["VGND"].keys()), "Opposing PG vias at same location")
    for layer, a in rails["VPWR"]:
        for other, b in rails["VGND"]:
            if layer == other:
                require(a[2] < b[0] or b[2] < a[0] or a[3] < b[1] or b[3] < a[1],
                        f"Opposite-rail contact on {layer}")
    return {"connected": sorted(PG_PORTS), "rail_or_stripe_counts": {k: len(v) for k, v in rails.items()},
            "via_locations": {k: len(v) for k, v in vias.items()}}


def verify_netlists(state, document, manifest):
    logical = Netlist(Path(state["nl"]))
    powered = Netlist(Path(state["pnl"]))
    components = def_section(document, "COMPONENTS")
    match("NL/PNL instances", set(logical.cells), set(powered.cells))
    for instance, (master, pins) in logical.cells.items():
        other, powered_pins = powered.cells[instance]
        match(f"{instance} NL/PNL/DEF master", (other, components[instance].split()[0]), (master, master))
        match(f"{instance} NL/PNL connectivity", {p: n for p, n in powered_pins.items() if p not in PG}, pins)
    logical.check_logic()
    clock = logical.clock_tree(manifest["cts_root"])
    nets = def_section(document, "NETS")
    for level in clock["levels"]:
        if level.get("dummy"):
            continue
        net = level["net"]
        pins = set(re.findall(r"\(\s+(\S+)\s+(\S+)\s+\)", nets[net].split("+", 1)[0]))
        match(f"{net} clock loads and driver in DEF",
              {(name(inst), pin) for inst, pin in pins}, set(level["loads"]) | {logical.driver(net)})
    require(re.search(r"\( PIN clk \)", nets["clk"]), "Missing DEF clk terminal")
    return logical, powered, clock


def congestion(path, post_repair=True):
    log = text(path)
    sections = log.split("Final congestion report:")[1:]
    require(len(sections) >= (2 if post_repair else 1), f"Incomplete GRT evidence in {path}")
    if post_repair:
        require(re.search(r"\+ repair_design\b[^\n]*-slew_margin 10(?:\.0+)? "
                          r"-cap_margin 10(?:\.0+)?\b", log), "Missing effective GRT repair options")
    require(not re.search(r"^\+ global_route .*\s-allow_congestion\b", log, re.M),
            "GRT ran with congestion allowed")
    reports = []
    for section in sections:
        layers = {}
        for layer, resource, demand, usage, hor, vert, overflow in re.findall(
                r"^(Metal\d|Total)\s+(\d+)\s+(\d+)\s+([\d.]+)%\s+(\d+)\s*/\s*(\d+)\s*/\s*(\d+)",
                section, re.M):
            match(f"{layer} overflow", [int(i) for i in (hor, vert, overflow)], [0, 0, 0])
            layers[layer] = {"resources": int(resource), "demand": int(demand), "usage_pct": float(usage)}
        require({"Metal2", "Metal3", "Metal4", "Total"} <= layers.keys(), "Missing congestion table/layers")
        require(layers["Total"]["demand"] > 0, "GRT did not route any demand")
        reports.append(layers)
    lengths = re.findall(r"Total wirelength:\s*(\d+) um", log)
    require(lengths and int(lengths[-1]) > 0, "Missing GRT wirelength")
    return {"tables": reports, "wirelength_um": int(lengths[-1])}


def verify_checkpoint(output, manifest, report, original=None, identity=None):
    report["provenance"] = verify_provenance(output, manifest, original, identity)
    config = read_json(output / "design/config.json")
    run = output / "design/runs/e01-pre-eco"
    report["constraints"] = verify_resolved(run, config, manifest)
    repair = step(run, "openroad-repairdesignpostgrt")
    require(not list(run.glob("*-odb-insertecobuffers")) and not list(run.glob("*-openroad-detailedrouting")),
            "Checkpoint includes a downstream physical step")
    for suffix in ("openroad-floorplan", "openroad-generatepdn", "openroad-globalplacement",
                   "odb-customioplacement", "openroad-detailedplacement", "openroad-cts",
                   "openroad-globalrouting", "openroad-tapendcapinsertion"):
        file_nonempty(step(run, suffix) / f"{suffix}.log")
    state = read_state(repair / "state_out.json", output)
    for layout, suffix in {"odb": ".odb", "def": ".def", "nl": ".nl.v",
                           "pnl": ".pnl.v", "sdc": ".sdc"}.items():
        match(f"Post-GRT {layout} path", Path(state[layout]), repair / (TOP + suffix))
    match("effective Post-GRT SDC", sdc_digest(Path(state["sdc"])), manifest["sdc_command_sha256"])
    report["constraints"]["effective_sdc_commands_sha256"] = manifest["sdc_command_sha256"]
    file_nonempty(repair / "after_grt.guide")
    before = step(run, "openroad-globalrouting") / "openroad-globalrouting.log"
    report["grt_initial"] = congestion(before, post_repair=False)
    data = congestion(repair / "openroad-repairdesignpostgrt.log")
    data["vias_estimated"] = state["metrics"]["global_route__vias"]
    require(data["vias_estimated"] > 0, "Missing GRT vias")
    match("GRT wirelength metric", state["metrics"]["global_route__wirelength"], data["wirelength_um"])
    report["grt"] = data
    log = text(repair / "openroad-repairdesignpostgrt.log")
    for edge in ("min", "max"):
        layer = config["RT_" + edge.upper() + "_LAYER"]
        require(f"Setting signal {edge} routing layer to: {layer} and clock {edge} routing layer to {layer}."
                in log, f"Missing effective signal/clock {edge} layer evidence")
    document = text(Path(state["def"]))
    units, die = geometry(document)
    native = output / "native"
    native_def = text(native / "from-odb.def")
    match("ODB–DEF geometry", geometry(native_def), (units, die))
    for section in ("PINS", "COMPONENTS", "NETS", "SPECIALNETS"):
        match(f"ODB–DEF {section}", def_section(native_def, section), def_section(document, section))
    report["placement"] = verify_placement(native, document, units)
    report["ports"] = verify_ports(native, document, units, die)
    logical, powered, report["cts"] = verify_netlists(state, document, manifest)
    report["pg"] = verify_power(native, document, units, text(output / "native.log"), powered)
    report["eco_review"] = logical.find_eco(manifest["eco_reference_signature"])
    report["checkpoint_views"] = {key: str(Path(state[key]).relative_to(output))
                                  for key in ("odb", "def", "nl", "pnl", "sdc")}
    report["checkpoint_state"] = str((repair / "state_out.json").relative_to(output))
    report["status"] = "partial_checkpoint_verified"


def replay_checkpoint(output, original, identity, report):
    verify_integrity(output)
    match("original payload manifest", digest(output / "SHA256SUMS"), identity["payload_manifest_sha256"])
    match("original checkpoint record", digest(output / "checkpoint.json"), identity["checkpoint_record_sha256"])
    match("original checkpoint provenance", digest(output / "provenance.json"), identity["provenance_sha256"])
    saved = read_json(output / "checkpoint.json")
    match("original physical flow return code", saved["flow_status"], 0)
    match("original native inspection return code", saved["inspection_status"], 0)
    verify_checkpoint(output, read_json(MANIFEST), report, original, identity)
    match("original state hash", digest(output / report["checkpoint_state"]), identity["state_sha256"])


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("phase", choices=("checkpoint",))
    parser.add_argument("output", type=Path)
    parser.add_argument("--flow-status", type=int, default=0)
    parser.add_argument("--inspection-status", type=int, default=0)
    parser.add_argument("--archive", action="store_true", help="Replay an existing sealed archive")
    parser.add_argument("--e01-original", type=Path, help="Export of the pinned E01 Git commit")
    parser.add_argument("--report", type=Path, help="Write a separate replay report outside the payload")
    args = parser.parse_args()
    output = args.output.resolve()
    if args.e01_original and not args.archive:
        parser.error("--e01-original requires --archive")
    if args.report and (not args.archive or args.report.resolve() == output
                        or output in args.report.resolve().parents):
        parser.error("--report requires --archive and a path outside the payload")
    report = {"status": "partial_checkpoint_failed", "signoff": "pending",
              "flow_status": args.flow_status, "inspection_status": args.inspection_status}
    try:
        match("flow return code", args.flow_status, 0)
        match("native inspection return code", args.inspection_status, 0)
        if args.e01_original:
            identity = read_json(Path(__file__).with_name("continuation.json"))["checkpoint"]
            replay_checkpoint(output, args.e01_original, identity, report)
        else:
            if args.archive:
                verify_integrity(output)
                saved = read_json(output / "checkpoint.json")
                match("saved checkpoint status", saved["status"], "partial_checkpoint_verified")
            verify_checkpoint(output, read_json(MANIFEST), report)
            if args.archive:
                match("replayed checkpoint report", json.loads(json.dumps(report)), saved)
    except (ValueError, KeyError, OSError, TypeError, AttributeError) as error:
        report.update(status="partial_checkpoint_failed", error=str(error))
    if args.report:
        write_json(args.report, report)
    elif not args.archive:
        write_json(output / "checkpoint.json", report)
    print(report["status"], report.get("error", ""))
    return int(report["status"] != "partial_checkpoint_verified")


if __name__ == "__main__":
    raise SystemExit(main())
