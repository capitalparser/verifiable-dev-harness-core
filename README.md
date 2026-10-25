# Verifiable Dev Harness Core

Verifiable Dev Harness Core is a small, self-contained Python and Git toolkit for selecting risk-proportional checks, navigating feature references, running a fixed synthetic CLI journey, validating receipt consistency, and safely copying a pinned three-file core into a separate product repository.

| Tool | Purpose |
|---|---|
| [tools/verify.py](tools/verify.py) | Select checks, run trusted configured commands, and write a receipt. |
| [tools/feature_map.py](tools/feature_map.py) | Resolve feature references and return lexical lookup candidates without importing mapped code. |
| [tools/harness_journey.py](tools/harness_journey.py) | Exercise the native CLI with a fixed synthetic alpha/beta fixture in a disposable repository. |
| [tools/verify_receipt.py](tools/verify_receipt.py) | Recompute expected state and check receipt, command, and log consistency without rerunning checks. |
| [tools/adopt.py](tools/adopt.py) | Preview or copy three pinned runtime files into a new .vdh directory; it does not configure the product. |

## Requirements

Python 3.10 or newer, Git, and the Python standard library. The tools do not install packages or fetch an upstream repository.

## Core self-check

Run this example from a clean, committed core checkout. Capture BASE before work begins. The evidence directory is outside the checkout and must be new or empty. This explicitly selects the harness-verification feature.

~~~sh
BASE="$(git rev-parse HEAD)"
HEAD_SHA="$(git rev-parse HEAD)"
RUN_ID="core-self-check-001"
CONTEXT="core-self-check-v1"
EVIDENCE="../vdh-evidence/run-001"

python -B tools/feature_map.py lookup --query "selection receipt navigation" --feature harness-verification
python -B tools/verify.py --base "$BASE" --feature harness-verification --plan --run-id "$RUN_ID" --context "$CONTEXT"
python -B tools/verify.py --base "$BASE" --feature harness-verification --output "$EVIDENCE" --run-id "$RUN_ID" --context "$CONTEXT"
python -B tools/verify_receipt.py --base "$BASE" --feature harness-verification --receipt "$EVIDENCE/receipt.json" --expect-head "$HEAD_SHA" --expect-run-id "$RUN_ID" --context "$CONTEXT" --require-clean
~~~

HEAD_SHA is captured independently for the validator. Use the same run ID and context for the runner and validator. Do not assume origin/main exists in a fresh repository.

## Read next

- [Selection and execution](docs/HARNESS.md)
- [Navigation and synthetic journey](docs/FACTORY_READY.md)
- [Receipt integrity](docs/EVIDENCE_INTEGRITY.md)
- [Pinned adoption instructions](docs/SETUP.md)
- [Feature specification](specs/harness-verification/SPEC.md)
- [Acceptance expectations](specs/harness-verification/ACCEPTANCE.md)

The fixed native-CLI journey is a synthetic self-test of this toolkit. It does not verify an adopted product's behavior. Installation, a configured product map, executed product checks, and approval are separate states. Trusted check commands are not sandboxed; receipts are consistency evidence and do not authorize a merge or establish product correctness.

This edition was curated with static review only; unit tests and the synthetic journey were not run for this publication.

## Files

~~~text
.
├── .gitignore
├── README.md
├── docs/
│   ├── EVIDENCE_INTEGRITY.md
│   ├── FACTORY_READY.md
│   ├── HARNESS.md
│   └── SETUP.md
├── harness/
│   ├── examples/verification.project.json
│   ├── fixtures/native-journey.json
│   └── verification.json
├── specs/harness-verification/
│   ├── ACCEPTANCE.md
│   └── SPEC.md
├── tests/
│   ├── test_adopt.py
│   ├── test_factory_ready.py
│   ├── test_verify.py
│   └── test_verify_receipt.py
└── tools/
    ├── adopt.py
    ├── feature_map.py
    ├── harness_journey.py
    ├── verify.py
    └── verify_receipt.py
~~~

License has not yet been selected; this repository does not currently include a license grant.
