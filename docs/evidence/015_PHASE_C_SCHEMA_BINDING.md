# 015 Phase C schema binding and locked CREATE statements

```text
TASK_ID=AI-STOCK-V1-015-PHASE-C-DDL-FIX
BASE_COMMIT=ff2487a
BRANCH=feat/015-phase-c-prep
SECURITY_MASTER_BASELINE_EXISTENCE=ABSENT_IN_32_TABLE_INVENTORY
SECURITY_MASTER_CONTRACT_BINDING=PASS_8_OF_8
CREATE_SHA256_ENFORCEMENT=IMPLEMENTED_ALL_5
REAL_DB_WRITE=NOT_STARTED
REAL_DB_CONNECTION=NOT_STARTED
```

## Authoritative offline evidence

On 2026-10-18, engineering prep read only the supplied local schema inventory
`~/prompts/DB_EXISTING_SCHEMA_INVENTORY_V1.json`. Its original 118,662 bytes have
SHA256 `c9eaf6d1bc8927c8c0dbdb6dd0404ddc52751b2bcefbbad1c5b66a406fbadbd2`.
The declared `table_count`, number of table entries and number of unique names
all equal **32**. Exact-name inspection of every `tables[].name` confirms that
**`security_master` does not exist in the supplied B-baseline inventory**.
The other four Phase C table names are also absent. No database path in this
inventory or the earlier E7 audit was opened or contacted; this is verification
against the supplied artifact, not a new measurement of a real database.
Only schema facts are recorded here; row counts and holding details are omitted.

The earlier E7 audit supplied digests without table definitions. That source
limitation remains factual, but its previous fail-stop finding is superseded
for this task by the supplied inventory and the commander's canonical CREATE
file. The previously voided CREATE statement is not used.

## security_master contract binding

The only authorized source is `~/prompts/security_master_ddl_canonical.sql`.
Its complete bytes were read programmatically into `DDL_BY_STEP["015-04"]`;
no SQL was retyped, reformatted, normalized or stripped. The runtime string is
exactly the original **458 UTF-8 bytes**, including the final LF after `;`.

| Import contract field | Canonical DDL field | Type | Required/default | Primary key position |
| --- | --- | --- | --- | --- |
| `market` | `market` | TEXT | NOT NULL | 1 |
| `symbol` | `symbol` | TEXT | NOT NULL | 2 |
| `name` | `name` | TEXT | NOT NULL | — |
| `security_type` | `security_type` | TEXT | NOT NULL | — |
| `lot_size` | `lot_size` | INTEGER | NOT NULL | — |
| `currency` | `currency` | TEXT | NOT NULL, DEFAULT 'CNY' | — |
| `list_date` | `list_date` | TEXT | NULL | — |
| `status` | `status` | TEXT | NOT NULL | — |

`SECURITY_COLUMNS`, the canonical CREATE's `PRAGMA table_xinfo` result and the
import INSERT/SELECT field lists match **1:1 in the same order: exactly eight
fields**, with no extra or hidden columns. The composite PK is `(market, symbol)`.
CHECK constraints permit markets `SSE`, `SZSE`, `BSE`; security types `STOCK`,
`ETF`; positive `lot_size`; and statuses `LISTED`, `SUSPENDED`, `DELISTED`.
The scratch schema test checks types, nullability, defaults, PK order and the
existing contract validator's CHECK and unique-index requirements.

015-04 now creates the absent table through its exact DDL whitelist before
importing the complete security snapshot. The canonical SQL has no
`IF NOT EXISTS`; an existing object is schema-validated instead of re-created.
Repeated imports remain no-ops. An empty source still commits a newly created
table. CREATE and INSERT share the guarded transaction, preimage backup,
schema-delta verification and rollback checks. Dry runs create only in the
memory rehearsal and leave the scratch target and directory unchanged.

## Five independently locked CREATE hashes

`tools/phase_c_safety.py::CREATE_SHA256_BY_STEP` pins the following hashes
independently of the SQL strings. Before each whitelisted CREATE, SHA256 is
computed over `statement.encode("utf-8")` and checked against that step's pins.
A mismatch raises the fixed `CREATE_SHA256_MISMATCH` error before SQLite
executes the statement. Verification covers the contract CREATE helper, the
guarded SQL execution boundary and the schema verifier's memory CREATEs.
No normalization, whitespace trimming or line-ending conversion is allowed.

| Table | Step | UTF-8 bytes | CREATE text SHA256 |
| --- | --- | --- | --- |
| `security_master` | 015-04 | 458 | `20ee80403e222acb544790e8df9cea8bad6143777998a89c4c5320fd1c661565` |
| `holding_config` | 015-05 | 270 | `d764d2839f0177a4bd26880a41d603a0dd067e2a0f5e099fa2fd8fb187ed54d0` |
| `holding_config_meta` | 015-05 | 104 | `e81abd28d2917ec1a631e005fbf66d23ace5dfc9cfa34eb7db7a7bc5cdf72de2` |
| `dashboard_position_snapshot` | 015-06 | 345 | `a19aacc38e712e1b105b8280d97e93cc9ce4106df59d362aa55663c17e169589` |
| `dashboard_overview` | 015-06 | 250 | `f81b9634b58a44d8359f07dbe0da93aed306ad22851707fbd8d6b09536eb4fd8` |

The `security_master` hash is also the canonical source file SHA256. Per the
commander's 2026-10-18 contract revision, `holding_config` now checks
`avg_cost = ROUND(avg_cost, 3)`; its revised hash is pinned above. The other
three CREATE strings remain byte-for-byte identical to `ff2487a`. All four
015-05/015-06 strings end at their semicolon without a trailing newline.

The four addition contracts retain these fields in order:

| Table | Contract fields |
| --- | --- |
| `holding_config` | `market`, `symbol`, `quantity`, `avg_cost` |
| `holding_config_meta` | `key`, `value` |
| `dashboard_position_snapshot` | `batch_id`, `market`, `symbol`, `name`, `quantity`, `avg_cost`, `cost_value`, `as_of_date` |
| `dashboard_overview` | `batch_id`, `as_of_date`, `total_positions`, `total_cost_value`, `currency` |

## 015-05 avg_cost precision revision

The commander revised `avg_cost` to **at most 3 decimal places (最多 3 位小数)**.
CSV validation and the SQL CHECK both accept `4.241` and reject `4.2415`;
values are never rounded to fit. Exact arithmetic uses integer thousandths of
yuan, including 015-06 position costs and totals, and rejects SQLite REAL
round-trip precision loss. For synthetic quantity 42,000, retaining the third
decimal preserves 42 yuan compared with truncating the example cost to `4.24`.
Existing incompatible schemas still fail validation; this revision performs
no migration. The 015-05 real DB write remains **PAUSED until this fix is merged**.

## Validation boundary

All database exercises use synthetic scratch databases under `/tmp` or in
memory. Tests cover single-character tampering of all five whitelisted CREATEs
(including a valid SQL whitespace change), rejection before SQL execution,
trailing-LF tampering through dry-run/apply preflight, exact eight-column scratch
creation, end-to-end import into the newly created table, the complete
04 -> 05 -> 06 hash-handoff chain, repeat no-ops, empty-source creation and
rollback restoring exact bytes after security CREATE/INSERT failure.

Full-suite results are recorded after execution in the
[engineering prep report](015_PHASE_C_PREP_REPORT.md). PR #49 remains draft;
passing engineering checks does not grant real execution approval.
`REAL_DB_WRITE=NOT_STARTED`; `REAL_DB_CONNECTION=NOT_STARTED`.
