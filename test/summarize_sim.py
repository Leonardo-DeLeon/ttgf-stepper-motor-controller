#!/usr/bin/env python3
"""Require an explicit result and the expected coverage for every bench."""
import re
import sys
from pathlib import Path

EXPECTED = {
    "tb_clock_enable": 20, "tb_step_counter": 10, "tb_stepper_fsm": 5,
    "tb_stepper_controller": 155, "tb_stepper_demo": 5, "tb_tiny_tapeout": 3,
}
COVERAGE = {
    "tb_clock_enable": {"timer": 20},
    "tb_step_counter": {"counter": 8},
    "tb_stepper_fsm": {"phase": 4},
    "tb_stepper_controller": {"matrix": 128, "stop": 24},
    "tb_stepper_demo": {"demo": 5},
    "tb_tiny_tapeout": {},
}


def main():
    folder = Path(sys.argv[1])
    benches = sys.argv[2:] or list(EXPECTED)
    total_pass = total_fail = missing = incomplete = 0
    lines = []
    for bench in benches:
        log = folder / (bench + ".log")
        contents = log.read_text(errors="replace") if log.is_file() else ""
        matches = re.findall(rf"\bRESULT {bench} passed=(\d+) failed=(\d+)\b", contents)
        if len(matches) != 1:
            lines.append(f"{bench}: sin un resultado único, ejecución no acreditada")
            missing += 1
            continue
        passed, failed = map(int, matches[0])
        total_pass += passed
        total_fail += failed
        coverage = ", ".join(
            f"{kind} {actual}/{expected}"
            for kind, expected in COVERAGE[bench].items()
            for actual in [len(set(re.findall(rf"\bCOVER {kind} ([^\r\n]+)", contents)))]
        )
        lines.append(f"{bench}: {passed} aprobadas, {failed} fallidas"
                     + (f" · {coverage}" if coverage else ""))
        if passed != EXPECTED[bench] or failed:
            incomplete += 1
        elif any(len(set(re.findall(rf"\bCOVER {kind} ([^\r\n]+)", contents))) != expected
                 for kind, expected in COVERAGE[bench].items()):
            incomplete += 1
    lines.append(f"Total: {total_pass} aprobadas, {total_fail} fallidas, "
                 f"{missing} bancos sin resultado, {incomplete} bancos incompletos.")
    summary = "\n".join(lines) + "\n"
    (folder / "summary.txt").write_text(summary)
    print(summary, end="")
    return bool(missing or incomplete)


if __name__ == "__main__":
    sys.exit(main())
