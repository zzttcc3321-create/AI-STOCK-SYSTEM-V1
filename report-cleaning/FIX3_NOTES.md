# Second re-audit fixes

Fixed both P2 findings in `REAUDIT2_VERDICT.md`, starting from
`36fcce6b849d5e0ad7b6ae43f0004dc0b77f621e` on `feat/report-cleaning`.
The verdict was read in full before changes. All writes remain inside
`report-cleaning/`; no verdict file was edited.

## Report boundary

The stream discovery path bounded candidates only by subsequent `codex` lines.
Consequently, headings after a later `user` could complete an earlier report.
Four section headings also established only report completeness, with no check
that the suffix belonged to the report. A single report followed by tool output
or other text could therefore lose its entire original CLI context.

Discovery now bounds each candidate by the next unprotected `user` **or**
`codex` role. Only Codex segments qualify, and strict stream discovery requires
the qualifying report to be in the last role segment. Report validation also
rejects unprotected role lines, independently of discovery, so later sections
cannot complete an earlier segment.

For a full-template report immediately after a Codex role, `_stream_boundary()`
checks the suffix before prefix removal. Unprotected search traces, standalone
exec markers, and role lines are rejected. A lone report must have a final risk
list whose nonblank, unprotected lines are Markdown bullets or numbered items
through EOF. Unstructured continuation is uncertain and preserves the complete
original object with a fallback warning. Protected code remains report content.
This deliberately also fails open on a lone report whose risk section uses
unstructured prose; headings alone cannot prove its endpoint.

With repeated anchors, the existing whole-slice equality checks must prove the
end. The usage separator still requires exactly two complete templates, a proven
role/envelope, exact separator syntax, and equality of the first report with
the entire repeated suffix through EOF. Extra material after the final copy,
different copies, or incomplete templates return the full original. Direct
exact-copy deduplication and repeated telemetry retain their earlier contract.
All successful returns remain original contiguous slices; no output text is
rewritten, normalized, or constructed from separate report parts.

Nine regression methods were added. They cover the four requested failures
(search tail, exec tail, later user role, and sections three/four only after a
user role), plain-text tails, a 135-case tail/envelope/EOL/copy matrix, numbered
risk lists, conservative handling of unstructured risk prose, and protected
transcript examples. The existing positive stream test still requires exactly
one original report, no warning, UTF-8 slice preservation, and idempotence for
report + usage separator + identical duplicate.

## Sanitization inventory

The inherited fixture contained identifiers matching the capture:
**吉林高速 / 601518** and **东方海洋**. The other requested portfolio names and
codes were absent from fixtures. The inherited action sentence also matched
captured report wording. The documentation example repeated captured
price/condition/share-sale details, so correcting only the stock identifiers
would not have fixed fixture hygiene.

Replacements in `test_report_clean.py` include:

- Invented holding names 示例股份丙 / 示例股份丁 and code `900003`.
- An invented future report date; invented quotes, condition thresholds,
  quantity, daily percentage, and P/L placeholders. Inherited numeric examples
  were replaced even where a capture match had not been established.
- New observation, action, risk, repeated-paragraph, and short-report prose.
  The action uses an invented signal/threshold/quantity rather than a captured
  trading instruction.
- Every dependent price, wording, whitespace, formatting, date-anchor, recovery,
  and JSON-delivery mutation. These continue to exercise actual differences;
  formatting checks still cover signs, decimal precision, leading zeroes,
  thousands separators, internal spaces, and original line endings.

The separately invented stream fixtures retain their invented portfolio data.
All selected portfolio numbers in both report fixtures were checked numerically
against captured holding scalars, ignoring decimal formatting: no overlap.
Their fake sessions also do not match the captured session. Protocol labels,
template headings, model/configuration assertions, and earlier audit syntax are
intentional structural literals, not claims that every test string is invented.

`CLEANING.md` now uses an invented documentation example and limits its hygiene
claims to the current portfolio fixtures and example. Its earlier directory-wide
claim was inaccurate: legacy fixture data already overlapped the capture.
Historical notes/verdicts do not establish the provenance of those legacy data.

The complete directory was scanned recursively, including ignored/binary files,
for all seven requested portfolio names/codes, the requested captured quantity
and price strings, and the captured session ID read only in memory. The final
remaining matches are identifiers in this inventory and evidence in the
unchanged `REAUDIT2_VERDICT.md` (two stock names, one code, and its description
of the legacy action's quantity). No requested captured quantity/price/session
residue remains outside that verdict. No captured session match was found
anywhere in the directory. Verdict evidence is intentionally preserved as
requested; this is not a claim that the entire directory contains no real-data
references.

An additional scan compared 53 nonstructural captured report prose fragments
against every text file, decoding Python string literals to include fixture
values: no matches in editable files. Ten prose fragments from the two report
fixtures were also checked against the capture: no matches. No raw capture,
cleaned capture, captured session ID, captured quantity/price, or captured action
sentence was written during this fix.

## Validation

Required command, executed from the worktree root:

```bash
cd report-cleaning && python3 -B -m unittest test_report_clean
```

Actual result: **108 tests passed**, exit status **0**. All **99** existing
test method names remain; **9** were added. Four expected warning messages
come from the existing idempotence test's intentionally ambiguous inputs.

Additional independent scripts ran through standard input with `python3 -B`,
without importing shipped test fixtures:

- Earlier audit preservation/cleaning probes: **22/22 passed**, including bare
  roles, metadata/code preludes, differing terminal EOLs, protected tab usage,
  legitimate telemetry, exact whitespace, repeated paragraphs, short-report
  thresholds, genuine wrapper removal, and internal-exception fallback.
- Earlier recovery/channel probes: **20/20 passed**, including short/long
  diagnostics, protected examples, later error discussion, valid short reports,
  empty recovery, UTF-8/newline-preserving reads, bounded errors, and backup
  channel delivery. All filesystem/subprocess/SSH/waiting effects were mocked.
- Independently invented stream positives and near misses: **33/33 passed**,
  including all requested tails and cross-role splits, changed/truncated copies,
  missing framing evidence, usage syntax, prompt examples, and all EOL styles.
- UTF-8 contiguous-slice/idempotence matrix: **2,880/2,880 passed**;
  **1,242** inputs cleaned. Separate positive assertions require actual cleaning.
- `git diff --check`: passed.
- All six scheduler/recovery helper function ASTs match HEAD:
  `ssh_run`, `fetch_market_data`, `build_prompt`, `try_codex_analysis`, `main`,
  and `_recovery_error`. Their cleaner calls inherit the corrected behavior.
- All three verdict file hashes match their initial values. `run_codex.sh`
  remains byte-identical to HEAD.

## Reference stream verification

Read `/tmp/real_stream_2026-10-22.txt` directly from UTF-8 bytes, without newline
translation. Independently located the final Codex role and usage separator;
the report slice before usage equals the entire suffix after the count.
`clean_report` equals both report occurrences, is an original UTF-8 contiguous
slice, emits no warning, and is idempotent. Content was compared only in memory.

- Output length: **4,306 characters; 8,240 UTF-8 bytes**.
- Output UTF-8 SHA-256:
  `d617fca372d06134e9d0e9048e2c048244cabbef38ccb3b5a430ba3855b3b31f`.

This agent did not run a commit, push, PR creation, deployment, live scheduler,
or market-data fetch. Only `dual_channel.py`, `test_report_clean.py`,
`CLEANING.md`, and this notes file were written by this agent. During final
verification, HEAD changed externally to
`8843856b8fcce77cb1b2fcc9a3beb97dbf5f96a5`; that commit was left intact.
