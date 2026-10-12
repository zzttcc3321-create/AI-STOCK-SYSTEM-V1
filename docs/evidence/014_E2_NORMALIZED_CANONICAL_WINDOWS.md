# AI-STOCK-V1-014-E2-NORMALIZED-CANONICAL-WINDOWS

Baseline: `fd015578915c5f3f86a3be7a20f4cfa81a0cdcc5`.

The [frozen archive contract](../frozen/EVIDENCE_ARCHIVE_SIDECAR_V1.0.md)
requires separate content, identity, hash, durability and readback verification
for Raw, Normalized and Canonical Evidence. `ArchiveReader.read()` verifies Raw
only; valid Raw does not certify either appended stage.

`tests/test_normalizer.py` covers market data normalization.
`tests/test_information_normalizer.py` covers synthetic information envelopes,
sealed normalization results, canonical hashes and rejection of inconsistent
inputs. `tests/test_information_archive_roundtrip.py` covers synthetic archive
and temporary SQLite projection roundtrips. Existing Windows Phase C verifies
Provider/Raw and request binding, but never executes information normalization
or verifies Normalized and Canonical events on real Windows data.

The focused runner `tools/e2_windows_normalized_verify.py` uses the existing
provider to collect fresh announcement, news, research and three financial
statement responses. It reads the exact archived business response, runs
`InformationNormalizer.normalize()` twice to check determinism, builds a new
Canonical Evidence identity, appends both stages, and reads each back through
an independent `ArchiveReader`. Full normalized content and canonical records
must match. Raw bytes and metadata must remain unchanged after appending.
Every stage reports its own sealed sidecar hash and actual durability level;
the Canonical event also reports its record hash and links to Raw/Normalized.

Before invoking the Normalizer for `tdx_api_data`, the runner applies the same
fail-closed financial dual-request checks as Phase C `_verify_call`. The public
request passed to this run's `fetch()` supplies the expected view, tool and
stock; the archive never supplies the expected view. `semantic_request` must
equal that exact semantic request, `provider_request` must equal both the exact
expected wire request and the top-level method/stock_id/params, and wire
arguments must equal the independent `FINANCIAL_VIEW_ARGUMENTS` imported from
`tools.e2_windows_verify` (including code and fixedTag). Missing context, missing
trace or any mismatch yields `FAIL` with
`FINANCIAL_SEMANTIC_MAPPING_MISMATCH` before normalization. Expectations are not
derived from the Provider translation table.

Provider code and Phase A/B/C are unchanged.
The runner opens no database (`REAL_DB_WRITE=NO`), performs no migration and
stores no credentials or exception messages. Business content remains in the
isolated archive; the report contains only identities, hashes and check results.

On the real Windows machine, use Python 3.12+ (the inherited directory guard
requires `Path.is_junction()`), the existing environment credential setup and
a **new** output directory outside the repository and historical E2 roots:

```powershell
python tools/e2_windows_normalized_verify.py --output-dir D:\normalized014-new-run --stock-id 601518.SH --date-from 20260911 --date-to 20261011
```

Choose dates appropriate to the verification run. A category with empty
canonical content is `NOT_AVAILABLE_ACCEPTED` only after Raw archive integrity,
readback, exact response and request binding checks pass and the Normalizer
confirms legal Provider `NOT_AVAILABLE` status consistent with the response.
It is neither a content PASS nor a failure. No Normalized or Canonical event is
created; those two stages remain `verified=false`. Invalid Raw, binding, status
or financial mappings still fail closed. Exceptions report only their type and
a fixed error code, never exception text.

The report distinguishes these acceptance scopes:

- `PASS` / `verification_scope=FULL`: all six categories have nonempty content
  and all three stages verified, with explicit durability for each event.
- `LIMITED_PASS` / `verification_scope=LIMITED`: announcement and all three
  financial statements have complete nonempty PASS chains. News and research
  are each either PASS or `NOT_AVAILABLE_ACCEPTED`, with at least one accepted
  empty category. `verified_categories` lists only content PASS categories;
  `limited_scope` separately maps each empty category to
  `NOT_AVAILABLE_ACCEPTED`. Four successful chains plus two accepted empty
  responses certify four content categories, never six.
- `INCOMPLETE` / `verification_scope=UNVERIFIED`: a required core category is
  empty or a call remains incomplete. Any failed call makes the overall result
  `FAIL`. Missing auth produces `BLOCKED_AUTH`.

Exit codes are 0 for FULL PASS or LIMITED_PASS, 1 for FAIL and 2 for
incomplete/blocked execution. Exit 0 alone does not certify full scope or
Windows execution. `windows_real_chain_verified=true` requires actual Windows
execution (`sys.platform=win32`) and FULL PASS. LIMITED_PASS always leaves that
flag false; `windows_limited_chain_verified=true` identifies a qualified LIMITED
scope on Windows. Both flags remain false on Linux. Preserve the entire output
directory, including `archive/` and `normalized_verify_report.json`, and audit
the scope, category lists and stage results together with these flags.
`FILE_SYNC_ONLY` verifies the file durability contract; it does not certify
directory durability after power loss.

The new tests are offline and use synthetic responses with actual filesystem
archives. Linux test success does not certify Windows REAL execution. That
execution and its resulting archive/report remain pending Windows-side run.

Original implementation validation: the 16 offline tests passed. Full regression using
`REQUIRE_STREAMLIT=1 PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover -s tests -v`
passes **902/902** tests (886 baseline + 16 new), with zero failures, errors or
skips, in 366.921 seconds. No real database was written.

Review B rework adds 13 offline regression tests (`B01NoDataAcceptanceTests`
and `B02FinancialSemanticMappingTests`). They verify full and limited acceptance,
rejection of invalid empty responses, Windows/Linux report flag and exit-code
semantics, and intact archives with contradictory or missing financial request
traces. All 13 pass; all business calls use mocks and local archives.
The focused module passes **29/29** tests. Full rework regression using
`env -u TDX_MCP_API_KEY REQUIRE_STREAMLIT=1 PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover -s tests`
passes **915/915** tests (902 existing + 13 rework), with zero failures, errors
or skips, in 365.990 seconds. `git diff --check` passes. No real endpoint or
Windows machine was invoked, no real database was written, and
`REAL_DB_WRITE=NO` remains unchanged.
