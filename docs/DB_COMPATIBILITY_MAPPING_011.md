# #011 真实 SQLite 兼容映射：RESUME_AUTHORIZED

```text
G0_PERSISTENCE_READINESS=BLOCKED
EXECUTION_STATUS=RESUME_AUTHORIZED
PERSISTENCE_READINESS=READY
READINESS_SCOPE=V1.1_MAPPING_AND_SCHEMA_CONTRACT
SCHEMA_UNFREEZE=APPROVED_BY_QI
QI_DECISION=OPTION_A
DB_SCHEMA_TARGET=V1.1
UNFREEZE_SCOPE=DB_SCHEMA_ONLY
REAL_DB_CONNECTION=FORBIDDEN
```

本报告按 G0 BLOCKED → qi OPTION A → RESUME_AUTHORIZED 保留证据链。
下方原 G0 各节是解冻前历史审计，过去的“待批准/缺口”状态由末尾 RESUME 映射解决，
绝不宣称新表已经出现在真实库。V1.1 是获批新契约；CURRENT 仍指原 32 表。

输入与审计限制见 [G0 阻塞报告](PERSISTENCE_READINESS_011.md)。
四态严格使用 `EXISTING_EQUIVALENT / PARTIAL_EQUIVALENT / MISSING / CONFLICT`。
PARTIAL_EQUIVALENT 表示已识别相近职责候选，不证明任何旧业务列与新字段语义等价。
本报告无 CONFLICT 实证，也没有可无损 REUSE 的完整核心对象。

## Compatibility Matrix

CURRENT_COLUMNS 的 AH/SR 完整清单在下一节；∅ 表示不存在物理表/获批承载字段。
WRITE_STRATEGY / READ_STRATEGY 描述本轮允许行为，未批准的未来映射单独注明。

| LOGICAL_ENTITY | CURRENT_PHYSICAL_TABLE | CURRENT_COLUMNS | FROZEN_REQUIRED_FIELDS | MATCH_STATUS | WRITE_STRATEGY | READ_STRATEGY | MIGRATION_REQUIRED | RISK |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| analysis_tasks | ∅；screening_runs 仅被冻结文档提及，不等价 | ∅；SR 为被排除候选 | id, stock_id, task_date, prompt_version, model_version, status, created_by, created_at | MISSING | BLOCKED；冻结规则为获批后 CREATE | 无新分析任务读取能力 | YES | 身份、创建人和证券关联缺失 |
| analysis_reports | analysis_history（候选，非实际 analysis_reports） | AH | id, task_id, market_view, position_status, risk_level, operation_plan, t_strategy, reasoning, confidence, human_confirm, created_at | PARTIAL_EQUIVALENT | BLOCKED；未来 EXTEND_OR_MIGRATE 或最小 CREATE 需批准 | 旧历史职责不能冒充 #010 read DTO | YES | 七字段、确认历史及 revision 元数据缺失 |
| evidence_records | ∅ | ∅ | id, analysis_report_id nullable, source, timestamp, raw_data_path nullable, calculation nullable, model_version nullable, human_confirm, collection_task_id nullable, system_run_id nullable, created_at | MISSING | BLOCKED；冻结规则为获批后 CREATE | 当前不存在通用证据读取位置 | YES | 三源约束、immutable 历史和关联均无承载 |
| AnalysisRunEvidence | ∅ | ∅ | #009 完整字段见逐字段表 | MISSING | PERSISTENCE_MAPPING_GAP | 仅已有进程内对象 | YES 或获批混合合同 | run provenance 超出冻结 task 字段 |
| ConfirmationEvent | ∅ | ∅ | #010 完整字段见逐字段表 | MISSING | PERSISTENCE_MAPPING_GAP；不可塞 reasoning | 无持久化有序日志 | A: YES；B: 合同待批准 | 无法重建当前确认/历史/hash |
| HumanOverride | ∅ | ∅ | #010 完整字段见逐字段表 | MISSING | PERSISTENCE_MAPPING_GAP；不可作 Provider Evidence | 无持久化原值/替代值/撤销历史 | A: YES；B: 合同待批准 | 审核理由、身份与引用缺失 |
| PreparedFinalSnapshot | ∅ | ∅ | #010 完整字段见逐字段表 | MISSING | 当前 IN-MEMORY-only；不得存为 FINAL | 当前仅进程内查看候选 | 持久化恢复若要求则需 A/B | Gate 不可仅凭 JSON 恢复 |
| FinalAnalysisSnapshot | analysis_history 仅历史报告候选 | AH；没有直接承载完整快照的列 | #010 完整字段见逐字段表 | PARTIAL_EQUIVALENT | PERSISTENCE_MAPPING_GAP | 不能无损构造快照 | YES；附加内容需 A/B | FINAL hash、原值/有效值/决定与 correction 丢失 |
| FinalAnalysisEvidence | ∅ | ∅ | #010 完整字段见逐字段表 | MISSING | PERSISTENCE_MAPPING_GAP；不得写成 EvidenceRecord | 无最终证据元数据读取位置 | A: YES；B: 合同待批准 | 与三源证据的身份/语义不同 |
| AnalysisConclusion | analysis_history 仅部分报告职责 | AH | analysis_report_id, evidence_ids, structured_fields, model_version, calculation | PARTIAL_EQUIVALENT | 无独立无损映射；未来逻辑视图可行但需完整 backing | 当前不能重建完整 Conclusion | backing migration YES | 缺 evidence relation / final evidence binding |
| supersedes_analysis_report_id / supersedes_final_evidence_id | ∅ | ∅ | 显式旧 report / final evidence identity | MISSING | PERSISTENCE_MAPPING_GAP；不得塞任意旧字段 | 无可靠 revision 链 | A: YES；B: 合同待批准 | 旧修订误恢复、循环/悬空/分支不可判定 |

## 真实候选表：精确列定义

AH = analysis_history：

| 列 | type | not_null | pk_position |
| --- | --- | --- | --- |
| id | INTEGER | true | 1 |
| query_id | VARCHAR(64) | false | 0 |
| code | VARCHAR(10) | true | 0 |
| name | VARCHAR(50) | false | 0 |
| report_type | VARCHAR(16) | false | 0 |
| sentiment_score | INTEGER | false | 0 |
| operation_advice | VARCHAR(20) | false | 0 |
| trend_prediction | VARCHAR(50) | false | 0 |
| analysis_summary | TEXT | false | 0 |
| raw_result | TEXT | false | 0 |
| news_content | TEXT | false | 0 |
| context_snapshot | TEXT | false | 0 |
| ideal_buy | FLOAT | false | 0 |
| secondary_buy | FLOAT | false | 0 |
| stop_loss | FLOAT | false | 0 |
| take_profit | FLOAT | false | 0 |
| created_at | DATETIME | false | 0 |

PK=[id]，FK=[]。五个索引均非 unique：
ix_analysis_history_created_at(created_at)、ix_analysis_history_code(code)、
ix_analysis_history_query_id(query_id)、ix_analysis_history_report_type(report_type)、
ix_analysis_code_time(code, created_at)。其他旧表对 analysis_history.id 有 FK 引用，
包括 backtest_results 和 skill_opinion_samples；不得替换其 INTEGER PK 或破坏历史引用。

SR = screening_runs 的完整列：id INTEGER NOT NULL PK(1)、run_id VARCHAR(64) NOT NULL、
strategy VARCHAR(64) NOT NULL、market VARCHAR(16) NOT NULL、snapshot_source VARCHAR(64)、
snapshot_count INTEGER、after_filter_count INTEGER、candidate_count INTEGER NOT NULL、
llm_ranked BOOLEAN、daily_enriched BOOLEAN、source_errors_json TEXT、warnings_json TEXT、
result_json TEXT NOT NULL、created_at DATETIME NOT NULL。
其余列 not_null=false / pk_position=0，除 id 外所有列 pk_position=0；FK=[]。
7 个索引，其中 run_id 为 unique，另外六个为非 unique，覆盖 snapshot_source / strategy /
market / created_at / (market, created_at) / (strategy, created_at)。
冻结 Schema 已明确它是筛选运行，不能机械迁为 analysis_tasks。

全 32 表列名中，confirm / override / supersed / revision / evidence / snapshot_hash /
input_hash / validated_output_hash / model_output_hash 相关搜索仅命中
decision_signals.evidence_json。该 TEXT 列不构成通用 EvidenceRecord、最终证据或确认日志合同。
agent_provider_turns.messages_json 等通用载荷列也没有已批准的 #010 语义，不能借用。

## 冻结三实体逐字段结论

| 逻辑字段（每组适用同一结论） | 当前真实承载 | 判定 |
| --- | --- | --- |
| analysis_tasks.id | ∅；screening_runs.run_id 不等价 | MISSING |
| analysis_tasks.stock_id / task_date / prompt_version / model_version / status / created_by / created_at | ∅；不得由 market、strategy、created_at 猜测任务语义 | MISSING |
| analysis_reports.id | 仅 analysis_history.id INTEGER 候选；#010 report ID 是字符串，非证明等价 | PARTIAL_EQUIVALENT |
| analysis_reports.task_id | ∅；query_id 的索引非 unique，且无任务 FK，不能认作 task_id | MISSING |
| analysis_reports.market_view | ∅；trend_prediction 不是结构化同义字段 | MISSING |
| analysis_reports.position_status | ∅ | MISSING |
| analysis_reports.risk_level | ∅ | MISSING |
| analysis_reports.operation_plan | ∅；operation_advice 不是完整七字段对象 | MISSING |
| analysis_reports.t_strategy | ∅ | MISSING |
| analysis_reports.reasoning | ∅；analysis_summary 不能证明推理结构与引用等价 | MISSING |
| analysis_reports.confidence | ∅；sentiment_score 不是 confidence | MISSING |
| analysis_reports.human_confirm | ∅；无确认状态与日志列 | MISSING |
| analysis_reports.created_at | analysis_history.created_at DATETIME 可空候选；无 timezone 证明 | PARTIAL_EQUIVALENT |
| evidence_records.id / analysis_report_id / source / timestamp / raw_data_path / calculation / model_version / human_confirm / collection_task_id / system_run_id / created_at | ∅；整张实体缺失，以上每字段均无明确位置 | MISSING |

## #009 / #010 全字段承载审计

以下公开字段清单直接核对既有 dataclass，未推测新增列。除上表明确的旧报告候选外，
每个所列字段目前均无获批真实存储位置；结论为 PERSISTENCE_MAPPING_GAP。
`_gate_digest` 为私有进程内标记，明确排除于持久化字段。

| 对象 | 全部公开字段 | 当前分类 |
| --- | --- | --- |
| AnalysisRunEvidence | analysis_run_id, stock_id, prompt_version, template_hash, input_hash, output_schema_version, model_version, model_output_hash, validated_output_hash, input_evidence_ids, used_evidence_ids, created_at, analysis_status, human_confirm, final_conclusion | NOT-PERSISTABLE-WITH-CURRENT-SCHEMA |
| ConfirmationEvent | event_id, analysis_run_id, analysis_report_id, revision_id, target, reason_code, previous_state, current_state, actor_id, decision_reason, created_at, override_id, human_decision, reviewed_snapshot_hash | NOT-PERSISTABLE-WITH-CURRENT-SCHEMA |
| HumanOverride | override_id, analysis_run_id, analysis_report_id, revision_id, target, reason_code, original_value_hash, replacement, reason, actor_id, created_at, referenced_evidence_ids, origin, human_confirm | NOT-PERSISTABLE-WITH-CURRENT-SCHEMA |
| PreparedFinalSnapshot | analysis_report_id, analysis_run_id, revision_id, stock_id, original_validated_output_hash, model_version, seven_fields, confirmation_decisions, human_overrides, watch_items, confirmation_events, confirmation_log_hash, supersedes_analysis_report_id, supersedes_final_evidence_id, prepared_snapshot_hash, human_confirm, final_state | IN-MEMORY-only；恢复/跨进程持久化未批准 |
| FinalAnalysisSnapshot | analysis_report_id, analysis_run_id, revision_id, stock_id, input_hash, model_output_hash, original_validated_output_hash, model_version, seven_fields, confirmation_decisions, human_overrides, watch_items, confirmation_events, confirmation_log_hash, prepared_snapshot_hash, confirmed_by, confirmed_at, supersedes_analysis_report_id, final_snapshot_hash, human_confirm, final_state | NOT-PERSISTABLE-WITH-CURRENT-SCHEMA |
| FinalAnalysisEvidence | final_evidence_id, analysis_report_id, analysis_run_id, stock_id, model_version, input_hash, model_output_hash, validated_output_hash, final_snapshot_hash, confirmation_log_hash, input_evidence_ids, used_evidence_ids, confirmed_by, confirmed_at, supersedes_final_evidence_id, human_confirm | NOT-PERSISTABLE-WITH-CURRENT-SCHEMA |
| AnalysisConclusion | analysis_report_id, evidence_ids, structured_fields, model_version, calculation | NOT-PERSISTABLE-WITH-CURRENT-SCHEMA |

没有 #010 新对象可判为已经 DB-resident 或已批准 SIDECAR-resident。
现有 domain 对象运行于内存，不意味着其必须长期保持 IN-MEMORY-only；上表标明的是
当前映射能力。PreparedFinalSnapshot 明确为候选，不得冒充 FINAL report。

## 七字段与 run / report 身份

| FinalSnapshot 路径 | 冻结逻辑候选 | 实际 DB 结论 |
| --- | --- | --- |
| seven_fields.market_view | analysis_reports.market_view | 实际表/列均不存在 |
| seven_fields.position_status | analysis_reports.position_status | 实际表/列均不存在 |
| seven_fields.risk_level | analysis_reports.risk_level | 实际表/列均不存在 |
| seven_fields.operation_plan | analysis_reports.operation_plan | 实际表/列均不存在 |
| seven_fields.t_strategy | analysis_reports.t_strategy | 实际表/列均不存在 |
| seven_fields.reasoning | analysis_reports.reasoning | 实际表/列均不存在 |
| seven_fields.confidence | analysis_reports.confidence | 实际表/列均不存在 |

每个 seven_fields 项都包含 AI_ORIGINAL / AI_OUTPUT_HASH / DECISION / EFFECTIVE_VALUE /
HUMAN_DECISION / HUMAN_OVERRIDE / ORIGIN / decision_reasons。
只写 EFFECTIVE_VALUE 会丢原 AI、驳回原因、人工修改与 hash 输入；只写 AI_ORIGINAL 会丢
最终有效判断。未来将完整 wrapper 存为该逻辑列的 JSON object 是**待批准解释**，
冻结 DB Schema 未定义列类型/JSON 载荷合同，不能当成已获批映射。
驳回字段的 EFFECTIVE_VALUE=null 是 wrapper 内的合法值，不能用整列 NULL 丢弃 wrapper。

AnalysisRunEvidence 与 analysis_tasks 职责相关，但不能整体直接映入冻结 task 八字段。
task_id/run_id 同一身份方案尚未批准，实际 task 表不存在，冻结 schema 未指定 PK 类型。
不能复用筛选 run_id、转换为 analysis_history.id INTEGER 或自行生成替代 ID。
task_date、created_by 不在 RunEvidence 中，不能截取 created_at 或使用确认 actor 偷补。
template/output-schema/hash、输入/使用证据集合、原 envelope 和模型响应还需要无损承载。
status 与 analysis_status/human_confirm/final_conclusion 也不应合并为一个猜测 enum。

AnalysisConclusion 在 #010 工厂中与 FinalAnalysisSnapshot 共用 analysis_report_id，
每个 finalization 产生一个 Conclusion：**逻辑上一份 report revision 对一份 Conclusion**。
它可作为 report + run provenance + final evidence + evidence relations 的只读逻辑视图，
无需据此擅自新建 Conclusion 表；但当前 backing 不完整，视图不能无损实现。
evidence_ids 为 run.used_evidence_ids，不能混入 HumanOverride 的上下文引用。
calculation 既有键：analysis_run_id / input_hash / model_output_hash / validated_output_hash /
final_snapshot_hash / confirmation_log_hash / final_evidence_id / prepared_snapshot_hash /
confirmation_events / supersedes_analysis_report_id。
这些是既有 domain Calculation 内容，不能据此将其塞到某条 Provider EvidenceRecord
的 calculation 或报告 reasoning 来假装已有存储合同。

## Sidecar 与修订的明确缺口

冻结 Evidence 设计仅批准 Provider 元数据 sidecar 承载 endpoint_or_tool / request_params /
provider_timestamp / raw_response_hash / normalized_data_hash / analysis_output_hash，
关联 raw_response_path。原始响应和元数据仍须原子归档；本轮没有新 sidecar 写入。

EvidenceRecord 的其他字段对应冻结 evidence_records 逻辑字段，但实际物理表缺失。
supersedes_evidence_id 在既有 EvidenceRecord domain 中存在，冻结表必填清单没有对应列；
这也是需明确批准的承载缺口，不能拿 collection_task_id 或 system_run_id 作链字段。
analysis_report_id 与证据可被多份报告复用的 relation 语义，也需批准独立无损关联机制；
禁止为挂上新报告 UPDATE 旧 EvidenceRecord。

ConfirmationEvent、HumanOverride、FinalAnalysisEvidence、完整 final snapshot、
有序事件日志、两个 #010 supersedes 字段不在已批准的 Provider sidecar 范围。
它们也不能因数据是 JSON 就自动归入 sidecar。
确认事件的 created_at 可相同，不足以恢复事件顺序；需要批准稳定日志顺序载体，
以保持 previous/current 状态链和 confirmation_log_hash。
冻结 analysis_reports.human_confirm 单列即使未来存在，也不足以保存事后纠错与历史。

因此需要 qi 的 A/B 决策及缺失冻结实体的受控迁移授权；选择前保持 BLOCKED，
不创建新表、不编写 migration SQL、不借任意旧 JSON/TEXT 字段规避缺口。

## RESUME：获批 V1.1 七表逐字段映射

[冻结 V1.1](frozen/DB_SCHEMA_V1.1.md) 与 [qi 决策](decisions/DB_SCHEMA_V1_1_PERSISTENCE_UNFREEZE_20261003.md)
规定新增七表，MIGRATION_POLICY=ADDITIVE_ONLY；旧 32 表保持不变。没有实际 migration apply。

| LOGICAL_ENTITY | CURRENT_PHYSICAL_TABLE / CURRENT_COLUMNS | FROZEN_REQUIRED_FIELDS | MATCH_STATUS | WRITE_STRATEGY | READ_STRATEGY | MIGRATION_REQUIRED | RISK |
| --- | --- | --- | --- | --- | --- | --- | --- |
| AnalysisRunEvidence / ValidatedAnalysisDraft | ∅ | analysis_tasks 下方完整字段 | MISSING | #012 additive CREATE；run_id 原样为 task.id | 新任务 read DTO；不恢复 Gate seal | YES（#012） | explicit actor/date/input/raw provenance 必须齐全 |
| FinalAnalysisSnapshot / AnalysisConclusion | analysis_history / AH（仅旧候选） | analysis_reports 七字段、revision、calculation、绑定 | PARTIAL_EQUIVALENT | #012 additive CREATE，旧 history 只读 | 1 report revision ↔ 1 Conclusion；新报告 read DTO | YES（#012） | 未验证 legacy 语义不可混用 |
| EvidenceRecord | ∅ | evidence_records 下方完整字段 | MISSING | #012 additive CREATE；INSERT_ONLY | 仅三源与原始数据 trace | YES（#012） | JSON 引用必须强校验，不是 SQL FK |
| ConfirmationEvent | ∅ | analysis_confirmation_events 全公开字段 + event_sequence | MISSING | #012 additive CREATE；INSERT_ONLY | 按稳定 ordinal 读历史/当前派生状态 | YES（#012） | timestamp 不能代替顺序 |
| HumanOverride | ∅ | analysis_human_overrides 全公开字段 | MISSING | #012 additive CREATE；INSERT_ONLY | 原值 hash / replacement / 参考证据 DTO | YES（#012） | HUMAN ≠ EvidenceSource |
| PreparedFinalSnapshot / FinalAnalysisSnapshot | ∅ | analysis_snapshots 身份/kind/hash/完整 payload | MISSING | #012 additive CREATE；INSERT_ONLY | 区分 PREPARED 和 FINAL；只读 DTO | YES（#012） | 不靠 JSON 重造 _gate_digest |
| FinalAnalysisEvidence | ∅ | analysis_final_evidence 全公开字段 | MISSING | #012 additive CREATE；INSERT_ONLY | analysis-level final trace | YES（#012） | 不冒充 Provider Evidence |

每个新列的 CURRENT_COLUMNS=∅（未迁移）；以下是明确获批的 TARGET_COLUMNS 映射，
不是对现有库 guessed column mapping。MATCH_STATUS 保持冻结四态；RESUME_AUTHORIZED 是执行状态。

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

### 嵌套 JSON 全字段映射及分类

| 对象 | JSON 载体 | 完整公开字段（每项映射为同名 JSON key） |
| --- | --- | --- |
| AnalysisRunEvidence | `analysis_tasks.run_evidence_json` | `analysis_run_id`, `stock_id`, `prompt_version`, `template_hash`, `input_hash`, `output_schema_version`, `model_version`, `model_output_hash`, `validated_output_hash`, `input_evidence_ids`, `used_evidence_ids`, `created_at`, `analysis_status`, `human_confirm`, `final_conclusion` |
| ValidatedAnalysisDraft | `analysis_tasks.validated_draft_json` | `output`, `human_confirmation_items`, `input_hash`, `template_hash`, `model_output_hash`, `validated_output_hash`, `input_evidence_ids`, `used_evidence_ids`, `output_schema_version`, `analysis_status`, `human_confirm`, `final_state` |
| PromptEnvelope | `analysis_tasks.prompt_envelope_json` | `prompt_version`, `stock_id`, `position_mode`, `analysis_mode`, `system_prompt`, `user_prompt`, `evidence_ids`, `input_hash`, `template_hash`, `NETWORK_CALL_COUNT`, `MODEL_CALL_COUNT`, `DATABASE_WRITE` |
| PreparedFinalSnapshot | `analysis_snapshots.payload_json（PREPARED）` | `analysis_report_id`, `analysis_run_id`, `revision_id`, `stock_id`, `original_validated_output_hash`, `model_version`, `seven_fields`, `confirmation_decisions`, `human_overrides`, `watch_items`, `confirmation_events`, `confirmation_log_hash`, `supersedes_analysis_report_id`, `supersedes_final_evidence_id`, `prepared_snapshot_hash`, `human_confirm`, `final_state` |
| FinalAnalysisSnapshot | `analysis_snapshots.payload_json（FINAL）` | `analysis_report_id`, `analysis_run_id`, `revision_id`, `stock_id`, `input_hash`, `model_output_hash`, `original_validated_output_hash`, `model_version`, `seven_fields`, `confirmation_decisions`, `human_overrides`, `watch_items`, `confirmation_events`, `confirmation_log_hash`, `prepared_snapshot_hash`, `confirmed_by`, `confirmed_at`, `supersedes_analysis_report_id`, `final_snapshot_hash`, `human_confirm`, `final_state` |

每个列出的公开字段逐一保留为载体中的同名 key；不省略 final_state、human_confirm、
analysis_status、final_conclusion、两类 supersedes 或任何 hash。_gate_digest 仅 IN-MEMORY-only。
所有 #010 新对象目标为 DB-resident；旧 Provider raw_response_path 为既有 SIDECAR-resident 原材料，
不批准用 sidecar 隐藏 #010 对象。

analysis_reports 七列各存完整 wrapper，不只存 EFFECTIVE_VALUE。
AnalysisConclusion.evidence_ids = analysis_reports.evidence_ids；structured_fields 为七列组成的对象；
calculation 为同名 JSON 列；id = analysis_reports.id，不新建第八表。
Snapshot 的 supersedes 字段在 payload 中原样保存，同时 FINAL 的 report predecessor
映射显式 analysis_reports.supersedes_analysis_report_id；FinalAnalysisEvidence predecessor
映射显式 analysis_final_evidence.supersedes_final_evidence_id，不能省略互相配对校验。
EvidenceRecord.normalization_rule 在 calculation.normalization_rule；endpoint_or_tool / request_params /
provider_timestamp / validation_result（含 status/failures）/ raw_response_hash / normalized_data_hash /
analysis_output_hash 继续仅由冻结 Evidence Pipeline 的 metadata sidecar 权威承载，数据库不设副本。
三个 supersedes 字段允许多个后继，保留 FK 与 no-self-reference；DOMAIN_LOGIC_CHANGE=NO。
以下 G0 inventory 中 decision_signals 的既有 metadata_json 是旧表原始字段，原样保留，与新
evidence_records 无关；本轮不修改 inventory、旧表或 Evidence Pipeline。

analysis_history=LEGACY_READ_ONLY_HISTORY；不 backfill，不强行转换新对象；record_origin=LEGACY_HISTORY。
唯一 UPDATE 例外为 analysis_tasks.status，五张指定 INSERT_ONLY 表及正式 report 不允许 UPDATE/DELETE。
事务、JSON/hash、ordered evidence arrays、FK 与 repository 强校验详见 V1.1 第 3–6 节。

## 真实 G0 32 表 inventory 纳入文档（仅 schema 元数据）

源归档：`~/workspace/stock-system/docs/freeze/DB_EXISTING_SCHEMA_INVENTORY_V1.json`。
本轮使用已在手的逐字节副本 `docs/freeze/DB_EXISTING_SCHEMA_INVENTORY_V1.json`，未重新访问源目录或数据库。
原文件 118662 字节，SHA-256=`c9eaf6d1bc8927c8c0dbdb6dd0404ddc52751b2bcefbbad1c5b66a406fbadbd2`。
下面投影完整保留 32 表 name / columns(name,type,not_null,pk_position) / primary_key / indexes /
foreign_keys，以及必要 SQLite 状态；主动省略全部 row_count 和文件大小，不含业务行。
原 inventory 保持不变且不提交，以本节完整 schema 投影作为可审查交付。
投影 canonical JSON SHA-256=`9bbfe333e79695671bdd531bb2b77f83842edbf473a688918f32edbda2ba7fbb`。

inventory quick_check=ok、journal_mode=wal、sqlite_version=3.49.1、user_version=0、
foreign_key_violations=0；226 索引、11 外键。没有完整 integrity_check 或连接级 foreign_keys 元数据；
不将 quick_check 冒称完整检查。原 CREATE SQL/默认值/CHECK/触发器等缺失继续如实记录，
本轮只测试获批新表，不猜测重建 legacy schema。

<!-- G0_INVENTORY_BEGIN -->
```json
{
  "scope": "read_only_existing_sqlite_inventory",
  "database_path": "data/stock_analysis.db",
  "sqlite_version": "3.49.1",
  "journal_mode": "wal",
  "user_version": 0,
  "quick_check": "ok",
  "foreign_key_violations": 0,
  "table_count": 32,
  "tables": [
    {
      "name": "agent_provider_turns",
      "columns": [
        {
          "name": "id",
          "type": "INTEGER",
          "not_null": true,
          "pk_position": 1
        },
        {
          "name": "session_id",
          "type": "VARCHAR(100)",
          "not_null": true,
          "pk_position": 0
        },
        {
          "name": "run_id",
          "type": "VARCHAR(64)",
          "not_null": true,
          "pk_position": 0
        },
        {
          "name": "provider",
          "type": "VARCHAR(64)",
          "not_null": true,
          "pk_position": 0
        },
        {
          "name": "model",
          "type": "VARCHAR(160)",
          "not_null": true,
          "pk_position": 0
        },
        {
          "name": "anchor_user_message_id",
          "type": "INTEGER",
          "not_null": true,
          "pk_position": 0
        },
        {
          "name": "anchor_assistant_message_id",
          "type": "INTEGER",
          "not_null": true,
          "pk_position": 0
        },
        {
          "name": "messages_json",
          "type": "TEXT",
          "not_null": true,
          "pk_position": 0
        },
        {
          "name": "contains_reasoning",
          "type": "BOOLEAN",
          "not_null": true,
          "pk_position": 0
        },
        {
          "name": "contains_tool_calls",
          "type": "BOOLEAN",
          "not_null": true,
          "pk_position": 0
        },
        {
          "name": "contains_thinking_blocks",
          "type": "BOOLEAN",
          "not_null": true,
          "pk_position": 0
        },
        {
          "name": "must_roundtrip",
          "type": "BOOLEAN",
          "not_null": true,
          "pk_position": 0
        },
        {
          "name": "estimated_tokens",
          "type": "INTEGER",
          "not_null": true,
          "pk_position": 0
        },
        {
          "name": "created_at",
          "type": "DATETIME",
          "not_null": false,
          "pk_position": 0
        }
      ],
      "primary_key": [
        "id"
      ],
      "indexes": [
        {
          "name": "ix_agent_provider_turns_session_id",
          "unique": false,
          "columns": [
            "session_id"
          ]
        },
        {
          "name": "ix_agent_provider_turns_provider",
          "unique": false,
          "columns": [
            "provider"
          ]
        },
        {
          "name": "ix_agent_provider_turns_anchor_assistant_message_id",
          "unique": false,
          "columns": [
            "anchor_assistant_message_id"
          ]
        },
        {
          "name": "ix_agent_provider_turns_model",
          "unique": false,
          "columns": [
            "model"
          ]
        },
        {
          "name": "ix_agent_provider_turns_must_roundtrip",
          "unique": false,
          "columns": [
            "must_roundtrip"
          ]
        },
        {
          "name": "ix_agent_provider_turns_run_id",
          "unique": false,
          "columns": [
            "run_id"
          ]
        },
        {
          "name": "ix_agent_provider_turns_anchor_user_message_id",
          "unique": false,
          "columns": [
            "anchor_user_message_id"
          ]
        },
        {
          "name": "ix_agent_provider_turn_bucket",
          "unique": false,
          "columns": [
            "session_id",
            "provider",
            "model",
            "must_roundtrip"
          ]
        },
        {
          "name": "ix_agent_provider_turns_created_at",
          "unique": false,
          "columns": [
            "created_at"
          ]
        }
      ],
      "foreign_keys": []
    },
    {
      "name": "alert_cooldowns",
      "columns": [
        {
          "name": "id",
          "type": "INTEGER",
          "not_null": true,
          "pk_position": 1
        },
        {
          "name": "rule_id",
          "type": "INTEGER",
          "not_null": false,
          "pk_position": 0
        },
        {
          "name": "rule_key",
          "type": "VARCHAR(255)",
          "not_null": false,
          "pk_position": 0
        },
        {
          "name": "target",
          "type": "VARCHAR(64)",
          "not_null": true,
          "pk_position": 0
        },
        {
          "name": "severity",
          "type": "VARCHAR(16)",
          "not_null": true,
          "pk_position": 0
        },
        {
          "name": "last_triggered_at",
          "type": "DATETIME",
          "not_null": false,
          "pk_position": 0
        },
        {
          "name": "cooldown_until",
          "type": "DATETIME",
          "not_null": false,
          "pk_position": 0
        },
        {
          "name": "reason",
          "type": "TEXT",
          "not_null": false,
          "pk_position": 0
        },
        {
          "name": "state",
          "type": "VARCHAR(16)",
          "not_null": true,
          "pk_position": 0
        },
        {
          "name": "updated_at",
          "type": "DATETIME",
          "not_null": false,
          "pk_position": 0
        }
      ],
      "primary_key": [
        "id"
      ],
      "indexes": [
        {
          "name": "ix_alert_cooldowns_updated_at",
          "unique": false,
          "columns": [
            "updated_at"
          ]
        },
        {
          "name": "ix_alert_cooldowns_rule_id",
          "unique": false,
          "columns": [
            "rule_id"
          ]
        },
        {
          "name": "ix_alert_cooldowns_target",
          "unique": false,
          "columns": [
            "target"
          ]
        },
        {
          "name": "ix_alert_cooldowns_cooldown_until",
          "unique": false,
          "columns": [
            "cooldown_until"
          ]
        },
        {
          "name": "ix_alert_cooldowns_severity",
          "unique": false,
          "columns": [
            "severity"
          ]
        },
        {
          "name": "ix_alert_cooldowns_state",
          "unique": false,
          "columns": [
            "state"
          ]
        },
        {
          "name": "ix_alert_cooldowns_rule_key",
          "unique": false,
          "columns": [
            "rule_key"
          ]
        },
        {
          "name": "ix_alert_cooldowns_last_triggered_at",
          "unique": false,
          "columns": [
            "last_triggered_at"
          ]
        },
        {
          "name": "sqlite_autoindex_alert_cooldowns_1",
          "unique": true,
          "columns": [
            "rule_id",
            "target",
            "severity"
          ]
        }
      ],
      "foreign_keys": []
    },
    {
      "name": "alert_notifications",
      "columns": [
        {
          "name": "id",
          "type": "INTEGER",
          "not_null": true,
          "pk_position": 1
        },
        {
          "name": "trigger_id",
          "type": "INTEGER",
          "not_null": false,
          "pk_position": 0
        },
        {
          "name": "channel",
          "type": "VARCHAR(32)",
          "not_null": true,
          "pk_position": 0
        },
        {
          "name": "attempt",
          "type": "INTEGER",
          "not_null": true,
          "pk_position": 0
        },
        {
          "name": "success",
          "type": "BOOLEAN",
          "not_null": true,
          "pk_position": 0
        },
        {
          "name": "error_code",
          "type": "VARCHAR(64)",
          "not_null": false,
          "pk_position": 0
        },
        {
          "name": "retryable",
          "type": "BOOLEAN",
          "not_null": true,
          "pk_position": 0
        },
        {
          "name": "latency_ms",
          "type": "INTEGER",
          "not_null": false,
          "pk_position": 0
        },
        {
          "name": "diagnostics",
          "type": "TEXT",
          "not_null": false,
          "pk_position": 0
        },
        {
          "name": "created_at",
          "type": "DATETIME",
          "not_null": false,
          "pk_position": 0
        }
      ],
      "primary_key": [
        "id"
      ],
      "indexes": [
        {
          "name": "ix_alert_notification_trigger_channel",
          "unique": false,
          "columns": [
            "trigger_id",
            "channel"
          ]
        },
        {
          "name": "ix_alert_notifications_trigger_id",
          "unique": false,
          "columns": [
            "trigger_id"
          ]
        },
        {
          "name": "ix_alert_notifications_success",
          "unique": false,
          "columns": [
            "success"
          ]
        },
        {
          "name": "ix_alert_notifications_created_at",
          "unique": false,
          "columns": [
            "created_at"
          ]
        },
        {
          "name": "ix_alert_notifications_channel",
          "unique": false,
          "columns": [
            "channel"
          ]
        }
      ],
      "foreign_keys": []
    },
    {
      "name": "alert_rules",
      "columns": [
        {
          "name": "id",
          "type": "INTEGER",
          "not_null": true,
          "pk_position": 1
        },
        {
          "name": "name",
          "type": "VARCHAR(64)",
          "not_null": true,
          "pk_position": 0
        },
        {
          "name": "target_scope",
          "type": "VARCHAR(32)",
          "not_null": true,
          "pk_position": 0
        },
        {
          "name": "target",
          "type": "VARCHAR(64)",
          "not_null": true,
          "pk_position": 0
        },
        {
          "name": "alert_type",
          "type": "VARCHAR(32)",
          "not_null": true,
          "pk_position": 0
        },
        {
          "name": "parameters",
          "type": "TEXT",
          "not_null": true,
          "pk_position": 0
        },
        {
          "name": "severity",
          "type": "VARCHAR(16)",
          "not_null": true,
          "pk_position": 0
        },
        {
          "name": "enabled",
          "type": "BOOLEAN",
          "not_null": true,
          "pk_position": 0
        },
        {
          "name": "source",
          "type": "VARCHAR(16)",
          "not_null": true,
          "pk_position": 0
        },
        {
          "name": "cooldown_policy",
          "type": "TEXT",
          "not_null": false,
          "pk_position": 0
        },
        {
          "name": "notification_policy",
          "type": "TEXT",
          "not_null": false,
          "pk_position": 0
        },
        {
          "name": "created_at",
          "type": "DATETIME",
          "not_null": false,
          "pk_position": 0
        },
        {
          "name": "updated_at",
          "type": "DATETIME",
          "not_null": false,
          "pk_position": 0
        }
      ],
      "primary_key": [
        "id"
      ],
      "indexes": [
        {
          "name": "ix_alert_rules_source",
          "unique": false,
          "columns": [
            "source"
          ]
        },
        {
          "name": "ix_alert_rules_severity",
          "unique": false,
          "columns": [
            "severity"
          ]
        },
        {
          "name": "ix_alert_rules_created_at",
          "unique": false,
          "columns": [
            "created_at"
          ]
        },
        {
          "name": "ix_alert_rules_target_scope",
          "unique": false,
          "columns": [
            "target_scope"
          ]
        },
        {
          "name": "ix_alert_rules_target",
          "unique": false,
          "columns": [
            "target"
          ]
        },
        {
          "name": "ix_alert_rules_enabled",
          "unique": false,
          "columns": [
            "enabled"
          ]
        },
        {
          "name": "ix_alert_rule_type_target",
          "unique": false,
          "columns": [
            "alert_type",
            "target"
          ]
        },
        {
          "name": "ix_alert_rules_updated_at",
          "unique": false,
          "columns": [
            "updated_at"
          ]
        },
        {
          "name": "ix_alert_rules_alert_type",
          "unique": false,
          "columns": [
            "alert_type"
          ]
        }
      ],
      "foreign_keys": []
    },
    {
      "name": "alert_triggers",
      "columns": [
        {
          "name": "id",
          "type": "INTEGER",
          "not_null": true,
          "pk_position": 1
        },
        {
          "name": "rule_id",
          "type": "INTEGER",
          "not_null": false,
          "pk_position": 0
        },
        {
          "name": "target",
          "type": "VARCHAR(64)",
          "not_null": true,
          "pk_position": 0
        },
        {
          "name": "observed_value",
          "type": "FLOAT",
          "not_null": false,
          "pk_position": 0
        },
        {
          "name": "threshold",
          "type": "FLOAT",
          "not_null": false,
          "pk_position": 0
        },
        {
          "name": "reason",
          "type": "TEXT",
          "not_null": false,
          "pk_position": 0
        },
        {
          "name": "data_source",
          "type": "VARCHAR(64)",
          "not_null": false,
          "pk_position": 0
        },
        {
          "name": "data_timestamp",
          "type": "DATETIME",
          "not_null": false,
          "pk_position": 0
        },
        {
          "name": "triggered_at",
          "type": "DATETIME",
          "not_null": false,
          "pk_position": 0
        },
        {
          "name": "status",
          "type": "VARCHAR(16)",
          "not_null": true,
          "pk_position": 0
        },
        {
          "name": "diagnostics",
          "type": "TEXT",
          "not_null": false,
          "pk_position": 0
        }
      ],
      "primary_key": [
        "id"
      ],
      "indexes": [
        {
          "name": "ix_alert_triggers_target",
          "unique": false,
          "columns": [
            "target"
          ]
        },
        {
          "name": "ix_alert_triggers_status",
          "unique": false,
          "columns": [
            "status"
          ]
        },
        {
          "name": "ix_alert_trigger_rule_time",
          "unique": false,
          "columns": [
            "rule_id",
            "triggered_at"
          ]
        },
        {
          "name": "ix_alert_triggers_rule_id",
          "unique": false,
          "columns": [
            "rule_id"
          ]
        },
        {
          "name": "ix_alert_triggers_data_timestamp",
          "unique": false,
          "columns": [
            "data_timestamp"
          ]
        },
        {
          "name": "ix_alert_triggers_triggered_at",
          "unique": false,
          "columns": [
            "triggered_at"
          ]
        }
      ],
      "foreign_keys": []
    },
    {
      "name": "analysis_history",
      "columns": [
        {
          "name": "id",
          "type": "INTEGER",
          "not_null": true,
          "pk_position": 1
        },
        {
          "name": "query_id",
          "type": "VARCHAR(64)",
          "not_null": false,
          "pk_position": 0
        },
        {
          "name": "code",
          "type": "VARCHAR(10)",
          "not_null": true,
          "pk_position": 0
        },
        {
          "name": "name",
          "type": "VARCHAR(50)",
          "not_null": false,
          "pk_position": 0
        },
        {
          "name": "report_type",
          "type": "VARCHAR(16)",
          "not_null": false,
          "pk_position": 0
        },
        {
          "name": "sentiment_score",
          "type": "INTEGER",
          "not_null": false,
          "pk_position": 0
        },
        {
          "name": "operation_advice",
          "type": "VARCHAR(20)",
          "not_null": false,
          "pk_position": 0
        },
        {
          "name": "trend_prediction",
          "type": "VARCHAR(50)",
          "not_null": false,
          "pk_position": 0
        },
        {
          "name": "analysis_summary",
          "type": "TEXT",
          "not_null": false,
          "pk_position": 0
        },
        {
          "name": "raw_result",
          "type": "TEXT",
          "not_null": false,
          "pk_position": 0
        },
        {
          "name": "news_content",
          "type": "TEXT",
          "not_null": false,
          "pk_position": 0
        },
        {
          "name": "context_snapshot",
          "type": "TEXT",
          "not_null": false,
          "pk_position": 0
        },
        {
          "name": "ideal_buy",
          "type": "FLOAT",
          "not_null": false,
          "pk_position": 0
        },
        {
          "name": "secondary_buy",
          "type": "FLOAT",
          "not_null": false,
          "pk_position": 0
        },
        {
          "name": "stop_loss",
          "type": "FLOAT",
          "not_null": false,
          "pk_position": 0
        },
        {
          "name": "take_profit",
          "type": "FLOAT",
          "not_null": false,
          "pk_position": 0
        },
        {
          "name": "created_at",
          "type": "DATETIME",
          "not_null": false,
          "pk_position": 0
        }
      ],
      "primary_key": [
        "id"
      ],
      "indexes": [
        {
          "name": "ix_analysis_history_created_at",
          "unique": false,
          "columns": [
            "created_at"
          ]
        },
        {
          "name": "ix_analysis_history_code",
          "unique": false,
          "columns": [
            "code"
          ]
        },
        {
          "name": "ix_analysis_history_query_id",
          "unique": false,
          "columns": [
            "query_id"
          ]
        },
        {
          "name": "ix_analysis_history_report_type",
          "unique": false,
          "columns": [
            "report_type"
          ]
        },
        {
          "name": "ix_analysis_code_time",
          "unique": false,
          "columns": [
            "code",
            "created_at"
          ]
        }
      ],
      "foreign_keys": []
    },
    {
      "name": "backtest_results",
      "columns": [
        {
          "name": "id",
          "type": "INTEGER",
          "not_null": true,
          "pk_position": 1
        },
        {
          "name": "analysis_history_id",
          "type": "INTEGER",
          "not_null": true,
          "pk_position": 0
        },
        {
          "name": "code",
          "type": "VARCHAR(10)",
          "not_null": true,
          "pk_position": 0
        },
        {
          "name": "analysis_date",
          "type": "DATE",
          "not_null": false,
          "pk_position": 0
        },
        {
          "name": "eval_window_days",
          "type": "INTEGER",
          "not_null": true,
          "pk_position": 0
        },
        {
          "name": "engine_version",
          "type": "VARCHAR(16)",
          "not_null": true,
          "pk_position": 0
        },
        {
          "name": "eval_status",
          "type": "VARCHAR(16)",
          "not_null": true,
          "pk_position": 0
        },
        {
          "name": "evaluated_at",
          "type": "DATETIME",
          "not_null": false,
          "pk_position": 0
        },
        {
          "name": "operation_advice",
          "type": "VARCHAR(20)",
          "not_null": false,
          "pk_position": 0
        },
        {
          "name": "position_recommendation",
          "type": "VARCHAR(8)",
          "not_null": false,
          "pk_position": 0
        },
        {
          "name": "start_price",
          "type": "FLOAT",
          "not_null": false,
          "pk_position": 0
        },
        {
          "name": "end_close",
          "type": "FLOAT",
          "not_null": false,
          "pk_position": 0
        },
        {
          "name": "max_high",
          "type": "FLOAT",
          "not_null": false,
          "pk_position": 0
        },
        {
          "name": "min_low",
          "type": "FLOAT",
          "not_null": false,
          "pk_position": 0
        },
        {
          "name": "stock_return_pct",
          "type": "FLOAT",
          "not_null": false,
          "pk_position": 0
        },
        {
          "name": "direction_expected",
          "type": "VARCHAR(16)",
          "not_null": false,
          "pk_position": 0
        },
        {
          "name": "direction_correct",
          "type": "BOOLEAN",
          "not_null": false,
          "pk_position": 0
        },
        {
          "name": "outcome",
          "type": "VARCHAR(16)",
          "not_null": false,
          "pk_position": 0
        },
        {
          "name": "stop_loss",
          "type": "FLOAT",
          "not_null": false,
          "pk_position": 0
        },
        {
          "name": "take_profit",
          "type": "FLOAT",
          "not_null": false,
          "pk_position": 0
        },
        {
          "name": "hit_stop_loss",
          "type": "BOOLEAN",
          "not_null": false,
          "pk_position": 0
        },
        {
          "name": "hit_take_profit",
          "type": "BOOLEAN",
          "not_null": false,
          "pk_position": 0
        },
        {
          "name": "first_hit",
          "type": "VARCHAR(16)",
          "not_null": false,
          "pk_position": 0
        },
        {
          "name": "first_hit_date",
          "type": "DATE",
          "not_null": false,
          "pk_position": 0
        },
        {
          "name": "first_hit_trading_days",
          "type": "INTEGER",
          "not_null": false,
          "pk_position": 0
        },
        {
          "name": "simulated_entry_price",
          "type": "FLOAT",
          "not_null": false,
          "pk_position": 0
        },
        {
          "name": "simulated_exit_price",
          "type": "FLOAT",
          "not_null": false,
          "pk_position": 0
        },
        {
          "name": "simulated_exit_reason",
          "type": "VARCHAR(24)",
          "not_null": false,
          "pk_position": 0
        },
        {
          "name": "simulated_return_pct",
          "type": "FLOAT",
          "not_null": false,
          "pk_position": 0
        }
      ],
      "primary_key": [
        "id"
      ],
      "indexes": [
        {
          "name": "ix_backtest_results_analysis_history_id",
          "unique": false,
          "columns": [
            "analysis_history_id"
          ]
        },
        {
          "name": "ix_backtest_results_evaluated_at",
          "unique": false,
          "columns": [
            "evaluated_at"
          ]
        },
        {
          "name": "ix_backtest_results_code",
          "unique": false,
          "columns": [
            "code"
          ]
        },
        {
          "name": "ix_backtest_results_analysis_date",
          "unique": false,
          "columns": [
            "analysis_date"
          ]
        },
        {
          "name": "ix_backtest_code_date",
          "unique": false,
          "columns": [
            "code",
            "analysis_date"
          ]
        },
        {
          "name": "sqlite_autoindex_backtest_results_1",
          "unique": true,
          "columns": [
            "analysis_history_id",
            "eval_window_days",
            "engine_version"
          ]
        }
      ],
      "foreign_keys": [
        {
          "from": "analysis_history_id",
          "table": "analysis_history",
          "to": "id"
        }
      ]
    },
    {
      "name": "backtest_summaries",
      "columns": [
        {
          "name": "id",
          "type": "INTEGER",
          "not_null": true,
          "pk_position": 1
        },
        {
          "name": "scope",
          "type": "VARCHAR(16)",
          "not_null": true,
          "pk_position": 0
        },
        {
          "name": "code",
          "type": "VARCHAR(16)",
          "not_null": false,
          "pk_position": 0
        },
        {
          "name": "eval_window_days",
          "type": "INTEGER",
          "not_null": true,
          "pk_position": 0
        },
        {
          "name": "engine_version",
          "type": "VARCHAR(16)",
          "not_null": true,
          "pk_position": 0
        },
        {
          "name": "computed_at",
          "type": "DATETIME",
          "not_null": false,
          "pk_position": 0
        },
        {
          "name": "total_evaluations",
          "type": "INTEGER",
          "not_null": false,
          "pk_position": 0
        },
        {
          "name": "completed_count",
          "type": "INTEGER",
          "not_null": false,
          "pk_position": 0
        },
        {
          "name": "insufficient_count",
          "type": "INTEGER",
          "not_null": false,
          "pk_position": 0
        },
        {
          "name": "long_count",
          "type": "INTEGER",
          "not_null": false,
          "pk_position": 0
        },
        {
          "name": "cash_count",
          "type": "INTEGER",
          "not_null": false,
          "pk_position": 0
        },
        {
          "name": "win_count",
          "type": "INTEGER",
          "not_null": false,
          "pk_position": 0
        },
        {
          "name": "loss_count",
          "type": "INTEGER",
          "not_null": false,
          "pk_position": 0
        },
        {
          "name": "neutral_count",
          "type": "INTEGER",
          "not_null": false,
          "pk_position": 0
        },
        {
          "name": "direction_accuracy_pct",
          "type": "FLOAT",
          "not_null": false,
          "pk_position": 0
        },
        {
          "name": "win_rate_pct",
          "type": "FLOAT",
          "not_null": false,
          "pk_position": 0
        },
        {
          "name": "neutral_rate_pct",
          "type": "FLOAT",
          "not_null": false,
          "pk_position": 0
        },
        {
          "name": "avg_stock_return_pct",
          "type": "FLOAT",
          "not_null": false,
          "pk_position": 0
        },
        {
          "name": "avg_simulated_return_pct",
          "type": "FLOAT",
          "not_null": false,
          "pk_position": 0
        },
        {
          "name": "stop_loss_trigger_rate",
          "type": "FLOAT",
          "not_null": false,
          "pk_position": 0
        },
        {
          "name": "take_profit_trigger_rate",
          "type": "FLOAT",
          "not_null": false,
          "pk_position": 0
        },
        {
          "name": "ambiguous_rate",
          "type": "FLOAT",
          "not_null": false,
          "pk_position": 0
        },
        {
          "name": "avg_days_to_first_hit",
          "type": "FLOAT",
          "not_null": false,
          "pk_position": 0
        },
        {
          "name": "advice_breakdown_json",
          "type": "TEXT",
          "not_null": false,
          "pk_position": 0
        },
        {
          "name": "diagnostics_json",
          "type": "TEXT",
          "not_null": false,
          "pk_position": 0
        }
      ],
      "primary_key": [
        "id"
      ],
      "indexes": [
        {
          "name": "ix_backtest_summaries_code",
          "unique": false,
          "columns": [
            "code"
          ]
        },
        {
          "name": "ix_backtest_summaries_scope",
          "unique": false,
          "columns": [
            "scope"
          ]
        },
        {
          "name": "ix_backtest_summaries_computed_at",
          "unique": false,
          "columns": [
            "computed_at"
          ]
        },
        {
          "name": "sqlite_autoindex_backtest_summaries_1",
          "unique": true,
          "columns": [
            "scope",
            "code",
            "eval_window_days",
            "engine_version"
          ]
        }
      ],
      "foreign_keys": []
    },
    {
      "name": "conversation_messages",
      "columns": [
        {
          "name": "id",
          "type": "INTEGER",
          "not_null": true,
          "pk_position": 1
        },
        {
          "name": "session_id",
          "type": "VARCHAR(100)",
          "not_null": true,
          "pk_position": 0
        },
        {
          "name": "role",
          "type": "VARCHAR(20)",
          "not_null": true,
          "pk_position": 0
        },
        {
          "name": "content",
          "type": "TEXT",
          "not_null": true,
          "pk_position": 0
        },
        {
          "name": "created_at",
          "type": "DATETIME",
          "not_null": false,
          "pk_position": 0
        }
      ],
      "primary_key": [
        "id"
      ],
      "indexes": [
        {
          "name": "ix_conversation_messages_session_id",
          "unique": false,
          "columns": [
            "session_id"
          ]
        },
        {
          "name": "ix_conversation_messages_created_at",
          "unique": false,
          "columns": [
            "created_at"
          ]
        }
      ],
      "foreign_keys": []
    },
    {
      "name": "conversation_session_states",
      "columns": [
        {
          "name": "session_id",
          "type": "VARCHAR(100)",
          "not_null": true,
          "pk_position": 1
        },
        {
          "name": "selected_skill_ids_json",
          "type": "TEXT",
          "not_null": true,
          "pk_position": 0
        },
        {
          "name": "created_at",
          "type": "DATETIME",
          "not_null": true,
          "pk_position": 0
        },
        {
          "name": "updated_at",
          "type": "DATETIME",
          "not_null": true,
          "pk_position": 0
        }
      ],
      "primary_key": [
        "session_id"
      ],
      "indexes": [
        {
          "name": "sqlite_autoindex_conversation_session_states_1",
          "unique": true,
          "columns": [
            "session_id"
          ]
        }
      ],
      "foreign_keys": []
    },
    {
      "name": "conversation_summaries",
      "columns": [
        {
          "name": "id",
          "type": "INTEGER",
          "not_null": true,
          "pk_position": 1
        },
        {
          "name": "session_id",
          "type": "VARCHAR(100)",
          "not_null": true,
          "pk_position": 0
        },
        {
          "name": "summary",
          "type": "TEXT",
          "not_null": true,
          "pk_position": 0
        },
        {
          "name": "covered_message_id",
          "type": "INTEGER",
          "not_null": true,
          "pk_position": 0
        },
        {
          "name": "source_message_count",
          "type": "INTEGER",
          "not_null": true,
          "pk_position": 0
        },
        {
          "name": "estimated_tokens",
          "type": "INTEGER",
          "not_null": true,
          "pk_position": 0
        },
        {
          "name": "created_at",
          "type": "DATETIME",
          "not_null": false,
          "pk_position": 0
        },
        {
          "name": "updated_at",
          "type": "DATETIME",
          "not_null": false,
          "pk_position": 0
        }
      ],
      "primary_key": [
        "id"
      ],
      "indexes": [
        {
          "name": "ix_conversation_summaries_updated_at",
          "unique": false,
          "columns": [
            "updated_at"
          ]
        },
        {
          "name": "ix_conversation_summaries_created_at",
          "unique": false,
          "columns": [
            "created_at"
          ]
        },
        {
          "name": "ix_conversation_summaries_session_id",
          "unique": true,
          "columns": [
            "session_id"
          ]
        }
      ],
      "foreign_keys": []
    },
    {
      "name": "decision_signal_feedback",
      "columns": [
        {
          "name": "id",
          "type": "INTEGER",
          "not_null": true,
          "pk_position": 1
        },
        {
          "name": "signal_id",
          "type": "INTEGER",
          "not_null": true,
          "pk_position": 0
        },
        {
          "name": "feedback_value",
          "type": "VARCHAR(16)",
          "not_null": true,
          "pk_position": 0
        },
        {
          "name": "reason_code",
          "type": "VARCHAR(64)",
          "not_null": false,
          "pk_position": 0
        },
        {
          "name": "note",
          "type": "TEXT",
          "not_null": false,
          "pk_position": 0
        },
        {
          "name": "source",
          "type": "VARCHAR(16)",
          "not_null": true,
          "pk_position": 0
        },
        {
          "name": "created_at",
          "type": "DATETIME",
          "not_null": false,
          "pk_position": 0
        },
        {
          "name": "updated_at",
          "type": "DATETIME",
          "not_null": false,
          "pk_position": 0
        }
      ],
      "primary_key": [
        "id"
      ],
      "indexes": [
        {
          "name": "ix_decision_signal_feedback_signal_id",
          "unique": true,
          "columns": [
            "signal_id"
          ]
        },
        {
          "name": "ix_decision_signal_feedback_reason_code",
          "unique": false,
          "columns": [
            "reason_code"
          ]
        },
        {
          "name": "ix_decision_signal_feedback_updated_at",
          "unique": false,
          "columns": [
            "updated_at"
          ]
        },
        {
          "name": "ix_decision_signal_feedback_source",
          "unique": false,
          "columns": [
            "source"
          ]
        },
        {
          "name": "ix_decision_signal_feedback_feedback_value",
          "unique": false,
          "columns": [
            "feedback_value"
          ]
        },
        {
          "name": "ix_decision_signal_feedback_created_at",
          "unique": false,
          "columns": [
            "created_at"
          ]
        }
      ],
      "foreign_keys": []
    },
    {
      "name": "decision_signal_outcomes",
      "columns": [
        {
          "name": "id",
          "type": "INTEGER",
          "not_null": true,
          "pk_position": 1
        },
        {
          "name": "signal_id",
          "type": "INTEGER",
          "not_null": true,
          "pk_position": 0
        },
        {
          "name": "horizon",
          "type": "VARCHAR(16)",
          "not_null": true,
          "pk_position": 0
        },
        {
          "name": "engine_version",
          "type": "VARCHAR(32)",
          "not_null": true,
          "pk_position": 0
        },
        {
          "name": "eval_status",
          "type": "VARCHAR(24)",
          "not_null": true,
          "pk_position": 0
        },
        {
          "name": "outcome",
          "type": "VARCHAR(16)",
          "not_null": false,
          "pk_position": 0
        },
        {
          "name": "direction_expected",
          "type": "VARCHAR(16)",
          "not_null": false,
          "pk_position": 0
        },
        {
          "name": "direction_correct",
          "type": "BOOLEAN",
          "not_null": false,
          "pk_position": 0
        },
        {
          "name": "unable_reason",
          "type": "VARCHAR(64)",
          "not_null": false,
          "pk_position": 0
        },
        {
          "name": "anchor_date",
          "type": "DATE",
          "not_null": false,
          "pk_position": 0
        },
        {
          "name": "eval_window_days",
          "type": "INTEGER",
          "not_null": false,
          "pk_position": 0
        },
        {
          "name": "start_price",
          "type": "FLOAT",
          "not_null": false,
          "pk_position": 0
        },
        {
          "name": "end_close",
          "type": "FLOAT",
          "not_null": false,
          "pk_position": 0
        },
        {
          "name": "max_high",
          "type": "FLOAT",
          "not_null": false,
          "pk_position": 0
        },
        {
          "name": "min_low",
          "type": "FLOAT",
          "not_null": false,
          "pk_position": 0
        },
        {
          "name": "stock_return_pct",
          "type": "FLOAT",
          "not_null": false,
          "pk_position": 0
        },
        {
          "name": "action",
          "type": "VARCHAR(16)",
          "not_null": false,
          "pk_position": 0
        },
        {
          "name": "market",
          "type": "VARCHAR(8)",
          "not_null": false,
          "pk_position": 0
        },
        {
          "name": "market_phase",
          "type": "VARCHAR(24)",
          "not_null": false,
          "pk_position": 0
        },
        {
          "name": "source_type",
          "type": "VARCHAR(32)",
          "not_null": false,
          "pk_position": 0
        },
        {
          "name": "source_agent",
          "type": "VARCHAR(64)",
          "not_null": false,
          "pk_position": 0
        },
        {
          "name": "plan_quality",
          "type": "VARCHAR(16)",
          "not_null": false,
          "pk_position": 0
        },
        {
          "name": "data_quality_level",
          "type": "VARCHAR(24)",
          "not_null": false,
          "pk_position": 0
        },
        {
          "name": "holding_state",
          "type": "VARCHAR(16)",
          "not_null": true,
          "pk_position": 0
        },
        {
          "name": "created_at",
          "type": "DATETIME",
          "not_null": false,
          "pk_position": 0
        },
        {
          "name": "updated_at",
          "type": "DATETIME",
          "not_null": false,
          "pk_position": 0
        }
      ],
      "primary_key": [
        "id"
      ],
      "indexes": [
        {
          "name": "ix_decision_signal_outcomes_engine_version",
          "unique": false,
          "columns": [
            "engine_version"
          ]
        },
        {
          "name": "ix_decision_signal_outcomes_action",
          "unique": false,
          "columns": [
            "action"
          ]
        },
        {
          "name": "ix_decision_signal_outcomes_data_quality_level",
          "unique": false,
          "columns": [
            "data_quality_level"
          ]
        },
        {
          "name": "ix_decision_signal_outcomes_anchor_date",
          "unique": false,
          "columns": [
            "anchor_date"
          ]
        },
        {
          "name": "ix_decision_signal_outcomes_signal_id",
          "unique": false,
          "columns": [
            "signal_id"
          ]
        },
        {
          "name": "ix_decision_signal_outcomes_plan_quality",
          "unique": false,
          "columns": [
            "plan_quality"
          ]
        },
        {
          "name": "ix_decision_signal_outcomes_unable_reason",
          "unique": false,
          "columns": [
            "unable_reason"
          ]
        },
        {
          "name": "ix_decision_signal_outcomes_horizon",
          "unique": false,
          "columns": [
            "horizon"
          ]
        },
        {
          "name": "ix_decision_signal_outcomes_source_agent",
          "unique": false,
          "columns": [
            "source_agent"
          ]
        },
        {
          "name": "ix_decision_signal_outcomes_direction_expected",
          "unique": false,
          "columns": [
            "direction_expected"
          ]
        },
        {
          "name": "ix_decision_signal_outcomes_source_type",
          "unique": false,
          "columns": [
            "source_type"
          ]
        },
        {
          "name": "ix_decision_signal_outcomes_updated_at",
          "unique": false,
          "columns": [
            "updated_at"
          ]
        },
        {
          "name": "ix_decision_signal_outcomes_outcome",
          "unique": false,
          "columns": [
            "outcome"
          ]
        },
        {
          "name": "ix_decision_signal_outcome_stats_market",
          "unique": false,
          "columns": [
            "engine_version",
            "market",
            "horizon"
          ]
        },
        {
          "name": "ix_decision_signal_outcomes_market_phase",
          "unique": false,
          "columns": [
            "market_phase"
          ]
        },
        {
          "name": "ix_decision_signal_outcomes_created_at",
          "unique": false,
          "columns": [
            "created_at"
          ]
        },
        {
          "name": "ix_decision_signal_outcome_stats_action",
          "unique": false,
          "columns": [
            "engine_version",
            "action",
            "horizon"
          ]
        },
        {
          "name": "ix_decision_signal_outcomes_eval_status",
          "unique": false,
          "columns": [
            "eval_status"
          ]
        },
        {
          "name": "ix_decision_signal_outcomes_market",
          "unique": false,
          "columns": [
            "market"
          ]
        },
        {
          "name": "ix_decision_signal_outcomes_holding_state",
          "unique": false,
          "columns": [
            "holding_state"
          ]
        },
        {
          "name": "sqlite_autoindex_decision_signal_outcomes_1",
          "unique": true,
          "columns": [
            "signal_id",
            "horizon",
            "engine_version"
          ]
        }
      ],
      "foreign_keys": []
    },
    {
      "name": "decision_signals",
      "columns": [
        {
          "name": "id",
          "type": "INTEGER",
          "not_null": true,
          "pk_position": 1
        },
        {
          "name": "stock_code",
          "type": "VARCHAR(16)",
          "not_null": true,
          "pk_position": 0
        },
        {
          "name": "stock_name",
          "type": "VARCHAR(64)",
          "not_null": false,
          "pk_position": 0
        },
        {
          "name": "market",
          "type": "VARCHAR(8)",
          "not_null": true,
          "pk_position": 0
        },
        {
          "name": "source_type",
          "type": "VARCHAR(32)",
          "not_null": true,
          "pk_position": 0
        },
        {
          "name": "source_agent",
          "type": "VARCHAR(64)",
          "not_null": false,
          "pk_position": 0
        },
        {
          "name": "source_report_id",
          "type": "INTEGER",
          "not_null": false,
          "pk_position": 0
        },
        {
          "name": "trace_id",
          "type": "VARCHAR(64)",
          "not_null": false,
          "pk_position": 0
        },
        {
          "name": "decision_profile",
          "type": "VARCHAR(16)",
          "not_null": false,
          "pk_position": 0
        },
        {
          "name": "market_phase",
          "type": "VARCHAR(24)",
          "not_null": false,
          "pk_position": 0
        },
        {
          "name": "trigger_source",
          "type": "VARCHAR(64)",
          "not_null": true,
          "pk_position": 0
        },
        {
          "name": "action",
          "type": "VARCHAR(16)",
          "not_null": true,
          "pk_position": 0
        },
        {
          "name": "action_label",
          "type": "VARCHAR(32)",
          "not_null": false,
          "pk_position": 0
        },
        {
          "name": "confidence",
          "type": "FLOAT",
          "not_null": false,
          "pk_position": 0
        },
        {
          "name": "score",
          "type": "INTEGER",
          "not_null": false,
          "pk_position": 0
        },
        {
          "name": "horizon",
          "type": "VARCHAR(16)",
          "not_null": false,
          "pk_position": 0
        },
        {
          "name": "entry_low",
          "type": "FLOAT",
          "not_null": false,
          "pk_position": 0
        },
        {
          "name": "entry_high",
          "type": "FLOAT",
          "not_null": false,
          "pk_position": 0
        },
        {
          "name": "stop_loss",
          "type": "FLOAT",
          "not_null": false,
          "pk_position": 0
        },
        {
          "name": "target_price",
          "type": "FLOAT",
          "not_null": false,
          "pk_position": 0
        },
        {
          "name": "invalidation",
          "type": "TEXT",
          "not_null": false,
          "pk_position": 0
        },
        {
          "name": "watch_conditions",
          "type": "TEXT",
          "not_null": false,
          "pk_position": 0
        },
        {
          "name": "reason",
          "type": "TEXT",
          "not_null": false,
          "pk_position": 0
        },
        {
          "name": "risk_summary",
          "type": "TEXT",
          "not_null": false,
          "pk_position": 0
        },
        {
          "name": "catalyst_summary",
          "type": "TEXT",
          "not_null": false,
          "pk_position": 0
        },
        {
          "name": "evidence_json",
          "type": "TEXT",
          "not_null": false,
          "pk_position": 0
        },
        {
          "name": "data_quality_summary_json",
          "type": "TEXT",
          "not_null": false,
          "pk_position": 0
        },
        {
          "name": "plan_quality",
          "type": "VARCHAR(16)",
          "not_null": true,
          "pk_position": 0
        },
        {
          "name": "status",
          "type": "VARCHAR(16)",
          "not_null": true,
          "pk_position": 0
        },
        {
          "name": "expires_at",
          "type": "DATETIME",
          "not_null": false,
          "pk_position": 0
        },
        {
          "name": "created_at",
          "type": "DATETIME",
          "not_null": false,
          "pk_position": 0
        },
        {
          "name": "updated_at",
          "type": "DATETIME",
          "not_null": false,
          "pk_position": 0
        },
        {
          "name": "metadata_json",
          "type": "TEXT",
          "not_null": false,
          "pk_position": 0
        }
      ],
      "primary_key": [
        "id"
      ],
      "indexes": [
        {
          "name": "ix_decision_signals_trace_id",
          "unique": false,
          "columns": [
            "trace_id"
          ]
        },
        {
          "name": "ix_decision_signal_market_stock_profile_created",
          "unique": false,
          "columns": [
            "market",
            "stock_code",
            "decision_profile",
            "created_at"
          ]
        },
        {
          "name": "ix_decision_signals_plan_quality",
          "unique": false,
          "columns": [
            "plan_quality"
          ]
        },
        {
          "name": "ix_decision_signal_trace_type_market_stock_profile_action_horizon_phase",
          "unique": false,
          "columns": [
            "trace_id",
            "source_type",
            "market",
            "stock_code",
            "decision_profile",
            "action",
            "horizon",
            "market_phase"
          ]
        },
        {
          "name": "ix_decision_signals_source_report_id",
          "unique": false,
          "columns": [
            "source_report_id"
          ]
        },
        {
          "name": "ix_decision_signal_report_type_market_stock_profile_action_horizon_phase",
          "unique": false,
          "columns": [
            "source_report_id",
            "source_type",
            "market",
            "stock_code",
            "decision_profile",
            "action",
            "horizon",
            "market_phase"
          ]
        },
        {
          "name": "ix_decision_signals_horizon",
          "unique": false,
          "columns": [
            "horizon"
          ]
        },
        {
          "name": "ix_decision_signal_trace_type_market_stock_action_horizon_phase",
          "unique": false,
          "columns": [
            "trace_id",
            "source_type",
            "market",
            "stock_code",
            "action",
            "horizon",
            "market_phase"
          ]
        },
        {
          "name": "ix_decision_signals_source_type",
          "unique": false,
          "columns": [
            "source_type"
          ]
        },
        {
          "name": "ix_decision_signal_report_type_market_stock_action_horizon_phase",
          "unique": false,
          "columns": [
            "source_report_id",
            "source_type",
            "market",
            "stock_code",
            "action",
            "horizon",
            "market_phase"
          ]
        },
        {
          "name": "ix_decision_signals_action",
          "unique": false,
          "columns": [
            "action"
          ]
        },
        {
          "name": "ix_decision_signal_market_status_time",
          "unique": false,
          "columns": [
            "market",
            "status",
            "created_at"
          ]
        },
        {
          "name": "ix_decision_signals_updated_at",
          "unique": false,
          "columns": [
            "updated_at"
          ]
        },
        {
          "name": "ix_decision_signal_stock_status_time",
          "unique": false,
          "columns": [
            "stock_code",
            "status",
            "created_at"
          ]
        },
        {
          "name": "ix_decision_signals_trigger_source",
          "unique": false,
          "columns": [
            "trigger_source"
          ]
        },
        {
          "name": "ix_decision_signals_stock_code",
          "unique": false,
          "columns": [
            "stock_code"
          ]
        },
        {
          "name": "ix_decision_signals_created_at",
          "unique": false,
          "columns": [
            "created_at"
          ]
        },
        {
          "name": "ix_decision_signals_market_phase",
          "unique": false,
          "columns": [
            "market_phase"
          ]
        },
        {
          "name": "ix_decision_signals_market",
          "unique": false,
          "columns": [
            "market"
          ]
        },
        {
          "name": "ix_decision_signals_expires_at",
          "unique": false,
          "columns": [
            "expires_at"
          ]
        },
        {
          "name": "ix_decision_signals_decision_profile",
          "unique": false,
          "columns": [
            "decision_profile"
          ]
        },
        {
          "name": "ix_decision_signals_status",
          "unique": false,
          "columns": [
            "status"
          ]
        }
      ],
      "foreign_keys": []
    },
    {
      "name": "fundamental_snapshot",
      "columns": [
        {
          "name": "id",
          "type": "INTEGER",
          "not_null": true,
          "pk_position": 1
        },
        {
          "name": "query_id",
          "type": "VARCHAR(64)",
          "not_null": true,
          "pk_position": 0
        },
        {
          "name": "code",
          "type": "VARCHAR(10)",
          "not_null": true,
          "pk_position": 0
        },
        {
          "name": "payload",
          "type": "TEXT",
          "not_null": true,
          "pk_position": 0
        },
        {
          "name": "source_chain",
          "type": "TEXT",
          "not_null": false,
          "pk_position": 0
        },
        {
          "name": "coverage",
          "type": "TEXT",
          "not_null": false,
          "pk_position": 0
        },
        {
          "name": "created_at",
          "type": "DATETIME",
          "not_null": false,
          "pk_position": 0
        }
      ],
      "primary_key": [
        "id"
      ],
      "indexes": [
        {
          "name": "ix_fundamental_snapshot_created",
          "unique": false,
          "columns": [
            "created_at"
          ]
        },
        {
          "name": "ix_fundamental_snapshot_query_code",
          "unique": false,
          "columns": [
            "query_id",
            "code"
          ]
        },
        {
          "name": "ix_fundamental_snapshot_code",
          "unique": false,
          "columns": [
            "code"
          ]
        },
        {
          "name": "ix_fundamental_snapshot_query_id",
          "unique": false,
          "columns": [
            "query_id"
          ]
        },
        {
          "name": "ix_fundamental_snapshot_created_at",
          "unique": false,
          "columns": [
            "created_at"
          ]
        }
      ],
      "foreign_keys": []
    },
    {
      "name": "intelligence_items",
      "columns": [
        {
          "name": "id",
          "type": "INTEGER",
          "not_null": true,
          "pk_position": 1
        },
        {
          "name": "source_id",
          "type": "INTEGER",
          "not_null": false,
          "pk_position": 0
        },
        {
          "name": "source_name",
          "type": "VARCHAR(100)",
          "not_null": false,
          "pk_position": 0
        },
        {
          "name": "source_type",
          "type": "VARCHAR(32)",
          "not_null": true,
          "pk_position": 0
        },
        {
          "name": "title",
          "type": "VARCHAR(300)",
          "not_null": true,
          "pk_position": 0
        },
        {
          "name": "summary",
          "type": "TEXT",
          "not_null": false,
          "pk_position": 0
        },
        {
          "name": "url",
          "type": "VARCHAR(1000)",
          "not_null": true,
          "pk_position": 0
        },
        {
          "name": "source",
          "type": "VARCHAR(100)",
          "not_null": false,
          "pk_position": 0
        },
        {
          "name": "published_at",
          "type": "DATETIME",
          "not_null": false,
          "pk_position": 0
        },
        {
          "name": "fetched_at",
          "type": "DATETIME",
          "not_null": false,
          "pk_position": 0
        },
        {
          "name": "scope_type",
          "type": "VARCHAR(32)",
          "not_null": true,
          "pk_position": 0
        },
        {
          "name": "scope_value",
          "type": "VARCHAR(64)",
          "not_null": true,
          "pk_position": 0
        },
        {
          "name": "market",
          "type": "VARCHAR(32)",
          "not_null": true,
          "pk_position": 0
        },
        {
          "name": "raw_payload",
          "type": "TEXT",
          "not_null": false,
          "pk_position": 0
        }
      ],
      "primary_key": [
        "id"
      ],
      "indexes": [
        {
          "name": "ix_intelligence_items_fetched_at",
          "unique": false,
          "columns": [
            "fetched_at"
          ]
        },
        {
          "name": "ix_intelligence_items_source_type",
          "unique": false,
          "columns": [
            "source_type"
          ]
        },
        {
          "name": "ix_intelligence_items_market",
          "unique": false,
          "columns": [
            "market"
          ]
        },
        {
          "name": "ix_intelligence_items_source_name",
          "unique": false,
          "columns": [
            "source_name"
          ]
        },
        {
          "name": "ix_intel_item_fetch_time",
          "unique": false,
          "columns": [
            "fetched_at"
          ]
        },
        {
          "name": "ix_intelligence_items_scope_type",
          "unique": false,
          "columns": [
            "scope_type"
          ]
        },
        {
          "name": "ix_intelligence_items_url",
          "unique": false,
          "columns": [
            "url"
          ]
        },
        {
          "name": "ix_intelligence_items_source_id",
          "unique": false,
          "columns": [
            "source_id"
          ]
        },
        {
          "name": "ix_intelligence_items_published_at",
          "unique": false,
          "columns": [
            "published_at"
          ]
        },
        {
          "name": "ix_intel_item_scope_time",
          "unique": false,
          "columns": [
            "scope_type",
            "scope_value",
            "market",
            "published_at"
          ]
        },
        {
          "name": "ix_intelligence_items_scope_value",
          "unique": false,
          "columns": [
            "scope_value"
          ]
        },
        {
          "name": "sqlite_autoindex_intelligence_items_1",
          "unique": true,
          "columns": [
            "source_id",
            "url",
            "scope_type",
            "scope_value",
            "market"
          ]
        }
      ],
      "foreign_keys": [
        {
          "from": "source_id",
          "table": "intelligence_sources",
          "to": "id"
        }
      ]
    },
    {
      "name": "intelligence_sources",
      "columns": [
        {
          "name": "id",
          "type": "INTEGER",
          "not_null": true,
          "pk_position": 1
        },
        {
          "name": "name",
          "type": "VARCHAR(100)",
          "not_null": true,
          "pk_position": 0
        },
        {
          "name": "source_type",
          "type": "VARCHAR(32)",
          "not_null": true,
          "pk_position": 0
        },
        {
          "name": "url",
          "type": "VARCHAR(1000)",
          "not_null": true,
          "pk_position": 0
        },
        {
          "name": "enabled",
          "type": "BOOLEAN",
          "not_null": true,
          "pk_position": 0
        },
        {
          "name": "scope_type",
          "type": "VARCHAR(32)",
          "not_null": true,
          "pk_position": 0
        },
        {
          "name": "scope_value",
          "type": "VARCHAR(64)",
          "not_null": false,
          "pk_position": 0
        },
        {
          "name": "market",
          "type": "VARCHAR(32)",
          "not_null": true,
          "pk_position": 0
        },
        {
          "name": "description",
          "type": "TEXT",
          "not_null": false,
          "pk_position": 0
        },
        {
          "name": "last_status",
          "type": "VARCHAR(32)",
          "not_null": false,
          "pk_position": 0
        },
        {
          "name": "last_error",
          "type": "TEXT",
          "not_null": false,
          "pk_position": 0
        },
        {
          "name": "last_fetched_at",
          "type": "DATETIME",
          "not_null": false,
          "pk_position": 0
        },
        {
          "name": "created_at",
          "type": "DATETIME",
          "not_null": false,
          "pk_position": 0
        },
        {
          "name": "updated_at",
          "type": "DATETIME",
          "not_null": false,
          "pk_position": 0
        }
      ],
      "primary_key": [
        "id"
      ],
      "indexes": [
        {
          "name": "ix_intelligence_sources_scope_type",
          "unique": false,
          "columns": [
            "scope_type"
          ]
        },
        {
          "name": "ix_intel_source_scope",
          "unique": false,
          "columns": [
            "scope_type",
            "scope_value",
            "market"
          ]
        },
        {
          "name": "ix_intelligence_sources_created_at",
          "unique": false,
          "columns": [
            "created_at"
          ]
        },
        {
          "name": "ix_intelligence_sources_source_type",
          "unique": false,
          "columns": [
            "source_type"
          ]
        },
        {
          "name": "ix_intelligence_sources_market",
          "unique": false,
          "columns": [
            "market"
          ]
        },
        {
          "name": "ix_intelligence_sources_updated_at",
          "unique": false,
          "columns": [
            "updated_at"
          ]
        },
        {
          "name": "ix_intelligence_sources_name",
          "unique": true,
          "columns": [
            "name"
          ]
        },
        {
          "name": "ix_intelligence_sources_enabled",
          "unique": false,
          "columns": [
            "enabled"
          ]
        },
        {
          "name": "ix_intelligence_sources_scope_value",
          "unique": false,
          "columns": [
            "scope_value"
          ]
        },
        {
          "name": "ix_intelligence_sources_last_fetched_at",
          "unique": false,
          "columns": [
            "last_fetched_at"
          ]
        }
      ],
      "foreign_keys": []
    },
    {
      "name": "llm_usage",
      "columns": [
        {
          "name": "id",
          "type": "INTEGER",
          "not_null": true,
          "pk_position": 1
        },
        {
          "name": "call_type",
          "type": "VARCHAR(32)",
          "not_null": true,
          "pk_position": 0
        },
        {
          "name": "model",
          "type": "VARCHAR(128)",
          "not_null": true,
          "pk_position": 0
        },
        {
          "name": "stock_code",
          "type": "VARCHAR(16)",
          "not_null": false,
          "pk_position": 0
        },
        {
          "name": "provider",
          "type": "VARCHAR(64)",
          "not_null": false,
          "pk_position": 0
        },
        {
          "name": "prompt_tokens",
          "type": "INTEGER",
          "not_null": true,
          "pk_position": 0
        },
        {
          "name": "completion_tokens",
          "type": "INTEGER",
          "not_null": true,
          "pk_position": 0
        },
        {
          "name": "total_tokens",
          "type": "INTEGER",
          "not_null": true,
          "pk_position": 0
        },
        {
          "name": "provider_usage_json",
          "type": "TEXT",
          "not_null": false,
          "pk_position": 0
        },
        {
          "name": "provider_usage_schema_name",
          "type": "VARCHAR(64)",
          "not_null": false,
          "pk_position": 0
        },
        {
          "name": "provider_usage_schema_version",
          "type": "VARCHAR(32)",
          "not_null": false,
          "pk_position": 0
        },
        {
          "name": "provider_usage_observed_at",
          "type": "VARCHAR(32)",
          "not_null": false,
          "pk_position": 0
        },
        {
          "name": "normalized_prompt_tokens",
          "type": "INTEGER",
          "not_null": false,
          "pk_position": 0
        },
        {
          "name": "normalized_completion_tokens",
          "type": "INTEGER",
          "not_null": false,
          "pk_position": 0
        },
        {
          "name": "normalized_total_tokens",
          "type": "INTEGER",
          "not_null": false,
          "pk_position": 0
        },
        {
          "name": "normalized_cache_read_tokens",
          "type": "INTEGER",
          "not_null": false,
          "pk_position": 0
        },
        {
          "name": "normalized_cache_write_tokens",
          "type": "INTEGER",
          "not_null": false,
          "pk_position": 0
        },
        {
          "name": "normalized_cache_miss_tokens",
          "type": "INTEGER",
          "not_null": false,
          "pk_position": 0
        },
        {
          "name": "normalized_uncached_input_tokens",
          "type": "INTEGER",
          "not_null": false,
          "pk_position": 0
        },
        {
          "name": "normalized_cache_eligible_input_tokens",
          "type": "INTEGER",
          "not_null": false,
          "pk_position": 0
        },
        {
          "name": "normalized_cache_hit_ratio",
          "type": "FLOAT",
          "not_null": false,
          "pk_position": 0
        },
        {
          "name": "normalized_cache_write_ratio",
          "type": "FLOAT",
          "not_null": false,
          "pk_position": 0
        },
        {
          "name": "cache_capability",
          "type": "VARCHAR(32)",
          "not_null": false,
          "pk_position": 0
        },
        {
          "name": "cache_eligibility",
          "type": "VARCHAR(32)",
          "not_null": false,
          "pk_position": 0
        },
        {
          "name": "cache_observation",
          "type": "VARCHAR(32)",
          "not_null": false,
          "pk_position": 0
        },
        {
          "name": "estimated_prefix_tokens",
          "type": "INTEGER",
          "not_null": false,
          "pk_position": 0
        },
        {
          "name": "provider_reported_prompt_tokens",
          "type": "INTEGER",
          "not_null": false,
          "pk_position": 0
        },
        {
          "name": "provider_reported_cached_tokens",
          "type": "INTEGER",
          "not_null": false,
          "pk_position": 0
        },
        {
          "name": "provider_min_cache_tokens",
          "type": "INTEGER",
          "not_null": false,
          "pk_position": 0
        },
        {
          "name": "eligibility_confidence",
          "type": "VARCHAR(32)",
          "not_null": false,
          "pk_position": 0
        },
        {
          "name": "tokenizer_name",
          "type": "VARCHAR(128)",
          "not_null": false,
          "pk_position": 0
        },
        {
          "name": "tokenizer_version",
          "type": "VARCHAR(64)",
          "not_null": false,
          "pk_position": 0
        },
        {
          "name": "messages_hmac",
          "type": "VARCHAR(64)",
          "not_null": false,
          "pk_position": 0
        },
        {
          "name": "system_message_hmac",
          "type": "VARCHAR(64)",
          "not_null": false,
          "pk_position": 0
        },
        {
          "name": "user_message_hmac",
          "type": "VARCHAR(64)",
          "not_null": false,
          "pk_position": 0
        },
        {
          "name": "hmac_key_version",
          "type": "VARCHAR(64)",
          "not_null": false,
          "pk_position": 0
        },
        {
          "name": "hmac_domain",
          "type": "VARCHAR(32)",
          "not_null": false,
          "pk_position": 0
        },
        {
          "name": "hash_scope",
          "type": "VARCHAR(32)",
          "not_null": false,
          "pk_position": 0
        },
        {
          "name": "language",
          "type": "VARCHAR(16)",
          "not_null": false,
          "pk_position": 0
        },
        {
          "name": "market_group",
          "type": "VARCHAR(16)",
          "not_null": false,
          "pk_position": 0
        },
        {
          "name": "analysis_mode",
          "type": "VARCHAR(64)",
          "not_null": false,
          "pk_position": 0
        },
        {
          "name": "legacy_prompt_mode",
          "type": "VARCHAR(32)",
          "not_null": false,
          "pk_position": 0
        },
        {
          "name": "skill_config_hmac",
          "type": "VARCHAR(64)",
          "not_null": false,
          "pk_position": 0
        },
        {
          "name": "transport",
          "type": "VARCHAR(64)",
          "not_null": false,
          "pk_position": 0
        },
        {
          "name": "message_count",
          "type": "INTEGER",
          "not_null": false,
          "pk_position": 0
        },
        {
          "name": "estimated_total_prompt_tokens",
          "type": "INTEGER",
          "not_null": false,
          "pk_position": 0
        },
        {
          "name": "approx_common_prefix_chars",
          "type": "INTEGER",
          "not_null": false,
          "pk_position": 0
        },
        {
          "name": "approx_common_prefix_tokens",
          "type": "INTEGER",
          "not_null": false,
          "pk_position": 0
        },
        {
          "name": "known_dynamic_marker_positions",
          "type": "TEXT",
          "not_null": false,
          "pk_position": 0
        },
        {
          "name": "called_at",
          "type": "DATETIME",
          "not_null": false,
          "pk_position": 0
        }
      ],
      "primary_key": [
        "id"
      ],
      "indexes": [
        {
          "name": "ix_llm_usage_called_at",
          "unique": false,
          "columns": [
            "called_at"
          ]
        },
        {
          "name": "ix_llm_usage_call_type",
          "unique": false,
          "columns": [
            "call_type"
          ]
        }
      ],
      "foreign_keys": []
    },
    {
      "name": "news_intel",
      "columns": [
        {
          "name": "id",
          "type": "INTEGER",
          "not_null": true,
          "pk_position": 1
        },
        {
          "name": "query_id",
          "type": "VARCHAR(64)",
          "not_null": false,
          "pk_position": 0
        },
        {
          "name": "code",
          "type": "VARCHAR(10)",
          "not_null": true,
          "pk_position": 0
        },
        {
          "name": "name",
          "type": "VARCHAR(50)",
          "not_null": false,
          "pk_position": 0
        },
        {
          "name": "dimension",
          "type": "VARCHAR(32)",
          "not_null": false,
          "pk_position": 0
        },
        {
          "name": "query",
          "type": "VARCHAR(255)",
          "not_null": false,
          "pk_position": 0
        },
        {
          "name": "provider",
          "type": "VARCHAR(32)",
          "not_null": false,
          "pk_position": 0
        },
        {
          "name": "title",
          "type": "VARCHAR(300)",
          "not_null": true,
          "pk_position": 0
        },
        {
          "name": "snippet",
          "type": "TEXT",
          "not_null": false,
          "pk_position": 0
        },
        {
          "name": "url",
          "type": "VARCHAR(1000)",
          "not_null": true,
          "pk_position": 0
        },
        {
          "name": "source",
          "type": "VARCHAR(100)",
          "not_null": false,
          "pk_position": 0
        },
        {
          "name": "published_date",
          "type": "DATETIME",
          "not_null": false,
          "pk_position": 0
        },
        {
          "name": "fetched_at",
          "type": "DATETIME",
          "not_null": false,
          "pk_position": 0
        },
        {
          "name": "query_source",
          "type": "VARCHAR(32)",
          "not_null": false,
          "pk_position": 0
        },
        {
          "name": "requester_platform",
          "type": "VARCHAR(20)",
          "not_null": false,
          "pk_position": 0
        },
        {
          "name": "requester_user_id",
          "type": "VARCHAR(64)",
          "not_null": false,
          "pk_position": 0
        },
        {
          "name": "requester_user_name",
          "type": "VARCHAR(64)",
          "not_null": false,
          "pk_position": 0
        },
        {
          "name": "requester_chat_id",
          "type": "VARCHAR(64)",
          "not_null": false,
          "pk_position": 0
        },
        {
          "name": "requester_message_id",
          "type": "VARCHAR(64)",
          "not_null": false,
          "pk_position": 0
        },
        {
          "name": "requester_query",
          "type": "VARCHAR(255)",
          "not_null": false,
          "pk_position": 0
        }
      ],
      "primary_key": [
        "id"
      ],
      "indexes": [
        {
          "name": "ix_news_intel_query_source",
          "unique": false,
          "columns": [
            "query_source"
          ]
        },
        {
          "name": "ix_news_intel_fetched_at",
          "unique": false,
          "columns": [
            "fetched_at"
          ]
        },
        {
          "name": "ix_news_intel_provider",
          "unique": false,
          "columns": [
            "provider"
          ]
        },
        {
          "name": "ix_news_intel_code",
          "unique": false,
          "columns": [
            "code"
          ]
        },
        {
          "name": "ix_news_intel_published_date",
          "unique": false,
          "columns": [
            "published_date"
          ]
        },
        {
          "name": "ix_news_code_pub",
          "unique": false,
          "columns": [
            "code",
            "published_date"
          ]
        },
        {
          "name": "ix_news_intel_dimension",
          "unique": false,
          "columns": [
            "dimension"
          ]
        },
        {
          "name": "ix_news_intel_query_id",
          "unique": false,
          "columns": [
            "query_id"
          ]
        },
        {
          "name": "sqlite_autoindex_news_intel_1",
          "unique": true,
          "columns": [
            "url"
          ]
        }
      ],
      "foreign_keys": []
    },
    {
      "name": "portfolio_accounts",
      "columns": [
        {
          "name": "id",
          "type": "INTEGER",
          "not_null": true,
          "pk_position": 1
        },
        {
          "name": "owner_id",
          "type": "VARCHAR(64)",
          "not_null": false,
          "pk_position": 0
        },
        {
          "name": "name",
          "type": "VARCHAR(64)",
          "not_null": true,
          "pk_position": 0
        },
        {
          "name": "broker",
          "type": "VARCHAR(64)",
          "not_null": false,
          "pk_position": 0
        },
        {
          "name": "market",
          "type": "VARCHAR(8)",
          "not_null": true,
          "pk_position": 0
        },
        {
          "name": "base_currency",
          "type": "VARCHAR(8)",
          "not_null": true,
          "pk_position": 0
        },
        {
          "name": "is_active",
          "type": "BOOLEAN",
          "not_null": true,
          "pk_position": 0
        },
        {
          "name": "created_at",
          "type": "DATETIME",
          "not_null": false,
          "pk_position": 0
        },
        {
          "name": "updated_at",
          "type": "DATETIME",
          "not_null": false,
          "pk_position": 0
        }
      ],
      "primary_key": [
        "id"
      ],
      "indexes": [
        {
          "name": "ix_portfolio_accounts_market",
          "unique": false,
          "columns": [
            "market"
          ]
        },
        {
          "name": "ix_portfolio_account_owner_active",
          "unique": false,
          "columns": [
            "owner_id",
            "is_active"
          ]
        },
        {
          "name": "ix_portfolio_accounts_owner_id",
          "unique": false,
          "columns": [
            "owner_id"
          ]
        },
        {
          "name": "ix_portfolio_accounts_is_active",
          "unique": false,
          "columns": [
            "is_active"
          ]
        },
        {
          "name": "ix_portfolio_accounts_created_at",
          "unique": false,
          "columns": [
            "created_at"
          ]
        }
      ],
      "foreign_keys": []
    },
    {
      "name": "portfolio_cash_ledger",
      "columns": [
        {
          "name": "id",
          "type": "INTEGER",
          "not_null": true,
          "pk_position": 1
        },
        {
          "name": "account_id",
          "type": "INTEGER",
          "not_null": true,
          "pk_position": 0
        },
        {
          "name": "event_date",
          "type": "DATE",
          "not_null": true,
          "pk_position": 0
        },
        {
          "name": "direction",
          "type": "VARCHAR(8)",
          "not_null": true,
          "pk_position": 0
        },
        {
          "name": "amount",
          "type": "FLOAT",
          "not_null": true,
          "pk_position": 0
        },
        {
          "name": "currency",
          "type": "VARCHAR(8)",
          "not_null": true,
          "pk_position": 0
        },
        {
          "name": "note",
          "type": "VARCHAR(255)",
          "not_null": false,
          "pk_position": 0
        },
        {
          "name": "created_at",
          "type": "DATETIME",
          "not_null": false,
          "pk_position": 0
        }
      ],
      "primary_key": [
        "id"
      ],
      "indexes": [
        {
          "name": "ix_portfolio_cash_account_date",
          "unique": false,
          "columns": [
            "account_id",
            "event_date"
          ]
        },
        {
          "name": "ix_portfolio_cash_ledger_event_date",
          "unique": false,
          "columns": [
            "event_date"
          ]
        },
        {
          "name": "ix_portfolio_cash_ledger_created_at",
          "unique": false,
          "columns": [
            "created_at"
          ]
        },
        {
          "name": "ix_portfolio_cash_ledger_account_id",
          "unique": false,
          "columns": [
            "account_id"
          ]
        }
      ],
      "foreign_keys": [
        {
          "from": "account_id",
          "table": "portfolio_accounts",
          "to": "id"
        }
      ]
    },
    {
      "name": "portfolio_corporate_actions",
      "columns": [
        {
          "name": "id",
          "type": "INTEGER",
          "not_null": true,
          "pk_position": 1
        },
        {
          "name": "account_id",
          "type": "INTEGER",
          "not_null": true,
          "pk_position": 0
        },
        {
          "name": "symbol",
          "type": "VARCHAR(16)",
          "not_null": true,
          "pk_position": 0
        },
        {
          "name": "market",
          "type": "VARCHAR(8)",
          "not_null": true,
          "pk_position": 0
        },
        {
          "name": "currency",
          "type": "VARCHAR(8)",
          "not_null": true,
          "pk_position": 0
        },
        {
          "name": "effective_date",
          "type": "DATE",
          "not_null": true,
          "pk_position": 0
        },
        {
          "name": "action_type",
          "type": "VARCHAR(24)",
          "not_null": true,
          "pk_position": 0
        },
        {
          "name": "cash_dividend_per_share",
          "type": "FLOAT",
          "not_null": false,
          "pk_position": 0
        },
        {
          "name": "split_ratio",
          "type": "FLOAT",
          "not_null": false,
          "pk_position": 0
        },
        {
          "name": "note",
          "type": "VARCHAR(255)",
          "not_null": false,
          "pk_position": 0
        },
        {
          "name": "created_at",
          "type": "DATETIME",
          "not_null": false,
          "pk_position": 0
        }
      ],
      "primary_key": [
        "id"
      ],
      "indexes": [
        {
          "name": "ix_portfolio_ca_account_date",
          "unique": false,
          "columns": [
            "account_id",
            "effective_date"
          ]
        },
        {
          "name": "ix_portfolio_corporate_actions_symbol",
          "unique": false,
          "columns": [
            "symbol"
          ]
        },
        {
          "name": "ix_portfolio_corporate_actions_effective_date",
          "unique": false,
          "columns": [
            "effective_date"
          ]
        },
        {
          "name": "ix_portfolio_corporate_actions_account_id",
          "unique": false,
          "columns": [
            "account_id"
          ]
        },
        {
          "name": "ix_portfolio_corporate_actions_created_at",
          "unique": false,
          "columns": [
            "created_at"
          ]
        }
      ],
      "foreign_keys": [
        {
          "from": "account_id",
          "table": "portfolio_accounts",
          "to": "id"
        }
      ]
    },
    {
      "name": "portfolio_daily_snapshots",
      "columns": [
        {
          "name": "id",
          "type": "INTEGER",
          "not_null": true,
          "pk_position": 1
        },
        {
          "name": "account_id",
          "type": "INTEGER",
          "not_null": true,
          "pk_position": 0
        },
        {
          "name": "snapshot_date",
          "type": "DATE",
          "not_null": true,
          "pk_position": 0
        },
        {
          "name": "cost_method",
          "type": "VARCHAR(8)",
          "not_null": true,
          "pk_position": 0
        },
        {
          "name": "base_currency",
          "type": "VARCHAR(8)",
          "not_null": true,
          "pk_position": 0
        },
        {
          "name": "total_cash",
          "type": "FLOAT",
          "not_null": true,
          "pk_position": 0
        },
        {
          "name": "total_market_value",
          "type": "FLOAT",
          "not_null": true,
          "pk_position": 0
        },
        {
          "name": "total_equity",
          "type": "FLOAT",
          "not_null": true,
          "pk_position": 0
        },
        {
          "name": "unrealized_pnl",
          "type": "FLOAT",
          "not_null": true,
          "pk_position": 0
        },
        {
          "name": "realized_pnl",
          "type": "FLOAT",
          "not_null": true,
          "pk_position": 0
        },
        {
          "name": "fee_total",
          "type": "FLOAT",
          "not_null": true,
          "pk_position": 0
        },
        {
          "name": "tax_total",
          "type": "FLOAT",
          "not_null": true,
          "pk_position": 0
        },
        {
          "name": "fx_stale",
          "type": "BOOLEAN",
          "not_null": true,
          "pk_position": 0
        },
        {
          "name": "payload",
          "type": "TEXT",
          "not_null": false,
          "pk_position": 0
        },
        {
          "name": "created_at",
          "type": "DATETIME",
          "not_null": false,
          "pk_position": 0
        },
        {
          "name": "updated_at",
          "type": "DATETIME",
          "not_null": false,
          "pk_position": 0
        }
      ],
      "primary_key": [
        "id"
      ],
      "indexes": [
        {
          "name": "ix_portfolio_daily_snapshots_account_id",
          "unique": false,
          "columns": [
            "account_id"
          ]
        },
        {
          "name": "ix_portfolio_daily_snapshots_created_at",
          "unique": false,
          "columns": [
            "created_at"
          ]
        },
        {
          "name": "ix_portfolio_daily_snapshots_snapshot_date",
          "unique": false,
          "columns": [
            "snapshot_date"
          ]
        },
        {
          "name": "sqlite_autoindex_portfolio_daily_snapshots_1",
          "unique": true,
          "columns": [
            "account_id",
            "snapshot_date",
            "cost_method"
          ]
        }
      ],
      "foreign_keys": [
        {
          "from": "account_id",
          "table": "portfolio_accounts",
          "to": "id"
        }
      ]
    },
    {
      "name": "portfolio_fx_rates",
      "columns": [
        {
          "name": "id",
          "type": "INTEGER",
          "not_null": true,
          "pk_position": 1
        },
        {
          "name": "from_currency",
          "type": "VARCHAR(8)",
          "not_null": true,
          "pk_position": 0
        },
        {
          "name": "to_currency",
          "type": "VARCHAR(8)",
          "not_null": true,
          "pk_position": 0
        },
        {
          "name": "rate_date",
          "type": "DATE",
          "not_null": true,
          "pk_position": 0
        },
        {
          "name": "rate",
          "type": "FLOAT",
          "not_null": true,
          "pk_position": 0
        },
        {
          "name": "source",
          "type": "VARCHAR(32)",
          "not_null": true,
          "pk_position": 0
        },
        {
          "name": "is_stale",
          "type": "BOOLEAN",
          "not_null": true,
          "pk_position": 0
        },
        {
          "name": "updated_at",
          "type": "DATETIME",
          "not_null": false,
          "pk_position": 0
        }
      ],
      "primary_key": [
        "id"
      ],
      "indexes": [
        {
          "name": "ix_portfolio_fx_rates_rate_date",
          "unique": false,
          "columns": [
            "rate_date"
          ]
        },
        {
          "name": "ix_portfolio_fx_rates_to_currency",
          "unique": false,
          "columns": [
            "to_currency"
          ]
        },
        {
          "name": "ix_portfolio_fx_rates_from_currency",
          "unique": false,
          "columns": [
            "from_currency"
          ]
        },
        {
          "name": "sqlite_autoindex_portfolio_fx_rates_1",
          "unique": true,
          "columns": [
            "from_currency",
            "to_currency",
            "rate_date"
          ]
        }
      ],
      "foreign_keys": []
    },
    {
      "name": "portfolio_position_lots",
      "columns": [
        {
          "name": "id",
          "type": "INTEGER",
          "not_null": true,
          "pk_position": 1
        },
        {
          "name": "account_id",
          "type": "INTEGER",
          "not_null": true,
          "pk_position": 0
        },
        {
          "name": "cost_method",
          "type": "VARCHAR(8)",
          "not_null": true,
          "pk_position": 0
        },
        {
          "name": "symbol",
          "type": "VARCHAR(16)",
          "not_null": true,
          "pk_position": 0
        },
        {
          "name": "market",
          "type": "VARCHAR(8)",
          "not_null": true,
          "pk_position": 0
        },
        {
          "name": "currency",
          "type": "VARCHAR(8)",
          "not_null": true,
          "pk_position": 0
        },
        {
          "name": "open_date",
          "type": "DATE",
          "not_null": true,
          "pk_position": 0
        },
        {
          "name": "remaining_quantity",
          "type": "FLOAT",
          "not_null": true,
          "pk_position": 0
        },
        {
          "name": "unit_cost",
          "type": "FLOAT",
          "not_null": true,
          "pk_position": 0
        },
        {
          "name": "source_trade_id",
          "type": "INTEGER",
          "not_null": false,
          "pk_position": 0
        },
        {
          "name": "updated_at",
          "type": "DATETIME",
          "not_null": false,
          "pk_position": 0
        }
      ],
      "primary_key": [
        "id"
      ],
      "indexes": [
        {
          "name": "ix_portfolio_position_lots_symbol",
          "unique": false,
          "columns": [
            "symbol"
          ]
        },
        {
          "name": "ix_portfolio_position_lots_open_date",
          "unique": false,
          "columns": [
            "open_date"
          ]
        },
        {
          "name": "ix_portfolio_position_lots_updated_at",
          "unique": false,
          "columns": [
            "updated_at"
          ]
        },
        {
          "name": "ix_portfolio_lot_account_symbol",
          "unique": false,
          "columns": [
            "account_id",
            "symbol"
          ]
        },
        {
          "name": "ix_portfolio_position_lots_account_id",
          "unique": false,
          "columns": [
            "account_id"
          ]
        }
      ],
      "foreign_keys": [
        {
          "from": "source_trade_id",
          "table": "portfolio_trades",
          "to": "id"
        },
        {
          "from": "account_id",
          "table": "portfolio_accounts",
          "to": "id"
        }
      ]
    },
    {
      "name": "portfolio_positions",
      "columns": [
        {
          "name": "id",
          "type": "INTEGER",
          "not_null": true,
          "pk_position": 1
        },
        {
          "name": "account_id",
          "type": "INTEGER",
          "not_null": true,
          "pk_position": 0
        },
        {
          "name": "cost_method",
          "type": "VARCHAR(8)",
          "not_null": true,
          "pk_position": 0
        },
        {
          "name": "symbol",
          "type": "VARCHAR(16)",
          "not_null": true,
          "pk_position": 0
        },
        {
          "name": "market",
          "type": "VARCHAR(8)",
          "not_null": true,
          "pk_position": 0
        },
        {
          "name": "currency",
          "type": "VARCHAR(8)",
          "not_null": true,
          "pk_position": 0
        },
        {
          "name": "quantity",
          "type": "FLOAT",
          "not_null": true,
          "pk_position": 0
        },
        {
          "name": "avg_cost",
          "type": "FLOAT",
          "not_null": true,
          "pk_position": 0
        },
        {
          "name": "total_cost",
          "type": "FLOAT",
          "not_null": true,
          "pk_position": 0
        },
        {
          "name": "last_price",
          "type": "FLOAT",
          "not_null": true,
          "pk_position": 0
        },
        {
          "name": "market_value_base",
          "type": "FLOAT",
          "not_null": true,
          "pk_position": 0
        },
        {
          "name": "unrealized_pnl_base",
          "type": "FLOAT",
          "not_null": true,
          "pk_position": 0
        },
        {
          "name": "valuation_currency",
          "type": "VARCHAR(8)",
          "not_null": true,
          "pk_position": 0
        },
        {
          "name": "updated_at",
          "type": "DATETIME",
          "not_null": false,
          "pk_position": 0
        }
      ],
      "primary_key": [
        "id"
      ],
      "indexes": [
        {
          "name": "ix_portfolio_positions_symbol",
          "unique": false,
          "columns": [
            "symbol"
          ]
        },
        {
          "name": "ix_portfolio_positions_account_id",
          "unique": false,
          "columns": [
            "account_id"
          ]
        },
        {
          "name": "ix_portfolio_positions_updated_at",
          "unique": false,
          "columns": [
            "updated_at"
          ]
        },
        {
          "name": "sqlite_autoindex_portfolio_positions_1",
          "unique": true,
          "columns": [
            "account_id",
            "symbol",
            "market",
            "currency",
            "cost_method"
          ]
        }
      ],
      "foreign_keys": [
        {
          "from": "account_id",
          "table": "portfolio_accounts",
          "to": "id"
        }
      ]
    },
    {
      "name": "portfolio_trades",
      "columns": [
        {
          "name": "id",
          "type": "INTEGER",
          "not_null": true,
          "pk_position": 1
        },
        {
          "name": "account_id",
          "type": "INTEGER",
          "not_null": true,
          "pk_position": 0
        },
        {
          "name": "trade_uid",
          "type": "VARCHAR(128)",
          "not_null": false,
          "pk_position": 0
        },
        {
          "name": "symbol",
          "type": "VARCHAR(16)",
          "not_null": true,
          "pk_position": 0
        },
        {
          "name": "market",
          "type": "VARCHAR(8)",
          "not_null": true,
          "pk_position": 0
        },
        {
          "name": "currency",
          "type": "VARCHAR(8)",
          "not_null": true,
          "pk_position": 0
        },
        {
          "name": "trade_date",
          "type": "DATE",
          "not_null": true,
          "pk_position": 0
        },
        {
          "name": "side",
          "type": "VARCHAR(8)",
          "not_null": true,
          "pk_position": 0
        },
        {
          "name": "quantity",
          "type": "FLOAT",
          "not_null": true,
          "pk_position": 0
        },
        {
          "name": "price",
          "type": "FLOAT",
          "not_null": true,
          "pk_position": 0
        },
        {
          "name": "fee",
          "type": "FLOAT",
          "not_null": false,
          "pk_position": 0
        },
        {
          "name": "tax",
          "type": "FLOAT",
          "not_null": false,
          "pk_position": 0
        },
        {
          "name": "note",
          "type": "VARCHAR(255)",
          "not_null": false,
          "pk_position": 0
        },
        {
          "name": "dedup_hash",
          "type": "VARCHAR(64)",
          "not_null": false,
          "pk_position": 0
        },
        {
          "name": "created_at",
          "type": "DATETIME",
          "not_null": false,
          "pk_position": 0
        }
      ],
      "primary_key": [
        "id"
      ],
      "indexes": [
        {
          "name": "ix_portfolio_trades_account_id",
          "unique": false,
          "columns": [
            "account_id"
          ]
        },
        {
          "name": "ix_portfolio_trades_dedup_hash",
          "unique": false,
          "columns": [
            "dedup_hash"
          ]
        },
        {
          "name": "ix_portfolio_trades_created_at",
          "unique": false,
          "columns": [
            "created_at"
          ]
        },
        {
          "name": "ix_portfolio_trades_symbol",
          "unique": false,
          "columns": [
            "symbol"
          ]
        },
        {
          "name": "ix_portfolio_trades_trade_date",
          "unique": false,
          "columns": [
            "trade_date"
          ]
        },
        {
          "name": "ix_portfolio_trade_account_date",
          "unique": false,
          "columns": [
            "account_id",
            "trade_date"
          ]
        },
        {
          "name": "sqlite_autoindex_portfolio_trades_2",
          "unique": true,
          "columns": [
            "account_id",
            "dedup_hash"
          ]
        },
        {
          "name": "sqlite_autoindex_portfolio_trades_1",
          "unique": true,
          "columns": [
            "account_id",
            "trade_uid"
          ]
        }
      ],
      "foreign_keys": [
        {
          "from": "account_id",
          "table": "portfolio_accounts",
          "to": "id"
        }
      ]
    },
    {
      "name": "schema_migrations",
      "columns": [
        {
          "name": "version",
          "type": "VARCHAR(64)",
          "not_null": true,
          "pk_position": 1
        },
        {
          "name": "description",
          "type": "VARCHAR(255)",
          "not_null": true,
          "pk_position": 0
        },
        {
          "name": "applied_at",
          "type": "DATETIME",
          "not_null": true,
          "pk_position": 0
        }
      ],
      "primary_key": [
        "version"
      ],
      "indexes": [
        {
          "name": "ix_schema_migrations_applied_at",
          "unique": false,
          "columns": [
            "applied_at"
          ]
        },
        {
          "name": "sqlite_autoindex_schema_migrations_1",
          "unique": true,
          "columns": [
            "version"
          ]
        }
      ],
      "foreign_keys": []
    },
    {
      "name": "screening_runs",
      "columns": [
        {
          "name": "id",
          "type": "INTEGER",
          "not_null": true,
          "pk_position": 1
        },
        {
          "name": "run_id",
          "type": "VARCHAR(64)",
          "not_null": true,
          "pk_position": 0
        },
        {
          "name": "strategy",
          "type": "VARCHAR(64)",
          "not_null": true,
          "pk_position": 0
        },
        {
          "name": "market",
          "type": "VARCHAR(16)",
          "not_null": true,
          "pk_position": 0
        },
        {
          "name": "snapshot_source",
          "type": "VARCHAR(64)",
          "not_null": false,
          "pk_position": 0
        },
        {
          "name": "snapshot_count",
          "type": "INTEGER",
          "not_null": false,
          "pk_position": 0
        },
        {
          "name": "after_filter_count",
          "type": "INTEGER",
          "not_null": false,
          "pk_position": 0
        },
        {
          "name": "candidate_count",
          "type": "INTEGER",
          "not_null": true,
          "pk_position": 0
        },
        {
          "name": "llm_ranked",
          "type": "BOOLEAN",
          "not_null": false,
          "pk_position": 0
        },
        {
          "name": "daily_enriched",
          "type": "BOOLEAN",
          "not_null": false,
          "pk_position": 0
        },
        {
          "name": "source_errors_json",
          "type": "TEXT",
          "not_null": false,
          "pk_position": 0
        },
        {
          "name": "warnings_json",
          "type": "TEXT",
          "not_null": false,
          "pk_position": 0
        },
        {
          "name": "result_json",
          "type": "TEXT",
          "not_null": true,
          "pk_position": 0
        },
        {
          "name": "created_at",
          "type": "DATETIME",
          "not_null": true,
          "pk_position": 0
        }
      ],
      "primary_key": [
        "id"
      ],
      "indexes": [
        {
          "name": "ix_screening_runs_snapshot_source",
          "unique": false,
          "columns": [
            "snapshot_source"
          ]
        },
        {
          "name": "ix_screening_runs_strategy",
          "unique": false,
          "columns": [
            "strategy"
          ]
        },
        {
          "name": "ix_screening_runs_created_at",
          "unique": false,
          "columns": [
            "created_at"
          ]
        },
        {
          "name": "ix_screening_runs_run_id",
          "unique": true,
          "columns": [
            "run_id"
          ]
        },
        {
          "name": "ix_screening_runs_market",
          "unique": false,
          "columns": [
            "market"
          ]
        },
        {
          "name": "ix_screening_run_market_created",
          "unique": false,
          "columns": [
            "market",
            "created_at"
          ]
        },
        {
          "name": "ix_screening_run_strategy_created",
          "unique": false,
          "columns": [
            "strategy",
            "created_at"
          ]
        }
      ],
      "foreign_keys": []
    },
    {
      "name": "skill_opinion_outcomes",
      "columns": [
        {
          "name": "id",
          "type": "INTEGER",
          "not_null": true,
          "pk_position": 1
        },
        {
          "name": "skill_opinion_sample_id",
          "type": "INTEGER",
          "not_null": true,
          "pk_position": 0
        },
        {
          "name": "horizon",
          "type": "VARCHAR(16)",
          "not_null": true,
          "pk_position": 0
        },
        {
          "name": "engine_version",
          "type": "VARCHAR(32)",
          "not_null": true,
          "pk_position": 0
        },
        {
          "name": "eval_status",
          "type": "VARCHAR(24)",
          "not_null": true,
          "pk_position": 0
        },
        {
          "name": "outcome",
          "type": "VARCHAR(16)",
          "not_null": false,
          "pk_position": 0
        },
        {
          "name": "direction_correct",
          "type": "BOOLEAN",
          "not_null": false,
          "pk_position": 0
        },
        {
          "name": "unable_reason",
          "type": "VARCHAR(64)",
          "not_null": false,
          "pk_position": 0
        },
        {
          "name": "analysis_date",
          "type": "DATE",
          "not_null": false,
          "pk_position": 0
        },
        {
          "name": "start_trade_date",
          "type": "DATE",
          "not_null": false,
          "pk_position": 0
        },
        {
          "name": "end_trade_date",
          "type": "DATE",
          "not_null": false,
          "pk_position": 0
        },
        {
          "name": "start_price",
          "type": "FLOAT",
          "not_null": false,
          "pk_position": 0
        },
        {
          "name": "end_close",
          "type": "FLOAT",
          "not_null": false,
          "pk_position": 0
        },
        {
          "name": "stock_return_pct",
          "type": "FLOAT",
          "not_null": false,
          "pk_position": 0
        },
        {
          "name": "directional_return_pct",
          "type": "FLOAT",
          "not_null": false,
          "pk_position": 0
        },
        {
          "name": "created_at",
          "type": "DATETIME",
          "not_null": false,
          "pk_position": 0
        },
        {
          "name": "updated_at",
          "type": "DATETIME",
          "not_null": false,
          "pk_position": 0
        }
      ],
      "primary_key": [
        "id"
      ],
      "indexes": [
        {
          "name": "ix_skill_opinion_outcomes_engine_version",
          "unique": false,
          "columns": [
            "engine_version"
          ]
        },
        {
          "name": "ix_skill_opinion_outcome_candidate",
          "unique": false,
          "columns": [
            "engine_version",
            "eval_status",
            "updated_at"
          ]
        },
        {
          "name": "ix_skill_opinion_outcomes_unable_reason",
          "unique": false,
          "columns": [
            "unable_reason"
          ]
        },
        {
          "name": "ix_skill_opinion_outcomes_skill_opinion_sample_id",
          "unique": false,
          "columns": [
            "skill_opinion_sample_id"
          ]
        },
        {
          "name": "ix_skill_opinion_outcomes_eval_status",
          "unique": false,
          "columns": [
            "eval_status"
          ]
        },
        {
          "name": "ix_skill_opinion_outcomes_analysis_date",
          "unique": false,
          "columns": [
            "analysis_date"
          ]
        },
        {
          "name": "ix_skill_opinion_outcomes_updated_at",
          "unique": false,
          "columns": [
            "updated_at"
          ]
        },
        {
          "name": "ix_skill_opinion_outcomes_created_at",
          "unique": false,
          "columns": [
            "created_at"
          ]
        },
        {
          "name": "ix_skill_opinion_outcome_horizon_status",
          "unique": false,
          "columns": [
            "engine_version",
            "horizon",
            "eval_status"
          ]
        },
        {
          "name": "ix_skill_opinion_outcomes_outcome",
          "unique": false,
          "columns": [
            "outcome"
          ]
        },
        {
          "name": "ix_skill_opinion_outcomes_end_trade_date",
          "unique": false,
          "columns": [
            "end_trade_date"
          ]
        },
        {
          "name": "ix_skill_opinion_outcomes_start_trade_date",
          "unique": false,
          "columns": [
            "start_trade_date"
          ]
        },
        {
          "name": "ix_skill_opinion_outcomes_horizon",
          "unique": false,
          "columns": [
            "horizon"
          ]
        },
        {
          "name": "sqlite_autoindex_skill_opinion_outcomes_1",
          "unique": true,
          "columns": [
            "skill_opinion_sample_id",
            "horizon",
            "engine_version"
          ]
        }
      ],
      "foreign_keys": [
        {
          "from": "skill_opinion_sample_id",
          "table": "skill_opinion_samples",
          "to": "id"
        }
      ]
    },
    {
      "name": "skill_opinion_samples",
      "columns": [
        {
          "name": "id",
          "type": "INTEGER",
          "not_null": true,
          "pk_position": 1
        },
        {
          "name": "analysis_history_id",
          "type": "INTEGER",
          "not_null": true,
          "pk_position": 0
        },
        {
          "name": "stock_code",
          "type": "VARCHAR(16)",
          "not_null": true,
          "pk_position": 0
        },
        {
          "name": "skill_id",
          "type": "VARCHAR(128)",
          "not_null": true,
          "pk_position": 0
        },
        {
          "name": "skill_version",
          "type": "VARCHAR(64)",
          "not_null": false,
          "pk_position": 0
        },
        {
          "name": "signal",
          "type": "VARCHAR(16)",
          "not_null": true,
          "pk_position": 0
        },
        {
          "name": "confidence",
          "type": "FLOAT",
          "not_null": true,
          "pk_position": 0
        },
        {
          "name": "horizon",
          "type": "VARCHAR(16)",
          "not_null": false,
          "pk_position": 0
        },
        {
          "name": "data_quality_level",
          "type": "VARCHAR(24)",
          "not_null": false,
          "pk_position": 0
        },
        {
          "name": "opinion_created_at",
          "type": "DATETIME",
          "not_null": false,
          "pk_position": 0
        },
        {
          "name": "sample_schema_version",
          "type": "VARCHAR(32)",
          "not_null": true,
          "pk_position": 0
        },
        {
          "name": "created_at",
          "type": "DATETIME",
          "not_null": false,
          "pk_position": 0
        }
      ],
      "primary_key": [
        "id"
      ],
      "indexes": [
        {
          "name": "ix_skill_opinion_samples_signal",
          "unique": false,
          "columns": [
            "signal"
          ]
        },
        {
          "name": "ix_skill_opinion_samples_stock_code",
          "unique": false,
          "columns": [
            "stock_code"
          ]
        },
        {
          "name": "ix_skill_opinion_samples_opinion_created_at",
          "unique": false,
          "columns": [
            "opinion_created_at"
          ]
        },
        {
          "name": "ix_skill_opinion_samples_skill_id",
          "unique": false,
          "columns": [
            "skill_id"
          ]
        },
        {
          "name": "ix_skill_opinion_samples_horizon",
          "unique": false,
          "columns": [
            "horizon"
          ]
        },
        {
          "name": "ix_skill_opinion_samples_sample_schema_version",
          "unique": false,
          "columns": [
            "sample_schema_version"
          ]
        },
        {
          "name": "ix_skill_opinion_samples_analysis_history_id",
          "unique": false,
          "columns": [
            "analysis_history_id"
          ]
        },
        {
          "name": "ix_skill_opinion_sample_skill_horizon_created",
          "unique": false,
          "columns": [
            "skill_id",
            "horizon",
            "created_at"
          ]
        },
        {
          "name": "ix_skill_opinion_samples_skill_version",
          "unique": false,
          "columns": [
            "skill_version"
          ]
        },
        {
          "name": "ix_skill_opinion_sample_stock_created",
          "unique": false,
          "columns": [
            "stock_code",
            "created_at"
          ]
        },
        {
          "name": "ix_skill_opinion_samples_created_at",
          "unique": false,
          "columns": [
            "created_at"
          ]
        },
        {
          "name": "ix_skill_opinion_samples_data_quality_level",
          "unique": false,
          "columns": [
            "data_quality_level"
          ]
        },
        {
          "name": "sqlite_autoindex_skill_opinion_samples_1",
          "unique": true,
          "columns": [
            "analysis_history_id",
            "skill_id",
            "sample_schema_version"
          ]
        }
      ],
      "foreign_keys": [
        {
          "from": "analysis_history_id",
          "table": "analysis_history",
          "to": "id"
        }
      ]
    },
    {
      "name": "stock_daily",
      "columns": [
        {
          "name": "id",
          "type": "INTEGER",
          "not_null": true,
          "pk_position": 1
        },
        {
          "name": "code",
          "type": "VARCHAR(10)",
          "not_null": true,
          "pk_position": 0
        },
        {
          "name": "date",
          "type": "DATE",
          "not_null": true,
          "pk_position": 0
        },
        {
          "name": "open",
          "type": "FLOAT",
          "not_null": false,
          "pk_position": 0
        },
        {
          "name": "high",
          "type": "FLOAT",
          "not_null": false,
          "pk_position": 0
        },
        {
          "name": "low",
          "type": "FLOAT",
          "not_null": false,
          "pk_position": 0
        },
        {
          "name": "close",
          "type": "FLOAT",
          "not_null": false,
          "pk_position": 0
        },
        {
          "name": "volume",
          "type": "FLOAT",
          "not_null": false,
          "pk_position": 0
        },
        {
          "name": "amount",
          "type": "FLOAT",
          "not_null": false,
          "pk_position": 0
        },
        {
          "name": "pct_chg",
          "type": "FLOAT",
          "not_null": false,
          "pk_position": 0
        },
        {
          "name": "ma5",
          "type": "FLOAT",
          "not_null": false,
          "pk_position": 0
        },
        {
          "name": "ma10",
          "type": "FLOAT",
          "not_null": false,
          "pk_position": 0
        },
        {
          "name": "ma20",
          "type": "FLOAT",
          "not_null": false,
          "pk_position": 0
        },
        {
          "name": "volume_ratio",
          "type": "FLOAT",
          "not_null": false,
          "pk_position": 0
        },
        {
          "name": "data_source",
          "type": "VARCHAR(50)",
          "not_null": false,
          "pk_position": 0
        },
        {
          "name": "canonical_id",
          "type": "VARCHAR(32)",
          "not_null": false,
          "pk_position": 0
        },
        {
          "name": "created_at",
          "type": "DATETIME",
          "not_null": false,
          "pk_position": 0
        },
        {
          "name": "updated_at",
          "type": "DATETIME",
          "not_null": false,
          "pk_position": 0
        }
      ],
      "primary_key": [
        "id"
      ],
      "indexes": [
        {
          "name": "ix_stock_daily_date",
          "unique": false,
          "columns": [
            "date"
          ]
        },
        {
          "name": "ix_stock_daily_canonical_id",
          "unique": false,
          "columns": [
            "canonical_id"
          ]
        },
        {
          "name": "ix_stock_daily_code",
          "unique": false,
          "columns": [
            "code"
          ]
        },
        {
          "name": "ix_code_date",
          "unique": false,
          "columns": [
            "code",
            "date"
          ]
        },
        {
          "name": "sqlite_autoindex_stock_daily_1",
          "unique": true,
          "columns": [
            "code",
            "date"
          ]
        }
      ],
      "foreign_keys": []
    }
  ]
}
```
<!-- G0_INVENTORY_END -->
