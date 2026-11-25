"""Stage the isolated design and preserve the provenance and state of each run."""

import argparse
from hashlib import sha256
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import subprocess
import tarfile


PACKAGE = Path(__file__).resolve().parent
MANIFEST = PACKAGE / "manifest.json"
MOUNT = PurePosixPath("/e01")
WORKFLOW = ".github/workflows/e01-compact.yaml"
FALLBACK = ".github/workflows/gds.yaml"


def read_json(path):
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path, value):
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def digest(path):
    with path.open("rb") as stream:
        hashed = sha256()
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            hashed.update(chunk)
    return hashed.hexdigest()


def require(condition, message):
    if not condition:
        raise ValueError(message)


def match(label, actual, expected):
    require(actual == expected, f"{label}: expected {expected!r}, got {actual!r}")


def git(*args):
    return subprocess.check_output(["git", *args], text=True).strip()


def verify_inputs(root, manifest):
    for path, expected in manifest["baseline_inputs"].items():
        match(path, digest(root / path), expected)


def stage(output, manifest):
    root = Path.cwd()
    commit = git("rev-parse", "HEAD")
    match("Actions commit", os.environ["GITHUB_SHA"], commit)
    match("direct parent", git("show", "-s", "--format=%P", "HEAD"), manifest["base_commit"])
    match("branch", os.environ["GITHUB_REF"], manifest["ref"])
    match("repository", os.environ["GITHUB_REPOSITORY"], manifest["repository"])
    match("event", os.environ["GITHUB_EVENT_NAME"], "workflow_dispatch")

    changes = git("diff", "--name-only", manifest["base_commit"], commit).splitlines()
    expected = set(manifest["publication_paths"])
    if FALLBACK in changes:
        match("branch fallback workflow", digest(root / FALLBACK), digest(root / WORKFLOW))
        expected.add(FALLBACK)
    match("published paths", sorted(changes), sorted(expected))
    match("tracked checkout clean", git("status", "--porcelain", "--untracked-files=no"), "")
    verify_inputs(root, manifest)

    output.mkdir(exist_ok=True)
    metadata = {
        "variant": manifest["variant"],
        "phase": "post_grt_checkpoint",
        "commit": commit,
        "base_commit": manifest["base_commit"],
        "ref": os.environ["GITHUB_REF"],
        "repository": os.environ["GITHUB_REPOSITORY"],
        "event": os.environ["GITHUB_EVENT_NAME"],
        "workflow_ref": os.environ["GITHUB_WORKFLOW_REF"],
        "run_id": os.environ["GITHUB_RUN_ID"],
        "attempt": os.environ["GITHUB_RUN_ATTEMPT"],
        "image": manifest["image"],
        "mounts": {"output": str(MOUNT), "ciel": "/e01-pdk", "scripts": "/e01-src"},
        "inputs": manifest["baseline_inputs"],
        "publication": {path: digest(root / path) for path in sorted(expected)},
        "tt_support_tools_reference": manifest["tt_support_tools"],
    }
    write_json(output / "provenance.json", metadata)
    shutil.copyfile(MANIFEST, output / "manifest.json")
    design = output / "design"
    (design / "rtl").mkdir(parents=True)
    for name in ("config.json", "pins.cfg"):
        shutil.copyfile(PACKAGE / name, design / name)
    for path in manifest["baseline_inputs"]:
        if path.startswith("src/") and path.endswith(".v"):
            shutil.copyfile(root / path, design / "rtl" / Path(path).name)


def verify_image(path, manifest):
    images = read_json(path)
    match("image count", len(images), 1)
    image = images[0]
    require(manifest["image"] in image["RepoDigests"], "Missing pinned RepoDigest")
    match("image OS and architecture", (image["Os"], image["Architecture"]), ("linux", "amd64"))


def verify_versions(path, manifest):
    versions = path.read_text(encoding="utf-8")
    for tool, expected in {
        "librelane": manifest["librelane"],
        "openroad": manifest["openroad_rcx"][:8],
        "opensta": manifest["opensta"],
        "open_pdks": manifest["pdk_revision"],
        "architecture": "x86_64",
    }.items():
        require(re.search(rf"^{tool}: [^\n]*{re.escape(expected)}", versions, re.M),
                f"{tool}: expected {expected} in {path}")


def artifact_files(root):
    for path in sorted(root.rglob("*")):
        require(not path.is_symlink(), f"Unexpected symlink in artifact: {path}")
        if path.is_file() and path != root / "SHA256SUMS":
            yield path


def hash_lines(root):
    return "".join(f"{digest(path)}  {path.relative_to(root).as_posix()}\n"
                   for path in artifact_files(root))


def verify_integrity(root):
    match("all payload paths and hashes", hash_lines(root),
          (root / "SHA256SUMS").read_text(encoding="utf-8"))


def unpack(source, target):
    """Check both artifact layers before reading or reusing any archived state."""
    tar_path = source / "e01-output.tar.gz"
    line = (source / "SHA256SUMS").read_text(encoding="utf-8")
    match("outer tar SHA-256", f"{digest(tar_path)}  {tar_path.name}\n", line)
    with tarfile.open(tar_path, "r:gz") as archive:
        members = archive.getmembers()
        names = set()
        for member in members:
            path = PurePosixPath(member.name)
            require(not path.is_absolute() and ".." not in path.parts,
                    f"Path traversal in artifact: {member.name}")
            require(member.isfile() or member.isdir(), f"Unexpected tar entry: {member.name}")
            require(path not in names, f"Duplicate tar entry: {member.name}")
            names.add(path)
        target.mkdir()
        archive.extractall(target, members=members)
    match("inner manifest copy", (target / "SHA256SUMS").read_bytes(),
          (source / "payload.sha256").read_bytes())
    verify_integrity(target)


def state_paths(value, payload, source_mount=MOUNT, destination_mount=None):
    """Validate every referenced view, including nested SDF/SPEF maps."""
    if isinstance(value, dict):
        return {key: state_paths(view, payload, source_mount, destination_mount)
                for key, view in value.items()}
    if value is None:
        return None
    require(isinstance(value, str), f"Unexpected state entry: {value!r}")
    source = PurePosixPath(value)
    require(source.is_absolute() and source_mount in source.parents
            and ".." not in source.parts, f"Path outside checkpoint mount: {value}")
    relative = source.relative_to(source_mount)
    path = payload / relative
    require(payload.resolve() in path.resolve().parents and path.is_file()
            and path.stat().st_size > 0, f"Missing or invalid view: {path}")
    return str(destination_mount / relative) if destination_mount else str(path)


def read_state(path, payload, source_mount=MOUNT, destination_mount=None):
    raw = read_json(path)
    metrics = raw.pop("metrics")
    views = state_paths(raw, payload, source_mount, destination_mount)
    return {**views, "metrics": metrics}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="action", required=True)
    for action in ("stage", "image", "versions", "seal", "integrity"):
        sub.add_parser(action).add_argument("path", type=Path)
    sub.add_parser("field").add_argument("key")
    unpack_parser = sub.add_parser("unpack")
    unpack_parser.add_argument("source", type=Path)
    unpack_parser.add_argument("target", type=Path)
    relocate = sub.add_parser("state")
    relocate.add_argument("payload", type=Path)
    relocate.add_argument("state", type=Path)
    relocate.add_argument("destination", type=Path)
    relocate.add_argument("--old-mount", type=PurePosixPath, default=MOUNT)
    relocate.add_argument("--new-mount", type=PurePosixPath, default=MOUNT)
    args = parser.parse_args()
    manifest = read_json(MANIFEST)
    if args.action == "field":
        print(manifest[args.key])
    elif args.action == "unpack":
        unpack(args.source, args.target)
    elif args.action == "state":
        verify_integrity(args.payload)
        require(args.payload.resolve() in args.state.resolve().parents,
                "state_out.json must be inside the verified payload")
        require(args.destination.resolve() != args.state.resolve(),
                "Write the relocated state to a different file")
        require(args.old_mount.is_absolute() and args.new_mount.is_absolute(),
                "Mounts must be absolute")
        state = read_state(args.state, args.payload, args.old_mount, args.new_mount)
        write_json(args.destination, state)
    elif args.action == "seal":
        (args.path / "SHA256SUMS").write_text(hash_lines(args.path), encoding="utf-8")
    elif args.action == "integrity":
        verify_integrity(args.path)
    elif args.action == "stage":
        stage(args.path, manifest)
    elif args.action == "image":
        verify_image(args.path, manifest)
    elif args.action == "versions":
        verify_versions(args.path, manifest)


if __name__ == "__main__":
    main()
