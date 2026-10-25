#!/usr/bin/env python3
"""Risk-proportional local verification; stdlib only, Python 3.10+."""
from __future__ import annotations

import argparse
import fnmatch
import hashlib
import json
import math
import os
from pathlib import Path
import platform
import re
import subprocess
import sys
import time
import uuid
from datetime import datetime, timezone

LEVELS = ("docs", "focused", "full")


def strings(value: object, label: str, *, empty: bool = False) -> list[str]:
    if not isinstance(value, list) or (not value and not empty) or any(
        not isinstance(x, str) or not x.strip() for x in value
    ):
        raise ValueError(f"{label}: expected {'possibly empty ' if empty else ''}string list")
    return value


def validate(data: dict) -> dict:
    """Fail on malformed plans rather than accidentally passing zero tests."""
    if not isinstance(data, dict) or type(data.get("schema_version")) is not int or data.get("schema_version") != 1:
        raise ValueError("manifest schema_version must be 1")
    checks, profiles = data.get("checks"), data.get("profiles")
    if not isinstance(checks, dict) or not checks or not isinstance(profiles, dict):
        raise ValueError("checks and profiles must be nonempty objects")
    for key, check in checks.items():
        if not re.fullmatch(r"[A-Za-z0-9_-]+", key) or not isinstance(check, dict):
            raise ValueError("check IDs must be safe filenames and checks must be objects")
        strings(check.get("argv"), f"{key}.argv")
        limit = check.get("timeout_seconds")
        if isinstance(limit, bool) or not isinstance(limit, (int, float)) or not math.isfinite(limit) or limit <= 0:
            raise ValueError(f"{key}: finite positive timeout_seconds required")
    for level in LEVELS:
        ids = strings(profiles.get(level), f"profiles.{level}")
        if any(x not in checks for x in ids):
            raise ValueError(f"{level}: unknown check ID")
    for key in ("docs_paths", "risk_paths"):
        strings(data.get(key), key, empty=True)
    features = data.get("features")
    if not isinstance(features, list):
        raise ValueError("features must be a list")
    seen = set()
    for feature in features:
        if not isinstance(feature, dict):
            raise ValueError("feature must be an object")
        key = feature.get("id")
        if not isinstance(key, str) or not key.strip() or key in seen:
            raise ValueError("feature IDs must be unique nonempty strings")
        seen.add(key)
        for name in ("paths", "checks", "symptoms", "entrypoints", "journey"):
            strings(feature.get(name), f"{key}.{name}")
        if any(x not in checks for x in feature["checks"]):
            raise ValueError(f"{key}: unknown check ID")
        if not isinstance(feature.get("failure_probe"), str) or not feature["failure_probe"].strip():
            raise ValueError(f"{key}: failure_probe required")
    return data


def matches(path: str, patterns: list[str]) -> bool:
    # Repository-relative POSIX paths; fnmatch '*' also spans directory separators.
    return any(fnmatch.fnmatchcase(path, p) for p in patterns)


def select(data: dict, paths: list[str], requested: str = "auto", feature_ids: tuple[str, ...] = ()) -> dict:
    validate(data)
    if requested not in ("auto", *LEVELS):
        raise ValueError("unknown profile")
    known = {f["id"] for f in data["features"]}
    if set(feature_ids) - known:
        raise ValueError("unknown requested feature")
    features = [f for f in data["features"] if f["id"] in feature_ids or any(matches(p, f["paths"]) for p in paths)]
    risky = [p for p in paths if matches(p, data["risk_paths"])]
    unmapped = [p for p in paths if not matches(p, data["docs_paths"]) and not any(matches(p, f["paths"]) for f in features)]
    minimum = "full" if risky or unmapped else "focused" if features else "docs"
    if requested != "auto" and LEVELS.index(requested) < LEVELS.index(minimum):
        raise ValueError(f"cannot downgrade {minimum} to {requested}; review the mapping, not the evidence")
    level = minimum if requested == "auto" else requested
    # Full includes EVERY feature's checks, not just an ambiguously named 'full' command.
    included = data["features"] if level == "full" else features
    ids = list(data["profiles"]["docs"])
    if level != "docs":
        ids += data["profiles"]["focused"]
    if level == "full":
        ids += data["profiles"]["full"]
    ids += [c for f in included for c in f["checks"]]
    return {"profile": level, "minimum_profile": minimum, "changed_paths": sorted(set(paths)),
            "features": [f["id"] for f in features], "risk_paths": risky, "unmapped_paths": unmapped,
            "checks": list(dict.fromkeys(ids)), "feature_map": included,
            "no_changes": not paths and not feature_ids}


def git(root: Path, *args: str) -> bytes:
    result = subprocess.run(["git", "-C", str(root), *args], capture_output=True, timeout=30, check=False,
                            env={k: v for k, v in os.environ.items() if not k.startswith("GIT_")})
    if result.returncode:
        raise ValueError(result.stderr.decode("utf-8", "replace").strip() or "git failed")
    return result.stdout


def names(raw: bytes) -> list[str]:
    return [os.fsdecode(p) for p in raw.split(b"\0") if p]


def snapshot(root: Path, base: str) -> dict:
    head = git(root, "rev-parse", "--verify", "HEAD").decode().strip()
    base_sha = git(root, "rev-parse", "--verify", "--end-of-options", base + "^{commit}").decode().strip()
    ancestor = git(root, "merge-base", base_sha, head).decode().strip()
    paths = names(git(root, "diff", "--name-only", "--no-renames", "-z", ancestor, "--"))
    paths += names(git(root, "diff", "--cached", "--name-only", "--no-renames", "-z", "HEAD", "--"))
    staged = git(root, "diff", "--cached", "--binary", "--no-ext-diff", "--no-textconv", "HEAD", "--")
    unstaged = git(root, "diff", "--binary", "--no-ext-diff", "--no-textconv", "--")
    untracked = names(git(root, "ls-files", "--others", "--exclude-standard", "-z"))
    digest = hashlib.sha256(staged + b"\0" + unstaged)
    for name in sorted(untracked):
        path = root / name
        digest.update(os.fsencode(name) + b"\0")
        if path.is_symlink():
            digest.update(os.fsencode(os.readlink(path)))
        else:
            with path.open("rb") as stream:
                for block in iter(lambda: stream.read(1024 * 1024), b""):
                    digest.update(block)
        digest.update(b"\0")
    return {"head_sha": head, "base_sha": base_sha, "merge_base_sha": ancestor,
            "workspace_sha256": digest.hexdigest(), "dirty": bool(staged or unstaged or untracked),
            "changed_paths": sorted(set(paths + untracked))}


def log_fingerprint(path: Path) -> dict:
    """Bind the actual log bytes, including a legitimate silent successful check."""
    digest = hashlib.sha256()
    size = 0
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
            size += len(block)
    return {"log_sha256": digest.hexdigest(), "log_bytes": size}


def execute(root: Path, data: dict, plan: dict, state: dict, output: Path, context: str,
            run_id: str | None = None) -> dict:
    run_id = run_id if run_id is not None else uuid.uuid4().hex
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,95}", run_id):
        raise ValueError("run_id must be a safe nonempty identifier")
    output = output.resolve()
    if output == root or root in output.parents:
        raise ValueError("put evidence outside the checkout to avoid source/evidence collisions")
    if output.exists() and (not output.is_dir() or any(output.iterdir())):
        raise ValueError("output must be a new or empty directory")
    output.mkdir(parents=True, exist_ok=True)
    receipt = {"schema_version": 1, "evidence_contract_version": 1, "run_id": run_id, "started_at": datetime.now(timezone.utc).isoformat(),
               "state": state, "plan": plan, "context": context,
               "manifest_sha256": hashlib.sha256(json.dumps(data, sort_keys=True).encode()).hexdigest(),
               "environment": {"python": sys.version, "platform": platform.platform()},
               "status": "RUNNING", "checks": [], "authority": "evidence_only_no_merge_or_approval"}
    started = time.monotonic()
    failed = False
    for key in plan["checks"]:
        check = data["checks"][key]
        argv = [x.replace("{python}", sys.executable).replace("{base}", state["merge_base_sha"])
                .replace("{head}", state["head_sha"]).replace("{output}", str(output)) for x in check["argv"]]
        item = {"id": key, "argv": argv, "status": "NOT_RUN", "returncode": None, "elapsed_seconds": 0}
        receipt["checks"].append(item)
        if failed:
            continue
        tick = time.monotonic()
        item["log"] = f"{key}.log"
        with (output / item["log"]).open("wb") as log:
            try:
                result = subprocess.run(argv, cwd=root, stdout=log, stderr=subprocess.STDOUT,
                                        timeout=check["timeout_seconds"], check=False, shell=False,
                                        env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"})
                item.update(returncode=result.returncode, status="PASS" if result.returncode == 0 else "FAIL")
            except subprocess.TimeoutExpired:
                item["status"] = "TIMEOUT"
            except OSError as exc:
                item.update(status="ERROR", error=str(exc))
        item.update(log_fingerprint(output / item["log"]))
        item["elapsed_seconds"] = round(time.monotonic() - tick, 3)
        failed = item["status"] != "PASS"
    receipt["status"] = "FAIL" if failed else "NO_CHANGES" if plan["no_changes"] else "PASS"
    try:
        receipt["state_after"] = snapshot(root, state["base_sha"])
        if receipt["state_after"] != state:
            receipt["status"] = "STALE"
    except (OSError, ValueError, subprocess.TimeoutExpired) as exc:
        receipt.update(status="STALE", state_error=str(exc))
    receipt["elapsed_seconds"] = round(time.monotonic() - started, 3)
    receipt["finished_at"] = datetime.now(timezone.utc).isoformat()
    (output / "receipt.json").write_text(json.dumps(receipt, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return receipt


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", default=".")
    parser.add_argument("--manifest", default="harness/verification.json")
    parser.add_argument("--base", required=True, help="explicit comparison commit/ref; no network fetch")
    parser.add_argument("--profile", choices=("auto", *LEVELS), default="auto")
    parser.add_argument("--feature", action="append", default=[])
    parser.add_argument("--plan", action="store_true", help="select only; do not execute checks")
    parser.add_argument("--output", help="new/empty evidence directory outside the checkout")
    parser.add_argument("--context", default="unspecified", help="non-secret fixture/config/runtime identity")
    parser.add_argument("--run-id", help="coordinator-issued run ID; defaults to a fresh UUID")
    args = parser.parse_args()
    try:
        root = Path(os.fsdecode(git(Path(args.root).resolve(), "rev-parse", "--show-toplevel")).strip()).resolve()
        data = validate(json.loads((root / args.manifest).read_text(encoding="utf-8")))
        state = snapshot(root, args.base)
        plan = select(data, state["changed_paths"], args.profile, tuple(args.feature))
        if args.plan:
            print(json.dumps({"status": "PLANNED_NOT_RUN", "state": state, "plan": plan}, ensure_ascii=False, indent=2))
            return 0
        if not args.output:
            raise ValueError("--output required for execution")
        receipt = execute(root, data, plan, state, Path(args.output), args.context, args.run_id)
        print(json.dumps({"status": receipt["status"], "profile": plan["profile"],
                          "head_sha": state["head_sha"], "dirty": state["dirty"],
                          "run_id": receipt["run_id"], "receipt": str(Path(args.output).resolve() / "receipt.json")}, ensure_ascii=False))
        return 0 if receipt["status"] in ("PASS", "NO_CHANGES") else 1
    except (OSError, ValueError, subprocess.TimeoutExpired) as exc:
        print(json.dumps({"status": "BLOCKED", "error": str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
