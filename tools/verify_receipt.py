#!/usr/bin/env python3
"""Read-only integrity check for the existing risk-proportional runner's receipt.

No alternate feature registry, command runner, approval or execution attestation.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import math
import os
from pathlib import Path
import re
import subprocess
import sys

import verify


class EvidenceError(ValueError):
    """Fail-closed, machine-readable evidence inconsistency."""


def require(condition: bool, reason: str) -> None:
    if not condition:
        raise EvidenceError(reason)


def unique_pairs(pairs: list) -> dict:
    result = {}
    for key, value in pairs:
        require(key not in result, "duplicate_json_key")
        result[key] = value
    return result


def reject_constant(value: str) -> None:
    raise EvidenceError("nonfinite_json_number")


def load_json(path: Path) -> dict:
    require(not path.is_symlink() and path.is_file(), "regular_file_required")
    require(path.stat().st_size <= 2 * 1024 * 1024, "json_too_large")
    data = json.loads(path.read_text(encoding="utf-8"), object_pairs_hook=unique_pairs,
                      parse_constant=reject_constant)
    require(type(data) is dict, "json_object_required")
    return data


def canonical(value: object) -> str:
    # Also distinguishes JSON true from 1 and false from 0 in nested records.
    return json.dumps(value, sort_keys=True, allow_nan=False)


def moment(value: object) -> datetime:
    require(type(value) is str, "invalid_timestamp")
    result = datetime.fromisoformat(value.replace("Z", "+00:00"))
    require(result.utcoffset() is not None, "timezone_required")
    return result


def nonnegative(value: object) -> bool:
    if type(value) not in (int, float):
        return False
    try:
        return math.isfinite(value) and value >= 0
    except OverflowError:
        return False


def validate_receipt(receipt: dict, data: dict, plan: dict, state: dict, evidence_dir: Path,
                     *, expected_head: str, expected_run: str, expected_context: str,
                     require_clean: bool = False, now: datetime | None = None) -> dict:
    """Caller supplies independently recomputed state/plan and trusted expectations."""
    require(type(receipt) is dict, "receipt_object_required")
    require(type(receipt.get("schema_version")) is int and receipt["schema_version"] == 1,
            "unsupported_receipt_schema")
    require(type(receipt.get("evidence_contract_version")) is int
            and receipt["evidence_contract_version"] == 1, "integrity_metadata_missing_rerun")
    require(type(expected_head) is str and re.fullmatch(r"[0-9a-f]{40}|[0-9a-f]{64}", expected_head) is not None,
            "full_expected_head_required")
    require(type(expected_run) is str and re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,95}", expected_run) is not None,
            "invalid_expected_run")
    require(receipt.get("run_id") == expected_run, "run_mismatch")
    require(state.get("head_sha") == expected_head, "head_mismatch")
    require(canonical(receipt.get("state")) == canonical(state)
            and canonical(receipt.get("state_after")) == canonical(state), "source_state_mismatch")
    require(not require_clean or state.get("dirty") is False, "clean_commit_required")
    require(canonical(receipt.get("plan")) == canonical(plan), "selected_plan_mismatch")
    require(receipt.get("context") == expected_context, "context_mismatch")
    manifest_hash = hashlib.sha256(canonical(data).encode()).hexdigest()
    require(receipt.get("manifest_sha256") == manifest_hash, "manifest_mismatch")
    require(receipt.get("authority") == "evidence_only_no_merge_or_approval", "authority_mismatch")
    expected_status = "NO_CHANGES" if plan["no_changes"] else "PASS"
    require(receipt.get("status") == expected_status, "run_not_passed")
    start, finish = moment(receipt.get("started_at")), moment(receipt.get("finished_at"))
    require(start <= finish <= (now or datetime.now(timezone.utc)), "invalid_time_order_or_future")
    require(nonnegative(receipt.get("elapsed_seconds")), "invalid_elapsed_time")
    environment = receipt.get("environment")
    require(type(environment) is dict and all(type(environment.get(k)) is str and environment[k]
                                             for k in ("python", "platform")), "environment_missing")
    results = receipt.get("checks")
    require(type(results) is list and bool(results) and len(results) == len(plan["checks"]),
            "incomplete_check_coverage")
    evidence_dir = evidence_dir.resolve(strict=True)
    for key, item in zip(plan["checks"], results):
        require(type(item) is dict and item.get("id") == key, "check_identity_or_order_mismatch")
        require(item.get("status") == "PASS" and type(item.get("returncode")) is int
                and item["returncode"] == 0, "check_not_passed")
        expected_argv = [x.replace("{python}", sys.executable).replace("{base}", state["merge_base_sha"])
                         .replace("{head}", state["head_sha"]).replace("{output}", str(evidence_dir))
                         for x in data["checks"][key]["argv"]]
        require(item.get("argv") == expected_argv, "command_mismatch")
        require(nonnegative(item.get("elapsed_seconds")), "invalid_check_elapsed_time")
        require(item.get("log") == f"{key}.log", "unsafe_or_wrong_log_path")
        log = evidence_dir / item["log"]
        require(not log.is_symlink() and log.is_file(), "regular_log_required")
        actual = verify.log_fingerprint(log)
        require(type(item.get("log_bytes")) is int and item["log_bytes"] == actual["log_bytes"],
                "log_size_mismatch")
        require(item.get("log_sha256") == actual["log_sha256"], "log_digest_mismatch")
    return {"status": "NO_CHANGES_EVIDENCE" if plan["no_changes"] else "VERIFIED_EVIDENCE",
            "binding": "workspace" if state["dirty"] else "commit", "profile": plan["profile"],
            "head_sha": expected_head, "run_id": expected_run, "checks": plan["checks"],
            "execution_attested": False, "merge_authorized": False}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path.cwd())
    parser.add_argument("--manifest", default="harness/verification.json")
    parser.add_argument("--base", required=True)
    parser.add_argument("--profile", choices=("auto", *verify.LEVELS), default="auto")
    parser.add_argument("--feature", action="append", default=[])
    parser.add_argument("--receipt", type=Path, required=True)
    parser.add_argument("--expect-head", required=True)
    parser.add_argument("--expect-run-id", required=True)
    parser.add_argument("--context", required=True)
    parser.add_argument("--require-clean", action="store_true")
    args = parser.parse_args(argv)
    try:
        root = Path(os.fsdecode(verify.git(args.root.resolve(), "rev-parse", "--show-toplevel")).strip()).resolve()
        require(not args.receipt.is_symlink(), "receipt_symlink_forbidden")
        receipt_path = args.receipt.resolve(strict=True)
        require(root not in receipt_path.parents, "evidence_must_be_outside_checkout")
        data = verify.validate(load_json(root / args.manifest))
        state = verify.snapshot(root, args.base)
        plan = verify.select(data, state["changed_paths"], args.profile, tuple(args.feature))
        result = validate_receipt(load_json(receipt_path), data, plan, state, receipt_path.parent,
                                  expected_head=args.expect_head, expected_run=args.expect_run_id,
                                  expected_context=args.context, require_clean=args.require_clean)
        require(verify.snapshot(root, args.base) == state, "source_changed_during_validation")
        print(json.dumps(result, sort_keys=True))
        return 0
    except (OSError, ValueError, TypeError, KeyError, RecursionError, subprocess.SubprocessError) as exc:
        reason = str(exc) if isinstance(exc, EvidenceError) else type(exc).__name__
        print(json.dumps({"status": "BLOCKED", "reason": reason,
                          "execution_attested": False, "merge_authorized": False}, sort_keys=True))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
