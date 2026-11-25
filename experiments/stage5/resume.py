"""Recover the authenticated E01 checkpoint and prepare an isolated LibreLane restart."""

import argparse
from fnmatch import fnmatchcase
from pathlib import Path
import stat
import subprocess
from zipfile import ZipFile

from prepare import MANIFEST, MOUNT, digest, match, read_json, read_state, require, unpack, write_json
from verify import PG_PORTS, Netlist, replay_checkpoint, sdc_digest, step


PLAN = Path(__file__).with_name("continuation.json")
OUTER_FILES = {"SHA256SUMS", "e01-output.tar.gz", "payload.sha256",
               "provenance.json", "checkpoint.json", "runner.log"}


def git(repo, *args):
    return subprocess.check_output(["git", "--no-optional-locks", "-C", str(repo), *args])


def extract_zip(source, destination, identity):
    match("GitHub archive size", source.stat().st_size, identity["artifact_size"])
    match("GitHub archive SHA-256", digest(source), identity["artifact_sha256"])
    with ZipFile(source) as archive:
        match("GitHub ZIP inventory", sorted(archive.namelist()), sorted(OUTER_FILES))
        for entry in archive.infolist():
            require(not entry.is_dir() and not stat.S_ISLNK(entry.external_attr >> 16),
                    f"Unexpected ZIP entry: {entry.filename}")
        match("GitHub ZIP CRC", archive.testzip(), None)
        destination.mkdir()
        archive.extractall(destination)
    match("original tar SHA-256", digest(destination / "e01-output.tar.gz"),
          identity["tar_sha256"])


def verify_actions(attempt, artifact_listing, identity):
    match("official run and attempt", (str(attempt["id"]), str(attempt["run_attempt"])),
          (identity["run_id"], identity["attempt"]))
    match("official commit, branch, event and path",
          (attempt["head_sha"], attempt["head_branch"], attempt["event"], attempt["path"]),
          (identity["commit"], identity["ref"].split("/", 2)[2],
           "workflow_dispatch", ".github/workflows/gds.yaml"))
    match("official repository", attempt["repository"]["full_name"], identity["repository"])
    artifacts = [item for item in artifact_listing["artifacts"] if item["id"] == identity["artifact_id"]]
    match("official artifact count", len(artifacts), 1)
    artifact = artifacts[0]
    match("official artifact name, size and digest",
          (artifact["name"], artifact["size_in_bytes"], artifact["digest"]),
          (identity["artifact_name"], identity["artifact_size"], "sha256:" + identity["artifact_sha256"]))
    run = artifact["workflow_run"]
    match("artifact run, branch and commit", (str(run["id"]), run["head_branch"], run["head_sha"]),
          (identity["run_id"], attempt["head_branch"], identity["commit"]))


def export_original(repo, destination, identity, manifest):
    commit = identity["commit"]
    match("original E01 parent", git(repo, "show", "-s", "--format=%P", commit).decode().strip(),
          identity["base_commit"])
    expected = sorted(manifest["publication_paths"] + [".github/workflows/gds.yaml"])
    match("original E01 publication paths",
          sorted(git(repo, "diff", "--name-only", identity["base_commit"], commit).decode().splitlines()),
          expected)
    for name in expected + list(manifest["baseline_inputs"]):
        content = git(repo, "show", f"{commit}:{name}")
        path = destination / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
    match("original Git manifest", digest(destination / MANIFEST.relative_to(MANIFEST.parents[2])),
          identity["manifest_sha256"])


def recover(source, work, repo, attempt_path, artifacts_path):
    plan = read_json(PLAN)
    identity = plan["checkpoint"]
    verify_actions(read_json(attempt_path), read_json(artifacts_path), identity)
    work.mkdir()
    outer = work / "outer"
    payload = work / "payload"
    extract_zip(source, outer, identity)
    unpack(outer, payload)
    for name in ("provenance.json", "checkpoint.json", "runner.log"):
        match("outer copy of " + name, (outer / name).read_bytes(), (payload / name).read_bytes())
    original = work / "original-git"
    manifest = read_json(MANIFEST)
    export_original(repo, original, identity, manifest)
    report = {"status": "partial_checkpoint_failed", "signoff": "pending",
              "flow_status": 0, "inspection_status": 0}
    replay_checkpoint(payload, original, identity, report)
    write_json(work / "checkpoint-replay.json", report)
    path = payload / identity["state_path"]
    match("Post-GRT route guide", digest(path.with_name("after_grt.guide")), identity["guide_sha256"])
    state = read_state(path, payload, destination_mount=MOUNT)
    match("restored initial state", state, read_json(path))
    write_json(work / "restored-state.json", state)
    print(report["status"])


def verify_checkout(repo, current, ref, repo_name, event, workflow):
    plan = read_json(PLAN)
    orig = plan["checkpoint"]
    settings = plan["continuation"]
    match("manual continuation origin", (ref, repo_name, event, workflow),
          (orig["ref"], orig["repository"], "workflow_dispatch", orig["workflow_ref"]))
    match("current checkout", git(repo, "rev-parse", "HEAD").decode().strip(), current)
    match("continuation direct parent", git(repo, "show", "-s", "--format=%P", current).decode().strip(),
          orig["commit"])
    paths = sorted(git(repo, "diff", "--name-only", orig["commit"], current).decode().splitlines())
    match("continuation publication paths", paths, sorted(settings["publication_paths"]))
    match("clean tracked checkout", git(repo, "status", "--porcelain", "--untracked-files=no"), b"")


def verify_bridge(payload, restored):
    plan = read_json(PLAN)
    identity, settings = plan["checkpoint"], plan["continuation"]
    run = payload / "design/runs" / settings["run_tag"]
    previous = read_json((payload / identity["state_path"]).parent.parent / "resolved.json")
    match("resolved continuation unchanged", read_json(run / "resolved.json"), previous)
    entry = step(run, "odb-diodesonports")
    first = sorted(path for path in run.iterdir() if path.is_dir() and path.name[:1].isdigit())[0]
    match("first executable step", first, entry)
    match("continuation state_in", read_json(entry / "state_in.json"), read_json(restored))
    require(not list(run.glob("*-odb-insertecobuffers")), "Unexpected explicit ECO step")


def sta_inputs(payload):
    plan = read_json(PLAN)
    manifest = read_json(MANIFEST)
    run = payload / "design/runs" / plan["continuation"]["run_tag"]
    resolved = read_json(run / "resolved.json")
    placement = step(run, "openroad-stapostpnr")
    state = read_state(placement / "state_in.json", payload)
    match("directed STA SDC", sdc_digest(Path(state["sdc"])), manifest["sdc_command_sha256"])
    netlist = Netlist(Path(state["nl"]))
    signature = netlist.find_eco(manifest["eco_reference_signature"])
    write_json(payload / "directed-signature.json", signature)
    require(len(signature["candidates"]) == 1, "Extracted netlist requires manual ECO signature review")
    candidate, = signature["candidates"]
    require(signature["disposition"] == "unique_direct_match_review_sta",
            "Routed load partition changed; inspect the extracted ECO signature")
    net = candidate["net"]
    driver = candidate["target"]
    instance = driver.rsplit("/", 1)[0]
    pins = netlist.cells[instance][1]
    input_nets = [pins[pin] for pin in ("A1", "A2")]
    input_pins = [f"{instance}/{pin}" for pin in ("A1", "A2")]
    loads = [f"{instance}/{pin}" for instance, pin in sorted(netlist.loads[net])]
    match("ECO branch fanout", len(loads), candidate["direct_fanout"])
    require(net not in PG_PORTS and loads, "Missing ECO data net/receptors")

    def mounted(path):
        return str(MOUNT / Path(path).relative_to(payload))

    for corner in resolved["STA_CORNERS"]:
        liberty, = [lib for pattern, files in resolved["LIB"].items()
                    if fnmatchcase(corner, pattern) for lib in files]
        spef, = [path for pattern, path in state["spef"].items() if fnmatchcase(corner, pattern)]
        print(corner, liberty, mounted(state["nl"]), mounted(state["sdc"]), mounted(spef),
              net, driver, " ".join(loads), " ".join(input_nets), " ".join(input_pins), sep="\t")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="action", required=True)
    recovery = sub.add_parser("recover")
    for arg in ("source", "work", "repo", "attempt_path", "artifacts_path"):
        recovery.add_argument(arg, type=Path)
    check = sub.add_parser("checkout")
    check.add_argument("repo", type=Path)
    for arg in ("current", "ref", "repo_name", "event", "workflow"):
        check.add_argument(arg)
    bridge = sub.add_parser("bridge")
    bridge.add_argument("payload", type=Path)
    bridge.add_argument("restored", type=Path)
    sub.add_parser("sta-inputs").add_argument("payload", type=Path)
    args = vars(parser.parse_args())
    action = args.pop("action")
    {"recover": recover, "checkout": verify_checkout, "bridge": verify_bridge,
     "sta-inputs": sta_inputs}[action](**args)


if __name__ == "__main__":
    main()
