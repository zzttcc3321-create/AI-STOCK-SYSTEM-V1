# #012A Controlled Persistence Implementation

授权依据：`/tmp/TASK_CARD_012.md` 末尾 DECISION（2026-10-03，qi，C_SPLIT），以及本次恢复指令。
当前分支固定为 `codex/v1-controlled-persistence-012`。#012A 不等于 #012 全闭环。

```text
#012_STOP_DECISION=RESOLVED
DECISION=C_SPLIT
RESUME_#012A=YES
KEEP_EXISTING_SQLITE_GUARD_FIX=YES
EVIDENCE_METADATA_READER=DEFERRED_TO_#012B
SIDECAR_FORMAT_CHANGE_IN_#012A=NO
EVIDENCE_PIPELINE_CHANGE_IN_#012A=NO
PROVIDER_CHANGE_IN_#012A=NO
REAL_PROVIDER_EVIDENCE_RUNTIME_WRITE=DISABLED
FULL_EVIDENCE_TRACE_COMPLETE=NO
PROVIDER_EVIDENCE_ARCHIVE_IMPLEMENTED=NO
EVIDENCE_METADATA_RUNTIME_READ=NO
FULL_EVIDENCE_PERSISTENCE_READY=NO
DB_SCHEMA_V1_1_APPLIED=TEMPORARY_TEST_DATABASES_ONLY
REPOSITORY_CORE_IMPLEMENTED=YES
READ_MODEL_CORE_IMPLEMENTED=YES
REAL_DB_APPLY=WAITING_QI
REAL_DB_MIGRATION=NOT_AUTHORIZED
```

## 实现与边界

- 唯一 production SQL：`migration/003_db_schema_v1_1_additive.sql`，静态 CREATE 七表、一个显式 partial index 和 21 个 immutable triggers。无 IF NOT EXISTS、ALTER、DROP、回填或旧表写入。
- Runner：A=所有批准对象缺失；B=完整匹配 frozen contract；C=部分、碰撞、附加对象或定义不一致。C 抛出 SCHEMA_DRIFT。对完整 sqlite_master 定义（含 CHECK、deferred FK、partial index、trigger bodies）进行核对，格式空白仅在 SQL literal 之外规范化。双内存库另比较 column/FK/index introspection。
- `BEGIN IMMEDIATE` 内重新做 preflight，逐条 execute，验证后 COMMIT；没有 executescript 隐式提交。异常（包括 COMMIT）统一 rollback。`foreign_keys=ON`、`recursive_triggers=ON`、有界 busy timeout 在每次操作前配置并核实；已有事务直接拒绝，不提交调用方事务。
- Online Backup API 捕获 WAL 已提交内容，独占创建目标、不覆盖文件、不做文件复制 fallback。有界超时；记录大小、SHA-256、UTC 备份时刻、源/备份 schema fingerprint、完整性及 FK 检查；关闭后只读重开验证。失败产物不得当作有效备份。演练只迁移另一份 online-backup copy，备份原件保持原哈希。
- Serialization 使用 canonical UTF-8 JSON，拒绝重复键（包括转义同名）、NaN/Infinity/溢出、非字符串 key、implicit repr。保留整数/浮点、数组顺序、Unicode 与时间 offset。DTO 不保存或重新生成 `_gate_digest`。
- Repository 接收现有 sealed domain 对象，并调用 draft/run/prepared/finalization Gates。显式要求 task_date、created_by、raw_model_output、evidence_created_at；原响应按原字节重算 hash。相同 identity/projection 内容为 IDEMPOTENT_NOOP；内容冲突为 IDENTITY_CONFLICT。状态修改仅走 update_task_status，不由重复保存任务恢复旧状态。
- finalization 同事务验证 task 与 predecessor，插入 Provider DB projection、events、overrides、PREPARED/FINAL、report、final evidence，检查 scope、hashes、有序日志、used⊆input、引用存在与 predecessor 配对，全部成功才提交。合成上游记录也通过现有 in-memory domain Trace gate；这不代表持久化 raw/sidecar 完整性。
- **真实 Provider Evidence 的 run/finalization 写入禁用。** 含 Evidence 的调用必须显式 `allow_synthetic_evidence=True`，并再次核对 SQLite main 是 :memory: 或系统临时目录下文件，拒绝额外 attached database。SQLite 内部无文件 temp schema 可以存在。此开关只供临时合成测试；不得将真实事实库或真实 Provider 数据放到临时目录规避限制。当前未实现真实 Evidence metadata 身份比对。
- 三个 read repositories 均只执行 SELECT。Analysis 返回七字段完整 wrapper、revision/supersedes/model/hashes 与生成时/当前状态；Confirmation 按 report/revision 的连续 event_sequence 重放。生成 FINAL 后追加 rejection 会使当前状态失效，但历史 snapshot 不变。
- Analysis revision graph 检测分叉、循环、缺 predecessor、stock/run 不同和 report/final-evidence predecessor 不配对。拒绝用时间或 ID 在后继中挑选；被拒绝的后继不恢复旧版本。多个独立 root 也保守返回 REVISION_CHAIN_AMBIGUOUS，当前实现不以 timestamp 选择多个 root。latest_confirmed_analysis 返回唯一且当前仍 confirmed 的 effective revision。Evidence predecessor 分叉同样返回无 effective projection。
- 旧 analysis_history 通过独立 legacy_history 接口原样返回 opaque record，标记 LEGACY_HISTORY / LEGACY_UNKNOWN，不猜测旧 stock 关联、不当作 V1.1 confirmed analysis。

所有 Evidence sidecar 字段和 Analysis Evidence completeness 都如实标记：

```text
metadata_availability=UNAVAILABLE
metadata_reason=SIDECAR_PHYSICAL_CONTRACT_NOT_IMPLEMENTED
integrity_status=NOT_VERIFIED
evidence_completeness=PARTIAL_DB_PROJECTION
```

endpoint_or_tool/request_params/provider_timestamp/validation_result 与 sidecar hashes 为 None；当前 Provider 确认状态未知，绝不由生成时状态或 sidecar 缺失推断验证通过。

## 验收自检

以下 PASS 仅指临时合成 SQLite 的自动化验证；不代表 Windows 现场结果。

| 任务卡验收项 | #012A 结果 / 证据 |
| --- | --- |
| BASELINE_548 | PASS，91c19e5 独立临时 detached checkout，548/548 |
| GLOBAL_SQLITE_CONNECT_PATCH / SQLITE_GUARD_SCOPED | REMOVED / PASS，成功与异常退出均恢复原函数 |
| MIGRATION_SQL_STATIC / ADDITIVE_ONLY / SCHEMA_CONTRACT_PARITY | PASS，migration tests 双库 introspection 与漂移拒绝 |
| WAL_SAFE_BACKUP / BACKUP_HASH / BACKUP_INTEGRITY | PASS / VERIFIED / PASS，temp WAL committed row、重开与哈希验证 |
| REHEARSAL_MIGRATION / LEGACY_32_SCHEMA_UNCHANGED / LEGACY_DATA_COUNT_DIGEST_UNCHANGED | PASS，32 张合成 legacy 表 + copy migration + repository/read E2E；并非真实 32 表结构复刻 |
| SERIALIZATION_ROUNDTRIP / HASH_ROUNDTRIP / TIMEZONE_ROUNDTRIP | PASS，run/draft/input/raw/prepared/final/confirmation-log、原 offset、JSON 类型 |
| REPOSITORY_IDEMPOTENCY / IDENTITY_CONFLICT_FAIL_CLOSED | PASS，全部七表身份冲突与 status 单独更新 |
| FINALIZATION_TRANSACTION_ATOMIC / INSERT_STEP_FAILURE_ROLLBACK / COMMIT_FAILURE_ROLLBACK | PASS，每个实际 insert step 注入失败；在实际 COMMIT 前插入 deferred FK violation，提交失败全部回滚 |
| SUPERSEDES_BRANCH_DETECTION / SUPERSEDES_CYCLE_DETECTION | PASS，真实临时 DB 分叉和独立 graph 故障场景 |
| ANALYSIS_READ_MODEL / CONFIRMATION_READ_MODEL | PASS，保留原判断/人工修改/拒绝；后续事件重放；query_only 连接 |
| EVIDENCE_READ_MODEL | PARTIAL_DB_PROJECTION_PASS；metadata runtime read 未实现，integrity_status 始终 NOT_VERIFIED |
| LEGACY_HISTORY_MARKING | PASS，LEGACY_HISTORY / LEGACY_UNKNOWN |
| SYNTHETIC_ROWS_IN_REAL_DB / OLD_TABLE_WRITE / OLD_TABLE_ALTER / OLD_DATA_BACKFILL | 0；从未连接真实业务库；legacy fixture 创建/填充仅为测试准备 |
| AWS_DB_WRITE / MODEL_CALL / PROVIDER_CALL / TRADING_WRITE | 0；只使用离线 FakeModelExecutor |
| Windows G0 / real backup / real copy rehearsal / real schema apply | TO_BE_EXECUTED_ON_WINDOWS / WAITING_QI |

#011 scope test 原断言禁止任何 migration SQL，已按本次明确批准更新为：保留 #011 历史 whitelist；#012 仅允许本任务 16 文件；frozen V1.0/V1.1 与 schema_v1_1 原文校验。其余 main 基线用例保留。

FULL_TEST=584/584_PASS_TEMPORARY_SQLITE
MIGRATION_SHA256=0b8055ce4326f40ff0245e8be2e9f45946ee6fa576c219c0bd499ec1848b6d1d

## 后续与停止点

WINDOWS_LOCAL_STEPS 与 REAL_DB_PREAPPLY_GATE 见 [runbook](PERSISTENCE_MIGRATION_RUNBOOK_012.md)。本轮不得把真实库 schema 状态记为 APPLIED，不宣称完整持久化 Evidence Trace 或 full Evidence persistence ready。
#012B 才能定义 archive root/目录/raw+metadata 文件名/sidecar JSON schema/URI resolver/原子 writer/reader/retention；本轮没有创建任何 sidecar、raw archive 或 provider 改动。

## 本轮 Git 交付阻塞（实际结果）

- `git add` 仅列出 16 个白名单文件，但失败：`.git/index.lock: Read-only file system`。没有暂存任何文件。
- `git commit` 同样失败于只读 `.git/index.lock`；没有生成 commit。
- 因 commit 未完成，没有把旧 HEAD 当作实现推送。`git push --dry-run origin codex/v1-controlled-persistence-012` 失败：`Could not resolve host: github.com`。
- `gh pr create --base main --head codex/v1-controlled-persistence-012 ...` 已尝试，失败：`error connecting to api.github.com`。没有创建 PR，没有可报告的 PR URL。
- 分支仍为 `codex/v1-controlled-persistence-012`；全部实现保留在工作区。另导出 `/tmp/012A-controlled-persistence.patch`（仅白名单文件）便于在可写 Git 环境审核/继续交付。
