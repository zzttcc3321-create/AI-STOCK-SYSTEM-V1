# 证据链管线设计 V1.0

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

依据冻结基线：`docs/freeze/DB_SCHEMA_V1.0.md`（2.14 evidence_records / 2.11 collection_tasks / 2.15 system_runs）、
`docs/freeze/DATA_COLLECTION_SPEC_V1.0.md`（第 6 节采集证据链、第 9 节标准分析数据包）。
（冲突处以冻结文档为准。）

---

## 0. DRAFT → V1.0 修订日志

| # | 修订来源 | 改动 |
|---|---------|------|
| R1 | GPT 评审 Q1 | 第 4 节：`endpoint_or_tool` / `request_params` / `provider_timestamp` 不新增列，保持 metadata sidecar 存储（轻量原则） |
| R2 | GPT 评审 Q2 | 第 6 节：原始响应保留期限冻结为"人工确认完成后 +90 天"；最大磁盘占用 AWS 本地磁盘 5%；超过阈值压缩归档 |
| R3 | GPT 评审 Q3 | 第 6 节：`raw_response_path` 三级目录约定冻结：`system_run_id/` / `collection_task_id/` / `request_id/` |
| R4 | GPT 评审 Q4 | 第 7.3 节：`human_confirm` 分级——采集证据 `PENDING` 即可；分析结论必须 `CONFIRMED`；操作建议必须 `CONFIRMED` |
| R5 | GPT 评审 Q5 | 复核差异事件的严重等级与通知机制：移出 P2，进入 P5 验收标准（第 7.2 节记录衔接） |
| R6 | GPT 评审 Q6 | 第 3 节：`request_params` 脱敏清单——禁止保存 Token / Password / Cookie / Session / API Key；允许股票代码 / 日期范围 / 查询类型 |
| R7 | 反向验证 | 第 3.1 节新增完整性 Hash 字段：`raw_response_hash`、`normalized_data_hash`、`analysis_output_hash`（防篡改、可追溯） |
| R8 | 反向验证 | 第 2 节强化证据不可变规则：`UPDATE evidence_records = forbidden`；`DELETE evidence_records = forbidden`；纠错用 new evidence + supersede link |
| R9 | 反向验证 | 第 7.3 节新增人工确认对象分级表：原始行情否、标准化结果否、异常数据是、操作建议是、最终报告是 |
| R10 | 反向验证 | 引用 8 条系统铁律全文（第 10 节；主要载体为 Provider 设计文档），尤其 `EVIDENCE_IMMUTABLE` / `MODEL_OUTPUT_NEEDS_EVIDENCE` |

---

## 1. 设计目标与范围

### 1.1 目标

定义从"分析结论"到"原始数据"的完整证据链：链条模型、每个 Evidence 必须记录的字段、
证据采集时机、原始响应存储规范，以及数据追溯、分析复核、人工确认、后续审计四种能力的设计。
P2 只做设计，不进入编码。

### 1.2 非目标

- 不编写证据管线实现代码。
- 不调用任何数据源；不伪造证据记录示例（本文示例均为"示意"）。
- 不修改数据库（含 `evidence_records` 在内的任何 DDL/DML）。

### 1.3 Evidence 是一级对象

Evidence 不是"日志"。Evidence 是：分析结果的法律依据、复核的唯一入口、审计的检查点。
任何 AI 分析输出必须绑定证据（`MODEL_OUTPUT_NEEDS_EVIDENCE`），无证据的结论不得进入"完整分析"。

---

## 2. 证据链模型

一条完整证据链（自顶向下）：

```text
分析结论（analysis_reports）
  → 分析结果明细（结构化字段：market_view / position_status / risk_level / …）
    → 模型版本（model_version：产生结论的模型与版本）
      → 计算过程（calculation：指标公式、参数、中间结果引用）
        → 原始数据（raw_data：标准化前的 Provider 原始响应）
          → 数据源（source：三源之一）
            → 获取时间（request_timestamp / provider_timestamp）
```

原则：

- 链条上任一环节缺失，该结论即为"证据不完整"，按 fail-closed 处理（不得输出为"完整分析"）。
- 证据只追加、不修改：已记录的证据不可 UPDATE/DELETE；纠错以新增证据 + 关联原证据的方式进行。
- 标准化结果不得冒充原始返回：`raw_data` 与 `normalized_data` 永远分开引用。

```text
EVIDENCE_IMMUTABLE
UPDATE evidence_records = forbidden
DELETE evidence_records = forbidden
纠错方式：new evidence + supersede link（新证据关联被替代证据，不改历史）
```

---

## 3. 每个 Evidence 的字段

### 3.0 基础 12 字段

| # | 字段 | 必填 | 说明 |
|---|------|------|------|
| 1 | `source` | 是 | 数据源名称：三源之一（`TUSHARE_MCP` / `TDX_OFFICIAL_LOCAL_HTTP` / `WORKBUDDY_TDX_MCP`） |
| 2 | `endpoint_or_tool` | 是 | 调用的端点或工具标识（如 `TUSHARE_MCP.daily`、`TDX_LOCAL_HTTP/quote`，为逻辑标识，示意） |
| 3 | `request_params` | 是 | 请求参数快照（股票代码、日期范围、查询类型；脱敏清单见下） |
| 4 | `request_timestamp` | 是 | 调用方发起时间（UTC+8，ISO8601） |
| 5 | `provider_timestamp` | 按需 | 数据方声称的数据时间；缺失为 null + freshness 未知 |
| 6 | `raw_response_path` | 按需 | 原始响应存储路径；`fetch` 成功/`PARTIAL` 时必填 |
| 7 | `normalization_rule` | 按需 | 标准化规则标识（引用 `DATA_NORMALIZER_DESIGN_V1.0` 的规则）；未经过标准化为 null |
| 8 | `validation_result` | 是 | 校验结果：`PASS` / `FAIL` + 失败明细；未校验为 `NOT_VALIDATED` |
| 9 | `collection_task_id` | 是 | 关联 `collection_tasks.id` |
| 10 | `system_run_id` | 是 | 关联 `system_runs.id` |
| 11 | `model_version` | 按需 | 产生使用该证据的分析结论的模型版本；纯采集证据为 null |
| 12 | `human_confirm` | 是 | 人工确认状态：`PENDING` / `CONFIRMED` / `REJECTED`（见第 7.3 节） |

字段口径与冻结采集规范第 6 节对齐，并新增 `model_version` / `human_confirm` 以覆盖"分析复核"与"人工确认"能力。

`request_params` 脱敏清单（V1.0 冻结）：

```text
禁止保存：Token / Password / Cookie / Session / API Key
允许保存：股票代码 / 日期范围 / 查询类型
```

### 3.1 完整性 Hash 字段（V1.0 新增）

每个 Evidence 必须携带以下三个 Hash，用于防篡改与变更追溯：

| 字段 | 说明 |
|------|------|
| `raw_response_hash` | 原始响应的 Hash（原始数据是否被篡改的依据） |
| `normalized_data_hash` | 标准化后数据的 Hash（Normalizer 规则变更后可追溯、可比对） |
| `analysis_output_hash` | 分析输出的 Hash（分析结果与输入数据是否对得上） |

- Hash 随证据元数据存储（metadata sidecar，见第 4 节）；校验时重新计算比对，不一致即记为证据异常事件。
- Hash 算法选型（SHA-256 或等效）由 P6 落定，本设计只定字段与用途。

---

## 4. 与 evidence_records 表的映射

冻结 Schema（2.14）`evidence_records` 已有字段与本设计字段的映射关系（设计映射，不改表）：

| 本设计字段 | 映射去向 |
|-----------|---------|
| `source` | `evidence_records.source` |
| `request_timestamp` | `evidence_records.timestamp` |
| `raw_response_path` | `evidence_records.raw_data_path` |
| `normalization_rule` + `validation_result` | `evidence_records.calculation`（结构化记录，示意） |
| `model_version` | `evidence_records.model_version` |
| `human_confirm` | `evidence_records.human_confirm` |
| `collection_task_id` | `evidence_records.collection_task_id` |
| `system_run_id` | `evidence_records.system_run_id` |
| `endpoint_or_tool` / `request_params` / `provider_timestamp` | 随 `raw_response_path` 指向的原始响应元数据一并存储（metadata sidecar，不新增列） |
| `raw_response_hash` / `normalized_data_hash` / `analysis_output_hash` | 随证据元数据存储（sidecar），校验时重算比对 |

说明：

- 本设计**不新增表、不新增字段**；暂无独立列的字段随原始响应元数据存储（metadata sidecar），符合 2GB 轻量原则。
- 若未来评审认为某字段应独立成列，需走"冻结修订"流程（用户明确解冻/升级版本），P2/P6 不擅自决定。

---

## 5. 证据采集时机

| 时机 | 采集内容 | 触发方 |
|------|---------|--------|
| Provider `fetch` 返回 | 字段 1–6、9、10（来源与获取）+ `raw_response_hash` | Provider 契约（P6 实现） |
| 标准化完成 | 字段 7（`normalization_rule`）+ `normalized_data_hash` | Normalizer（P6 实现） |
| 校验完成 | 字段 8（`validation_result`） | 校验层（P6 实现） |
| 分析任务使用证据 | 字段 11（`model_version`）+ `analysis_output_hash` + 关联 `analysis_report_id` | 分析任务（P8 / 后续阶段） |
| 人工确认 | 字段 12（`human_confirm` 状态流转） | 人工确认流程（第 7.3 节） |

采集失败不许静默：证据写入失败本身记为一次证据缺失事件，关联到当次 `collection_task_id`。

---

## 6. 原始响应存储规范

- `raw_response_path` 指向原始响应的完整存储，内容为 Provider 返回的**原样**（未做任何清洗、截断、改写）。
- 目录约定（V1.0 冻结）：

```text
system_run_id/
  collection_task_id/
    request_id/
```

给定证据能唯一定位到原始文件。

- 原始响应元数据（`endpoint_or_tool`、`request_params`、`provider_timestamp`、三个 Hash）与原始响应同处存储，P6 实现时保证原子写入（要么都有，要么记为存储失败）。
- 保留期限（V1.0 冻结）：关联分析结论的人工确认完成后 **+90 天**。
- 磁盘配额（V1.0 冻结）：原始响应存储最大占用 **AWS 本地磁盘 5%**；超过阈值时压缩归档（保留 Hash 与元数据可验证性）。
- 2GB 约束：不许为"全量永久保存"而无限制膨胀磁盘。

---

## 7. 四种能力设计

### 7.1 数据追溯

给定任一分析结论（`analysis_report_id`），可逐级下钻：结论 → 结构化字段 → `model_version` →
`calculation` → `raw_response_path`（原始响应）→ `source` → `request_timestamp`/`provider_timestamp`。
追溯查询为只读；P6 实现时以只读查询支撑，不新增写接口。

### 7.2 分析复核

- 复核输入 = 原始证据集合（`raw_response_path` 重新读取）+ 记录的 `normalization_rule` + `model_version`。
- 复核可重放标准化与计算过程，比对结果是否一致；不一致记为复核差异事件，关联原证据。
- 复核不修改原证据、不修改原结论；只产生新的复核记录。
- 复核差异事件的严重等级与通知机制：移出 P2，进入 P5 验收标准（本设计只记录衔接）。

### 7.3 人工确认

`human_confirm` 状态机：

```text
PENDING → CONFIRMED
PENDING → REJECTED
CONFIRMED → REJECTED（纠错时，须附原因，原记录保留）
```

`human_confirm` 分级（V1.0 冻结）：

| 证据对象 | 必填状态 |
|---------|---------|
| 采集证据（原始行情 / 标准化结果） | `PENDING` 即可（无需人工逐条确认） |
| 分析结论 | 必须 `CONFIRMED` |
| 操作建议 | 必须 `CONFIRMED` |

人工确认对象分级表（V1.0 新增）：

| 对象 | 是否需要人工确认 |
|------|----------------|
| 原始行情 | 否 |
| 标准化结果 | 否 |
| 异常数据 | 是 |
| 操作建议 | 是 |
| 最终报告 | 是 |

- 分析结论发布前，关键结论的 `human_confirm` 须为 `CONFIRMED`（Human-in-the-loop，冻结产品基线要求）。
- `REJECTED` 的证据不得作为"已确认结论"的支撑；但记录本身保留，用于审计。
- 确认人身份按冻结 Schema 2.12 的 V1 极简用户关联记录。

### 7.4 后续审计

- 审计输入：时间范围 + `system_run_id` / `stock_id` 任一维度，可列出全部证据链。
- 审计只读；审计过程本身记一条审计证据（谁、何时、查了什么范围），形成"审计的审计"。
- 证据不可变性（第 2 节）是审计可信的前提；任何 UPDATE/DELETE 证据的行为即视为破坏审计链，设计上不提供此类接口。

---

## 8. 与标准分析数据包的衔接

冻结采集规范第 9 节的 `analysis_package.json` 中的 `evidence_index` 即本设计的证据索引：
每个数据包必须携带其全部输入数据的证据引用（字段子集：`source`、`request_timestamp`、
`provider_timestamp`、`validation_result`、`human_confirm`），使数据包离线后仍可追溯。

---

## 9. GPT 评审结论（18 个开放问题批复摘要）

评审结论：`EVIDENCE_PIPELINE_DESIGN=APPROVED_WITH_MINOR_FIX`。6 个开放问题批复如下，均已落实：

| # | 开放问题 | 批复 | 落实 |
|---|---------|------|------|
| 1 | 三字段是否新增列 | 通过：不新增列，保持 metadata sidecar | R1（第 4 节） |
| 2 | 原始响应保留期限 / 磁盘配额 | 修改：人工确认后 +90 天；最大磁盘 5%；超阈值压缩归档 | R2（第 6 节） |
| 3 | raw_response_path 目录约定 | 通过并冻结：三级目录 | R3（第 6 节） |
| 4 | human_confirm 覆盖范围 | 修改：分级——采集证据 PENDING 即可，分析结论/操作建议必须 CONFIRMED | R4（第 7.3 节） |
| 5 | 复核差异事件定级与通知 | 移出 P2，进入 P5 验收标准 | R5（第 7.2 节） |
| 6 | request_params 脱敏规则 | 补充：明确脱敏清单 | R6（第 3 节） |

---

## 10. 系统铁律引用

8 条系统铁律全文见 `PROVIDER_DESIGN_V1.0` 第 10.4 节（主要载体），本设计共同遵守，
尤其以下三条：

```text
EVIDENCE_IMMUTABLE（证据只追加、不覆盖；UPDATE/DELETE evidence_records = forbidden）
MODEL_OUTPUT_NEEDS_EVIDENCE（任何 AI 分析必须绑定证据）
RAW_DATA_NEVER_REPLACED（原始数据永久保留原貌）
```

---

## 11. 跨阶段衔接说明

- 复核差异事件的严重等级与通知机制：进入 P5 验收标准。
- Hash 算法选型与 sidecar 物理形态：P6 落定。
- 压缩归档的具体策略（算法、触发检查频率）：P6 按 2GB 实测决定，本设计只定阈值与原则。
- `human_confirm` 的 P6 状态机实现：只允许第 7.3 节定义的流转，不许新增静默流转。
