# 股票AI辅助系统｜数据采集规范 V1.0

Status: `FROZEN_FOR_IMPLEMENTATION`

适用基线：`PRODUCT_MANUAL_FREEZE_2026-09-29_V1`、`ARCHITECTURE_FREEZE_2026-09-29_OFFICIAL_DATA_V3`、`DB_SCHEMA_V1.0`。本规范定义目标数据契约，不表示三源 Provider 已实现或云端接入已验证。

## 1. 资源与安全边界

```text
SERVER=AWS Lightsail
SERVER_MEMORY=2GB
DESIGN_POLICY=LIGHTWEIGHT_FIRST
MAX_CONCURRENCY=1
ACTIVE_DATA_SOURCE_COUNT_MAX=3
TRADING_WRITE=FORBIDDEN
```

默认串行执行，仅使用下述三个正式源。所有调用只读；不进行下单、撤单、券商账户写入或自动交易。

## 2. 正式三源与职责

### 2.1 TUSHARE_MCP

```text
ROLE=HISTORICAL_DAILY_PRIMARY
```

负责历史日K、历史行情、复权相关历史数据，是历史日K正式主源。返回须经过日期完整性、字段完整性、复权口径及异常值校验后才可入库或进入分析数据包。

### 2.2 TDX_OFFICIAL_LOCAL_HTTP

```text
ROLE=REALTIME_OPTIONAL_ENHANCEMENT
ENDPOINT=http://127.0.0.1:17709/
TDX_LOCAL_DEPLOYMENT=WINDOWS_ONLY_VALIDATED
TDX_CLOUD_DEPLOYMENT=NOT_VERIFIED
REALTIME_DATA_REQUIRED_FOR_ANALYSIS=NO
IMPLICIT_FALLBACK=FORBIDDEN
```

它是**实时行情类别的正式主源**，但实时数据是分析的可选增强，不是云端核心运行依赖。当前仅验证过 Windows 通达信金融终端加本地 TQ HTTP；不得假设能直接部署到 Ubuntu Lightsail。

本地 TDX 不在线或无法返回可信实时快照时，必须显式设置 `REALTIME_QUOTE_STATUS=MISSING`。系统继续历史日K分析、持仓分析、风险分析、基本面分析和分析数据包生成，但不得声称有实时价格。

禁止自动切换 Tencent、Akshare 或任何第四源；禁止用历史价格冒充实时价格。已有历史价格若作为历史证据使用，必须保留日期和 stale/缺失说明。

### 2.3 WORKBUDDY_TDX_MCP

```text
ROLE=INFORMATION_FUNDAMENTAL_PRIMARY
SERVICE=tdx-connector
```

负责公告、新闻、F10、财务、研报、估值、行业/板块及资金上下文。某类官方数据为空时记录 `NOT_AVAILABLE` 或 `MISSING`，不得用模型记忆补填。云端独立访问能力尚须在实施阶段验证，不能把已在 WorkBuddy 成功调用等同于 AWS 直连已通过。

## 3. 最小采集任务

| 任务 | 唯一正式主源 | 目的 |
| --- | --- | --- |
| `daily_kline_job` | `TUSHARE_MCP` | 采集、校验历史日K |
| `quote_job` | `TDX_OFFICIAL_LOCAL_HTTP` | Windows 本地可用时采只读快照；不可用则标记缺失 |
| `fundamental_job` | `WORKBUDDY_TDX_MCP` | 低频采集公告、财务、资讯及研报上下文 |

任务记录至少关联 `collection_task_id`、`system_run_id`，保存开始/结束时间、`SUCCESS/FAILED/PARTIAL` 状态、错误与 heartbeat；默认 `MAX_CONCURRENCY=1`。

## 4. 历史日K校验与官方备用

```text
PRIMARY=TUSHARE_MCP
FALLBACK=TDX_OFFICIAL_LOCAL_HTTP
```

校验：日期完整性、字段完整性、复权口径、异常值。历史日K缺失时，仅在**已验证的 Windows 本地 TDX 可用**且官方本地数据通过同样校验的前提下使用该官方备用。它已属于三源之一，不是第四源。AWS 云端不得依赖该备用才能继续运行；备用不可用时明确记录缺失/失败，不进行隐式跨源切换，不伪造完整日K。

## 5. 单位标准化

统一标准：`price=元`、`volume=股`、`amount=元`。任何单位不同的原始值，均保存 `raw_value`、`raw_unit`、`normalized_value`、`normalized_unit`、`conversion_rule`，并保留原始响应。单位不明时禁止猜测转换；明确标记未验证。行情进入核心表之前须经过标准化与校验。

## 6. 采集证据链

每次采集至少保存或可追溯：

```text
provider
endpoint_or_tool
method
request_params
request_timestamp
provider_timestamp_or_trade_date
raw_response_path
normalization_rule
validation_result
collection_task_id
system_run_id
```

请求时间与数据时间分别记录。若提供方未返回行情时间戳，不能仅凭请求时间判定数据新鲜。原始响应与标准化值分开保存，分析输入能够反查官方来源。

## 7. 正式运行时点

### 08:30

- 校验上一交易日日K。
- 低频公告/财务增量。
- 生成盘前数据包。

### 11:00

- Windows 本地 TDX 在线时采实时行情；离线则 `REALTIME_QUOTE_STATUS=MISSING`，任务继续。
- 复用已有历史日K，资讯按需增量。
- 生成盘中数据包。

### 16:00

- 本地 TDX 在线时采收盘快照。
- Tushare 尝试补当天日K；未发布则标记 `PENDING`，不得伪造完整状态。

本节为后续调度实施规范；本轮不启动、修改 Scheduler。

## 8. Cloud Independent Mode

AWS Lightsail 7×24 独立运行的目标依赖：`TUSHARE_MCP`、`WORKBUDDY_TDX_MCP`、SQLite、分析任务和 Dashboard。TDX 本地实时行情**不是**云端核心依赖。实施时须分别验证云端两个 MCP 的实际可达性与授权；未验证前不得报告云端能力 PASS。缺失实时行情时沿用显式 `MISSING` 降级，不引入第四源。

## 9. 标准分析数据包

`analysis_package.json` 至少包含：

```text
stock_identity
holding_snapshot
latest_quote
daily_kline_summary
recent_announcements
recent_news
financial_summary
valuation_summary
sector_context
research_summary
data_quality
evidence_index
```

`data_quality` 至少包含：`source_status`、`missing_fields`、`stale_fields`、`fallback_used`、`validation_warnings`、`REALTIME_QUOTE_STATUS`。缺失或不可得的数据保留原状态，不用模型补齐事实。

## 10. Fail-closed 与允许降级

股票身份异常、持仓数据缺失、成本缺失、历史日K严重不完整或关键证据无法追溯时，禁止输出“完整分析”；记录明确失败或不完整状态。仅实时行情缺失时允许分析继续，但必须明确标记 `REALTIME_QUOTE_STATUS=MISSING`，不得称为实时/完整实时结论。

## 11. 轻量约束

禁止引入 Kafka、Redis、Celery、非必要常驻 Worker、高频轮询、全市场扫描、多源竞合或第四正式 Provider。尽量复用已有历史数据与 PRE-QMT 资产，避免重复采集；默认串行执行。本文档不授权 Provider 开发、数据库变更、AWS 部署或交易写操作。
