# 股票AI辅助系统｜DB Schema V1.1

```text
VERSION=V1.1
STATUS=FROZEN_FOR_IMPLEMENTATION
SUPERSEDES=DB_SCHEMA_V1.0
CHANGE_REASON=PERSISTENCE_G0_BLOCKED_UNFREEZE
SCHEMA_UNFREEZE=APPROVED_BY_QI
QI_DECISION=OPTION_A
UNFREEZE_SCOPE=DB_SCHEMA_ONLY
MIGRATION_POLICY=ADDITIVE_ONLY
NEW_TABLE_COUNT=7
LEGACY_TABLE_ALTER=NO
LEGACY_DATA_BACKFILL=NO
NEW_DATABASE=NO
SOURCE_OF_TRUTH=WINDOWS_EXISTING_SQLITE
REAL_DB_CONNECTION=FORBIDDEN
REAL_DB_WRITE=NO
DDL=NO
DML=NO
MIGRATION_APPLY=NO
```

日期：2026-10-03。qi 已批准 OPTION A，GPT D22 定义解冻范围；本文件是 #011 冻结候选交付，
状态按授权标记 FROZEN_FOR_IMPLEMENTATION，并不表示已经对事实库实施。
[决策记录](../decisions/DB_SCHEMA_V1_1_PERSISTENCE_UNFREEZE_20261003.md)与任务卡 RESUME 是授权依据。
[DB_SCHEMA_V1.0.md](DB_SCHEMA_V1.0.md) 原文完全不动；未涉及的 V1.0 规则继续适用。

## 1. G0 事实与精确解冻范围

G0 真实 inventory 有 32 表，analysis_tasks / analysis_reports / evidence_records 都不存在。
analysis_history 只有部分旧报告职责，无法无损承载 #009/#010；原结果为 BLOCKED。
完整去业务计数 inventory 和逐字段映射见 [兼容映射](../DB_COMPATIBILITY_MAPPING_011.md)。
七张表是明确获批的新契约，绝不伪称来自旧事实库 DDL。

新增表恰好为 analysis_tasks、analysis_reports、evidence_records、analysis_confirmation_events、
analysis_human_overrides、analysis_snapshots、analysis_final_evidence。
不新增第八张 AnalysisConclusion / relation / users / stocks / collection_tasks / system_runs 表。
analysis_tasks.id 明确定义为 analysis_run_id 的原字符串，是新表的身份约定，绝非旧 ID 重映射。
stock_id / actor_id / created_by / collection_task_id / system_run_id 保留上游 opaque ID；
不凭列名推断与旧表的语义等价，不向 inventory 中不存在的表建立 FK。

## 2. 七张新表最小字段契约

所有 ID 为原样 TEXT，不转 INTEGER、不重新生成。以下 canonical JSON 为 TEXT 编码，
绝不采用 Python repr。NOT NULL JSON 列必须存相应完整 object / array，空数组为 []。
外键无 CASCADE；循环 final report/snapshot/final evidence 引用使用 deferred FK，必须同事务提交。
DDL 唯一可执行副本在 [schema_v1_1.py](../../persistence/schema_v1_1.py)，仅用于 :memory: 测试。

### analysis_tasks

| 字段 | SQLite 类型 / 约束 | 确定映射 |
| --- | --- | --- |
| `id` | `TEXT NOT NULL PRIMARY KEY` | AnalysisRunEvidence.analysis_run_id；与新任务 PK 同一字符串身份，不对应任何旧 ID |
| `stock_id` | `TEXT NOT NULL` | AnalysisRunEvidence.stock_id；opaque canonical ID，不猜测 stocks/stock_daily 关联 |
| `task_date` | `TEXT NOT NULL` | 调用方显式 persistence envelope.task_date（YYYY-MM-DD），不截取 created_at |
| `prompt_version` | `TEXT NOT NULL` | AnalysisRunEvidence.prompt_version |
| `model_version` | `TEXT NOT NULL` | AnalysisRunEvidence.model_version |
| `status` | `TEXT NOT NULL CHECK(length(status) > 0)` | 显式任务运行状态；初始来自 AnalysisRunResult.status；不替代确认状态 |
| `created_by` | `TEXT NOT NULL` | 调用方显式 persistence envelope.created_by；不借用 confirmed_by，不推测旧 users |
| `created_at` | `TEXT NOT NULL` | AnalysisRunEvidence.created_at；保留 offset 原文 |
| `run_evidence_json` | `TEXT NOT NULL` | AnalysisRunEvidence.to_dict() 完整公开对象；字段列表见下 |
| `validated_draft_json` | `TEXT NOT NULL` | ValidatedAnalysisDraft.to_dict() 完整公开对象；字段列表见下 |
| `prompt_envelope_json` | `TEXT NOT NULL` | PromptEnvelope.to_dict() 完整公开对象；保留 input_hash 重算输入 |
| `raw_model_output` | `TEXT NOT NULL` | 模型原响应原样 Unicode TEXT；其 UTF-8 bytes 用于 model_output_hash 重算，不 parse/reformat |

### analysis_reports

| 字段 | SQLite 类型 / 约束 | 确定映射 |
| --- | --- | --- |
| `id` | `TEXT NOT NULL PRIMARY KEY` | AnalysisConclusion.analysis_report_id = FinalAnalysisSnapshot.analysis_report_id |
| `task_id` | `TEXT NOT NULL REFERENCES analysis_tasks(id)` | FinalAnalysisSnapshot.analysis_run_id = analysis_tasks.id |
| `market_view` | `TEXT NOT NULL` | FinalAnalysisSnapshot.seven_fields.market_view 的完整 JSON wrapper（含 AI_ORIGINAL / EFFECTIVE_VALUE / DECISION） |
| `position_status` | `TEXT NOT NULL` | FinalAnalysisSnapshot.seven_fields.position_status 的完整 JSON wrapper（含 AI_ORIGINAL / EFFECTIVE_VALUE / DECISION） |
| `risk_level` | `TEXT NOT NULL` | FinalAnalysisSnapshot.seven_fields.risk_level 的完整 JSON wrapper（含 AI_ORIGINAL / EFFECTIVE_VALUE / DECISION） |
| `operation_plan` | `TEXT NOT NULL` | FinalAnalysisSnapshot.seven_fields.operation_plan 的完整 JSON wrapper（含 AI_ORIGINAL / EFFECTIVE_VALUE / DECISION） |
| `t_strategy` | `TEXT NOT NULL` | FinalAnalysisSnapshot.seven_fields.t_strategy 的完整 JSON wrapper（含 AI_ORIGINAL / EFFECTIVE_VALUE / DECISION） |
| `reasoning` | `TEXT NOT NULL` | FinalAnalysisSnapshot.seven_fields.reasoning 的完整 JSON wrapper（含 AI_ORIGINAL / EFFECTIVE_VALUE / DECISION） |
| `confidence` | `TEXT NOT NULL` | FinalAnalysisSnapshot.seven_fields.confidence 的完整 JSON wrapper（含 AI_ORIGINAL / EFFECTIVE_VALUE / DECISION） |
| `human_confirm` | `TEXT NOT NULL CHECK(human_confirm = 'CONFIRMED')` | FinalAnalysisSnapshot.human_confirm = CONFIRMED，历史事实不可 UPDATE |
| `created_at` | `TEXT NOT NULL` | FinalAnalysisSnapshot.confirmed_at；报告生成即确认时刻，原文保存 |
| `revision_id` | `TEXT NOT NULL UNIQUE` | FinalAnalysisSnapshot.revision_id；新修订新 ID |
| `model_version` | `TEXT NOT NULL` | AnalysisConclusion.model_version = FinalAnalysisSnapshot.model_version |
| `calculation` | `TEXT NOT NULL` | AnalysisConclusion.calculation 完整 JSON object（#010 已有键，不借 reasoning） |
| `evidence_ids` | `TEXT NOT NULL` | AnalysisConclusion.evidence_ids 的有序 JSON array = run.used_evidence_ids |
| `supersedes_analysis_report_id` | `TEXT REFERENCES analysis_reports(id)` | FinalAnalysisSnapshot.supersedes_analysis_report_id；显式 FK |
| `final_snapshot_hash` | `TEXT NOT NULL UNIQUE REFERENCES analysis_snapshots(snapshot_hash) DEFERRABLE INITIALLY DEFERRED` | FinalAnalysisSnapshot.final_snapshot_hash；关联 FINAL snapshot |
| `final_evidence_id` | `TEXT NOT NULL UNIQUE REFERENCES analysis_final_evidence(final_evidence_id) DEFERRABLE INITIALLY DEFERRED` | FinalAnalysisEvidence.final_evidence_id；显式关联最终证据 |

表级约束：`CHECK(supersedes_analysis_report_id IS NULL OR supersedes_analysis_report_id <> id)`。

### evidence_records

| 字段 | SQLite 类型 / 约束 | 确定映射 |
| --- | --- | --- |
| `id` | `TEXT NOT NULL PRIMARY KEY` | EvidenceRecord.evidence_id |
| `analysis_report_id` | `TEXT REFERENCES analysis_reports(id) DEFERRABLE INITIALLY DEFERRED` | 显式 insertion-time 可空报告关联；共享证据以 report/final evidence ID arrays 关联，不 UPDATE 此列 |
| `source` | `TEXT NOT NULL CHECK(source IN ('TUSHARE_MCP', 'TDX_OFFICIAL_LOCAL_HTTP', 'WORKBUDDY_TDX_MCP'))` | EvidenceRecord.source.value；仅三源 |
| `timestamp` | `TEXT NOT NULL` | EvidenceRecord.request_timestamp；不是 provider_timestamp |
| `raw_data_path` | `TEXT` | EvidenceRecord.raw_response_path（可空） |
| `calculation` | `TEXT` | EvidenceRecord.normalization_rule 为 null 时 NULL，否则 JSON object {normalization_rule: 有序 array} |
| `model_version` | `TEXT` | EvidenceRecord.model_version（可空） |
| `human_confirm` | `TEXT NOT NULL CHECK(human_confirm IN ('PENDING', 'CONFIRMED', 'REJECTED'))` | EvidenceRecord.human_confirm.value；证据生成时状态，后续改动追加 revision |
| `collection_task_id` | `TEXT` | EvidenceRecord.collection_task_id；opaque ID，禁止虚构旧 collection_tasks FK |
| `system_run_id` | `TEXT` | EvidenceRecord.system_run_id；opaque ID，禁止复用 screening_runs |
| `created_at` | `TEXT NOT NULL` | 调用方显式 persistence envelope.evidence_created_at，非猜测的 Provider 时间 |
| `supersedes_evidence_id` | `TEXT REFERENCES evidence_records(id)` | EvidenceRecord.supersedes_evidence_id；显式 FK，旧证据不变 |

表级约束：`CHECK(supersedes_evidence_id IS NULL OR supersedes_evidence_id <> id)`。

### analysis_confirmation_events

| 字段 | SQLite 类型 / 约束 | 确定映射 |
| --- | --- | --- |
| `event_id` | `TEXT NOT NULL PRIMARY KEY` | ConfirmationEvent.event_id |
| `analysis_run_id` | `TEXT NOT NULL REFERENCES analysis_tasks(id)` | ConfirmationEvent.analysis_run_id |
| `analysis_report_id` | `TEXT NOT NULL` | ConfirmationEvent.analysis_report_id |
| `revision_id` | `TEXT NOT NULL` | ConfirmationEvent.revision_id |
| `target` | `TEXT NOT NULL` | ConfirmationEvent.target |
| `reason_code` | `TEXT NOT NULL` | ConfirmationEvent.reason_code |
| `previous_state` | `TEXT NOT NULL CHECK(previous_state IN ('PENDING', 'CONFIRMED', 'REJECTED'))` | ConfirmationEvent.previous_state |
| `current_state` | `TEXT NOT NULL CHECK(current_state IN ('PENDING', 'CONFIRMED', 'REJECTED'))` | ConfirmationEvent.current_state |
| `actor_id` | `TEXT NOT NULL` | ConfirmationEvent.actor_id |
| `decision_reason` | `TEXT NOT NULL` | ConfirmationEvent.decision_reason |
| `created_at` | `TEXT NOT NULL` | ConfirmationEvent.created_at |
| `override_id` | `TEXT REFERENCES analysis_human_overrides(override_id) DEFERRABLE INITIALLY DEFERRED` | ConfirmationEvent.override_id |
| `human_decision` | `TEXT NOT NULL CHECK(human_decision IN ('CONFIRM', 'REJECT', 'MODIFY'))` | ConfirmationEvent.human_decision |
| `reviewed_snapshot_hash` | `TEXT REFERENCES analysis_snapshots(snapshot_hash) DEFERRABLE INITIALLY DEFERRED` | ConfirmationEvent.reviewed_snapshot_hash |
| `event_sequence` | `INTEGER NOT NULL CHECK(event_sequence >= 0)` | 日志中稳定 0-based ordinal；按 report/revision 分组、追加且连续；不按 timestamp 排序 |

表级约束：`UNIQUE(analysis_report_id, revision_id, event_sequence)`。

### analysis_human_overrides

| 字段 | SQLite 类型 / 约束 | 确定映射 |
| --- | --- | --- |
| `override_id` | `TEXT NOT NULL PRIMARY KEY` | HumanOverride.override_id |
| `analysis_run_id` | `TEXT NOT NULL REFERENCES analysis_tasks(id)` | HumanOverride.analysis_run_id |
| `analysis_report_id` | `TEXT NOT NULL` | HumanOverride.analysis_report_id |
| `revision_id` | `TEXT NOT NULL` | HumanOverride.revision_id |
| `target` | `TEXT NOT NULL` | HumanOverride.target |
| `reason_code` | `TEXT NOT NULL` | HumanOverride.reason_code |
| `original_value_hash` | `TEXT NOT NULL` | HumanOverride.original_value_hash |
| `replacement` | `TEXT NOT NULL` | HumanOverride.replacement；完整 canonical JSON object |
| `reason` | `TEXT NOT NULL` | HumanOverride.reason |
| `actor_id` | `TEXT NOT NULL` | HumanOverride.actor_id |
| `created_at` | `TEXT NOT NULL` | HumanOverride.created_at |
| `referenced_evidence_ids` | `TEXT NOT NULL` | HumanOverride.referenced_evidence_ids；有序 JSON array，独立于 model-used IDs |
| `origin` | `TEXT NOT NULL CHECK(origin = 'HUMAN')` | HumanOverride.origin |
| `human_confirm` | `TEXT NOT NULL CHECK(human_confirm = 'CONFIRMED')` | HumanOverride.human_confirm |

### analysis_snapshots

| 字段 | SQLite 类型 / 约束 | 确定映射 |
| --- | --- | --- |
| `snapshot_hash` | `TEXT NOT NULL PRIMARY KEY` | PREPARED.prepared_snapshot_hash 或 FINAL.final_snapshot_hash；内容寻址 PK |
| `snapshot_kind` | `TEXT NOT NULL CHECK(snapshot_kind IN ('PREPARED', 'FINAL'))` | PREPARED.final_state / FINAL.final_state；仅 PREPARED 或 FINAL |
| `analysis_run_id` | `TEXT NOT NULL REFERENCES analysis_tasks(id)` | 相应 snapshot.analysis_run_id；FK 到新任务 |
| `analysis_report_id` | `TEXT NOT NULL` | 相应 snapshot.analysis_report_id；PREPARED 可先于报告存在 |
| `revision_id` | `TEXT NOT NULL` | 相应 snapshot.revision_id |
| `payload_json` | `TEXT NOT NULL` | PreparedFinalSnapshot.to_dict() 或 FinalAnalysisSnapshot.to_dict() 完整公开 JSON object；字段列表见下 |

表级约束：`CHECK(length(snapshot_hash) = 64 AND snapshot_hash NOT GLOB '*[^0-9a-f]*')`，
`UNIQUE(analysis_report_id, revision_id, snapshot_kind)`（snapshot_kind 即契约中的 snapshot_type）。
同一 report revision 至多一个 PREPARED candidate；保留 `snapshot_kind = 'FINAL'` 时
`analysis_report_id` 的 partial UNIQUE index，每 report 至多一个 FINAL。

### analysis_final_evidence

| 字段 | SQLite 类型 / 约束 | 确定映射 |
| --- | --- | --- |
| `final_evidence_id` | `TEXT NOT NULL PRIMARY KEY` | FinalAnalysisEvidence.final_evidence_id |
| `analysis_report_id` | `TEXT NOT NULL UNIQUE REFERENCES analysis_reports(id) DEFERRABLE INITIALLY DEFERRED` | FinalAnalysisEvidence.analysis_report_id |
| `analysis_run_id` | `TEXT NOT NULL REFERENCES analysis_tasks(id)` | FinalAnalysisEvidence.analysis_run_id |
| `stock_id` | `TEXT NOT NULL` | FinalAnalysisEvidence.stock_id |
| `model_version` | `TEXT NOT NULL` | FinalAnalysisEvidence.model_version |
| `input_hash` | `TEXT NOT NULL` | FinalAnalysisEvidence.input_hash |
| `model_output_hash` | `TEXT NOT NULL` | FinalAnalysisEvidence.model_output_hash |
| `validated_output_hash` | `TEXT NOT NULL` | FinalAnalysisEvidence.validated_output_hash |
| `final_snapshot_hash` | `TEXT NOT NULL UNIQUE REFERENCES analysis_snapshots(snapshot_hash) DEFERRABLE INITIALLY DEFERRED` | FinalAnalysisEvidence.final_snapshot_hash |
| `confirmation_log_hash` | `TEXT NOT NULL` | FinalAnalysisEvidence.confirmation_log_hash |
| `input_evidence_ids` | `TEXT NOT NULL` | FinalAnalysisEvidence.input_evidence_ids；有序 JSON array |
| `used_evidence_ids` | `TEXT NOT NULL` | FinalAnalysisEvidence.used_evidence_ids；有序 JSON array；须为 input 子集 |
| `confirmed_by` | `TEXT NOT NULL` | FinalAnalysisEvidence.confirmed_by |
| `confirmed_at` | `TEXT NOT NULL` | FinalAnalysisEvidence.confirmed_at |
| `supersedes_final_evidence_id` | `TEXT REFERENCES analysis_final_evidence(final_evidence_id)` | FinalAnalysisEvidence.supersedes_final_evidence_id |
| `human_confirm` | `TEXT NOT NULL CHECK(human_confirm = 'CONFIRMED')` | FinalAnalysisEvidence.human_confirm |

表级约束：`CHECK(supersedes_final_evidence_id IS NULL OR supersedes_final_evidence_id <> final_evidence_id)`。

## 3. 结构化载荷与对象边界

| 对象 | JSON 载体 | 完整公开字段（每项映射为同名 JSON key） |
| --- | --- | --- |
| AnalysisRunEvidence | `analysis_tasks.run_evidence_json` | `analysis_run_id`, `stock_id`, `prompt_version`, `template_hash`, `input_hash`, `output_schema_version`, `model_version`, `model_output_hash`, `validated_output_hash`, `input_evidence_ids`, `used_evidence_ids`, `created_at`, `analysis_status`, `human_confirm`, `final_conclusion` |
| ValidatedAnalysisDraft | `analysis_tasks.validated_draft_json` | `output`, `human_confirmation_items`, `input_hash`, `template_hash`, `model_output_hash`, `validated_output_hash`, `input_evidence_ids`, `used_evidence_ids`, `output_schema_version`, `analysis_status`, `human_confirm`, `final_state` |
| PromptEnvelope | `analysis_tasks.prompt_envelope_json` | `prompt_version`, `stock_id`, `position_mode`, `analysis_mode`, `system_prompt`, `user_prompt`, `evidence_ids`, `input_hash`, `template_hash`, `NETWORK_CALL_COUNT`, `MODEL_CALL_COUNT`, `DATABASE_WRITE` |
| PreparedFinalSnapshot | `analysis_snapshots.payload_json（PREPARED）` | `analysis_report_id`, `analysis_run_id`, `revision_id`, `stock_id`, `original_validated_output_hash`, `model_version`, `seven_fields`, `confirmation_decisions`, `human_overrides`, `watch_items`, `confirmation_events`, `confirmation_log_hash`, `supersedes_analysis_report_id`, `supersedes_final_evidence_id`, `prepared_snapshot_hash`, `human_confirm`, `final_state` |
| FinalAnalysisSnapshot | `analysis_snapshots.payload_json（FINAL）` | `analysis_report_id`, `analysis_run_id`, `revision_id`, `stock_id`, `input_hash`, `model_output_hash`, `original_validated_output_hash`, `model_version`, `seven_fields`, `confirmation_decisions`, `human_overrides`, `watch_items`, `confirmation_events`, `confirmation_log_hash`, `prepared_snapshot_hash`, `confirmed_by`, `confirmed_at`, `supersedes_analysis_report_id`, `final_snapshot_hash`, `human_confirm`, `final_state` |

七字段各列保存整个 wrapper：AI_ORIGINAL、AI_OUTPUT_HASH、DECISION、EFFECTIVE_VALUE、
HUMAN_DECISION、HUMAN_OVERRIDE、ORIGIN、decision_reasons。驳回的 EFFECTIVE_VALUE=null 合法，
其余原值、理由与引用仍保留；整列不能变 NULL。
AnalysisConclusion 一对一对应最终报告 revision：id / 七字段 / model_version / calculation /
evidence_ids 直接读取，不新增实体表。calculation 保留 #010 已有全部键：analysis_run_id、
input_hash、model_output_hash、validated_output_hash、final_snapshot_hash、confirmation_log_hash、
final_evidence_id、prepared_snapshot_hash、confirmation_events、supersedes_analysis_report_id。

#010 对象均 DB-resident 于新七表；_gate_digest 仅 IN-MEMORY-only，不能存后重造 Gate 凭证。
反序列化只返回只读 DTO，不凭 hash 自动恢复可继续操作的 sealed domain 对象。
Provider 原始响应仍由已有 raw_data_path 引用，属既有 SIDECAR-resident 原始材料；
EvidenceRecord 的 endpoint_or_tool / request_params / provider_timestamp / validation_result /
raw_response_hash / normalized_data_hash / analysis_output_hash 唯一权威载体仍是冻结
Evidence Pipeline 的 metadata sidecar，不增设数据库元数据副本，不改变既有 sidecar 协议。
#010 对象不藏在 Provider sidecar，不塞 reasoning，不混为 EvidenceRecord。
HUMAN != EvidenceSource：HUMAN 仅 override.origin / 七字段 ORIGIN；Provider Source 仍只有三源。
FinalAnalysisEvidence 是 analysis-level 对象，有独立表、ID、hash 与 supersedes 链，不是第四源。

## 4. INSERT_ONLY、修订与顺序

```text
INSERT_ONLY=evidence_records,analysis_confirmation_events,analysis_human_overrides,analysis_snapshots,analysis_final_evidence
UPDATE=FORBIDDEN
DELETE=FORBIDDEN
ONLY_UPDATE_EXCEPTION=analysis_tasks.status
ANALYSIS_REPORTS=IMMUTABLE_FINAL_REVISIONS
ANALYSIS_HISTORY=LEGACY_READ_ONLY_HISTORY
CORRECTION=NEW_REVISION_PLUS_SUPERSEDES
```

上述五表仅 INSERT；analysis_reports 也只追加正式 revision，不 UPDATE/DELETE。
analysis_tasks 除 status 外所有列不可 UPDATE，任务也不可 DELETE。status 是运行投影，
不是不可变 run_evidence_json.analysis_status 或人工确认状态；不能覆盖 original draft。
禁止 INSERT OR REPLACE / REPLACE / UPSERT 改写事实；相同 ID+相同内容重试先校验后 no-op，
相同 ID+不同内容拒绝。测试触发器实现删除/更新禁止和重复 identity 拒绝；recursive_triggers=ON。

每次纠错追加 confirmation event；正式纠错创建新 report_id、revision_id、final_evidence_id，
使用显式 supersedes_analysis_report_id / supersedes_final_evidence_id。两个链都不能塞无关 JSON。
旧 final snapshot / conclusion / confirmation log/hash 不变。旧 evidence 修正也追加新 evidence_id
及 supersedes_evidence_id。三个 supersedes 字段允许多个后继，保留 FK 和 no-self-reference
检查；persistence 不引入单后继规则，DOMAIN_LOGIC_CHANGE=NO。不能用 timestamp 或 ID 字典序
选择胜者。同一 report revision 至多一个 PREPARED candidate，每 report 至多一个 FINAL。

确认日志 event_sequence 按 report/revision 连续递增；相同 created_at 不影响稳定顺序。
FINAL payload.confirmation_events 是生成时有序前缀，后续事件追加到日志表，不修改旧 snapshot。
analysis_reports.human_confirm 固定生成时 CONFIRMED；当前状态由追加日志和 revision 链派生，
被驳回的后继不自动恢复已驳回的旧 revision。

## 5. JSON、时间与 hash

UTF-8、ensure_ascii=False、sort_keys=True、separators=(",", ":")、allow_nan=False，
与 analysis.contracts.stable_json 一致。不改变 Unicode、数字类型、数组顺序或时间 offset 原文。
解码拒绝 duplicate keys（含转义后重复）、NaN、Infinity、数值溢出及非字符串编码 key；
不能通过键重命名吞掉语义重复键。enum 为 TEXT，HumanConfirm 仅 PENDING / CONFIRMED / REJECTED。
时间为 timezone-aware ISO8601 TEXT（日期 task_date 例外为 YYYY-MM-DD）；hash 为小写 64 字节 hex。

model_output_hash 重算原 raw_model_output UTF-8；validated_output_hash 重算 draft.output；
input_hash 从保存的 PromptEnvelope 按 output_validator.prompt_inputs 的既有规则重算；
prepared/final_snapshot_hash 从完整相应 payload 剔除自己的 hash key 重算；
confirmation_log_hash 从该 snapshot 的有序 confirmation_events 重算。
Evidence 的 raw/normalized/analysis 三 hash 保留原算法及元数据，缺值如实 NULL。
不能靠 SQL CHECK 证明 hash、规范 JSON 或上游硬 Gate 已通过。

## 6. 事务合同与 #012 必须实施的校验

```text
BEGIN
  校验/关联同身份 task 与不可变 run/draft/input/raw archive
  INSERT 最终 report revision
  INSERT confirmation events / overrides / PREPARED与FINAL snapshots
  INSERT final evidence 与有序 evidence ID bindings
  校验全部 payload/scalar/hash/引用/revision 一致性
COMMIT
任一步骤失败（包括 deferred FK 导致 COMMIT 失败）→ ROLLBACK
```

本轮只测试 :memory: 的 schema/约束与原子回滚；不实现 repository 或生产事务。
#012 必须在同 connection/transaction 开启并验证 foreign_keys=ON、recursive_triggers=ON，
校验 ID/stock/model/hash 同绑定、snapshot_kind 与 final_state/human_confirm 一致、
final report 三个互指对象是同一 report/run/revision、PREPARED hash 对应人工审阅对象。
校验 event_sequence 连续、previous/current state 链、override 同作用域、FINAL 日志前缀/内容一致；
校验 JSON ID arrays 无重复、有序、used⊆input、全部 referenced IDs 存在并属于 Provider Evidence。
SQLite JSON arrays 无逐元素 FK，所以这些是强制 repository 校验，不冒称数据库已强制。

pending report/revision ID 为上游显式分配，events/overrides/PREPARED 不强制 FK 到尚未产生的最终报告；
仍须存在 task。pending 可先单独持久化，FINAL 提交须检查同作用域所有绑定且无半成品。
所有 predecessor 必须已存在、同证券、同逻辑链、不能自引用/循环；report 和 final evidence 的
predecessor 配对校验及当前有效状态查询均属于 #012。不猜测 legacy record 归属。

## 7. 旧历史与后续顺序

analysis_history=LEGACY_READ_ONLY_HISTORY。旧 32 表不 DROP/RENAME/ALTER/UPDATE/DELETE，
不回填旧记录，不改 INTEGER PK 或旧 FK；Dashboard 旧历史必须标 record_origin=LEGACY_HISTORY。
只读接口与 DTO 要求沿用 [审计文档](../PERSISTENCE_READINESS_011.md)，实现归 #012；#011 不做 Dashboard。

只有 PERSISTENCE_READINESS=READY 才允许定义 #012 正式 migration；READY 表示映射/契约可实施，
不授权 #011 连接或写入真实库，也不冒称生产数据库已经 READY。
#012 顺序：V1.1 schema → WAL-safe backup → controlled additive migration → repository implementation
→ transaction atomicity → read-model。真实 legacy DDL/约束快照和现场完整 integrity_check 等元数据
仍由后续获授权流程补齐，不能把 inventory 缺失的约束补成假 legacy schema。
