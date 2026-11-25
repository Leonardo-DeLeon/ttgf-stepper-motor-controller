"""Check the evidence produced after an independently reviewed E01 continuation."""

import argparse
from collections import Counter
from decimal import Decimal as D
from importlib.metadata import version
import math
from pathlib import Path
import re
import xml.etree.ElementTree as ET

from eco_signature import LIBRARY, Netlist
from prepare import MANIFEST, digest, match, read_json, read_state, require, verify_integrity, write_json
from verify import (DIE, TOP, congestion, def_section, file_nonempty, geometry, name, sdc_digest,
                    step, text, verify_netlists, verify_placement, verify_ports, verify_power)


DRV = {"max slew": "design__max_slew_violation__count",
       "max fanout": "design__max_fanout_violation__count",
       "max cap": "design__max_cap_violation__count"}


def verify_sta(run, output, manifest, corners, final):
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
    slacks = {}
    for corner in corners:
        folder = sta / corner
        checks = text(folder / "checks.rpt")
        match(f"{corner} report label", set(re.findall(r"^=+ (\w+) Corner ", checks, re.M)), {corner})
        for label, metric in DRV.items():
            match(f"{corner} {label}", metrics[metric + "__corner:" + corner], 0)
            match(f"{corner} {label} in report", re.findall(rf"^{label} violation count (\d+)\s*$", checks, re.M), ["0"])
        for mode, delay in (("setup", "max"), ("hold", "min")):
            match(f"{corner} {mode}", metrics[f"timing__{mode}_vio__count__corner:{corner}"], 0)
            value = metrics[f"timing__{mode}__ws__corner:{corner}"]
            require(math.isfinite(value) and value >= 0, f"{corner} invalid {mode} slack: {value}")
            match(f"{corner} {mode} in report", float(re.search(
                rf"^{corner}: (\S+)\s*$", text(folder / f"ws.{delay}.rpt"), re.M)[1]), value)
            slacks.setdefault(corner, {})[mode + "_ns"] = value
        annotation = "timing__unannotated_net_filtered__count__corner:" + corner
        match(f"{corner} relevant unannotated count", metrics[annotation], 0)
        match(f"{corner} annotation report", read_json(folder / "filter_unannotated_metrics.json")[annotation], 0)
        for view in ("clock.rpt", "skew.max.rpt", "skew.min.rpt", "sta.log"):
            file_nonempty(folder / view)
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


def verify_streams(gds, oasis, inventory, ports, units):
    match("KLayout Python version", version("klayout"), "0.30.14")
    import klayout.db as kdb

    layouts = []
    layer_sets = []
    for path in (gds, oasis):
        layout = kdb.Layout()
        layout.read(str(file_nonempty(path)))
        tops = layout.top_cells()
        match(f"{path.name} top", [cell.name for cell in tops], [TOP])
        cell = tops[0]
        scale = D(str(layout.dbu))

        def coordinates(box):
            return tuple(D(value) * scale for value in (box.left, box.bottom, box.right, box.top))

        boundary = [shape for shape in cell.shapes(layout.layer(0, 0)).each() if not shape.is_text()]
        match(f"{path.name} PR boundary", len(boundary), 1)
        match(f"{path.name} PR bbox", coordinates(boundary[0].bbox()), DIE)
        match(f"{path.name} overall bbox", coordinates(cell.bbox()), DIE)
        drawing_layers = {"Metal2": 36, "Metal3": 42, "Metal4": 46}
        for pin, shapes in ports.items():
            metal = drawing_layers[shapes[0][4]]
            label_shapes = cell.shapes(layout.layer(metal, 10))
            labels = [(D(shape.text.trans.disp.x) * scale, D(shape.text.trans.disp.y) * scale)
                      for shape in label_shapes.each() if shape.is_text() and name(shape.text.string) == pin]
            require(labels, f"Missing top-level label {pin} at {metal}/10")
            for _, _, _, _, _, x1, y1, x2, y2 in shapes:
                require(any(D(x1)/units <= x <= D(x2)/units and D(y1)/units <= y <= D(y2)/units
                            for x, y in labels), f"No label inside {pin} access rectangle")
        for leaf in layout.each_cell():
            for layer in layout.layer_indexes():
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
        refs = Counter()
        for instance in cell.each_inst():
            refs[instance.cell.name] += max(instance.na, 1) * max(instance.nb, 1)
        match(f"{path.name} cell inventory", refs, Counter(inventory))
        layouts.append((layout, cell))
        layer_sets.append({(layer.layer, layer.datatype) for layer in layout.layer_infos()})
    (left, left_cell), (right, right_cell) = layouts
    match("GDSII/OASIS DBU", left.dbu, right.dbu)
    match("GDSII/OASIS layer sets", layer_sets[0], layer_sets[1])
    for layer, purpose in sorted(layer_sets[0]):
        first = kdb.Region(left_cell.begin_shapes_rec(left.layer(layer, purpose)))
        second = kdb.Region(right_cell.begin_shapes_rec(right.layer(layer, purpose)))
        require((first ^ second).is_empty(), f"Nonzero stream XOR on {layer}/{purpose}")


def verify_drc_report(path, clean):
    root = ET.parse(file_nonempty(path)).getroot()
    match("KLayout report type", root.tag, "report-database")
    require(root.find("categories") is not None and root.find("items") is not None,
            f"Incomplete KLayout report: {path}")
    count = len(root.findall("./items/item"))
    if clean:
        match(path.name, count, 0)
    return count


def test_xml(path, minimum):
    root = ET.parse(file_nonempty(path)).getroot()
    tests = root.findall(".//testcase")
    require(len(tests) >= minimum, f"Too few cases in {path}")
    for case in tests:
        require(all(case.find(tag) is None for tag in ("failure", "error", "skipped")),
                f"Unsuccessful testcase: {case.get('name')}")
    return len(tests)


def verify_final(output, tag, report):
    manifest = read_json(MANIFEST)
    checkpoint = read_json(output / "checkpoint.json")
    match("checkpoint", checkpoint["status"], "partial_checkpoint_verified")
    run = output / "design/runs" / tag
    final = run / "final"
    resolved = read_json(run / "resolved.json")
    corners = checkpoint["constraints"]["corners"]
    match("final corner set", resolved["STA_CORNERS"], corners)
    previous = read_json(output / "design/runs/e01-pre-eco/resolved.json")
    for key, value in previous.items():
        if key not in {"meta", "INSERT_ECO_BUFFERS"}:
            match(f"continuation resolved {key}", resolved[key], value)
    match("continuation flow", resolved["meta"]["flow"], "Classic")
    meta_steps = resolved["meta"].get("substituting_steps") or {}
    configured = resolved.get("INSERT_ECO_BUFFERS") or []
    choice = read_json(output / "eco-decision.json")
    match("reviewed checkpoint identity", choice["state_sha256"],
          digest(output / checkpoint["checkpoint_state"]))
    start = "odb-diodesonports"
    if configured:
        match("continuation composition", meta_steps,
              {"+OpenROAD.RepairDesignPostGRT": "Odb.InsertECOBuffers"})
        match("reviewed ECO target", configured, [{"target": choice["target"],
                                                   "buffer": manifest["eco_buffer"]}])
        candidates = checkpoint["eco_review"]["candidates"]
        require(len(candidates) == 1 and candidates[0]["target"] == choice["target"]
                and candidates[0]["master"] == LIBRARY + "nor2_1"
                and checkpoint["eco_review"]["disposition"] == "unique_direct_match_review_sta",
                "ECO has no unique direct reference signature")
        start = "odb-insertecobuffers"
    else:
        match("no-ECO flow substitutions", meta_steps, {})
        match("documented decision", choice["target"], None)
        require(not list(run.glob("*-odb-insertecobuffers")), "Unexpected ECO step")
    require(choice["review_notes"].strip(), "Missing review of the repair decision")
    bridge = read_state(step(run, start) / "state_in.json", output)
    for key, source in checkpoint["checkpoint_views"].items():
        match(f"initial {key}", digest(Path(bridge[key])), digest(output / source))

    repair = step(run, "openroad-repairantennas")
    repair_logs = [log for log in repair.rglob("*.log") if "Final congestion report:" in text(log)]
    if repair_logs:
        report["grt_after_checkpoint"] = [congestion(log, post_repair=False) for log in repair_logs]
    else:
        report["grt_after_checkpoint"] = "see checkpoint; no antenna-reroute congestion table"
    if configured:
        eco_step = step(run, "odb-insertecobuffers")
        drt_step = step(run, "openroad-detailedrouting")
        routed = read_state(drt_step / "state_out.json", output)
        logical = Netlist(Path(routed["nl"]))
        audit = logical.find_eco(manifest["eco_reference_signature"])
        item, = audit["candidates"]
        match("preserved ECO driver", item["target"], choice["target"])
        match("ECO root fanout", item["direct_fanout"], 1)
        branch, = item["branches"]
        match("ECO load partition", (branch["master"], len(branch["loads"]), branch["branches"]),
              (manifest["eco_buffer"], 4, []))
        for state_file in (eco_step / "state_out.json", drt_step / "state_in.json", drt_step / "state_out.json"):
            eco_state = read_state(state_file, output)
            document = text(Path(eco_state["def"]))
            cells = def_section(document, "COMPONENTS")
            match("physical ECO buffer", cells[branch["instance"]].split()[0], manifest["eco_buffer"])
            nets = def_section(document, "NETS")
            for net in (item["net"], branch["net"]):
                pairs = re.findall(r"\(\s+(\S+)\s+(\S+)\s+\)", nets[net].split("+", 1)[0])
                match("ECO net continuity " + net, {(name(i), pin) for i, pin in pairs},
                      set(logical.loads[net]) | {logical.driver(net)})
    metrics = read_json(final / "metrics.json")
    for metric in ("route__drc_errors", "magic__drc_error__count", "magic__illegal_overlap__count",
                   "design__lvs_error__count", "antenna__violating__nets", "antenna__violating__pins",
                   "design__critical_disconnected_pin__count"):
        match(metric, metrics[metric], 0)
    lvs = step(run, "netgen-lvs") / "reports/lvs.netgen.rpt"
    require("Final result: Circuits match uniquely." in text(lvs), "Missing unique Netgen match")
    drt = text(step(run, "openroad-detailedrouting") / "openroad-detailedrouting.log")
    match("DRT stdcell pins without access", set(re.findall(r"#stdCellPinNoAp\s*=\s*(\d+)", drt)), {"0"})
    report["routing"] = {key: metrics[key] for key in ("route__wirelength", "route__vias")}
    require(min(report["routing"].values()) > 0, "Missing routing measurements")
    report["corners"] = verify_sta(run, output, manifest, corners, final)

    native = output / "final-native"
    document = text(final / "def" / (TOP + ".def"))
    units, die = geometry(document)
    routed_layers = Counter(layer for body in def_section(document, "NETS").values()
                            for layer in re.findall(r"(?:ROUTED|NEW) (Metal\d)\b", body))
    require(routed_layers and set(routed_layers) <= {"Metal1", "Metal2", "Metal3", "Metal4"},
            f"Unexpected final signal routing layers: {routed_layers}")
    report["routing"]["def_path_segments_by_layer"] = dict(routed_layers)
    native_def = text(native / "from-odb.def")
    match("ODB–DEF geometry", geometry(native_def), (units, die))
    for section in ("PINS", "COMPONENTS", "NETS", "SPECIALNETS"):
        match(f"ODB–DEF {section}", def_section(native_def, section), def_section(document, section))
    report["placement"] = verify_placement(native, document, units)
    report["ports"] = verify_ports(native, document, units, die)
    state = {"nl": final / "nl" / (TOP + ".nl.v"), "pnl": final / "pnl" / (TOP + ".pnl.v")}
    _, powered, report["cts"] = verify_netlists(state, document, manifest)
    report["pg"] = verify_power(native, document, units, text(output / "final-native.log"), powered)
    ir = text(step(run, "openroad-irdropreport") / "irdrop.rpt")
    drops = dict(re.findall(r"^Net\s*:\s*(VPWR|VGND)\n(?:(?!^Net\s*:).+\n)*?"
                            r"^Worstcase IR drop:\s*(\S+) V$", ir, re.M))
    match("IR net report set", set(drops), {"VPWR", "VGND"})
    report["worst_ir_drop_v"] = {net: float(drop) for net, drop in drops.items()}
    require(all(math.isfinite(drop) and drop >= 0 for drop in report["worst_ir_drop_v"].values()),
            "Invalid IR drop")

    external = output / "signoff"
    supplement = read_json(external / "provenance.json")
    match("test SHA", supplement["test_commit"], checkpoint["provenance"]["commit"])
    match("reviewed signoff PNL hash", supplement["pnl_sha256"], digest(state["pnl"]))
    gds = final / "gds" / (TOP + ".gds")
    match("reviewed signoff GDS hash", supplement["gds_sha256"], digest(gds))
    match("deck revision", supplement["deck_revision"], manifest["klayout_deck_revision"])
    match("deck runtime", supplement["klayout_version"], "0.30.14")
    verify_lef(final / "lef" / (TOP + ".lef"), units, report["ports"])
    verify_streams(gds, external / (TOP + ".oas"), report["placement"]["counts_by_master"],
                   report["ports"], units)
    report["klayout"] = {deck: verify_drc_report(external / f"{deck}.lyrdb", deck != "density")
                         for deck in ("geometry", "antenna", "density")}
    match("Icarus regression", re.findall(
        r"^Total: (\d+) aprobadas, (\d+) fallidas, (\d+) bancos sin resultado, (\d+) bancos incompletos\.$",
        text(external / "icarus-summary.txt"), re.M), [("198", "0", "0", "0")])
    match("RTL cocotb", test_xml(external / "rtl.xml", 2), 2)
    report["gate_level_cases"] = test_xml(external / "gl.xml", 2)
    report["status"] = "macro_checks_passed_upper_level_review_pending"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output", type=Path)
    parser.add_argument("run_tag")
    parser.add_argument("report", type=Path, help="Write the report outside the sealed payload")
    args = parser.parse_args()
    require(args.output.resolve() not in args.report.resolve().parents, "Report must be outside the payload")
    require(re.fullmatch(r"[\w.-]+", args.run_tag) and args.run_tag not in {".", "..", "e01-pre-eco"},
            "Expected a separate continuation tag")
    report = {"status": "signoff_failed"}
    try:
        verify_integrity(args.output)
        verify_final(args.output.resolve(), args.run_tag, report)
    except (ValueError, KeyError, OSError, TypeError, AttributeError, ImportError, RuntimeError, ET.ParseError) as error:
        report["error"] = str(error)
    write_json(args.report, report)
    print(report["status"], report.get("error", ""))
    return int(report["status"] == "signoff_failed")


if __name__ == "__main__":
    raise SystemExit(main())
