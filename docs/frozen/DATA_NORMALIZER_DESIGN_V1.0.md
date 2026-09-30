# 数据标准化设计 V1.0

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

依据冻结基线：`docs/freeze/DB_SCHEMA_V1.0.md`（2.7 daily_kline / 2.6 market_quotes）、
`docs/freeze/DATA_COLLECTION_SPEC_V1.0.md`（第 5 节单位标准化、第 4 节历史日K校验）。
（冲突处以冻结文档为准。）

---

## 0. DRAFT → V1.0 修订日志

| # | 修订来源 | 改动 |
|---|---------|------|
| R1 | GPT 评审 Q1 | 第 7 节第 4 条：`PRICE_CHANGE_WARNING_THRESHOLD=CONFIGURABLE`，默认 ±30%，仅 WARN 不 FAIL；ST / 新股 / 复牌等特殊状态由人工复核标记 |
| R2 | GPT 评审 Q2 | 增加 `DERIVED_AMOUNT_ALLOWED=NO`：禁止用 `close×volume` 自动推导 amount；未来如启用必须新增字段 `amount_source=DERIVED`，不能覆盖原始 amount |
| R3 | GPT 评审 Q3 | 第 5 节：canonical 唯一约束在 P6 落为数据库唯一索引 `UNIQUE(stock_id, trade_date, provider)`，目的防重复采集 |
| R4 | GPT 评审 Q4 | `UNVERIFIED` 口径数据的提示文案进入 P3 Prompt Template V1.0（本设计只记录衔接） |
| R5 | GPT 评审 Q5 | 五元组物理存储形态暂不冻结，留到 P6（涉及 SQLite 实际性能） |
| R6 | GPT 评审 Q6 | 新增第 5b 节：`market_quotes` 独立 canonical contract（含 `quote_timestamp`、bid/ask、`freshness`），不能复用 daily_kline |
| R7 | 反向验证 | 第 5 / 5b 节增加 `data_quality_status` 字段：`PASS` / `WARNING` / `UNVERIFIED` / `FAILED` |
| R8 | 反向验证 | 第 2 节立铁律：Normalizer 只能转换、不能推理；`MISSING_DATA != GENERATED_DATA` |
| R9 | 反向验证 | 引用 8 条系统铁律全文（第 10.4 节；主要载体为 Provider 设计文档） |
| R10 | 反向验证 | 明确 `CANONICAL_ONLY_FOR_BUSINESS`：业务层唯一入口为 canonical_data |

---

## 1. 设计目标与范围

### 1.1 目标

定义三层数据模型（raw → normalized → canonical）、字段五元组、单位统一口径、
`daily_kline` 与 `market_quotes` 的 canonical contract，以及 `adjust_type` 的冻结处理口径。
P2 只做设计，不进入编码；不改动任何表结构。

### 1.2 非目标

- 不编写 Normalizer 实现代码。
- 不调用任何数据源（无真实数据、无伪造返回示例；本文示例均为"示意"）。
- 不修改数据库；不新增表、不新增字段。

### 1.3 为什么需要 Normalizer

三源字段口径存在差异（示例均为示意）：本地 TDX 的 `volume` 单位可能是"手"，
Tushare 的 `volume` 单位需按 Provider 声明确认。若 Normalizer 不统一单位与字段映射，
后续分析模型会算错、Evidence 无法复核、Dashboard 显示错误。
Normalizer 是"三源异构 → 系统统一口径"的唯一关口。

---

## 2. 三层模型

```text
raw_data  →  normalized_data  →  canonical_data
（原始数据）   （标准化）          （业务使用）
```

| 层 | 定义 | 准入条件 | 允许的消费者 |
|----|------|---------|-------------|
| `raw_data` | Provider 返回的原始响应，按原样存储 | `fetch` 返回 `OK`/`PARTIAL` | 标准化层、证据链（追溯） |
| `normalized_data` | 单位统一、字段映射后的数据，保留五元组 | 通过单位转换与字段映射；单位不明则标记未验证 | 校验层 |
| `canonical_data` | 通过全部校验、可被业务使用的最终数据 | 通过第 7 节全部校验规则 | 分析任务、Dashboard、分析数据包 |

铁律：

- 任何数据进入 `canonical_data` 之前必须经过标准化与校验；不许跳过。
- `raw_data` 与 `normalized_data` 分开保存；`canonical_data` 不得冒充原始返回（冻结要求）。
- 校验失败的数据停在 `normalized_data` 层并标记失败原因，不进入 `canonical_data`。
- **Normalizer 只能转换，不能推理**（V1.0 新增）：缺收盘价不许"猜一个"，
  缺成交额不许用 `close×volume` 补；`MISSING_DATA != GENERATED_DATA`。

```text
CANONICAL_ONLY_FOR_BUSINESS
```

业务层唯一入口：任何 Dashboard、任何 Prompt、任何分析模型只能读取 `canonical_data`；
禁止直接读取 `raw_data`，禁止直接读取 Provider 返回。

---

## 3. 字段五元组

所有数值字段在 `normalized_data` 层必须保留五元组：

| 字段 | 说明 |
|------|------|
| `raw_value` | 原始值（Provider 返回的原样） |
| `raw_unit` | 原始单位（Provider 声明的单位；声明缺失则为 null，不许猜） |
| `normalized_value` | 转换后的标准单位值 |
| `normalized_unit` | 标准单位（见第 4 节） |
| `conversion_rule` | 转换规则标识（如 `YUAN_TO_YUAN_IDENTITY`、`LOT100_TO_SHARE`）；无需转换时记 `IDENTITY` |

规则：

- 单位不明时禁止猜测转换：`normalized_value` 为 null，标记 `unit_status=UNVERIFIED`。
- `conversion_rule` 必须可枚举、可审计；不许出现"人工心算"式转换。
- 五元组随数据进入证据链（`normalization_rule` 字段引用本节规则标识）。
- 五元组物理存储形态（宽表列 vs JSON 打包）暂不冻结，留到 P6（涉及 SQLite 实际性能）。

---

## 4. 单位统一

| 度量 | 标准单位 | 说明 |
|------|---------|------|
| 价格 `price` | 元 | open/high/low/close/pre_close 统一为元 |
| 成交量 `volume` | 股 | 统一为股；"手"按 1 手=100 股转换并记录规则 |
| 成交额 `amount` | 元 | 统一为元；"万元"按 ×10000 转换并记录规则 |

单位不同的原始值一律走五元组；转换系数必须来自 Provider 声明或公开计量常识（如手→股），不得来自猜测。

```text
DERIVED_AMOUNT_ALLOWED=NO
```

禁止用 `close×volume` 自动推导 `amount`。未来如经评审启用，必须新增字段
`amount_source=DERIVED` 显式标记派生来源，绝不能覆盖原始 `amount`。

---

## 5. daily_kline 的 canonical contract

`daily_kline`（候选复用 `stock_daily`，以兼容评估为准）进入业务使用层的字段契约：

| 字段 | 类型 | 可空 | 说明 |
|------|------|------|------|
| `stock_id` | TEXT | 否 | 证券标识 |
| `trade_date` | TEXT(YYYY-MM-DD) | 否 | 交易日期 |
| `open` | REAL | 否 | 开盘价（元） |
| `high` | REAL | 否 | 最高价（元） |
| `low` | REAL | 否 | 最低价（元） |
| `close` | REAL | 否 | 收盘价（元） |
| `volume` | REAL/INT | 否 | 成交量（股） |
| `amount` | REAL | 可 | 成交额（元；源未提供则 null，不伪造、不推导） |
| `adjust_type` | TEXT | 可 | 复权口径（见第 6 节；未知则 null + 标记） |
| `data_quality_status` | TEXT | 否 | 数据质量状态：`PASS` / `WARNING` / `UNVERIFIED` / `FAILED`（V1.0 新增；`adjust_type` 只是质量的一部分） |
| `provider` | TEXT | 否 | 数据源名称（三源之一） |
| `provider_timestamp` | TEXT | 可 | 数据方时间戳；缺失为 null + freshness 未知 |

约束：

- `(stock_id, trade_date, provider)` 唯一约束在 P6 落为数据库唯一索引：
  `UNIQUE(stock_id, trade_date, provider)`，目的防止重复采集。
- 价格/量/额必须已按第 4 节统一单位；未统一的数据不许进入本层。
- `provider` 必须是三源之一；出现第四源名称即 `VALIDATION_FAILED`。

## 5b. market_quotes 的 canonical contract（V1.0 新增，独立定义）

实时行情与历史K线语义不同，不能复用 daily_kline contract：

| 字段 | 类型 | 可空 | 说明 |
|------|------|------|------|
| `stock_id` | TEXT | 否 | 证券标识 |
| `quote_timestamp` | TEXT | 否 | 行情时间戳（数据方声称；缺失则为 null + freshness 未知，历史收盘价不得冒充） |
| `price` | REAL | 否 | 当前价（元） |
| `bid` / `ask` | REAL | 可 | 买一 / 卖一价（元；源未提供则 null） |
| `volume` | REAL/INT | 可 | 成交量（股；快照口径按 Provider 声明） |
| `amount` | REAL | 可 | 成交额（元；不伪造、不推导） |
| `freshness` | TEXT | 否 | 新鲜度标记：`REALTIME` / `STALE` / `UNKNOWN` |
| `data_quality_status` | TEXT | 否 | `PASS` / `WARNING` / `UNVERIFIED` / `FAILED` |
| `provider` | TEXT | 否 | 必须为 `TDX_OFFICIAL_LOCAL_HTTP`（实时行情唯一事实来源） |

---

## 6. adjust_type 冻结口径

冻结要求：新增 `adjust_type` 时**不可猜测旧数据复权口径**；未验证的复权口径不许入库为"已复权"。

| 取值 | 含义 | 允许条件 |
|------|------|---------|
| `UNADJUSTED` | 不复权 | Provider 明确声明未复权 |
| `QFQ` | 前复权 | Provider 明确声明且口径已验证 |
| `HFQ` | 后复权 | Provider 明确声明且口径已验证 |
| `NULL` + `adjust_status=UNVERIFIED` | 未知/未验证 | 默认状态：Provider 未声明，或声明未经验证 |

规则：

- 不得猜测：历史数据的复权口径未知时，一律 `NULL` + `UNVERIFIED`，不许按"看起来像"填 `QFQ`。
- 不伪造：未验证的复权口径不许入库为"已复权"；分析使用未验证口径数据时必须显式提示口径未验证（提示文案进入 P3 Prompt Template V1.0 统一）。
- 同一 `stock_id` 下不同 `adjust_type` 的序列不得混算（如算涨跌幅），混算前必须先统一口径或明确拒绝。

---

## 7. 校验规则

进入 `canonical_data` 前必须通过（对应冻结采集规范第 4 节）：

1. **日期完整性**：`trade_date` 为合法交易日历日期；序列连续性按交易日检查，缺失交易日记为数据缺口（`MISSING`），不插值补齐。
2. **字段完整性**：`open/high/low/close/volume` 非空；`amount` 可空但需标记。
3. **复权口径**：`adjust_type` 符合第 6 节；`UNVERIFIED` 数据可入库但必须携带标记。
4. **异常值**：`high >= low`、`high >= open`、`high >= close`、`low <= open`、`low <= close`；价格/量为负即失败；单日涨跌幅超出阈值记为警告而非直接失败。
5. **来源合法性**：`provider` 为三源之一。

```text
PRICE_CHANGE_WARNING_THRESHOLD=CONFIGURABLE
```

单日涨跌幅警告阈值默认 ±30%，**仅 WARN、不 FAIL**（V1.0 修订：不冻结 ±30%）。
A股存在 ST 涨跌停限制、新股、复牌等特殊交易状态，触发阈值的数据标记 `data_quality_status=WARNING`，
由人工复核标记处理，不直接判失败。

任一硬规则失败 → `VALIDATION_FAILED`（此时 `data_quality_status=FAILED`），数据不进 `canonical_data`，失败原因写入证据。

---

## 8. 失败处理

| 失败点 | 处理 |
|--------|------|
| 单位不明 | `normalized_value=null`，`unit_status=UNVERIFIED`，可进 `normalized_data`，不可进 `canonical_data` |
| 校验硬规则失败 | `VALIDATION_FAILED`，停在 `normalized_data`，记录失败原因与证据，不重试 |
| 部分日期缺失 | 缺失日期记 `MISSING`，已有日期正常流转，不插值 |
| Provider 返回 `PARTIAL` | 按缺失片段分别标记，成功片段正常流转；补拉仅人工触发（`PARTIAL_REFETCH=MANUAL_TRIGGER_ONLY`） |

---

## 9. 与证据链的衔接

- 每次标准化动作产生 `normalization_rule` 记录（规则标识 + 输入五元组引用），供证据链字段中的 `normalization_rule` 引用。
- 每次校验产生 `validation_result`（通过/失败 + 失败明细），供证据链引用。
- `raw_data` 存储引用即证据链的 `raw_response_path`；三者同源，保证"结论可反查到原始响应"。
- `data_quality_status` 随校验结果进入证据链，供追溯与复核使用。

---

## 10. 轻量约束与系统铁律引用

```text
LIGHTWEIGHT_FIRST=YES
SERIAL_EXECUTION_DEFAULT=YES
```

- 标准化为串行批处理；不引入流式框架、消息队列、缓存服务。
- 2GB 内存约束：单批处理量上限待 P6 按实测定，本设计只要求"分批、可中断、可恢复"。

8 条系统铁律全文见 `PROVIDER_DESIGN_V1.0` 第 10.4 节（主要载体），本设计共同遵守，
尤其以下三条：

```text
CANONICAL_ONLY_FOR_BUSINESS（业务层只能读取 canonical_data）
MISSING_DATA != GENERATED_DATA（Normalizer 只能转换、不能推理）
RAW_DATA_NEVER_REPLACED（原始数据永久保留原貌）
```

---

## 11. GPT 评审结论（18 个开放问题批复摘要）

评审结论：`NORMALIZER_DESIGN=APPROVED_WITH_MINOR_FIX`。6 个开放问题批复如下，均已落实：

| # | 开放问题 | 批复 | 落实 |
|---|---------|------|------|
| 1 | 异常值阈值 ±30% | 修改：改为可配置，默认 ±30%，仅 WARN 不 FAIL | R1（第 7 节） |
| 2 | amount 缺失是否允许 close×volume 估算 | 通过但明确：默认禁止；未来启用须新增 `amount_source=DERIVED` | R2（第 4 节） |
| 3 | canonical 唯一约束落数据库还是应用 | 修改：P6 落数据库唯一索引 | R3（第 5 节） |
| 4 | UNVERIFIED 提示是否进入 Prompt | 通过：进入 P3 Prompt Template V1.0 | R4（第 6 节衔接） |
| 5 | 五元组物理存储形态 | 暂不冻结，留到 P6 | R5（第 3 节） |
| 6 | market_quotes 是否单独定义 | 修改：需要单独定义 | R6（第 5b 节） |

---

## 12. 跨阶段衔接说明

- `UNVERIFIED` 口径数据的提示文案：进入 P3 Prompt Template V1.0 统一。
- 五元组物理存储形态：P6 按 SQLite 实际性能决定。
- 唯一索引 `UNIQUE(stock_id, trade_date, provider)` 的 DDL 落定：P6 实施动作。
- `market_quotes` contract 的 P6 实现细节（如 bid/ask 缺失时的 freshness 判定）：P6 定义。
