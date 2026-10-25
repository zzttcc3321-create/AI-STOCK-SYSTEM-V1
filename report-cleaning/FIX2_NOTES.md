# Real-stream cleaning fix

## Root cause

The audited cleaner handled simplified envelopes but failed open on the captured
stream. Three independent mismatches prevented cleaning:

1. Banner recognition required the first nonblank line to be the banner. The
   CLI's preceding stdin notice therefore prevented envelope recognition.
2. The report title included a date followed by a parenthesized time. Title
   recognition removed dates but left the time behind and rejected the title.
3. Exact-copy comparison treated everything between repeated titles as report
   bytes. The CLI usage label/count between the two reports made those ranges
   unequal. Removing a header alone could not establish a safe footer boundary.

The prompt echo also contains report anchors, and intermediate agent/tool output
occupies a separate `codex` segment. Searching globally for anchors or accepting
long title-led chatter would weaken the existing preservation guarantees.

## Implemented rule

The exact stdin notice is recognized only together with the existing complete
banner, separators, and required nonempty header fields. Parenthesized title
times are recognized without rewriting any original characters.

For notice-bearing streams or transcripts with multiple `codex` roles, search
only the first nonblank, unprotected line after each role following the header.
It must be a recognized title, and its segment must validate all four template
sections exactly once and in order, before another role or the repeated title.
There must be exactly one qualifying segment, and it must be the final `codex`
segment. Prompt anchors are outside this search; incomplete intermediate
segments, chatter, search traces, and command errors cannot establish a report.
Multiple complete candidates or a later chatter segment preserve the original.

Usage removal additionally requires exactly two matching title anchors, a
complete four-section template in both report slices, a complete envelope, and
an immediate preceding `codex` role. The separator must be exactly an unprotected,
unindented usage line or label/count pair immediately before the second title.
The original slice before usage must equal the entire second report through EOF,
including whitespace, line endings, and final newline presence. No trimming or
normalization is used to make the copies equal. The result is the first original
report slice; any uncertainty returns the full original object with a warning.

The audited legacy handling of simple envelopes, date/section starts, and short
title-led reports remains available. Short reports cannot authorize the new
multi-segment discovery or usage removal. Single terminal usage remains
ambiguous, code contexts stay protected, and identical telemetry-bearing blocks
retain their telemetry. Recovery and scheduler function ASTs are unchanged.

## Synthetic fixture and regressions

`test_report_clean.py` defines `STREAM_REPORT`, `STREAM_PREFIX`, `STREAM_USAGE`,
and `STRUCTURAL_STREAM` independently of the reference capture. They reproduce:
the stdin notice; a complete fake CLI header/session; a user prompt containing
template anchors, task text, and JSON; a warning; intermediate agent chatter,
search traces, and a failed bash command; a second `codex` role; a dated, timed,
four-section report; usage label/count; and the exact repeated report.

The portfolio uses invented 示例股份甲/示例股份乙, `SYNTH-A`/`SYNTH-B`, an invented
future date, invented prices/quantities, and `synthetic-session-fix-two`. A local
in-memory check confirmed the new synthetic portfolio/date values are disjoint
from the captured JSON values and the fake session is absent from the capture.
No capture excerpt, portfolio value, title, session ID, or report output was
written to the worktree. Existing legacy fixtures were not replaced with capture
data.

Twenty new test methods cover the complete stream, all three newline styles,
optional stdin notice, prompt examples, protected intermediate examples,
missing/ambiguous roles and headers, immediate title placement, missing/reordered
sections, section validation across role boundaries, differing/truncated copies,
usage framing, repeated telemetry, exact boundary whitespace, idempotence,
contiguous-byte-slice preservation, and mocked recovery.

All 79 previously audited test methods remain. Exactly two existing tests were
adjusted for behavior incompatible with the required stream:

- `test_usage_between_duplicates`: a complete template after a proven `codex`
  role, followed by usage and an exact second copy, now expects the first report.
  This is the newly supported framing rule.
- `test_recovery_preserves_ambiguous_usage`: the second synthetic copy now has a
  differing price, so the test continues to require full-original recovery and
  a warning for ambiguous usage. Its prior identical full-template copies are
  now authorized for cleaning; the new synthetic-stream recovery test covers
  that successful path.

No other existing assertion, test name, or fixture was changed.

## Validation

The baseline suite passed all 79 tests before editing. The full required command
after the fix was:

```bash
cd report-cleaning && python3 -B -m unittest test_report_clean
```

Result: **99 tests passed** (exit status 0). The four warnings from the existing
idempotence test's intentionally ambiguous inputs remain expected.

Additional in-memory scripts independently checked the audit probe outcomes,
without importing the committed test fixtures:

- Audit preservation/cleaning probes: **20/20 passed**, with exact outcomes
  compared against the audited HEAD implementation, including the legacy
  short-report prompt-echo positive case.
- Recovery/channel probes: **20/20 passed**, including leading diagnostics,
  later diagnostic prose, protected examples, short reports, whitespace-only
  recovery, UTF-8/newline-preserving reads, the 300-character error bound, and
  `aqi_fallback` delivery. All filesystem/network/subprocess effects were mocked.
- Fresh four-anchor, three-EOL, five-envelope, six-tail, three-copy property
  matrix: **1080/1080 passed**, checking UTF-8 contiguous slices, idempotence,
  and exact agreement with the audited baseline.
- Positive wrapper/exact-copy matrix: **72/72 passed**.
- Complete-block terminal-EOL equality matrix: **24/24 passed**, also compared
  against the audited baseline.
- Scheduler/recovery function AST comparison: unchanged. All 79 prior test
  method names retained. `git diff --check`: passed.

## Local capture verification

The supplied capture was read locally as UTF-8 without newline translation.
The expected report was independently located between the final `codex` role
and usage label, then compared with the complete repeated report after the
usage count. `clean_report` matched both occurrences exactly. UTF-8 contiguous
slice preservation and idempotence also passed. Only output lengths and its hash
are retained here; no raw or cleaned captured content was saved.

- Cleaned length: **4,306 characters; 8,240 UTF-8 bytes**.
- Cleaned UTF-8 SHA-256:
  `d617fca372d06134e9d0e9048e2c048244cabbef38ccb3b5a430ba3855b3b31f`.

All edits are local to `report-cleaning/`. No commit, push, PR, or deployment
was performed; the capture remains outside the worktree.
