# Prompt 模板 V1.0（初稿）

```text
STATUS=FROZEN
VERSION=V1.0
PHASE=P3_PRODUCT_DEFINITION
DESIGN_ONLY=YES
IMPLEMENTATION=FORBIDDEN_IN_THIS_PHASE
```

依据冻结文档：`docs/freeze/DB_SCHEMA_V1.0.md`（2.13 analysis_reports、2.14 evidence_records）、
`docs/freeze/DATA_COLLECTION_SPEC_V1.0.md`（第 9 节标准分析数据包、第 10 节 Fail-closed）、
`docs/PROVIDER_DESIGN_V1.0.md`（8 条系统铁律、TRADING_WRITE=FORBIDDEN）、
`docs/DATA_NORMALIZER_DESIGN_V1.0.md`（canonical contract、UNVERIFIED 提示衔接）、
`docs/EVIDENCE_PIPELINE_DESIGN_V1.0.md`（证据 12 字段、human_confirm 分级）。
冲突处以冻结文档为准。

## 修订记录（V1.0_DRAFT → V1.1_DRAFT）

依据 GPT P3_REVIEW_RESULT（2026-10-01）：三份均为 PASS_WITH_CHANGE，无 BLOCK。
本文件 6 项修改：① `holding_position` 支持 WATCH_ONLY（无持仓纯观察，§2.1、§2.3）
② 建议输出改为三级模式，纳入 qi 选 B 拍板（§3.1、§3.2）
③ ~~`human_confirm` 增加第 4 项"数据源有效性确认"~~——qi 2026-10-01 决定不加
（每次分析都确认数据源属过度确认；数据源状态已在 Dashboard 系统健康页展示），保持三项（§4）
④ UNVERIFIED 措辞微调（§5.4） ⑤ 增加 PARTIAL_DATA_TEMPLATE（§5.5）
⑥ 8 条铁律改为版本引用（§6.1）。

---

## 1. 目标

把冻结的数据结构（canonical_data + evidence_records）转换成 ChatGPT Plus 可执行的分析模板。
模板的唯一合法输入是 `canonical_data`（铁律 `CANONICAL_ONLY_FOR_BUSINESS`）；
模板的每一条输出结论必须绑定证据（铁律 `MODEL_OUTPUT_NEEDS_EVIDENCE`）。

本阶段只定义模板，不实现 Prompt 组装代码，不调用任何模型 API。

---

## 2. Prompt 输入契约

### 2.1 固定输入（7 字段，缺一不可）

| # | 字段 | 说明 | 来源 |
|---|------|------|------|
| 1 | `stock_id` | 证券标识 | `stocks` 表 |
| 2 | `holding_position` | 持仓快照：数量、成本、当前浮盈亏（用户自有数据，非模型推断）；无持仓时为 NULL，此时 `position_mode=WATCH_ONLY` | `portfolio_positions`（P1 迁移表） |
| 3 | `canonical_market_data` | 标准化后的行情数据：`daily_kline` canonical contract（11+1 字段）与 `market_quotes` canonical contract；含 `data_quality_status` 与 `adjust_type` 口径标记 | Normalizer 输出层 |
| 4 | `financial_data` | 财务摘要（canonical 层，来源 `WORKBUDDY_TDX_MCP`） | `financial_reports` |
| 5 | `intelligence_data` | 资讯/公告/研报/估值摘要（canonical 层，来源 `WORKBUDDY_TDX_MCP`） | `news_items` / `research_reports` |
| 6 | `evidence_index` | 证据索引：本次分析输入数据的证据引用集合（`source`、`request_timestamp`、`provider_timestamp`、`validation_result`、`human_confirm`） | `evidence_records` |
| 7 | `data_quality_report` | 数据质量报告：`DATA_AVAILABILITY_REPORT`（各源 PASS / MISSING / NOT_AVAILABLE / WARNING）+ `data_quality_status` 汇总 + `REALTIME_QUOTE_STATUS` | Normalizer 校验层 + Provider 状态 |

### 2.2 输入禁令（违反即视为模板失效）

```text
禁止 1：直接粘贴 Provider 原始返回（raw_response）作为 Prompt 输入。
输入必须是 canonical_data；raw 只用于证据追溯，不进入模型上下文。
禁止 2：直接输入未验证数据（data_quality_status=FAILED 或未经 validate_contract 的数据）。
禁止 3：混入模型记忆补充事实。模型不得用训练记忆中的"公司基本面""历史事件"
填补输入缺失；缺失即缺失，按第 5 节模板显式声明。

### 2.3 分析模式分支

- `HOLDING_ANALYSIS`（`holding_position` 非空）：完整 7 字段输出，含 `position_status` 持仓评估。
- `WATCH_ANALYSIS`（`holding_position=NULL`，`position_mode=WATCH_ONLY`）：纯观察分析；
  `position_status` 输出"无持仓（观察模式）"，不评估持仓相对位置；其余 6 字段正常输出。
```

---

## 3. 分析输出契约

### 3.1 输出字段（严格对应 DB Schema 2.13，7 字段）

| 字段 | 含义 | 证据要求 |
|------|------|---------|
| `market_view` | 市场观点：趋势判断（上升/震荡/下降/不明）及依据 | 必须引用 `canonical_market_data` 证据 + 时间范围 + `data_quality_status` |
| `position_status` | 持仓状态评估：当前持仓相对趋势与成本的位置 | 必须引用 `holding_position` + 行情证据 + 时间范围 |
| `risk_level` | 风险等级：高/中/低 + 风险点清单 | 每条风险点必须有证据引用 |
| `operation_plan` | **操作建议（建议位）**：明确给出买入/卖出/持有/加仓/减仓/止损位/止盈位 | 必须引用支撑证据 + 时间范围 + 数据质量状态；无证据支撑的确定性建议禁止输出，证据不足时可输出低置信度条件建议（须满足 3.2） |
| `t_strategy` | **T 策略（建议位）**：针对被套/持仓的具体操作节奏建议（如分批减仓位、关键价位） | 同上；必须可执行、可验证 |
| `reasoning` | 推理过程：结论如何从证据得出，分步骤 | 每步标注所用证据 ID |
| `confidence` | 置信度：高/中/低 + 置信度依据说明 | 必须说明降置信的原因（如数据缺失、口径未验证） |

### 3.2 "建议位"的强制要求（三级输出模式）

`operation_plan` 与 `t_strategy` 是本模板的"建议位"，按证据充分度分三级输出
（V1.1 修订：纳入 qi 选 B 拍板，2026-10-01）：

```text
Level 1（证据充分）：输出明确的操作建议（买入/卖出/持有/加仓/减仓/止损位/止盈位），
confidence=HIGH。不许只给中性信息、不许用"可关注""或有机会"等模糊表述代替建议。
每条建议必须绑定证据（MODEL_OUTPUT_NEEDS_EVIDENCE）：
建议 → 支撑的行情/财务/资讯证据 → 证据时间范围 → 数据质量状态。

Level 2（证据有限）：不得输出确定性操作结论；允许输出低置信度、条件式建议
（如"若 A 发生，则考虑 B"），confidence=LOW，必须同时满足：
① 进入第 4 节人工确认流程（初始 PENDING；确认后 CONFIRMED，否决 REJECTED；
未确认不得标记为"最终分析结论"）；
② 显式标注证据不足（说明缺失/不足的证据项、降置信的具体原因、影响范围）；
③ 建议可执行、可验证（明确触发条件、观察指标、后续验证方式，
禁止无法验证的泛化判断）。

Level 3（证据不足）：无法形成建议，显式声明"证据不足，暂不给出操作建议"，
不许用模型记忆或推测补足。

铁律：无证据支撑的确定性建议一律禁止输出；MISSING_DATA != GENERATED_DATA。
```

### 3.3 每个字段的三要素

输出的每个字段必须同时包含：

1. **证据引用**：`evidence_id` 列表，可反查到 `evidence_records`。
2. **时间范围**：结论所依据的数据时间窗口（如"依据 2026-06-01 至 2026-09-30 日K"）。
3. **数据质量状态**：该字段所用数据的 `data_quality_status` 汇总（如"行情 PASS、实时 MISSING"）。

任一要素缺失，该字段视为"证据不完整"，按 fail-closed 处理。

---

## 4. Human-in-the-loop 设计

Prompt 必须生成以下三段式结构，缺一段即视为模板不合格：

```text
第一段：AI 建议
→ 第 3 节 7 字段完整输出（含 operation_plan / t_strategy 的明确建议）。

第二段：人工确认项
→ 逐条列出需要人工确认的事项，包括以下三项：
1. 操作建议确认（operation_plan / t_strategy 是否采纳：CONFIRMED / REJECTED；
低置信度条件式建议需单独确认是否接受）
2. 异常数据确认（data_quality_status=WARNING / UNVERIFIED 的数据是否采信；
数据不足是否影响本次分析的使用）
3. 关键假设确认（推理中依赖的、证据未完全覆盖的假设点是否认可）
→ 对应 evidence_records.human_confirm 状态机：PENDING → CONFIRMED / REJECTED。

第三段：最终状态
→ 人工确认完成后，输出"最终分析结论"快照：
采纳的建议 / 驳回的建议及原因 / 待观察事项。
→ 未经人工确认的分析，标题必须标注"未确认"，不得作为"最终结论"呈现。
```

禁令：

```text
TRADING_WRITE=FORBIDDEN
Prompt 禁止生成"自动买入""自动卖出""已下单""已委托"等表述；
禁止输出任何券商委托指令格式；禁止暗示系统会代为交易。
```

---

## 5. 缺失数据处理模板

### 5.1 通用模板句式

任何数据缺失必须按以下句式显式声明（三要素：缺什么 / 为什么 / 影响什么）：

```text

缺失项：{实时行情 / 某类财务数据 / 研报 …}
原因：{TDX_LOCAL_RUNTIME 不可用 / 源返回 NOT_AVAILABLE / …}
影响：{不影响历史趋势分析 / 本结论置信度降为"低" / 该子项暂不给出建议 …}
```

### 5.2 实时行情缺失示例（冻结口径）

```text

缺失项：实时行情
原因：TDX_LOCAL_RUNTIME 不可用（Local Runtime Dependency，仅 Windows 主机在线时可用）
影响：不影响历史趋势分析；所有涉及"当前价"的判断改用最近日K收盘价并标注日期，
不得称为实时结论。
REALTIME_QUOTE_STATUS=MISSING
```

### 5.3 绝对禁令

```text
禁止"根据历史推测当前价格"。
禁止用"按趋势外推""大致在 X 元附近"等表述替代缺失的实时数据。
禁止用模型记忆中的公司信息填补缺失的公告/财务/研报。
MISSING_DATA!= GENERATED_DATA（全系统铁律，见 PROVIDER_DESIGN_V1.0 第 10.3 节）。
```

### 5.4 UNVERIFIED 数据提示文案（承接 Normalizer R4）

```text

当前数据口径未完成验证（UNVERIFIED：Provider 未声明或未经验证），
相关计算结果存在口径风险。涉及涨跌幅、均线等计算的结论仅供参考，
跨口径比较已禁用。如需精确复权口径，请人工确认数据源声明后再行分析。
```

### 5.5 PARTIAL_DATA_TEMPLATE（部分数据缺失）

部分日期/部分字段缺失时，按四要素声明：

```text

缺失范围：{缺失的交易日区间 / 缺失的字段}
原因：{源返回 PARTIAL / 采集中断 / …}
影响：{影响哪些结论 / 置信度变化}
处理：{缺失片段记 MISSING，不插值；PARTIAL_REFETCH=MANUAL_TRIGGER_ONLY}
```

### 5.6 数据缺失不等于禁止分析

缺失项必须显式声明（第 5.1—5.5 节），但不得隐藏缺失项、不得将推测表达为事实；
在缺失已声明的前提下，其余有证据支撑的部分正常分析（fail-closed 针对的是
"完整分析"的认定，不是禁止一切输出）。

---

## 6. 完整 Prompt 模板示例

> 以下为模板结构示例。所有数据均为**脱敏示意**，不对应任何真实持仓与真实行情。

### 6.1 System Prompt（系统提示词）

```text
你是"AI 股票系统"的分析引擎，运行在 Human-in-the-loop 模式下。


- 你只做分析，不做交易。TRADING_WRITE=FORBIDDEN：你不得下单、撤单、
生成券商委托指令，不得使用"自动买入/卖出"等表述。
- 你的唯一合法输入是 canonical_data（已标准化、已校验的数据）。
不得直接读取 Provider 原始返回，不得用模型记忆补充事实。


- 遵守 SYSTEM_RULES_V1.0（8 条系统铁律，见 PROVIDER_DESIGN_V1.0），
特别注意：NO_IMPLICIT_FALLBACK（缺失即声明缺失，不得隐式切换数据源）、
NO_FOURTH_PROVIDER（数据源只有三源）、MISSING_DATA != GENERATED_DATA
（不得插值、外推、猜测填补；禁止"根据历史推测当前价格"）、
MODEL_OUTPUT_NEEDS_EVIDENCE（每条结论必须绑定 evidence_id）。


- 严格按 7 字段输出：market_view / position_status / risk_level /
operation_plan / t_strategy / reasoning / confidence。
- 每个字段必须包含：证据引用（evidence_id 列表）、时间范围、数据质量状态。
- operation_plan 与 t_strategy 必须给出明确的操作建议
（买入/卖出/持有/加仓/减仓/止损位/止盈位），不许只给中性信息；
无证据支撑时显式声明"证据不足，暂不给出操作建议"。
- 输出后必须生成"人工确认项"清单；未经确认的分析标注"未确认"。


缺失项、原因、影响三要素，格式见用户输入中的 data_quality_report 要求。
```

### 6.2 User Prompt（用户提示词结构）

```text

- stock_id：STOCK_EXAMPLE_A（示意）
- task_date：2026-09-30（示意）
- prompt_version：PROMPT_TEMPLATE_V1.0_DRAFT
- model_version：{由分析任务记录，示意：MODEL_X}

（示意数据）
- 持仓数量：10000 股（示意）
- 持仓成本：10.00 元（示意）
- 当前浮盈亏：-8.0%（示意）
（WATCH_ONLY 模式：持仓段为"无持仓（观察模式）"，position_status 相应输出）

（示意数据）
- daily_kline：STOCK_EXAMPLE_A 2026-06-01 至 2026-09-30（示意区间），
adjust_type=UNVERIFIED，data_quality_status=PASS
- market_quotes：REALTIME_QUOTE_STATUS=MISSING（TDX_LOCAL_RUNTIME 不可用）

（示意）
- 来源 WORKBUDDY_TDX_MCP，最新报告期 2026-06-30（示意），data_quality_status=PASS

（示意）
- 近 30 天公告 2 条（示意），研报 0 条（NOT_AVAILABLE）

（示意）
- evidence_id 列表：EV-001 … EV-00N（示意）

（示意）
- TUSHARE_MCP：PASS；TDX_OFFICIAL_LOCAL_HTTP：MISSING；
WORKBUDDY_TDX_MCP：PASS（研报类 NOT_AVAILABLE）
- REALTIME_QUOTE_STATUS=MISSING


按 system prompt 的 7 字段输出，并附"人工确认项"清单。
```

### 6.3 期望输出结构（示意框架，不填真实结论）

```text
market_view：{趋势判断}｜证据：[EV-xxx]｜时间范围：{…}｜质量：{…}
position_status：{持仓评估}｜证据：[EV-xxx]｜时间范围：{…}｜质量：{…}
risk_level：{高/中/低}｜风险点：[{…}]｜证据：[EV-xxx]｜…
operation_plan：{明确操作建议}｜证据：[EV-xxx]｜时间范围：{…}｜质量：{…}
t_strategy：{T 策略建议}｜证据：[EV-xxx]｜…
reasoning：{分步推理，每步标注证据}
confidence：{高/中/低}｜降置信原因：{…}

（如有，按第 5 节句式）
（如 adjust_type=UNVERIFIED，按第 5.4 节文案）


1. 操作建议确认：□ CONFIRMED □ REJECTED（确认人：____）
2. 异常数据确认：□ 采信 □ 不采信（确认人：____）
3. 关键假设确认：□ 确认 □ 驳回（确认人：____)

（人工确认后填写）
```

---

## 7. 模板版本与变更管理

- `prompt_version` 随模板冻结版本记录于 `analysis_tasks.prompt_version`。
- 模板的任何修改必须走"冻结修订"流程（用户明确解冻/升级版本），不得擅自改模板后声称仍是 V1.0。
- 每次分析必须记录实际使用的 `prompt_version` + `model_version`，供证据链追溯（`EVIDENCE_PIPELINE_DESIGN_V1.0` 第 3 节字段 11）。

---

## 8. 评审结论（P3_REVIEW_RESULT，2026-10-01）

结论：PASS_WITH_CHANGE（GPT 评审，无 BLOCK）。
6 项修改已纳入本 V1.1：① `holding_position` 支持 WATCH_ONLY（§2.1、§2.3、§6.2）
② 三级输出模式，纳入 qi 选 B 拍板（§3.1、§3.2）
③ ~~`human_confirm` 增加第 4 项~~——qi 决定不加，保持三项（§4）
④ UNVERIFIED 措辞微调（§5.4） ⑤ 增加 PARTIAL_DATA_TEMPLATE（§5.5）
⑥ 8 条铁律改为版本引用（§6.1）。
