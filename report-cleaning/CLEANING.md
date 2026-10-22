# 股票日报报告清洗

## Scope and contract

`dual_channel.py` recovers a Codex result file containing merged stdout/stderr.
`clean_report(text: str) -> str` removes a proven CLI prefix and exact duplicate
complete reports. It uses only the Python standard library and never calls a
model, subprocess, or network service itself.

Cleaning is delete-only: the result is a contiguous slice of the original text.
It never rewrites wording, numbers, Markdown, spaces, tabs, or line endings.
When recognition or validation is uncertain, or an internal exception occurs,
it returns the complete original input object and logs a warning. Cleaning the
result again leaves it unchanged.

## Recognition rules

1. A removable prefix requires the complete banner form: an `OpenAI Codex v…`
   line whose version starts with a digit, a dashed separator (at least three
   hyphens), known nonempty metadata fields including **both** `model` and
   `session id`, and a closing dashed separator. The field whitelist is
   `workdir`, `model`, `provider`, `approval`, `sandbox`, `reasoning effort`,
   `reasoning summaries`, and `session id` (case insensitive). Blank lines may
   occur within the header. Metadata fields alone and a bare `codex` line never
   establish CLI provenance; such unwrapped preambles are preserved in full.
   The exact unindented notice `Reading additional input from stdin...` may
   precede the banner. The notice alone authorizes no deletion; the complete
   banner and header must still validate. Unknown preludes remain untouched.
2. Banner, separator, metadata, role, and report-anchor recognition all respect
   the code-context mask. Backtick/tilde fenced code and indentation of at least
   four columns are protected. Tabs advance to four-column stops, so a leading
   tab or a mixture of spaces and tabs reaching four columns is protected too.
3. After a complete header, a simple envelope may contain a leading `codex`
   role, or a `user` prompt echo followed by one `codex` role. These retain the
   audited report-start rules below. For streams with the stdin notice or
   multiple `codex` segments, discovery is stricter: search only immediately
   after unprotected `codex` roles following the header. The first nonblank line
   must be a recognized **title**, and that segment must contain all four
   template sections in order before another role or a repeated title. Exactly
   one segment must qualify, and it must be the final `codex` segment. Missing
   or multiple qualifying reports return the complete original input.
   Prompt text before the first `codex` is never a report candidate, even when
   it contains dates, section anchors, or a complete example report. Intermediate
   chatter, `web search:` traces, and `exec` commands/errors are skipped only
   when a later segment independently qualifies. Indented or fenced roles cannot
   supply a boundary. A title followed by long chatter is insufficient for this
   stricter discovery.
   Both `user` and `codex` end a segment; later roles cannot supply missing
   sections. A role after the report makes its boundary uncertain and preserves
   the full original. Report slices cannot contain unprotected role lines.
4. Report starts support the titles 持仓晨报, 午盘简报, 持仓日报, 持仓报告, 股票日报;
   Markdown headings/bold, dates, A股/股票 prefixes, simple title separators,
   and parenthesized `HH:MM` times are supported, including
   `**2047年6月19日｜持仓日报（08:37）**`.
   Date-only starts (`YYYY-MM-DD` or `YYYY年M月D日`, optionally
   with time and `日期:`/`日期：`) and `一、持仓盈亏表` starts are also recognized.
5. A complete template report contains the four recognized sections in order:
   `一、持仓盈亏表`, `二、技术面一句话`, `三、今日动作指令`, `四、风险提示`.
   Template section suffixes such as `（每只一行）` are allowed. Alternatively,
   a title-led short report needs at least **20 non-whitespace body characters**
   after its title, counted with `not character.isspace()` outside protected
   code. Internal spaces, tabs, and other whitespace do not count. This short
   alternative does not apply to date-only or first-section starts, stricter
   transcript discovery, or usage-separator confirmation.
6. A single full-template report immediately after a transcript `codex` role
   must end with a recognizable risk list: nonblank unprotected lines after
   section four must be Markdown bullets or numbered items, through EOF.
   Unstructured prose there is ambiguous and returns the full original with a
   warning. Protected code remains part of the report. This conservative rule
   does not infer an endpoint from the presence of four headings alone.
   Unprotected `web search:` traces, standalone `exec`, and subsequent roles
   also cause full fallback. With repeated title anchors, the exact whole-copy
   checks below must prove the endpoint; material after the final copy makes
   those copies unequal. Legacy short reports keep their audited validation.

## Exact duplicate comparison

Subsequent unprotected anchors must match the first anchor line exactly apart
from its line terminator for locating candidate starts. Each candidate block then
extends from its anchor through the line before the next matching anchor, or
through the end of the input for the last block. All blocks must pass report
validation. More than 64 anchors causes a full fallback.

The **original blocks must match exactly**, including their anchor's line
terminator, final newline presence, LF/CRLF/CR style, whitespace-only boundary
lines, telemetry, spaces, wording, and numbers. Nothing is normalized for
comparison. The framed usage separator described below is the sole exception
to using the entire anchor-to-anchor range as a report block. Any differing
report byte preserves **both copies and the entire input**
with a fallback warning, including any CLI header. Identical blocks retain the
entire first block. Without an envelope, whitespace preceding that first block
is retained too. There is no paragraph similarity matching or line deduplication.

## Token usage and ambiguity

A session header does not prove where the report ends. The cleaner therefore
**does not independently strip token-usage lines** from the merged text stream.
An unindented terminal `tokens used: 1,234` line or `tokens used` / `1,234` pair
on a single enveloped report is ambiguous and returns the entire original input,
including its header. Trailing blank/whitespace-only lines do not remove this
ambiguity. Indented or fenced examples stay protected report content.

A usage separator between two reports can be removed only when all of these
conditions hold:

- A complete CLI header was consumed, and the first report title immediately
  follows an unprotected `codex` role (blank lines may intervene).
- Both title-led reports independently contain the full four-section template
  in order. The short-report alternative cannot authorize this deletion.
- There are exactly two matching title anchors. A single unindented
  `tokens used: 1,234` line or `tokens used` / `1,234` pair immediately precedes
  the second title, with no extra lines or boundary whitespace in the separator.
- The original first report slice **before** that separator exactly equals the
  second report slice through EOF, including every line terminator and final
  newline. No trimming makes copies equal.

This framing permits removal of the prefix, usage separator, and repeated final
report from the captured stream shape. The returned text is the first original
report slice. A different price, wording, space, EOL, truncated copy, extra copy,
unknown separator, or missing role/template evidence preserves the full input.
Whitespace that occurs identically inside both report slices is retained.

In all other cases, usage between copies or on just one copy makes the original
blocks different and preserves the entire input. If exact duplicate blocks both
contain telemetry, deduplication keeps all telemetry in the first block.
Unwrapped single reports retain telemetry unchanged.

## Recovery and failure detection

`try_codex_analysis()` reads the recovered file with UTF-8 and `newline=""`,
passes the original text to `clean_report()`, and checks for empty output and
explicit CLI diagnostics before returning success. Successful report text then
passes through `result["result"]` into `main()`'s `output["report"]` JSON. No
separate final-report file or new JSON field is introduced.

There is no minimum recovery length. Valid short reports, including unknown
non-error formats, remain deliverable. At the first nonblank line (or immediately
after a complete header and optional `codex` role), case-insensitive `Error:` or
`fatal:` diagnostics, optionally prefixed by `codex:` or `codex exec:`, and
`Traceback (most recent call last):` cause `success=False`. The error field is
trimmed and limited to 300 characters. Error text mentioned later in a report
body is not treated as a leading diagnostic. This is explicit diagnostic
recognition, not a general judgment of report correctness.

`Error: authentication failed.` therefore engages `main()`'s existing
`aqi_fallback` channel. An empty/whitespace-only result still returns
`{"success": false, "error": "Codex输出为空"}`. Cleaner fallback is distinct from
channel fallback: an uncertain non-error report remains a successful recovery
of its exact original text. SSH/SCP calls, model/reasoning configuration, temporary
file cleanup, and the existing JSON channel format are unchanged by these fixes.

## Logging and example

Warnings use Python logging (stderr by default), never report text or JSON stdout:

```text
[REPORT_CLEAN_FALLBACK] reason=ValueError:ambiguous_usage_footer
```

Other reasons include `missing_banner`, `missing_transcript_role`,
`invalid_banner`, `incomplete_header`, `unclosed_header`,
`ambiguous_transcript_roles`, `unrecognized_report_start`,
`unrecognized_report_body`, `uncertain_report_end`,
`nonidentical_anchored_blocks`, and `too_many_anchors`.
Portfolio identifiers, dates, prices, quantities, conditions, and report prose
in the current test fixtures are invented. Protocol labels, template headings,
model/configuration literals, and earlier audit probes intentionally retain
their required syntax. The inherited portfolio fixture previously overlapped
the capture; [FIX3_NOTES.md](FIX3_NOTES.md) records its sanitization and the
directory scan, including identifiers recorded in that inventory and evidence
in the unchanged historical verdict.

For example, a complete header followed by `codex`, then two exactly identical
copies of the following block, returns the first block with its final newline:

```text
# 股票日报
虚构样本现价71.6800，模拟阈值74.3900，数量6137股；记录紫色信号灯。
```

Adding token usage to only one copy of this short report returns the entire
original input: it lacks the full template needed for separator confirmation.
A bare `codex` or two metadata lines before the block also returns the entire
original input.

## Validation and delivery

From the worktree root, run:

```bash
cd report-cleaning && python3 -B -m unittest test_report_clean
```

The suite retains all 99 methods present at the second re-audit and adds report
boundary regressions. The earlier safety fixes are
recorded in [FIX_NOTES.md](FIX_NOTES.md); this extension's two expectation/fixture
adjustments, final test count, and local capture length/hash are recorded in
[FIX2_NOTES.md](FIX2_NOTES.md). Current sanitization scope, boundary changes,
test results, and the reference output length/hash are recorded in
[FIX3_NOTES.md](FIX3_NOTES.md). No capture file or cleaned capture is stored in
the worktree. Historical verdicts retain their evidence and have not been
edited; the fixtures and this documentation's example no longer use captured
portfolio data.

Integration tests mock SSH, SCP, subprocesses, market data, temporary files, and
waiting. They exercise cleaning, recovery failure detection, and both JSON
channels without running Codex or fetching market data. `run_codex.sh` and
`AUDIT_VERDICT.md` are unchanged. These fixes are local to `report-cleaning/`;
no commit, push, PR, or deployment is part of this work.
