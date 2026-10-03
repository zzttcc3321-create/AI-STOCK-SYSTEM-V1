# #008 Information canonical — G0 审计、实现与验收

执行日期：2026-10-03。依据：TASK_CARD_008_INFORMATION_CANONICAL v1.0 及补充研报样本。

```text
G0_SHAPE_AUDIT=PASS
SOURCE_FIELD_MAPPING=EXPLICIT_ONLY
FUZZY_FIELD_GUESSING=FORBIDDEN
#008_IMPLEMENTATION=PASS
INFORMATION_CANONICALIZATION=SUPPORTED_CORE_ONLY
SUPPORTED=recent_announcements、recent_news、research_summary、financial_summary
NOT_YET_CANONICALIZED=valuation_summary、sector_context、tdx_ai_listening、wenda_macro_query、tdx_api_data:company
MODEL_EXECUTION=NO
PRODUCTION_DEPLOYMENT=NO
DATABASE_WRITE=NO
FROZEN_DOC_CHANGE=NO
```

支持的是经过明确映射和 Evidence gate 的只读记录，不代表缺失字段已经验证。
**本轮真实财务指标全部保持 NULL**：样本没有单位声明，cash_flow 也没有已冻结的细分口径。
财务支持报告期、表类型和源自带概况；并未宣称支持可用于数值分析的财务指标。
新闻/公告发布时间、研报发布日期、机构、评级也保持 NULL。
这些限制是 nullable 合同的具体处理，不扩充冻结语义，不根据数值大小、正文或标题猜测。
四个支持区块的真实数据质量均为 UNVERIFIED，整体 AnalysisPackage 仍为 INCOMPLETE。

## G0 真实来源与完整性

此前本文记录的研报空样本阻断已由用户提供的非空真实 archive 解除。
实现前先只读审计下面的原始响应、表头、真实记录类型、工具参数及股票身份。
没有用测试 fixture 定义映射，没有新增联网查询，也没有改动 provider。

| 工具 / view | 原始 Evidence 元数据路径 | 真实记录数 |
| --- | --- | ---: |
| wenda_notice_query | `/tmp/verify006r1_evidence/767161505908419a8a862fb533fdb603/816f7fb4d4484713bf06c165a633547d/9555f8f1733b4a16858d95dca04a3932/aa1d90db8f0a4c80bee092a875807d7b.evidence.json` | 5 |
| wenda_news_query | `/tmp/verify006r1_evidence/4fc63b5bc865451f8777c8e46a987ff3/4af6798ff17442668cf51028f2427d90/1a14aa0c75694a40b14900f24d3ab83e/f31018bb9df04ed89e91d1ec17fe00cb.evidence.json` | 5 |
| wenda_report_query | `/tmp/tdx-008-g0-evidence/report_evidence_record.json`；raw=`report_evidence_rawbody.txt` | 5 |
| tdx_api_data / profit | `/tmp/tdx-r2-live-evidence/run_006r2_20261002T165631695248/task_006r2/f10_profit/de34672ae40d46b68f8beee057bda5f1.evidence.json` | 24 |
| tdx_api_data / balance | `/tmp/tdx-r2-live-evidence/run_006r2_20261002T165631695248/task_006r2/f10_balance/666585ca673c429b984cee4314c2ca15.evidence.json` | 23 |
| tdx_api_data / cashflow | `/tmp/tdx-r2-live-evidence/run_006r2_20261002T165631695248/task_006r2/f10_cashflow/0e75bf199897482c8aa6f3d97082f43e.evidence.json` | 23 |

六份原始 bytes 的 SHA-256 均与原元数据一致。研报实际文件为 15,879 bytes，
SHA-256=`7fac19b854622def1831812f773779a3f97d87ee5583b803ebf3b2d0fad30eb1`。
其元数据的 raw_response_bytes=6670 与实际文件长度不一致；没有改写元数据，
不将该长度声明当作完整性证明，按实际 bytes、匹配的 SHA-256、完整 JSON 及 5 条记录验收。
元数据里的 session_id 不复制到 canonical、验证 Evidence 或 Prompt。

notice/news 原请求的 symbol=601518，stock_id=601518.SH；API 返回的记录时间
不受原查询窗口约束，本轮不声称它们是窗口内最新数据，不从含糊时间列做过滤。
研报原请求是 query=吉林高速，stock_id=601518.SH；这是补充样本给定的明确关联，
仅允许这一已审计关联用于无 symbol 的研报 archive，不做公司名识别或泛化猜测。
通常的 #006 Provider 请求仍要求 symbol 与 stock_id 的六位代码一致。

## 真实字段映射表

N（notice/news）：原 JSON-RPC `result.structuredContent.data`，二维数组；
R（report）：补充 archive 根 `data`，二维数组，根 `tool_name=wenda_report_query`。
三者经现有 `TdxInformationProvider._data` 提取均为 `payload.data`，
表头确认为 `[标题, 时间, 链接, 来源, 摘要]`。R 的 `时间` 是日期字符串，
N 的 `时间` 是无时区日期时间字符串，两者都没有已确认的 publication 语义。

F（三种财务 view）：原 JSON-RPC
`result.structuredContent.response.transformed.tables[0].rows`；
Provider 提取后为 `payload.data.transformed.tables[0].rows`。
parser=`statements`，table.name=`statements`，layout=`table`。

“样本非空”不保证接口永不缺失，nullable 字段仍允许 NULL。

| tool / view | 层级 / 真实字段 | 观察类型、可空性 | 时间或指标语义 | canonical 目标 / 无法映射处理 |
| --- | --- | --- | --- | --- |
| notice | N，标题 | str，5/5 非空；核心必需 | 标题 | recent_announcements.title |
| news | N，标题 | str，5/5 非空；核心必需 | 标题 | recent_news.title |
| report | R，标题 | str，5/5 非空；核心必需 | 标题 | research_summary.title |
| notice/news | N，时间 | str，5/5 非空，无时区 | 未声明 publication_time | publish_time=NULL；原值仅留 raw |
| report | R，时间 | str，YYYY-MM-DD，5/5 非空 | 未声明 publish_date | publish_date=NULL；不凭日期形状认定发布时间 |
| notice/news/report | N/R，摘要 | str，样本非空；目标 nullable | 源摘要，不是正文 | 原样 source_supplied_summary；SOURCE_SUPPLIED_SUMMARY=YES / MODEL_GENERATED_SUMMARY=NO；缺失为 NULL/NO |
| notice/news | N，未提供完整正文 | 未提供 | 无 | content=NULL，不将摘要当正文 |
| report | R，来源 | str，样本为机构名称字符串 | 通用来源列，不等同冻结 institution 合同 | institution=NULL，不按标题或来源猜机构 |
| report | R，未提供评级字段 | 未提供 | 无 | rating=NULL，不从摘要推断评级 |
| notice/news/report | N/R，链接、来源 | str，样本非空 | 文章来源不能替换逻辑数据源 | 仅留 raw；不进入 canonical |
| profit | F，日期 | str，24/24 非空，YYYY-MM-DD | statements 报告期，非发布时间 | financial_summary.report_date |
| balance/cashflow | F，日期 | str，各 23/23 非空，YYYY-MM-DD | 同上 | financial_summary.report_date |
| profit | F，营业收入、净利润、基本每股收益 | int/float，样本非空；目标 nullable | 对应 revenue/net_profit/eps 的明确候选，但单位未声明 | 本轮目标 NULL；不使用营业总收入、归属母公司净利润、稀释每股收益替补 |
| balance | F，资产合计、负债合计 | int/float，样本非空；目标 nullable | total_asset/total_liability 的候选，单位未声明 | 本轮目标 NULL；不转换、不推算 |
| cashflow | F，经营活动现金流量净额 | int/float，样本非空；目标 nullable | 经营现金流；冻结 cash_flow 未细分口径 | cash_flow=NULL；不等同于总净现金流 |
| cashflow | F，五、现金及现金等价物净增加额、现金现金等价物净增加额、经营活动现金流量净额2 | 数值或部分期缺失 | 不同口径或补充表字段 | 不作别名、不替补，仅留 raw |
| 三种财务 view | F，无 ROE 对应字段 | 未提供 | 无 | roe=NULL，禁止反算 |
| 三种财务 view | transformed.summary | str，非空；目标 nullable | 源提供表概况 | 原样源摘要并标明两个 summary 标记；不从摘要抽取指标 |
| 三种财务 view | 请求 arguments.entry | str，非空 | 明确 view selector | statement_view=profit/balance/cashflow，作为只读表类型，不合并或计算 |
| 所有样本 | 原 Evidence request_params.stock_id | str，601518.SH，核心必需 | 股票身份 | stock_id；须与包、Provider、wire 参数一致 |
| 所有样本 | 原逻辑源 / 已审计 endpoint | WORKBUDDY_TDX_MCP | 三源逻辑身份 | source（资讯）/provider（财务），不是文章来源 |
| 所有样本 | Evidence request_timestamp | 含 +08:00 的 str，非空 | request / collection clock | 仅保留在来源绑定与 Evidence index，不作为发布时间或报告期 |
| 所有样本 | Evidence provider_timestamp | NULL | provider clock 未提供 | 保持 NULL，不用其他时钟补齐 |

具体财务 entry 映射仅为：

- `TdxShareCW.ph_agf10_cw_lyb` → profit。
- `TdxShareCW.ph_agf10_cw_zcfzb` → balance。
- `TdxShareCW.ph_agf10_cw_xjllb` → cashflow。

其他成本、税费、权益、折旧、现金流流入/流出及 presentation/App 字段均仅留 raw Evidence。
没有声明单位的原值不进入 canonical 数值字段。日后数值支持需要独立确认单位和指标口径，
不能通过给本 Normalizer 传入猜测声明开启。核心 title/report_date 的已观察字段可稳定映射；
其他目标本来允许 nullable，因此 G0 不需要扩大合同，也不需要修改 #006 Provider。

## 合同与 gate

`normalizer/information_contracts.py` 定义不可变 InformationNormalizationResult；
canonical_data、provenance 和其嵌套内容均只读。provenance 不进入 Prompt。
`normalizer/information.py` 只做提取、明确映射、验证及质量标记，无 I/O 写入、网络或摘要生成。

完整链路：

```text
原 #006 / 补充 G0 raw Evidence（保留原 bytes）
  → TdxInformationProvider._data（已有静态解析；无 fetch/network）
  → InformationNormalizer.normalize
  → 不可变 canonical information read model
  → 现有 EvidenceBuilder.build（新 PASS 记录及 canonical hash）
  → AnalysisPackageBuilder.build(canonical_information=...)
  → PromptBuilder.inputs / build（冻结 P3 不变；无模型执行）
```

Normalizer 检查原 bytes/hash/path、逻辑 source、evidence_id、原验证非 FAIL、
股票身份、tool/data_kind、请求参数、原响应解析结果与 Provider.data 相等及时钟一致。
JSON/SSE 支持真实 archive 的外层格式；重复键、非有限 JSON、含多个响应的模糊 SSE 拒绝。
只有明确标题字段和审计过的财务报告期字段可映射，未知字段不会作为别名。

Normalizer 的输出兼容现有 EvidenceBuilder：normalized_data 带通过的验证结果，
canonical_data 的 `sha256_digest` 成为 normalized_data_hash。Gate PASS 只说明原响应、
映射与绑定通过验证；nullable 时间、机构、指标并未完全确认，记录仍为 UNVERIFIED。
原 raw sidecar 保持 NOT_VALIDATED / normalized_data_hash=NULL。
新记录用 supersedes_evidence_id 关联原 evidence_id，保留相同 raw_path/raw_hash，
使用新的 evidence_id；不重写原 Evidence，不把 canonical 内容当成 raw。
补充 report 元数据适配为只读 NOT_VALIDATED EvidenceRecord，stock/query 关联取自用户样本，
没有将其宣称为原先已有 PASS 的 #006 sidecar。

Package 的 canonical_information 接受 InformationNormalizer 产生的 sealed batch 序列，
不接受普通字典、ProviderEnvelope、raw payload 或 dataclasses.replace 后的未校验对象。
每条记录用 `category.N` 的 evidence_links 显式绑定新 PASS Evidence。
Package 对信息要求完整 typed EvidenceRecord，以检查原关联、raw/canonical hash、
stock/tool/参数、路径及时钟；六字段简化元数据不足以支持信息记录。
缺失、NOT_VALIDATED、FAIL Evidence 的信息记录排除且 package=INCOMPLETE；
错误 source、股票、工具、类别、哈希或原证据关联直接拒绝。
同一财务报告期的三个 view 分别保留，不合并、不计算、不推导。

包内只输出冻结六字段 evidence_index，原路径、原请求参数、哈希和 supersedes 关联
保留在外部验证 Evidence。record 关联证据的完整批次 hash，不把单条记录冒充整个响应。
信息记录与 #007 information_availability 的 MISSING/NOT_AVAILABLE 声明冲突时拒绝。
valuation_summary/sector_context 仍只接受显式缺失声明，不能用任意 gate batch填充。
映射后的 title/summary 仍经过既有 safe_json 检查；敏感内容会阻止构包，不能直达 Prompt。

## 使用方式

```python
from normalizer import InformationNormalizer
from evidence import EvidenceBuilder
from analysis import AnalysisPackageBuilder, PromptBuilder

result = InformationNormalizer().normalize(
    envelope, source_evidence=original_evidence, raw_bytes=original_raw_bytes,
)
verified = EvidenceBuilder().build(
    fetch_result=envelope,
    endpoint_or_tool=original_evidence.endpoint_or_tool,
    request_params=original_evidence.request_params,
    collection_task_id=original_evidence.collection_task_id,
    system_run_id=original_evidence.system_run_id,
    normalization_result=result,
    supersedes_evidence_id=original_evidence.evidence_id,
    raw_response_hash=original_evidence.raw_response_hash,
)
links = {f"{result.category}.{n}": [verified.evidence_id]
         for n in range(len(result.canonical_data))}
package = AnalysisPackageBuilder().build(
    stock_identity={"stock_id": result.provenance["stock_id"]},
    canonical_information=[result], evidence_metadata=[verified],
    evidence_links=links,
    source_status={"WORKBUDDY_TDX_MCP": "UNVERIFIED"},
)
inputs = PromptBuilder().inputs(package)
# 只有规范输入生成；不执行模型。实际市场/身份输入仍按 #007 的 Evidence 要求提供。
```

同一 category 有多个 batch 时，记录索引须按 batch 顺序累计，不能各自从 0 覆盖。
原始 bytes 由调用方只读提供；Normalizer 不读/写 Evidence 文件。

## 验收

基线独立执行：`PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover -s tests`，249/249 PASS。
最终相同命令：287/287 PASS（原 249 项 + 新增 38 项）。`git diff --check` PASS。
测试覆盖错误 source/股票/tool/hash/时钟/原证据关联、缺 publication、不推导财务指标、
raw 绕行、敏感源摘要、不可变对象、PASS Evidence 限制、冲突与禁止类别，以及零外部调用。

| 编号 | 验收 | 结果 |
| --- | --- | --- |
| 1 | #007 基线继续通过 | PASS，249/249 保留 |
| 2 | #008 新增测试 | PASS，38/38 |
| 3 | notice 真实转 recent_announcements | PASS，5 条 |
| 4 | news 真实转 recent_news | PASS，5 条 |
| 5 | report 真实转 research_summary | PASS，5 条，publish_date/institution/rating=NULL |
| 6 | profit/balance/cashflow 真实进入 financial_summary | PASS，24/23/23 条；报告期与源概况有效，数值指标 NULL/UNVERIFIED |
| 7 | raw payload 不直入包 | PASS，只有 sealed gate result 可进入 |
| 8 | 未知字段不猜测 | PASS，仅留原 raw |
| 9 | 请求时间不补发布时间 | PASS，出版时间 NULL；provider/request 时钟单独保留 |
| 10 | 缺指标保持 NULL，不计算 | PASS，含原值但单位/口径未验证时也为 NULL |
| 11 | 无 PASS Evidence 不入 Prompt | PASS，排除相应信息且包 INCOMPLETE |
| 12 | Evidence source 错误拒绝 | PASS |
| 13 | 第四源拒绝 | PASS |
| 14 | valuation_summary/sector_context 继续 MISSING/NOT_AVAILABLE | PASS |
| 15 | Prompt 仅 canonical，无 raw/凭据 | PASS，真实 Prompt input 与敏感内容反例均核查 |

真实验收使用 dev-box 已有 append-only Evidence 重放，不用 fixture 代替真实数据：

```text
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=. python3 /tmp/verify_information_008.py
REAL_END_TO_END_ACCEPTANCE=PASS
notice=5, news=5, report=5, profit=24, balance=23, cashflow=23
CANONICAL_RECORD_COUNT=85
ORIGINAL_FILES_UNCHANGED=YES
RAW_OR_CREDENTIALS_IN_PROMPT=NO
PACKAGE_STATUS=INCOMPLETE
```

最终新建验收目录：`/tmp/tdx-008-acceptance/72217504d6c64e6393ce178ee028586a/`。
该目录含 `acceptance.json`、六份 `*.verified.evidence.json`、六份 `*.canonical.json`、
`analysis_package.json`、`prompt_input.json`。acceptance.json 保存每个原路径、原 ID、
实际 raw 长度、raw SHA-256、canonical hash、新 PASS ID 和真实链路结果。
每份样本都完整经过 Provider 解析 → Normalizer → canonical → Evidence → Package → Prompt；
并在结束时复核原 raw 和原元数据文件的哈希未变。
仅向新的本地验收目录追加文件，不写数据库或分析结论，不调用模型，不部署。

仓库变更仅限任务卡白名单的八个文件；providers、bridge、evidence、冻结文档与 schema 均未修改。
已有未跟踪的 __pycache__ 目录保留，不作为本任务代码变更。
