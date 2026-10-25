# Audit fixes for report cleaning

Fixed the five findings in `AUDIT_VERDICT.md` against branch
`feat/report-cleaning`, starting at `62addc3`. The cleaner remains delete-only;
uncertain cleaning returns the complete original input object with a warning.
The implementation and its contract are documented in `CLEANING.md`.

## 1. P1: report text supplying wrapper evidence

**Root cause:** `_prefix()` accepted two metadata names or a bare `codex` as an
envelope. Its banner, metadata, separator, and leading-role checks did not all
consult the supplied code-context mask. Legitimate report preludes and indented
examples could consequently authorize their own deletion.

**Fix:** prefix removal now requires a complete `OpenAI Codex v…` banner,
opening/closing dashed separators, and nonempty known metadata including both
`model` and `session id`. Every recognized header/role line must be outside
protected code. Metadata-only and role-only preludes fail open. Echoed prompts
still require a unique unprotected role/report boundary after a complete header.
Deduplication without an envelope also preserves leading whitespace.

**New tests:** `test_bare_codex_is_real_prelude`,
`test_unwrapped_real_metadata`, `test_four_space_code_metadata`,
`test_indented_banner_is_protected`, `test_indented_header_field_is_protected`,
`test_indented_leading_roles_are_protected`,
`test_indented_echo_role_cannot_authorize_prefix_deletion`,
`test_fenced_echo_role_cannot_authorize_prefix_deletion`, and
`test_dedup_without_envelope_retains_leading_whitespace`.

## 2. P2: code and body telemetry deleted as a footer

**Root cause:** indentation counted literal spaces only; footer recognition then
stripped all indentation. Header presence alone authorized deleting terminal
usage-shaped text, even when it belonged to the report.

**Fix:** indentation uses four-column tab stops. Four-space, tab, mixed-indent,
and fenced code cannot be recognized as CLI usage. More fundamentally, the
merged stream has no independent report terminator: a header or another similar
report cannot prove that usage belongs outside the report. This version does
not independently delete token-usage lines. A usage-shaped suffix on a single
enveloped report causes a full fallback, including when followed by blank lines.
Different blocks, including blocks differing only in usage, also cause full
fallback. Exactly identical blocks containing telemetry keep that telemetry in
the retained first block.

This is intentionally conservative: real CLI usage may remain in uncertain
outputs. For the audit's legitimate-body-usage probe, even `HEADER` is retained;
returning the complete original satisfies fail-open without partially deleting
an ambiguous envelope. The tab-indented probe still removes the proven header
and preserves the entire report/code body.

**New tests:** `test_tab_indented_body_code`,
`test_indented_usage_code_preserved`, `test_legitimate_body_usage_data`,
`test_identical_body_telemetry_preserved_with_envelope`,
`test_unconfirmed_usage_on_different_copies_falls_back`,
`test_usage_difference_is_not_a_duplicate`,
`test_inline_usage_difference_is_not_a_duplicate`,
`test_ambiguous_usage_with_trailing_whitespace_falls_back`, and
`test_recovery_preserves_ambiguous_usage`.

## 3. P2: comparison of blocks that were not byte-identical

**Root cause:** `_core_end()` removed whitespace-only boundary lines, and
`.rstrip("\r\n")` removed terminal EOL differences before comparison. The old
documentation explicitly permitted those exceptions despite the exact-byte goal.

**Fix:** compare complete original block slices from each matching anchor to the
next anchor or EOF. No boundary trimming, usage stripping, or newline
normalization occurs before comparison. Any difference returns the full original
input. Exact copies retain the first block's entire final EOL and boundary
whitespace. `CLEANING.md` now states this strict rule and defines block boundaries.

**New tests:** `test_nonidentical_final_eol`,
`test_nonidentical_missing_final_eol`, `test_nonidentical_boundary_whitespace`,
`test_identical_boundary_whitespace_is_preserved`,
`test_footer_confirmation_does_not_ignore_final_eol`,
`test_usage_difference_is_not_a_duplicate`, and
`test_inline_usage_difference_is_not_a_duplicate`.

## 4. P2: short CLI errors accepted as successful reports

**Root cause:** changing recovery success from a length gate to nonempty content
removed the old rejection of short CLI failures without adding diagnostic
recognition. `Error: authentication failed.` therefore bypassed the backup.

**Fix:** `_recovery_error()` recognizes leading explicit `Error:`/`fatal:`
diagnostics (including supported `codex:` prefixes) and Python traceback headers,
outside protected code, either at the first nonblank line or after a complete
header and optional `codex` role. Recovery returns `success=False` with the
bounded diagnostic, allowing unchanged `main()` to choose `aqi_fallback`.
Detection is based on content, not length. Valid short and unknown non-error
reports remain accepted; mentioning an error later in report prose is allowed.
The cleaner itself still returns raw error text unchanged when it cannot clean it.

**New tests:** `test_short_error_recovery_fails`,
`test_cli_error_detection_is_content_based`,
`test_report_discussing_cli_errors_is_accepted`, and
`test_short_error_recovery_engages_fallback_channel`.
Existing `test_short_clean_report_not_lost` and
`test_unknown_recovery_returns_raw` also continue to verify short-report success.

## 5. P3: non-whitespace threshold counted internal whitespace

**Root cause:** summing `len(line.strip())` counted internal spaces/tabs, so a
body with two actual characters and 18 spaces met the claimed 20-character rule.

**Fix:** count `not character.isspace()` outside protected code after the title.
At least 20 such body characters are required for the short-title alternative;
the four-section template alternative is unchanged. Documentation now specifies
the counting operation and distinguishes cleaning validation from recovery's
length-independent acceptance policy.

**New tests:** `test_short_threshold_ignores_internal_whitespace` reproduces the
exact audit input and directly checks validation plus preservation of repeated
input. `test_short_threshold_exact_boundary` checks 19 versus 20 actual
characters separated by internal spaces/tabs.

## Every requested audit probe is a regression

`AUDIT_REPORT` uses the audit's exact report fixture. These tests reproduce all
seven failed preservation probes, the short-error recovery, and the threshold.

| Audit probe | Regression test |
| --- | --- |
| `bare_codex_is_real_prelude` | `test_bare_codex_is_real_prelude` |
| `unwrapped_real_metadata` | `test_unwrapped_real_metadata` |
| `four_space_code_metadata` | `test_four_space_code_metadata` |
| `nonidentical_final_EOL` | `test_nonidentical_final_eol` |
| `nonidentical_missing_final_EOL` | `test_nonidentical_missing_final_eol` |
| `tab_indented_body_code` | `test_tab_indented_body_code` |
| `legitimate_body_usage_data` | `test_legitimate_body_usage_data` |
| Short authentication error | `test_short_error_recovery_fails` and `test_short_error_recovery_engages_fallback_channel` |
| Two characters plus 18 internal spaces | `test_short_threshold_ignores_internal_whitespace` |

The audit's passing fenced-example and repeated-paragraph cases remain covered
by the existing fenced-code and legal-repeated-paragraph tests.

## Adjustments to original tests

All **50 original test methods remain**; 29 new methods bring the total to 79.
The following 11 original methods encoded unsafe prefix/footer deletion and were
adjusted. No original method was removed. The original idempotence assertions
are unchanged, including their now-expected fallback warnings.

| Original test | Adjustment and reason |
| --- | --- |
| `test_loose_header` | Metadata-only prefix now asserts full fallback (finding 1). |
| `test_header_and_footer` | Header plus standalone usage now asserts full fallback (finding 2). |
| `test_inline_usage_footer` | Inline standalone usage now asserts full fallback (finding 2). |
| `test_usage_between_duplicates` | Usage makes original blocks different; now asserts full fallback (findings 2–3). |
| `test_date_anchor_from_template` | Keeps successful date-anchor coverage with exact copies; original usage-between-copies input now asserts fallback (findings 2–3). |
| `test_triple_duplicate` | Exact triple still deduplicates; the original input with trailing usage now asserts fallback (findings 2–3). |
| `test_numbers_wording_spaces_exact` | Exact formatted copies still deduplicate unchanged; original trailing-usage input now asserts fallback (findings 2–3). |
| `test_crlf_exact` | Exact CRLF copies still deduplicate; original usage-between-copies input now asserts fallback (findings 2–3). |
| `test_deletion_only_and_idempotence_across_formats` | Success matrix uses exact copies/proven headers. Original between-copy and standalone usage forms each explicitly assert fallback in every format (findings 2–3). |
| `test_recovery_cleans_before_return` | Success fixture now uses exact copies without intervening usage; new `test_recovery_preserves_ambiguous_usage` verifies the original raw fixture is delivered unchanged (findings 2–3). |
| `test_model_reasoning_and_json_delivery` | Uses exact copies without intervening usage to test successful cleaning and unchanged model/reasoning/JSON behavior. Ambiguous-usage recovery is covered separately (findings 2–3). |

## Actual validation

2026-10-22 UTC, Python 3.10.12, in this worktree. The original suite was run
before changes: **50 tests passed**. After the fixes the requested command was
run from the worktree root:

```bash
cd report-cleaning && python3 -B -m unittest test_report_clean
```

Exit status: **0**. Actual combined output:

```text
....................................[REPORT_CLEAN_FALLBACK] reason=ValueError:nonidentical_anchored_blocks
[REPORT_CLEAN_FALLBACK] reason=ValueError:nonidentical_anchored_blocks
[REPORT_CLEAN_FALLBACK] reason=ValueError:ambiguous_usage_footer
[REPORT_CLEAN_FALLBACK] reason=ValueError:ambiguous_usage_footer
...........................................
----------------------------------------------------------------------
Ran 79 tests in 0.273s

OK
```

The four warnings are expected from the unchanged original idempotence test's
ambiguous inputs; its first and second calls each return the identical raw input.
The final suite has **66 cleaner tests and 13 mocked recovery/JSON tests**.

Additional checks passed:

- `git diff --check`.
- `bash -n report-cleaning/run_codex.sh`.
- AST parsing of both Python files.
- AST comparison against `HEAD`: `ssh_run`, `fetch_market_data`, `build_prompt`,
  and `main` are unchanged.
- AST inventory confirms all 50 original test names are retained.
- `AUDIT_VERDICT.md` SHA-256 is unchanged from the initial read:
  `4e2bad675915d6d97a59440947471f48dc16311feb9f1afc8f245d33e90520f1`.
- `run_codex.sh` SHA-256 is unchanged:
  `51abb5c8b5a92b3d89d14fb697c466a02a89006c5af99c863a7d1b44a49744c8`.

Only `dual_channel.py`, `test_report_clean.py`, `CLEANING.md`, and this
`FIX_NOTES.md` were written, all inside `report-cleaning/`. The pre-existing
untracked audit verdict was only read. No live scheduler, SSH/SCP, Codex, market
data, or message delivery was run. No commit, push, PR, or deployment was made.
