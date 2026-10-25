#!/usr/bin/env python3
"""Copy a pinned verification core into a new .vdh directory; never configure a product.

Default: read-only plan. No network, package installation, model dispatch, Git writes,
project-file replacement, check execution, service installation or approval changes.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys

CORE = ("verify.py", "verify_receipt.py", "feature_map.py")
DESTINATION = ".vdh"


def require(condition: bool, reason: str) -> None:
    if not condition:
        raise ValueError(reason)


def git(root: Path, *args: str, allowed: tuple[int, ...] = (0,)) -> subprocess.CompletedProcess:
    env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    result = subprocess.run(["git", "-C", str(root), *args], capture_output=True,
                            check=False, timeout=30, env=env, shell=False)
    require(result.returncode in allowed, "git_failed: " + result.stderr.decode("utf-8", "replace").strip())
    return result


def repository(path: Path) -> Path:
    path = path.resolve(strict=True)
    top = Path(os.fsdecode(git(path, "rev-parse", "--show-toplevel").stdout).strip()).resolve()
    require(path == top, "use_repository_root_not_subdirectory")
    return top


def commit(root: Path, revision: str) -> str:
    require(re.fullmatch(r"[0-9a-f]{40}|[0-9a-f]{64}", revision) is not None,
            "full_pinned_commit_required")
    actual = git(root, "rev-parse", "--verify", "--end-of-options", revision + "^{commit}").stdout.decode().strip()
    require(actual == revision, "revision_mismatch")
    return actual


def target_state(root: Path) -> tuple[str, bool]:
    head = git(root, "rev-parse", "--verify", "HEAD").stdout.decode().strip()
    dirty = bool(git(root, "status", "--porcelain=v1", "-z", "--untracked-files=all",
                     "--ignore-submodules=none").stdout)
    return head, dirty


def prepare(source: Path, target: Path, revision: str) -> tuple[dict, dict[str, bytes]]:
    source, target = repository(source), repository(target)
    require(source != target, "source_and_product_must_be_separate_repositories")
    revision = commit(source, revision)
    head, dirty = target_state(target)
    destination = target / DESTINATION
    require(not destination.is_symlink() and not destination.exists(), "destination_exists_do_not_overwrite")
    contents = {}
    for name in CORE:
        path = "tools/" + name
        tree = git(source, "ls-tree", revision, "--", path).stdout
        require(tree.startswith((b"100644 blob ", b"100755 blob ")) and tree.count(b"\n") == 1,
                "missing_or_nonregular_core_file: " + path)
        contents[name] = git(source, "show", revision + ":" + path).stdout
    # Git objects, not source worktree bytes, are copied. This is pinning, not publisher authentication.
    metadata = {"schema_version": 1, "source_commit": revision,
                "files": {name: hashlib.sha256(raw).hexdigest() for name, raw in contents.items()},
                "scope": "verification_core_only_product_not_configured"}
    contents["UPSTREAM.json"] = (json.dumps(metadata, indent=2, sort_keys=True) + "\n").encode()
    ignored = git(target, "check-ignore", "--no-index", "--", *[
        DESTINATION + "/" + name for name in contents], allowed=(0, 1))
    require(ignored.returncode == 1, "destination_ignored_review_gitignore_before_installing")
    plan = {"status": "PLANNED_NOT_APPLIED", "source_commit": revision,
            "target": str(target), "target_head": head, "target_dirty_before": dirty,
            "destination": str(destination), "files": metadata["files"],
            "creates": [DESTINATION + "/" + name for name in contents],
            "product_configured": False, "merge_authorized": False}
    return plan, contents


def adopt(source: Path, target: Path, revision: str, *, apply: bool = False,
          expected_head: str | None = None) -> dict:
    plan, contents = prepare(source, target, revision)
    if not apply:
        return plan
    require(expected_head == plan["target_head"], "explicit_current_target_head_required")
    require(not plan["target_dirty_before"], "dirty_target_use_owned_clean_worktree")
    root = Path(plan["target"])
    require(target_state(root) == (expected_head, False), "target_changed_after_plan")
    destination = Path(plan["destination"])
    # Exclusive creation: no --force, upgrade or overwrite mode. The caller holds the workspace lease.
    destination.mkdir()
    created = []
    try:
        for name, raw in contents.items():
            path = destination / name
            with path.open("xb") as stream:
                created.append(path)
                stream.write(raw)
    except OSError as exc:
        cleanup_errors = []
        for path in reversed(created):
            try:
                path.unlink()
            except OSError as cleanup_error:
                cleanup_errors.append(str(cleanup_error))
        try:
            destination.rmdir()  # Never recursively delete files another process may have created.
        except OSError as cleanup_error:
            cleanup_errors.append(str(cleanup_error))
        raise ValueError("install_failed; owned_cleanup=" + ("incomplete" if cleanup_errors else "complete")) from exc
    return {**plan, "status": "FILES_INSTALLED_PRODUCT_NOT_CONFIGURED"}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, default=Path(__file__).resolve().parents[1],
                        help="local upstream repository; never downloaded by this command")
    parser.add_argument("--target", type=Path, required=True, help="product Git root with an initial commit")
    parser.add_argument("--revision", required=True, help="reviewed full upstream commit SHA, not a branch")
    parser.add_argument("--apply", action="store_true", help="create .vdh; otherwise only print a plan")
    parser.add_argument("--expect-target-head", help="full target SHA from the reviewed plan; required with --apply")
    args = parser.parse_args(argv)
    try:
        result = adopt(args.source, args.target, args.revision, apply=args.apply,
                       expected_head=args.expect_target_head)
        print(json.dumps(result, ensure_ascii=False, sort_keys=True))
        return 0
    except (OSError, ValueError, subprocess.SubprocessError) as exc:
        print(json.dumps({"status": "BLOCKED", "reason": str(exc),
                          "product_configured": False, "merge_authorized": False}, ensure_ascii=False))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
