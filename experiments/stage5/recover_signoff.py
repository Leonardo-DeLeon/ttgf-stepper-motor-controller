"""Authenticate, unpack and reconstruct evidence for E01 compact signoff."""

import argparse
from pathlib import Path, PurePosixPath
import shutil
import stat
import tarfile
from zipfile import ZipFile

from prepare import MANIFEST, digest, match, read_json, require, verify_inputs, verify_integrity, write_json
from resume import PLAN, git, verify_actions, verify_bridge
from verify import TOP, step

IDENTITY = Path(__file__).with_name("signoff-manifest.json")
CASES = {"finite_full_step", "disable_reset_and_rearm"}
BENCHES = {"clock_enable": 20, "step_counter": 10, "stepper_controller": 155,
           "stepper_demo": 5, "stepper_fsm": 5, "tiny_tapeout": 3}
TAR = "e01-continuation.tar.gz"
ORIGINAL_SIGNOFF = "01caba04a3f8a3cd5f849a6167224e7506b3ccd4"
FIX_PATHS = (".github/workflows/gds.yaml", "experiments/stage5/recover_signoff.py",
             "experiments/stage5/signoff.py")


def safe_path(value):
    path = PurePosixPath(value)
    require(not path.is_absolute() and ".." not in path.parts and "\\" not in value
            and ":" not in value and "\n" not in value and "\r" not in value, f"Unsafe path: {value}")
    return path


def extract_zip(source, target, identity, inventory):
    match("official ZIP bytes", source.stat().st_size, identity["artifact_size"])
    match("official ZIP SHA-256", digest(source), identity["artifact_sha256"])
    with ZipFile(source) as archive:
        match("ZIP inventory", sorted(archive.namelist()), sorted(inventory))
        for member in archive.infolist():
            safe_path(member.filename)
            require(stat.S_IFMT(member.external_attr >> 16) in (0, stat.S_IFREG)
                    and not member.is_dir(), f"Unexpected ZIP entry: {member.filename}")
        match("ZIP CRC", archive.testzip(), None)
        target.mkdir()
        archive.extractall(target)


def extract_payload(outer, target):
    tar_path = outer / TAR
    match("TAR hash", (outer / "SHA256SUMS").read_text(), f"{digest(tar_path)}  {TAR}\n")
    with tarfile.open(tar_path, "r:gz") as archive:
        members = archive.getmembers()
        paths = set()
        for member in members:
            path = safe_path(member.name)
            require(path not in paths and (member.isfile() or member.isdir())
                    and (path.parts or member.isdir()), f"Unexpected TAR entry: {member.name}")
            paths.add(path)
            # Use regular permissions regardless of the original runner's umask.
            member.mode = 0o755 if member.isdir() else 0o644
        target.mkdir()
        archive.extractall(target, members=members)
    match("TAR manifest copy", (target / "SHA256SUMS").read_bytes(), (outer / "payload.sha256").read_bytes())
    verify_integrity(target)


def verify_artifact(listing, identity):
    entries = [item for item in listing["artifacts"] if item["id"] == identity["artifact_id"]]
    match("official artifact count", len(entries), 1)
    item, = entries
    match("official name, bytes and digest", (item["name"], item["size_in_bytes"], item["digest"]),
          (identity["artifact_name"], identity["artifact_size"], "sha256:" + identity["artifact_sha256"]))
    run = item["workflow_run"]
    match("official run, branch and SHA", (str(run["id"]), run["head_branch"], run["head_sha"]),
          (identity["run_id"], identity["ref"].split("/", 2)[2], identity["commit"]))
    match("official repository ID", run["repository_id"], 1471159896)


def verify_job(listing, identity):
    jobs = [job for job in listing["jobs"] if job["id"] == identity["job_id"]]
    match("official job count", len(jobs), 1)
    job, = jobs
    match("official job run, status and conclusion",
          (str(job["run_id"]), job["status"], job["conclusion"]),
          (identity["run_id"], "completed", "success"))


def verify_execution(evidence, identity):
    plan = read_json(PLAN)
    execution = read_json(evidence / "continuation-provenance.json")
    for key in ("checkpoint", "continuation"):
        match(key, execution[key], plan[key])
    match("continuation execution", execution["execution"], {
        "GITHUB_SHA": identity["commit"], "GITHUB_REF": identity["ref"],
        "GITHUB_RUN_ID": identity["run_id"], "GITHUB_RUN_ATTEMPT": identity["attempt"],
        "GITHUB_WORKFLOW_REF": identity["workflow_ref"],
    })
    match("successful flow exit", (evidence / "flow-status.txt").read_text().strip(), "0")


def stage_rtl(source, target, identity):
    files = {"tb.fst", "results.xml", "output/summary.txt", "output/stepper_demo.vcd"}
    files.update(f"output/tb_{bench}.{suffix}" for bench in BENCHES for suffix in ("log", "compile.log"))
    extract_zip(source, target, identity, files)


def reconstruct(original, evidence, work):
    """Recreate /e01's archived paths in a separate working tree."""
    plan = read_json(PLAN)
    original = original.resolve()
    evidence = evidence.resolve()
    verify_integrity(original)
    verify_integrity(evidence)
    identity = read_json(IDENTITY)["continuation"]
    verify_execution(evidence, identity)
    verify_actions(read_json(evidence / "actions-original-attempt.json"),
                   read_json(evidence / "actions-original-artifacts.json"), plan["checkpoint"])
    shutil.copytree(original, work)
    tag = plan["continuation"]["run_tag"]
    shutil.copytree(evidence / tag, work / "design/runs" / tag)
    shutil.copytree(evidence / "final-native", work / "final-native")
    for filename in ("final-native.log", "restored-state.json", "checkpoint-replay.json",
                     "continuation-provenance.json", "directed-signature.json", "directed-sta-inputs.tsv",
                     "odb-reopened.guide", "after_grt.guide.nets", "odb-reopened.guide.nets",
                     "tool-versions.txt", "container-inspect.json", "flow-status.txt"):
        shutil.copyfile(evidence / filename, work / ("continuation-" + filename if
                        filename in {"tool-versions.txt", "container-inspect.json"} else filename))
    shutil.copytree(evidence / "directed-sta", work / "directed-sta")
    verify_bridge(work, work / "restored-state.json")
    checkpoint = plan["checkpoint"]
    original_state = work / checkpoint["state_path"]
    match("restored state vs original", read_json(work / "restored-state.json"), read_json(original_state))
    match("original state hash", digest(original_state), checkpoint["state_sha256"])
    match("original guide hash", digest(original_state.with_name("after_grt.guide")), checkpoint["guide_sha256"])
    verify_views(work / "design/runs" / tag / "final", identity["views"])


def verify_views(final, views):
    actual = {path.relative_to(final).as_posix(): digest(path)
              for path in sorted(final.rglob("*")) if path.is_file()}
    match("complete final view inventory and hashes", actual, views)


def verify_checkout(repo, manifest):
    identity = manifest["continuation"]
    from os import environ
    match("manual signoff origin",
          tuple(environ[key] for key in ("GITHUB_REF", "GITHUB_REPOSITORY", "GITHUB_EVENT_NAME", "GITHUB_WORKFLOW_REF")),
          (identity["ref"], identity["repository"], "workflow_dispatch", identity["workflow_ref"]))
    head = git(repo, "rev-parse", "HEAD").decode().strip()
    match("current checkout", head, environ["GITHUB_SHA"])
    match("direct original signoff parent",
          git(repo, "show", "-s", "--format=%P", ORIGINAL_SIGNOFF).decode().strip(), identity["commit"])
    match("original signoff publication paths",
          sorted(git(repo, "diff", "--name-only", identity["commit"], ORIGINAL_SIGNOFF).decode().splitlines()),
          sorted(manifest["publication_paths"]))
    match("direct corrective parent", git(repo, "show", "-s", "--format=%P", head).decode().strip(),
          ORIGINAL_SIGNOFF)
    match("corrective publication paths",
          sorted(git(repo, "diff", "--no-renames", "--name-status", ORIGINAL_SIGNOFF, head).decode().splitlines()),
          [f"M\t{path}" for path in FIX_PATHS])
    verify_inputs(repo, read_json(MANIFEST))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="action", required=True)
    for action, arguments in {
        "extract": ("zip", "outer", "payload", "attempt", "listing", "jobs"),
        "rtl": ("zip", "target", "listing", "jobs"),
        "reconstruct": ("original", "evidence", "work"),
        "checkout": ("repo",),
    }.items():
        command = sub.add_parser(action)
        for argument in arguments:
            command.add_argument(argument, type=Path)
    sub.add_parser("field").add_argument("key")
    args = parser.parse_args()
    manifest = read_json(IDENTITY)
    if args.action == "extract":
        identity = manifest["continuation"]
        attempt = read_json(args.attempt)
        verify_actions(attempt, read_json(args.listing), identity)
        match("continuation conclusion", attempt["conclusion"], "success")
        verify_job(read_json(args.jobs), identity)
        extract_zip(args.zip, args.outer, identity, {"SHA256SUMS", "payload.sha256", TAR})
        extract_payload(args.outer, args.payload)
        verify_execution(args.payload, identity)
    elif args.action == "rtl":
        identity = manifest["rtl"]
        verify_artifact(read_json(args.listing), identity)
        verify_job(read_json(args.jobs), identity)
        stage_rtl(args.zip, args.target, identity)
    elif args.action == "reconstruct":
        reconstruct(args.original, args.evidence, args.work)
    elif args.action == "checkout":
        verify_checkout(args.repo, manifest)
    else:
        from functools import reduce
        print(reduce(lambda data, key: data[key], args.key.split("."), manifest))


if __name__ == "__main__":
    main()
