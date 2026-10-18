# 015 Phase C engineering prep — canonical DDL fix

```text
TASK_ID=AI-STOCK-V1-015-PHASE-C-DDL-FIX
RESUME_COMMIT=ff2487a
BRANCH=feat/015-phase-c-prep
SECURITY_MASTER_CONTRACT_BINDING=PASS_8_OF_8
SECURITY_MASTER_BASELINE_EXISTENCE=ABSENT_IN_32_TABLE_INVENTORY
CREATE_SHA256_ENFORCEMENT=IMPLEMENTED_ALL_5
HASH_HANDOFF=IMPLEMENTED
PHASE_C_TESTS=76_PASS
FULL_TEST_SUITE=1148_PASS
REVIEW_B=REQUEST_CHANGES_UNRESOLVED
REAL_DB_WRITE=NOT_STARTED
REAL_DB_CONNECTION=NOT_STARTED
```

## Canonical DDL fix — 2026-10-18

Continued from `ff2487a` on the existing branch. The commander voided the prior
security CREATE and supplied `~/prompts/security_master_ddl_canonical.sql` as
the only valid source. Its complete 458 bytes, including the final LF, were
read programmatically into the 015-04 DDL whitelist without retyping or
reformatting. The source file was not modified. Its locked SHA256 is
`20ee80403e222acb544790e8df9cea8bad6143777998a89c4c5320fd1c661565`.

The supplied `DB_EXISTING_SCHEMA_INVENTORY_V1.json` has 32 unique table entries
matching its declared count and contains no `security_master` table. The
canonical eight fields match the import contract 1:1. This resolves the earlier
missing-evidence blocker for this task using offline artifacts; no real DB
connection or new real schema measurement was performed. The
[schema binding ledger](015_PHASE_C_SCHEMA_BINDING.md) records source provenance,
field types/defaults/nullability/PK positions, CHECK constraints and all five
CREATE hashes. The other four CREATE strings were compared with `ff2487a`
and remain byte-for-byte unchanged.

015-04 creates a missing `security_master` with the whitelisted CREATE in the
same transaction as the import. An existing table is schema-validated before
use, preserving repeat no-ops despite the canonical SQL having no
`IF NOT EXISTS`. An empty snapshot still commits table creation. A failed
transaction rolls back both CREATE and INSERT and verifies exact preimage bytes.
Dry runs rehearse CREATE/import only in a memory clone.

Independent SHA256 pins cover all five CREATE statements. The contract CREATE
helper, guarded connection and memory schema verifier check exact UTF-8 text
before execution. A single-character change, including otherwise valid SQL
whitespace or a removed final LF, fails with `CREATE_SHA256_MISMATCH` before
the affected CREATE executes. The exact SQL whitelist and SQLite authorizer
continue to enforce the per-step table/operation limits.

## Retained runtime boundaries

These are offline engineering prep scripts. Database exercises use synthetic
SQLite databases under `/tmp` or in memory. No real Windows fact DB, selector,
real holding data, provider, LLM, scheduler or remote DB connection was accessed.
GitHub delivery is separate from the script runtime. Diagnostics contain fixed
codes, aggregate counts, dates and hashes; source paths and holding details
are never emitted or recorded in evidence.

| Step / exact authorization | Permitted mutations | Permitted CREATE |
| --- | --- | --- |
| 015-04 / `SECURITY_MASTER_IMPORT=YES` | Security snapshot INSERT or skip; conflicts fail | Canonical `security_master` only, when absent |
| 015-05 / `HOLDING_CONFIG_WRITE=YES` | Atomic holding replacement and three metadata keys | Unchanged `holding_config`, `holding_config_meta` |
| 015-06 / `DASHBOARD_INITIAL_DATA_WRITE=YES` | Deterministic append-only dashboard batch | Unchanged `dashboard_position_snapshot`, `dashboard_overview` |

Every invocation requires an exact auth flag, explicit safe absolute paths and
`--expected-db-sha256`. The scripts validate complete sources and rehearse in
memory before target writes. Apply obtains an exclusive transaction, rechecks
the target's identity/hash, verifies the preimage backup, applies the step,
checks schema/integrity/data, and commits. Symlinks, hard links, repository
runtime paths, network-style paths, WAL files and existing sidecars are refused.
DDL outside the whitelist, triggers/views with indirect writes, ATTACH and
unapproved PRAGMAs/functions remain denied. Existing incompatible schemas are
rejected rather than migrated. Windows execution remains a separate reviewed
action; this prep does not authorize access to that machine.

## Runner hash handoff

Every successful non-dry invocation emits aggregate JSON followed by exactly
one `POST_WRITE_SHA256=<64 lowercase hex>` line after SQLite closes. The runner
requires exit code 0 and `result=PASS`, then passes that value unchanged as the
next step's `--expected-db-sha256`: initial verified hash -> 015-04 -> H04 ->
015-05 -> H05 -> 015-06 -> H06. A no-op emits the unchanged hash. Dry runs and
failures emit no handoff. Stale/tampered hashes and intervening DB changes stop
the chain. A post-commit publication failure must stop the runner; no rollback
is claimed after commit.

## Validation

```console
$ PYTHONDONTWRITEBYTECODE=1 TMPDIR=/tmp python3 -m unittest tests.test_015_phase_c_prep
Ran 76 tests in 21.299s
OK
$ PYTHONDONTWRITEBYTECODE=1 REQUIRE_STREAMLIT=1 TMPDIR=/tmp python3 -m unittest discover -s tests -v
Ran 1148 tests in 572.783s
OK
$ git diff --check
(clean)
```

The focused suite includes all five pinned hashes, exact eight-field scratch
creation, rejection of one-character tampering even if the whitelist is altered,
preflight refusal without filesystem changes, import into a newly created table,
empty-source creation, full 04 -> 05 -> 06 CLI hash chaining, repeat no-ops,
dry-run isolation and exact-byte rollback after CREATE/INSERT failure. Existing
auth/path/source/schema/monetary/privacy and indirect-write regressions remain.
Independent verification confirmed canonical-source byte equality and unchanged
015-05/015-06 SQL strings against the starting commit.

PR #49 remains draft. `REVIEW_B=REQUEST_CHANGES_UNRESOLVED` records that no new
review approval is claimed. `REAL_DB_WRITE=NOT_STARTED` and
`REAL_DB_CONNECTION=NOT_STARTED`.
