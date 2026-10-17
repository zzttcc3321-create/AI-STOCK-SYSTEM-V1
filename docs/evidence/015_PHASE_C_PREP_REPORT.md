# 015 Phase C engineering prep — commander contracts implemented

```text
TASK_ID=AI-STOCK-V1-015-RUNTIME-INITIALIZATION-PREP-RESUME
RESUME_COMMIT=e1a1f71
BRANCH=feat/015-phase-c-prep
SCRIPT_IMPLEMENTATION=COMPLETE
SCRATCH_DB_REHEARSAL=PASS
PHASE_C_TESTS=65_PASS
FULL_TEST_SUITE=1135_PASS
REVIEW_B=REQUIRED_PENDING
REAL_DB_WRITE=NOT_STARTED
REAL_DB_CONNECTION=NOT_STARTED
```

The commander supplied all three storage contracts, replacing the blockers
recorded in `e1a1f71`. Work continued on the existing branch. Every database
execution for this task used synthetic SQLite databases under `/tmp` or in
memory. No real Windows fact DB, selector, holding data, provider, LLM, scheduler,
or remote DB connection was accessed. GitHub delivery is separate from the
offline script runtime.

## Review A — implementation facts

`CHANGE_SCOPE` and `CODE_DIFF_SUMMARY` are listed below.
`STATIC_ANALYSIS_RESULT=PASS`: all six Python files parse, and staged/unstaged
`git diff --check` is clean. `TEST_RESULT` is recorded in the verification section.

| File | Responsibility |
| --- | --- |
| `tools/015_04_security_master_import.py` | Authorized insert-or-skip full security snapshot import. |
| `tools/015_05_holding_config_write.py` | Authorized atomic full replacement of holdings and three metadata rows. |
| `tools/015_06_dashboard_seed.py` | Authorized deterministic in-DB projection and append-only batches. |
| `tools/phase_c_safety.py` | CLI, path/auth/hash gates, memory rehearsal, SQL authorizer, snapshots, transactions, rollback and schema verification. |
| `tools/phase_c_contracts.py` | Canonical columns/DDL, whole-file validation, transformations and post-write checks. |
| `tests/test_015_phase_c_prep.py` | Synthetic contract, safety, rollback and privacy regressions. |
| `.gitignore` | One exact exception for the holding script; data, CSVs, DBs, snapshots and logs remain ignored. |

No existing runtime module, frozen schema, migration or production data was
changed. The prep tools do not call runtime factories or persistence services.
They reuse the C01-fixed `require_safe_directory()` for directory validation
and every backup-directory creation.

## Contract binding and Review B

Table and field names are **canonical per commander**, not inferred mappings to
the historical repository schema. **Review B must bind these names, constraints,
PKs, defaults, indexes and triggers against the real schema before any later
real execution.** This report does not claim that binding has occurred.

- `security_master` is assumed to exist in the baseline. Script code never
  issues its CREATE; only synthetic fixture setup creates it. Missing tables
  fail even in dry-run. Schema validation checks columns, defaults, required
  CHECK constraints, composite PK and absence of extra unique constraints.
  SQL comments cannot impersonate CHECK constraints.
- The only schema additions are the four supplied tables, two per step below.
  Their CREATE statements are verbatim in `DDL_BY_STEP`. **Review B must approve
  these additions.** Existing tables must match the canonical definitions; the
  scripts do not migrate them. The schema inventory/fingerprint verifier permits
  only new canonical tables and their implicit PK indexes and preserves every
  pre-existing schema object.
- 015-04 cannot delete or update securities. A target key absent from the full
  source snapshot therefore fails the count requirement before live writes.
  Changed fields and duplicate keys fail the batch. Post-write field comparison
  covers every row, exceeding the requested spot check. Blank currency becomes
  `CNY`; blank list date becomes SQL NULL. Names need not be unique.
- 015-05 applies the explicit SKIP rule to normalized PK/quantity/cost data and
  `as_of_date`. Reordered/reformatted identical data does not write metadata:
  the committed source SHA remains provenance of the last applied snapshot.
  A replacement stores the new source SHA and exactly `as_of_date`,
  `version=1.0`, and `source_sha256`, removing obsolete metadata. Empty sources
  clear holdings. Missing/delisted securities fail before DDL.
- 015-06 derives batch IDs only from stored date/SHA. Existing batches are
  verified and skipped; new batches use the ordered composite-key join and
  preserve older batches. New snapshot counts equal holding counts; overview
  totals match both exact integer-cent arithmetic and SQLite's rounded SUM.
  Empty holdings create one zero overview and no snapshots. Latest-batch ordering
  is `ORDER BY as_of_date DESC, batch_id DESC LIMIT 1`; no dashboard UI/read
  model is changed by this seed-only task.
- Monetary arithmetic uses integer cents and checks decimal-cent round trips
  through SQLite REAL. Values SQLite cannot represent/aggregate to the required
  cents fail rather than silently round. Integers must fit signed 64-bit storage.

Validation covers local POSIX dev-box paths and rollback-journal SQLite files.
Symlinks, reparse points, hard links, repository runtime paths, network-style
paths, WAL-format DBs and existing SQLite sidecars are refused. Windows
path/platform binding remains part of later reviewed execution; this prep does
not authorize access to that machine.

## Per-step whitelist

| Step / exact `--auth-flag` | Business tables read | Permitted mutations | Permitted DDL |
| --- | --- | --- | --- |
| 015-04 / `SECURITY_MASTER_IMPORT=YES` | `security_master` | `security_master`: INSERT only | None |
| 015-05 / `HOLDING_CONFIG_WRITE=YES` | `security_master`, `holding_config`, `holding_config_meta` | `holding_config`: DELETE/INSERT; `holding_config_meta`: DELETE obsolete keys, INSERT/UPSERT | Verbatim CREATE IF NOT EXISTS for `holding_config`, `holding_config_meta` only |
| 015-06 / `DASHBOARD_INITIAL_DATA_WRITE=YES` | `security_master`, `holding_config`, `holding_config_meta`, both dashboard tables | Both dashboard tables: INSERT only | Verbatim CREATE IF NOT EXISTS for `dashboard_position_snapshot`, `dashboard_overview` only |

Schema inspection and integrity checks are allowed. Trigger/view-origin
operations, unlisted tables, ATTACH, arbitrary DDL, time/random functions and
unapproved PRAGMAs are denied. Exact-statement DDL authorization, SQLite's
operation authorizer and independent schema-delta verification all apply.

The filesystem write whitelist is the explicit absolute `--target-db`, its
transient SQLite rollback journal, and the verified preimage
`<backup-dir>/<step>-<expected-db-sha256>.sqlite`. Missing backup-directory
components are created through the safe-directory helper. Sources are read-only;
scripts create no config, report, log, provider or LLM artifacts. Diagnostics
contain fixed status/error codes, aggregate row counts, date and SHA only,
never source paths, symbols, names, quantities or costs.

Every invocation requires the exact auth value and target SHA256. Wrong/missing
authorization or hash refuses before writes or directory creation. Each script
rehearses the operation in memory, then locks the target with `BEGIN EXCLUSIVE`,
rechecks identity/hash, verifies a byte-identical pre-write snapshot, performs
one transaction, verifies data/schema/integrity and commits. Failures roll back
DDL and DML together and verify the restored target SHA. No step runner
continues after failure.

`--dry-run` performs validation, DDL and projection only in the memory clone,
then rolls it back. It never opens the target writable or creates backups.
Reports say `DRY_RUN_VALIDATED`, `changed=0`, with planned counts separately
named. Identical repeats report `IDEMPOTENT_NOOP`, `changed=0`, without a new
snapshot.

## Scratch rehearsal evidence — 2026-10-17

Fresh root: `/tmp/015-phase-c-prep-resume-20261017-321999`. The harness used the
synthetic fixtures from `tests/test_015_phase_c_prep.py`. Only fixture setup
created baseline `security_master` in this scratch DB. CSVs, snapshots and
restore DBs remain outside Git; no position details appear in this evidence.

Each CLI call used absolute `--target-db`, `--backup-dir` and `--source` paths
(where applicable), the exact flag, and a freshly calculated
`--expected-db-sha256`. 015-05 used `--as-of-date 2026-10-16`. Each step ran
dry-run, scratch apply and identical repeat before advancing.

| Step | Dry-run status / actual changed | Validated rows | Scratch apply | Repeat |
| --- | --- | --- | --- | --- |
| 015-04 | DRY_RUN_VALIDATED / 0 | 2 | PASS, inserted=2 | IDEMPOTENT_NOOP, skipped=2, changed=0 |
| 015-05 | DRY_RUN_VALIDATED / 0 | 2 | PASS, inserted=2, deleted=0 | IDEMPOTENT_NOOP, changed=0 |
| 015-06 | DRY_RUN_VALIDATED / 0 | 2 | PASS, changed=3 | IDEMPOTENT_NOOP, changed=0 |

Before/after dry-run target SHA256 pairs were identical:

```text
015-04=cce577054698840aafbbd70258a4eab1daf088ce8c84e1f49772addb3789f950
015-05=e622fbe3268c6343921e62a730cd7314eac012303358fa39dc01ee07a37730f7
015-06=3f46e35d48a85c86f471ae0fffa1d9f2a4614b6737991ff12438ad3b83b36382
MASTER_SOURCE_SHA256=7ab83dc8e25effc40bc90815f9d4540e59ee6c8a73ad12118f597efa34424463
HOLDING_SOURCE_SHA256=cf602fb1362fa1bf572015d698088a7fa97760921deb40e29ef5976b48680362
AS_OF_DATE=2026-10-16
DRY_RUN_DIRECTORY_INVENTORY_UNCHANGED=PASS_ALL_3
REPEAT_DIRECTORY_INVENTORY_UNCHANGED=PASS_ALL_3
PREWRITE_SNAPSHOT_HASH_MATCH=PASS_ALL_3
RESTORED_SNAPSHOT_HASH_AND_INTEGRITY=PASS_ALL_3
REAL_DB_WRITE=NOT_STARTED
REAL_DB_CONNECTION=NOT_STARTED
```

For each apply, the harness compared the snapshot to the pre-write hash, copied
it to a separate scratch restore DB, and checked its hash and
`PRAGMA integrity_check`. Every dry-run/repeat comparison covered all scratch
directory entries and file hashes, not just the target DB.

## Verification and remaining review

The 65 Phase C tests cover exact auth/hash gates; required/absolute/unsafe paths;
CSV header, enum, empty, quantity, decimal, duplicate and conflict cases;
canonical schema and DDL fences; replacement/empty holdings; ordered exact
projection/empty dashboards; idempotency; DB changes between rehearsal and apply;
dry-run with no filesystem changes; no network/LLM calls; redacted success and
failure; pre-write snapshots; and restoration after injected live DML, replace
and DDL failures. Trigger writes and spoofed CHECK comments are rejected.

```console
$ PYTHONDONTWRITEBYTECODE=1 python3 -m unittest tests.test_015_phase_c_prep
Ran 65 tests in 27.254s
OK
$ PYTHONDONTWRITEBYTECODE=1 REQUIRE_STREAMLIT=1 python3 -m unittest discover -s tests -v
Ran 1135 tests in 596.014s
OK
$ git diff --check
(clean)
```

`IMPACT_ANALYSIS`: new offline prep tools only; no live operational change.
`RISK_FOUND`: physical schema compatibility and Windows execution are
unverified; the two per-step table additions require Review B approval.
`REVIEW_B=REQUIRED_PENDING`; these are code facts, not an approval. PR #49
remains draft. Real execution is a separate reviewed action requiring fresh
per-step authorization. `REAL_DB_WRITE=NOT_STARTED`.
