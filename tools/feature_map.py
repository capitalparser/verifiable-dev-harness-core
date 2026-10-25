#!/usr/bin/env python3
"""Read-only navigation over the existing feature map; no source imports or execution."""
from __future__ import annotations

import argparse
import ast
import hashlib
import json
from pathlib import Path, PurePosixPath
import re
import sys

import verify


class NavigationError(ValueError):
    """An adopted navigation reference is unsafe, absent or inconsistent."""


def require(condition: bool, message: str) -> None:
    if not condition:
        raise NavigationError(message)


def source_file(root: Path, name: str) -> Path:
    require(isinstance(name, str) and bool(name), "path_required")
    parts = PurePosixPath(name)
    require(not parts.is_absolute() and parts.as_posix() == name
            and not any(p in {".", ".."} for p in parts.parts)
            and not any(c in name for c in ("\\", ":", "\0")), "unsafe_reference_path")
    root = root.resolve(strict=True)
    path = root
    for part in parts.parts:
        path = path / part
        require(not path.is_symlink(), "symlink_reference_forbidden")
    require(path.is_file() and path.resolve().is_relative_to(root), "missing_reference_file")
    return path


def resolve(root: Path, reference: str) -> dict:
    """Resolve Python qualified names, Markdown ID headings or ordinary files."""
    require(type(reference) is str and bool(reference.strip()), "reference_required")
    name, heading_sep, heading = reference.partition("#")
    name, symbol_sep, symbol = name.partition(":")
    require(not (heading_sep and symbol_sep), "mixed_reference_kind")
    path = source_file(root, name)
    raw = path.read_bytes()
    require(len(raw) <= 2 * 1024 * 1024, "reference_too_large")
    text = raw.decode("utf-8")
    line, end = 1, max(1, len(text.splitlines()))
    if symbol_sep:
        require(path.suffix == ".py" and all(p.isidentifier() for p in symbol.split(".")),
                "python_symbol_required")
        nodes = ast.parse(text, filename=name).body
        found = None
        for part in symbol.split("."):
            matches = [n for n in nodes if isinstance(n, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef))
                       and n.name == part]
            require(len(matches) == 1, "missing_or_ambiguous_symbol: " + reference)
            found = matches[0]
            nodes = found.body
        line, end = found.lineno, found.end_lineno
    elif heading_sep:
        require(path.suffix == ".md" and re.fullmatch(r"[A-Za-z][A-Za-z0-9_-]*", heading) is not None,
                "markdown_id_heading_required")
        matches, fence = [], None
        for number, value in enumerate(text.splitlines(), 1):
            marker = re.match(r"^\s{0,3}(`{3,}|~{3,})", value)
            if marker:
                token = marker.group(1)
                if fence is None:
                    fence = token
                elif token[0] == fence[0] and len(token) >= len(fence):
                    fence = None
                continue
            if fence is None and re.match(r"^#{1,6}\s+" + re.escape(heading) + r"(?=\s|:|$)", value):
                matches.append(number)
        require(len(matches) == 1, "missing_or_ambiguous_heading: " + reference)
        line = end = matches[0]
    return {"reference": reference, "path": name, "line": line, "end_line": end,
            "sha256": hashlib.sha256(raw).hexdigest(), "basis": "current_file_not_test_result"}


def unique_pairs(pairs: list) -> dict:
    result = {}
    for key, value in pairs:
        require(key not in result, "duplicate_json_key")
        result[key] = value
    return result


def load_map(path: Path) -> dict:
    require(path.stat().st_size <= 2 * 1024 * 1024, "manifest_too_large")
    def invalid_constant(value: str) -> None:
        raise NavigationError("nonfinite_json_number")
    data = json.loads(path.read_text(encoding="utf-8"), object_pairs_hook=unique_pairs,
                      parse_constant=invalid_constant)
    return verify.validate(data)


def build_index(data: dict, root: Path, feature_ids: tuple[str, ...] = ()) -> dict:
    verify.validate(data)
    require(not set(feature_ids) - {f["id"] for f in data["features"]}, "unknown_feature")
    common = {c for profile in data["profiles"].values() for c in profile}
    records, unmapped = [], []
    for feature in data["features"]:
        if feature_ids and feature["id"] not in feature_ids:
            continue
        nav = feature.get("navigation")
        if nav is None:
            require(not feature_ids, "requested_feature_navigation_unmapped")
            unmapped.append(feature["id"])
            continue
        require(type(nav) is dict and set(nav) == {"spec", "acceptance", "tests", "environment"},
                "invalid_navigation_fields")
        require(type(nav["acceptance"]) is list and bool(nav["acceptance"]), "acceptance_required")
        refs = [resolve(root, nav["spec"]), resolve(root, nav["environment"])]
        require("#" in nav["spec"], "spec_heading_required")
        refs += [resolve(root, ref) for ref in feature["entrypoints"]]
        refs += [resolve(root, ref) for ref in verify.strings(nav["tests"], "navigation.tests")]
        covered, seen = set(), set()
        for ac in nav["acceptance"]:
            require(type(ac) is dict and set(ac) in ({"ref", "checks"}, {"ref", "checks", "evidence"}), "invalid_acceptance_binding")
            require(type(ac["ref"]) is str and "#AC-" in ac["ref"], "acceptance_id_required")
            require(ac["ref"] not in seen, "duplicate_acceptance")
            seen.add(ac["ref"])
            checks = verify.strings(ac["checks"], "acceptance.checks")
            require(len(checks) == len(set(checks)) and set(checks) <= set(feature["checks"]) | common,
                    "invalid_acceptance_check")
            if "evidence" in ac:
                bindings = ac["evidence"]
                require(type(bindings) is list and bool(bindings), "evidence_bindings_required")
                for binding in bindings:
                    require(type(binding) is dict and isinstance(binding.get("kind"), str)
                            and bool(binding["kind"].strip()) and binding.get("check") in checks,
                            "invalid_evidence_check_binding")
            covered.update(checks)
            refs.append(resolve(root, ac["ref"]))
        require(set(feature["checks"]) <= covered, "feature_check_without_acceptance")
        records.append({"id": feature["id"], "symptoms": feature["symptoms"],
                        "entrypoints": feature["entrypoints"], "journey": feature["journey"],
                        "failure_probe": feature["failure_probe"], "navigation": nav,
                        "resolved": refs,
                        "checks": {c: data["checks"][c] for c in feature["checks"]}})
    return {"status": "MAP_VALID", "scope": "adopted_navigation_only", "features": records,
            "unmapped_features": unmapped, "execution_attested": False, "merge_authorized": False}


def lookup(index: dict, query: str) -> dict:
    tokens = set(re.findall(r"\w+", query.casefold()))
    require(bool(tokens), "nonempty_query_required")
    candidates = []
    for feature in index["features"]:
        haystack = " ".join([feature["id"], *feature["symptoms"], *feature["entrypoints"],
                             *feature["journey"]]).casefold()
        matched = sorted(t for t in tokens if t in haystack)
        if matched:
            candidates.append({"matched_terms": matched, **feature})
    candidates.sort(key=lambda f: (-len(f["matched_terms"]), f["id"]))
    return {"status": "CANDIDATES" if candidates else "UNRESOLVED", "query": query,
            "ambiguous": len(candidates) > 1, "candidates": candidates,
            "unmapped_features": index["unmapped_features"], "method": "lexical_not_diagnosis",
            "execution_attested": False, "merge_authorized": False}


def main(argv: list[str] | None = None) -> int:
    cli = argparse.ArgumentParser(description=__doc__)
    commands = cli.add_subparsers(dest="command", required=True)
    for name in ("lint", "lookup"):
        sub = commands.add_parser(name)
        sub.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
        sub.add_argument("--manifest", default="harness/verification.json")
        sub.add_argument("--feature", action="append", default=[])
        if name == "lookup":
            sub.add_argument("--query", required=True)
    args = cli.parse_args(argv)
    try:
        data = load_map(source_file(args.root, args.manifest))
        result = build_index(data, args.root, tuple(args.feature))
        if args.command == "lookup":
            result = lookup(result, args.query)
        print(json.dumps(result, ensure_ascii=True, sort_keys=True))
        return 1 if result["status"] == "UNRESOLVED" else 0
    except (OSError, ValueError, TypeError, KeyError, SyntaxError, RecursionError) as exc:
        reason = str(exc) if isinstance(exc, NavigationError) else type(exc).__name__
        print(json.dumps({"status": "BLOCKED", "reason": reason, "merge_authorized": False}))
        return 1


if __name__ == "__main__":
    sys.exit(main())
