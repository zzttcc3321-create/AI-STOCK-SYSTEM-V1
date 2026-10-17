# 015 Phase B execution report — BLOCKED

- Task: `AI-STOCK-V1-015-RUNTIME-INITIALIZATION`
- Execution date: `2026-10-16` (UTC)
- Base: `main`, `fc1daa02c3172af32e353f8a7aa2899e5e6dfd8c`
- Branch: `feat/015-phase-b` (already present at the specified base when work began)
- Phase B status: **BLOCKED / NOT COMPLETED**
- `REAL_DB_WRITE=NOT_STARTED`; tool constant remains `false`.
- `APPLY_REAL_DB=NOT_AUTHORIZED`; write scope remains empty.

## Stop event

The execution wrapper installed a Python audit hook before importing the existing
initialization tool. Its filesystem allowlist permitted only the specified
temporary root, its four children, and two planned evidence files under that root.
It also prohibited database connections, socket operations, runtime imports and
unapproved subprocesses. Python bytecode generation was disabled.

Before invoking the CLI, the wrapper called the existing C01
`require_safe_directory()` on `/`, `/tmp`, the intended root and all four intended
children. These checks returned without error. The implementation uses `lstat`,
directory mode and `FILE_ATTRIBUTE_REPARSE_POINT`, including checks of original
ancestors before path resolution. No production code was modified.

The single initialization invocation was:

```text
015-01 --base-dir /tmp/015-phase-b-20261016 \
  --expected-head fc1daa02c3172af32e353f8a7aa2899e5e6dfd8c
```

**Inferred stop trigger: `UNEXPECTED_WRITE`.** The wrapper incorrectly classified opening
`/dev/null` with `O_RDWR` as an unexpected write. The call path is
`directories()` → `git(..., 'rev-parse', 'HEAD')` →
`subprocess.check_output(stderr=subprocess.DEVNULL)` →
`subprocess.Popen._get_devnull()` → `os.open(os.devnull, os.O_RDWR)`.
Read-only inspection of the installed Python implementation confirmed that final
operation after the failure. This is a wrapper allowlist error, not evidence of a
runtime-directory escape or database write.

The tool catches the wrapper's `RuntimeError` and returns the generic
`STEP_EXECUTION_FAILED` shown below; the original audit event was not separately
persisted before the wrapper exited. The attribution above follows from the
wrapper predicate and the inspected call path. The wrapper exited with status 1.
No retry, repair, later initialization step or test run followed the failure.

## Step results

| Item | Result | Evidence / limitation |
| --- | --- | --- |
| 015-01 | BLOCKED | C01 prechecks returned; HEAD subprocess setup was intercepted before directory creation. |
| 015-02 Schema fingerprint | NOT VERIFIED | No approved offline input was supplied or located; actual fingerprint was not computed. |
| 015-02 Legacy digest | NOT VERIFIED | No approved offline input or definition of the supplied single digest was available. |
| 015-02 file inventory | PARTIAL | Post-stop inspection confirms the temporary root is absent and Git has no tracked changes or untracked files before this report; the planned full manifest was not persisted. |
| 015-03 configuration validation | NOT RUN | Stopped at 015-01; no runtime configuration was created or validated. |
| 015-03 initialization dry-run | NOT RUN | Stopped at 015-01; future authorized execution has not been certified. |
| 015-04 | NOT EXECUTED | Handler was never called; authorization flag was never supplied. |
| 015-05 | NOT EXECUTED | Handler was never called; authorization flag was never supplied. |
| 015-06 | NOT EXECUTED | Handler was never called; authorization flag was never supplied. |

The user-supplied expectations remain unchanged:

```text
SCHEMA_EXPECTED=f0f32924c129d986595817a5a88f3d50cf0a80fcf1394a24854b34e7082b21d0
LEGACY_EXPECTED=33a48e82dce954a9f6e9b59e9a56199f3314f7d5e592bb3116f21ec705cd5f90
SCHEMA_ACTUAL=NOT_COMPUTED
LEGACY_ACTUAL=NOT_COMPUTED
```

The current 015-02 implementation requires an explicit database input and a
baseline containing `SCHEMA_AFTER`,
`LEGACY_DIGEST_AFTER={schema,row_count_digest}` and `TABLE_COUNT_AFTER`.
The supplied single Legacy hash does not identify its mapping to those fields.
Repository inspection and inspection of candidate historical E7 JSON evidence
did not locate the two expected values. This absence is an input gap, not an
observed `SCHEMA_CHANGED` or `LEGACY_DIGEST_CHANGED` result. No current snapshot
or synthetic database was substituted for the approved baseline.

An input request was issued for an approved offline evidence/snapshot path,
the Legacy digest definition and the complete baseline. It remained unanswered
when this report was written. No real database was connected.

## Creation and file inventory

| Path | Outcome |
| --- | --- |
| `/tmp/015-phase-b-20261016/` | Not created; absent on post-stop inspection. |
| `/tmp/015-phase-b-20261016/runtime/` | Not created. |
| `/tmp/015-phase-b-20261016/cache/` | Not created. |
| `/tmp/015-phase-b-20261016/logs/` | Not created. |
| `/tmp/015-phase-b-20261016/reports/` | Not created. |
| `logs/phase-b-execution.jsonl` under the intended root | Planned evidence only; not created. |
| `reports/repository-before.json` under the intended root | Planned evidence only; not created. |
| `docs/evidence/015_PHASE_B_COMPLETION_REPORT.md` | Sole repository deliverable; contains the failure log. |

The wrapper gathered a repository inventory in memory before execution, excluding
`.git` and including ignored files, but stopped before persisting or comparing it.
Consequently a complete before/after hash inventory cannot be claimed. No
`UNKNOWN_FILE_CREATED` event was reported. Post-stop `git status --short` was empty
before adding this report. Git metadata changes associated with delivering this
report are separate from runtime initialization.

## Execution / dry-run log

The actual CLI result, captured by the wrapper's assertion output:

```json
{"APPLY_REAL_DB":"NOT_AUTHORIZED","CREATED_DIRECTORIES":[],"CREATED_FILES":[],"ENABLED_FEATURES":[],"FILE_HASHES":{},"LLM_CALLS":0,"NETWORK_CALLS":0,"REAL_DB_WRITE":false,"REAL_DB_WRITE_SCOPE":[],"RUNTIME_INITIALIZATION_STATUS":"BLOCKED","SCHEDULER_STARTED":false,"STEP":"015-01","error_code":"STEP_EXECUTION_FAILED","result":"FAIL"}
```

The CLI safety fields above are fixed declarations, not a system-wide monitor.
Within the attempted invocation, the wrapper stopped execution during subprocess
setup, before directory creation or any database handler. No network, scheduler
or LLM operation was initiated by this invocation. This does not assert monitoring
coverage over unrelated processes in the shared environment.

```text
BASE_AND_BRANCH_CHECK=PASS
INITIAL_GIT_WORKTREE=CLEAN
C01_DIRECTORY_PRECHECKS=PASS
015-01=BLOCKED
WRAPPER_EXIT_CODE=1
015-02=NOT_RUN
015-03_CONFIG=NOT_RUN
015-03_DRY_RUN=NOT_RUN
015-04=NOT_EXECUTED
015-05=NOT_EXECUTED
015-06=NOT_EXECUTED
POST_STOP_TEMP_ROOT_EXISTS=false
POST_STOP_TEMP_ROOT_IS_SYMLINK=false
REAL_DB_WRITE=NOT_STARTED
USAGE_LIMIT_HIT=false
STOP_TRIGGER_ATTRIBUTION=WRAPPER_PREDICATE_AND_INSPECTED_CALL_PATH
STOP_TRIGGER=UNEXPECTED_WRITE
```

## Validation and scope

- No tests were executed after the stop. Required regression success is **not
  established**; Phase B completion criteria are unmet.
- Read-only post-stop `git diff --check` passed before report creation;
  `git diff --cached --check` also passed for the staged report.
- `git diff --exit-code fc1daa02 -- tools/015_runtime_init.py
  tools/e2_windows_verify.py tests/test_e2_windows_verify.py` passed.
  C01 and B06 code remain unchanged.
- No production configuration, schema, migration, business code or tests changed.
- No holdings, real database selectors, credentials or business data appear in
  this report. No synthetic or real database was created during this attempt.

Execution remains stopped. Resuming requires resolving the wrapper allowlist
error and the missing approved offline inputs; this report does not authorize a
retry or any execution of 015-04/05/06.

## 2026-10-16 authorized retry — offline 015-01/02/03 completed

This appended section records the new coordinator-authorized attempt. The entire
failure record above and commit `aff6c60382e287d3a4288650fa8d30598f7ec021` are
preserved. The earlier BLOCKED heading describes the original attempt.

- Execution: `2026-10-16T20:22:55Z`, branch `feat/015-phase-b`, starting HEAD
  `aff6c60382e287d3a4288650fa8d30598f7ec021`.
- Retry status: **OFFLINE_COMPLETE**, limited to the approved 015-01/02/03 scope.
- Overall runtime initialization: **PENDING_AUTHORIZATION**.
- `REAL_DB_WRITE=NOT_STARTED`, `APPLY_REAL_DB=NOT_AUTHORIZED`, write scope `[]`.
- One live retry invocation, exit code `0`; no live stop event and no usage limit.

### Step zero: monitor repair and regression

`tools/phase_b_monitor.py` now permits the exact Git HEAD query's standard
`/dev/null` access, including `O_RDWR`. The device exception checks the literal
absolute path, Linux character-device identity and allowed flags. Read-only
access to `/dev/random`, `/dev/urandom` and `/dev/zero` is also permitted.
Creation/truncation flags and writes to those other devices remain blocked.
Normal read-only directory opens are accepted without mistaking `O_DIRECTORY`
for `O_TMPFILE`. The parser uses the C01-checked `/tmp` parent directly, avoiding
an incidental temporary-directory probe write.

Writes require exact absolute allowlisted paths and safe ancestors; relative
paths with `dir_fd`, symlink/reparse escapes, unapproved files/directories,
database connections and unapproved subprocesses are rejected. The first stop
event is latched before logging/raising, so the CLI's generic exception handler
cannot hide it. The live log uses an already-open evidence stream.

All **12 monitor regression tests passed before the live retry**. The actual
Git `rev-parse HEAD` call with `stderr=DEVNULL` succeeded without a stop event.
The synthetic unauthorized write raised `UNEXPECTED_WRITE` before its target
was created, and subsequent monitored operations remained stopped. Other
negative fixtures covered unknown directories, network/database operations,
scheduler/LLM imports, Git trace overrides and device flags. These were isolated
`REGRESSION` child processes with `expected_negative_test=true`; they were not
live Phase B violations.

Tool-preparation tests also exposed an ineffective authorization-route check.
It now compares against fixed expected options and checks saved original
handler signatures, before guard wrappers can hide a malformed signature.
All affected tests passed before the live execution.

### Approved offline inputs and verification boundary

The following values came directly from the coordinator's retry instruction,
with the source attribution supplied there. They were not collected from a
database at runtime:

```json
{
  "schema_fingerprint": "f0f32924c129d986595817a5a88f3d50cf0a80fcf1394a24854b34e7082b21d0",
  "legacy_digest": {
    "schema_digest": "33a48e82dce954a9f6e9b59e9a56199f3314f7d5e592bb3116f21ec705cd5f90",
    "row_count_digest": "ab2597853b857f9e2e6d10b58bed4dd6e7a7da6f4d6698ca0e5d216674677ccd"
  },
  "table_count": 32
}
```

Source attribution: `e7-audit.json` `SCHEMA_AFTER` / `LEGACY_DIGEST`, and
`docs/freeze/DB_EXISTING_SCHEMA_INVENTORY_V1.json` for the approved table count.
Those source files were not available in this checkout and were not read.
The legacy `schema_digest` field maps to the existing verifier's `schema` field;
neither digest value changes.

`015_phase_b_retry.py` materializes the approved reference and checks its exact
keys, types and values without calling the database-backed 015-02 handler.
This establishes offline input consistency only: the observed JSON is a copy of
the approved constants, not an independent measurement of system state.
`CURRENT_SCHEMA_FINGERPRINT`, `CURRENT_LEGACY_DIGEST`,
`CURRENT_TOTAL_TABLE_COUNT` and `DATABASE_INTEGRITY` are all `NOT_MEASURED`.
No current snapshot or synthetic database was substituted for the reference.

### Retry results and dry-run

| Item | Result | Evidence / scope |
| --- | --- | --- |
| 015-01 | PASS | Original C01 `external_root()` / `require_safe_directory()` checked original ancestors and targets using `lstat` plus `REPARSE_POINT`; the original CLI created only the specified temporary root and four children. |
| 015-02 | PASS, OFFLINE_APPROVED_REFERENCE_ONLY | Approved fingerprint, both legacy digest fields and integer table count 32 matched the materialized reference. No database connection or current-state measurement. |
| 015-03 configuration | PASS | Original config validator accepted literal boolean `false` for `AUTO_TRADING`, `SCHEDULER` and `AUTO_REFRESH`. |
| 015-03 dry-run | PASS, STATIC_DISPATCH_AND_AUTHORIZATION_PREFLIGHT | Original handler signatures, dispatch routes, required input options and authorization defaults exist for 015-04/05/06. |
| 015-04 / 015-05 / 015-06 | SKIPPED_PENDING_AUTHORIZATION | Handler calls `0 / 0 / 0`; no authorization flags supplied. |

The dry-run verifies that future authorized calls have the expected routes and
argument contracts. It does not invoke the handlers or certify future input
validity: `FUTURE_READINESS=ROUTES_PRESENT_INPUT_VALIDATION_DEFERRED`. Actual
execution and all future input checks still require separate authorization.

The sole live invocation was:

```text
python3 -B tools/015_phase_b_retry.py --expected-head aff6c60382e287d3a4288650fa8d30598f7ec021
```

### Created inventory and evidence

All runtime creation occurred under `/tmp/015-phase-b-20261016/`:

```text
/tmp/015-phase-b-20261016/
  runtime/
    offline-reference.json
    config.json
  cache/
  logs/
    phase-b-retry.jsonl
  reports/
    retry-result.json
```

The root and all four children were absent before execution and appear in
`CREATED_DIRECTORIES`. The two input files, log and result above are the exact
four allowlisted files. No dashboard HTML or optional-step output was created.

| Evidence | SHA-256 |
| --- | --- |
| `runtime/offline-reference.json` | `08f0607a3efa42c2d46628c8f8cc50161d06a0ef2d04b4efaef09f78713e12ce` |
| `runtime/config.json` | `59e138a85ea56604d95e661120e5821a0b739a0f203774cd50bec68c63c1cb98` |
| `logs/phase-b-retry.jsonl` | `1b356cd78dbf4948c07138482b82236ef4966a372cc42ba010c93fc7d7afa84e` |
| `reports/retry-result.json` | `cc75868de62506118e176ecd8d4189026612852b5b5afef16ef9b4e292a368d8` |

The canonical approved-reference hash and reread-reference hash both equal
`50911daa6ea936bd841d412822f3d60900b22df23a2866c49a51722055aec502`.
The runtime wrapper compared a 381-entry repository manifest, including ignored
files and excluding `.git`. Before/after hashes both equal
`e26b22f384f8bba828da01740e8d499585b1774a8504640117cf25a4b8257047`.
There were no repository creations or changes during that invocation. Subsequent
report edits and Git delivery are outside the runtime execution interval.

```text
C01_PRECHECK=PASS
015-01=PASS
015-02=PASS_OFFLINE_APPROVED_REFERENCE_ONLY
015-03_CONFIG=PASS
015-03_DRY_RUN=PASS_STATIC_PREFLIGHT
DATABASE_CONNECTIONS=0
OPTIONAL_HANDLER_CALLS={015-04:0,015-05:0,015-06:0}
MONITOR_STOP_EVENT=null
REPOSITORY_UNCHANGED=true
REAL_DB_WRITE=NOT_STARTED
APPLY_REAL_DB=NOT_AUTHORIZED
RUNTIME_INITIALIZATION_STATUS=PENDING_AUTHORIZATION
PHASE_B_STATUS=OFFLINE_COMPLETE
USAGE_LIMIT_HIT=false
```

The monitor covers audited Python operations and permits one exact read-only
Git child command. It is not a system-wide/native-code sandbox. During the
approved invocation no network, scheduler, LLM or database operation was
initiated; user-authorized Git push/PR updates are separate delivery operations.

### Regression and change scope

**35 relevant tests passed**, using only synthetic temporary files and isolated
negative fixtures, without running 015-04/05/06 or connecting any database:

```text
python3 -B -m unittest discover -s tests -p test_phase_b_monitor.py -v       # 12 PASS
python3 -B -m unittest discover -s tests -p test_015_phase_b_retry.py -v     # 11 PASS
python3 -B -m unittest discover -s tests -p test_015_phase_b_safety.py -v    # 12 PASS
```

The existing complete runtime-init suite was not run because it executes
optional handlers. The new safety suite covers C01's directory/symlink/reparse
and Windows metadata behavior plus strict config validation, without a DB.
`git diff --check` and staged whitespace checks are clean. Comparison with
`aff6c60` confirms `tools/015_runtime_init.py`, `tools/e2_windows_verify.py` and
`tests/test_e2_windows_verify.py` are unchanged; B06, schema, migration and
business code are untouched. Only the retry/monitor tools, three synthetic test
files and this appended report are included in the retry delivery.

No real holdings, database selectors, credentials or business data are included
in these changes or the PR description. The prior failed commit is retained;
the retry is delivered as a new commit on the existing branch and PR #48.
