# Acceptance: Runnable harness navigation

## AC-H010: Current navigation without changing intent

Given a registered feature, lookup returns its candidate ID, SPEC/AC references, actual source/test lines, and current-file content hashes. Missing files, symbols, headings, or invalid acceptance/check bindings block. Line movement is reflected on the next read; intended behavior is never rewritten. Empty or unmatched queries remain unresolved, and multiple matches remain candidates.

## AC-H011: Real native CLI journey

Given an isolated repository with fixed synthetic alpha/beta inputs, public --plan reports PLANNED_NOT_RUN. Focused execution selects smoke and alpha exactly once and omits beta; its receipt and log contain the observed ALPHA_OK result. The adapter does not use select() or execute() to manufacture expected results. Commands, timing, actual stdout/stderr, receipts, and logs appear in the structured report captured by the existing runner.

## AC-H012: Discriminating failure cases

Given alpha input reject, the runner returns exit 1 and FAIL; the alpha check records the child's exit 7 and FAIL status. Given a changed risk path and requested focused profile, the runner returns exit 2 and BLOCKED before creating output. Failed or unavailable steps cannot yield JOURNEY_PASS. Cleanup runs on ordinary exceptions and timeouts, removes only adapter-owned temporary state, and reports a cleanup failure instead of inventing a successful teardown.

## AC-H013: Guardrails and proportional scope

Navigation parses references without importing mapped code, writing files, or executing commands. Traversal and symlink references are rejected. The pilot copies only its fixed fixture inputs and the current native CLI, forwards no arbitrary environment keys, and never accepts an external cleanup target. Documentation-only changes select the reference checks; full includes the retained core unit suite and the synthetic journey once, with check ID deduplication.

## Evidence and limits

This is the real developer-tool CLI exercised with synthetic inputs, not an adopted product or user-PC deployment. No live model evaluation, Windows execution, or performance improvement is claimed unless separately executed. Timings are observations, not a benchmark. Evidence consistency and acceptance never authorize a merge.
