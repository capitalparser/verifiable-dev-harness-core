"""Bootstrap safety on disposable repositories; not a test of an adopted product."""
from __future__ import annotations

import hashlib
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("vdh_adopt", ROOT / "tools/adopt.py")
adopt = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(adopt)


def git(root, *args):
    result = subprocess.run(["git", "-C", str(root), *args], check=True, capture_output=True, timeout=10)
    return result.stdout.decode().strip()


def init(path):
    path.mkdir()
    git(path, "init", "-q")
    git(path, "config", "user.name", "Adoption Test")
    git(path, "config", "user.email", "adoption@example.invalid")


def commit(root):
    git(root, "add", ".")
    git(root, "commit", "-qm", "fixture")
    return git(root, "rev-parse", "HEAD")


class AdoptionTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory(prefix="vdh-adoption-")
        self.addCleanup(temp.cleanup)
        self.parent = Path(temp.name).resolve()
        self.source, self.target = self.parent / "upstream", self.parent / "product with spaces"
        init(self.source)
        init(self.target)
        (self.source / "tools").mkdir()
        for name in adopt.CORE:
            # These are intentionally inert fixtures, not fake verified runtime modules.
            (self.source / "tools" / name).write_text("raise RuntimeError('DO_NOT_EXECUTE_IMPORTED_CORE')\n", encoding="utf-8")
        self.revision = commit(self.source)
        (self.target / "README.md").write_text("Existing project\n", encoding="utf-8")
        self.head = commit(self.target)

    def install(self, **kwargs):
        return adopt.adopt(self.source, self.target, self.revision,
                           apply=True, expected_head=self.head, **kwargs)

    def test_plan_is_read_only_and_discloses_non_readiness(self):
        before = git(self.target, "status", "--porcelain")
        out = adopt.adopt(self.source, self.target, self.revision)
        self.assertEqual(out["status"], "PLANNED_NOT_APPLIED")
        self.assertFalse(out["product_configured"])
        self.assertFalse(out["merge_authorized"])
        self.assertFalse((self.target / ".vdh").exists())
        self.assertEqual(git(self.target, "status", "--porcelain"), before)

    def test_new_project_install_exact_pinned_bytes_and_provenance(self):
        self.assertEqual(adopt.CORE, ("verify.py", "verify_receipt.py", "feature_map.py"))
        out = self.install()
        self.assertEqual(out["status"], "FILES_INSTALLED_PRODUCT_NOT_CONFIGURED")
        self.assertEqual(set(p.name for p in (self.target / ".vdh").iterdir()),
                         {"verify.py", "verify_receipt.py", "feature_map.py", "UPSTREAM.json"})
        meta = json.loads((self.target / ".vdh/UPSTREAM.json").read_text())
        self.assertEqual(meta["source_commit"], self.revision)
        for name in adopt.CORE:
            raw = (self.target / ".vdh" / name).read_bytes()
            self.assertEqual(raw, (self.source / "tools" / name).read_bytes())
            self.assertEqual(meta["files"][name], hashlib.sha256(raw).hexdigest())
        self.assertEqual(git(self.target, "rev-parse", "HEAD"), self.head)
        self.assertFalse((self.target / "harness/verification.json").exists())

    def test_existing_project_keeps_agents_runner_manifest_and_config(self):
        records = {"AGENTS.md": "Existing authority\n", "verify.sh": "existing-runner\n",
                   "harness/verification.json": '{"existing": true}\n',
                   ".env": "SYNTHETIC_NOT_A_SECRET=fixture\n", "src/app.py": "x = 1\n"}
        for name, raw in records.items():
            path = self.target / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(raw, encoding="utf-8")
        self.head = commit(self.target)
        self.install()
        for name, raw in records.items():
            self.assertEqual((self.target / name).read_text(), raw)
        self.assertEqual(git(self.target, "diff", "--name-only", self.head), "")

    def test_existing_destination_refused_even_if_empty_or_identical(self):
        destination = self.target / ".vdh"
        destination.mkdir()
        with self.assertRaisesRegex(ValueError, "destination_exists"):
            self.install()
        destination.rmdir()
        self.install()
        with self.assertRaisesRegex(ValueError, "destination_exists"):
            self.install()

    def test_dirty_target_plan_allowed_but_apply_refused(self):
        for staged in (False, True):
            with self.subTest(staged=staged):
                (self.target / "README.md").write_text("unsaved user work\n")
                if staged:
                    git(self.target, "add", "README.md")
                plan = adopt.adopt(self.source, self.target, self.revision)
                self.assertTrue(plan["target_dirty_before"])
                with self.assertRaisesRegex(ValueError, "dirty_target"):
                    self.install()
                self.assertEqual((self.target / "README.md").read_text(), "unsaved user work\n")
                self.assertFalse((self.target / ".vdh").exists())

    def test_untracked_work_is_not_deleted_or_stashed(self):
        path = self.target / "notes.txt"
        path.write_text("in flight")
        with self.assertRaisesRegex(ValueError, "dirty_target"):
            self.install()
        self.assertEqual(path.read_text(), "in flight")
        self.assertEqual(git(self.target, "stash", "list"), "")

    def test_apply_requires_reviewed_current_head(self):
        for head in (None, "0" * 40):
            with self.subTest(head=head), self.assertRaisesRegex(ValueError, "target_head"):
                adopt.adopt(self.source, self.target, self.revision, apply=True, expected_head=head)
        self.assertFalse((self.target / ".vdh").exists())

    def test_source_uses_pinned_commit_not_dirty_or_newer_worktree(self):
        (self.source / "tools/verify.py").write_text("newer committed content\n")
        commit(self.source)
        (self.source / "tools/verify.py").write_text("dirty source content\n")
        self.install()
        self.assertIn("DO_NOT_EXECUTE", (self.target / ".vdh/verify.py").read_text())

    def test_missing_core_file_fails_before_writing(self):
        (self.source / "tools/verify_receipt.py").unlink()
        self.revision = commit(self.source)
        with self.assertRaisesRegex(ValueError, "missing_or_nonregular_core_file"):
            self.install()
        self.assertFalse((self.target / ".vdh").exists())

    def test_unpinned_refs_are_rejected(self):
        for revision in ("HEAD", "main", self.revision[:8], "--help"):
            with self.subTest(revision=revision), self.assertRaisesRegex(ValueError, "full_pinned_commit"):
                adopt.adopt(self.source, self.target, revision)

    def test_symlink_destination_does_not_write_outside(self):
        external = self.parent / "outside"
        external.mkdir()
        try:
            (self.target / ".vdh").symlink_to(external, target_is_directory=True)
        except OSError:
            self.skipTest("symlink unavailable")
        with self.assertRaisesRegex(ValueError, "destination_exists"):
            self.install()
        self.assertEqual(list(external.iterdir()), [])

    def test_symlink_source_blob_is_rejected(self):
        path = self.source / "tools/verify.py"
        path.unlink()
        try:
            path.symlink_to("verify_receipt.py")
        except OSError:
            self.skipTest("symlink unavailable")
        self.revision = commit(self.source)
        with self.assertRaisesRegex(ValueError, "missing_or_nonregular_core_file"):
            self.install()
        self.assertFalse((self.target / ".vdh").exists())

    def test_ignored_destination_is_not_silently_unversioned(self):
        (self.target / ".gitignore").write_text(".vdh/\n")
        self.head = commit(self.target)
        with self.assertRaisesRegex(ValueError, "destination_ignored"):
            self.install()
        self.assertFalse((self.target / ".vdh").exists())

    def test_initial_commit_and_root_required(self):
        empty = self.parent / "empty"
        init(empty)
        with self.assertRaisesRegex(ValueError, "git_failed"):
            adopt.adopt(self.source, empty, self.revision)
        child = self.target / "subdir"
        child.mkdir()
        with self.assertRaisesRegex(ValueError, "repository_root"):
            adopt.adopt(self.source, child, self.revision)

    def test_cannot_install_into_source_itself(self):
        with self.assertRaisesRegex(ValueError, "separate_repositories"):
            adopt.adopt(self.source, self.source, self.revision)

    def test_owned_clean_git_worktree_is_supported(self):
        worktree = self.parent / "clean worktree"
        git(self.target, "worktree", "add", "--detach", str(worktree), self.head)
        try:
            out = adopt.adopt(self.source, worktree, self.revision, apply=True, expected_head=self.head)
            self.assertEqual(out["status"], "FILES_INSTALLED_PRODUCT_NOT_CONFIGURED")
            self.assertFalse((self.target / ".vdh").exists())
        finally:
            git(self.target, "worktree", "remove", "--force", str(worktree))

    def test_moved_target_head_is_detected(self):
        (self.target / "README.md").write_text("new commit")
        commit(self.target)
        with self.assertRaisesRegex(ValueError, "target_head"):
            self.install()

    def test_race_after_plan_blocks_without_creating_destination(self):
        real = adopt.target_state
        calls = []
        def state(root):
            calls.append(root)
            return real(root) if len(calls) == 1 else (self.head, True)
        with patch.object(adopt, "target_state", side_effect=state):
            with self.assertRaisesRegex(ValueError, "target_changed_after_plan"):
                self.install()
        self.assertFalse((self.target / ".vdh").exists())

    def test_copy_error_removes_only_owned_files(self):
        real_open = Path.open
        destination = self.target / ".vdh"
        def fail(path, *args, **kwargs):
            if path == destination / "feature_map.py":
                raise OSError("synthetic disk failure")
            return real_open(path, *args, **kwargs)
        with patch.object(Path, "open", fail):
            with self.assertRaisesRegex(ValueError, "owned_cleanup=complete"):
                self.install()
        self.assertFalse(destination.exists())
        self.assertEqual((self.target / "README.md").read_text(), "Existing project\n")

    def test_copy_error_preserves_unowned_intervening_file(self):
        real_open = Path.open
        destination = self.target / ".vdh"
        def fail(path, *args, **kwargs):
            if path == destination / "feature_map.py":
                with real_open(destination / "someone-else.txt", "w") as stream:
                    stream.write("preserve")
                raise OSError("synthetic concurrent writer")
            return real_open(path, *args, **kwargs)
        with patch.object(Path, "open", fail):
            with self.assertRaisesRegex(ValueError, "owned_cleanup=incomplete"):
                self.install()
        self.assertEqual((destination / "someone-else.txt").read_text(), "preserve")
        self.assertEqual(sorted(p.name for p in destination.iterdir()), ["someone-else.txt"])

    def test_public_cli_plan_and_apply(self):
        argv = [sys.executable, "-B", str(ROOT / "tools/adopt.py"), "--source", str(self.source),
                "--target", str(self.target), "--revision", self.revision]
        plan = subprocess.run(argv, capture_output=True, text=True, timeout=20)
        self.assertEqual(plan.returncode, 0, plan.stdout + plan.stderr)
        self.assertEqual(json.loads(plan.stdout)["status"], "PLANNED_NOT_APPLIED")
        applied = subprocess.run(argv + ["--apply", "--expect-target-head", self.head],
                                 capture_output=True, text=True, timeout=20)
        self.assertEqual(applied.returncode, 0, applied.stdout + applied.stderr)
        self.assertEqual(json.loads(applied.stdout)["status"], "FILES_INSTALLED_PRODUCT_NOT_CONFIGURED")

    def test_starter_manifest_fails_until_product_is_configured(self):
        data = json.loads((ROOT / "harness/examples/verification.project.json").read_text())
        self.assertEqual(data["features"], [])
        for profile in ("docs", "focused", "full"):
            self.assertIn("adoption-pending", data["profiles"][profile])
        argv = [arg.replace("{python}", sys.executable) for arg in data["checks"]["adoption-pending"]["argv"]]
        result = subprocess.run(argv, capture_output=True, text=True, timeout=10)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("PRODUCT_NOT_CONFIGURED", result.stderr)
        self.assertIn(".vdh/*", data["risk_paths"])


if __name__ == "__main__":
    unittest.main()
