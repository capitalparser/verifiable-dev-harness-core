# Feature: Runnable navigation for harness-verification

## BR-H002

A contributor receiving a verification report can find the relevant feature, read its intended behavior, resolve real source and test locations, and reproduce the native CLI journey in a disposable workspace using the existing runner.

## WF-H002

Symptom -> candidate feature IDs -> SPEC/AC and actual source -> mapped check IDs -> inspect the plan -> execute in an external evidence directory -> inspect the receipt and logs.

## Retained owners

- tools/verify.py owns manifest validation, risk-proportional selection, source snapshots, check execution, and receipt production.
- tools/feature_map.py owns static feature navigation and lexical query candidates.
- tools/harness_journey.py owns the disposable synthetic CLI journey.
- tools/verify_receipt.py owns read-only receipt consistency validation.
- tools/adopt.py owns pinned, copy-only installation of the three runtime modules.

The feature map in harness/verification.json is the edition's single check and navigation map. See [navigation and journey behavior](../../docs/FACTORY_READY.md).

## Input and output

Input is the existing harness/verification.json plus repository-relative file, Python symbol, and Markdown heading references and an optional symptom query. Output is read-only line/hash navigation and a structured report from the real native CLI journey. The normal runner can capture that report in its check log and receipt.

## Invariants and limits

Intended behavior stays in this SPEC and ACCEPTANCE; it is not generated from implementation. There is no second feature map, automatic rewrite of expectations, arbitrary command execution from lookup, or duplicate run of a check ID. Missing or unsafe mapped references block. Unmapped features are listed explicitly. Ambiguous or unmatched queries remain candidates or unresolved results; they are not diagnoses.

The journey creates and cleans up only its own temporary workspace, forwards an allowlisted environment, and calls the real tools/verify.py entrypoint with fixed synthetic inputs. This is scoped test isolation, not a security sandbox for trusted native commands. The runner's commands are trusted configuration. Receipt consistency and navigation do not attest product correctness or authorize a merge.
