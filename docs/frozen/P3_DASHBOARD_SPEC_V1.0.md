# Dashboard 功能说明 V1.0（初稿）

```text
STATUS=FROZEN
VERSION=V1.0
PHASE=P3_PRODUCT_DEFINITION
DESIGN_ONLY=YES
IMPLEMENTATION=FORBIDDEN_IN_THIS_PHASE
```

依据冻结文档：`docs/freeze/DB_SCHEMA_V1.0.md`（2.13 analysis_reports、2.14 evidence_records、2.11/2.12/2.15 任务表）、
`docs/PROVIDER_DESIGN_V1.0.md`（8 条系统铁律、DATA_AVAILABILITY_REPORT、TDX Local Runtime Dependency）、
`docs/DATA_NORMALIZER_DESIGN_V1.0.md`（CANONICAL_ONLY_FOR_BUSINESS、data_quality_status）、
`docs/EVIDENCE_PIPELINE_DESIGN_V1.0.md`（追溯/复核/人工确认/审计四能力）。
冲突处以冻结文档为准。

## 修订记录（V1.0_DRAFT → V1.1_DRAFT）

依据 GPT P3_REVIEW_RESULT（2026-10-01）：PASS_WITH_CHANGE，无 BLOCK；四页面结构 PASS，保留不合并。
本文件 5 项修改：① 示例允许真实股票代码（如 601518），仍禁止真实数量/成本/盈亏/账户信息（§2.6）
② Evidence 页与系统健康页保持分离（§3、§4）
③ 增加 Human Confirmation Center 逻辑概念，非新增页面（§1.4、§2.4、§3.4）
④ K 线缩放/平移保留，限轻量实现（§2.4） ⑤ 总览页增加系统健康摘要入口（§1.2）。

---

## 0. 产品定位（写死）

```text
本 Dashboard 是"被套持仓辅助分析 Dashboard"，不是交易终端。
```

- 用途：帮助用户看清持仓处境（趋势、风险、操作建议、证据），辅助人工决策。
- 禁止：无下单入口、无委托按钮、无券商账号接入、无"一键跟随建议"式交互
 （`TRADING_WRITE=FORBIDDEN`，见 `PROVIDER_DESIGN_V1.0` 第 12 节）。
- 数据铁律：所有页面只读 `canonical_data` 与 `evidence_records`
 （`CANONICAL_ONLY_FOR_BUSINESS`）；禁止展示未经校验的原始返回。

---

## 1. 页面 1：总览

### 1.1 用途

一屏回答四个问题：我有哪些股票、持仓什么状态、数据健不健康、有没有需要人工确认的事项。

### 1.2 展示元素

| 区域 | 元素 | 说明 |
|------|------|------|
| 股票池 | 股票列表（`stock_id` + 名称示意） | 来自 `stocks` 表；点击进入页面 2 |
| 持仓 | 每只持仓股：数量、成本、最新价（标注日期）、浮盈亏金额/比例 | 来自 `portfolio_positions` + `canonical_market_data`；最新价若非实时必须标注日期 |
| 当前状态 | 每只股票的风险等级徽标（高/中/低）与趋势方向 | 来自最近一次 `analysis_reports`（`risk_level` / `market_view`） |
| 数据健康 | 三源状态灯：`TUSHARE_MCP` / `TDX_OFFICIAL_LOCAL_HTTP` / `WORKBUDDY_TDX_MCP`（PASS / MISSING / UNAVAILABLE / UNKNOWN） | 来自 `DATA_AVAILABILITY_REPORT`；缺失必须显式展示，禁止隐藏 |
| 系统健康摘要 | 磁盘占用状态（正常/预警） | 点击进入页面 4；磁盘占用超 5% 时红色预警 |
| 待确认 | `human_confirm=PENDING` 的分析结论/操作建议数量与入口 | 来自 `evidence_records` / `analysis_reports` |

### 1.3 数据来源

`stocks`、`portfolio_positions`（P1 迁移表）、`analysis_reports`（最新一条/股票）、
`evidence_records`（`human_confirm` 状态汇总）、Provider 健康状态（`health_check` 结果）。

### 1.4 交互说明

- 点击股票 → 跳转页面 2（个股分析页）。
- 点击三源状态灯 → 跳转页面 4（系统健康）。
- 点击"待确认" → 进入 Human Confirmation Center（逻辑概念，非独立页面）：
  按待确认事项类型分别定位到页面 2（操作建议确认）或页面 3（证据确认）。
- 刷新为手动触发（按钮），不做高频自动轮询（轻量约束：`NO_HIGH_FREQUENCY_LOOP`）。

### 1.5 异常状态展示

- 任一源 MISSING/UNKNOWN：状态灯红色/灰色 + 文字说明原因（如"本地终端离线"），不显示为系统故障。
- 无持仓数据：持仓区显示"暂无持仓数据（不可用，非零持仓）"，不得显示为"空仓"。
- 分析报告缺失：显示"尚未生成分析"，不伪造结论。

---

## 2. 页面 2：个股分析页

### 2.1 用途

单只股票的"一页纸诊断"：行情、持仓、AI 分析一次看完，所有结论可下钻到证据。

### 2.2 展示元素

**A. 行情区**

| 元素 | 说明 |
|------|------|
| 最新日K | `daily_kline` canonical contract：open/high/low/close/volume/amount（单位已统一） |
| 趋势 | 均线（示意：MA5/MA20，口径未验证时标注 UNVERIFIED）、趋势方向 |
| 量价 | 成交量柱状（单位：股）、量价关系文字摘要 |
| 实时行情位 | `market_quotes`：有则显示（标注 `quote_timestamp`）；无则按第 2.5 节展示 MISSING |

**B. 持仓区**

| 元素 | 说明 |
|------|------|
| 成本 / 数量 / 浮盈亏 | 来自 `holding_position`；浮盈亏同时显示金额与比例 |
| 持仓状态评估 | `position_status` 字段全文 |

**C. AI 分析区**（对应 `analysis_reports` 7 字段）

`market_view` / `position_status` / `risk_level` / `operation_plan` / `t_strategy` /
`reasoning` / `confidence` 逐字段展示；每个字段旁显示证据引用数、时间范围、
`data_quality_status` 徽标；点击任一字段 → 跳转页面 3 对应证据链。

`operation_plan` / `t_strategy`（建议位）必须**明确展示操作建议**
（买入/卖出/持有/加减仓/止损止盈位），不得只展示中性描述；
建议下方必须展示人工确认状态（`PENDING` / `CONFIRMED` / `REJECTED`）。

### 2.3 数据来源

`canonical_market_data`（`daily_kline` + `market_quotes`）、`portfolio_positions`、
`analysis_reports`（最新一条）、`evidence_records`（字段级证据引用）。

### 2.4 交互说明

- K 线支持缩放/平移（只读展示，限轻量实现，禁止引入复杂图表库，轻量约束）。
- 点击 AI 分析任一字段 → 页面 3，定位到该字段的证据链。
- "人工确认"按钮：仅在 `human_confirm=PENDING` 时可用；点击后进入确认流程
 （确认/驳回 + 确认人记录），对应 `EVIDENCE_PIPELINE_DESIGN_V1.0` 第 7.3 节状态机。
 该按钮是 Human Confirmation Center 的入口之一（另两个入口：总览页"待确认"、
 Evidence 页证据节点确认入口）。
- 无"跟随建议下单"类按钮（`TRADING_WRITE=FORBIDDEN`）。

### 2.5 异常状态展示

- 实时行情 MISSING：行情区显示"实时行情 MISSING（TDX 本地终端离线），
  以下为最近日K收盘价（日期：____），不作为实时结论"。
- `adjust_type=UNVERIFIED`：趋势区顶部显示口径未验证提示条（文案见 Prompt 模板第 5.4 节）。
- `data_quality_status=WARNING`：对应字段显示黄色徽标 + 人工复核提示。
- 分析报告为"未确认"：页面顶部横幅"本页分析尚未人工确认"，`operation_plan` 置灰并标注。

### 2.6 示例数据说明

本页示例允许使用真实股票代码做结构示例（如 `STOCK_EXAMPLE_A（601518）`），
但**不得写入任何真实持仓信息**（数量、成本、浮盈亏、账户信息均不得为真值；
数量/成本/盈亏一律用 `QTY_EXAMPLE` / `COST_EXAMPLE` 占位）。

---

## 3. 页面 3：Evidence 页面（证据追溯页）

### 3.1 用途

回答"这个结论从哪来"：从任一分析结论出发，逐级下钻到原始数据与原始文件。

### 3.2 追溯链展示

按 `EVIDENCE_PIPELINE_DESIGN_V1.0` 第 2 节证据链模型渲染为可点击的纵向链条：

```text
分析结论（analysis_report_id，7 字段）
  ↓ 点击展开
结构化字段明细（market_view / … / confidence）
  ↓ 点击展开
模型版本（model_version）+ Prompt 版本（prompt_version）
  ↓ 点击展开
计算过程（calculation：指标公式、参数）
  ↓ 点击展开
原始数据（raw_data 摘要；标准化结果明确标注非原始）
  ↓ 点击展开
数据源（source：三源之一）+ 获取时间（request_timestamp / provider_timestamp）
  ↓ 点击展开
原始文件（raw_response_path：三级目录 system_run_id/collection_task_id/request_id）
  + 完整性 Hash（raw_response_hash / normalized_data_hash / analysis_output_hash）
```

### 3.3 数据来源

`evidence_records` 全字段（含 metadata sidecar）、`analysis_reports`、`analysis_tasks`
（`prompt_version` / `model_version`）、原始响应存储（只读）。

### 3.4 交互说明

- 支持从页面 1/2 的"证据引用数""待确认"入口 deep-link 定位到具体证据节点。
- 每个节点显示 `human_confirm` 状态；`REJECTED` 节点红色标记但保留可查（审计要求）。
- Hash 校验入口：点击可触发"重新计算并比对"（只读动作），不一致记为证据异常事件展示。
- 追溯查询全程只读；不提供任何证据的新增/修改/删除入口
 （`EVIDENCE_IMMUTABLE`：UPDATE/DELETE evidence_records = forbidden）。
- Human Confirmation Center 入口之三：证据节点的确认操作入口
 （`human_confirm` 为 PENDING 的节点的确认/驳回，审计保留）。

### 3.5 异常状态展示

- 证据链任一环节缺失：该节点显示"证据缺失"，整条结论标注"证据不完整"
  （fail-closed，不展示为"完整分析"）。
- Hash 不一致：红色告警"完整性校验失败"，该证据不可作为确认依据。
- 原始文件已归档（超 90 天压缩）：显示"已归档"，仍可验证 Hash 与元数据。

---

## 4. 页面 4：系统健康

### 4.1 用途

回答"数据底座健不健康"：三源状态、任务状态、配额状态一目了然；缺失必须显式展示。

### 4.2 展示元素

| 区域 | 元素 | 说明 |
|------|------|------|
| 三源状态 | `TUSHARE_MCP` / `TDX_OFFICIAL_LOCAL_HTTP` / `WORKBUDDY_TDX_MCP`：状态（PASS / MISSING / NOT_AVAILABLE / UNKNOWN / DISABLED）、最近 `health_check` 时间、连续失败次数 | 探针定义见 `PROVIDER_DESIGN_V1.0` 第 3.1 节；只验证通道，不拉业务数据 |
| TDX 说明 | `TDX_LOCAL_DEPLOYMENT=WINDOWS_ONLY_VALIDATED` / `AWS_REALTIME_VALIDATION=NOT_DONE` 状态说明 | 云端未验证显示"未验证"，不显示为故障 |
| 采集任务 | `collection_tasks` 最近 N 次运行：任务类型、状态、耗时、错误摘要 | 只读展示 |
| 系统运行 | `system_runs` 当前/最近运行状态 | 只读展示 |
| 磁盘配额 | 原始响应存储占用 vs 5% 上限；压缩归档状态 | 来自 `EVIDENCE_PIPELINE_DESIGN_V1.0` 第 6 节 |

### 4.3 数据来源

Provider `health_check` 结果、`collection_tasks`、`system_runs`、磁盘用量统计（只读）。

### 4.4 交互说明

- "立即检测"按钮：手动触发三源 `health_check`（轻量探针，≤5s，不拉业务数据）。
- 点击失败任务 → 显示错误对象（`code` / `message` / `retryable`），不做自动重试入口
  （重试是 P6 调度逻辑，Dashboard 只展示）。
- 不提供"禁用/启用数据源"开关（`SOURCE_IDENTITY_LOCKED`；数据源启停走配置与人工流程，不在 Dashboard 操作）。

### 4.5 异常状态展示（核心要求）

```text
禁止隐藏缺失。
```

- `TDX_OFFICIAL_LOCAL_HTTP` 不可用：显示"本地终端离线 / 未验证（UNKNOWN）"，
  不得显示为"系统故障"，不得显示为 ERROR。
- `WORKBUDDY_TDX_MCP` 某类数据为空：显示"该类官方数据为空（NOT_AVAILABLE）"，
  不得用占位数据填充。
- 磁盘占用超 5%：红色告警 + "已触发压缩归档"状态。
- 所有异常状态必须同时出现在页面 1 的数据健康区（`DATA_AVAILABILITY_REPORT` 一致性）。

---

## 5. 全局设计约束

- 只读展示：Dashboard 不写数据库（`DATABASE_WRITE=NO`，P3 阶段要求）；
  唯一允许的写动作是人工确认状态流转（`human_confirm`），且走独立确认流程。
- 无交易能力：全站无下单入口（`TRADING_WRITE=FORBIDDEN`）。
- 轻量：`NO_KAFKA` / `NO_REDIS` / `NO_CELERY`；手动刷新优先，不做高频轮询。
- 脱敏：所有示例、截图、演示数据一律使用示意占位，不得出现真实持仓。
- 冲突时以冻结文档（`docs/freeze/`）与 P2 V1.0 冻结设计为准。

---

## 6. 评审结论（P3_REVIEW_RESULT，2026-10-01）

结论：PASS_WITH_CHANGE（GPT 评审，无 BLOCK）。四页面结构 PASS，保留、不合并。
5 项修改已纳入本 V1.1：① 示例允许真实股票代码（§2.6）
② Evidence 页与系统健康页保持分离（§3、§4）
③ Human Confirmation Center 逻辑概念（§1.4、§2.4、§3.4）
④ K 线交互轻量保留（§2.4） ⑤ 总览页系统健康摘要（§1.2）。
qi 对四页产品调性的确认待补；确认后进入 V1.1 汇总冻结。
