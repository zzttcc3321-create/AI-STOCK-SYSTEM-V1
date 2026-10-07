# #014-R1 V1 Runtime Closure 总结

**结论：BLOCKED_WITH_ROOT_CAUSE**

TASK_ID=AI-STOCK-V1-E2E-R1-014。已全文读取 `/home/ubuntu/e2e_014r1_taskcard.md`，按工程卫生 → P0 Evidence Identity/跨模块测试 → 指标 → Dashboard → 最终 E2E 的顺序执行。起点为 main `5a866554a4e8bfa0069ae2ee26ce8569f64ac298`；最终代码验证提交为 `25e1e00e315e2470bc571133e4f35e0f02a0c736`。本总结与 JSON 是随后追加的审查证据，不修改运行代码。

## 已实现与证据

- BLOCKER_014_001：补齐四类 Python 缓存忽略规则；隔离检出在代码提交后 `git status --short` 为空。原仓库 main 未切换、未改写；缓存卫生修复尚未合并到原 main。
- BLOCKER_014_002：Evidence V1.1 三字段 `evidence_id/canonical_id/raw_reference_id`。初始 canonical `EV-00001` 对应 raw `abc123`，supersedes=NULL；升级产生新 canonical ID 并链接上一 canonical。追加独立 canonical sidecar，Reader 不再用 raw URI ID 代替 canonical ID。保留原始字节和旧 V1 sidecar，不改 migration，扩展存入已有 JSON。
- 跨模块 7/7 PASS：真实生产模块 raw acquisition → InformationNormalizer → EvidenceBuilder → normalized/canonical archive → ArchiveReader → SQLite Evidence 投影；覆盖公告、新闻、研报、利润/资产负债/现金流、升级不可变性、篡改拒绝、旧 sidecar 兼容及已有 writer 调用入口。
- BLOCKER_014_003：实现 `analysis/indicator_calculator.py`，输入 DB Evidence ID，读经过验证的 canonical daily sidecar，计算 MA5/20/60、成交量变化和 UP/DOWN/SIDEWAYS。缺历史或未知复权保留 NULL/UNKNOWN/PARTIAL；零基数不伪造变化率。6/6 PASS，SQLite authorizer 证实计算只读新 Evidence 表。窗口是最近可用日K观察数，不认证交易日历完整性。
- BLOCKER_014_004：实现 Streamlit app/runtime/factory；显示四个任务阶段、PASS/PARTIAL、Evidence 数量、Prompt；接入已有 AnalysisRunner、逐项人工确认/驳回、最终快照查看与确认归档。runtime 6/6 PASS，验证最终读回完整 trace、写入失败不提前确认以及归档同 ID 重试。无默认模型/Provider、自动通知、调度或交易入口。

## 实际测试结果

命令均为实际执行，未更改任何原有测试文件，也未以跳过原测试放行。

| 执行 | 结果 |
| --- | --- |
| 基线 `python3 -m unittest discover -s tests` | 750/750 PASS，395.204s |
| 初版 P0 完整回归 | 755/755 PASS，386.104s |
| 最终 `python3 -m unittest discover -s tests -v` | 772 项：769 PASS、0 FAIL、0 ERROR、3 SKIP，348.345s |
| 原有测试子集 | **750/750 PASS，无 skip** |
| 新增子集 | 19 PASS；3 个真实 Streamlit AppTest 因依赖缺失 skip，不能计为 UI PASS |
| 静态检查 | 全部 94 个 Python 文件 AST/编译 PASS；diff whitespace PASS；143 个跟踪文件已知凭据格式/禁入文件名扫描 0 findings |

日志和 hash 清单位于 `/tmp/014r1-delivery`。仓库内摘要 JSON 在 `docs/evidence/014_R1_RESULTS/`。凭据扫描只是已知格式检测，不声称穷尽全部秘密类型。

## 最终 E2E 重跑

已执行 `python3 tools/e2e_014_r1.py`。仅使用 dev-box 临时 SQLite/合成数据及标明的测试执行器；真实数据库访问、真实 Provider 调用、真实模型调用均为 0。

| 项目 | 实际状态 | 证据与范围 |
| --- | --- | --- |
| E0 | PASS_DEV_SCOPE | 基线为祖先、隔离工作区 CLEAN、冻结文件/migration/schema 路径未改；原 main/origin 保留。真实 Schema B/老32表历史摘要未在 Windows 核验 |
| E3 | PASS_DEV_SCOPE | 六类信息 canonical/raw ID 分離，Reader/hash/raw byte roundtrip 通过 |
| E4 | PASS_DEV_SCOPE | DB Evidence→MA5=157、MA20=149.5、MA60=129.5、volume_change=0.15、trend=UP，含 Evidence/hash 追溯 |
| E5 | PASS_CODE_CONTRACT | 精确七字段、确定性 input_hash、WATCH_ONLY/无持仓；缺失信息保留，DATA_STATUS=PARTIAL |
| E6 runtime | PASS_DEV_SIMULATION | COLLECTING→NORMALIZING→READY_FOR_REVIEW→ARCHIVED；逐项确认、最终快照及 SQLite 归档通过，质量仍为 PARTIAL |
| E6 Streamlit 页面 | **BLOCKED_WITH_ROOT_CAUSE** | Streamlit 未安装；pip/ensurepip 均不可用，安装尝试实际返回 `No module named pip`；没有页面运行/点击 PASS 证据 |
| E1/E2/E7 | NOT_RUN_WINDOWS_DEFERRED | 按本任务卡排除，待 qi 后续安排；合成确认不等于 qi 确认 |

**不声明 V1 试运行验收通过，也不声明整个真实 Windows E2E PASS。**

## PR URL 列表与 BLOCKER 状态

每个修复点有独立本地分支与提交，Part A 已附；PR 按依赖堆叠，base 为上一修复分支，使每个 PR 的 diff 仅包含自身修复。尚未创建任何远程 PR，因此 URL 如实记为 NOT_CREATED。

| BLOCKER | 本地分支 | PR URL | 关闭状态 |
| --- | --- | --- | --- |
| BLOCKER_014_001 | codex/014-r1-blocker-001-hygiene | NOT_CREATED | LOCAL_FIX_VERIFIED；远程审查/合并待办 |
| BLOCKER_014_002 | codex/014-r1-blocker-002-evidence | NOT_CREATED | LOCAL_CODE_CLOSED；跨模块验证通过，远程审查/合并待办 |
| BLOCKER_014_003 | codex/014-r1-blocker-003-indicators | NOT_CREATED | LOCAL_CODE_CLOSED；指标验证通过，远程审查/合并待办 |
| BLOCKER_014_004 | codex/014-r1-blocker-004-dashboard | NOT_CREATED | OPEN_UI_RUNTIME_VERIFICATION；后端运行链已通过 |

Part A 分别在 `docs/evidence/014_R1_BLOCKER_001_PART_A.md` 至 `014_R1_BLOCKER_004_PART_A.md`。审查 Part B、AWS 验证和 qi 决策均未冒充完成。

## 未完成项的实际根因与交付位置

1. GitHub 连接器读取仓库成功，但 `github_create_tree` 写入调用被策略拒绝：`MCP tool call requires approval, but approval policy is never`。本地 GitHub DNS 不可用，gh 已配置的凭据状态为 invalid；未读取或打印凭据值。远程 branch/commit/tree/PR 全部未创建；不能提供虚构 PR URL。
2. dev-box 缺少 Streamlit/pip/ensurepip，包安装无法执行；E6 页面实测尚未闭合。3 个 AppTest 与要求安装依赖的 CI 工作流已交付，但 CI 尚未触发/执行。
3. 原工作区 `.git` 是只读挂载，分支/提交在 `/tmp/ai-stock-014r1` 隔离检出内完成。该检出在提交后 CLEAN；原 main 保持基线，其缓存忽略修复待合并。

`/tmp/014r1-delivery` 提供四分支 Git bundle、四份独立补丁、PR_QUEUE.json、带 Part A 的 PR 正文、实际测试/E2E/静态结果及 SHA256SUMS。可在具备 GitHub 写权限的会话导入 bundle，按队列推送并建 PR；安装 requirements-dashboard.txt 后运行 `REQUIRE_STREAMLIT=1 python3 -m unittest discover -s tests -v` 和最终 E2E，再核实 BLOCKER_014_004。

未 push main、未合并 PR、未改原仓库 origin、未改 migration 或冻结文件、未触碰 dev-box 外真实数据库、未提交真实持仓/凭据/Tokens。源码修复已可审查；发布与验收仍受上述两个运行/交付阻断影响。
