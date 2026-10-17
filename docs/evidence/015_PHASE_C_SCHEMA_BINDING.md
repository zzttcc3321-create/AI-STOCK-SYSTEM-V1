# 015 Phase C schema binding — FAIL-STOP

```text
TASK_ID=AI-STOCK-V1-015-PHASE-C-REMEDIATION
BASE_COMMIT=bdd552e
BRANCH=feat/015-phase-c-prep
ITEM_1_SCHEMA_BINDING=BLOCKED_MISSING_SCHEMA_DETAILS
SECURITY_MASTER_BASELINE_EXISTENCE=UNVERIFIED
ITEM_2_CREATE_SHA256_ENFORCEMENT=BLOCKED_DEPENDS_ON_ITEM_1
REAL_DB_WRITE=NOT_STARTED
REAL_DB_CONNECTION=NOT_STARTED
```

## Audit read first

On 2026-10-17, the supplied local file `/home/ubuntu/prompts/e7-audit.json`
(`~/prompts/e7-audit.json`) was read before attempting schema binding. Its
SHA256 over the original file bytes is:

```text
89e0cf77da38409201ba0c58af9653447c140e735a714a5e28010fc8fbba80a1
```

It identifies task `AI-STOCK-V1-014-E7-PRE-APPLY-RUNTIME-GATE`, step `e7-07`,
result `PASS`, and Git head `9059e0f1bdec0fb7dd0dbc379c2a863c4e8f8a86`.
The schema-related content is exactly:

```json
{
  "SCHEMA_BEFORE": "f0f32924c129d986595817a5a88f3d50cf0a80fcf1394a24854b34e7082b21d0",
  "SCHEMA_AFTER": "f0f32924c129d986595817a5a88f3d50cf0a80fcf1394a24854b34e7082b21d0",
  "LEGACY_DIGEST": {
    "schema": "33a48e82dce954a9f6e9b59e9a56199f3314f7d5e592bb3116f21ec705cd5f90",
    "row_count_digest": "ab2597853b857f9e2e6d10b58bed4dd6e7a7da6f4d6698ca0e5d216674677ccd"
  }
}
```

The complete top-level key list is:

```text
APPLY_REAL_DB, BACKUP_PATH, BACKUP_SHA256, DATABASE_PATH, E7_STATUS,
GIT_HEAD, LEGACY_DIGEST, REAL_DB_WRITE, ROLLBACK_RESULT, SCHEMA_AFTER,
SCHEMA_BEFORE, STEP, STEPS, TASK_ID, result
```

`STEPS` contains only a `result: PASS` for each of `e7-01` through `e7-06`.
There is **no `SCHEMA_FINGERPRINT_AFTER`, table list, CREATE statement, column
inventory, constraint/index inventory, or schema-detail attachment reference**.
The DB and backup paths in the audit were not opened or contacted. A digest
does not reveal the table names or definitions it represents.

The [Phase B report](015_PHASE_B_COMPLETION_REPORT.md#approved-offline-inputs-and-verification-boundary)
attributes its fingerprint to this audit's `SCHEMA_AFTER`. It explicitly says
that the offline reference is not an independent measurement, with current
schema fingerprint/table count/integrity `NOT_MEASURED`. Its approved table
count does not establish any individual table's existence or fields.

**Whether `security_master` EXISTS in the B baseline cannot be confirmed or
denied from the supplied audit.** There is no table list or CREATE statement to
cite. The commander's assumption and the synthetic test fixture are not real
schema evidence. Item 1 stops here; no table/field name is invented, remapped,
or declared compatible. `tools/phase_c_contracts.py` retains the unverified
commander contract, and the scripts still refuse a missing `security_master`.

## Field-by-field binding ledger

`UNRESOLVED` means the real table/field cannot be determined from the audit;
it does not mean the table or field is absent. The four approved new CREATEs
define proposed additions only. Their baseline absence, existing-name conflicts,
and compatibility cannot be checked from this artifact.

| Contract table | Contract field | Real B-baseline table | Real B-baseline field | Evidence/status |
| --- | --- | --- | --- | --- |
| `security_master` | `market` | UNRESOLVED | UNRESOLVED | Audit has no table/column definitions |
| `security_master` | `symbol` | UNRESOLVED | UNRESOLVED | Audit has no table/column definitions |
| `security_master` | `name` | UNRESOLVED | UNRESOLVED | Audit has no table/column definitions |
| `security_master` | `security_type` | UNRESOLVED | UNRESOLVED | Audit has no table/column definitions |
| `security_master` | `lot_size` | UNRESOLVED | UNRESOLVED | Audit has no table/column definitions |
| `security_master` | `currency` | UNRESOLVED | UNRESOLVED | Audit has no table/column definitions |
| `security_master` | `list_date` | UNRESOLVED | UNRESOLVED | Audit has no table/column definitions |
| `security_master` | `status` | UNRESOLVED | UNRESOLVED | Audit has no table/column definitions |
| `holding_config` | `market` | UNRESOLVED | UNRESOLVED | Approved proposed addition; baseline unknown |
| `holding_config` | `symbol` | UNRESOLVED | UNRESOLVED | Approved proposed addition; baseline unknown |
| `holding_config` | `quantity` | UNRESOLVED | UNRESOLVED | Approved proposed addition; baseline unknown |
| `holding_config` | `avg_cost` | UNRESOLVED | UNRESOLVED | Approved proposed addition; baseline unknown |
| `holding_config_meta` | `key` | UNRESOLVED | UNRESOLVED | Approved proposed addition; baseline unknown |
| `holding_config_meta` | `value` | UNRESOLVED | UNRESOLVED | Approved proposed addition; baseline unknown |
| `dashboard_position_snapshot` | `batch_id` | UNRESOLVED | UNRESOLVED | Approved proposed addition; baseline unknown |
| `dashboard_position_snapshot` | `market` | UNRESOLVED | UNRESOLVED | Approved proposed addition; baseline unknown |
| `dashboard_position_snapshot` | `symbol` | UNRESOLVED | UNRESOLVED | Approved proposed addition; baseline unknown |
| `dashboard_position_snapshot` | `name` | UNRESOLVED | UNRESOLVED | Approved proposed addition; baseline unknown |
| `dashboard_position_snapshot` | `quantity` | UNRESOLVED | UNRESOLVED | Approved proposed addition; baseline unknown |
| `dashboard_position_snapshot` | `avg_cost` | UNRESOLVED | UNRESOLVED | Approved proposed addition; baseline unknown |
| `dashboard_position_snapshot` | `cost_value` | UNRESOLVED | UNRESOLVED | Approved proposed addition; baseline unknown |
| `dashboard_position_snapshot` | `as_of_date` | UNRESOLVED | UNRESOLVED | Approved proposed addition; baseline unknown |
| `dashboard_overview` | `batch_id` | UNRESOLVED | UNRESOLVED | Approved proposed addition; baseline unknown |
| `dashboard_overview` | `as_of_date` | UNRESOLVED | UNRESOLVED | Approved proposed addition; baseline unknown |
| `dashboard_overview` | `total_positions` | UNRESOLVED | UNRESOLVED | Approved proposed addition; baseline unknown |
| `dashboard_overview` | `total_cost_value` | UNRESOLVED | UNRESOLVED | Approved proposed addition; baseline unknown |
| `dashboard_overview` | `currency` | UNRESOLVED | UNRESOLVED | Approved proposed addition; baseline unknown |

Types, nullability, defaults, keys, CHECK constraints, indexes and triggers are
also unresolved against the B baseline. Schema-only authoritative evidence
linked to the approved B fingerprint is required to complete the ledger.

## Approved CREATE text hashes — reference only

The remediation instruction approves the commander's four CREATE texts for
verbatim locking. The source available locally is
`/home/ubuntu/prompts/015-phase-c-resume-prompt.txt`, whose original file SHA256 is
`c5479a07f06e655fbbe32f2c503957dda383088786fcbf21e63e2eaea3f970e9`.
Each CREATE in that source matches the corresponding existing `DDL_BY_STEP`
string byte for byte under the following definition:

- UTF-8, beginning with the `C` of `CREATE`, ending with the terminating `;`.
- Internal spaces and LF line endings preserved, including the closing-line
  indentation. No trailing newline or prompt indentation before `CREATE`.
- No SQL normalization, stripping of internal whitespace, or substitution.

| Contract table | Step | UTF-8 bytes | CREATE text SHA256 |
| --- | --- | --- | --- |
| `holding_config` | 015-05 | 270 | `b1eb4797677f786c61e1b413686edc80fdcbca62e75dabc67016b11de176717b` |
| `holding_config_meta` | 015-05 | 104 | `e81abd28d2917ec1a631e005fbf66d23ace5dfc9cfa34eb7db7a7bc5cdf72de2` |
| `dashboard_position_snapshot` | 015-06 | 345 | `a19aacc38e712e1b105b8280d97e93cc9ce4106df59d362aa55663c17e169589` |
| `dashboard_overview` | 015-06 | 250 | `f81b9634b58a44d8359f07dbe0da93aed306ad22851707fbd8d6b09536eb4fd8` |

**These are reference hashes, not a completed runtime lock.** Item 2 depends
on item 1 and is stopped. No SHA256 enforcement or tampered-CREATE test is
claimed by this remediation. The existing exact-statement allowlist remains
as previously reviewed; it is not equivalent to independent pinned hashes.
No `security_master` CREATE is authorized or added.

## Unblocking requirement

Provide an authoritative, schema-only B-baseline artifact with the complete
table list and CREATE/column/constraint/index/trigger definitions, tied to the
approved fingerprint. Then finish item 1 without inference, implement item 2's
independent CREATE hash verification and tamper tests, and rerun all tests.
Do not obtain this evidence by connecting to the real Windows fact DB during
engineering prep. PR #49 remains draft with Review B changes still required.
