# #007 Analysis Package & Prompt Assembly Foundation

本模块只组装 canonical 分析输入和冻结 P3 Prompt，不调用模型、不生成分析结论、不写数据库或人工确认状态。唯一依赖方向：Provider → Normalizer/Canonical Store → Evidence → AnalysisPackageBuilder → PromptBuilder。

## 接口与唯一内部结构

`analysis.AnalysisPackageBuilder.build(...) → AnalysisPackage`，随后 `analysis.PromptBuilder.build(package) → PromptEnvelope`。`PromptBuilder.inputs(package)` 返回冻结的七字段映射；没有第二套可独立构造的业务数据包。

输入参数：

- `stock_identity`：必须包含非空 `stock_id`，可带 `symbol`、`market`、`name`、`industry`；每条行情和持仓必须匹配相同证券身份。
- `canonical_data`：只接收 `daily_kline` 和 `market_quotes` 两个具名集合；传入 Normalizer 的 `canonical_data` 子集或满足相同契约的持久化记录。不得传入完整 NormalizationResult、ProviderEnvelope、Provider 实例、资讯 payload 或 raw 内容。行情可带已有持久化元数据 `id/created_at/updated_at/request_timestamp`，不进行数据库读取或字段迁移。
- `holding_snapshot`：显式 `NULL` 表示无持仓；非空时使用冻结 holdings 字段 `stock_id/quantity/cost_price/snapshot_time`，可带 `market_value/profit_loss/profit_loss_pct`。数值缺失保留 NULL、标记缺失，不计算盈亏；未知旧字段语义不自动映射。
- `evidence_metadata`：六字段 metadata 映射，或已有 `EvidenceRecord` 实例。类型化 EvidenceRecord 只投影六字段，不读取其原始路径、请求参数或响应内容；完整 sidecar 映射必须由调用方先投影为 metadata。
- `evidence_links`：股票身份、持仓分别使用 `stock_identity`、`holding_snapshot`；行情逐条使用 `daily_kline.N`、`market_quotes.N`，N 是传入集合的下标。值为 Evidence ID 列表。
- `source_status`：三源的已整理状态，仅接受 `PASS/WARNING/UNVERIFIED/MISSING/NOT_AVAILABLE/FAILED`；未声明的源按 MISSING 保留。Provider 可用性枚举不在这里静默转换。
- `information_availability`：六个资讯/财务类别的 `{status, reason}`，仅接受 MISSING 或 NOT_AVAILABLE，保留原状态和原因；默认说明资讯 canonical 化尚待 #008。
- `missing_fields/stale_fields/validation_warnings`：显式、已脱敏的质量信息，去重排序。历史缺口由上游声明；本层不推断交易日历、不插值、不补拉。
- `fallback_used`：NULL 或明确的 `{from_source, to_source, reason, validated}`。只允许 TUSHARE_MCP → TDX_OFFICIAL_LOCAL_HTTP 的历史日K备用，`validated=true` 且原因非空，必须确有备用记录。不得借此给实时行情或资讯切换来源。

示意调用（以下变量均由上游 canonical/Evidence 边界提供）：

```python
from analysis import AnalysisPackageBuilder, PromptBuilder

package = AnalysisPackageBuilder().build(
    stock_identity=identity,
    canonical_data={"daily_kline": canonical_rows},
    holding_snapshot=None,
    evidence_metadata=evidence_metadata,
    evidence_links=record_evidence_links,
    source_status={
        "TUSHARE_MCP": "PASS",
        "TDX_OFFICIAL_LOCAL_HTTP": "MISSING",
        "WORKBUDDY_TDX_MCP": "MISSING",
    },
)
envelope = PromptBuilder().build(package)
```

## 数据包与 P3 映射

AnalysisPackage 覆盖 DATA_COLLECTION_SPEC §9 的全部十二字段。业务块统一为 `{status, records, reason}`；`records` 中每条为 `{data, evidence_ids}`。此处 summary 保留已校验记录，不生成指标、摘要事实或投资判断。另带路由标识 `stock_id` 和 `position_mode`。

| P3 固定输入 | 唯一 AnalysisPackage 来源 |
| --- | --- |
| stock_id | stock_id；stock_identity 的业务数据及证据保留在 canonical_market_data 中 |
| holding_position | holding_snapshot；无持仓时为 NULL |
| canonical_market_data | stock_identity、daily_kline_summary、latest_quote |
| financial_data | financial_summary |
| intelligence_data | recent_announcements、recent_news、valuation_summary、sector_context、research_summary |
| evidence_index | 本次实际保留数据引用的 Evidence metadata |
| data_quality_report | data_quality |

`holding_snapshot=NULL → position_mode=WATCH_ONLY，analysis_mode=WATCH_ANALYSIS`。非空快照为 `HOLDING_ANALYSIS`；快照因缺证据被排除时，仍保留持仓分析模式和缺失标记，不能把“持仓输入不可用”伪装成“无持仓”。

## Gate、质量与 Evidence

只允许 TUSHARE_MCP、TDX_OFFICIAL_LOCAL_HTTP、WORKBUDDY_TDX_MCP；任何输入位置的第四源均拒绝。行情使用现有 canonical 字段、有限数值和合法时间；重用 Normalizer 的纯校验函数检查价格/量/复权硬规则，不进行 raw 标准化。FAILED 行情、失败来源携带的数据、FAIL Evidence、隐式备用、重复行情主键、身份不匹配或未验证复权伪装成 PASS 均拒绝。实时快照必须由调用方明确选择一条，不由本层推测哪条“最新”。

每条保留的业务记录都必须有可解析、校验 PASS 且来源匹配的 Evidence 引用。Evidence 索引至少包含 `evidence_id/source/request_timestamp/provider_timestamp/validation_result/human_confirm`；请求时间与提供方时间分开保留，PENDING/CONFIRMED/REJECTED 不被修改。缺 Evidence 或 NOT_VALIDATED 的数据被排除，缺口显式记录，包标记 INCOMPLETE。证券路由标识可以保留以标识不完整任务，但无证据的身份名称、行情和持仓事实不会进入 Prompt。

`data_quality` 保留 `source_status/missing_fields/stale_fields/fallback_used/validation_warnings/REALTIME_QUOTE_STATUS`，另提供 `DATA_AVAILABILITY_REPORT/data_quality_status/package_status/complete_analysis_input/incomplete_reasons`。WARNING/UNVERIFIED 明确保留。报告中的 PASS 汇总描述可用数据的质量，不代表完整性；完整性必须看 `package_status/complete_analysis_input`。

实时缺失或快照 freshness 为 STALE/UNKNOWN 时，`REALTIME_QUOTE_STATUS=MISSING`；不删除已有证据的日K，不构造当前价格。冻结 §5.2/§5.3 文案进入 System Prompt，明确禁止实时结论和推测当前价格。NULL、MISSING、NOT_AVAILABLE 不补值；完整输入判定对历史缺口、缺失持仓数值和关键证据采取 fail-closed。

递归白名单及敏感字段/文本校验阻止 raw、字节、opaque 对象、认证信息进入包。异常不回显输入值。输入映射被复制并递归冻结；`to_dict()` 返回独立 JSON 结构。PromptBuilder 只接受实际经过 Builder Gate 的不可变对象；内部完整性摘要不出现在序列化契约里。它是进程内检查，不是对外部数据的认证；持久化包必须重新经过 Builder，不得直接反序列化为可信对象。Evidence 链接的事实关联由 canonical/Evidence 调用方提供，本层检查引用、来源和验证状态，不新增证据或伪造来源。

## 冻结模板和确定性

Prompt 文案直接读取 `docs/frozen/P3_PROMPT_TEMPLATE_V1.0.md`。System Prompt 使用 §6.1 原文，并附 §2.3 模式、§3.2 三级建议、§4 人工确认与 §5 缺失处理的原文；User Prompt 为模式/版本 metadata、七字段 JSON 和 §6.2 的原文指令。冻结示例中的持仓、股票、日期和 model_version 不被当成事实。

版本使用冻结声明 `VERSION=V1.0`，记录为 `PROMPT_TEMPLATE_V1.0`；文件中示例的 `_DRAFT` 标识和历史修订说明不改变实际冻结版本。模板完整文件 SHA-256 被固定校验，变化时必须走版本评审，不能悄悄沿用版本。未改动冻结文件。

序列化采用 UTF-8、排序键、固定分隔符、禁止 NaN/Infinity；Evidence、质量列表去重并稳定排序，行情保持调用方顺序。相同输入产生完全一致的包、Prompt 和 input_hash。Hash 覆盖实际 system_prompt/user_prompt、版本、模式、证券、Evidence IDs 和模板摘要；不使用时钟、UUID 或随机数。

PromptEnvelope 提供 `prompt_version/stock_id/position_mode/analysis_mode/system_prompt/user_prompt/evidence_ids/input_hash/template_hash`，以及 `NETWORK_CALL_COUNT=0/MODEL_CALL_COUNT=0/DATABASE_WRITE=0`。这些值表示本纯组装模块的执行边界；离线测试同时拦截网络、Provider.fetch、数据库连接及文件写入，核对无调用，并检查无模型客户端依赖。

## 已知缺口与范围合规

`KNOWN_GAP_INFORMATION_CANONICALIZATION=YES`。#006 information provider=YES、真实接口已验证=YES；信息类 canonical 化=NOT YET，可直接进入 Prompt=NO。已有资讯接口成功不等于资讯契约已完成。

#007 没有另造财务/资讯 canonical contract；即使给 raw payload 加 PASS 标签也不能进入包。财务、公告、新闻、估值、行业和研报只提供显式 availability。由于这些 canonical 字段尚缺，当前包一律不会声称七字段业务事实已完整；有证据的历史数据仍可进入部分输入 Prompt。后续 #008 提供契约后另行扩展 Gate。

仅修改任务白名单内的四个 analysis 文件、两个新增测试文件及本文档。未修改 providers/bridge/normalizer/evidence/docs/frozen 或 schema/migration；未新增 LLM/API 调用、分析报告/人工确认写入、Dashboard、Scheduler、部署、交易或券商接口。

验证命令：`python3 -m unittest discover -s tests`；对变更 Python 文件执行 `python3 -m py_compile`；对变更文件执行凭据模式扫描和 `git diff --check`。新增测试覆盖任务卡十五项验收所需的可执行行为与依赖边界；真实接口复测和后续模型输出验证不属于 #007。

本次离线验证：完整套件 **245/245 PASS**（原有 180 项 + 新增 65 项），六个变更 Python 文件 py_compile PASS；凭据名称 grep 命中仅为拒绝规则和明确的离线负向测试，凭据值模式扫描 **0 findings**。
