# Adopt the pinned verification core

This guide installs three reviewed runtime files into a separate product repository. Adoption copies those files and writes generated UPSTREAM metadata only. It does not create a product manifest, configure checks, verify product behavior, or approve changes.

## Prerequisites and separate repositories

Use Python 3.10 or newer and Git. The upstream public-core repository and product must be separate Git repositories. The product must have an initial commit, and its working tree must be clean before installation. The upstream source is a separate, clean reference checkout that is already available beside the product at ../verifiable-dev-harness-core. The adopt command does not clone or fetch repositories.

There are two different revisions:

- VDH_SHA is the full public-core commit C that was statically reviewed for installation.
- PRODUCT_BASE is the product repository's current HEAD, captured before installation and product changes.

They belong to different repositories and are not interchangeable. The docs-only commit D contains this guide and pins C. Read D's setup instructions, but run adopt.py from a clean checkout of C. The UPSTREAM.json written to the product records C and hashes of the copied file bytes, never D or private-source provenance.

This guide is the documentation-only child D of reviewed core commit C. Both VDH_SHA assignments below pin C by its full immutable SHA. Run the adoption tool from a clean checkout of C; do not substitute D or a moving branch.

## Select the reviewed core checkout

Run from the product root. Ensure VDH_SOURCE points to a separate, owned reference checkout, not to the product. The selected checkout must be clean and at the reviewed C revision before its tools/adopt.py is run.

POSIX shell:

~~~sh
set -eu
VDH_SOURCE="../verifiable-dev-harness-core"
VDH_SHA="488ec5583862af253fc3a7a7350476789a32a3b1"

core_status="$(git -C "$VDH_SOURCE" status --porcelain)"
test -z "$core_status"
git -C "$VDH_SOURCE" switch --detach "$VDH_SHA"
core_head="$(git -C "$VDH_SOURCE" rev-parse HEAD)"
test "$core_head" = "$VDH_SHA"
core_status="$(git -C "$VDH_SOURCE" status --porcelain)"
test -z "$core_status"
~~~

PowerShell:

~~~powershell
$ErrorActionPreference = "Stop"
$VDH_SOURCE = "../verifiable-dev-harness-core"
$VDH_SHA = "488ec5583862af253fc3a7a7350476789a32a3b1"

$coreStatus = git -C $VDH_SOURCE status --porcelain
if ($LASTEXITCODE -ne 0 -or $coreStatus) { throw "The core reference checkout must be clean." }
git -C $VDH_SOURCE switch --detach $VDH_SHA
if ($LASTEXITCODE -ne 0) { throw "Could not select the reviewed core commit." }
$coreHead = (git -C $VDH_SOURCE rev-parse HEAD).Trim()
if ($LASTEXITCODE -ne 0 -or $coreHead -ne $VDH_SHA) { throw "Core HEAD does not equal VDH_SHA." }
$coreStatus = git -C $VDH_SOURCE status --porcelain
if ($LASTEXITCODE -ne 0 -or $coreStatus) { throw "The selected core checkout must be clean." }
~~~

The POSIX shell stops on a failed status, checkout, or comparison; the PowerShell example checks each Git exit code. Do not continue to adopt.py unless the checkout succeeded, HEAD equals VDH_SHA, and the checkout is clean.

## Preview, then apply

Capture PRODUCT_BASE from the clean product before installation. Keep that exact value for this operation; do not change the base after product work to hide changes.

POSIX shell:

~~~sh
set -eu
PRODUCT_BASE="$(git rev-parse HEAD)"
product_status="$(git status --porcelain)"
test -z "$product_status"

python -B "$VDH_SOURCE/tools/adopt.py" --source "$VDH_SOURCE" --target . --revision "$VDH_SHA"
~~~

Review the plan output. It must name the same C SHA, the expected PRODUCT_BASE, and exactly .vdh/verify.py, .vdh/verify_receipt.py, .vdh/feature_map.py, and .vdh/UPSTREAM.json. A plan is read-only. If the target is dirty, the expected HEAD changed, .vdh exists or is ignored, or a source file is missing or nonregular, the tool blocks. Resolve the actual condition and start again from the clean product state; do not overwrite existing work.

Apply only after reviewing the plan and confirming the product is still clean at PRODUCT_BASE:

~~~sh
set -eu
product_head="$(git rev-parse HEAD)"
test "$product_head" = "$PRODUCT_BASE"
product_status="$(git status --porcelain)"
test -z "$product_status"
python -B "$VDH_SOURCE/tools/adopt.py" --source "$VDH_SOURCE" --target . --revision "$VDH_SHA" --apply --expect-target-head "$PRODUCT_BASE"
~~~

The product HEAD check is separate from the VDH_SHA checkout check. The apply command creates .vdh exclusively; if any copy fails it removes only files created by that invocation. It never overwrites an existing destination. The final directory is:

~~~text
.vdh/
├── verify.py
├── verify_receipt.py
├── feature_map.py
└── UPSTREAM.json
~~~

UPSTREAM.json contains schema_version 1, source_commit C, SHA-256 hashes of the three copied modules, and a scope marker. These hashes describe copied bytes; they are not publisher authentication.

PowerShell equivalent:

~~~powershell
$PRODUCT_BASE = (git rev-parse HEAD).Trim()
if ($LASTEXITCODE -ne 0) { throw "Could not read product HEAD." }
$productStatus = git status --porcelain
if ($LASTEXITCODE -ne 0 -or $productStatus) { throw "The product must be clean before installation." }

python -B "$VDH_SOURCE/tools/adopt.py" --source "$VDH_SOURCE" --target . --revision "$VDH_SHA"
if ($LASTEXITCODE -ne 0) { throw "Adoption preview was blocked." }
# Review the preview output and verify it names PRODUCT_BASE and exactly four files.
$productHead = (git rev-parse HEAD).Trim()
if ($LASTEXITCODE -ne 0 -or $productHead -ne $PRODUCT_BASE) { throw "Product HEAD changed after preview." }
$productStatus = git status --porcelain
if ($LASTEXITCODE -ne 0 -or $productStatus) { throw "The product changed after preview." }
python -B "$VDH_SOURCE/tools/adopt.py" --source "$VDH_SOURCE" --target . --revision "$VDH_SHA" --apply --expect-target-head "$PRODUCT_BASE"
if ($LASTEXITCODE -ne 0) { throw "Adoption was blocked; inspect the reason before retrying." }
~~~

## Configure a product-specific map

The core does not install harness/verification.json or copy product files. Read the starter example at $VDH_SOURCE/harness/examples/verification.project.json and create a product-owned harness/verification.json. Tailor docs_paths, risk_paths, checks, profiles, and features to the product's actual source, contracts, and tests. Map feature IDs to product paths, real entrypoints, acceptance headings, tests, and symptoms.

The starter has features empty and an adoption-pending check in all profiles. That check is deliberately failing. Replace it only when the product has an owned acceptance condition, a real check, a normal and negative journey, and the required full suite. MAP_VALID with an empty feature list only means the references currently supplied to the map are valid; it is not adoption completion or product readiness.

After writing the product manifest, run navigation against the product root:

~~~sh
python -B .vdh/feature_map.py lint --root . --manifest harness/verification.json
python -B .vdh/feature_map.py lookup --root . --manifest harness/verification.json --query "describe the observed problem"
~~~

Lookup returns lexical candidates or UNRESOLVED. It does not diagnose the cause or prove behavior. See [navigation and journey behavior](FACTORY_READY.md).

## Plan and execute product checks

Use the PRODUCT_BASE captured before product work for plan, run, and receipt validation. Do not replace it with a later base to hide changed paths. Explicit --feature adds the named feature's checks; it never excludes checks required by risk or unmapped paths.

Use a new external evidence directory for each run:

~~~sh
FEATURE_ID="a-real-product-feature"
EVIDENCE="../vdh-evidence/product-run-001"
RUN_ID="product-run-001"
CONTEXT="synthetic-fixture-v1"
HEAD_SHA="$(git rev-parse HEAD)"

python -B .vdh/verify.py --root . --manifest harness/verification.json --base "$PRODUCT_BASE" --feature "$FEATURE_ID" --plan --context "$CONTEXT"
python -B .vdh/verify.py --root . --manifest harness/verification.json --base "$PRODUCT_BASE" --feature "$FEATURE_ID" --output "$EVIDENCE" --run-id "$RUN_ID" --context "$CONTEXT"
python -B .vdh/verify_receipt.py --root . --manifest harness/verification.json --base "$PRODUCT_BASE" --feature "$FEATURE_ID" --receipt "$EVIDENCE/receipt.json" --expect-head "$HEAD_SHA" --expect-run-id "$RUN_ID" --context "$CONTEXT"
~~~

HEAD_SHA is an independently captured product HEAD. Use exactly the same base, feature, run ID, and context in both tool invocations. Add --require-clean only when you expect a clean source snapshot at validation time. The evidence directory must be outside the checkout and new or empty. The validator checks consistency and logs; it does not rerun checks or approve the product. See [receipt integrity](EVIDENCE_INTEGRITY.md).

## Prove product behavior

Before replacing the failing starter guard, define a product-owned acceptance condition and connect a real check. Run a positive case that demonstrates expected behavior and a negative case that discriminates the relevant failure. Record the observed outputs and logs. Then run the retained core unit suite and verify the product's full profile includes its required suite. The fixed alpha/beta journey in this repository tests this toolkit only; it is not a product journey.

Installation, a valid configured map, executed product checks, and approval are distinct. A receipt does not establish correctness or authorize a merge. See [selection and execution](HARNESS.md).

## Upgrade and revert

Adoption is copy-only and refuses an existing .vdh directory. Upgrade manually: commit the current product state, review the new public-core source commit, compare the three runtime files and their behavior contracts, and update only the intended vendor files and matching UPSTREAM hashes in an owned product change. Do not point installation at a moving branch. Validate the resulting product with its own checks and journey. Revert by reverting the product commit that changed .vdh; preserve product data and evidence.

## AC-H061: Safe pinned adoption

A plan writes nothing. An explicit apply copies only pinned regular blobs for verify.py, verify_receipt.py, and feature_map.py into a new .vdh directory, records source commit C and per-file hashes, and leaves product contracts and commands untouched. Existing, symlinked, ignored, or nonregular destinations/files, a missing core module, unpinned revisions, dirty targets, and changed or missing expected product HEAD block without overwriting user work. Clean owned Git worktrees are supported. The initial product manifest remains failing until product-specific checks replace its guard; installation, map validity, executed product checks, and approval remain distinct. Upgrade is manual.
