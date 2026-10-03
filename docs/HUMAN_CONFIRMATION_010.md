# #010 人工确认、最终快照与证据桥接

日期：2026-10-03。基线 main：`4a244f61642c99643c6cf656252957d2168629f4`，412/412 PASS。

```text
HUMAN_CONFIRMATION_CAPABILITY=DEVELOPED
FINAL_ANALYSIS_CONTRACTS=SEALED
ACTOR_ID_BOUND=YES
ACTOR_DB_VERIFIED=NO
DATABASE_ENABLED=NO
ANALYSIS_REPORT_PERSISTENCE=NO
EVIDENCE_RECORD_PERSISTENCE=NO
LIVE_MODEL_CALL=NO
MODEL_REGENERATION=NO
PRODUCTION_VALIDATION=NO
NEW_DATA_SOURCE=NO
NEW_EVIDENCE_SOURCE=NO
AUTO_TRADING=NO
```

本任务实现进程内对象与 Gate。数据库启用、登录鉴权、UI 接入、生产运行和生产数据验证均未实施。
`actor_id` 由调用者显式提供；不生成用户身份，不宣称已校验 users.id FK。
seal 使用 SHA-256 做进程内完整性检查，延续 #009 的机制，不是用户身份认证、数字签名或可直接反序列化的持久化凭证。

## 输入与确认对象

`ConfirmationFlow(draft, run_evidence, *, analysis_report_id=None)` 分别调用
`validate_draft()` 和 `validate_analysis_run_evidence()`，任一失败即
`CONFIRMATION_INPUT_INVALID`。两个 Gate 都会执行；随后逐一比较
`input_hash / validated_output_hash / model_output_hash / template_hash / output_schema_version /
input_evidence_ids / used_evidence_ids`，禁止混用不同版本。
后续每次操作重新校验输入 seal 与流程日志 seal。

`flow.items` 是初始 PENDING 清单，key 为 `(target, reason_code)`：

- 七字段各有 `REVIEW_FIELD`，使所有字段的人工结果都有明确记录。
- 原 draft 的全部 frozen P3 清单原样保留：两项 `REVIEW_ADVICE`、全部
  WARNING/UNVERIFIED/缺失/不可用项的 `REVIEW_DATA_QUALITY`、`REVIEW_ASSUMPTION`。
- `FINAL_REPORT / REVIEW_FINAL_REPORT` 单独确认，不能通过普通 confirm 接口绕过快照检查。

`ConfirmationEvent` 记录 event_id、analysis_run_id、analysis_report_id、revision_id、target、reason_code、
previous_state、current_state、actor_id、decision_reason、created_at、override_id、human_decision，
最终报告事件额外绑定 `reviewed_snapshot_hash`。timestamp 必须有时区。

状态严格复用 `evidence.contracts.HumanConfirm`，只有 PENDING / CONFIRMED / REJECTED：

```text
PENDING   → CONFIRMED
PENDING   → REJECTED
CONFIRMED → REJECTED（必须说明原因）
```

所有驳回均要求非空 reason。重复确认或重复驳回直接拒绝，不追加事件。
REJECTED→CONFIRMED、CONFIRMED→PENDING、REJECTED→PENDING 均禁止。
公开日志是 tuple，事件是 frozen dataclass；历史 tuple 保持原样，追加生成新 tuple。
update/delete 接口分别报 `UPDATE_CONFIRMATION_EVENT=FORBIDDEN` 和
`DELETE_CONFIRMATION_EVENT=FORBIDDEN`。强制篡改事件、Override、输入或日志会破坏流程 seal。

## 人工修改

`flow.modify(target, reason_code, replacement, *, actor_id, reason, referenced_evidence_ids=(), created_at=None)`
是一项人工操作，绝不增加 MODIFIED human_confirm 状态。

```text
原 AI 项：PENDING/CONFIRMED → REJECTED；HUMAN_DECISION=MODIFY
独立 HumanOverride：PENDING → CONFIRMED；ORIGIN=HUMAN
```

两个事件在全部检查通过后一起追加，失败不留下半个操作。
Override 记录 override_id、run/report/revision ID、target、reason_code、original_value_hash、replacement、
reason、actor_id、created_at、referenced_evidence_ids、origin=HUMAN、human_confirm=CONFIRMED。
每个字段每个 revision 最多一个 Override，避免不同确认原因生成相互冲突的有效值。
Override 的撤销也追加 CONFIRMED→REJECTED 事件，target 为 `HUMAN_OVERRIDE:<override_id>`，
reason_code 为 `REVIEW_OVERRIDE`；旧 Override 不变。

V1 replacement 是完整的结构化字段，使用既有七字段 JSON schema 与受限判断词汇。
可调整 assessment、已有建议的 action/级别及受限推理步骤声明；不接受任意自由文本断言。
facts、limitations、data_quality_status、evidence_ids、input_refs、time_range 都必须逐字保持 canonical JSON 相等，
包括数值类型。价格、财务值、公告标题、持仓数量和成本等事实不得增删或改值。
推理步骤的事实引用和 Evidence 引用不得修改。条件式建议的观察路径、比较值和验证方式必须保留，
不允许把证据不足建议升级为更强建议。无内容变化的 Override 拒绝。
如认为事实有误，驳回后回到上游数据/分析链；此模块不修改 canonical 数据或重跑模型。

Override Evidence 引用必须唯一且属于 `run.input_evidence_ids`。
可以引用未被模型使用的输入作为人工判断的上下文，但不会把这些引用加入 Conclusion 的模型依据。
原 draft、run evidence、model_output_hash 和 validated_output_hash 始终不变。

## 待审快照与最终 Gate

```text
#009 sealed draft + run evidence
  → ConfirmationFlow（全项 PENDING）
  → confirm / reject / modify
  → prepare_final_snapshot(flow)：sealed PreparedFinalSnapshot
  → 人工查看 candidate 的完整内容
  → flow.confirm_final_report(candidate, actor_id=...)
  → finalize_analysis(flow, candidate, evidence_records=...)
  → sealed FinalAnalysisSnapshot + sealed FinalAnalysisEvidence + AnalysisConclusion
  → EvidenceTrace / trace_final_analysis
```

所有必要项都必须已处理。任何 PENDING 阻止 prepare，处理结果可以是确认、驳回或修改。
PreparedFinalSnapshot 为 `PENDING / PREPARED`，此阶段不生成 FINAL 对象或 Conclusion。
每个 revision 只能 prepare 一次；之后修改内容需要新 revision。
准备后允许追加驳回作纠错，但会使 candidate 过期，不能再确认或最终化这个 candidate。
最终确认必须传入该 flow 实际生成的 sealed candidate，事件绑定其 hash。
FINAL_REPORT=PENDING/REJECTED 不生成最终对象；确认后若又发生纠错，最终化也拒绝。
重复最终化拒绝，工厂失败时不占用 final_evidence_id。

seven_fields 恰好包含 market_view / position_status / risk_level / operation_plan /
t_strategy / reasoning / confidence。每个字段包含：

| DECISION（快照呈现，非 human_confirm 状态） | EFFECTIVE_VALUE |
| --- | --- |
| CONFIRMED | AI_ORIGINAL |
| REJECTED | NULL |
| MODIFIED | HUMAN_OVERRIDE.replacement |

字段同时显示 AI_ORIGINAL、原 AI_OUTPUT_HASH、HUMAN_DECISION、HUMAN_OVERRIDE、ORIGIN 和逐原因处理记录。
任何普通驳回优先于 Override，防止人工替换掩盖尚未采纳的其他确认原因。
驳回异常数据项会抑制引用该 limitation 的字段；驳回关键假设会抑制 reasoning。
撤销 Override 后该字段的有效值也为 NULL。
watch_items 保留数据不足/异常项和驳回原因，确认异常项不会把原数据质量改成 PASS。
关键假设及异常项的处理结果完整保存在 confirmation_decisions。

FinalAnalysisSnapshot 绑定 report/run/revision/stock ID、input_hash、原 model_output_hash、
original_validated_output_hash、model_version、seven_fields、全部处理记录、Override、watch_items、
完整 confirmation_events、confirmation_log_hash、prepared_snapshot_hash、confirmed_by、confirmed_at、
supersedes_analysis_report_id、final_snapshot_hash，固定 `CONFIRMED / FINAL`。
所有嵌套内容深度只读，to_dict 返回独立 JSON 视图。

## Final Evidence 与原始 EvidenceTrace

FinalAnalysisEvidence 是独立的 analysis-level 对象，绝不创建第四个 EvidenceSource，也不调用 EvidenceBuilder。
它绑定任务卡要求的全部 ID、模型身份、五个 provenance hash、input/used IDs、确认人和时间、
supersedes_final_evidence_id，固定 human_confirm=CONFIRMED。
`validate_final_analysis_evidence()` 检查 `_gate_digest`、hash 格式、身份、时间和 Evidence 子集。
Final Snapshot 也有 `_gate_digest` 与 `validate_final_snapshot()`。
手工重建或 `dataclasses.replace()`（即使无修改）丢失 seal；强改 actor/time/hash/IDs 等任意字段校验失败。

最终工厂要求调用者提供原 EvidenceRecord。对 used IDs 和有效 Override 的 referenced IDs 检查：存在且唯一、三源枚举、
validation=PASS、human_confirm 为类型正确的 PENDING/CONFIRMED、raw/normalized hash 存在，
并要求既有 EvidenceTrace 返回完整链。REJECTED 或缺失的 used Evidence 阻止创建 Conclusion。
采集证据 PENDING 可用，符合冻结 Evidence 分级；未使用且未被有效 Override 引用的输入即使驳回也不阻断。
有效人工修改引用的 Evidence 若缺失或已驳回，同样阻止最终化；这些人工上下文 ID 不加入模型 used 集合。

工厂直接创建既有 `evidence.contracts.AnalysisConclusion`：

- evidence_ids 精确等于 run.used_evidence_ids。
- model_version 精确等于 run.model_version。
- structured_fields 是保留原文/人工决定/有效值的七字段快照。
- calculation 绑定 analysis_run_id、input_hash、model_output_hash、validated_output_hash、
  final_snapshot_hash、confirmation_log_hash、final_evidence_id、prepared_snapshot_hash、
  confirmation_events 和 supersedes_analysis_report_id。

```text
Conclusion.calculation.final_evidence_id → 独立 FinalAnalysisEvidence
Conclusion.structured_fields            → 最终七字段与原 AI/人工修改
Final Evidence.model_output_hash        → 原模型响应版本
Final Evidence.validated_output_hash    → 原 sealed draft 的七字段
Conclusion.evidence_ids                 → used 输入 EvidenceRecord
EvidenceTrace.chain                     → 三源、采集时间、原 raw_response_path
```

`validate_finalization_result(result, draft, run)` 检查上述对象的绑定与 Calculation。
`trace_final_analysis()` 先校验完整桥接，再调用原 EvidenceTrace；不读取 raw 文件。
hash 重算包括原响应 UTF-8 bytes、原七字段 canonical JSON、input envelope、完整确认日志和最终快照。
输入原响应文本不由 #010 持久化；需要原响应字节做复核时由上游归档层提供。
本模块验证元数据链和 seal；原文件字节真实性/重新标准化复核继续属于既有 replay 与归档层。

## 事后纠错与新修订

对最终报告调用 `reject_final_report(candidate, actor_id=..., reason=...)`，追加 CONFIRMED→REJECTED。
旧 Final Snapshot、Final Evidence、Conclusion 的原始确认记录永久保留且不修改。
查询当前有效性时必须结合该 revision 的追加日志；历史 seal 有效不等于当前仍获认可。
如果使用的原始 Evidence 后来被驳回，原 EvidenceTrace 会返回 incomplete，桥接查询也拒绝。

`flow.new_revision(analysis_report_id=None)` 创建全新 PENDING 流程，复用同一 sealed #009 输入对，
生成新 revision_id 和不同的 analysis_report_id，链接 supersedes_analysis_report_id。
最终工厂生成新的 final_evidence_id，并链接 supersedes_final_evidence_id。
已确认报告必须先有确认后的纠错事件才能建立修订；之前的 MODIFY 不冒充事后纠错。
旧 REJECTED 不回转。若纠错涉及底层事实或模型重算，应在此流程之外重新通过数据与 #009 Gate。
ID 全局唯一性、持久化追加约束和当前 revision 选择留给接入层；本模块无数据库 DML。

## 离线验收

全量 524/524 PASS：原 412 项 + #010 新增 112 项（确认 50、最终化 42、Trace/离线流程 20）。
凭据值扫描、既有 CI 敏感文件规则扫描、八文件白名单与 whitespace 检查全部 PASS。

```bash
PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover -s tests -q
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=.:tests python3 -c 'import json; from test_confirmation_trace import offline_demonstration; print(json.dumps(offline_demonstration(), sort_keys=True))'
git diff --check
```

演示使用已通过 #009 Gate 的合成 fixture，不调用 AnalysisRunner 或 ModelExecutor。
两份输入 Evidence，一份被模型七字段实际使用；确认建议、驳回 T 策略、修改风险判断，
其余必要项逐一确认，再准备快照、单独确认最终报告、生成最终对象并调用原 EvidenceTrace。
全部 hash 重新核对，原 draft/run/EvidenceRecord 与处理前一致。
进入 #010 前构建 fixture；进入后对文件 I/O、网络、数据库、Runner、Provider、Normalizer、EvidenceBuilder
和上游 Gate 重跑设 mock 守卫，所有调用均为零。

```text
E2E_HUMAN_CONFIRMATION_FLOW=PASS
INPUT_EVIDENCE_COUNT=2
USED_EVIDENCE_COUNT=1
CONFIRMATION_EVENT_COUNT=18
FINAL_FIELDS=7
TRACE_COMPLETE=YES
AI_ORIGINAL_UNCHANGED=YES
DATABASE_WRITE=0
MODEL_CALL=0
PROVIDER_CALL=0
NORMALIZER_CALL=0
RAW_WRITE=0
TRADING_WRITE=0
BROKER_INTEGRATION=0
```

验收映射：

| 任务卡编号 | 验证内容 |
| --- | --- |
| 1–2 | 原 412 基线与新增确认/最终化/Trace 测试 |
| 3–5 | 未 sealed、跨版本与各绑定字段不一致拒绝 |
| 6–14 | 三状态转换、原因、重复、append-only、actor、timezone |
| 15–20 | 原 AI 不变、双事件 MODIFY、HUMAN 来源、引用子集与事实修改拒绝 |
| 21–24 | 两项建议、所有质量/不足项及关键假设明确处理 |
| 25–30 | 必要项/最终报告 Gate、七字段、拒绝 NULL、修改双视图 |
| 31–33 | Final Evidence seal、replace、全字段强制篡改失败 |
| 34–38 | used IDs、模型身份、hash 重算、原 EvidenceTrace、拒绝源证据阻断 |
| 39–40 | 纠错日志、旧对象不变、新报告与两类 supersedes link |
| 41–46 | 完整离线链副作用 mock 守卫与零调用/零写入 |
| 47–48 | 凭据值/敏感文件扫描、whitespace 与八文件白名单检查 |

变更仅限任务卡八个文件；未改 evidence/*、runner、output_validator、package_builder、prompt_builder、
Provider、Normalizer、冻结文档、schema、migration。初始已存在的六个未跟踪 __pycache__ 目录保持原样。
