# TDX Cloud Information Provider — #006

TASK_ID: `AI-STOCK-V1-WORKBUDDY-006`
分支：`codex/v1-tdx-information` → `main`。仅通过 PR 交付；不合并、不发布。

## 源身份和职责

```text
LOGICAL_SOURCE=WORKBUDDY_TDX_MCP
ACTUAL_PROVIDER_SERVICE=TDX_OFFICIAL_CLOUD_MCP
ENDPOINT=https://txmcp.tdx.com.cn:3001/txmcp
DATA_SOURCE_COUNT_REMAINS=3
ARCHITECTURE_CHANGE=NO
SOURCE_ACCESS_PATH_CHANGE=YES
FOURTH_SOURCE_FORBIDDEN=YES
```

`TdxInformationProvider` 实现已有 `Provider` 接口，保持内部逻辑源身份，云端直连是唯一路径，无桌面客户端依赖。历史日线与实时行情继续由已有两个 Provider 负责。本次不修改冻结契约、normalizer、evidence 模块、数据库或 AWS/生产配置。

| 真实 MCP tool | 冻结 data_kind | 职责 |
| --- | --- | --- |
| `wenda_notice_query` | `announcement` | 公告 |
| `wenda_report_query` | `research` | 研报 |
| `wenda_news_query` | `news` | 资讯 |
| `wenda_macro_query` | `news` | 宏观信息，保留 tool 区分 |
| `tdx_api_data` | `financial` | F10/财务，额外限制参数 |
| `tdx_ai_listening` | `news` | 资讯聚合，仍须明确业务时间 |

冻结契约没有 `macro`/`f10` 类别，因此使用上述现有类别。不新增逻辑源。`tdx_quotes`、`tdx_kline`、交易、下单、账户和其他 tool 在 Provider 层拒绝，不建立连接。

## Task Card §2 验证记录与证据边界（原文）

以下为任务卡提供的前置验证记录，不是本次执行重新实测。本次仅使用合成数据和 mock transport，未访问真实 MCP endpoint。

> 第三个正式数据源 WORKBUDDY_TDX_MCP（information_fundamental 职责：
> 公告/研报/资讯/宏观/F10/财务）的 Provider 实现。
>
> 2026-10-02 dev-box 前置验证（阿七实测，非臆测）：
> - Endpoint：https://txmcp.tdx.com.cn:3001/txmcp
> - 协议：MCP Streamable HTTP（JSON-RPC 2.0）
> - 鉴权：Authorization: Bearer <API Key>（API Key 即通达信"词元 Key"，
>   需同时勾选"问小达MCP"+"通达信词元"权限；TDX_MCP_API_KEY 只从环境变量读）
> - 握手：initialize → Mcp-Session-Id → tools/list → tools/call，全通
> - 工具：20 个，含 wenda_notice_query（公告）、wenda_report_query（研报）、
>   wenda_news_query（资讯）、wenda_macro_query（宏观）、tdx_api_data（F10）、
>   tdx_ai_listening（24h资讯聚合）
> - 实测：wenda_report_query 查"吉林高速"返回真实研报
>   （标题/时间/链接/来源/摘要，如西南证券月报）
> - 自动化：纯 HTTPS + 静态 Key，无需桌面客户端/微信登录，
>   dev-box/AWS 上 cron 定时跑即可，7x24 成立
>
> 证据边界（GPT 要求如实标记，不得夸大）：
> - DEV_BOX_RUNTIME_VERIFIED=YES（上述实测）
> - PUBLIC_OFFICIAL_DOC_VERIFIED=PARTIAL（公开文档只证明通达信官方
>   量化平台提供金融数据/HTTP 能力，未直接证明该 endpoint 即官方服务）

## 调用和协议

构造器必须指定本地 `evidence_root`。生产构造时 Key 仅从 `os.environ["TDX_MCP_API_KEY"]` 读取，缺失或包含空白时立即报配置错误，错误文本不包含值。`environ` 参数仅作为合成测试环境注入点，没有 Key 参数、配置文件读取或日志输出。不要在仓库内生成 `.env` 或写入真实 Key。

下面是请求形状示例；只供理解接口，不是本次执行的联网操作，也不是已验证的真实工具参数 schema：

```python
from providers import TdxInformationProvider

# 调用方选择仓库外的本地证据输出目录；环境已提供凭据。
provider = TdxInformationProvider(evidence_root="/tmp/tdx-information-evidence")
request = {
    "stock_id": "601518.SH",
    "data_kind": "research",
    "tool": "wenda_report_query",
    "arguments": {
        "stock_code": "601518.SH",
        "bdate": "20261001",
        "edate": "20261002",
    },
    "system_run_id": "run_006",
    "collection_task_id": "task_006",
    "request_id": "request_006",
}
# 在已核对工具 schema 的获授权运行环境中，才执行 provider.fetch(request)。
```

- 连接惰性建立：`initialize` → `notifications/initialized`（202 空 ACK）→ `tools/list` → `tools/call`。
- `Mcp-Session-Id` 从初始化响应读取，后续请求携带；协商并发送 `MCP-Protocol-Version`。支持 `2025-03-26`、`2025-06-18`、`2024-11-05`，未知版本拒绝。
- `health_check()` 仅握手和工具发现，无 `tools/call` 或业务取数。控制面证据也写入本地文件。
- HTTPS endpoint 固定，使用 urllib 默认 TLS 验证及继承的代理配置。拒绝重定向，避免 Bearer 转发。默认每次 HTTP 操作超时 30 秒，可传入正有限数值；该值不是整个多次握手流程的总 deadline。
- `tools/list` 最多 8 页；发现工具不代表允许调用，仍以本地 tool 白名单为准。
- JSON-RPC 必须版本为 2.0、id 匹配、result/error 二者仅有一个。重复 JSON 字段、非有限数字、MCP `isError`、业务层非零 `code` 都拒绝。
- 支持 JSON 响应和有限 SSE 响应；SSE 按 RPC id 选择唯一结果，保留完整已读取 wire bytes。无后台 SSE 订阅、服务器主动请求处理或常驻监听。
- 业务数据支持 `structuredContent` 或单个 JSON text content；接受 `data` 包装或列表。未知文本/媒体格式安全失败并保留 raw，不猜测字段。

所有请求必须透传明确的 `bdate`/`edate`，或 `time_range={"start": 日期, "end": 日期}`；日期支持 YYYYMMDD 和 YYYY-MM-DD，开始日期不得晚于结束日期，不能同时提供两种范围。`latest` 不被接受。调用方提供服务器实际需要的其他参数，本实现不会由日期或股票推导自然语言查询。若提供 `stock_code`，必须等于外层 `stock_id`；宏观/资讯聚合可省略 stock_id。

`tdx_api_data` 额外仅接受 `api_name`、`stock_code`、时间范围字段，且 `api_name` 限于 `f10`/`financial`，`stock_code` 必须匹配。这是本地保守安全词汇，任务卡未提供其真实 schema；并非声称真实服务已经接受这些值。任何其他方法、fields 或嵌套通用 API 参数均拒绝。上线前应由人工利用获授权的 schema 核对具体信息子接口；当前执行不能用真实 endpoint 补证。

## 原始证据与结果

每个非空 HTTP 响应及异常空响应，在协议/业务解析前写入：

```text
<evidence_root>/<system_run_id>/<collection_task_id>/<request_id>/<uuid>.json
<evidence_root>/<system_run_id>/<collection_task_id>/<request_id>/<uuid>.evidence.json
```

`xb` 创建新文件，不覆盖旧记录。第一份为原始 bytes，第二份为元数据。`payload["evidence"]` 返回所有尝试的 `{record, raw_bytes, http_status, raw_response_complete, actual_provider_service}`；`record` 通过冻结 `EvidenceBuilder` 构建后转为同字段的可序列化副本，避免冻结验证器 `asdict` 对不可变映射的复制问题。

证据中 `source=WORKBUDDY_TDX_MCP`，`endpoint_or_tool` 为真实 endpoint 加真实 tool（例如 `https://txmcp.tdx.com.cn:3001/txmcp#wenda_report_query`）；同时记录真实服务标识、请求参数、stock_id、请求日、SHA-256(bytes)、原始路径及系统/任务标识。原始哈希显式传入 Builder，不用解析后的 JSON 哈希替代。会话和鉴权头不进入证据。正常成功的控制面和业务响应都留存；会话重试的两次响应也保留。

异常/错误只留证据，不进入 `payload["data"]`。成功只提供源数据 `data`，不声称完成字段标准化；`canonical_data` 始终为空。冻结 normalizer 尚无信息类标准化入口，本次不扩展它。证据标准化状态保持 `NOT_VALIDATED`，human_confirm 保持 `PENDING`，provider_timestamp 未确认时保持 None，不用采集时间冒充。

请求敏感字段、Key 值及嵌套敏感参数在联网前拒绝。若服务器把当前 Key 原样或以 Unicode 转义形式回显，返回 `VALIDATION_FAILED`，该响应不落盘、不入 Evidence；R6/用户的凭据禁入要求优先于 R7 raw 保留要求，不写脱敏伪 raw。HTTP body 最多读取 1 MiB + 1 byte；超限保留已读取前缀并标记 `raw_response_complete=false`，返回 `VALIDATION_FAILED`，不得把前缀当完整成功响应。正常范围内 raw 为完整响应；空 202 notification ACK 无 JSON-RPC body，不建记录。文件写入失败返回 `NOT_AVAILABLE`，不报告成功；已写入的前置 raw 不回滚。这里不引入跨文件事务或生产存储。

## 故障验收（全部 mock）

| 项目 | 触发方式 | 预期结果 | 实际结果 | 证据 / 测试 |
| --- | --- | --- | --- | --- |
| MCP 不可达 | ConnectionRefusedError / URLError | NOT_AVAILABLE，不抛异常 | 通过 | `test_connection_refused_and_dns_not_available_do_not_raise`；无响应无伪 raw |
| Key 错误/过期 | mock HTTP 401/403 | AUTH_FAILED，不泄露 Key | 通过 | `test_401_and_403_are_auth_failed_raw_retained`，raw 和 sidecar 留存 |
| 请求重复 | 参数重排、不同 request_id，同查询日 | 一次 tools/call / 一次证据 effect | 通过 | `test_same_query_has_one_effect_reordering_params_and_new_request_id` |
| 空 data | [] / {} / None | 空结果及 Evidence，不补填 | 通过 | `test_empty_data_has_evidence_no_fabrication_and_is_deduplicated`；NOT_AVAILABLE |
| 畸形 JSON-RPC | 非 JSON、数组、错 id、重复字段 | VALIDATION_FAILED，原 bytes 留证 | 通过 | `test_malformed_json_rpc_preserves_exact_raw` 等 |
| 会话失效 | 会话请求 mock 404 | 重握手一次；仍 404 则 TIMEOUT | 通过 | 两个 session expiration 测试；两次工具调用证据留存 |
| 超时 | TimeoutError / URLError(TimeoutError) | TIMEOUT，无旧结果兜底 | 通过 | `test_timeout_and_wrapped_timeout`；跨日失败无旧值 |
| 非零业务错误 | code=7 / isError / JSON-RPC error | 无 data/canonical，只有 raw | 通过 | nonzero / rpc_error / outer_business_error 测试 |
| 行情/交易越界 | tdx_quotes / tdx_kline / place_order / 通用 F10 越界参数 | 本地拒绝，零 HTTP effect | 通过 | quotes / generic_f10 两个边界测试 |

若重握手遇到明确 AUTH_FAILED/NOT_AVAILABLE/TIMEOUT，保持对应故障原因，不用 TIMEOUT 掩盖鉴权或不可达。普通故障不自动重试、不切换其他源、不提供旧结果。下一次显式 fetch 可重试失败查询。

## 幂等、资源和运行限制

去重键为 SHA-256(`stock_id + tool + SHA-256(排序后的完整参数 JSON) + 中国时区请求日`)。缓存成功/空结果；缓存命中返回原请求时间、原 Evidence 和原 request_id 的副本，重复任务不新建证据。调用方不能修改缓存内容。失败不缓存，跨日清空；更换 tool、参数、股票或请求日会查询一次新结果。

最多 64 个条目，按 raw/参数大小保守乘 8 估算后限制缓存预算 8 MiB；超过预算不缓存，LRU 淘汰后也会重新查询。幂等范围为单进程、当日且仍驻留缓存的查询；不是跨重启/跨进程的 exactly-once 保证。磁盘证据保留不自动清理。调用方若长期保留所有 envelopes，其内存/存储另行管理。

设计为一个串行脚本，由既有 cron/systemd timer 调度。不安装 Redis/Celery/Kafka，无后台线程。目标常驻内存 <100 MB；有限响应、分页和缓存服务于该目标，不声明真实服务负载下的 RSS 已实测。没有修改或部署任何 timer、AWS 或数据库配置。

## 本次全量测试记录

`unittest.defaultTestLoader.discover("tests")` 全量运行 130 个测试（既有 79 + 新增 51），130/130 通过；整个发现与执行流程同时 mock `socket.socket` 和 `socket.create_connection`，任何真实 TCP 调用立即失败。新增测试自身也有 module 级 socket 守卫；urllib wire 测试只 mock opener/response。未访问真实 endpoint，也未读取真实 TDX Key。

```text
Ran 130 tests in 1.148s
OK
Peak test-process RSS (KiB): 29856
```

这个 RSS 仅代表合成测试进程，不代表生产负载实测。

## 本次 Execution Environment / Boundary Check

2026-10-02 开工检查：git 2.34.1、gh 2.102.0 可用；分支为 `codex/v1-tdx-information`，HEAD 为 `d2643f83793a7195b150a862e514f2505ca32b42`，等于本地 `origin/main`（#11 合并）。本地 main 指针较旧，但属于 HEAD 祖先，未修改 main。

§3a 未完整通过：GitHub DNS 失败，gh 报本地凭据无效，不能证明有效远程认证。环境技能需要的 `environment_status` 工具在本会话不可用，`/etc/codex/network-policy.json` 不存在，网络/凭据 readiness 不作肯定判断。保持继承网络设置，不绕过限制、不读凭据文件、不交互登录。

检查原始错误：

```text
curl: (6) Could not resolve host: github.com
```

```text
github.com
  X Failed to log in to github.com account zzttcc3321-create (/home/ubuntu/.config/gh/hosts.yml)
  - Active account: true
  - The token in /home/ubuntu/.config/gh/hosts.yml is invalid.
  - To re-authenticate, run: gh auth refresh -h github.com
  - To forget about this account, run: gh auth logout -h github.com -u zzttcc3321-create
```

收尾发现当前工作区 `.git` 为只读挂载，原始错误：

```text
fatal: Unable to create '/home/ubuntu/work/AI-STOCK-SYSTEM-V1/.git/index.lock': Read-only file system
```

采用 `/tmp/ai-stock-task-006` 可写隔离副本完成提交：从当前本地仓库 clone，保持 task 分支和父提交 d2643f8，复制且仅复制四个允许文件。原始仓库的 main、origin 和 Git 元数据均未改变。副本本地读取来源命名为 workspace，新建 origin 指向原有 GitHub URL；没有删除或改写已有 origin。最终提交和 PR 命令从该副本执行。

收尾 §3b 只允许本任务三个新文件及 `providers/__init__.py` 一行 import。测试全部注入 TEST_KEY_ONLY 和 mock transport，禁止 socket 连接；真实 Key 不读取、不写文件、不进 Git。不修改 origin。push/PR 的实际结果以最终任务总结原始输出为准，不预先声称已建 PR 或 CI 通过。

## CODE_REVIEW_PACKAGE_A

```text
TASK_ID=AI-STOCK-V1-WORKBUDDY-006
BRANCH=codex/v1-tdx-information
FILES_CHANGED=providers/tdx_information.py, tests/test_tdx_information.py,
              docs/TDX_INFORMATION_PROVIDER.md, providers/__init__.py (+1 import)
CODE_DIFF_SUMMARY=只读云端 MCP、原始证据与 sidecar、信息职责白名单、当日有界去重、离线故障覆盖
TEST_RESULTS=PASS，130/130（既有79个 + 新增51个），全量socket守卫下运行
STATIC_ANALYSIS_RESULT=PASS，git diff --check、AST语法检查、允许文件边界与敏感文件/值目检
DATABASE_MODIFIED=NO
AWS_MODIFIED=NO
SECRETS_CHANGED=NO
IMPACT_ANALYSIS=不改冻结模块/职责；本地新增文件输出；单进程有界缓存
KNOWN_LIMITATIONS=PUBLIC_OFFICIAL_DOC_VERIFIED=PARTIAL；本次无真实 endpoint 验证；
  工具参数schema/真实F10子接口尚未获离线官方证据，当前F10词汇仅为本地保守白名单；
  canonical信息标准化未扩展；仅有限SSE；缓存非跨进程幂等；超大响应只保留前缀；
  真实负载RSS未测；GitHub网络/认证受阻，远程PR/CI待实际成功后确认
```

COMMIT 与 PR URL 由最终总结给出；A 部分不代替 GPT Review B、阿七人工审或 qi 合并批准。
