"""Small deterministic contract tests; no model calls, network or product fixtures."""
from __future__ import annotations

import copy
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

SPEC = importlib.util.spec_from_file_location("verify", Path(__file__).parents[1] / "tools/verify.py")
verify = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(verify)


def manifest():
    def check():
        return {"argv": ["{python}", "-c", "print('ok')"], "timeout_seconds": 10}
    def feature(key):
        return {"id": key, "paths": [f"src/{key}/*"], "checks": [key],
                "symptoms": [f"{key} breaks"], "entrypoints": [f"src/{key}/app.py"],
                "journey": ["invoke public entrypoint and inspect output"],
                "failure_probe": "invalid input must not be accepted"}
    return {"schema_version": 1, "docs_paths": ["docs/*", "README.md"],
            "risk_paths": ["migrations/*", "harness/*"],
            "checks": {key: check() for key in ("lint", "a", "b", "integration")},
            "profiles": {"docs": ["lint"], "focused": ["lint"], "full": ["integration"]},
            "features": [feature("a"), feature("b")]}


class SelectionTests(unittest.TestCase):
    def test_docs_only_does_not_run_product_tests(self):
        plan = verify.select(manifest(), ["docs/readme.md"])
        self.assertEqual((plan["profile"], plan["checks"]), ("docs", ["lint"]))

    def test_one_feature_does_not_run_neighbor(self):
        plan = verify.select(manifest(), ["src/a/app.py", "README.md"])
        self.assertEqual(plan["checks"], ["lint", "a"])

    def test_multi_feature_deduplicates_checks(self):
        data = manifest()
        data["features"][1]["checks"] = ["a", "b", "lint"]
        self.assertEqual(verify.select(data, ["src/a/x", "src/b/y"])["checks"], ["lint", "a", "b"])

    def test_unknown_path_escalates_and_full_covers_all_features(self):
        plan = verify.select(manifest(), ["new/unmapped.py"])
        self.assertEqual(plan["profile"], "full")
        self.assertEqual(plan["checks"], ["lint", "integration", "a", "b"])
        self.assertEqual(plan["unmapped_paths"], ["new/unmapped.py"])

    def test_risk_cannot_be_downgraded(self):
        with self.assertRaisesRegex(ValueError, "cannot downgrade"):
            verify.select(manifest(), ["migrations/001.sql"], "focused")

    def test_explicit_feature_is_additive_not_a_scope_escape(self):
        plan = verify.select(manifest(), ["src/b/x"], feature_ids=("a",))
        self.assertEqual(plan["checks"], ["lint", "a", "b"])
        with self.assertRaisesRegex(ValueError, "unknown requested"):
            verify.select(manifest(), [], feature_ids=("missing",))

    def test_no_changes_are_not_reported_as_a_feature_verification(self):
        self.assertTrue(verify.select(manifest(), [])["no_changes"])
        self.assertFalse(verify.select(manifest(), [], feature_ids=("a",))["no_changes"])

    def test_malformed_manifest_is_rejected(self):
        for mutate in (
            lambda d: d["profiles"].update(full=[]),
            lambda d: d["profiles"].update(docs=["absent"]),
            lambda d: d["checks"]["a"].update(argv="echo passed"),
            lambda d: d["checks"]["a"].update(timeout_seconds=float("inf")),
            lambda d: d["features"].append(copy.deepcopy(d["features"][0])),
            lambda d: d["features"][0].update(failure_probe=""),
            lambda d: d.update(schema_version=True),
        ):
            with self.subTest(mutation=mutate):
                data = manifest()
                mutate(data)
                with self.assertRaises(ValueError):
                    verify.validate(data)


class RepositoryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve() / "repo"
        self.root.mkdir()
        self.out = self.root.parent / "evidence"
        self.git("init", "-q")
        self.git("config", "user.email", "test@example.invalid")
        self.git("config", "user.name", "Test")
        (self.root / "README.md").write_text("base\n", encoding="utf-8")
        self.git("add", ".")
        self.git("commit", "-qm", "base")
        self.base = self.git("rev-parse", "HEAD").strip()

    def git(self, *args):
        return subprocess.check_output(["git", "-C", str(self.root), *args], text=True)

    def run_checks(self, data=None):
        data = data or manifest()
        state = verify.snapshot(self.root, self.base)
        plan = verify.select(data, state["changed_paths"], feature_ids=("a",))
        return verify.execute(self.root, data, plan, state, self.out, "synthetic-v1")

    def test_rename_includes_old_and_new_paths(self):
        self.git("mv", "README.md", "README-new.md")
        self.git("commit", "-qm", "rename")
        self.assertEqual(verify.snapshot(self.root, self.base)["changed_paths"], ["README-new.md", "README.md"])

    def test_untracked_and_staged_cancelled_by_worktree_are_included(self):
        (self.root / "README.md").write_text("staged\n")
        self.git("add", "README.md")
        (self.root / "README.md").write_text("base\n")
        (self.root / "untracked.py").write_text("x = 1\n")
        state = verify.snapshot(self.root, self.base)
        self.assertEqual(state["changed_paths"], ["README.md", "untracked.py"])
        self.assertTrue(state["dirty"])

    def test_missing_base_is_blocked(self):
        with self.assertRaises(ValueError):
            verify.snapshot(self.root, "missing-base-ref")

    def test_receipt_binds_actual_commands_and_source(self):
        receipt = self.run_checks()
        self.assertEqual(receipt["status"], "PASS")
        self.assertEqual(receipt["state"]["head_sha"], self.base)
        self.assertFalse(receipt["state"]["dirty"])
        self.assertEqual(receipt["checks"][0]["argv"][0], sys.executable)
        self.assertEqual(json.loads((self.out / "receipt.json").read_text())["context"], "synthetic-v1")
        self.assertTrue((self.out / "lint.log").is_file())

    def test_failure_stops_without_retries_and_marks_not_run(self):
        data = manifest()
        data["checks"]["lint"]["argv"] = ["{python}", "-c", "raise SystemExit(7)"]
        receipt = self.run_checks(data)
        self.assertEqual(receipt["status"], "FAIL")
        self.assertEqual([c["status"] for c in receipt["checks"]], ["FAIL", "NOT_RUN"])
        self.assertEqual(receipt["checks"][0]["returncode"], 7)

    def test_missing_executable_is_error_not_pass(self):
        data = manifest()
        data["checks"]["lint"]["argv"] = [str(self.root / "missing-command")]
        receipt = self.run_checks(data)
        self.assertEqual(receipt["status"], "FAIL")
        self.assertEqual(receipt["checks"][0]["status"], "ERROR")

    def test_timeout_is_not_success(self):
        data = manifest()
        data["checks"]["lint"] = {"argv": ["{python}", "-c", "import time; time.sleep(5)"], "timeout_seconds": 0.1}
        receipt = self.run_checks(data)
        self.assertEqual(receipt["checks"][0]["status"], "TIMEOUT")
        self.assertEqual(receipt["status"], "FAIL")

    def test_mutation_during_verification_invalidates_receipt(self):
        data = manifest()
        data["checks"]["a"]["argv"] = ["{python}", "-c", "from pathlib import Path; Path('README.md').write_text('changed')"]
        self.assertEqual(self.run_checks(data)["status"], "STALE")

    def test_output_cannot_overwrite_or_pollute_checkout(self):
        state = verify.snapshot(self.root, self.base)
        data = manifest()
        plan = verify.select(data, [])
        for output in (self.root / "evidence", self.root):
            with self.assertRaises(ValueError):
                verify.execute(self.root, data, plan, state, output, "test")
        self.out.mkdir()
        (self.out / "keep.txt").write_text("keep")
        with self.assertRaises(ValueError):
            verify.execute(self.root, data, plan, state, self.out, "test")
        self.assertEqual((self.out / "keep.txt").read_text(), "keep")

    def test_plan_is_non_executing_and_cli_reports_blocked(self):
        data = manifest()
        data["checks"]["lint"]["argv"] = ["{python}", "-c", "raise SystemExit(99)"]
        config = self.root / "manifest.json"
        config.write_text(json.dumps(data), encoding="utf-8")
        command = [sys.executable, "-B", str(Path(verify.__file__)), "--root", str(self.root),
                   "--manifest", "manifest.json", "--base", self.base]
        result = subprocess.run(command + ["--plan"], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout)["status"], "PLANNED_NOT_RUN")
        self.assertFalse(self.out.exists())
        result = subprocess.run(command, capture_output=True, text=True)
        self.assertEqual(result.returncode, 2)
        self.assertEqual(json.loads(result.stderr)["status"], "BLOCKED")


if __name__ == "__main__":
    unittest.main()
