"""Audit E01 geometry, area and the directed STA measurements of the ECO cone."""

from collections import Counter, defaultdict
import csv
from decimal import Decimal as D
import re
import struct

from eco_signature import BUFFERS, LIBRARY, PORTS, function
from prepare import digest, match, read_json, require
from verify import CORE, DIE, TOP, def_section, name, step, text, tsv


def gds_real(raw):
    if not any(raw):
        return D(0)
    return (D((-1 if raw[0] & 128 else 1) * int.from_bytes(raw[1:], "big")) / 2**56
            * D(16)**((raw[0] & 127) - 64))


def magic_stream(path):
    """Read the authenticated Magic stream's BOUNDARY, SREF and TEXT records."""
    cells = {}
    progress = 0
    dbu = None
    data = path.read_bytes()
    while progress < len(data):
        size, kind, _ = struct.unpack_from(">HBB", data, progress)
        require(4 <= size <= len(data) - progress, "Invalid GDSII record length")
        record = data[progress+4:progress+size]
        progress += size
        if kind == 3:
            dbu = (gds_real(record[8:16]) * 10**6).quantize(D("0.000000001"))
        elif kind == 6:
            cell = record.rstrip(b"\0").decode("ascii")
            require(cell not in cells, f"Duplicate GDSII cell {cell}")
            cells[cell] = []
        elif kind in (8, 10, 12):
            element = {"kind": kind, "angle": D(0), "mirror": False}
        elif kind in (9, 11, 20, 21, 45):
            raise ValueError(f"Unexpected element {kind} in the primary Magic stream")
        elif kind in (13, 14, 22):
            element["layer" if kind == 13 else "purpose"] = int.from_bytes(record, "big")
        elif kind in (18, 25):
            element["target" if kind == 18 else "label"] = record.rstrip(b"\0").decode("ascii")
        elif kind == 26:
            flags = int.from_bytes(record, "big")
            require(flags in (0, 32768), "Unsupported GDSII transformation")
            element["mirror"] = bool(flags)
        elif kind in (27, 28) and element["kind"] == 10:
            if kind == 27:
                match("GDS instance scale", gds_real(record), D(1))
            else:
                element["angle"] = gds_real(record)
        elif kind == 16:
            element["points"] = [(D(x)*dbu, D(y)*dbu) for x, y in struct.iter_unpack(">ii", record)]
        elif kind == 17:
            cells[cell].append(element)
    require(dbu and kind == 4, "Missing GDSII units or end record")
    return cells, dbu


def boxes(elements):
    data = []
    for item in elements:
        if item["kind"] == 8:
            points = item["points"]
            match("closed GDS boundary", points[0], points[-1])
            require(sum(x*y2-x2*y for (x, y), (x2, y2) in zip(points, points[1:])) != 0,
                    "Zero-area GDS boundary")
            xs, ys = zip(*points)
            data.append((item["layer"], item["purpose"], (min(xs), min(ys), max(xs), max(ys))))
    return data


def bbox(shapes):
    require(shapes, "Empty GDS geometry")
    return (min(box[0] for box in shapes), min(box[1] for box in shapes),
            max(box[2] for box in shapes), max(box[3] for box in shapes))


def transform(box, instance):
    x, y, r, t = box
    ((dx, dy),) = instance["points"]
    # The observed placements are row-aligned orthogonal transformations.
    angle = instance["angle"] % 360
    require(angle in (0, 90, 180, 270), "Nonorthogonal GDS instance")
    def corner(u, v):
        if instance["mirror"]:
            v = -v
        point = {0: (u, v), 90: (-v, u), 180: (-u, -v), 270: (v, -u)}[int(angle)]
        return point[0] + dx, point[1] + dy
    corners = [corner(u, v) for u in (x, r) for v in (y, t)]
    xs, ys = zip(*corners)
    return min(xs), min(ys), max(xs), max(ys)


def audit_stream(gds, native, document, units, ports):
    cells, dbu = magic_stream(gds)
    references = [item for item in cells[TOP] if item["kind"] == 10]
    masters = {item["target"] for item in references}
    match("GDS hierarchy", set(cells), masters | {TOP})
    shapes = {cell: boxes(elements) for cell, elements in cells.items()}
    bounds = {}
    physical_bounds = {}
    for cell in cells:
        if cell != TOP:
            require(all(item["kind"] != 10 for item in cells[cell]), "Unexpected hierarchy in master")
        boundary = [shape for layer, purpose, shape in shapes[cell] if (layer, purpose) == (0, 0)]
        match(f"{cell} unique PR boundary", len(boundary), 1)
        bounds[cell], = boundary
        physical_bounds[cell] = bbox([box for _, _, box in shapes[cell]])
    match("top PR boundary", bounds[TOP], DIE)
    observed = Counter((item["target"], transform(bounds[item["target"]], item)) for item in references)
    expected = Counter((master, tuple(D(n)/units for n in box))
                       for _, master, _, *box in tsv(native / "instances.tsv", 7))
    match("GDS vs native ODB placed cell rectangles", observed, expected)
    placement = def_section(document, "COMPONENTS")
    orientations = {(D(0), False): "N", (D(0), True): "FS",
                    (D(180), False): "S", (D(180), True): "FN"}
    for instance, master, _, x1, y1, x2, y2 in tsv(native / "instances.tsv", 7):
        hit = re.search(r"\+ (?:PLACED|FIXED|COVER) \( (-?\d+) (-?\d+) \) (\w+)", placement[name(instance)])
        require(hit, f"Missing final DEF placement {instance}")
        match(f"{instance} DEF location vs ODB", (int(hit[1]), int(hit[2])), (int(x1), int(y1)))
    actual_orientation = Counter((item["target"], transform(bounds[item["target"]], item),
                                  orientations[(item["angle"], item["mirror"])]) for item in references)
    expected_orientation = Counter((master, box, re.search(r" \) (\w+)$", body)[1])
                                   for inst, master, _, *raw in tsv(native / "instances.tsv", 7)
                                   for box, body in [(tuple(D(n)/units for n in raw), placement[name(inst)])])
    match("GDS vs DEF orientations", actual_orientation, expected_orientation)
    all_boxes = [physical_bounds[TOP]] + [transform(physical_bounds[item["target"]], item) for item in references]
    match("complete GDS bbox including master geometry", bbox(all_boxes), DIE)
    layers = {"Metal2": 36, "Metal3": 42, "Metal4": 46}
    labels = defaultdict(list)
    for item in cells[TOP]:
        if item["kind"] == 12 and item["purpose"] == 10:
            labels[item["layer"], name(item["label"])].extend(item["points"])
    # Drawing rectangles can merge ports with routing in the Magic stream.
    for pin, rows in ports.items():
        for _, _, _, _, layer, *rect in rows:
            metal = layers[layer]
            x1, y1, x2, y2 = (D(n)/units for n in rect)
            require(any(x1 <= x <= x2 and y1 <= y <= y2 for x, y in labels[metal, pin]),
                    f"Missing GDS label within {pin} access rectangle")
            require(any(x <= x1 and y <= y1 and r >= x2 and t >= y2
                        for l, purpose, (x, y, r, t) in shapes[TOP] if l == metal and purpose == 0),
                    f"Missing GDS drawing covering {pin}")
    return {master: (bounds[master][2]-bounds[master][0], bounds[master][3]-bounds[master][1])
            for master in masters}, str(dbu)


def audit_area(output, run, masters, logical, report):
    placement = report["placement"]
    synth = step(output / "design/runs/e01-pre-eco", "yosys-synthesis") / "reports/stat.json"
    stats = read_json(synth)["modules"]["\\"+TOP]
    size = {master: (w*h).quantize(D("0.0001")) for master, (w,h) in masters.items()}
    invocation = read_json(synth)["invocation"]
    liberty_file = re.search(r"-liberty (/e01/\S+)\s", invocation)[1]
    liberty_path = output / liberty_file[len("/e01/"):]
    liberty = {cell: D(area) for cell, area in re.findall(
        r"""(?m)^  cell\s*\(\s*["']?([^\s"')]+)["']?\s*\).*?^\s+area\s*:\s*([\d.]+)\s*;""",
        text(liberty_path), re.S)}
    synth_area = sum(liberty[cell]*count for cell, count in stats["num_cells_by_type"].items())
    match("synthesis calculated area from original Liberty", synth_area, D(str(stats["area"])))
    # Yosys maps some cells subsequently resized away. The original Liberty-derived
    # synthesis report includes those masters and reports its own total area.
    counts = placement["counts_by_master"]
    total = sum(size[master]*count for master, count in counts.items())
    fill = {master for master in size if function(master) in {"fill", "fillcap", "decap"}}
    physical = {LIBRARY+"filltie", LIBRARY+"endcap"}
    before = total - sum(size[m]*counts[m] for m in fill)
    functional = before - sum(size[m]*counts[m] for m in physical)
    match("GDS total vs ODB", total, D(str(placement["area_including_fill_um2"])))
    match("GDS prefill vs ODB", before, D(str(placement["area_before_fill_um2"])))
    match("GDS functional vs ODB", functional, D(str(placement["area_functional_aux_um2"])))
    core = (CORE[2]-CORE[0])*(CORE[3]-CORE[1])
    match("GDS cell area vs core", total, core)
    match("synthesis cell inventory count", sum(stats["num_cells_by_type"].values()), stats["num_cells"])
    cts = {entry["instance"]: entry["master"] for entry in report["cts"]["levels"]}
    fill_state = step(run, "openroad-fillinsertion")
    for phase, value in (("in", sum(v for m, v in counts.items() if m not in fill)), ("out", sum(counts.values()))):
        metrics = read_json(fill_state / f"state_{phase}.json")["metrics"]
        # OpenROAD's generic count describes the current database.
        match(f"fill step {phase} instance count", metrics["design__instance__count"], value)
    return {
        "synthesis_instances": stats["num_cells"], "synthesis_area_um2": str(synth_area.quantize(D("0.0001"))),
        "functional_aux_instances": sum(n for m, n in counts.items() if m not in (fill | physical)),
        "functional_aux_um2": str(functional),
        "before_fill_instances": sum(n for m, n in counts.items() if m not in fill),
        "before_fill_um2": str(before), "fill_instances": sum(counts[m] for m in fill),
        "fill_um2": str(total-before), "total_instances": sum(counts.values()), "total_um2": str(total),
        "core_um2": str(core), "reserve_um2": str(core-before), "occupancy_before_fill_pct": float(before/core*100),
        "data_buffer_instances": sum(i not in cts and function(m) in BUFFERS for i, (m, _) in logical.cells.items()),
        "data_buffer_area_um2": str(sum(size[m] for i, (m, _) in logical.cells.items()
                                       if i not in cts and function(m) in BUFFERS)),
        "cts_instances": len(cts), "cts_dummies": sum(bool(e.get("dummy")) for e in report["cts"]["levels"]),
        "cts_area_um2": str(sum(size[m] for m in cts.values())),
        "tie_instances": sum(n for m, n in counts.items() if function(m) in {"tieh", "tiel"}),
        "flip_flops": report["cts"]["flip_flops"], "taps": counts[LIBRARY+"filltie"],
        "endcaps": counts[LIBRARY+"endcap"],
        "by_master": {m: {"instances": counts[m], "width_um": str(rect[0]),
                          "height_um": str(rect[1]), "total_um2": str(size[m]*counts[m])}
                      for m, rect in sorted(masters.items())},
    }


def number(pattern, contents):
    values = re.findall(pattern, contents, re.M)
    match(f"report field {pattern}", len(values), 1)
    return float(values[0])


def spef_cone(path, net, driver, loads):
    document = text(path)
    for unit, value in (("C", "PF"), ("R", "OHM")):
        require(re.search(rf"^\*{unit}_UNIT 1 {value}$", document, re.M), f"Unexpected SPEF {unit} units")
    names = dict(re.findall(r"^(\*\d+) (\S+)$", document, re.M))
    encoded = next(key for key, value in names.items() if value == net)
    section = re.search(rf"^\*D_NET {re.escape(encoded)} (\S+)\n(.*?)^\*END$", document, re.M | re.S)
    require(section, "Missing SPEF ECO net")
    def decode(node):
        base, pin = node.split(":")
        return f"{names[base]}/{pin}"
    terminals = {(decode(pin), direction) for pin, direction in re.findall(r"^\*I (\S+) ([IO]) ", section[2], re.M)}
    match("ECO SPEF terminals", terminals, {(driver, "O")} | {(p, "I") for p in loads})
    graph = defaultdict(list)
    resistance = 0.0
    for line in section[2].split("*RES\n")[1].splitlines():
        _, first, second, raw = line.split()
        value = float(raw)
        a, b = decode(first), decode(second)
        graph[a].append((b, value))
        graph[b].append((a, value))
        resistance += value
    distances = {}
    todo = [(driver, None, 0.0)]
    while todo:
        node, predecessor, distance = todo.pop()
        require(node not in distances, "Loop in SPEF ECO topology")
        distances[node] = distance
        todo.extend((target, node, distance + edge) for target, edge in graph[node] if target != predecessor)
    match("all ECO resistance nodes reached", set(distances), set(graph))
    return {"spef_cap_ff": float(section[1])*1000, "sum_segment_res_ohm": round(resistance, 6),
            "driver_to_load_ohm": {pin: round(distances[pin], 6) for pin in loads}}


def audit_eco(output, run, logical, document, corners, manifest, checkpoint_hash):
    signature = logical.find_eco(manifest["eco_reference_signature"])
    match("directed ECO signature", signature, read_json(output / "directed-signature.json"))
    match("directed ECO topology", signature["disposition"], "unique_direct_match_review_sta")
    candidate, = signature["candidates"]
    driver, net = candidate["target"], candidate["net"]
    instance = driver.split("/")[0]
    pins = logical.cells[instance][1]
    loads = sorted(f"{i}/{p}" for i, p in logical.loads[net])
    driver_inputs = [f"{instance}/{pin}" for pin in ("A1", "A2")]
    expected = set(loads + driver_inputs + [driver])
    match("ECO direct fanout", candidate["direct_fanout"], 4)
    endpoints = re.findall(r"\( (\S+) (\S+) \)", def_section(document, "NETS")[net].split("+")[0])
    match("DEF ECO net pins", {f"{i}/{p}" for i, p in endpoints}, {driver, *loads})
    with (step(run, "odb-reportwirelength") / "wire_lengths.csv").open() as stream:
        length, = [float(row["length_um"][:-2]) for row in csv.DictReader(stream) if row["net"] == net]
    directory = output / "directed-sta"
    match("directed STA corners", {p.stem for p in directory.glob("*.rpt")}, set(corners))
    measurements = {}
    for corner in corners:
        path = directory / f"{corner}.rpt"
        report = text(path)
        match(f"{corner} markers", re.findall(r"^E01 extracted corner (\S+)$", report, re.M), [corner])
        require(report.rstrip().endswith("E01 directed STA complete") and "VIOLATED" not in report,
                f"{corner} directed STA failed or incomplete")
        require(re.search(r"(?m)^No paths found\.$", report), f"{corner} incomplete directed violator query")
        sections = re.split(r"(?m)^E01 path (min|max) through (\S+)\n", report)
        headers = sections[0]
        reports = headers.split("\nNet ")[1:]
        match("directed ECO nets", [n.splitlines()[0] for n in reports], [net, pins["A1"], pins["A2"]])
        group = reports[0]
        match("STA ECO fanout", number(r"^ Number of loads: (\d+)$", group), 4)
        match("STA ECO driver count", number(r"^ Number of drivers: (\d+)$", group), 1)
        require(f"{driver} output ({candidate['master']})" in group, f"{corner} driver mismatch")
        for load in loads:
            cell = load.split("/")[0]
            require(f"{load} input ({logical.cells[cell][0]})" in group, f"{corner} load mismatch at {load}")
        caps = {n.splitlines()[0]: number(r"^ Total capacitance: (\S+)$", n)*1000 for n in reports}
        require(all(0 < cap <= 200 for cap in caps.values()), f"{corner} ECO cone capacitance exceeds limit")
        slews = {pin: {"rise_min_ns": float(r1), "rise_max_ns": float(r2),
                       "fall_min_ns": float(f1), "fall_max_ns": float(f2)}
                 for pin, r1, r2, f1, f2 in re.findall(
                     r"^E01 pin (\S+)\n\1 \^ (\S+):(\S+) v (\S+):(\S+)$", headers, re.M)}
        match("directed STA slew pin set", set(slews), expected)
        require(all(0 < value <= 3 for row in slews.values() for value in row.values()),
                f"{corner} ECO cone transition exceeds limit")

        timing = defaultdict(dict)
        for mode, load, body in zip(sections[1::3], sections[2::3], sections[3::3]):
            match(f"{load} directed path type", re.findall(r"^Path Type: (\w+)$", body, re.M), [mode])
            slack = number(r"^\s*([\d.]+)\s+slack \(MET\)$", body)
            arc_delay = number(rf"^\s*4\s+\S+\s+\S+\s+(\S+)\s+\S+\s+[v^] {re.escape(driver)} \(", body)
            timing[load][mode] = {"slack_ns": slack, "driver_arc_delay_ns": arc_delay}
        match("directed paths", {p: set(modes) for p, modes in timing.items()},
              {load: {"min", "max"} for load in loads})
        rc = corner.split("_", 1)[0]
        spef = run / "final/spef" / rc / f"{TOP}.{rc}.spef"
        wire = spef_cone(spef, net, driver, loads)
        sta_ff = number(r"^ Wire capacitance: (\S+)$", group)*1000
        require(abs(sta_ff-wire["spef_cap_ff"]) <= 0.002, f"{corner} SPEF vs STA wire cap")
        measurements[corner] = {**wire, "cone_total_cap_ff": {n: round(c, 3) for n, c in caps.items()},
                                "slew_by_pin": slews, "paths_by_load": dict(timing),
                                "sta_report_sha256": digest(path), "spef_sha256": digest(spef)}
    return {**candidate, "loads": loads, "input_nets": dict(zip(driver_inputs, (pins["A1"], pins["A2"]))),
            "length_um": length, "state_sha256": checkpoint_hash, "target": None, "driver": driver,
            "review_notes": "Four direct loads remain. Extracted min/nom/max RC at TT, SS and FF meets "
                            "slew, capacitance, fanout, setup and hold limits without an inserted buffer.",
            "corners": measurements}
