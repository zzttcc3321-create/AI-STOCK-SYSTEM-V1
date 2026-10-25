FAIL

Audit of `62addc3f7b38b977c17321f9eda28781169db0cd` against parent
`207f7e115b27b92dc6823273f77993985f229c82`, performed on 2026-10-22 UTC.
All four committed files were read. The supplied tests pass, but independent
probes demonstrate silent content deletion and a violation of the exact-byte
deduplication claim.

## Findings, ordered by severity

1. **P1 — Report text can supply its own supposed wrapper evidence.**
   References: `report-cleaning/dual_channel.py:117`,
   `report-cleaning/dual_channel.py:123`, `report-cleaning/dual_channel.py:145`;
   `report-cleaning/CLEANING.md:20`, `report-cleaning/CLEANING.md:40`.

   Two recognized field names, including `model` or `session id`, are sufficient
   to discard a preamble; their values need not demonstrate CLI provenance.
   A lone `codex` line also sets `envelope=True`. With the report `R` defined
   below, both `"model: valuation\nsession id: research\n" + R` and
   `"codex\n" + R` return only `R`, without a fallback warning. These can be
   legitimate unwrapped report preambles.

   Header and leading-role recognition also ignore the supplied `outside` mask.
   Consequently, `"    model: valuation\n    session id: research\n\n" + R`
   loses the entire indented example. This directly contradicts CLEANING.md's
   protection for metadata inside four-space code. The documented permissive
   header/role rules match the implementation, but cannot uphold the broader
   promise to preserve unwrapped report content. Require independent envelope
   evidence and apply code-context protection before consuming a prefix.

2. **P2 — Footer recognition can delete report body data, including indented code.**
   References: `report-cleaning/dual_channel.py:80`,
   `report-cleaning/dual_channel.py:83`, `report-cleaning/dual_channel.py:154`,
   `report-cleaning/dual_channel.py:159`; `report-cleaning/CLEANING.md:37`.

   `_outside_fences()` counts only literal spaces as indentation. Tab-indented
   lines are marked outside code; `_core_end()` subsequently strips indentation
   before recognizing usage. `HEADER + R + "\n\ttokens used\n\t123\n"`
   returns only `R`, deleting the code and its numeric value. `HEADER` is the
   complete banner fixture from `test_report_clean.py:26`.

   Unindented terminal report telemetry is also vulnerable:
   `HEADER + R + "Report telemetry:\ntokens used\n123\n"` returns
   `R + "Report telemetry:\n"`. A valid session header alone does not establish
   that a matching terminal datum was emitted by the CLI. Protect tab-indented
   code and fail open when the body/footer boundary is ambiguous.

3. **P2 — Deduplication accepts blocks that are not byte-identical.**
   References: `report-cleaning/dual_channel.py:152`,
   `report-cleaning/dual_channel.py:206`, `report-cleaning/dual_channel.py:208`;
   `report-cleaning/CLEANING.md:33`.

   Comparison removes whitespace-only boundary lines and uses
   `.rstrip("\r\n")` on each block. For `R` ending in LF, both
   `R + R[:-1] + "\r\n"` and `R + R[:-1]` return `R`. The second blocks
   have different terminal bytes, yet they are discarded without fallback.
   CLEANING.md explicitly permits ignoring final newlines, so its rule is weaker
   than the commit/user claim of byte-identical blocks. The existing mixed-EOL
   test changes every newline and misses this last-line-only case. Compare the
   original block bytes, or explicitly revise the claimed contract.

4. **P2 — A non-cleaning recovery change accepts short CLI errors as reports.**
   References: `report-cleaning/dual_channel.py:332`,
   `report-cleaning/dual_channel.py:341`, `report-cleaning/dual_channel.py:375`;
   `report-cleaning/CLEANING.md:64`.

   The source scheduler requires `len(content) > 100`; the copy requires only
   `content.strip()`. In the existing mocked recovery context, the input
   `"Error: authentication failed.\n"` produces
   `{'success': True, 'result': 'Error: authentication failed.\n'}`.
   The source rejects that input. `main()` consequently delivers this error as a
   successful Codex report and bypasses the backup channel. CLEANING.md:64-67
   discloses this acceptance-policy change; it is an additional behavior change
   outside wrapper cleanup. Preserve explicit failure detection while supporting
   valid short reports.

5. **P3 — The documented short-report character threshold is inaccurate.**
   References: `report-cleaning/dual_channel.py:182`,
   `report-cleaning/dual_channel.py:184`; `report-cleaning/CLEANING.md:31`.

   The documentation requires at least 20 non-whitespace body characters.
   The code measures `len(line.strip())`, which includes internal spaces and
   tabs. `"# 股票日报\nx" + " " * 18 + "y\n"` has only two non-whitespace
   body characters, but passes validation and is deduplicated when repeated.
   This weakens the safeguard against treating ordinary repeated text as a
   complete report. Count non-whitespace characters or correct the claim.

## Tests run and actual outputs

From the worktree root, the requested test command was run exactly:

```bash
cd report-cleaning && python3 -B -m unittest test_report_clean
```

Exit status: **0**. Combined output:

```text
..................................................
----------------------------------------------------------------------
Ran 50 tests in 0.089s

OK
```

From the worktree root:

```bash
git diff --check 207f7e1..62addc3
```

Exit status: **0**. No stdout or stderr.

Additional preservation probes used `python3 -B` with an in-memory script;
no source or test file was created or edited. Common fixture:

```python
R = '# 股票日报\nKeep this report wording exactly as written.\n'
```

Each probe compared actual output to the expected original body (removing only
`HEADER` where present). Actual output, exit status **0**:

```text
FAIL bare_codex_is_real_prelude
  actual='# 股票日报\nKeep this report wording exactly as written.\n'
FAIL unwrapped_real_metadata
  actual='# 股票日报\nKeep this report wording exactly as written.\n'
FAIL four_space_code_metadata
  actual='# 股票日报\nKeep this report wording exactly as written.\n'
FAIL nonidentical_final_EOL
  actual='# 股票日报\nKeep this report wording exactly as written.\n'
FAIL nonidentical_missing_final_EOL
  actual='# 股票日报\nKeep this report wording exactly as written.\n'
FAIL tab_indented_body_code
  actual='# 股票日报\nKeep this report wording exactly as written.\n'
FAIL legitimate_body_usage_data
  actual='# 股票日报\nKeep this report wording exactly as written.\nReport telemetry:\n'
PASS unwrapped_fenced_example
PASS legitimate_repeated_paragraph
Content-preservation probes: 2 passed, 7 failed (9 total).
Short-error recovery: {'success': True, 'result': 'Error: authentication failed.\n'}
Stock >100-character success gate for this text: False
Slice/idempotence properties: 1080/1080 passed.
```

The property matrix used four title/date/section anchors, three newline styles
(LF, CRLF, CR), five envelope forms, six tail forms, and one/two/three copies.
For every input, assertions checked `cleaned in raw` and
`clean_report(cleaned) == cleaned`. No slice-preservation or idempotence
counterexample was found. The seven failed preservation comparisons above are
reported by the probe script; they are additional to the 50 passing supplied
unit tests.

The short-report threshold probe produced:

```text
Non-whitespace body characters: 2
Stripped body length: 20
Accepted as a valid short report: True
Two paragraphs reduced to one: True
Output: '# 股票日报\nx                  y\n'
```

`bash -n report-cleaning/run_codex.sh` produced no output. AST parsing of both
Python files succeeded:

```text
report-cleaning/dual_channel.py: syntax OK
report-cleaning/test_report_clean.py: syntax OK
```

## Provenance, scope, and sensitive-data checks

The reference files are available at `/home/ubuntu/work/stock-system-v2/`.
Direct comparison confirms `run_codex.sh` is byte-identical. Both copies have
SHA-256 `51abb5c8b5a92b3d89d14fb697c466a02a89006c5af99c863a7d1b44a49744c8`.
AST comparison with the reference scheduler produced:

```text
ssh_run: AST identical
fetch_market_data: AST identical
build_prompt: AST identical
try_codex_analysis: AST changed
main: AST identical
Existing constant changes: {}
Added imports: ['import logging', 'import re']
```

The full source diff confines functional changes to the added cleaner/logging,
raw UTF-8/newline-preserving recovery read, cleaning invocation, and the changed
success/error policy described in finding 4. Other differences remove trailing
source whitespace. No additional unrelated scheduler behavior change was found.
These comparisons establish agreement with the reference files available during
this audit; the external source directory is not a versioned parent baseline.

Call-level comparison also produced:

```text
Scheduler subprocess/SSH call ASTs identical to source: True
run_codex.sh byte-identical to source: True
```

The commit adds network-capable scheduler copies to the repository: SSH/SCP,
remote Codex execution, and invocation of the existing market-data fetcher
(`dual_channel.py:235`, `dual_channel.py:244`, `dual_channel.py:284`,
`dual_channel.py:297`, `run_codex.sh:9`). Those calls are inherited unchanged from
the stated source. The cleaner introduces no network/subprocess calls, imports
do not launch the scheduler, and audit/test execution used mocked recovery only.
No network request, Codex invocation, or live market-data fetch was performed.

Reading all four added files and scanning credential/network-related literals
found no embedded secret, credential token, private key, or live holdings file.
`SSH_CONFIG` and `SSH_KEY` contain inherited filesystem paths, not credentials.
The stock names, prices, quantities, session IDs, and token counts occur in
examples/tests explicitly identified as synthetic in `CLEANING.md:85` and
`test_report_clean.py:14`; no production holdings dataset was added.

The audited source/test files matched commit `62addc3` throughout the review.
Only this verdict file was written. No commit, push, PR, or deployment was made.
