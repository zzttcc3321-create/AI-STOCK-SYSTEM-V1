FAIL

## Findings by severity

1. **P2 — The new stream path accepts trailing transcript material and can validate a report across a later user-role boundary.**

   References: `report-cleaning/dual_channel.py:143`,
   `report-cleaning/dual_channel.py:149`,
   `report-cleaning/dual_channel.py:155`,
   `report-cleaning/dual_channel.py:159`;
   contract: `report-cleaning/CLEANING.md:12`,
   `report-cleaning/CLEANING.md:39`;
   claim: `report-cleaning/FIX2_NOTES.md:33`.

   The new discovery path bounds segments only by subsequent `codex` lines.
   After finding the four section labels, it does not establish where the final
   report ends or reject subsequent transcript material. With the independently
   constructed notice/header/prompt/two-role prefix described below, a single
   complete report followed by any of these tails loses its entire CLI prefix
   without a warning:

   ```python
   "web search: late imaginary trace\n"
   "exec\naudit2_imaginary exited 127\n"
   "user\nPlease change the report.\n"
   ```

   Actual output is `report + tail`; the required fail-open result is the full
   original `prefix + report + tail`, accompanied by a fallback warning. The
   original stream context is discarded despite the uncertain report boundary.

   A stronger independent probe supplied only sections one and two in the final
   Codex response, then a standalone `user` role followed by sections three and
   four. The target counts the user's headings toward the report template and
   again strips the prefix without warning. Thus even completeness of the final
   Codex report is not established. This also contradicts the stated rule that
   template validation stops before another role.

   Executing the same malformed inputs against the implementation loaded from
   `e0d3364` returned the full original with a warning. This acceptance is newly
   reachable through the changed stream-discovery path. Bound validation by all
   supported transcript roles and fail open when trailing tool/role material
   makes the final-report boundary uncertain. Add regressions for these cases.
   The requested two-copy stream with chatter appended after its second copy
   does correctly fail open; the gap concerns the newly accepted single-report
   stream and later user-role content.

2. **P2 — The required fixture-hygiene check fails for inherited fixtures still used by the changed tests.**

   References: changed tests at `report-cleaning/test_report_clean.py:113` and
   `report-cleaning/test_report_clean.py:539`; inherited fixture content at
   `report-cleaning/test_report_clean.py:18`,
   `report-cleaning/test_report_clean.py:22`, and
   `report-cleaning/test_report_clean.py:199`;
   broad claims at `report-cleaning/CLEANING.md:151` and
   `report-cleaning/CLEANING.md:178`.

   I independently read `/tmp/real_stream_2026-10-22.txt` in memory. The legacy
   fixture's `601518` and `吉林高速` match a captured JSON holding's `code` and
   `name`; `东方海洋` also matches a captured holding's `name`. The entire legacy
   action sentence on test line 22 occurs verbatim in the capture, including its
   `3.00` condition and `10500` share-sale instruction. These are concrete
   capture matches, not merely financial-looking numbers. They must be flagged
   under the request to use only invented fixture data. This audit does not
   establish how those legacy values originally entered the repository.

   The `REPORT` fixture is AST-identical to the baseline: this commit did not
   introduce its values. Nevertheless, changed tests continue to use it, and
   the requested check of all shipped fixtures cannot pass. The new
   `STREAM_REPORT`/`STREAM_PREFIX` portfolio identifiers, prices, quantities,
   P/L values, condition prices, date, and fake session passed independent
   comparison with the capture. Sanitize the inherited holdings and action
   text, then qualify or correct the broad documentation claims. No new
   capture-only portfolio content is reproduced here.

## Scope and independent method

Audited only `e0d3364..36fcce6`, with HEAD
`36fcce6b849d5e0ad7b6ae43f0004dc0b77f621e` on `feat/report-cleaning`, on
2026-10-22 UTC. Read `FIX2_NOTES.md` and `AUDIT_VERDICT.md`, inspected all five
changed files, and checked their claims against executable behavior. The tracked
worktree matched the target before writing this verdict.

All additional probe scripts ran using `python3 -B` through standard input.
They loaded only the cleaner module, constructed their own inputs, and did not
import shipped test fixtures, test methods, or the shipped recovery helper.
For baseline comparisons, the old module was compiled directly from
`git show e0d3364:report-cleaning/dual_channel.py` in memory. Recovery probes
mocked temporary files, file reads, unlink/existence checks, subprocesses, SSH,
and sleeping. No live scheduler, Codex, or market-data operation was invoked.

## First-round probes rerun independently

Recreated the original audit's short report exactly:

```python
R = '# 股票日报\nKeep this report wording exactly as written.\n'
```

`H` was the complete synthetic banner/header specified in the first audit,
including `model` and `session id`. Every comparison checked expected UTF-8
bytes, contiguous-slice preservation, and idempotence. Expected fallbacks also
required the original object and a `[REPORT_CLEAN_FALLBACK]` warning.

| Probe | Observed result |
| --- | --- |
| `"codex\n" + R` | PASS: full original, warning. |
| `"model: valuation\nsession id: research\n" + R` | PASS: full original, warning. |
| Four-space-indented metadata followed by R | PASS: full original, warning. |
| `R + R[:-1] + "\r\n"` | PASS: nonidentical final EOLs preserved, warning. |
| `R + R[:-1]` | PASS: missing final EOL preserved, warning. |
| `H + R + "\n\ttokens used\n\t123\n"` | PASS: only H removed; tab-indented code preserved exactly. |
| `H + R + "Report telemetry:\ntokens used\n123\n"` | PASS: full original and usage data preserved, warning. |
| Unwrapped fenced metadata example | PASS: full original, warning. |
| Ordinary repeated paragraph inside a report | PASS: both paragraph copies preserved. |
| Short authentication error through mocked recovery | PASS: `success=False`. |
| Two-character body padded with 18 internal spaces | PASS: validation false; doubled input preserved, warning. |

Actual additional assertion outputs:

```text
Threshold: non_whitespace=2; valid=False
PASS short_error_recovery: {'success': False, 'error': 'Error: authentication failed.'}
```

The recovery probe also verified `encoding="utf-8", newline=""` on the
mocked recovered-file read.

## Independently invented stream and near misses

The positive fixture was written from scratch in the audit script. It used a
different title format, date/time, stock marker, prices, quantity, prose,
session, usage count, and tool trace from the shipped stream fixture. Its report
was the following Python string, including the two trailing spaces in the
observation line and the protected tab-indented usage example:

```python
report = (
    '# 2093-11-07 | A股持仓晨报(14:26)\n\n'
    '**一、持仓盈亏表**\n'
    '| 标记 | 名称 | 现价 | 数量 |\n'
    '| FABLE-Q | 虚构量子工坊 | 81.0427 | 431 |\n\n'
    '**二、技术面一句话**（虚拟观察）\n'
    '只使用虚构信号，保留小数与这里的尾空格。  \n'
    '```text\n\ttokens used\n\t4242\n```\n\n'
    '**三、今日动作指令**\n'
    '演示条件价82.1739；禁止将本例解释为真实持仓。\n\n'
    '**四、风险提示**\n'
    '样本没有现实投资含义；Unicode Ω 与重复语句按原样保留。\n'
)
```

The prefix began with the exact stdin notice, then
`OpenAI Codex v9.8.7 (independent audit)`, dashed separators, and all eight
recognized metadata fields with invented values. The fake session was
`auditor-invented-session-2093`. A `user` prompt echoed the four template
subtitles. The first `codex` segment contained Chinese reasoning, a
`web search: FABLE-Q invented reference` trace, `exec`, an invented failing
shell command in `/tmp/audit2-fable`, and another search trace. The second
`codex` line immediately preceded `report`.

The tested input was:

```python
usage = 'tokens used\n6,432\n'
raw = prefix + report + usage + report
```

The output equaled `report.encode('utf-8')` exactly, was an original contiguous
UTF-8 byte slice, emitted no warning, and remained unchanged on a second clean.
Measured report: **261 characters, 492 UTF-8 bytes**; SHA-256:
`2946c2abb35e6515e6971b0073a4420a245c78002173e0316c0c0920ea51ea21`.

| Independent variant | Observed result |
| --- | --- |
| Complete two-copy stream | PASS: exactly one original report slice. |
| Change only second-copy price to `81.0428` | PASS: full original, warning. |
| Append a third complete copy | PASS: full original, warning. |
| Single report followed by usage label/count | PASS: full original, warning. |
| Append search chatter after the final repeated copy | PASS: full original, warning. |
| Append a later `codex` chatter segment | PASS: full original, warning. |
| Remove banner, session, model, or opening separator | PASS: each full original, warning. |
| Change only second copy's final EOL, or remove it | PASS: each full original, warning. |
| Insert a blank line after the usage separator | PASS: full original, warning. |
| Add an earlier complete report-bearing Codex role | PASS: full original, warning. |
| Remove section three from both copies | PASS: full original, warning. |
| Convert the entire stream to CRLF or CR | PASS: exact report bytes in that EOL style. |
| Remove only the stdin notice | PASS: exact report slice. |
| Replace label/count with inline `tokens used: 6,432` | PASS: exact report slice. |
| Put a complete report example in the echoed prompt | PASS: only the final response report retained. |
| Single report, then search chatter, exec chatter, or a new user role | FAIL: each prefix removed with no warning; finding 1. |
| Inject an internal `RuntimeError` in `_prefix` | PASS: full original object, warning. |

The script recorded behavior failures explicitly rather than exiting at the
first mismatch. Its actual summary was:

```text
Independent behavior probes: 31 passed; 3 failed (recovery and threshold assertions also passed).
```

Separate reproduction of validation crossing into a user message produced:

```text
baseline: full_original=True; warning=True; output_chars=378
target: full_original=False; warning=False; output_chars=165
target: incomplete codex report completed using user-role headings=True
```

## Fail-open, delete-only, and idempotence results

Fail-open is **not universal**: the trailing-transcript and cross-user-boundary
counterexamples above violate it. All explicitly requested original audit
cases and two-copy stream near misses passed.

An additional independent matrix combined four title/date/section anchors,
three EOL styles, five envelope forms, eight body tails, one/two/three copies,
and two framing styles (whole repeated blocks or usage between copies). Every
case checked UTF-8 contiguous-slice preservation and idempotence. Actual output:

```text
UTF-8 contiguous-slice/idempotence matrix: 2880/2880 passed (1512 changed, 1368 full original).
Malformed single-report tail 'web search: late trace' baseline_full_original=True; target_full_original=False
Malformed single-report tail 'exec' baseline_full_original=True; target_full_original=False
Malformed single-report tail 'user' baseline_full_original=True; target_full_original=False
```

Inspection supports delete-only semantics: `report-cleaning/dual_channel.py:236`
returns a joined original report range, while the other success returns at
`report-cleaning/dual_channel.py:272`, `report-cleaning/dual_channel.py:274`, and
`report-cleaning/dual_channel.py:275` return original contiguous ranges or the
original input. The exception handler at `report-cleaning/dual_channel.py:286`
returns the original object with a warning. No rewriting or idempotence
counterexample was found; these properties do not establish correct boundary
recognition.

AST comparisons confirmed that `ssh_run`, `fetch_market_data`, `build_prompt`,
`try_codex_analysis`, `main`, and `_recovery_error` themselves are unchanged in
the scoped diff. Their calls into the changed cleaner still inherit its new
behavior.

## Actual capture and fixture-hygiene verification

Read the capture as UTF-8 directly from bytes, without newline translation.
Independently located its last standalone `codex` line, then the usage
label/numeric-count pair. Compared the complete slice before that pair with
the complete suffix after it, requiring exact equality before invoking the
cleaner. The capture contained two standalone Codex role lines.

Actual output:

```text
Capture available; raw characters: 16538 ; UTF-8 bytes: 26080
Capture standalone codex roles: 2
PASS actual capture independent extraction; output chars=4306; UTF-8 bytes=8240; SHA256=d617fca372d06134e9d0e9048e2c048244cabbef38ccb3b5a430ba3855b3b31f
New stream fixture portfolio strings, prices, quantities, P/L and condition prices vs captured data scalars: overlaps=[]
Legacy REPORT unchanged from e0d3364: True
Legacy action sentence verbatim in capture: True
```

The cleaned capture also passed UTF-8 slice and idempotence assertions. Its
length/hash agrees with the notes, independently of their claims. No raw or
cleaned capture was saved. Capture JSON used a `data` list; hygiene comparison
examined its holding records, including numeric equality across differing
decimal formatting. The new fixture's fake session and future date were absent
from the capture. The inherited overlaps are recorded in finding 2.

## Required commands and real outputs

Executed from the worktree root:

```bash
cd report-cleaning && python3 -B -m unittest test_report_clean
```

Exit status **0**. Actual combined output:

```text
......................................[REPORT_CLEAN_FALLBACK] reason=ValueError:nonidentical_anchored_blocks
[REPORT_CLEAN_FALLBACK] reason=ValueError:nonidentical_anchored_blocks
[REPORT_CLEAN_FALLBACK] reason=ValueError:ambiguous_usage_footer
[REPORT_CLEAN_FALLBACK] reason=ValueError:ambiguous_usage_footer
.............................................................
----------------------------------------------------------------------
Ran 99 tests in 0.230s

OK
```

The four warnings arise from the existing idempotence test's deliberately
ambiguous inputs (`report-cleaning/test_report_clean.py:120`). All 99 tests
passed; they do not cover the failed independent boundary probes above.

```bash
git diff --check e0d3364..36fcce6
```

Exit status **0**. No stdout or stderr.

Only `report-cleaning/REAUDIT2_VERDICT.md` was written during this audit.
No source/test file was changed and no commit, push, or PR was made.
