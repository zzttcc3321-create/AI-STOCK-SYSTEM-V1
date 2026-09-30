# 验收标准 V1.0（初稿）

```text
STATUS=FROZEN
VERSION=V1.0
PHASE=P3_PRODUCT_DEFINITION
DESIGN_ONLY=YES
IMPLEMENTATION=FORBIDDEN_IN_THIS_PHASE
```

依据冻结文档：`docs/freeze/DB_SCHEMA_V1.0.md`、`docs/freeze/DATA_COLLECTION_SPEC_V1.0.md`
（第 10 节 Fail-closed）、`docs/PROVIDER_DESIGN_V1.0.md`（8 条系统铁律、不可用时行为矩阵）、
`docs/DATA_NORMALIZER_DESIGN_V1.0.md`（校验规则）、`docs/EVIDENCE_PIPELINE_DESIGN_V1.0.md`
（human_confirm 分级、保留期限）。冲突处以冻结文档为准。

## 修订记录（V1.0_DRAFT → V1.1_DRAFT）

依据 GPT P3_REVIEW_RESULT（2026-10-01）：PASS_WITH_CHANGE，无 BLOCK；30 项检查项粒度合理，不增减。
本文件 6 项修改：① 拆分设计验收 / 实施验收（§5）
② 750/640 独立命名为 SECURITY_BASELINE_V1.0，不再绑定 P1 Step 4（§4.5）
③ 取消一挂全停，改为记录+走完+按严重等级决定是否阻断（§5）
④ 增加严重等级 CRITICAL/HIGH/MEDIUM/LOW（§5）
⑤ 复核差异事件引用 DEVIATION_PROCESS，不展开（§5）
⑥ 验收记录 human_confirm 分级为 PENDING → REVIEWED → APPROVED（§5）。

验收总则：

```text
LIGHTWEIGHT_FIRST=YES
```

所有验收方法必须在 2GB 约束下可手工或轻量脚本执行；不引入验收专用重型框架。
每条标准格式为：**检查项 / 预期结果 / 检查方法**。

---

## 1. 数据源验收

| # | 检查项 | 预期结果 | 检查方法 |
|---|--------|---------|---------|
| 1.1 | `data_sources` 表记录数 | `ACTIVE` 记录数 = 3 | 只读查询 `data_sources` 表计数 |
| 1.2 | 三源身份 | 恰为 `TUSHARE_MCP`（historical_daily）、`TDX_OFFICIAL_LOCAL_HTTP`（realtime_quote）、`WORKBUDDY_TDX_MCP`（information_fundamental），name/role 与冻结一致 | 只读查询并与冻结值逐项比对 |
| 1.3 | 无禁用源 | 无 Tencent、无 Akshare、无任何第四源记录 | 全表 name 字段检查；`ACTIVE_DATA_SOURCE_COUNT_MAX=3` |
| 1.4 | source identity 锁定 | 自 P1 迁移以来三源的 name/role/职责绑定未被修改 | 比对迁移后基线快照（`data_sources` 初始记录）；`SOURCE_IDENTITY_LOCKED` |
| 1.5 | 禁止 DELETE | 不存在删除 `data_sources` 记录的操作痕迹 | 审计说明：设计上不提供删除接口；检查变更记录为空 |

---

## 2. 数据链路验收

目标：`Provider → Normalizer → Canonical → Evidence → Analysis` 全链路可测试、可追溯。

| # | 检查项 | 预期结果 | 检查方法 |
|---|--------|---------|---------|
| 2.1 | Provider 输出契约 | `fetch` 返回 envelope 字段名与冻结一致（`ENVELOPE_FIELD_NAMES=FROZEN`），9 种状态枚举齐备 | 抽查若干次 `fetch` 返回做字段名比对（设计走查；P6 后实测） |
| 2.2 | 契约校验位置 | 数据流为 fetch → `validate_contract()` → normalize，不可跳过 | 设计走查：确认 Normalizer 入口只接受已校验数据 |
| 2.3 | 单位统一 | 价格=元、成交量=股、成交额=元；五元组完整 | 抽查 `normalized_data` 若干字段的五元组 |
| 2.4 | Canonical 唯一入口 | Dashboard / Prompt / 分析模型只读 `canonical_data`；无直读 raw 或 Provider 返回的路径 | 设计走查：列出所有业务读取点，逐一确认来源为 canonical 层 |
| 2.5 | 唯一索引 | `UNIQUE(stock_id, trade_date, provider)` 在 P6 落为数据库唯一索引 | P6 实施后检查索引定义 |
| 2.6 | 证据链完整 | 任一分析结论可下钻：结论 → 模型版本 → 计算过程 → 原始数据 → 数据源 → 获取时间 → 原始文件 | 在 Dashboard Evidence 页手工下钻一条完整链路 |
| 2.7 | Hash 完整性 | `raw_response_hash` / `normalized_data_hash` / `analysis_output_hash` 存在且重算一致 | 抽查若干证据重算比对 |
| 2.8 | 证据不可变 | `evidence_records` 无 UPDATE/DELETE；纠错为 new evidence + supersede link | 检查表无更新/删除操作记录；抽查一条纠错链 |
| 2.9 | 分析输出 7 字段 | `market_view` / `position_status` / `risk_level` / `operation_plan` / `t_strategy` / `reasoning` / `confidence` 齐备，每字段有证据引用 + 时间范围 + 数据质量状态 | 抽查若干 `analysis_reports` 记录 |
| 2.10 | 建议位明确性 | `operation_plan` / `t_strategy` 给出明确操作建议（非中性表述）；无证据时显式声明"证据不足" | 抽查若干报告的建议位 |

---

## 3. 失败路径验收

每个场景给出预期表现与检查方法。通用原则：`NO_SILENT_DEGRADATION`（缺失必须显式展示）。

### 3.1 Tushare 不可用 → FAIL-CLOSED

| 检查项 | 预期结果 | 检查方法 |
|--------|---------|---------|
| `daily_kline_job` 主源失败且官方备用不可用 | 任务记失败；禁止输出"完整分析"；记录明确失败/不完整状态 | 模拟 Tushare 不可用（设计走查；P6 后用故障注入实测），检查任务状态与输出标记 |
| 历史日K严重不完整 | 触发 fail-closed 条件之一，不输出完整分析 | 构造缺失交易日序列，检查是否记 `MISSING` 且不插值 |

### 3.2 TDX 关闭 → REALTIME=MISSING

| 检查项 | 预期结果 | 检查方法 |
|--------|---------|---------|
| 本地 TDX 不在线 | `quote_job` 记录 `REALTIME_QUOTE_STATUS=MISSING`；其余分析继续 | 关闭本地 TDX（或模拟），检查状态标记与分析是否继续 |
| 云端 health_check | 返回 `UNKNOWN` / `UNAVAILABLE`，不是 `ERROR`；Dashboard 显示"本地终端离线 / 未验证" | 在云端执行 `health_check`，检查返回状态与页面文案 |
| 无冒充 | 不得出现用历史收盘价冒充实时价格的展示 | 检查页面 2 行情区在 MISSING 时的文案 |

### 3.3 WorkBuddy 无资讯 → NOT_AVAILABLE

| 检查项 | 预期结果 | 检查方法 |
|--------|---------|---------|
| 某类官方数据为空（如无研报） | 该类别记 `NOT_AVAILABLE`；不补填；其余继续 | 模拟空返回，检查记录状态 |
| 无模型补填 | 不得用模型记忆补填研报/公告内容 | 抽查 `intelligence_data` 为空时的分析输出，确认无虚构内容 |

### 3.4 其他失败路径

| 检查项 | 预期结果 | 检查方法 |
|--------|---------|---------|
| PARTIAL 数据 | 缺失片段记 `MISSING`；禁止自动补拉（`PARTIAL_REFETCH=MANUAL_TRIGGER_ONLY`） | 构造部分缺失返回，检查无自动补拉行为 |
| 校验失败 | `VALIDATION_FAILED`；数据停在 `normalized_data`，不进 `canonical_data` | 构造异常数据（如 high < low），检查流转终止位置 |
| 跨职责请求 | 返回 `INVALID_DATA_KIND_SCOPE`，不"顺手"调其他源 | 向 Tushare Provider 请求 realtime_quote，检查拒绝 |

---

## 4. 安全验收

| # | 检查项 | 预期结果 | 检查方法 |
|---|--------|---------|---------|
| 4.1 | 无自动交易 | 全系统无下单/撤单/委托代码与入口；`TRADING_WRITE=FORBIDDEN` | 代码与文档走查：搜索下单相关调用为零；Dashboard 无交易入口 |
| 4.2 | 无券商账号接入 | 无券商账号、API Key、交易 token 的配置项与存储 | 配置与代码走查 |
| 4.3 | 无 Cookie/Token 落盘 | `request_params` 脱敏清单执行：禁止保存 Token / Password / Cookie / Session / API Key | 抽查 `evidence_records` sidecar 元数据，确认无敏感字段 |
| 4.4 | 原始响应脱敏 | 原始响应存储中不含敏感 credential | 抽查原始响应文件 |
| 4.5 | 最小权限（SECURITY_BASELINE_V1.0） | AWS 侧文件权限：目录 750 / 文件 640，属主 deploy:deploy | 抽查关键路径权限 |
| 4.6 | 证据保留期限 | 原始响应保留"人工确认后 +90 天"；磁盘占用 ≤5%，超阈值压缩归档 | 检查保留策略配置与磁盘用量 |

---

## 5. 验收执行说明

- **执行方**：阿七执行技术验收项；qi 做最终确认；GPT 做设计评审。
- **两阶段验收**：
  - `P3_DESIGN_ACCEPTANCE`（设计验收，本阶段执行）：设计完整、规则明确、验收方法存在。
    结论效力限于设计层，不代表实施层通过。
  - `P6_IMPLEMENTATION_ACCEPTANCE`（实施验收，P6 实现完成后执行）：
    真实代码/真实环境实测，含故障注入（只在测试环境做，
    不得在 Windows 事实源上做破坏性测试）。
- **验收顺序与阻断规则**：数据源验收 → 数据链路验收 → 失败路径验收 → 安全验收；
  发现问题必须记录（问题清单），继续完成其余验收，
  最终按严重等级决定是否阻断发布，不搞"一挂全停"。
- **严重等级**：CRITICAL / HIGH / MEDIUM / LOW。
  如全系统存在下单/委托代码或入口（违反 `TRADING_WRITE=FORBIDDEN`）→ CRITICAL，立即阻断；
  文档格式类问题 → LOW，只记录。等级判定标准随实施验收细化。
- **轻量执行**：手工检查优先；确需脚本的，用单文件只读脚本，不引入验收框架。
- **验收记录**：每条标准的检查结果（通过/不通过 + 证据）记入验收报告；
  验收报告本身作为证据留存（`EVIDENCE_IMMUTABLE` 同理）；
  验收记录的确认分级为 `SYSTEM_ACCEPTANCE`：PENDING → REVIEWED → APPROVED。
- **复核差异事件**：不展开，引用 `DEVIATION_PROCESS.md` 执行。
- **回归要求**：P6 实现完成后，本标准全量执行一遍；后续任何冻结修订后，
  按影响范围执行相关子集。

---

## 6. 评审结论（P3_REVIEW_RESULT，2026-10-01）

结论：PASS_WITH_CHANGE（GPT 评审，无 BLOCK）。30 项检查项粒度合理，不增减。
6 项修改已纳入本 V1.1（见文件头修订记录）。
