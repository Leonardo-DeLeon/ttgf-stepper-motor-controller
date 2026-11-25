"""Identify the reference ECO cone using connectivity. This module never writes a netlist."""

from collections import defaultdict
from functools import lru_cache
import re

from prepare import require


LIBRARY = "gf180mcu_fd_sc_mcu7t5v0__"
PG = {"VDD": "VPWR", "VNW": "VPWR", "VSS": "VGND", "VPW": "VGND"}
OUTPUTS = {"Z", "ZN", "Q", "QN"}
BUFFERS = {"buf", "clkbuf", "dlyb"}
PORTS = {
    "N": ("clk", "rst_n", "ena"),
    "W": tuple(f"ui_in[{i}]" for i in range(8)),
    "E": tuple(f"uo_out[{i}]" for i in range(8)),
    "S": tuple(f"{bus}[{i}]" for bus in ("uio_in", "uio_out", "uio_oe") for i in range(8)),
}
INPUT_PORTS = set(PORTS["N"] + PORTS["W"] + PORTS["S"][:8])
OUTPUT_PORTS = set(PORTS["E"] + PORTS["S"][8:])
CONSTANT_PORTS = set(PORTS["E"][6:] + PORTS["S"][8:])


def function(master):
    return re.sub(r"_\d+$", "", master[len(LIBRARY):])


class Netlist:
    def __init__(self, path):
        text = path.read_text(encoding="utf-8")
        cell_pattern = rf"^\s*({LIBRARY}\w+)\s+(\S+)\s*\((.*?)\);"
        terminal_pattern = r"\.(\w+)\s*\(\s*(.*?)\s*\)"
        self.cells = {}
        self.drivers = defaultdict(list)
        self.loads = defaultdict(list)
        self.exported = defaultdict(set)
        for name in OUTPUT_PORTS:
            self.exported[name].add(name)
        for port, net in re.findall(r"^\s*assign\s+(\S+)\s*=\s*(.*?)\s*;", text, re.M):
            require(port in OUTPUT_PORTS, f"Unknown continuous assignment: {port}")
            self.exported[net.lstrip("\\")].add(port)
            self.exported.pop(port, None)
        for master, name, body in re.findall(cell_pattern, text, re.M | re.S):
            pins = {pin: net.strip().lstrip("\\")
                    for pin, net in re.findall(terminal_pattern, body, re.S)}
            name = name.lstrip("\\")
            physical_only = function(master) in {"filltie", "endcap", "fill", "fillcap", "decap"}
            require(name not in self.cells and (pins or physical_only), f"Invalid instance {name}")
            self.cells[name] = master, pins
            for pin, net in pins.items():
                if net and pin not in PG:
                    table = self.drivers if pin in OUTPUTS else self.loads
                    table[net].append((name, pin))
        require(self.cells, f"No GF180 cells parsed: {path}")
        self.ffs = {name for name, (master, _) in self.cells.items()
                    if function(master).startswith("dff")}

    def driver(self, net):
        drivers = self.drivers[net]
        require(len(drivers) == 1 and net not in INPUT_PORTS,
                f"Expected one internal driver on {net}, found {drivers}")
        return drivers[0]

    def ff_identity(self, name):
        _, pins = self.cells[name]
        outputs = sorted(net for pin, net in pins.items() if pin in {"Q", "QN"} and net)
        stable = sorted({net for net in outputs if net.startswith("controller.")}
                        | {port for net in outputs for port in self.exported[net]})
        require(stable, f"Sequential endpoint lacks a stable logical name: {name}")
        return stable

    @lru_cache(maxsize=None)
    def upstream(self, net, trail=()):
        require(net and net not in trail, f"Cycle or missing upstream connection: {net}")
        if net in INPUT_PORTS:
            require(not self.drivers[net], f"Internally driven input port: {net}")
            return ["port", net]
        name, output = self.driver(net)
        master, pins = self.cells[name]
        if name in self.ffs:
            return ["ff", self.ff_identity(name), output]
        kind = function(master)
        if kind in BUFFERS:
            return self.upstream(pins["I"], (*trail, net))
        inputs = {pin: self.upstream(other, (*trail, net))
                  for pin, other in sorted(pins.items()) if pin not in PG and pin not in OUTPUTS}
        return [kind, output, inputs]

    @lru_cache(maxsize=None)
    def downstream(self, net, trail=()):
        require(net not in trail, f"Cycle in downstream cone: {net}")
        self.driver(net)
        endpoints = {("port", port) for port in self.exported[net]}
        for instance, pin in self.loads[net]:
            if instance in self.ffs:
                endpoints.update(("ff", name, pin) for name in self.ff_identity(instance))
                continue
            _, pins = self.cells[instance]
            for output, other in pins.items():
                if output in OUTPUTS and other:
                    endpoints.update(self.downstream(other, (*trail, net)))
        return endpoints

    def partition(self, net, trail=()):
        require(net not in trail, f"Cycle in load partition: {net}")
        self.driver(net)
        leaves = []
        branches = []
        for instance, pin in self.loads[net]:
            master, pins = self.cells[instance]
            if function(master) in BUFFERS and pin == "I" and pins.get("Z"):
                loads, sub = self.partition(pins["Z"], (*trail, net))
                leaves.extend(loads)
                branches.append({"instance": instance, "master": master, "net": pins["Z"],
                                 "loads": loads, "branches": sub})
            else:
                outputs = [other for port, other in pins.items() if port in OUTPUTS and other]
                endpoints = {end for other in outputs for end in self.downstream(other)}
                leaves.append({"function": function(master), "pin": pin,
                               "endpoints": [list(end) for end in sorted(endpoints)]})
        leaves.extend({"port": port} for port in sorted(self.exported[net]))
        return sorted(leaves, key=repr), branches

    def signature(self, instance):
        master, pins = self.cells[instance]
        net = pins["ZN"]
        require(self.driver(net) == (instance, "ZN"), f"Unexpected NOR driver on {net}")
        loads, branches = self.partition(net)
        signature = {
            "function": function(master),
            "inputs": {pin: self.upstream(pins[pin]) for pin in ("A1", "A2")},
            "receivers": loads,
        }
        return signature, {"target": f"{instance}/ZN", "master": master, "net": net,
                           "direct_fanout": len(self.loads[net]), "effective_fanout": len(loads),
                           "branches": branches}

    def find_eco(self, reference):
        candidates = []
        for name, (master, pins) in self.cells.items():
            if function(master) != reference["function"]:
                continue
            try:
                inputs = {pin: self.upstream(pins[pin]) for pin in ("A1", "A2")}
                if inputs != reference["inputs"]:
                    continue
                signature, detail = self.signature(name)
            except ValueError:
                continue
            if signature == reference:
                candidates.append(detail)
        disposition = "no_unique_match"
        if len(candidates) == 1:
            candidate = candidates[0]
            if candidate["branches"]:
                disposition = "redistributed_review_partition_and_sta"
            elif candidate["direct_fanout"] == candidate["effective_fanout"] == 4:
                disposition = "unique_direct_match_review_sta"
            else:
                disposition = "changed_direct_fanout_review"
        return {"disposition": disposition, "candidates": candidates, "eco_authorized": False}

    def check_logic(self):
        for port in INPUT_PORTS:
            require(not self.drivers[port], f"Unexpected cell driving {port}")
            require(bool(self.loads[port]) == (port not in PORTS["S"][:8]),
                    f"Unexpected load on {port}")
        for port in OUTPUT_PORTS:
            nets = [net for net, names in self.exported.items() if port in names]
            require(len(nets) == 1, f"Unexpected continuous assignment on {port}")
            net = nets[0]
            name, out = self.driver(net)
            master = self.cells[name][0]
            source = self.upstream(net) if function(master) in BUFFERS else [function(master), out]
            if port in CONSTANT_PORTS:
                require(source[:2] == ["tiel", "ZN"], f"Expected constant zero at {port}")
            else:
                require(source[0] not in {"tiel", "tieh"}, f"Unexpected constant on {port}")

    def clock_tree(self, root_master):
        roots = self.loads["clk"]
        require(len(roots) == 1 and roots[0][1] == "I" and not self.drivers["clk"],
                f"Unexpected clock root loads: {roots}")
        root = roots[0][0]
        require(self.cells[root][0] == root_master, f"Wrong CTS root master: {root}")
        pending = [(root, 1)]
        levels = []
        seen_buffers = set()
        seen_ffs = set()
        while pending:
            name, depth = pending.pop()
            require(name not in seen_buffers, f"Clock cycle or multiple paths to {name}")
            seen_buffers.add(name)
            master, pins = self.cells[name]
            require(function(master) in BUFFERS | {"clkinv", "inv"}, f"Invalid CTS cell {name}")
            out = next((pin for pin in ("Z", "ZN") if pins.get(pin)), None)
            if out is None:
                levels.append({"instance": name, "master": master, "depth": depth, "dummy": True})
                continue
            net = pins[out]
            require(self.driver(net) == (name, out), f"Wrong clock driver on {net}")
            require(self.loads[net], f"Clock net without loads: {net}")
            levels.append({"instance": name, "master": master, "depth": depth,
                           "net": net, "loads": self.loads[net]})
            for item, pin in self.loads[net]:
                if item in self.ffs:
                    require(pin == "CLK" and item not in seen_ffs, f"Invalid clock sink {item}/{pin}")
                    seen_ffs.add(item)
                else:
                    require(pin == "I", f"Invalid buffer clock pin {item}/{pin}")
                    pending.append((item, depth + 1))
        require(len(self.ffs) == 61 and seen_ffs == self.ffs, "CTS must reach all 61 FFs")
        return {"flip_flops": len(seen_ffs), "root": root, "levels": levels}
