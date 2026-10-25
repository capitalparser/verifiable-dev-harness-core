#!/usr/bin/env python3
"""One native-CLI acceptance check with disposable state, not a second runner."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import platform
import shutil
import subprocess
import sys
import tempfile
import time

from feature_map import source_file

ROOT = Path(__file__).resolve().parents[1]
FIXTURE = "harness/fixtures/native-journey.json"


class JourneyError(ValueError):
    """Observed public-CLI behavior does not satisfy the independent expectation."""


def expect(condition: bool, label: str) -> None:
    if not condition:
        raise JourneyError(label)


def child_environment(home: Path) -> dict[str, str]:
    allowed = {"PATH", "SYSTEMROOT", "WINDIR", "PATHEXT", "COMSPEC"}
    env = {k: v for k, v in os.environ.items() if k.upper() in allowed}
    env.update(HOME=str(home), USERPROFILE=str(home), XDG_CONFIG_HOME=str(home),
               APPDATA=str(home), LOCALAPPDATA=str(home), TMPDIR=str(home),
               TMP=str(home), TEMP=str(home), GIT_CONFIG_NOSYSTEM="1",
               GIT_CONFIG_GLOBAL=os.devnull, GIT_TERMINAL_PROMPT="0",
               PYTHONDONTWRITEBYTECODE="1", PYTHONIOENCODING="utf-8")
    return env


def invoke(argv: list[str], cwd: Path, env: dict[str, str], steps: list, deadline: float) -> subprocess.CompletedProcess:
    remaining = min(10, deadline - time.monotonic())
    expect(remaining > 0, "journey_budget_exhausted")
    record = {"argv": argv, "status": "RUNNING"}
    steps.append(record)
    tick = time.monotonic()
    try:
        result = subprocess.run(argv, cwd=cwd, env=env, stdin=subprocess.DEVNULL,
                                capture_output=True, text=True, encoding="utf-8", errors="replace",
                                timeout=remaining, check=False, shell=False)
        record.update(returncode=result.returncode, stdout=result.stdout, stderr=result.stderr,
                      status="OBSERVED")
        return result
    except subprocess.TimeoutExpired:
        record.update(status="TIMEOUT")
        raise
    except OSError:
        record.update(status="ERROR")
        raise
    finally:
        record["elapsed_seconds"] = round(time.monotonic() - tick, 6)


def read_result(result: subprocess.CompletedProcess, code: int, status: str) -> dict:
    expect(result.returncode == code, "unexpected_cli_exit")
    observed = json.loads(result.stdout if result.stdout.strip() else result.stderr)
    expect(type(observed) is dict and observed.get("status") == status, "unexpected_cli_status")
    return observed


def observe_receipt(output: Path) -> dict:
    receipt = json.loads((output / "receipt.json").read_text(encoding="utf-8"))
    # Fixed pilot check IDs: never follow arbitrary log paths from a candidate receipt.
    logs = {name: (output / f"{name}.log").read_text(encoding="utf-8")
            for name in ("smoke", "alpha", "beta") if (output / f"{name}.log").is_file()}
    return {"receipt": receipt, "logs": logs}


def run_pilot(source_root: Path = ROOT) -> dict:
    tick = time.monotonic()
    report = {"status": "JOURNEY_FAIL", "feature_id": "harness-verification",
              "scope": "real_native_cli_with_synthetic_inputs", "steps": [], "observations": {},
              "environment": {"python": sys.version, "platform": platform.platform()},
              "os_sandbox": False, "execution_attested": False, "merge_authorized": False}
    temp = None
    owned = None
    try:
        runner_path = source_file(source_root, "tools/verify.py")
        fixture_path = source_file(source_root, FIXTURE)
        runner_bytes, fixture_bytes = runner_path.read_bytes(), fixture_path.read_bytes()
        fixture = json.loads(fixture_bytes)
        expect(type(fixture) is dict and type(fixture.get("version")) is int
               and fixture["version"] == 1, "invalid_fixture")
        expect(set(fixture["files"]) == {"probe.py", "src/alpha.txt", "src/beta.txt"}, "unexpected_fixture_path")
        report["inputs"] = {"runner_sha256": hashlib.sha256(runner_bytes).hexdigest(),
                            "fixture_sha256": hashlib.sha256(fixture_bytes).hexdigest()}
        git = shutil.which("git")
        expect(git is not None, "git_unavailable")
        temp = tempfile.TemporaryDirectory(prefix="rdc-native-journey-")
        owned = Path(temp.name).resolve()
        repo, home, app = owned / "repo", owned / "home", owned / "app"
        for path in (repo, home, app, owned / "empty-template"):
            path.mkdir()
        (app / "verify.py").write_bytes(runner_bytes)
        for name in ("probe.py", "src/alpha.txt", "src/beta.txt"):
            path = repo / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(fixture["files"][name], encoding="utf-8")
        (repo / "manifest.json").write_text(json.dumps(fixture["manifest"]), encoding="utf-8")
        env = child_environment(home)
        deadline = tick + 45

        def call(argv: list[str]) -> subprocess.CompletedProcess:
            return invoke(argv, repo, env, report["steps"], deadline)

        for args in (["init", "-q", "--template", str(owned / "empty-template")], ["add", "."],
                     ["-c", "user.name=Harness Fixture", "-c", "user.email=fixture@example.invalid",
                      "-c", "commit.gpgsign=false", "-c", "core.hooksPath=" + str(owned / "empty-template"),
                      "commit", "-qm", "fixed synthetic input"]):
            expect(call([git, *args]).returncode == 0, "fixture_git_setup_failed")
        result = call([git, "rev-parse", "HEAD"])
        expect(result.returncode == 0, "fixture_head_unavailable")
        base = result.stdout.strip()
        command = [sys.executable, "-B", str(app / "verify.py"), "--root", str(repo),
                   "--manifest", "manifest.json", "--base", base, "--context", "native-cli-pilot-v1"]
        (repo / "src/alpha.txt").write_text("changed\n", encoding="utf-8")
        planned = read_result(call(command + ["--plan"]), 0, "PLANNED_NOT_RUN")
        expect(planned["plan"]["profile"] == "focused" and planned["plan"]["checks"] == ["smoke", "alpha"],
               "readiness_plan_not_focused")
        report["observations"]["readiness"] = planned

        output = owned / "positive"
        read_result(call(command + ["--output", str(output)]), 0, "PASS")
        positive = observe_receipt(output)
        checks = positive["receipt"]["checks"]
        expect([c["id"] for c in checks] == ["smoke", "alpha"]
               and all(c["status"] == "PASS" and type(c["returncode"]) is int and c["returncode"] == 0 for c in checks),
               "focused_execution_or_result_wrong")
        expect(positive["receipt"]["state"] == positive["receipt"]["state_after"], "source_changed_during_journey")
        expect(positive["logs"]["alpha"].strip() == "ALPHA_OK" and "beta" not in positive["logs"],
               "public_output_or_scope_wrong")
        report["observations"]["positive"] = positive

        (repo / "src/alpha.txt").write_text("reject\n", encoding="utf-8")
        output = owned / "failed-check"
        read_result(call(command + ["--output", str(output)]), 1, "FAIL")
        failed = observe_receipt(output)
        checks = failed["receipt"]["checks"]
        expect([c["id"] for c in checks] == ["smoke", "alpha"]
               and checks[1]["status"] == "FAIL" and checks[1]["returncode"] == 7,
               "failed_check_hidden_or_retried")
        expect(failed["logs"]["alpha"].strip() == "ALPHA_REJECTED", "missing_rejection_observation")
        report["observations"]["negative_input"] = failed

        (repo / "migrations").mkdir()
        (repo / "migrations/001.sql").write_text("-- synthetic risk marker\n", encoding="utf-8")
        output = owned / "must-not-exist"
        blocked = read_result(call(command + ["--profile", "focused", "--output", str(output)]), 2, "BLOCKED")
        expect(not output.exists(), "downgraded_run_executed")
        report["observations"]["negative_downgrade"] = blocked
        expect(runner_path.read_bytes() == runner_bytes and fixture_path.read_bytes() == fixture_bytes,
               "pilot_source_changed")
        report["status"] = "JOURNEY_PASS"
    except (OSError, ValueError, TypeError, KeyError, subprocess.SubprocessError) as exc:
        report.update(status="JOURNEY_FAIL", reason=str(exc) if isinstance(exc, JourneyError) else type(exc).__name__)
    finally:
        try:
            if temp is not None:
                temp.cleanup()
            report["cleanup"] = "removed_owned_workspace" if owned is not None and not owned.exists() else "not_created"
            if owned is not None and owned.exists():
                report.update(status="JOURNEY_FAIL", cleanup="failed")
        except OSError as exc:
            report.update(status="JOURNEY_FAIL", cleanup="failed", cleanup_error=type(exc).__name__)
        report["elapsed_seconds"] = round(time.monotonic() - tick, 6)
    return report


def main() -> int:
    # Deliberately no arbitrary source, command, credential or cleanup target arguments.
    argparse.ArgumentParser(description=__doc__).parse_args()
    report = run_pilot()
    print(json.dumps(report, ensure_ascii=True, sort_keys=True))
    return 0 if report["status"] == "JOURNEY_PASS" else 1


if __name__ == "__main__":
    sys.exit(main())
