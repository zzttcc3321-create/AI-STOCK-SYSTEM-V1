# CODE_REVIEW_PACKAGE — A 部分（R1-D03）

- TASK_NUMBER: R1-D03
- TASK_ID: AI-STOCK-V1-R1-D03-KNOWABILITY
- BASELINE_MAIN: e34456dff23477d1d12039be17a95a1c5178f9d4
- BRANCH: codex/v1-r1-d03-knowability-gate
- GPT B 部分及最终规则判断：待独立审查；本记录只报告代码事实。

## CHANGE_SCOPE

| 文件 | 改动 |
| --- | --- |
| `evidence/contracts.py` | 新增不可变 `TimeFact`、`KnowabilityTimes` 合同；原 EvidenceRecord 字段不变 |
| `evidence/knowability.py` | 新增独立时间事实、精确披露字段映射、时区校验、截止比较及冲突原因 |
| `normalizer/contracts.py` | 引入共享时间合同 |
| `normalizer/information_contracts.py` | 新增披露映射及四时钟规则 ID |
| `normalizer/information.py` | 财报输出独立 `publish_time/knowability`，接受获取/截止时间；资讯保持公开语义未验证 |
| `normalizer/normalizer.py` | 行情标准化接受可选获取/截止时间，按上下文输出四项时间 |
| `analysis/package_builder.py` | 接受/继承历史截止，排除受限记录，传递具体限制；未来持仓快照排除 |
| `tests/test_knowability.py` | 新增 6 个测试方法，含正常、反例和异常子场景 |
| `tests/test_information_normalizer.py` | 原规则集合断言补充两个新增规则 ID |
| 本文件 | 新增 A 部分事实记录；未修改已有冻结文档 |

## CODE_DIFF_SUMMARY

canonical 记录新增 `knowability` 四项独立可读事实，每项都有
`value / source_basis / status`，状态为 VERIFIED、UNVERIFIED 或 CONFLICT。

| 字段 | 语义 | 来源依据 |
| --- | --- | --- |
| `data_period` | 数据所属期，财报为报告期末，不作为披露时间 | `statement.row.日期` 或 canonical 行情期/时间字段 |
| `disclosed_at` | 此版本公开/披露时间 | 原响应行的精确披露字段；缺失/未知语义绝不补用其他时钟 |
| `acquired_at` | 本响应获取完成时间，不是请求开始时间 | 调用方显式 `acquired_at` 上下文；缺省 NULL/UNVERIFIED |
| `judgment_cutoff` | 本次历史判断截止时间 | 调用方显式 `judgment_cutoff` 上下文 |

精确披露候选仅为 `disclosure_time / publish_time / 披露时间 / 披露日期 / 公告日期`。
同一行多个候选的时刻不一致时标为 CONFLICT；不同 offset 表达相同时刻视为一致。
`日期 / 时间 / late / request_timestamp / provider_timestamp` 不冒充公开时间。
日期型披露候选可读保留，但没有时区/日内时刻则 UNVERIFIED；不猜测交易所时区。

财报的 `publish_time` 单独输出明确候选；缺失、非法或冲突为 NULL，日期候选
保留同时在 `disclosed_at.status` 显式未验证。财务数值单位、原 UNVERIFIED
质量及三类 statement_view 规则不变。Raw bytes、原 Evidence、原 archive record 不更新。

`InformationNormalizer.normalize(..., acquired_at=..., judgment_cutoff=...)` 和
`DataNormalizer.normalize/normalize_daily/normalize_market_quotes` 接受独立时间上下文。
`AnalysisPackageBuilder.build(..., judgment_cutoff=...)` 应用 gate；标准化已封存的截止
即使调用方省略也会继承，包括空资讯批次。多个截止上下文不同则拒绝组包。
没有截止上下文时保留既有非历史流程；未知公开时间仍明确 UNVERIFIED。

历史 gate 要求所属期、公开时间和获取时间均已验证，公开/获取时刻不晚于截止；
等于截止允许。所属期晚于截止也排除。公开晚于获取、获取早于绑定 Evidence
请求开始、结构化所属期与业务所属期不一致均视为来源时间冲突。
标准化阶段还拒绝获取早于原请求的输入；原股票/源/tool/raw hash/批次 hash gate 保留。

被排除的业务记录不进入可分析 records，不将其独占 Evidence 加入可引用 index；
`data_quality.knowability_restrictions` 保留路径、四项事实、原因码、来源及被排除
Evidence ID，`usable_at_cutoff=false`。包为 INCOMPLETE，限制为 UNVERIFIED。
PromptBuilder 原有七输入自动传递该质量报告；不修改冻结 Prompt。
持仓快照晚于截止同样排除并报告 incomplete reason，不引入 Provider Evidence。

时间结构进入现有 canonical normalized_data_hash；封存结果递归不可变。
组包截止及限制进入现有 Prompt/input hash。使用现有 JSON sidecar/包结构，
没有 EvidenceRecord、archive 物理格式、数据库表/列/索引或 migration 变更。

## STATIC_ANALYSIS_RESULT

- `git diff --check`: PASS。
- 改动 Python 文件 AST 解析：PASS。
- 冻结文件、`persistence`、`migration` 及 Provider 文件与基线逐字不变。
- 没有新增依赖、网络调用、模型调用或生产持久化路径。

## TEST_RESULT

- 基线：`python3 -m unittest discover -s tests`，665/665 PASS。
- 新增：6 个测试方法及异常子场景。
- 完整实现：`PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover -s tests`，671/671 PASS。
- 最终代码全量用时 93.451 秒；随后补充截止等于披露/获取的子断言，6/6 定向测试 PASS。
- 任务卡写明 665（含新增）；指定基线实际已含 665 个测试，因此完整保留并增加至 671，未删减测试。
- 覆盖：披露早于/等于截止可用；Q1 `report_date=2026-03-31`、披露 2026-04-20、
  截止 2026-04-15 必须排除；公开缺失/非法/日期无时区；获取缺失/晚于截止；
  披露候选冲突/时区等价；披露晚于获取；获取早于请求；历史行情/快照越界；
  截止继承/冲突/非法输入；哈希绑定/不可变；限制进入 Prompt。
- 原全套测试覆盖三源角色、Evidence 原始绑定、输出 gate、人工确认、归档及临时 SQLite 持久化。

## IMPACT_ANALYSIS

- DB_SCHEMA_CHANGE=NO；REAL_WINDOWS_DB_WRITE=NO；AWS_DB_WRITE=NO。
- SOURCE_COUNT=3；新增真实 Provider/模型/交易调用=0。
- 接口为可选 keyword-only 参数；财报/资讯新增 JSON 字段，原字段语义不变。
- 原 EvidenceRecord 六字段分析投影和 archive record 保持；不回填旧数据。
- 性能：逐记录时间解析和比较；沿用原哈希封存，未新增 I/O。

## RISK_FOUND

- 获取完成时刻需要调用方提供真实接收上下文；旧 envelope 仅有请求开始时刻，
  缺失获取时刻保守排除，不能据请求开始推定已获取。
- G0 通用资讯时间语义、财报数值单位仍未验证，不能因本修复宣告已验证。
- 历史 gate 严格排除截止后才获取的数据，当前重拉历史数据不会自动成为当时可用证据。
- 日期型披露无时区/日内时刻仍受限；未来需要明确来源合同才能放宽。
- 新增 JSON 字段需要下游允许 additive read-model 字段；未增加 DB 列。
- 环境原 `.git` 只读，已在 `/tmp` 可写副本提交。CLI GitHub DNS 不可用；
  GitHub `create_tree` 上传被自动审批机制拒绝（需要审批，当前 approval policy=never）。
  无远端提交/PR；另提供可推送的本地分支、patch 和 bundle，发布待可写网络环境恢复。
- 仅提交 A 部分；未合 main，未自行完成 GPT B 审查或 AWS 验证。
