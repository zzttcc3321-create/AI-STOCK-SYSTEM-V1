# CODE_REVIEW_PACKAGE — A 部分（R1-D04）

- TASK_NUMBER: R1-D04
- TASK_ID: AI-STOCK-V1-R1-D04-FRESHNESS
- BASELINE_MAIN: fa181c991fa09b0d73bf61c610c054602ec4b958
- BRANCH: codex/v1-r1-d04-freshness-gate
- GPT B 部分及最终规则判断：待独立审查；本记录只报告代码事实。

## CHANGE_SCOPE

| 文件 | 改动 |
| --- | --- |
| `normalizer/contracts.py` | 新增版本化 `FreshnessCheck` 和两个固定阈值；保留原 freshness 枚举 |
| `normalizer/normalizer.py` | 行情 freshness 默认 UNKNOWN，结合请求/获取/判定时间进行保守校验 |
| `normalizer/validation.py` | 新增 freshness 原因转为可读 WARNING，沿用已有校验状态 |
| `analysis/package_builder.py` | 行情可选字段允许 `freshness_check` 透传；仅改一行 |
| `tests/test_freshness.py` | 新增 9 个测试方法，含四类场景的正常例、反例及端到端断言 |
| 本文件 | 新增 A 部分事实记录；未修改已有冻结文档 |

## CODE_DIFF_SUMMARY

旧逻辑在未声明 freshness 时仅因存在 timestamp 就赋 REALTIME。
新逻辑只有明确 REALTIME 且通过全部时点校验时才保留 REALTIME。
未声明/非法声明默认 UNKNOWN；有明确过期依据可降为 STALE。
明确 UNKNOWN/STALE 不自动升级为 REALTIME。

合同版本为 `QUOTE_FRESHNESS_V1.0`，规则在 `FreshnessCheck` 中说明，
各行情记录的 `freshness_check` 同时输出版本、阈值、原时点和原因。

| 项目 | V1.0 规则 |
| --- | --- |
| 行情时钟来源 | 原响应行显式 `quote_timestamp`；字段不存在时才用 envelope `provider_timestamp`；显式 NULL 保持缺失 |
| 参考时点优先级 | caller `judgment_cutoff` → caller `acquired_at` → envelope `request_timestamp` |
| 时间格式 | ISO timestamp，必须含明确时区；按真实时刻比较，不推测时区 |
| 过期阈值 | 年龄 > 300 秒为 STALE；等于 300 秒允许 |
| 未来容差 | 0 秒；行情晚于参考时点，或晚于显式获取完成时间，均为 UNKNOWN/异常 |
| 请求上下文一致性 | 参考时点必须位于 [请求时间, 请求时间 + 300 秒]；行情不能晚于请求 300 秒，否则 UNKNOWN/受限 |
| 多重限制 | 缺失/非法时钟、未来或请求冲突优先输出 UNKNOWN；过期原因仍保留 |

保留 `quote_timestamp / timestamp_source / declared_freshness / request_timestamp /
acquired_at / judgment_cutoff / reference_time / reference_source / age_seconds /
reason_codes`；没有使用隐式系统当前时钟，没有用 collection 时钟补披露事实。

原因包含 `FRESHNESS_UNDECLARED / INVALID_FRESHNESS_DECLARATION /
QUOTE_TIMESTAMP_MISSING / QUOTE_TIMESTAMP_INVALID / REFERENCE_TIME_MISSING /
REFERENCE_TIME_INVALID / REQUEST_TIMESTAMP_MISSING / REQUEST_TIMESTAMP_INVALID /
STALE_QUOTE_TIMESTAMP / FUTURE_QUOTE_TIMESTAMP / QUOTE_AFTER_ACQUISITION /
REQUEST_TIME_MISMATCH / DECLARED_UNKNOWN / DECLARED_STALE`。

受限行情仍能保留可读 canonical 记录，校验质量为 WARNING；freshness 原因进入
package 的 `validation_warnings`，sidecar 随行情传到 Prompt 的 canonical_market_data。
已有 realtime gate 继续把 STALE/UNKNOWN 列为 stale_fields，caller
`REALTIME_QUOTE_STATUS=MISSING`，不会得到实时 PASS。
R1-D03 历史 knowability 不足的记录继续按原规则排除。

新增 sidecar 进入既有 canonical normalized_data_hash；原 raw snapshot 保留。
行情外的标准化流程、EvidenceRecord、Provider、归档与持久化接口不变。
package 只允许这个额外行情字段，不增加新来源或跳过历史 gate。

## STATIC_ANALYSIS_RESULT

- `git diff --check`: PASS。
- 5 个改动/新增 Python 文件 AST 解析：PASS。
- R1-D03 日线 normalize、daily record、行情 knowability 构造、historical gate、
  historical identity gate 的 AST 与基线一致。
- `evidence/knowability.py`、共享 Evidence 合同、InformationNormalizer 及其合同与基线逐字一致。
- 冻结文档、persistence、migration 和 Provider 文件无变更。
- 没有新增依赖、网络调用、模型调用或真实持久化路径。

## TEST_RESULT

- 基线包含 677 个测试；新增 9 个方法及多项 subTest/边界断言。
- 定向 freshness：9/9 PASS。
- 最终全量：`PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover -s tests`，
  **686/686 PASS，100%，108.663 秒**。
- 原 12 个 knowability 测试方法随全量通过；没有改动或删除原测试。
- 四类主场景均含正常例与反例，并贯穿 normalizer → typed Evidence → package → Prompt：
  缺失/存在行情时间；过期 301 秒/阈值 300 秒；未来 1 微秒/等于参考时点；
  请求上下文越界/300 秒边界一致。
- 另覆盖未声明但有新旧时钟、请求后的正常采集延迟、获取之后的未来行情、
  来源 fallback/显式 NULL、参考时点优先级、时区等价、非法/无时区时钟、
  非法声明、UNKNOWN/STALE 不升级、raw 不变、时点独立、Evidence hash 包含 sidecar。
- 原测试继续覆盖三源、输出 gate、人工确认、归档及临时 SQLite；无真实库写入。

## IMPACT_ANALYSIS

- DB_SCHEMA_CHANGE=NO；REAL_WINDOWS_DB_WRITE=NO；AWS_DB_WRITE=NO。
- SOURCE_COUNT=3；新增 Provider/模型/交易调用=0。
- 无新增调用参数；沿用 R1-D03 的可选 acquired_at/judgment_cutoff。
- canonical 行情新增 JSON sidecar；原 freshness 三值接口不变。
- package 的既有七输入和 Evidence 六字段投影不变；不回填旧记录。
- 每条行情固定次数解析/比较时间；无新增 I/O。

## RISK_FOUND

- 300 秒是此合同的保守固定阈值，覆盖 collection age；不按市场休市或来源自动放宽。
- 无调用方判定/获取时点时，只能以 envelope 请求时点验证；这是该请求的快照判断，
  不是日后永久实时证明。调用方应提供本次判断时点，不能把旧 envelope 当作新请求。
- 显式 REALTIME 声明仍是来源声明；时点通过不等于证实行情事件时间或披露语义。
- 无时区/非法 quote 时钟保留原值并明确 UNKNOWN；package 原 timestamp gate 仍会拒绝非法 canonical 时钟。
- 旧手工 canonical 记录不要求 sidecar，保留既有接口；本修复覆盖 DataNormalizer 接入输出。
- 未声明 freshness 的原接入将从隐式 REALTIME 降为 UNKNOWN 或 STALE；这是预期修复。
- canonical hash 会包含新增 sidecar；已有封存记录不改写。
- 当前挂载 `.git` 只读，工作分支在 `/tmp/AI-STOCK-SYSTEM-V1-R1-D04`。
  Git CLI 无法解析 GitHub 域名；通过 GitHub connector 核实远端 main，并导入签名基线
  的 byte-exact commit，校验 SHA 与 tree 均一致。
- 发布未完成：`git push` 因 DNS 失败；GitHub connector `create_tree` 返回
  `MCP tool call requires approval, but approval policy is never`，自动审批机制拒绝上传。
  没有远端分支提交或 PR；交付本地提交、format-patch 与可导入目标分支的 git bundle。
- 仅提交 A 部分；未合 main，未自行完成 GPT B 审查或 AWS 验证。
