# Feature navigation and native CLI journey

The feature map makes existing source and acceptance references easier to inspect. It reads the manifest and referenced files without importing mapped Python code or executing commands.

## Supported references

References may point to a repository-relative file, a Python qualified name such as tools/verify.py:select or tools/verify.py:SomeClass.method, or a Markdown ID heading such as specs/harness-verification/SPEC.md#BR-H002. Python names are resolved from the AST. Markdown references must match one explicit heading ID outside a fenced code block.

Each resolved reference reports its current line and end line where applicable, the SHA-256 of the full referenced file, and basis current_file_not_test_result. A line or file change is reflected on the next lookup; the hash is not a test result. Missing files, missing or ambiguous symbols/headings, traversal, and symlink references block map validation.

Run feature_map.py lint to check the manifest's source, test, specification, environment, and acceptance references. The lookup command is lexical:

~~~sh
python -B tools/feature_map.py lookup --query "stale receipt" --feature harness-verification
~~~

Lookup returns CANDIDATES or UNRESOLVED and marks whether multiple features matched. Candidates help navigation; they do not diagnose a bug or select an approved check by themselves.

## Fixed synthetic journey

harness_journey.py runs the real verify.py command line against a fixed, synthetic alpha/beta fixture. It creates a disposable Git repository and copies only the runner and fixture inputs into its owned temporary workspace.

- The positive case changes alpha, selects smoke and alpha once, omits beta, and observes ALPHA_OK in the receipt log.
- The negative input case makes the alpha child command exit 7 and return FAIL; the runner reports FAIL with exit code 1 and the observed ALPHA_REJECTED output.
- The downgrade case adds a risk path and requests focused. The runner returns BLOCKED before creating the requested output directory.

The adapter records actual commands, outputs, receipts, logs, timing, and cleanup status. Fixed input expectations do not make timestamps, temporary paths, Git commit hashes, or report bytes deterministic. The adapter cleans up only its own temporary workspace on ordinary errors and timeouts. This is scoped test isolation, not a security sandbox against malicious native commands.

JOURNEY_PASS means the fixed synthetic CLI expectations held. It is a self-test of the developer tooling, not a journey through an adopted product. A product needs its own checks and positive and negative journey.
