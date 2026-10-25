"""Focused navigation/adapter contracts. The real journey is one separate runner check."""
from __future__ import annotations

import ast
import copy
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
import feature_map as navigation
import harness_journey as journey
import verify
from test_verify import manifest


def mapped_fixture(root: Path) -> dict:
    (root / "source.py").write_text("raise RuntimeError('MUST NOT IMPORT')\n\ndef main():\n    pass\n", encoding="utf-8")
    (root / "test_source.py").write_text("class Tests:\n    def test_main(self):\n        pass\n", encoding="utf-8")
    (root / "spec.md").write_text("## BR-1\nHuman intent\n## AC-1: observable output\nGiven/When/Then\n", encoding="utf-8")
    data = manifest()
    data["features"] = data["features"][:1]
    feature = data["features"][0]
    feature.update(entrypoints=["source.py:main"], symptoms=["검증 누락", "too many tests"],
                   navigation={"spec": "spec.md#BR-1", "environment": "spec.md",
                               "acceptance": [{"ref": "spec.md#AC-1", "checks": ["a"]}],
                               "tests": ["test_source.py:Tests.test_main"]})
    (root / "map.json").write_text(json.dumps(data), encoding="utf-8")
    return data


class MapTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.data = mapped_fixture(self.root)

    def test_current_lines_hashes_and_no_import_or_rewrite(self):
        before = {p.name: p.read_bytes() for p in self.root.iterdir()}
        result = navigation.build_index(self.data, self.root)
        first = navigation.resolve(self.root, "source.py:main")
        self.assertEqual(first["line"], 3)
        self.assertFalse(result["merge_authorized"])
        self.assertEqual(before, {p.name: p.read_bytes() for p in self.root.iterdir()})
        (self.root / "source.py").write_text("\n\n" + before["source.py"].decode(), encoding="utf-8")
        after = navigation.resolve(self.root, "source.py:main")
        self.assertEqual(after["line"], 5)
        self.assertNotEqual(first["sha256"], after["sha256"])
        self.assertEqual((self.root / "spec.md").read_bytes(), before["spec.md"])

    def test_missing_symbol_blocks(self):
        self.data["features"][0]["entrypoints"] = ["source.py:renamed"]
        with self.assertRaisesRegex(navigation.NavigationError, "missing_or_ambiguous_symbol"):
            navigation.build_index(self.data, self.root)

    def test_missing_heading_and_fenced_example_do_not_count(self):
        (self.root / "spec.md").write_text("## BR-1\n```markdown\n## AC-1\n```\n", encoding="utf-8")
        with self.assertRaisesRegex(navigation.NavigationError, "missing_or_ambiguous_heading"):
            navigation.build_index(self.data, self.root)

    def test_duplicate_heading_blocks(self):
        with (self.root / "spec.md").open("a", encoding="utf-8") as stream:
            stream.write("## AC-1\n")
        with self.assertRaises(navigation.NavigationError):
            navigation.build_index(self.data, self.root)

    def test_invalid_ac_check_and_missing_coverage_block(self):
        for values in (["missing"], ["lint"], ["a", "a"]):
            with self.subTest(checks=values):
                data = copy.deepcopy(self.data)
                data["features"][0]["navigation"]["acceptance"][0]["checks"] = values
                with self.assertRaises(navigation.NavigationError):
                    navigation.build_index(data, self.root)

    def test_unsafe_and_missing_references(self):
        for ref in ("../outside.py", "/etc/passwd", "C:/secret", "source.py:main#AC-1", "source.py:missing",
                    "src//x.py", "missing.py", "src\\x.py"):
            with self.subTest(ref=ref), self.assertRaises(navigation.NavigationError):
                navigation.resolve(self.root, ref)

    def test_symlink_reference_is_not_followed(self):
        try:
            (self.root / "link.py").symlink_to(self.root / "source.py")
        except OSError:
            self.skipTest("OS does not allow unprivileged symlinks")
        with self.assertRaisesRegex(navigation.NavigationError, "symlink"):
            navigation.resolve(self.root, "link.py:main")

    def test_unmapped_is_explicit_not_false_completion(self):
        del self.data["features"][0]["navigation"]
        index = navigation.build_index(self.data, self.root)
        self.assertEqual(index["unmapped_features"], ["a"])
        self.assertEqual(index["features"], [])
        with self.assertRaisesRegex(navigation.NavigationError, "unmapped"):
            navigation.build_index(self.data, self.root, ("a",))

    def test_lookup_korean_no_match_and_ambiguity(self):
        index = navigation.build_index(self.data, self.root)
        self.assertEqual(navigation.lookup(index, "검증 누락")["candidates"][0]["id"], "a")
        self.assertEqual(navigation.lookup(index, "unrelated_999")["status"], "UNRESOLVED")
        second = copy.deepcopy(index["features"][0]); second["id"] = "b"
        index["features"].append(second)
        self.assertTrue(navigation.lookup(index, "검증")["ambiguous"])
        with self.assertRaises(navigation.NavigationError):
            navigation.lookup(index, "  ")

    def test_unknown_feature_and_malformed_navigation_block(self):
        with self.assertRaisesRegex(navigation.NavigationError, "unknown_feature"):
            navigation.build_index(self.data, self.root, ("unknown",))
        self.data["features"][0]["navigation"]["auto_rewrite_spec"] = True
        with self.assertRaises(navigation.NavigationError):
            navigation.build_index(self.data, self.root)

    def test_manifest_duplicate_keys_block(self):
        p = self.root / "map.json"; p.write_text('{"features":[],"features":[]}', encoding="utf-8")
        with self.assertRaisesRegex(navigation.NavigationError, "duplicate_json_key"):
            navigation.load_map(p)

    def test_public_lookup_cli(self):
        command = [sys.executable, "-B", str(ROOT / "tools/feature_map.py"), "lookup", "--root", str(self.root),
                   "--manifest", "map.json", "--query", "검증 누락"]
        result = subprocess.run(command, capture_output=True, text=True, encoding="utf-8", timeout=10)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout)["status"], "CANDIDATES")

    def test_shipped_manifest_and_check_deduplication(self):
        data = navigation.load_map(ROOT / "harness/verification.json")
        index = navigation.build_index(data, ROOT)
        self.assertEqual([f["id"] for f in index["features"]], ["harness-verification"])
        self.assertEqual(index["unmapped_features"], [])
        docs = verify.select(data, ["README.md"])
        self.assertEqual(docs["checks"], ["diff", "feature-map"])
        full = verify.select(data, ["tools/feature_map.py"])
        self.assertEqual(full["checks"].count("harness-journey"), 1)
        self.assertEqual(full["checks"].count("harness-contract"), 1)
        self.assertEqual(full["checks"].count("feature-map"), 1)


class GuardrailTests(unittest.TestCase):
    def test_navigation_has_no_execution_or_write_dependencies(self):
        tree = ast.parse((ROOT / "tools/feature_map.py").read_text(encoding="utf-8"))
        allowed = {"__future__", "argparse", "ast", "hashlib", "json", "pathlib", "re", "sys", "verify"}
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                self.assertLessEqual({a.name.split(".")[0] for a in node.names}, allowed)
            if isinstance(node, ast.ImportFrom):
                self.assertIn(node.module.split(".")[0], allowed)
            if isinstance(node, ast.Call):
                if isinstance(node.func, ast.Name):
                    self.assertNotIn(node.func.id, {"exec", "eval", "compile", "__import__", "open"})
                if isinstance(node.func, ast.Attribute):
                    self.assertNotIn(node.func.attr, {"execute", "run", "Popen", "write_text", "write_bytes", "unlink", "open"})

    def test_environment_does_not_forward_credentials_or_redirects(self):
        with patch.dict("os.environ", {"PATH": "/approved/bin", "SECRET": "secret", "DATABASE_URL": "prod",
                                      "GIT_DIR": "other", "PYTHONPATH": "injection"}, clear=True):
            env = journey.child_environment(Path("/owned/home"))
        self.assertFalse(set(env) & {"SECRET", "DATABASE_URL", "GIT_DIR", "PYTHONPATH"})
        self.assertEqual(env["HOME"], "/owned/home")

    def test_timeout_records_failure_and_cleans_only_owned_workspace(self):
        seen = []
        def timeout(argv, cwd, env, steps, deadline):
            seen.append(cwd.parent)
            raise subprocess.TimeoutExpired(argv, 0.1)
        with patch.object(journey, "invoke", side_effect=timeout):
            result = journey.run_pilot()
        self.assertEqual(result["status"], "JOURNEY_FAIL")
        self.assertEqual(result["cleanup"], "removed_owned_workspace")
        self.assertTrue(seen)
        self.assertTrue(all(not p.exists() for p in seen))
        self.assertTrue((ROOT / "tools/verify.py").exists())

    def test_invoke_records_timeout_without_retry(self):
        steps = []
        with patch("subprocess.run", side_effect=subprocess.TimeoutExpired(["fixed"], 1)) as process:
            with self.assertRaises(subprocess.TimeoutExpired):
                journey.invoke(["fixed"], ROOT, {}, steps, time.monotonic() + 2)
            self.assertEqual(process.call_count, 1)
        self.assertEqual(steps[0]["status"], "TIMEOUT")

    def test_planned_or_wrong_exit_is_not_a_successful_run(self):
        for result in (subprocess.CompletedProcess([], 0, '{"status":"PLANNED_NOT_RUN"}', ''),
                       subprocess.CompletedProcess([], 7, '{"status":"PASS"}', '')):
            with self.subTest(result=result), self.assertRaises(journey.JourneyError):
                journey.read_result(result, 0, "PASS")

    def test_pilot_rejects_arbitrary_cleanup_arguments(self):
        result = subprocess.run([sys.executable, "-B", str(ROOT / "tools/harness_journey.py"),
                                 "--cleanup", "/not-owned"], capture_output=True, text=True, timeout=10)
        self.assertEqual(result.returncode, 2)
        self.assertIn("unrecognized arguments", result.stderr)
        self.assertEqual(result.stdout, "")

    def test_missing_fixture_is_failure_not_skip(self):
        with tempfile.TemporaryDirectory() as path:
            result = journey.run_pilot(Path(path))
        self.assertEqual(result["status"], "JOURNEY_FAIL")
        self.assertEqual(result["cleanup"], "not_created")


if __name__ == "__main__":
    unittest.main()
