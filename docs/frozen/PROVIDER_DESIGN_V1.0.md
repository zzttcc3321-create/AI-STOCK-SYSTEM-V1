# Provider 设计 V1.0

```text
STATUS=FROZEN
VERSION=V1.0
FROZEN_DATE=2026-09-30
PHASE=P2_PROVIDER_DESIGN
DESIGN_ONLY=YES
IMPLEMENTATION=FORBIDDEN_IN_THIS_PHASE
REVISION_BASIS=GPT评审 APPROVED_WITH_MINOR_FIX（18 个开放问题逐条批复，12 条文档修订）
              + 反向验证（8 条系统铁律 + 加固项）；经 qi 确认采用方案 A 一次并入冻结
```

依据冻结基线：`docs/freeze/DB_SCHEMA_V1.0.md`、`docs/freeze/DATA_COLLECTION_SPEC_V1.0.md`
（`FROZEN_DOC_SYNC_POLICY_V1`：冻结文档只读，本设计不得与其冲突；冲突处以冻结文档为准并上报。）

---

## 0. DRAFT → V1.0 修订日志

| # | 修订来源 | 改动 |
|---|---------|------|
| R1 | GPT 评审 Q1 | 第 5 节增加 `P6_CALIBRATION_RULE`：每个 Provider 至少采样 30 次成功请求，覆盖工作日交易时段 / 非交易时段 / 网络正常 / 异常状态，统计 p50 / p95 / timeout_rate 后调整超时初值 |
| R2 | GPT 评审 Q2 | 第 6 节增加 `PARTIAL_REFETCH=MANUAL_TRIGGER_ONLY`：PARTIAL 数据禁止自动补拉，只能人工或明确任务重新发起 |
| R3 | GPT 评审 Q3 | 第 3.1 节写死三源 `health_check` 探针定义：只验证连接 / 授权 / HTTP 健康，禁止拉取业务数据作为 health_check |
| R4 | GPT 评审 Q4 | TDX 云端 `health_check` 返回格式与本地统一（仅状态不同）；云端未验证前状态为 `UNKNOWN` / `UNAVAILABLE`，不是 `ERROR` |
| R5 | GPT 评审 Q5 | 输出 envelope 字段名冻结：`ENVELOPE_FIELD_NAMES=FROZEN`，P6 不得改名 |
| R6 | GPT 评审 Q6 | 新增错误码 `INVALID_DATA_KIND_SCOPE`（跨职责请求拒绝），不复用 `VALIDATION_FAILED` |
| R7 | 反向验证 | 第 1 节增加数据所有权表与统一边界图（数据源 → Provider → Normalizer → Canonical Data → Analysis） |
| R8 | 反向验证 | 第 11 节增加 TDX 运行状态模型：`TDX_LOCAL_DEPLOYMENT=WINDOWS_ONLY_VALIDATED`、`AWS_REALTIME_VALIDATION=NOT_DONE` |
| R9 | 反向验证 | 第 3.4 节错误状态机由 7 种扩展到 9 种（新增 `DISABLED`、`UNKNOWN`） |
| R10 | 反向验证 | 第 3.1 节增加内部方法 `validate_contract()`；数据流明确为 fetch → contract validate → normalize |
| R11 | 反向验证 | 第 10.4 节新增 8 条系统铁律专章（本文件为主要载体） |
| R12 | 反向验证 | 第 9 节增加 `DATA_AVAILABILITY_REPORT` 要求：任何 Provider 失败必须在分析结果中显式可见 |

---

## 1. 设计目标与范围

### 1.1 目标

为三个正式数据源定义统一的 Provider 接口契约：输入参数、输出契约、错误状态、超时策略、重试策略、时间戳规范、来源记录规范、不可用时行为。P2 只做设计，不进入编码。

### 1.2 非目标（本阶段明确不做）

- 不编写任何 Provider 实现代码。
- 不调用 Tushare MCP / TDX 本地 HTTP / WorkBuddy TDX MCP（无真实调用）。
- 不修改数据库（含 `data_sources` 表在内的任何 DDL/DML）。
- 不创建 API、不修改 Scheduler、不启动任何服务。

### 1.3 数据所有权（V1.0 新增）

P1 迁移暴露的最大问题是"不知道数据到底从哪里来"。本设计首先冻结数据所有权：

| 数据 | 唯一事实来源 |
|------|-------------|
| 历史日K | `TUSHARE_MCP` |
| 实时行情 | `TDX_OFFICIAL_LOCAL_HTTP` |
| 财务 / 资讯 / 研报 / 估值 | `WORKBUDDY_TDX_MCP` |
| 历史迁移数据（P1 已迁移） | Windows SQLite（唯一事实源；AWS 为单向受控迁移副本） |

三份 P2 文档（Provider / Normalizer / Evidence）统一使用以下边界图：

```text
数据源
  ↓
Provider（只获取、只返回原始数据、只标注状态）
  ↓
Normalizer（只转换、不推理）
  ↓
Canonical Data（系统唯一业务入口）
  ↓
Analysis（分析任务 / Dashboard / 分析数据包）
```

---

## 2. 正式三源与职责（写死）

| # | name | role | 职责 |
|---|------|------|------|
| 1 | `TUSHARE_MCP` | `historical_daily` | 历史日K正式主源：历史日K、历史行情、复权相关历史数据 |
| 2 | `TDX_OFFICIAL_LOCAL_HTTP` | `realtime_quote` | 实时行情增强源：只读实时快照（Local Runtime Dependency，见第 11 节） |
| 3 | `WORKBUDDY_TDX_MCP` | `information_fundamental` | 资讯/F10/财务/研报/估值源：公告、新闻、F10、财务、研报、估值、行业/板块及资金上下文 |

```text
ACTIVE_DATA_SOURCE_COUNT_MAX=3
```

三源职责与 `data_sources` 表初始记录一致，设计阶段不许增删改。

---

## 3. Provider 统一接口契约

所有 Provider 实现（P6 阶段）必须实现以下逻辑契约。本节是接口定义，不是代码。

### 3.1 逻辑方法

| 方法 | 说明 |
|------|------|
| `fetch(request)` | 执行一次只读数据获取，返回统一输出 envelope |
| `validate_contract()` | （内部方法，V1.0 新增）对 `fetch` 返回做契约校验；不通过不得进入 Normalizer |
| `health_check()` | 轻量可用性探针，定义见下；不拉取业务数据，只验证通道可达与授权有效 |
| `describe()` | 返回本 Provider 的 `name` / `role` / `priority` / 能力声明（只读） |

数据流（V1.0 冻结）：

```text
fetch → contract validate → normalize
```

`health_check` 三源探针定义（写死，V1.0 新增）：

| Provider | 探针内容 | 禁止事项 |
|----------|---------|---------|
| `TUSHARE_MCP` | 仅验证 MCP 连接与授权状态 | 禁止拉取业务数据 |
| `TDX_OFFICIAL_LOCAL_HTTP` | 验证 `127.0.0.1:17709` HTTP 健康 | 禁止拉取业务数据 |
| `WORKBUDDY_TDX_MCP` | 验证 MCP endpoint / auth 状态 | 禁止拉取业务数据 |

`health_check` 超时一律 ≤5s。

### 3.2 输入参数（`fetch` 请求契约）

| 参数 | 必填 | 说明 |
|------|------|------|
| `stock_id` | 是 | 证券标识（与 `stocks` 表主键语义一致，格式如 `600000.SH`，此处为示意） |
| `data_kind` | 是 | 数据类别：`daily_kline` / `realtime_quote` / `announcement` / `financial` / `news` / `research` / `valuation` |
| `date_from` / `date_to` | 按需 | 日期范围（`daily_kline` 必填；`realtime_quote` 不适用） |
| `request_id` | 是 | 调用方生成的唯一请求 ID，用于幂等与追踪 |
| `collection_task_id` | 是 | 关联的采集任务 ID（`collection_tasks.id`） |
| `system_run_id` | 是 | 关联的系统运行 ID（`system_runs.id`） |

约束：`data_kind` 必须落在该 Provider 职责范围内；跨职责请求必须在接口层拒绝，
错误码为 `INVALID_DATA_KIND_SCOPE`（V1.0 新增，不复用 `VALIDATION_FAILED`），不许"顺手"去调别的源。

### 3.3 输出契约（统一 envelope）

每次 `fetch` 返回以下结构（字段名为逻辑契约，P6 落到具体实现时保持语义一致）：

| 字段 | 必填 | 说明 |
|------|------|------|
| `status` | 是 | 9 种状态之一（见 3.4） |
| `data` | 按需 | 业务数据载荷；`status != OK/PARTIAL` 时必须为 null，不许用占位数据填充 |
| `provider` | 是 | Provider 名称（`data_sources.name` 之一） |
| `provider_timestamp` | 按需 | 数据方提供的数据时间戳；缺失则为 null 并标记 freshness 未知 |
| `request_timestamp` | 是 | 本次请求的发起时间（UTC+8，ISO8601） |
| `raw_response_ref` | 是 | 原始响应的存储引用（路径或句柄），`status=OK/PARTIAL` 时必填 |
| `error` | 按需 | 错误对象：`code` / `message` / `retryable`；成功时为 null |

```text
ENVELOPE_FIELD_NAMES=FROZEN
```

envelope 字段名已在 P2 冻结，P6 不得改名（后续 Evidence / Normalizer / Dashboard 均依赖）。

### 3.4 错误状态枚举（9 种，V1.0）

| 状态 | 含义 | 是否可重试 | 后续行为 |
|------|------|-----------|---------|
| `OK` | 成功，数据完整 | — | 经 `validate_contract()` 后进入标准化层 |
| `PARTIAL` | 部分成功（部分字段/日期缺失） | 否 | 缺失部分标记 `MISSING`，不补齐；补拉仅允许人工触发（见 6 节） |
| `MISSING` | 源明确返回无数据 | 否 | 记录缺失，不重试，不切换源 |
| `NOT_AVAILABLE` | 该类别官方数据为空（如无研报） | 否 | 记录 `NOT_AVAILABLE`，不补填 |
| `TIMEOUT` | 超时 | 是 | 按第 6 节重试，重试耗尽记失败 |
| `AUTH_FAILED` | 授权失败 | 否 | 立即失败，上报，不重试 |
| `VALIDATION_FAILED` | 返回数据未通过契约校验 | 否 | 数据不得入库，记录证据后失败 |
| `DISABLED` | Provider 被人工/配置禁用 | 否 | 不发起调用，直接记录禁用状态 |
| `UNKNOWN` | 状态未知（如 TDX 云端未验证） | 否 | 按不可用处理，但必须与"故障"区分记录 |

---

## 4. 分 Provider 职责契约

### 4.1 TushareMCPProvider（`TUSHARE_MCP`）

- 职责：`daily_kline_job` 的唯一正式主源。
- 输入：`data_kind=daily_kline`，`stock_id`，`date_from`/`date_to`。
- 输出：历史日K序列，进入校验（日期完整性、字段完整性、复权口径、异常值）后才可进入标准化层。
- 历史日K缺失：仅在**已验证的 Windows 本地 TDX 可用**且其数据通过同样校验时，可使用该官方备用（它属于三源之一，不是第四源）；云端不得依赖该备用；备用不可用则明确记录缺失，不隐式切换、不伪造完整日K。
- 不可用时：`daily_kline_job` 失败 → FAIL-CLOSED（见第 9 节）。

### 4.2 TdxLocalHttpProvider（`TDX_OFFICIAL_LOCAL_HTTP`）

- 职责：`quote_job`，只读实时快照。
- 输入：`data_kind=realtime_quote`，`stock_id`。
- 约束：Local Runtime Dependency（见第 11 节）。云端未验证前，`health_check` 在云端必须返回 `UNKNOWN` / `UNAVAILABLE`，不许伪装可用。
- 不可用时：`REALTIME_QUOTE_STATUS=MISSING`，任务继续，不阻塞分析（见第 9 节）。

### 4.3 WorkBuddyTdxMcpProvider（`WORKBUDDY_TDX_MCP`）

- 职责：`fundamental_job`，公告、新闻、F10、财务、研报、估值、行业/板块及资金上下文。
- 某类官方数据为空：记录 `NOT_AVAILABLE` 或 `MISSING`，不得用模型记忆补填。
- 云端独立访问能力须在实施阶段验证；未验证前不得报告云端能力 PASS。

---

## 5. 超时策略

| Provider | 连接超时（初值） | 读取超时（初值） | 说明 |
|----------|---------------|---------------|------|
| `TUSHARE_MCP` | 10s | 30s | 远程 MCP，初值待实测校准 |
| `TDX_OFFICIAL_LOCAL_HTTP` | 3s | 5s | 本地回环，超时即判不可用 |
| `WORKBUDDY_TDX_MCP` | 10s | 30s | 远程 MCP，初值待实测校准 |

- 超时值是设计初值，P6 实施前必须用实测校准并记录。
- `health_check` 超时一律 ≤5s，不拉取业务数据。
- 超时不许用"延长等待"代替失败判定；超时即按 `TIMEOUT` 处理。

`P6_CALIBRATION_RULE`（V1.0 新增）：每个 Provider 在 P6 实施前至少采样 30 次成功请求，
覆盖：工作日交易时段 / 非交易时段 / 网络正常 / 网络异常状态；统计 `p50` / `p95` / `timeout_rate`，
根据结果调整超时初值并记录校准结论。

---

## 6. 重试策略

- 仅对幂等读操作（`fetch` 全部为只读）允许重试。
- 上限：最多重试 2 次（含初次共 3 次尝试）。
- 退避：指数退避，间隔 1s、3s；不许高频轮询。
- 不重试：`AUTH_FAILED`、`VALIDATION_FAILED`、`INVALID_DATA_KIND_SCOPE`、`MISSING`、`NOT_AVAILABLE`、`DISABLED`、`UNKNOWN`。
- 重试耗尽仍失败：记任务 `FAILED`，写错误与 heartbeat，不降级、不切换源。

```text
PARTIAL_REFETCH=MANUAL_TRIGGER_ONLY
```

`PARTIAL` 状态的缺失片段禁止自动补拉；补拉只能由人工或明确的任务重新发起（防止数据时间不一致、证据链复杂化、2GB 环境调度复杂度上升）。

---

## 7. 数据时间戳规范

- `provider_timestamp`：数据方声称的数据产生时间；缺失则为 null，并在输出中标记 `freshness=UNKNOWN`。
- `request_timestamp`：调用方发起时间，每次必填。
- 两者分别记录，互不替代。若提供方未返回行情时间戳，**不能**仅凭请求时间判定数据新鲜。
- `market_quotes.provider_timestamp` 缺失时如实存空（冻结要求），实时性标记为未知；历史收盘价不得冒充实时价格。

---

## 8. 来源记录规范

每次 `fetch`（无论成功失败）必须可追溯：

```text
provider / endpoint_or_tool / request_params / request_timestamp /
provider_timestamp / raw_response_ref / collection_task_id / system_run_id
```

- 成功/部分成功：`raw_response_ref` 必填，指向原始响应存储（见证据链设计）。
- 失败：记录错误对象与 `validation_result`（如有），不许只记日志不落任务表。
- 来源记录是证据链的上游输入，字段口径与 `EVIDENCE_PIPELINE_DESIGN_V1.0` 的字段对齐。

---

## 9. 不可用时行为矩阵

| 任务 | 主源不可用 | 行为 |
|------|-----------|------|
| `daily_kline_job` | `TUSHARE_MCP` 失败且官方备用不可用 | FAIL-CLOSED：禁止输出"完整分析"，记录明确失败/不完整状态 |
| `quote_job` | 本地 TDX 不在线 | `REALTIME_QUOTE_STATUS=MISSING`，其余分析继续，不得声称有实时价格 |
| `fundamental_job` | 某类数据为空 | 该类别记 `NOT_AVAILABLE`/`MISSING`，不补填，其余继续 |

通用原则：缺失是显式状态，不是异常分支；任何降级必须显式标记，不许静默。

`DATA_AVAILABILITY_REPORT`（V1.0 新增）：任何 Provider 失败必须在分析结果中显式可见，
例如某证券分析的数据可用性报告：日K `PASS` / 实时 `MISSING` / 财务 `PASS` / 资讯 `NOT_AVAILABLE`
（证券代码为示意占位，不使用真实持仓）。

---

## 10. 铁律（写死）

### 10.1 NO_IMPLICIT_FALLBACK（不许隐式降级切换）

任何跨源、跨方法的降级必须显式声明并记录。禁止自动切换到 Tencent、Akshare 或任何第四源；禁止用历史价格冒充实时价格；历史价格作历史证据使用时必须保留日期与 stale 说明。

### 10.2 NO_FOURTH_PROVIDER（不许第四数据源）

```text
FORBIDDEN_DATA_SOURCES=Tencent,Akshare,any fourth formal provider
ACTIVE_DATA_SOURCE_COUNT_MAX=3
```

正式活动源最终只能是第 2 节的三条。设计、实现、文档中不许为第四源预留"扩展位"之外的任何实质接入路径。

### 10.3 MISSING_DATA != GENERATED_DATA（缺数据≠造数据，FAIL-CLOSED）

缺失、超时、校验失败的数据一律保持缺失状态进入记录；不许用模型记忆、插值、外推或"合理推测"补填事实。触发 fail-closed 条件（股票身份异常、持仓数据缺失、成本缺失、历史日K严重不完整、关键证据无法追溯）时，禁止输出"完整分析"。

### 10.4 八条系统铁律（V1.0 新增，本文件为主要载体）

以下 8 条为全系统铁律，三份 P2 文档共同遵守，Provider 文档为主要载体：

```text
铁律1 CANONICAL_ONLY_FOR_BUSINESS
业务层（任何 Dashboard、任何 Prompt、任何分析模型）只能读取 canonical_data；
禁止直接读取 raw_data，禁止直接读取 Provider 返回。

铁律2 PROVIDER_HAS_NO_BUSINESS_LOGIC
Provider 只做三件事：获取数据、返回原始数据、标注数据状态；
不判断"数据好不好"、不判断"是否该用"、不产生任何投资判断。

铁律3 NO_SILENT_DEGRADATION
任何数据缺失必须显式展示（如 DATA_AVAILABILITY_REPORT）；
不许静默降级、不许让分析结果看起来"完整"而实际缺数据。

铁律4 EVIDENCE_IMMUTABLE
证据只追加、不覆盖；UPDATE evidence_records = forbidden；
DELETE evidence_records = forbidden；
纠错以 new evidence + supersede link 方式进行，不改历史。

铁律5 SOURCE_IDENTITY_LOCKED
数据源身份不可修改：三源的 name / role / 职责绑定关系锁定；
禁止 DELETE FROM data_sources 及修改 provider 身份（P1 迁移计划已冻结）。

铁律6 RAW_DATA_NEVER_REPLACED
原始数据永久保留原貌；标准化结果不得冒充原始返回；
raw_data 与 normalized_data 永远分开保存、分开引用。

铁律7 MODEL_OUTPUT_NEEDS_EVIDENCE
任何 AI 分析输出必须绑定证据；无证据的结论不得进入"完整分析"；
证据链任一环节缺失即按 fail-closed 处理。

铁律8 NO_RUNTIME_COMPLEXITY_GROWTH
2GB 服务器禁止引入重量级基础设施；
NO_KAFKA、NO_REDIS、NO_CELERY、NO_HIGH_FREQUENCY_LOOP，串行执行优先。
```

---

## 11. TDX Local Runtime Dependency

```text
TDX_LOCAL_DEPLOYMENT=WINDOWS_ONLY_VALIDATED
TDX_CLOUD_DEPLOYMENT=NOT_VERIFIED
AWS_REALTIME_VALIDATION=NOT_DONE
REALTIME_DATA_REQUIRED_FOR_ANALYSIS=NO
```

- TDX 本地 HTTP 是 Local Runtime Dependency：只在用户授权的 Windows 主机（通达信金融终端 + TQ HTTP + `127.0.0.1:17709`）可用。
- 云端（AWS Lightsail）不得假设其可用；`TdxLocalHttpProvider.health_check` 在云端未验证前必须返回 `UNKNOWN` / `UNAVAILABLE`。
- 云端未验证 ≠ TDX 故障：状态记为 `UNKNOWN` / `UNAVAILABLE`，不是 `ERROR`；Dashboard 显示"本地终端离线 / 未验证"，不显示为系统故障。
- 云端实时行情缺失时：`REALTIME_QUOTE_STATUS=MISSING`，沿用历史日K分析，不引入第四源，不隐式切换。
- 08:30 / 11:00 / 16:00 三个运行时点的 TDX 相关行为按冻结采集规范第 7 节执行；本轮不启动、不修改 Scheduler。

---

## 12. 轻量约束

```text
LIGHTWEIGHT_FIRST=YES
MAX_CONCURRENCY=1
SERIAL_EXECUTION_DEFAULT=YES
```

- 服务器 2GB 内存：`NO_KAFKA`、`NO_REDIS`、`NO_CELERY`、`NO_HIGH_FREQUENCY_LOOP`。
- Provider 调用默认串行执行；三源之间不做并发竞合。
- 所有调用只读：`TRADING_WRITE=FORBIDDEN`（不下单、不撤单、不写券商账户、不自动交易）。

---

## 13. GPT 评审结论（18 个开放问题批复摘要）

评审结论：`PROVIDER_DESIGN=APPROVED_WITH_MINOR_FIX`。6 个开放问题批复如下，均已落实为正文修订：

| # | 开放问题 | 批复 | 落实 |
|---|---------|------|------|
| 1 | 超时初值是否合理 / 校准标准 | 通过；补充 P6 校准规则 | R1（第 5 节） |
| 2 | 重试 2 次 + PARTIAL 补拉 | 部分修改：PARTIAL 禁止自动补拉 | R2（第 6 节） |
| 3 | health_check 是否在 P2 定义 | 修改：保留在 P2，写死三源探针定义 | R3（第 3.1 节） |
| 4 | TDX 云端 health_check 格式 | 通过：与本地统一，仅状态不同 | R4（第 3.1 / 11 节） |
| 5 | envelope 字段命名是否冻结 | 通过冻结：P6 不得改名 | R5（第 3.3 节） |
| 6 | 跨职责拒绝错误码 | 修改：新增 INVALID_DATA_KIND_SCOPE | R6（第 3.2 / 3.4 节） |

---

## 14. 跨阶段衔接说明

- 复核差异事件的严重等级与通知机制：移出 P2，进入 P5 验收标准。
- `UNVERIFIED` 口径数据的提示文案：进入 P3 Prompt Template V1.0 统一。
- `validate_contract()` 的具体校验项：P6 实现时定义，本设计只定方法与流程位置。
- `P6_CALIBRATION_RULE` 的校准执行与结论记录：P6 实施动作，本设计只定规则。
