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
