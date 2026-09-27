#!/usr/bin/env python3
"""Attest AM2R build artifacts and assemble a matched runtime set.

`begin` must run immediately before a build and `seal` immediately afterward.
An existing output that was not rewritten cannot receive a new attestation.
The manifest deliberately contains hashes and source identities, never game data.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
COMPONENTS = ("fpga", "frontend", "runner")
SKIP_DIRS = {".git", "CMakeFiles", "__pycache__", "build", "db", "incremental_db", "output_files"}
SOURCE_ROOTS = {
    "fpga": ("rtl", "sys"),
    "frontend": ("src/hps-wrapper", "third_party/Main_MiSTer-upstream"),
    "runner": ("third_party/Butterscotch",),
}
SOURCE_FILES = {
    "fpga": ("AM2R.qpf", "AM2R.qsf", "AM2R.sdc", "AM2R.sv", "files.qip"),
    "frontend": ("scripts/build-hps-wrapper.ps1", "scripts/zig-cc-arm-linux.cmd", "scripts/zig-cxx-arm-linux.cmd", "scripts/zig-ar.cmd", "scripts/zig-ranlib.cmd"),
    "runner": ("scripts/build-butterscotch-mister.ps1", "scripts/zig-cc-arm-linux.cmd", "scripts/zig-ar.cmd", "scripts/zig-ranlib.cmd"),
}
INSTALL_PATHS = {
    "fpga": "/media/fat/_Other/AM2R.rbf",
    "frontend": "/media/fat/MiSTer_AM2R",
    "runner": "/media/fat/games/am2r/bin/butterscotch",
}


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def source_paths(component: str) -> list[Path]:
    paths = [ROOT / name for name in SOURCE_FILES[component]]
    for name in SOURCE_ROOTS[component]:
        base = ROOT / name
        if not base.is_dir():
            raise ValueError(f"Missing source directory: {base}")
        for directory, child_dirs, files in os.walk(base):
            child_dirs[:] = sorted(item for item in child_dirs if item not in SKIP_DIRS)
            paths.extend(Path(directory) / item for item in files)
    for path in paths:
        if not path.is_file() or path.is_symlink():
            raise ValueError(f"Missing or linked source file: {path}")
    return sorted(set(paths), key=lambda path: path.relative_to(ROOT).as_posix())


def source_snapshot(component: str) -> dict[str, object]:
    digest = hashlib.sha256()
    files = source_paths(component)
    for path in files:
        relative = path.relative_to(ROOT).as_posix().encode("utf-8")
        digest.update(len(relative).to_bytes(4, "big"))
        digest.update(relative)
        digest.update(bytes.fromhex(sha256(path)))
    return {"sha256": digest.hexdigest(), "file_count": len(files)}


def artifact_snapshot(path: Path) -> dict[str, object] | None:
    if not path.is_file():
        return None
    stat = path.stat()
    return {"sha256": sha256(path), "bytes": stat.st_size, "mtime_ns": stat.st_mtime_ns}


def git_head() -> str | None:
    result = subprocess.run(
        ["git", "-C", str(ROOT), "rev-parse", "HEAD"],
        capture_output=True, text=True, check=False,
    )
    return result.stdout.strip() if result.returncode == 0 else None


def write_json(path: Path, value: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".new")
    temporary.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    temporary.replace(path)


def read_json(path: Path) -> dict[str, object]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict) or value.get("schema") != 1:
        raise ValueError(f"Invalid provenance schema: {path}")
    return value


def verify_sidecar(path: Path, artifact: Path, check_source: bool = False) -> dict[str, object]:
    record = read_json(path)
    component = record.get("component")
    if record.get("kind") != "am2r-artifact" or component not in COMPONENTS:
        raise ValueError(f"Not an AM2R artifact sidecar: {path}")
    actual = artifact_snapshot(artifact)
    if actual is None or actual["sha256"] != record.get("artifact_sha256") or actual["bytes"] != record.get("artifact_bytes"):
        raise ValueError(f"Artifact differs from {path}: {artifact}")
    if check_source and source_snapshot(component) != record.get("source"):
        raise ValueError(f"{component} source changed since {path} was sealed")
    return record


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="action", required=True)
    begin = sub.add_parser("begin")
    begin.add_argument("component", choices=COMPONENTS)
    begin.add_argument("artifact", type=Path)
    begin.add_argument("token", type=Path)
    seal = sub.add_parser("seal")
    seal.add_argument("token", type=Path)
    seal.add_argument("artifact", type=Path)
    seal.add_argument("sidecar", type=Path)
    verify = sub.add_parser("verify")
    verify.add_argument("sidecar", type=Path)
    verify.add_argument("artifact", type=Path)
    verify.add_argument("--check-source", action="store_true")
    bundle = sub.add_parser("bundle")
    bundle.add_argument("output", type=Path)
    for component in COMPONENTS:
        bundle.add_argument(f"--{component}", type=Path, required=True)
    bundle.add_argument("--rbf-install-path", choices=("/media/fat/_Other/AM2R.rbf", "/media/fat/_Dev/AM2R.rbf"), default=INSTALL_PATHS["fpga"])
    observe = sub.add_parser("observe", help="Record existing files without claiming build provenance")
    observe.add_argument("output", type=Path)
    for component in COMPONENTS:
        observe.add_argument(f"--{component}", type=Path, required=True)
    observe.add_argument("--rbf-install-path", choices=("/media/fat/_Other/AM2R.rbf", "/media/fat/_Dev/AM2R.rbf"), default=INSTALL_PATHS["fpga"])
    device = sub.add_parser("verify-device", help="Compare a build set with sha256sum output on stdin")
    device.add_argument("manifest", type=Path)
    args = parser.parse_args()

    if args.action == "begin":
        write_json(args.token, {
            "schema": 1, "kind": "am2r-build-token", "component": args.component,
            "source": source_snapshot(args.component),
            "before": artifact_snapshot(args.artifact),
            "artifact_path": str(args.artifact.resolve()),
        })
        print(f"Recorded {args.component} pre-build source snapshot")
    elif args.action == "seal":
        token = read_json(args.token)
        component = token.get("component")
        if token.get("kind") != "am2r-build-token" or component not in COMPONENTS:
            raise ValueError("Invalid build token")
        if str(args.artifact.resolve()) != token.get("artifact_path"):
            raise ValueError("Artifact path differs from build token")
        source = source_snapshot(component)
        if source != token.get("source"):
            raise ValueError(f"{component} source changed during build")
        after = artifact_snapshot(args.artifact)
        if after is None:
            raise ValueError(f"{component} build output is missing: {args.artifact}")
        if after == token.get("before"):
            if args.sidecar.is_file():
                prior = verify_sidecar(args.sidecar, args.artifact, check_source=True)
                if prior["component"] == component:
                    print(f"Reused previously attested {component}: {after['sha256']}")
                    return 0
            raise ValueError(f"{component} output was not rewritten; refusing to attest a possibly stale artifact")
        record = {
            "schema": 1, "kind": "am2r-artifact", "component": component,
            "source": source, "git_head": git_head(),
            "artifact_sha256": after["sha256"], "artifact_bytes": after["bytes"],
        }
        write_json(args.sidecar, record)
        print(f"Sealed {component}: {after['sha256']}")
    elif args.action == "verify":
        record = verify_sidecar(args.sidecar, args.artifact, args.check_source)
        print(f"Verified {record['component']}: {record['artifact_sha256']}")
    elif args.action in ("bundle", "observe"):
        components: dict[str, object] = {}
        for component in COMPONENTS:
            artifact = getattr(args, component)
            actual = artifact_snapshot(artifact)
            if actual is None:
                raise ValueError(f"Missing {component} artifact: {artifact}")
            components[component] = {
                "sha256": actual["sha256"],
                "bytes": actual["bytes"],
                "install_path": args.rbf_install_path if component == "fpga" else INSTALL_PATHS[component],
            }
            if args.action == "bundle":
                sidecar = artifact.with_name(artifact.name + ".provenance.json")
                record = verify_sidecar(sidecar, artifact, check_source=True)
                if record["component"] != component:
                    raise ValueError(f"Wrong sidecar component for {artifact}")
                components[component]["source_sha256"] = record["source"]["sha256"]
                components[component]["git_head"] = record["git_head"]
        write_json(args.output, {
            "schema": 1, "kind": "am2r-build-set", "attested": args.action == "bundle",
            "components": components,
        })
        print(f"Wrote {'attested' if args.action == 'bundle' else 'observed'} build set: {args.output}")
    elif args.action == "verify-device":
        manifest = read_json(args.manifest)
        if manifest.get("kind") != "am2r-build-set":
            raise ValueError("Not an AM2R build-set manifest")
        received: dict[str, str] = {}
        for line in sys.stdin:
            match = re.fullmatch(r"([0-9a-fA-F]{64})\s+(/media/fat/\S+)", line.strip())
            if match:
                received[match.group(2)] = match.group(1).lower()
        for component in COMPONENTS:
            item = manifest["components"][component]
            path = item["install_path"]
            if received.get(path) != item["sha256"]:
                raise ValueError(f"{component} mismatch or missing on device: {path}")
            print(f"Verified device {component}: {item['sha256']}")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, ValueError, json.JSONDecodeError) as error:
        print(f"provenance: {error}", file=sys.stderr)
        raise SystemExit(1)
