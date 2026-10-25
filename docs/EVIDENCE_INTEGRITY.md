# Receipt integrity

The runner's receipt is a record of a selected run and the source state observed around it. The validator checks consistency against state and expectations supplied independently by the caller. It is read-only and never executes recorded argv.

## What the receipt binds

Schema fields schema_version and evidence_contract_version are both version 1. The receipt records:

- run ID, context, start and finish times, and Python/platform details;
- HEAD, comparison base, merge base, changed paths, dirty state, and a workspace digest covering staged and unstaged changes and untracked file bytes;
- the selected plan and a hash of the canonical manifest;
- each check ID, resolved argv, status, return code, elapsed time, and log filename;
- each log's byte length and SHA-256 digest; an empty log is valid when its recorded zero-byte length and digest match.

The runner writes logs to the external evidence directory. It runs each selected command at most once. A failed command leaves later checks NOT_RUN. A source-state change during or after execution changes the receipt status to STALE.

## Validate with independent expectations

Choose the base before work starts. Capture the current product HEAD independently, and keep the run ID and non-secret context selected for the run. Use the same base, feature, run ID, and context for both commands. The evidence directory must be outside the checkout.

~~~sh
BASE="$(git rev-parse HEAD)"
HEAD_SHA="$(git rev-parse HEAD)"
RUN_ID="core-self-check-001"
CONTEXT="core-self-check-v1"
EVIDENCE="../vdh-evidence/run-001"

python -B tools/verify.py --base "$BASE" --feature harness-verification --output "$EVIDENCE" --run-id "$RUN_ID" --context "$CONTEXT"
python -B tools/verify_receipt.py --base "$BASE" --feature harness-verification --receipt "$EVIDENCE/receipt.json" --expect-head "$HEAD_SHA" --expect-run-id "$RUN_ID" --context "$CONTEXT" --require-clean
~~~

The example assumes a clean checkout. --require-clean requires the independently recomputed workspace state to have dirty=false; it does not mean that checks are approved or that the product is correct. Omit that flag only when you intentionally validate a matching dirty workspace snapshot. If source changes between execution and validation, the state comparison fails.

The validator checks receipt and evidence versions, expected head and run ID, context, current source state, selected plan, manifest hash, authority marker, successful statuses, argv, and every log's path, size, and digest. It does not trust a PASS label by itself. Stale receipts, failed or NOT_RUN checks, missing or tampered logs, changed manifests, mismatched command arguments, and changed source state are rejected.

A valid result is VERIFIED_EVIDENCE or NO_CHANGES_EVIDENCE. The response indicates whether the evidence is bound to a commit or a workspace and explicitly reports execution_attested=false and merge_authorized=false. Receipt consistency is not proof that a check was trustworthy, that the product behaves correctly, or that a change is approved.
