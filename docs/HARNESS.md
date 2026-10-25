# Selection and execution

The runner reads the manifest at harness/verification.json and compares the current repository state with an explicitly supplied Git base. It chooses checks from changed paths, feature mappings, risk paths, and any explicitly requested feature.

## Selection rules

| Change | Minimum profile | Selection |
|---|---|---|
| Documentation-only change that maps to no feature | docs | diff and feature-map |
| Mapped feature change | focused | docs checks plus the mapped feature's checks |
| Risk path or unmapped non-documentation path | full | full profile plus every feature's checks |

The current manifest has one feature, harness-verification. Its feature checks are harness-contract, feature-map, and harness-journey. Check IDs are deduplicated, so a check that appears in a profile and feature is run once. A future full profile includes checks for every feature in the manifest.

A requested feature adds scope; it never removes checks required by changed paths. A requested profile below the computed minimum is rejected. For example, requesting focused for a risk path is blocked before checks run. The caller supplies the base; the runner resolves it locally and uses the merge base with HEAD for the comparison. There is no implicit origin/main default.

With no changed paths and no explicit feature, the plan is marked no_changes. The docs profile checks may still run, but that result is not feature verification. An explicit --feature harness-verification selects the feature checks even when the checkout has no changed paths.

## Plan, execution, and source state

Use --plan to inspect the selected profile, reasons, changed paths, and check IDs without executing checks. For execution, pass --output with a new or empty evidence directory outside the checkout. The runner records a snapshot of HEAD, base and merge-base commits, changed paths, staged and unstaged diffs, untracked file contents, and a workspace digest.

Each configured argv runs at most once. Commands are trusted manifest configuration and are launched directly without a shell, but they are not sandboxed. Each command has a timeout. A failed, timed-out, or unavailable command leaves later selected checks as NOT_RUN. The runner compares repository state again after execution; a changed source state makes the receipt STALE.

The receipt run status is PASS, FAIL, STALE, or NO_CHANGES. Check statuses include PASS, FAIL, TIMEOUT, ERROR, and NOT_RUN. Invalid input or a refused downgrade is reported as BLOCKED. These statuses describe execution and source state only; they do not make an approval decision.

## Evidence and authority

The evidence directory contains receipt.json and one log per executed check. Keep it outside the source checkout and use a fresh directory for each run. Commands, context, source snapshot, manifest digest, return codes, and log hashes and sizes are recorded for later consistency checks.

The receipt validator is read-only and does not rerun commands. A consistent receipt is not an execution attestation, proof of product correctness, or merge authorization. See [Receipt integrity](EVIDENCE_INTEGRITY.md) for its exact contract and validation command.
