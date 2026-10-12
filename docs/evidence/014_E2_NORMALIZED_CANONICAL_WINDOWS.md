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

Provider code, Phase A/B/C and closed financial binding checks are unchanged.
The runner opens no database (`REAL_DB_WRITE=NO`), performs no migration and
stores no credentials or exception messages. Business content remains in the
isolated archive; the report contains only identities, hashes and check results.

On the real Windows machine, use Python 3.12+ (the inherited directory guard
requires `Path.is_junction()`), the existing environment credential setup and
a **new** output directory outside the repository and historical E2 roots:

```powershell
python tools/e2_windows_normalized_verify.py --output-dir D:\normalized014-new-run --stock-id 601518.SH --date-from 20260911 --date-to 20261011
```

Choose dates appropriate to the verification run. Empty responses produce
`INCOMPLETE`, never a content PASS; adjust the range and use another fresh
directory if necessary. Missing auth produces `BLOCKED_AUTH`. Exit codes are
0 for PASS, 1 for FAIL and 2 for incomplete/blocked execution. Preserve the
entire output directory, including `archive/` and `normalized_verify_report.json`.
Acceptance requires all six calls and all three stages verified, explicit
durability for each event and `windows_real_chain_verified=true`.
`FILE_SYNC_ONLY` verifies the file durability contract; it does not certify
directory durability after power loss.

The new tests are offline and use synthetic responses with actual filesystem
archives. Linux test success does not certify Windows REAL execution. That
execution and its resulting archive/report remain pending Windows-side run.

Local validation: the 16 new offline tests pass. Full regression using
`REQUIRE_STREAMLIT=1 PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover -s tests -v`
passes **902/902** tests (886 baseline + 16 new), with zero failures, errors or
skips, in 366.921 seconds. No real database was written.
