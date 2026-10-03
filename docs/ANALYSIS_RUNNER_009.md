# #009 Analysis Runner：离线待确认分析链路

日期：2026-10-03。基线 main：`1b842b35a57a68f6a0473e4aab056c45b10f3e0b`。

```text
ANALYSIS_RUNNER_CAPABILITY=DEVELOPED
RUNTIME_ANALYSIS_MODEL=NOT_FROZEN
LIVE_MODEL_BACKEND_CONFIGURED=NO
LIVE_MODEL_RUNNING=NO
REAL_MODEL_OUTPUT_VERIFIED=NO
ANALYSIS_STATUS=UNCONFIRMED
HUMAN_CONFIRM=PENDING
FINAL_CONCLUSION=NO
VALIDATE_ONLY=YES
REWRITE_CONTENT=FORBIDDEN
GENERATE_MISSING_FIELD=FORBIDDEN
DATABASE_WRITE=0
PROVIDER_CALL=0
RAW_READ_BY_RUNNER=0
ANALYSIS_REPORT_PERSISTENCE=NO
ANALYSIS_TASK_PERSISTENCE=NO
HUMAN_CONFIRM_TRANSITION=NO
```

开发模型不代表运行模型。生产接口没有供应商、默认模型、网络适配器或 credential。
项目只新增 Protocol，测试中的 deterministic fake 不属于正式模型实现。

## 边界与接口

```text
已通过 #007/#008 Gate 的 AnalysisPackage
  → validate_package
  → 原 PromptBuilder → 冻结 P3 PromptEnvelope
  → injected ModelExecutor.generate(envelope, output_schema)（至多一次）
  → 原始 JSON text → OutputParser → SevenFieldValidator
  → 不可变 ValidatedAnalysisDraft
  → 不可变 AnalysisRunEvidence（UNCONFIRMED / PENDING）
```

```python
from analysis import AnalysisRunner

# package 必须是 AnalysisPackageBuilder 的 Gate 产物，不能反序列化字典冒充。
# executor 必须明确提供 model_version 与 generate；本任务不提供生产 adapter。
result = AnalysisRunner(executor).run(package)
if result.status == "UNCONFIRMED":
    draft, run_evidence = result.draft, result.evidence
    assert run_evidence.input_hash == result.prompt_envelope.input_hash
    assert run_evidence.human_confirm == "PENDING"
```

`INPUT_INVALID`：未通过 Gate、重建/replace 后失去 seal、Gate 后强制改动；不调用模型。
`MODEL_FAILED`：模型身份缺失/敏感、executor 异常、生成期间 model_version 改变；不重试。
`OUTPUT_INVALID`：非法 JSON 或任何输出 Gate 失败；不生成草稿/运行证据。
所有失败返回 `draft=None / evidence=None`，错误码不携带异常正文或原始模型内容。

Runner 的唯一输入源是 package。PromptBuilder 按 #007 原实现只读一次冻结模板；
后续校验只解码内存中的 envelope，不读取 Provider、Normalizer、数据库、raw 或 Evidence 文件。
Runner 不提供任何持久化接口。注入 executor 的外部行为由调用者负责；本次实际执行均使用测试 fake。
没有通过计数器宣称任意第三方 executor 都不会自行联网。

## 三段式与七字段 schema

`output_schema()` 返回独立的 JSON Schema（版本 `P3_SEVEN_FIELD_OUTPUT_V1.0`）：

```text
response（恰好三个键）
├─ analysis（恰好七个业务字段）
│  ├─ market_view
│  ├─ position_status
│  ├─ risk_level
│  ├─ operation_plan
│  ├─ t_strategy
│  ├─ reasoning
│  └─ confidence
├─ human_confirmation_items
└─ final_state = UNCONFIRMED
```

确认项和状态是冻结 P3 的第二、第三段，不是第八/第九个分析字段。
每个层级拒绝缺字段、多字段、错误类型；字段内部也拒绝未知属性。

每个分析字段包含 `content / evidence_ids / time_range / data_quality_status`。
`position_status` 另含 `input_refs`。`content` 使用以下结构：

- `statement`：本 schema 的受限分析/不足声明词汇。
- `facts`：`input_path / value / evidence_ids`；路径指向 Prompt canonical record 的非 NULL 标量，
  值和 JSON 数值类型必须与输入一致，事实自己的 Evidence 必须实际支持那条记录，且被字段引用。
- `limitations`：`input_path / status / reason / impact`；状态必须与输入一致，
  reason 逐字使用输入区块的原因；输入没有原因时使用明确的 WARNING/UNVERIFIED/缺失声明，
  impact 显式声明本字段判断受限、置信度降低。
- 市场/风险/置信度字段另有对应 `assessment` 枚举；建议位另有结构化 `advice`。
- `confidence.assessment` 支持 `HIGH / MEDIUM / LOW`，保留 `statement` 中的置信度依据。
  MEDIUM 不放宽建议门槛：LEVEL_1 仍须 HIGH，LEVEL_2 仍须 LOW。
- `reasoning.content.steps` 是至少含一步的有序数组；每步须有 `statement / evidence_ids`，
  可选 `fact_refs` 是本字段 `facts` 中的精确 `input_path` 列表。
  步骤声明限定为“核对所引用输入事实”“依据所引用事实与限制，结论须人工核查”或
  “输入缺失，无法形成有证据支撑的结论”，不能承载模型记忆或额外公司事实。
  每步 Evidence 必须属于本字段及 Prompt，且实际支持所引用事实；不能用一个合法 ID 支撑另一条记录。
  不提供 `fact_refs` 时，仅使用本步骤 Evidence 支持的已校验事实；显式引用不存在或不受支持的事实即拒绝。
  核对事实步骤必须有事实支撑；缺失步骤须有明确限制、空 Evidence；结论步骤须有事实或明确限制。

例如已有日 K 收盘价事实为：

```json
{
  "input_path": "canonical_market_data.daily_kline.records.0.data.close",
  "value": 10.5,
  "evidence_ids": ["EV-DAILY"]
}
```

任意自由文本公司事实不能可靠地靠关键词证明来自 Prompt，因此 V1 不接受这类文本。
源提供 title/summary 可通过精确路径和值引用；公司事实不能藏在 statement、reason、确认项或新增键里。
这只是可验证的输出表示，不冻结新的分析评分算法，也不证明趋势/风险/建议的投资正确性。
Validator 不润色、不填充、不计算模型遗漏的持仓盈亏、不推导财务指标。

`time_range={start,end}` 必须显式存在，等于字段所引用 Evidence 在 Prompt 中对应的数据时间范围。
只用 `trade_date / quote_timestamp / report_date / publish_time / publish_date`；
引用持仓输入时还纳入 `snapshot_time`。无可用数据时间时两个值都为 NULL。
采集 request clock 不冒充出版时间；不猜测缺失时间，不用当前机器时间补数据时间。

`evidence_ids` 必须是 Prompt IDs 子集；每条 fact 还检查实际记录关联，不能借一个合法 ID 支撑另一条记录。
WARNING/UNVERIFIED 不能标成 PASS，UNVERIFIED 不能降为 WARNING。
缺失数据不可作为已获得的非 NULL 事实。已验证历史字段可保留 PASS，其他区块缺失不阻断历史分析。

`WATCH_ONLY`：`position_status.statement="无持仓（观察模式）"`、`input_refs=[]`，禁止持仓事实。
`HOLDING_ANALYSIS`：必须引用 `holding_position` 并至少引用实际日 K/行情 Evidence。
数量/成本/已提供的盈亏只来自 `holding_position.records.*.data`，其 provenance 是 `portfolio_positions`，
事实 `evidence_ids=[]`；行情判断由对应市场 Evidence 支持。没有重造 WORKBUDDY 持仓 Evidence。
持仓模式无行情 Evidence 时 fail-closed，不伪装成 WATCH_ONLY 或补造行情。

`REALTIME_QUOTE_STATUS=MISSING` 时允许明确的历史分析。
schema 不接受“当前价/实时价”的自由文本断言，也不允许把 STALE/UNKNOWN quote 用作实时条件触发。
所有输出拒绝 credential/raw/provider payload、自动交易/已下单/已委托/已成交和确认完成状态。

## 三级建议与人工确认

`operation_plan / t_strategy` 的 advice：

- `LEVEL_1`：明确 action，必须有实际市场/财务/资讯事实支撑、HIGH 置信度、PASS 质量。
  仅使用现有 Gate 的 `COMPLETE / complete_analysis_input` 和明确状态：所有区块与源 PASS，
  没有 missing/stale/warning。输入不满足时拒绝，不自行判断哪些缺口可以忽略。
  #008 当前估值/行业缺失、信息 UNVERIFIED，实际合法包不能通过 Level 1；这不是开放 Level 1 的质量证明。
- `LEVEL_2`：LOW、条件式 action、显式不足项/原因/影响，以及 `condition / observation_path / MANUAL_RECHECK`。
  触发条件必须绑定已有证据支持的数值观察项，比较基准直接来自输入；不编造目标价或充分度阈值。
  这里的 GT/LT/EQ 是模型表达条件的运算符，不是系统判断证据充分度的评分规则。
- `LEVEL_3`：仅声明“证据不足，暂不给出操作建议”，禁止夹带 action；无证据时可继续生成此类待确认草稿。

两项建议、所有 WARNING/UNVERIFIED 区块/源、缺失区块和模型引用的不足项均须进入确认清单。
关键假设明确为“分析仅依据所提供的证据，缺失数据不补足”；schema 不允许引入无法校验的额外公司假设。
只生成清单和 `UNCONFIRMED` 状态；没有确认、批准、采纳或执行状态流转。

## Hash 与运行证据

`model_output_hash = SHA256(executor 返回的完整原始 JSON text 的 UTF-8 bytes)`。
不在 hash 前重新编码或修改 executor 文本，响应不接受 dict、fence、修复后的 JSON。

`validated_output_hash = SHA256(stable_json(parsed_response["analysis"]).encode("utf-8"))`。
这是固定的 canonical 七字段序列化视图：保留所有 content、引用、时间和状态值，不重排数组。
完整原响应 hash 与七字段 canonical 视图 hash 的范围由 schema 明确区分；
差异来自 JSON 解析及上述确定性序列化视图，不代表任何内容修订。
第二、第三段仍完整保存在 draft 的 confirmation items 和 final_state，不被删除或改写。
测试同时断言原七字段与 draft.to_dict()["output"] 完全相等、不同 JSON 排版的 validated hash 一致。

`AnalysisRunEvidence` 绑定：
`analysis_run_id / stock_id / prompt_version / template_hash / input_hash / output_schema_version /
model_version / model_output_hash / validated_output_hash / input_evidence_ids / used_evidence_ids /
analysis_status=UNCONFIRMED / human_confirm=PENDING / created_at / final_conclusion=False`。

draft 和运行证据使用与 #007/#008 类似的进程内 seal：手工构造、dataclasses.replace 或强制改动后的草稿不能进入 factory。
`AnalysisRunEvidence.from_draft()` 校验 prompt 绑定及实际七字段引用集合后设置 `_gate_digest`，
覆盖上述全部 15 个 provenance 与业务状态字段，并调用 `validate_analysis_run_evidence()`。
后续使用可从 `analysis.analysis_evidence` 导入该函数校验；手工构造或 replace（含无改动的 replace）
都会失去 seal，强制改动任一字段会造成 digest mismatch，均拒绝。
seal 为 `init=False`，不进入 `to_dict()` 或 repr；它是完整性检查，不是外部签名或认证。
运行 ID 用新 UUID，created_at 是有时区的运行创建时间；它不是行情时钟。
draft 深度只读、证据引用 tuple、所有业务状态字段禁止构造时指定确认完成状态。

`AnalysisRunEvidence != EvidenceRecord != AnalysisConclusion`。
未修改 EvidenceBuilder，也不调用其 `analysis_output` 路径。最终 Evidence/Conclusion 链留给 #010。

## 验证

```bash
PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover -s tests -q
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=. python3 /tmp/verify009_offline.py
git diff --check
```

独立基线 290/290 PASS。Review B 返修前 386/386 PASS；返修后全部 412/412 PASS：
原 290 项 + #009 原新增 96 项 + 本次针对两个阻断项新增 26 项。
新增测试位于 `test_analysis_output.py / test_analysis_runner.py / test_analysis_evidence.py`，
覆盖任务卡列举的全部 18 条失败路径及持仓时钟、字段与记录的 Evidence 关联、seal、schema mutation、
hash、确认项、一次调用、供应商中立状态和副作用守卫。
本次回归补充 MEDIUM、非空步骤、步骤 Prompt 子集与实际事实关联、受限声明、
Runner 拒绝非法步骤，以及运行证据手工构造、replace、全部 15 个字段强制篡改和 seal 丢失/篡改。

真实离线验收使用既有 `/tmp/verify_information_008_repair.py` 的六份真实 archive。
`/tmp/verify009_offline.py` 去除该上游验收脚本的 artifact 写入部分，保留原 replay 的完整校验，
在 Runner 之前构建新的合法 sealed AnalysisPackage；没有把之前持久化的 JSON 直接冒充 Gate 产物。
notice/news/report/profit/balance/cashflow 分别 5/5/5/24/23/23 条，共 85 条。
输入包含 6 个验证 Evidence，fake 使用其中 profit 的 1 个 Evidence；未引用的其余 ID 保留在 input 集合。
市场证据缺失时 market_view 趋势不明；两项建议 LEVEL_3；信息 UNVERIFIED、估值和行业 MISSING 均显式待核查。

```text
E2E_OFFLINE_ANALYSIS_RUN=PASS
CANONICAL_RECORD_COUNT=85
PACKAGE_STATUS=INCOMPLETE
MODEL_CALL_COUNT=1（OFFLINE-DETERMINISTIC-FAKE-009）
INPUT_EVIDENCE_COUNT=6
USED_EVIDENCE_COUNT=1
ORIGINAL_FILES_UNCHANGED=YES
RAW_READ_BY_RUNNER=0
DATABASE_WRITE=0
PROVIDER_CALL=0
ANALYSIS_REPORT_PERSISTENCE=NO
REAL_MODEL_OUTPUT_VERIFIED=NO
```

上游 replay 的 raw 读取只用于 #008 的既有 Gate，发生在 Runner 接收 package 之前。
Runner 阶段所有 Provider fetch、Normalizer normalize、EvidenceBuilder、数据库、网络、文件写入均通过 mock 守卫核查；
唯一一次文件读取是原 PromptBuilder 的冻结模板读取。另测 executor 开始后禁止所有文件读取。
原 archive 文件 SHA-256 前后不变。fake 与原输入范围 hash 均重算一致。
此验收只证明 Runner 链路；不代表真实模型输出质量、投资有效性或实时股票分析已经验证。

| 验收编号 | 核查结果 |
| --- | --- |
| 1–2 | 290 项基线、96 项 #009 测试及 26 项 Review B 回归全部 PASS |
| 3–6 | sealed package 必需；模板/input hash 重算；单次 generate；Runner 无额外输入读取 |
| 7–9 | 七字段、逐字段证据/时间/质量、Prompt 子集和记录级引用 PASS |
| 10–12 | portfolio input 与市场 Evidence 分离；WATCH/HOLDING 正常与拒绝路径 PASS |
| 13–16 | 实时缺失、信息 UNVERIFIED、估值/行业缺失、三级建议与无证据门槛 PASS |
| 17 | 禁止交易写入、执行宣称、指令格式和未知文本/属性 PASS |
| 18–20 | input_hash 与 envelope 相同；canonical hash 重算；used IDs 精确等于引用集合 |
| 21–24 | UNCONFIRMED/PENDING；无最终结论；不使用 EvidenceBuilder 最终分析路径 |
| 25–28 | 仅 injected fake 调用；数据库写入、Provider 调用、Runner raw 读取为零 |
| 29–30 | #009 累计十个文件；本次返修仅七个允许文件；新增行及凭据值模式扫描 PASS |

Review B 返修只修改 `analysis/output_contracts.py / analysis/output_validator.py /
analysis/analysis_evidence.py / tests/test_analysis_output.py / tests/test_analysis_runner.py /
tests/test_analysis_evidence.py / docs/ANALYSIS_RUNNER_009.md`。
`git diff 1b842b3..HEAD --name-only` 包含原 #009 已有的十个文件，其中
`analysis/__init__.py / analysis/model_executor.py / analysis/runner.py` 在返修起点 `b278dce` 已存在差异。
这三文件本次未修改；返修白名单应通过 `git diff b278dce..HEAD --name-only` 核查。
未撤销原 #009 实现，未改 base，未合并或进入 #010。

没有改 package_builder、prompt_builder、Normalizer、Provider、Evidence、bridge、冻结文档、schema 或 migration。
初始已有的六个未跟踪 `__pycache__` 目录保留，测试使用 PYTHONDONTWRITEBYTECODE，不把它们当作本次变更。
没有部署、调度器、Dashboard、模型 credential、第四源或自动交易。
