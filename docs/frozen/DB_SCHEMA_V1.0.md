# 股票AI辅助系统｜DB Schema V1.0

Status: `FROZEN_FOR_IMPLEMENTATION`

适用基线：`PRODUCT_MANUAL_FREEZE_2026-09-29_V1`、`ARCHITECTURE_FREEZE_2026-09-29_OFFICIAL_DATA_V3`、`ENGINEERING_IMPLEMENTATION_TASKS_V1.0`。

## 1. 数据库权威与迁移原则

```text
DATABASE=EXISTING_SQLITE
DB_PATH=data\stock_analysis.db
DB_IMPLEMENTATION_POLICY=COMPATIBILITY_FIRST
NEW_DATABASE=FORBIDDEN
HISTORICAL_DATA_DELETION=FORBIDDEN
SOURCE_OF_TRUTH=WINDOWS_EXISTING_SQLITE
AWS_SQLITE_ROLE=TARGET_RUNTIME_AFTER_CONTROLLED_MIGRATION
MIGRATION_MODE=ONE_WAY_CONTROLLED_MIGRATION
REALTIME_SYNC=NO
```

Windows 现有 SQLite 是事实库。16 个逻辑实体 **不等于** 必须新建 16 张物理表；优先确认现有表、字段和历史记录是否承担同一职责。`docs/implementation/DB_SCHEMA_IMPLEMENTATION_MAPPING_V1.md` 是建库前的只读候选盘点，不是已通过的兼容迁移结论。

| 判定 | 处理规则 |
| --- | --- |
| `EXISTING_EQUIVALENT` | `REUSE`，不重复建表 |
| `PARTIAL_EQUIVALENT` | `EXTEND_OR_MIGRATE`，先证明兼容性再做增量迁移 |
| `MISSING` | `CREATE`，只补缺失的最小实体 |
| `CONFLICT` | `STOP_AND_REPORT`，不得靠破坏性重建解决 |

以下 `EXISTING_TABLE_MAPPING` 是候选关系；`IMPLEMENTATION_ACTION` 是在正式字段、约束及历史兼容性核验后的处理规则，并非本轮已执行操作。所有新增/扩展都必须纳入可重复执行或版本化的迁移。

## 2. 十六个逻辑实体

### 2.1 users

- PURPOSE：记录 V1 最多三位用户的基本身份与状态，不建立复杂 RBAC。
- REQUIRED_FIELDS：`id`, `username`, `email nullable`, `status`, `created_at`。
- EXISTING_TABLE_MAPPING：当前 SQLite 未发现明确对应表。
- IMPLEMENTATION_ACTION：`MISSING → CREATE`；若后续发现等价身份表，先改判 `EXISTING_EQUIVALENT → REUSE`。
- COMPATIBILITY_NOTES：不得把会话或聊天记录表直接视作用户表；不能凭推测回填真实用户身份。

### 2.2 stocks

- PURPOSE：维护动态股票池的证券身份与启停状态。
- REQUIRED_FIELDS：`id`, `symbol`, `market`, `name`, `industry nullable`, `status`, `created_at`, `updated_at`。
- EXISTING_TABLE_MAPPING：当前未发现明确证券主表；`stock_daily.code` 仅为历史行情列。
- IMPLEMENTATION_ACTION：确认缺失后 `MISSING → CREATE`。
- COMPATIBILITY_NOTES：不写死七只股票；与旧表中的 `code/symbol/market` 对应须保持历史可追溯。

### 2.3 accounts

- PURPOSE：关联用户与持仓账户。
- REQUIRED_FIELDS：`id`, `user_id`, `name`, `broker nullable`, `currency`, `created_at`。
- EXISTING_TABLE_MAPPING：候选 `portfolio_accounts`，已有 `id/owner_id/name/broker/base_currency/created_at`。
- IMPLEMENTATION_ACTION：字段语义确认后，等价则 `REUSE`；部分等价则 `EXTEND_OR_MIGRATE`。
- COMPATIBILITY_NOTES：`owner_id ↔ user_id`、`base_currency ↔ currency` 的对应不得靠列名猜测；不修改真实券商账户。

### 2.4 holdings

- PURPOSE：保存账户当前持仓快照与估值结果。
- REQUIRED_FIELDS：`id`, `account_id`, `stock_id`, `quantity`, `cost_price`, `market_value`, `profit_loss`, `profit_loss_pct`, `snapshot_time`。
- EXISTING_TABLE_MAPPING：候选 `portfolio_positions`，已有 `account_id/symbol/quantity/avg_cost/market_value_base/unrealized_pnl_base/updated_at`。
- IMPLEMENTATION_ACTION：完成股票身份、币种、估值口径与时间语义映射后 `REUSE` 或 `EXTEND_OR_MIGRATE`；冲突则 `STOP_AND_REPORT`。
- COMPATIBILITY_NOTES：已存在 `account_id` 到 `portfolio_accounts.id` 的外键；不得改动真实持仓数量、成本或盈亏历史。

### 2.5 trade_transactions

- PURPOSE：保存 BUY/SELL 历史交易流水。
- REQUIRED_FIELDS：`id`, `account_id`, `stock_id`, `trade_date`, `direction`, `price`, `quantity`, `fee`, `created_at`。
- EXISTING_TABLE_MAPPING：候选 `portfolio_trades`，已有 `account_id/symbol/trade_date/side/price/quantity/fee/created_at`。
- IMPLEMENTATION_ACTION：验证 `symbol ↔ stock_id` 与 `side ↔ direction` 后 `REUSE` 或 `EXTEND_OR_MIGRATE`。
- COMPATIBILITY_NOTES：保留现有 `tax/trade_uid/dedup_hash` 与历史交易；禁止重复导入或重算旧交易。

### 2.6 market_quotes

- PURPOSE：保存实时/最后行情快照及其来源、单位和请求时间。
- REQUIRED_FIELDS：`id`, `stock_id`, `price`, `pre_close`, `open_price`, `high`, `low`, `volume`, `amount`, `volume_unit`, `amount_unit`, `provider`, `provider_timestamp nullable`, `request_timestamp`。
- EXISTING_TABLE_MAPPING：未发现明确的独立行情快照表；`stock_daily` 不等价。
- IMPLEMENTATION_ACTION：确认缺失后 `MISSING → CREATE`。
- COMPATIBILITY_NOTES：`provider_timestamp` 缺失要如实保存为空并标记 freshness 未知；历史收盘价不得冒充实时价格。

### 2.7 daily_kline

- PURPOSE：保存经校验的历史日K与复权口径。
- REQUIRED_FIELDS：`id`, `stock_id`, `trade_date`, `open`, `high`, `low`, `close`, `volume`, `amount`, `adjust_type`, `provider`, `provider_timestamp nullable`, `created_at`。
- EXISTING_TABLE_MAPPING：候选 `stock_daily`，已有 `code/date/open/high/low/close/volume/amount/data_source/created_at` 等。
- IMPLEMENTATION_ACTION：核实代码键、日期、单位、复权口径及历史唯一性后 `REUSE` 或 `EXTEND_OR_MIGRATE`。
- COMPATIBILITY_NOTES：不能把 `code/date/data_source` 静默改名并使旧查询失效；新增 `adjust_type` 时不可猜测旧数据复权口径。

### 2.8 financial_reports

- PURPOSE：保存按证券和报告期关联的财务报告指标与来源。
- REQUIRED_FIELDS：`id`, `stock_id`, `report_date`, `revenue nullable`, `net_profit nullable`, `cash_flow nullable`, `total_asset nullable`, `total_liability nullable`, `roe nullable`, `eps nullable`, `provider`。
- EXISTING_TABLE_MAPPING：候选 `fundamental_snapshot`，现为 `code/payload/source_chain/coverage/created_at` 快照。
- IMPLEMENTATION_ACTION：验证报告期与指标可追溯后决定 `REUSE`、`EXTEND_OR_MIGRATE` 或 `CREATE`。
- COMPATIBILITY_NOTES：快照 payload 不能未经验证就声称等价于逐期财报；不伪造缺失财务数据。

### 2.9 news_items

- PURPOSE：保存新闻与公告内容及发布时间、来源。
- REQUIRED_FIELDS：`id`, `stock_id`, `title`, `content nullable`, `publish_time nullable`, `source`, `created_at`。
- EXISTING_TABLE_MAPPING：候选 `news_intel`、`intelligence_items`。
- IMPLEMENTATION_ACTION：完成去重和来源语义核对后 `REUSE` 或 `EXTEND_OR_MIGRATE`；若均不兼容才 `CREATE`。
- COMPATIBILITY_NOTES：现有 `snippet/published_date/published_at` 与所需字段并非自动等价；保留旧资讯历史。

### 2.10 research_reports

- PURPOSE：保存券商研报元数据及评级与来源。
- REQUIRED_FIELDS：`id`, `stock_id`, `institution nullable`, `title`, `rating nullable`, `publish_date nullable`, `source`, `created_at`。
- EXISTING_TABLE_MAPPING：`intelligence_items` 可能承载部分资讯，尚未证明独立研报契约。
- IMPLEMENTATION_ACTION：核实类型、机构和评级字段；等价则 `REUSE`，部分等价则 `EXTEND_OR_MIGRATE`，否则 `CREATE`。
- COMPATIBILITY_NOTES：官方接口返回空时保留 `NOT_AVAILABLE/MISSING`，不得由模型补填研报。

### 2.11 collection_tasks

- PURPOSE：记录低频采集任务的运行、来源和错误。
- REQUIRED_FIELDS：`id`, `task_type`, `source`, `status`, `start_time`, `end_time nullable`, `error_message nullable`, `created_at`。
- EXISTING_TABLE_MAPPING：未发现明确通用采集任务表。
- IMPLEMENTATION_ACTION：确认缺失后 `MISSING → CREATE`。
- COMPATIBILITY_NOTES：不得用日志文件替代任务状态表；需能关联证据记录。

### 2.12 analysis_tasks

- PURPOSE：记录分析请求、Prompt/模型版本、创建者与运行状态。
- REQUIRED_FIELDS：`id`, `stock_id`, `task_date`, `prompt_version`, `model_version`, `status`, `created_by`, `created_at`。
- EXISTING_TABLE_MAPPING：未发现明确通用分析任务表；`screening_runs` 为特定筛选运行。
- IMPLEMENTATION_ACTION：确认缺失后 `MISSING → CREATE`。
- COMPATIBILITY_NOTES：不能将旧筛选任务机械迁入；用户身份采用 V1 极简关联。

### 2.13 analysis_reports

- PURPOSE：保存结构化结论及人工确认状态。
- REQUIRED_FIELDS：`id`, `task_id`, `market_view`, `position_status`, `risk_level`, `operation_plan`, `t_strategy`, `reasoning`, `confidence`, `human_confirm`, `created_at`。
- EXISTING_TABLE_MAPPING：候选 `analysis_history`，已有历史分析内容与时间，但不等同于上述全部结构化字段。
- IMPLEMENTATION_ACTION：先核实历史查询契约与字段；可兼容则 `REUSE`/`EXTEND_OR_MIGRATE`，否则最小 `CREATE` 并保留旧表。
- COMPATIBILITY_NOTES：不得修改原始模型输出、已有分析历史或 Phase5 验收状态。

### 2.14 evidence_records

- PURPOSE：让分析结论可反查来源、原始数据、计算和人工确认。
- REQUIRED_FIELDS：`id`, `analysis_report_id nullable`, `source`, `timestamp`, `raw_data_path nullable`, `calculation nullable`, `model_version nullable`, `human_confirm`, `collection_task_id nullable`, `system_run_id nullable`, `created_at`。
- EXISTING_TABLE_MAPPING：已有局部证据字段与日志，但未发现满足完整契约的独立通用表。
- IMPLEMENTATION_ACTION：确认缺失后 `MISSING → CREATE`，并核对现有证据链引用关系。
- COMPATIBILITY_NOTES：`raw_data_path` 指向可追溯原始数据；不得将标准化结果冒充原始返回。

### 2.15 system_runs

- PURPOSE：保存通用运行状态、心跳、锁标识与错误。
- REQUIRED_FIELDS：`id`, `run_type`, `start_time`, `end_time nullable`, `status`, `heartbeat nullable`, `lock_id nullable`, `error_message nullable`。
- EXISTING_TABLE_MAPPING：`screening_runs` 仅覆盖特定筛选运行；未发现等价的通用 `system_runs` 表。
- IMPLEMENTATION_ACTION：确认缺失后 `MISSING → CREATE`。
- COMPATIBILITY_NOTES：保留现有 heartbeat、`runtime.lock` 运行语义，不擅自启用或改造 Scheduler。

### 2.16 data_sources

- PURPOSE：登记正式数据源及优先级、角色和状态。
- REQUIRED_FIELDS：`id`, `name`, `priority`, `role`, `status`, `created_at`, `updated_at`。
- EXISTING_TABLE_MAPPING：未发现正式三源注册表；`intelligence_sources` 是资讯来源，不直接复用。
- IMPLEMENTATION_ACTION：确认缺失后 `MISSING → CREATE`；使用名称唯一约束或等价机制保证后续初始化幂等。
- COMPATIBILITY_NOTES：不删除旧 fetcher 代码，但不把 Tencent、Akshare 写入正式三源注册表。

## 3. 正式 data_sources 初始记录

| name | priority | role |
| --- | ---: | --- |
| `TUSHARE_MCP` | 1 | `historical_daily` |
| `TDX_OFFICIAL_LOCAL_HTTP` | 2 | `realtime_quote` |
| `WORKBUDDY_TDX_MCP` | 3 | `information_fundamental` |

```text
ACTIVE_DATA_SOURCE_COUNT_MAX=3
FORBIDDEN_DATA_SOURCES=Tencent,Akshare,any fourth formal provider
```

初始化必须可重复执行，不产生重复记录；正式活动源最终只能是以上三条。这里是文档定义，本轮未建表、未写入任何数据。

## 4. 数据库保护要求

- migration 前必须对 `stock_analysis.db` 做可验证的**一致性备份**；若有 WAL/SHM，不能仅复制主 `.db` 文件。
- 不删除旧表，不清空历史记录，不修改真实持仓、真实成本、已有分析历史或 Phase5 验收状态。
- 不做破坏性 rename；不得让成熟旧代码因字段替换而失效。
- 每次兼容迁移须具备版本控制或重复执行安全性，并校验主键、外键或等价约束、历史数据与旧入口。
- 任何无法证明兼容的冲突均 `STOP_AND_REPORT`；禁止新建与事实库割裂的平行数据库。

## 5. AWS 迁移关系

`Windows existing SQLite → compatibility migration → validation → controlled copy/migration → AWS SQLite runtime`

AWS 端当前禁止独立从零建事实库。Windows 现有 SQLite 为来源事实；AWS SQLite 仅是受控迁移后的目标运行库。`REALTIME_SYNC=NO`，不将两端伪装为实时双向同步。

阿七原有 `001_init_schema.sql` 与 `002_seed_initial_data.sql`：`OBSOLETE_NOT_FOR_EXECUTION`。保留审计，不执行，不以其创建新的事实库。

本轮仅冻结 Schema 文档；数据库备份、DDL、DML、migration、AWS 建库均未执行。
