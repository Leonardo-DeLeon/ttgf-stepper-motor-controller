"""Check the evidence produced after an independently reviewed E01 continuation."""

import argparse
from collections import Counter
from datetime import datetime
from fnmatch import fnmatchcase
from decimal import Decimal as D
from importlib.metadata import PackageNotFoundError, version
import math
from pathlib import Path
import re
import xml.etree.ElementTree as ET

from compact_audit import audit_area, audit_eco, audit_stream
from eco_signature import PORTS, function
from prepare import (MANIFEST, digest, match, read_json, read_state, require,
                     verify_image, verify_integrity, verify_versions, write_json)
from recover_signoff import (BENCHES, CASES, IDENTITY, verify_artifact, verify_execution, verify_job,
                             verify_views)
from resume import PLAN, verify_actions, verify_bridge
from verify import (DIE, TOP, congestion, def_section, file_nonempty, geometry, name, sdc_digest,
                    step, text, verify_netlists, verify_placement, verify_ports, verify_power, verify_resolved)


DRV = {"max slew": "design__max_slew_violation__count",
       "max fanout": "design__max_fanout_violation__count",
       "max cap": "design__max_cap_violation__count"}


def verify_sta(run, output, manifest, corners, final, loose_outputs):
    sta = step(run, "openroad-stapostpnr")
    state_in = read_state(sta / "state_in.json", output)
    metrics = read_json(sta / "state_out.json")["metrics"]
    match("effective STA SDC", sdc_digest(Path(state_in["sdc"])), manifest["sdc_command_sha256"])
    match("STA corner report set", {p.parent.name for p in sta.glob("*/checks.rpt")}, set(corners))
    for rc in ("min", "nom", "max"):
        path = Path(state_in["spef"][rc + "_*"])
        spef = text(path)
        match(f"{rc} RCX revision", re.search(r'^\*VERSION "([^"]+)"', spef, re.M)[1],
              manifest["openroad_rcx"])
        match(f"{rc} SPEF vs final", digest(path), digest(final / "spef" / rc / path.name))
        require(re.search(r"^\*D_NET ", spef, re.M), f"{rc} SPEF has no extracted networks")
    match("STA corner library and extracted netlist hashes", digest(Path(state_in["nl"])),
          digest(final / "nl" / f"{TOP}.nl.v"))
    match("nine final SDF corners", {p.parent.name for p in final.glob("sdf/*/*.sdf")}, set(corners))
    match("nine final timing model corners", {p.parent.name for p in final.glob("lib/*/*.lib")}, set(corners))
    directed = {}
    for line in text(output / "directed-sta-inputs.tsv").splitlines():
        label, liberty, nl, sdc, rcx, *cone = line.split("\t")
        require(label not in directed and len(cone) == 5, "Duplicate or incomplete directed STA corner")
        directed[label] = (liberty, nl, sdc, rcx)
    match("directed STA corner inputs", set(directed), set(corners))
    resolved = read_json(run / "resolved.json")
    slacks = {}
    for corner in corners:
        folder = sta / corner
        for suffix, subdir in (("sdf", "sdf"), ("lib", "lib")):
            archived = folder / f"{TOP}__{corner}.{suffix}"
            match(f"{corner} {suffix}", digest(archived), digest(final / subdir / corner / archived.name))
        liberty, = [lib for pattern, libs in resolved["LIB"].items() if fnmatchcase(corner, pattern) for lib in libs]
        rc = corner.split("_", 1)[0]
        mounted = lambda value: "/e01/" + Path(value).relative_to(output).as_posix()
        match(f"{corner} directed inputs", directed[corner],
              (liberty, mounted(state_in["nl"]), mounted(state_in["sdc"]), mounted(state_in["spef"][rc+"_*"])))
        checks = text(folder / "checks.rpt")
        match(f"{corner} report label", set(re.findall(r"^=+ (\w+) Corner ", checks, re.M)), {corner})
        for label, metric in DRV.items():
            match(f"{corner} {label}", metrics[metric + "__corner:" + corner], 0)
            slacks.setdefault(corner, {})[metric.split("__")[1][4:] + "_violations"] = 0
            match(f"{corner} {label} in report", re.findall(rf"^{label} violation count (\d+)\s*$", checks, re.M), ["0"])
        for mode, delay in (("setup", "max"), ("hold", "min")):
            match(f"{corner} {mode}", metrics[f"timing__{mode}_vio__count__corner:{corner}"], 0)
            slacks[corner][mode + "_violations"] = 0
            value = metrics[f"timing__{mode}__ws__corner:{corner}"]
            require(math.isfinite(value) and value >= 0, f"{corner} invalid {mode} slack: {value}")
            match(f"{corner} {mode} in report", float(re.search(
                rf"^{corner}: (\S+)\s*$", text(folder / f"ws.{delay}.rpt"), re.M)[1]), value)
            slacks.setdefault(corner, {})[mode + "_ns"] = value
        annotation = "timing__unannotated_net_filtered__count__corner:" + corner
        match(f"{corner} relevant unannotated count", metrics[annotation], 0)
        match(f"{corner} annotation report", read_json(folder / "filter_unannotated_metrics.json")[annotation], 0)
        result = slacks[corner]
        raw = text(folder / "filter_unannotated.log").split("Reported nets:", 1)[0]
        match("unannotated pin set", set(re.findall(r"' (\S+)\\n'", raw)), loose_outputs)
        match("partially unannotated driver count", re.findall(r"Found (\d+) partially unannotated drivers", raw), ["0"])
        match("unannotated driver metric", metrics["timing__unannotated_net__count__corner:"+corner], len(loose_outputs))
        result.update(relevant_unannotated=0, raw_unannotated=len(loose_outputs), partially_unannotated=0)
        for mode, delay in (("setup", "max"), ("hold", "min")):
            skew = float(re.search(rf"^([-\d.]+) {mode} skew$", text(folder / f"skew.{delay}.rpt"), re.M)[1])
            require(abs(skew - metrics[f"clock__skew__worst_{mode}__corner:{corner}"]) <= 1e-6,
                    f"{corner} inconsistent {mode} skew")
            result[mode+"_skew_ns"] = skew
        clock = text(folder / "clock.rpt")
        match("resolved clock period in report", re.findall(r"^Period: (\S+)$", clock, re.M), ["1000.000000"])
        for edge, payload in re.findall(r"(rise|fall) -> \1\n(.*?)(?=\n\n)", clock, re.S):
            a, b, skew = re.search(r"^([\d.]+) ([\d.]+) latency\n\s+([\d.]+) skew$", payload, re.M).groups()
            result[edge+"_clock_latency_ns"] = [float(a), float(b)]
            result[edge+"_clock_tree_skew_ns"] = float(skew)
        require({"rise_clock_latency_ns", "fall_clock_latency_ns"} <= result.keys(), "Incomplete clock latency")
        for check, delay in (("recovery", "max"), ("removal", "min")):
            paths = text(folder / f"{delay}.rpt").split("Startpoint: ")[1:]
            samples = [float(re.search(r"^\s*([-\d.]+)\s+slack \(MET\)$", path, re.M)[1])
                       for path in paths if f"({check} check" in path]
            result[check + "_sample_count"] = len(samples)
            result[check + "_sample_min_slack_ns"] = min(samples) if samples else None
        result["min_pulse_width_slack_ns"] = None  # Directed STA checks violators only.
        file_nonempty(folder / "sta.log")
    return slacks


def verify_lef(path, units, ports):
    lef = text(path)
    match("LEF macro", re.findall(r"^MACRO (\S+)", lef, re.M), [TOP])
    size = re.search(r"^\s*SIZE ([\d.]+) BY ([\d.]+)\s*;", lef, re.M)
    match("LEF bbox dimensions", tuple(map(D, size.groups())), DIE[2:])
    pins = {}
    for pin, body in re.findall(r"^\s*PIN (\S+)\s*\n(.*?)^\s*END \1\s*$", lef, re.M | re.S):
        pins[name(pin)] = body
    match("LEF pins", set(pins), set(ports))
    for pin, body in pins.items():
        expected = ports[pin]
        require(re.search(rf"\bDIRECTION {expected[0][0]}(?:\s+TRISTATE)?\s*;", body),
                f"LEF direction: {pin}")
        use = re.search(r"\bUSE (\w+)\s*;", body)
        match(f"LEF use {pin}", use[1] if use else "SIGNAL", expected[0][1])
        shapes = re.findall(r"\bLAYER (\w+)\s*;\s*RECT ([\d. -]+)\s*;", body)
        actual = Counter((layer, *[D(n) for n in rect.split()]) for layer, rect in shapes)
        match(f"LEF vs DEF pin rectangles {pin}", actual,
              Counter((layer, *(D(n) / units for n in coords))
                      for _, _, _, _, layer, *coords in expected))


def stream_labels(cells, index):
    labels = Counter()
    orientations = {}
    for cell_name, cell in cells.items():
        for shape in cell.shapes(index).each():
            if shape.is_text():
                label = shape.text
                transform = label.trans
                key = (cell_name, label.string, transform.disp.x, transform.disp.y)
                labels[key] += 1
                orientations.setdefault(key, Counter())[(90 * transform.angle, transform.is_mirror())] += 1
    return labels, orientations


def orientation_counts(counts):
    return [{"rotation_degrees": angle, "mirror": mirror, "count": count}
            for (angle, mirror), count in sorted(counts.items())]


def record_layer_result(report, layer, datatype, first, second, xor_shapes, xor_area):
    first_labels, first_orientation = first
    second_labels, second_orientation = second
    row = {"layer": layer, "datatype": datatype, "gds_labels": sum(first_labels.values()),
           "oasis_labels": sum(second_labels.values()), "labels_equal": first_labels == second_labels,
           "xor_shapes": xor_shapes, "xor_area_dbu2": xor_area}
    report["layers"].append(row)
    for key in sorted(first_labels.keys() & second_labels.keys()):
        if first_labels[key] == second_labels[key] and first_orientation[key] != second_orientation[key]:
            cell_name, label, x, y = key
            report["orientation_differences"].append({
                "cell": cell_name, "name": label, "x_dbu": x, "y_dbu": y,
                "layer": layer, "datatype": datatype,
                "gds": orientation_counts(first_orientation[key]),
                "oasis": orientation_counts(second_orientation[key]),
            })
    if not row["labels_equal"]:
        row["gds_only_labels"] = sorted((first_labels - second_labels).items())
        row["oasis_only_labels"] = sorted((second_labels - first_labels).items())


def verify_layer_results(report):
    rows = report["layers"]
    report["xor_shapes"] = sum(row["xor_shapes"] for row in rows)
    mismatch = [(row["layer"], row["datatype"]) for row in rows if not row["labels_equal"]]
    match("GDSII/OASIS semantic label mismatches by layer", mismatch, [])
    mismatch = [(row["layer"], row["datatype"]) for row in rows if row["xor_shapes"]]
    match("GDSII/OASIS nonzero XOR layers", mismatch, [])


def verify_streams(gds, oasis, inventory, ports, units, report):
    expected_version = read_json(IDENTITY)["runtime"]["klayout"]
    try:
        installed_version = version("klayout")
    except PackageNotFoundError as error:
        raise ValueError(f"KLayout Python package {expected_version} is unavailable") from error
    match("KLayout Python version", installed_version, expected_version)
    import klayout.db as kdb

    streams = []
    for path in (gds, oasis):
        layout = kdb.Layout()
        layout.read(str(file_nonempty(path)))
        streams.append(layout)
    match("GDSII/OASIS DBU", streams[0].dbu, streams[1].dbu)

    layouts = []
    for path, layout in zip((gds, oasis), streams):
        tops = layout.top_cells()
        match(f"{path.name} top", [cell.name for cell in tops], [TOP])
        cell = tops[0]
        scale = D(str(layout.dbu))

        def coordinates(box):
            return tuple(D(value) * scale for value in (box.left, box.bottom, box.right, box.top))

        layer_info = [(index, layout.get_info(index)) for index in layout.layer_indexes()]
        layers = {(info.layer, info.datatype): index for index, info in layer_info}
        require((0, 0) in layers, f"{path.name} missing PR boundary layer")
        boundary_index = layers[0, 0]
        boundary = [shape for shape in cell.shapes(boundary_index).each() if not shape.is_text()]
        match(f"{path.name} PR boundary", len(boundary), 1)
        shape, = boundary
        match(f"{path.name} PR bbox", coordinates(shape.bbox()), DIE)
        expected_boundary = kdb.Region(shape.bbox())
        actual_boundary = kdb.Region(shape.box if shape.is_box() else shape.polygon)
        require((expected_boundary ^ actual_boundary).is_empty(),
                f"{path.name} PR boundary is not rectangular")
        match(f"{path.name} overall bbox", coordinates(cell.bbox()), DIE)
        drawing_layers = {"Metal2": 36, "Metal3": 42, "Metal4": 46}
        for pin, shapes in ports.items():
            metal = drawing_layers[shapes[0][4]]
            require((metal, 10) in layers, f"Missing marker layer {metal}/10")
            label_shapes = cell.shapes(layers[metal, 10])
            labels = [(D(shape.text.trans.disp.x) * scale, D(shape.text.trans.disp.y) * scale)
                      for shape in label_shapes.each() if shape.is_text() and name(shape.text.string) == pin]
            require(labels, f"Missing top-level label {pin} at {metal}/10")
            for _, _, _, _, _, x1, y1, x2, y2 in shapes:
                require(any(D(x1)/units <= x <= D(x2)/units and D(y1)/units <= y <= D(y2)/units
                            for x, y in labels), f"No label inside {pin} access rectangle")
        cells = {entry.name: entry for entry in layout.each_cell()}
        placements = {}
        for leaf in cells.values():
            for layer in layers.values():
                for shape in leaf.shapes(layer).each():
                    polygon = None
                    if shape.is_box():
                        polygon = shape.box
                    elif shape.is_polygon() or shape.is_simple_polygon():
                        polygon = shape.polygon
                    elif shape.is_path():
                        polygon = shape.path.polygon()
                    if polygon is not None:
                        require(polygon.area() > 0, f"Zero area in {path.name}/{leaf.name}/{layer}")
            placements[leaf.name] = Counter(
                (instance.cell.name, transform.disp.x, transform.disp.y,
                 transform.angle, transform.is_mirror(), transform.mag)
                for instance in leaf.each_inst() for transform in instance.cell_inst.each_cplx_trans())
        refs = Counter()
        for (master, *_), count in placements[TOP].items():
            refs[master] += count
        match(f"{path.name} cell inventory", refs, Counter(inventory))
        layouts.append((cell, layers, cells, placements))
    (left_cell, left_layers, left_cells, left_placements), (
        right_cell, right_layers, right_cells, right_placements) = layouts
    match("GDSII/OASIS layer sets", set(left_layers), set(right_layers))
    match("GDSII/OASIS cell definitions", set(left_cells), set(right_cells))
    for cell_name in sorted(left_cells):
        match(f"GDSII/OASIS instance placements in {cell_name}",
              left_placements[cell_name], right_placements[cell_name])

    report.update(klayout_version=expected_version, dbu_um=streams[0].dbu, cell_count=len(left_cells),
                  top_instances=sum(inventory.values()), layers=[], orientation_differences=[])
    for layer, datatype in sorted(left_layers):
        pair = layer, datatype
        left_labels = stream_labels(left_cells, left_layers[pair])
        right_labels = stream_labels(right_cells, right_layers[pair])
        first = kdb.Region(left_cell.begin_shapes_rec(left_layers[pair]))
        second = kdb.Region(right_cell.begin_shapes_rec(right_layers[pair]))
        xor = first ^ second
        record_layer_result(report, layer, datatype, left_labels, right_labels, xor.size(), xor.area())
    verify_layer_results(report)


def verify_drc_report(path, clean):
    root = ET.parse(file_nonempty(path)).getroot()
    match("KLayout report type", root.tag, "report-database")
    require(root.find("categories") is not None and root.find("items") is not None,
            f"Incomplete KLayout report: {path}")
    count = len(root.findall("./items/item"))
    if clean:
        match(path.name, count, 0)
    match(f"{path.name} top", root.findtext("top-cell"), TOP)
    require((root.findtext("generator") or "").endswith("/gf180mcu.drc'"),
            f"Unexpected DRC generator in {path}")
    require(root.findall(".//categories/category"), f"No rule categories in {path}")
    return count


def test_xml(path, minimum):
    root = ET.parse(file_nonempty(path)).getroot()
    tests = root.findall(".//testcase")
    require(len(tests) >= minimum, f"Too few cases in {path}")
    match("wrapper case identities", {case.get("name") for case in tests}, CASES)
    match("test case uniqueness", len(tests), len(CASES))
    for suite in ([root] if root.tag == "testsuite" else root.findall("./testsuite")):
        for key in ("errors", "failures", "skipped"):
            match(f"{path.name} suite {key}", int(suite.get(key, "0")), 0)
    for case in tests:
        match("cocotb suite", case.get("classname"), "test_stepper")
        require(all(case.find(tag) is None for tag in ("failure", "error", "skipped")),
                f"Unsuccessful testcase: {case.get('name')}")
    return len(tests)




def verify_gl_xml(gl):
    results = gl / "results.xml"
    if results.is_file():
        match("Gate Level result SHA", text(gl / "results.sha256").split()[0], digest(results))
    def utc_timestamp(value):
        instant = re.fullmatch(r"(\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d)(?:\.(\d{1,9}))?(?:Z|\+00:00)", value)
        require(instant is not None, f"Invalid UTC Gate Level timestamp: {value}")
        return datetime.fromisoformat(instant[1]), int((instant[2] or "").ljust(9, "0"))

    start, stop = map(utc_timestamp, text(gl / "execution-window.txt").splitlines())
    require(start <= stop, "Reversed Gate Level execution window")
    for suite in ET.parse(file_nonempty(results)).getroot().findall("./testsuite"):
        stamp = utc_timestamp(suite.attrib["timestamp"])
        require(start <= stamp <= stop, "Stale Gate Level XML outside test window")
    return test_xml(results, 2)

def verify_rtl(directory):
    match("Icarus regression", re.findall(
        r"^Total: (\d+) aprobadas, (\d+) fallidas, (\d+) bancos sin resultado, (\d+) bancos incompletos\.$",
        text(directory / "output/summary.txt"), re.M), [("198", "0", "0", "0")])
    for bench, count in BENCHES.items():
        match(f"Icarus {bench}", re.findall(
            rf"^RESULT tb_{bench} passed=(\d+) failed=(\d+)$",
            text(directory / "output" / f"tb_{bench}.log"), re.M), [(str(count), "0")])
    match("RTL cocotb", test_xml(directory / "results.xml", 2), 2)
    return {"icarus": sum(BENCHES.values()), "cocotb": 2, "benches": BENCHES}

def verify_physical(output, tag, report, *, check_view_hashes=True):
    manifest = read_json(MANIFEST)
    identity = read_json(IDENTITY)["continuation"]
    plan = read_json(PLAN)
    verify_execution(output, identity)
    verify_bridge(output, output / "restored-state.json")
    checkpoint = read_json(output / "checkpoint-replay.json")
    match("checkpoint", checkpoint["status"], "partial_checkpoint_verified")
    run = output / "design/runs" / tag
    final = run / "final"
    if check_view_hashes:
        verify_views(final, identity["views"])
    source = output / plan["checkpoint"]["state_path"]
    match("checkpoint state SHA-256", digest(source), plan["checkpoint"]["state_sha256"])
    match("restored state vs original", read_json(output / "restored-state.json"), read_json(source))
    original_state = read_state(source, output)
    initial_state = read_state(step(run, "odb-diodesonports") / "state_in.json", output)
    report["initial_view_sha256"] = {}
    for key in ("odb", "def", "nl", "pnl", "sdc"):
        signature = digest(Path(original_state[key]))
        match(f"first state {key}", digest(Path(initial_state[key])), signature)
        report["initial_view_sha256"][key] = signature
    guide = source.with_name("after_grt.guide")
    match("checkpoint guide SHA-256", digest(guide), plan["checkpoint"]["guide_sha256"])
    reopened = output / "odb-reopened.guide"
    def guide_boxes(path):
        nets = re.findall(r"^([^\s()]+)\n\(\n(.*?)^\)", text(path), re.M | re.S)
        return {net: Counter(tuple(line.split()) for line in boxes.splitlines()) for net, boxes in nets}
    guide_nets = guide_boxes(guide)
    restored_guides = guide_boxes(reopened)
    match("reopened guide boxes", restored_guides, guide_nets)
    match("reopened guide counts", len(guide_nets),
          len(text(output / "after_grt.guide.nets").splitlines()))
    match("archived guide net lists", text(output / "after_grt.guide.nets"),
          text(output / "odb-reopened.guide.nets"))
    report["guides"] = {"nets": len(guide_nets), "same_rectangles": guide_nets == restored_guides,
                        "original_sha256": digest(guide), "reopened_sha256": digest(reopened)}
    verify_image(output / "continuation-container-inspect.json", manifest)
    verify_versions(output / "continuation-tool-versions.txt", manifest)
    for tool in ("librelane", "openroad", "opensta", "architecture", "open_pdks"):
        pattern = rf"^{tool}:.*$"
        match(tool, re.findall(pattern, text(output / "continuation-tool-versions.txt"), re.M),
              re.findall(pattern, text(output / "tool-versions.txt"), re.M))
    config = verify_resolved(run, read_json(output / "design/config.json"), manifest)
    corners = checkpoint["constraints"]["corners"]
    match("resolved constraints vs replay", (config["corners"], config["io_load_ff"]),
          (corners, checkpoint["constraints"]["io_load_ff"]))
    match("final corner set", read_json(run / "resolved.json")["STA_CORNERS"], corners)
    match("no repeated synthesis, floorplan, placement or CTS",
          [p.name for p in run.iterdir()
           if re.search(r"^\d+-(?:yosys-.*|openroad-(?:.*floorplan|.*placement|cts))$", p.name)], [])
    report["grt"] = congestion(source.with_name("openroad-repairdesignpostgrt.log"))
    repair = step(run, "openroad-repairantennas")
    repair_logs = [log for log in repair.rglob("*.log") if "Final congestion report:" in text(log)]
    report["antenna_reroute_grt"] = [congestion(log, post_repair=False) for log in repair_logs]

    metrics = read_json(final / "metrics.json")
    for metric in ("route__drc_errors", "magic__drc_error__count", "magic__illegal_overlap__count",
                   "design__lvs_error__count", "antenna__violating__nets", "antenna__violating__pins",
                   "design__critical_disconnected_pin__count"):
        match(metric, metrics[metric], 0)
    lvs = step(run, "netgen-lvs") / "reports/lvs.netgen.rpt"
    require("Final result: Circuits match uniquely." in text(lvs), "Missing unique Netgen match")
    lvs_step = lvs.parents[1]
    lvs_script = text(lvs_step / "lvs_script.lvs")
    lvs_state = read_state(lvs_step / "state_in.json", output)
    for key, suffix in (("pnl", ".pnl.v"), ("spice", ".spice")):
        current = Path(lvs_state[key])
        require("/e01/" + current.relative_to(output).as_posix() in lvs_script,
                f"Netgen did not read expected {key}")
        match(f"LVS {key} vs final digest", digest(current), digest(final / key / (TOP + suffix)))
    magic = step(run, "magic-drc")
    match("Magic violations", re.findall(r"^\[INFO\] COUNT: (\d+)$",
          text(magic / "reports/drc.magic.rpt"), re.M), ["0"])
    antenna = text(step(run, "openroad-checkantennas-1") / "reports/antenna.rpt")
    match("Antenna net/pin counts", re.findall(r"Found (\d+) (net|pin) violations", antenna),
          [("0", "net"), ("0", "pin")])
    disconnected = text(step(run, "odb-reportdisconnectedpins") / "full_disconnected_pins_table.txt")
    match("deliberately unloaded pins", set(re.findall(r"│ (uio_in\[\d+\])\s+│$", disconnected, re.M)),
          set(PORTS["S"][:8]))
    drt = text(step(run, "openroad-detailedrouting") / "openroad-detailedrouting.log")
    match("DRT stdcell pins without access", set(re.findall(r"#stdCellPinNoAp\s*=\s*(\d+)", drt)), {"0"})
    history = [int(n) for n in re.findall(r"^\[INFO DRT-0199\]\s+Number of violations = (\d+)\.", drt, re.M)]
    match("detailed-router final DRC", history[-1], 0)
    drt_report = step(run, "openroad-detailedrouting") / f"{TOP}.drc.xml"
    match("route report item count", len(ET.parse(drt_report).getroot().findall("./items/item")), 0)
    report["routing"] = {key: metrics[key] for key in ("route__wirelength", "route__vias")}
    require(min(report["routing"].values()) > 0, "Missing routing measurements")
    for field in ("wirelength", "vias"):
        regex = "Total wire length = (\d+) um" if field == "wirelength" else "Total number of vias = (\d+)"
        match(f"final DRT {field} log and metrics", int(re.findall(regex, drt)[-1]), report["routing"]["route__"+field])
    report["routing"]["drc_history"] = history
    report["physical_checks"] = {"magic_drc_errors": 0, "router_drc_errors": 0, "lvs_unique": True,
                                 "antenna_nets": 0, "antenna_pins": 0, "critical_disconnected": 0}

    native = output / "final-native"
    document = text(final / "def" / (TOP + ".def"))
    units, die = geometry(document)
    routed_layers = Counter(layer for body in def_section(document, "NETS").values()
                            for layer in re.findall(r"(?:ROUTED|NEW) (Metal\d)\b", body))
    require(routed_layers and set(routed_layers) <= {"Metal1", "Metal2", "Metal3", "Metal4"},
            f"Unexpected final signal routing layers: {routed_layers}")
    report["routing"]["def_path_segments_by_layer"] = dict(routed_layers)
    signal_routes = def_section(document, "NETS")
    vias = Counter(via for body in signal_routes.values() for via in re.findall(r"\) (Via\w+)\b", body))
    match("DEF signal vias", sum(vias.values()), report["routing"]["route__vias"])
    report["routing"]["def_signal_vias_by_type"] = dict(vias)
    path_dbu = 0
    for body in signal_routes.values():
        for route in re.split(r"(?:ROUTED|NEW) Metal\d ", body)[1:]:
            prefix = re.match(r"(?:\( [-\d*]+ [-\d*]+(?: [-\d]+)? \)\s*)+", route)
            if not prefix:
                continue
            previous = None
            for x, y in re.findall(r"\( ([-\d*]+) ([-\d*]+)(?: [-\d]+)? \)", prefix[0]):
                point = tuple(previous[i] if value == "*" else int(value) for i, value in enumerate((x, y)))
                if previous is not None:
                    path_dbu += sum(abs(a-b) for a, b in zip(point, previous))
                previous = point
    report["routing"]["def_signal_path_centerlines_um"] = path_dbu / units
    native_def = text(native / "from-odb.def")
    match("ODB–DEF geometry", geometry(native_def), (units, die))
    for section in ("PINS", "COMPONENTS", "NETS", "SPECIALNETS"):
        match(f"ODB–DEF {section}", def_section(native_def, section), def_section(document, section))
    report["placement"] = verify_placement(native, document, units)
    report["ports"] = verify_ports(native, document, units, die)
    state = {"nl": final / "nl" / (TOP + ".nl.v"), "pnl": final / "pnl" / (TOP + ".pnl.v")}
    for kind, path in state.items():
        declarations = {}
        netlist = text(path)
        match(f"{kind} top module", re.findall(r"^module (\S+) \(", netlist, re.M), [TOP])
        for direction, msb, lsb, pin in re.findall(r"^\s*(input|output|inout)(?: \[(\d+):(\d+)\])? (\w+);",
                                                   netlist, re.M):
            expanded = [f"{pin}[{i}]" for i in range(int(lsb), int(msb)+1)] if msb else [pin]
            declarations.update((bit, direction.upper()) for bit in expanded)
        match(f"{kind} directions and port widths", declarations,
              {pin: rows[0][0] for pin, rows in report["ports"].items() if kind == "pnl" or pin not in {"VPWR", "VGND"}})
    logical, powered, report["cts"] = verify_netlists(state, document, manifest)
    loose_outputs = {f"uio_in[{i}]" for i in range(8)}
    for level in report["cts"]["levels"]:
        if level.get("dummy"):
            instance = level["instance"]
            outputs = [pin for pin in ("Z", "ZN") if pin in logical.cells[instance][1]]
            if not outputs:
                outputs = ["ZN" if function(level["master"]) in {"clkinv", "inv"} else "Z"]
            pin, = outputs
            loose_outputs.add(f"{instance}/{pin}")
    report["corners"] = verify_sta(run, output, manifest, corners, final, loose_outputs)
    report["pg"] = verify_power(native, document, units, text(output / "final-native.log"), powered)
    ir = text(step(run, "openroad-irdropreport") / "irdrop.rpt")
    drops = dict(re.findall(r"^Net\s*:\s*(VPWR|VGND)\n(?:(?!^Net\s*:).+\n)*?"
                            r"^Worstcase IR drop:\s*(\S+) V$", ir, re.M))
    match("IR net report set", set(drops), {"VPWR", "VGND"})
    report["worst_ir_drop_v"] = {net: float(drop) for net, drop in drops.items()}
    require(all(math.isfinite(drop) and drop >= 0 for drop in report["worst_ir_drop_v"].values()),
            "Invalid IR drop")

    verify_lef(final / "lef" / (TOP + ".lef"), units, report["ports"])
    gds = final / "gds" / (TOP + ".gds")
    match("primary GDS vs Magic stream", digest(gds),
          digest(final / "mag_gds" / (TOP + ".magic.gds")))
    masters, dbu = audit_stream(gds, native, document, units, report["ports"])
    report["gds"] = {"sha256": digest(gds), "bbox_um": list(DIE), "dbu_um": dbu}
    report["area"] = audit_area(output, run, masters, logical, report)
    report["eco"] = audit_eco(output, run, logical, document, corners, manifest, digest(source))
    report["eco"]["candidate_commit"] = identity["commit"]
    report["eco"]["candidate_run_id"] = identity["run_id"]
    report["eco"]["driver_instance"] = report["eco"]["driver"].rsplit("/", 1)[0]
    match("final SDC commands", sdc_digest(final / "sdc" / (TOP + ".sdc")),
          manifest["sdc_command_sha256"])
    return gds, state["pnl"], units


def verify_final(output, tag, report):
    manifest = read_json(MANIFEST)
    identity = read_json(IDENTITY)
    gds, pnl, units = verify_physical(output, tag, report)
    external = output / "signoff"
    supplement = read_json(external / "provenance.json")
    match("external continuation identity", supplement["continuation"], identity["continuation"])
    match("external RTL identity", supplement["rtl"], identity["rtl"])
    execution = supplement["execution"]
    match("external branch and workflow", (execution["GITHUB_REF"], execution["GITHUB_WORKFLOW_REF"]),
          (identity["continuation"]["ref"], identity["continuation"]["workflow_ref"]))
    require(re.fullmatch(r"[0-9a-f]{40}", execution["GITHUB_SHA"]) and
            all(re.fullmatch(r"[1-9]\d*", execution[key]) for key in ("GITHUB_RUN_ID", "GITHUB_RUN_ATTEMPT")),
            "Missing signoff commit, run or attempt")
    require(execution["GITHUB_RUN_ID"] != identity["continuation"]["run_id"],
            "External signoff needs its own run")
    physical = identity["continuation"]
    attempt = read_json(external / "continuation-attempt.json")
    verify_actions(attempt, read_json(external / "continuation-artifacts.json"), physical)
    match("official continuation success", attempt["conclusion"], "success")
    verify_job(read_json(external / "continuation-jobs.json"), physical)
    rtl = identity["rtl"]
    verify_artifact(read_json(external / "rtl-artifacts.json"), rtl)
    verify_job(read_json(external / "rtl-jobs.json"), rtl)
    match("original RTL ZIP", (file_nonempty(external / "rtl.zip").stat().st_size, digest(external / "rtl.zip")),
          (rtl["artifact_size"], rtl["artifact_sha256"]))
    from zipfile import ZipFile
    with ZipFile(external / "rtl.zip") as archive:
        match("official RTL extraction inventory", set(archive.namelist()),
              {p.relative_to(external / "rtl").as_posix()
               for p in (external / "rtl").rglob("*") if p.is_file()})
        for filename in archive.namelist():
            match(f"official RTL {filename}", (external / "rtl" / filename).read_bytes(), archive.read(filename))
    report["rtl"] = verify_rtl(external / "rtl")

    drc = external / "drc"
    match("GF180D deck revision", text(drc / "deck-revision.txt").strip(), manifest["klayout_deck_revision"])
    require(re.search(r"(?<![\d.])0\.30\.14(?![\d.])", text(drc / "klayout-version.txt")),
            "Wrong KLayout CLI version")
    drivers = [line.split(maxsplit=1) for line in text(drc / "drivers.sha256").splitlines()]
    match("DRC driver count", len(drivers), 2)
    deck_revision = manifest["klayout_deck_revision"]
    require("/versions/" + deck_revision + "/gf180mcuD/" in drivers[0][1] and
            drivers[0][1].endswith("/gf180mcu.drc"), "Wrong DRC deck path")
    require(re.search(rf"^{drivers[0][0]}  \./gf180mcu\.drc$", text(drc / "deck-files.sha256"), re.M),
            "DRC driver is missing from the deck manifest")
    match("DRC source GDS", drivers[1][0], digest(gds))
    modes = {"geometry": "all,-density,-antenna", "antenna": "antenna", "density": "density"}
    match("deck mode execution", text(drc / "deck-modes.tsv").splitlines(),
          [f"{deck}\t{mode}" for deck, mode in modes.items()])
    report["klayout"] = {}
    for deck in modes:
        match(f"{deck} DRC exit", text(drc / f"{deck}.status").strip(), "0")
        require("Executing rule" in text(drc / f"{deck}.log"), f"Missing executed rules for {deck}")
        database = drc / f"{deck}.lyrdb"
        match(f"{deck} RDB hash", text(drc / f"{deck}.sha256").split()[0], digest(database))
        report["klayout"][deck] = verify_drc_report(database, deck != "density")
    oasis = external / (TOP + ".oas")
    report["stream_equivalence"] = {"gds_sha256": digest(gds), "oasis_sha256": digest(oasis),
                                    "expected_klayout_version": identity["runtime"]["klayout"]}

    gl = external / "gl"
    match("Gate Level exit", text(gl / "status.txt").strip(), "0")
    require(re.search(r"^Icarus Verilog version 13\.0 ", text(gl / "iverilog-version.txt"), re.M),
            "Wrong Gate Level Icarus version")
    match("Gate Level cocotb version", text(gl / "cocotb-version.txt").strip(), "2.1.0")
    inputs = [line.split(maxsplit=1) for line in text(gl / "inputs.sha256").splitlines()]
    match("Gate Level inputs", len(inputs), 3)
    library = manifest["scl"]
    models = f"/versions/{manifest['pdk_revision']}/gf180mcuD/libs.ref/{library}/verilog/"
    for (hashed, src), filename in zip(inputs, ("primitives.v", library + ".v", pnl.name)):
        require(re.fullmatch(r"[0-9a-f]{64}", hashed) and Path(src).name == filename,
                f"Invalid Gate Level input {src}")
        if filename != pnl.name:
            require(models in src, f"Wrong GF180D model revision in {src}")
    match("Gate Level simulated PNL", inputs[2][0], digest(pnl))
    log = text(gl / "run.log")
    for macro in ("GL_TEST", "FUNCTIONAL", "USE_POWER_PINS", "SIM", "UNIT_DELAY"):
        require(f"-D{macro}" in log, f"Missing Gate Level compile define {macro}")
    require("TESTS=2 PASS=2 FAIL=0 SKIP=0" in log, "Missing Gate Level aggregate")
    report["gate_level_cases"] = verify_gl_xml(gl)
    report["signoff_execution"] = execution
    report["gate_level_mode"] = "functional_1MHz"
    verify_streams(gds, oasis, report["placement"]["counts_by_master"], report["ports"], units,
                   report["stream_equivalence"])
    report["status"] = "macro_checks_passed_upper_level_review_pending"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output", type=Path)
    parser.add_argument("run_tag")
    parser.add_argument("report", type=Path, help="Write the report outside the sealed payload")
    parser.add_argument("--physical-only", action="store_true")
    args = parser.parse_args()
    require(args.output.resolve() not in args.report.resolve().parents, "Report must be outside the payload")
    require(re.fullmatch(r"[\w.-]+", args.run_tag) and args.run_tag not in {".", "..", "e01-pre-eco"},
            "Expected a separate continuation tag")
    report = {"status": "signoff_failed"}
    try:
        verify_integrity(args.output)
        if args.physical_only:
            verify_physical(args.output.resolve(), args.run_tag, report)
            report["status"] = "physical_evidence_verified_external_signoff_pending"
        else:
            verify_final(args.output.resolve(), args.run_tag, report)
    except (ValueError, KeyError, OSError, TypeError, AttributeError, ImportError, RuntimeError, IndexError, ET.ParseError) as error:
        report["error"] = str(error)
    write_json(args.report, report)
    print(report["status"], report.get("error", ""))
    return int(report["status"] == "signoff_failed")


if __name__ == "__main__":
    raise SystemExit(main())
