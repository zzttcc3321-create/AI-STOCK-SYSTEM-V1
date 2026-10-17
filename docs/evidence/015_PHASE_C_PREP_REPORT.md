# 015 Phase C engineering prep — stopped on missing contracts

```text
TASK_ID=AI-STOCK-V1-015-RUNTIME-INITIALIZATION-PREP
BASE_COMMIT=61597e8a9273c0384b5ff393687bef672589bb55
BRANCH=feat/015-phase-c-prep
PREP_STATUS=BLOCKED_MISSING_STORAGE_CONTRACTS
REAL_DB_WRITE=NOT_STARTED
REAL_DB_CONNECTION=NOT_STARTED
SCRIPT_IMPLEMENTATION=NOT_STARTED
SCRATCH_DB_REHEARSAL=NOT_STARTED
```

This report records a fail-stop during repository inspection on Linux. No real
database was located, connected to, or modified. No Windows database path was
used, and no attempt was made to reach that machine. No holding data was read,
generated, logged, or committed. No database, config, backup, or runtime output
was created. The requested three scripts and their new tests have **not** been
implemented; this is a blocker report, not completion evidence.

The task explicitly requires: “If any requirement is ambiguous or conflicts
with a safety rule, STOP, document the conflict in the report, and do not guess.”
That instruction governs this stop. The current task authorizes engineering prep
and synthetic scratch writes; older documents' historical restrictions are not
being treated as a reason to request that authorization again. The unresolved
issue is the storage contract needed to implement the requested writes safely.

## Findings requiring resolution

| Step | Repository evidence | Unresolved contract and safety consequence |
| --- | --- | --- |
| 015-04 securities master import | [DB Schema V1.0 §2.2](../frozen/DB_SCHEMA_V1.0.md#22-stocks) lists logical stock fields but says no physical securities master was identified. [V1.1](../frozen/DB_SCHEMA_V1.1.md) explicitly excludes adding `stocks`; its [table definitions](../../persistence/schema_v1_1.py) and [migration definition](../../migration/003_db_schema_v1_1_additive.sql) add only seven analysis/evidence tables. [Phase A capability mapping](015_RUNTIME_INITIALIZATION_RUNBOOK.md#能力映射与复用) implements input validation only. | No established existing target table, source-to-column mapping, or PK/unique contract for this import was found. Creating a `stocks`/`security_master` table would violate this task's no-schema-change rule. Choosing an unrelated legacy table or inventing a scratch-only schema would not establish compatibility with the later Windows execution. |
| 015-05 holding config write | [Phase A 015-05](015_RUNTIME_INITIALIZATION_RUNBOOK.md#015-05--需-qi-单独授权默认不执行) validates `stock_code/shares/cost/date` and explicitly does not persist them. [Analysis package input contract](../ANALYSIS_PACKAGE_007.md) instead accepts `stock_id/quantity/cost_price/snapshot_time`; it does not define a config-file carrier. | The source and config document shapes, stock identity/date semantics, record key, and merge/replacement policy are unspecified. Choosing these could overwrite unrelated config or silently change holding semantics. A config-file implementation is allowed by the task, but its contract still needs to be supplied. |
| 015-06 dashboard seed | [Phase A 015-06](015_RUNTIME_INITIALIZATION_RUNBOOK.md#015-06--需-qi-单独授权默认不执行) writes only `reports/dashboard.html`, containing check status and table count. [Dashboard runtime](../DASHBOARD_RUNTIME_014_R1.md) requires an existing Schema B DB and canonical Evidence; it defines no dashboard seed table or transformation. | The seed destination and the mapping from existing DB records to seed records are unspecified. Selecting analysis/evidence tables would risk fabricating analysis or confirmation records; introducing a seed table would violate the schema rule. The existing static HTML output cannot be assumed to satisfy the new seed-data request. |

These findings describe available repository contracts, **not** an inspection of
the real database. A later schema may exist outside the repository; none was
assumed or sought on the Windows machine.

## Whitelist and output status

No execution whitelist has been invented or activated.

| Step | Writable tables | Writable paths |
| --- | --- | --- |
| 015-04 | Unresolved; no table writes permitted during this stop | Future explicit `--target-db` and snapshots in `--backup-dir`; no paths selected or opened |
| 015-05 | None if the config-file option is confirmed; DB alternative unresolved | Future explicit `--config-file` and its snapshots; carrier format unresolved and no paths selected or opened |
| 015-06 | Unresolved; no table writes permitted during this stop | Seed destination unresolved; no paths selected or opened |

The only task artifact is this report. Git commit/push and a draft PR are delivery
operations authorized by the task, separate from script runtime operations.
No providers, LLMs, schedulers, startup code, or runtime factories were invoked.

## Authorization and safety specification retained for implementation

| Script | Required exact `--auth-flag` value |
| --- | --- |
| `tools/015_04_security_master_import.py` | `SECURITY_MASTER_IMPORT=YES` |
| `tools/015_05_holding_config_write.py` | `HOLDING_CONFIG_WRITE=YES` |
| `tools/015_06_dashboard_seed.py` | `DASHBOARD_INITIAL_DATA_WRITE=YES` |

Each invocation, including dry-run and any retry, must require a freshly
reconfirmed flag. Missing/wrong flags must refuse with the expected flag name,
return nonzero, and change nothing. Flags must not be remembered as continuing
authorization. Re-import must be idempotent; any non-idempotent repeat must refuse
unless `--force-reaffirm` and the exact flag are both supplied.

After the storage contracts are resolved, implementation must enforce absolute
paths, explicit table/path whitelists, expected target DB SHA256 before writes,
verified pre-write snapshots, unchanged schema fingerprint after writes, and
rollback/restoration on failure. Directory creation must use the existing
C01-fixed `require_safe_directory()` in `tools/015_runtime_init.py`. A config-file
write must snapshot the existing carrier first. Dry-run must validate the entire
operation and leave files and directories unchanged. Planned holding writes and
evidence may expose only row counts and digests, never holding details. All
script operations must be offline with no LLM or scheduler activity.

These are requirements, not claims of implemented or tested behavior.

## Inspection and verification evidence

Initial repository checks:

```console
$ git status --short
(empty)
$ git branch --show-current
feat/015-phase-c-prep
$ git rev-parse HEAD
61597e8a9273c0384b5ff393687bef672589bb55
$ git rev-parse main
61597e8a9273c0384b5ff393687bef672589bb55
```

The requested branch already existed at the exact requested main baseline;
no reset or branch replacement was needed. Read-only inspection covered the
Phase A/B tools and evidence, frozen V1.0/V1.1 schema, compatibility mapping,
the additive migration, persistence utilities, and dashboard code/docs. An
independent read-only audit confirmed all three missing contracts, including
V1.1's explicit exclusion of `stocks`. No code was executed against a database.

```text
015-04_DRY_RUN=NOT_RUN_BLOCKED
015-05_DRY_RUN=NOT_RUN_BLOCKED
015-06_DRY_RUN=NOT_RUN_BLOCKED
NEW_TESTS=NOT_IMPLEMENTED_BLOCKED
TEST_SUITE=NOT_RUN_DOCUMENTATION_ONLY_STOP
DIFF_CHECK=PASS
```

There are no execution commands/outputs to present as dry-run evidence. No
scratch SQLite schema was invented to make tests pass. Required future tests
remain: wrong/missing authorization, non-whitelisted writes (including indirect
writes), duplicate-free repeat import, unchanged dry-run state, snapshot recovery
after simulated failure, holding redaction, and detection of undeclared files.

## Information needed to resume

Supply non-sensitive contracts only; no real DB, real selector, or holding data
is needed on this machine:

1. **015-04:** existing destination table DDL (columns, constraints, indexes,
   relevant triggers), offline input format and exact field mapping, record key,
   and policy for an existing key with changed values. If no compatible table
   exists, resolve that in a separately authorized schema task or revise this
   task's scope; these scripts must never create or migrate it.
2. **015-05:** source/config structure, identifier and date semantics, record key,
   and merge versus replacement rules, including preservation of unrelated
   config. Confirm the file carrier or specify an existing DB mapping.
3. **015-06:** exact seed destination tables or file and deterministic projection
   from existing DB data, including empty-input and repeat-run behavior.

Once those contracts are supplied, scratch fixtures can reproduce them under
`/tmp`, using obviously synthetic data, and implementation/testing can resume.
Real execution remains a separate action by qi on Windows with per-step
authorization.
