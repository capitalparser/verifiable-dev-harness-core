"""Native runner -> receipt -> read-only validation, including failure injection."""
from __future__ import annotations

import copy
import hashlib
import json
import shutil
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
import verify
import verify_receipt as gate
from test_verify import manifest


class ReceiptTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        # Execute the native runner once, then mutate isolated copies of its fixture.
        cls.seed = tempfile.TemporaryDirectory()
        cls.addClassCleanup(cls.seed.cleanup)
        cls.seed_root = Path(cls.seed.name).resolve() / "repo"
        cls.seed_root.mkdir()
        cls.seed_output = cls.seed_root.parent / "evidence"
        cls.seed_data = manifest()
        (cls.seed_root / "README.md").write_text("base\n", encoding="utf-8")
        (cls.seed_root / "manifest.json").write_text(json.dumps(cls.seed_data), encoding="utf-8")
        for args in [("init", "-q"), ("config", "user.name", "Receipt Tests"),
                     ("config", "user.email", "test@example.invalid"),
                     ("add", "."), ("commit", "-qm", "fixture")]:
            subprocess.run(["git", "-C", str(cls.seed_root), *args], check=True, capture_output=True)
        cls.seed_head = verify.git(cls.seed_root, "rev-parse", "HEAD").decode().strip()
        cls.seed_state = verify.snapshot(cls.seed_root, cls.seed_head)
        cls.seed_plan = verify.select(cls.seed_data, cls.seed_state["changed_paths"], feature_ids=("a",))
        cls.seed_receipt = verify.execute(cls.seed_root, cls.seed_data, cls.seed_plan, cls.seed_state,
                                          cls.seed_output, "fixture-v1", "run-1")

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve() / "repo"
        self.output = self.root.parent / "evidence"
        shutil.copytree(self.seed_root, self.root)
        shutil.copytree(self.seed_output, self.output)
        self.data = copy.deepcopy(self.seed_data)
        self.head = self.seed_head
        self.state = copy.deepcopy(self.seed_state)
        self.plan = copy.deepcopy(self.seed_plan)
        self.receipt = copy.deepcopy(self.seed_receipt)

    def check(self, **kwargs):
        options = dict(expected_head=self.head, expected_run="run-1", expected_context="fixture-v1")
        options.update(kwargs)
        return gate.validate_receipt(self.receipt, self.data, self.plan, self.state, self.output, **options)

    def cli(self, *extra):
        command = [sys.executable, str(ROOT / "tools/verify_receipt.py"), "--root", str(self.root),
                   "--manifest", "manifest.json", "--base", self.head, "--feature", "a",
                   "--receipt", str(self.output / "receipt.json"), "--expect-head", self.head,
                   "--expect-run-id", "run-1", "--context", "fixture-v1", *extra]
        result = subprocess.run(command, capture_output=True, text=True, timeout=20)
        return result.returncode, json.loads(result.stdout)

    def test_native_receipt_passes_without_approval(self):
        result = self.check()
        self.assertEqual(result["status"], "VERIFIED_EVIDENCE")
        self.assertEqual(result["binding"], "commit")
        self.assertFalse(result["execution_attested"])
        self.assertFalse(result["merge_authorized"])

    def test_check_never_runs_manifest_commands(self):
        with patch("subprocess.run", side_effect=AssertionError("must not execute checks")):
            self.assertEqual(self.check()["status"], "VERIFIED_EVIDENCE")

    def test_cli_native_receipt(self):
        code, result = self.cli()
        self.assertEqual((code, result["status"]), (0, "VERIFIED_EVIDENCE"))

    def test_cli_stale_source(self):
        (self.root / "README.md").write_text("changed", encoding="utf-8")
        code, result = self.cli()
        self.assertEqual((code, result["reason"]), (1, "source_state_mismatch"))

    def test_cli_changed_manifest(self):
        self.data["checks"]["a"]["argv"] = ["{python}", "-c", "print('different')"]
        (self.root / "manifest.json").write_text(json.dumps(self.data), encoding="utf-8")
        code, result = self.cli()
        self.assertEqual((code, result["status"]), (1, "BLOCKED"))

    def test_cli_wrong_expected_run(self):
        code, result = self.cli("--expect-run-id", "other-run")
        self.assertEqual((code, result["reason"]), (1, "run_mismatch"))

    def test_modified_log(self):
        (self.output / "lint.log").write_bytes(b"forged\n")
        with self.assertRaises(gate.EvidenceError):
            self.check()

    def test_same_size_modified_log(self):
        path = self.output / "lint.log"
        path.write_bytes(b"x" * path.stat().st_size)
        with self.assertRaisesRegex(gate.EvidenceError, "log_digest_mismatch"):
            self.check()

    def test_missing_log(self):
        (self.output / "lint.log").unlink()
        with self.assertRaisesRegex(gate.EvidenceError, "regular_log_required"):
            self.check()

    def test_symlink_log(self):
        path = self.output / "lint.log"
        data = path.read_bytes()
        path.unlink()
        alternate = self.output / "other.log"
        alternate.write_bytes(data)
        try:
            path.symlink_to(alternate)
        except OSError:
            self.skipTest("OS does not permit unprivileged symlinks")
        with self.assertRaisesRegex(gate.EvidenceError, "regular_log_required"):
            self.check()

    def test_silent_success_log_is_legitimate(self):
        self.data["checks"]["lint"]["argv"] = ["{python}", "-c", "pass"]
        # Configuration is an explicit test input; no claim of a product/approved plan.
        self.plan = verify.select(self.data, [], feature_ids=("a",))
        self.output = self.root.parent / "silent-evidence"
        self.receipt = verify.execute(self.root, self.data, self.plan, self.state,
                                      self.output, "fixture-v1", "run-1")
        self.assertEqual(self.receipt["checks"][0]["log_bytes"], 0)
        self.assertEqual(self.receipt["checks"][0]["log_sha256"], hashlib.sha256(b"").hexdigest())
        self.assertEqual(self.check()["status"], "VERIFIED_EVIDENCE")

    def test_dirty_run_is_workspace_not_verified_commit(self):
        (self.root / "README.md").write_text("dirty", encoding="utf-8")
        self.state = verify.snapshot(self.root, self.head)
        self.plan = verify.select(self.data, self.state["changed_paths"], feature_ids=("a",))
        self.output = self.root.parent / "dirty-evidence"
        self.receipt = verify.execute(self.root, self.data, self.plan, self.state,
                                      self.output, "fixture-v1", "run-1")
        self.assertEqual(self.check()["binding"], "workspace")
        with self.assertRaisesRegex(gate.EvidenceError, "clean_commit_required"):
            self.check(require_clean=True)

    def test_no_changes_not_feature_success(self):
        self.plan = verify.select(self.data, [])
        self.output = self.root.parent / "no-change-evidence"
        self.receipt = verify.execute(self.root, self.data, self.plan, self.state,
                                      self.output, "fixture-v1", "run-1")
        self.assertEqual(self.check()["status"], "NO_CHANGES_EVIDENCE")

    def test_no_new_registry_or_profile_required(self):
        for requested in ("auto", "focused", "full"):
            plan = verify.select(self.data, ["src/a/app.py"], requested)
            self.assertEqual(len(plan["checks"]), len(set(plan["checks"])))
        self.assertEqual(verify.select(self.data, ["docs/x.md"])["checks"], ["lint"])

    def test_legacy_receipt_requires_rerun(self):
        del self.receipt["evidence_contract_version"]
        with self.assertRaisesRegex(gate.EvidenceError, "integrity_metadata_missing_rerun"):
            self.check()

    def test_duplicate_json_keys(self):
        path = self.output / "bad.json"
        path.write_text('{"status":"PASS","status":"PASS"}')
        with self.assertRaisesRegex(gate.EvidenceError, "duplicate_json_key"):
            gate.load_json(path)

    def test_boolean_nested_state_cannot_equal_integer(self):
        self.receipt["state"]["dirty"] = 0
        with self.assertRaisesRegex(gate.EvidenceError, "source_state_mismatch"):
            self.check()

    def test_cli_rejects_malformed_json(self):
        (self.output / "receipt.json").write_text("{")
        code, result = self.cli()
        self.assertEqual((code, result["status"]), (1, "BLOCKED"))

    def test_runner_cli_emits_run_id_and_hashes(self):
        out = self.root.parent / "cli-evidence"
        command = [sys.executable, str(ROOT / "tools/verify.py"), "--root", str(self.root),
                   "--manifest", "manifest.json", "--base", self.head, "--feature", "a",
                   "--output", str(out), "--run-id", "from-coordinator", "--context", "fixture-v1"]
        result = subprocess.run(command, capture_output=True, text=True, timeout=20)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout)["run_id"], "from-coordinator")
        receipt = gate.load_json(out / "receipt.json")
        self.assertEqual(receipt["evidence_contract_version"], 1)
        self.assertTrue(all("log_sha256" in x for x in receipt["checks"]))


def altered_receipt(path, value):
    def test(self):
        target = self.receipt
        for part in path[:-1]:
            target = target[part]
        target[path[-1]] = copy.deepcopy(value)
        with self.assertRaises((gate.EvidenceError, ValueError)):
            self.check()
    return test


CASES = {
    "wrong_run": (["run_id"], "old-run"),
    "unknown_version": (["evidence_contract_version"], 2),
    "boolean_version": (["schema_version"], True),
    "wrong_manifest": (["manifest_sha256"], "f" * 64),
    "wrong_context": (["context"], "other-fixture"),
    "changed_authority": (["authority"], "merge-approved"),
    "missing_results": (["checks"], []),
    "duplicate_result": (["checks", 1, "id"], "lint"),
    "not_run": (["checks", 0, "status"], "NOT_RUN"),
    "failed": (["checks", 0, "status"], "FAIL"),
    "timeout": (["checks", 0, "status"], "TIMEOUT"),
    "boolean_exit": (["checks", 0, "returncode"], False),
    "nonzero_exit": (["checks", 0, "returncode"], 7),
    "command_drift": (["checks", 0, "argv"], ["echo", "passed"]),
    "path_traversal": (["checks", 0, "log"], "../README.md"),
    "invalid_size": (["checks", 0, "log_bytes"], True),
    "invalid_hash": (["checks", 0, "log_sha256"], "no-hash"),
    "plan_downgrade": (["plan", "profile"], "docs"),
    "stale_status": (["status"], "STALE"),
    "future_timestamp": (["finished_at"], "2099-01-01T00:00:00Z"),
    "naive_timestamp": (["finished_at"], "2000-01-01T00:00:00"),
    "negative_elapsed": (["elapsed_seconds"], -1),
    "oversized_elapsed": (["elapsed_seconds"], 10 ** 1000),
    "missing_environment": (["environment"], {}),
}
for name, (path, value) in CASES.items():
    setattr(ReceiptTests, "test_reject_" + name, altered_receipt(path, value))


if __name__ == "__main__":
    unittest.main()
