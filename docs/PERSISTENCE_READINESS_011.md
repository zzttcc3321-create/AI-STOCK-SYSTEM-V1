# #011 Persistence Readiness：RESUME_AUTHORIZED

## RESUME 当前执行状态（2026-10-03）

```text
EXECUTION_STATUS=RESUME_AUTHORIZED
PERSISTENCE_READINESS=READY
READINESS_SCOPE=V1.1_MAPPING_AND_SCHEMA_CONTRACT
G0_PERSISTENCE_READINESS=BLOCKED
SCHEMA_UNFREEZE=APPROVED_BY_QI
#011_SCHEMA_UNFREEZE_DECISION=APPROVED
QI_DECISION=OPTION_A
DB_SCHEMA_TARGET=V1.1
UNFREEZE_SCOPE=DB_SCHEMA_ONLY
REAL_DB_CONNECTION=FORBIDDEN
REAL_DB_WRITE=NO
DDL=NO
DML=NO
MIGRATION_APPLY=NO
```

qi 已选 A，GPT D22 限定七表增补；本轮只执行任务卡 RESUME 第 1–6 项。
[冻结 V1.1](frozen/DB_SCHEMA_V1.1.md)、[决策记录](decisions/DB_SCHEMA_V1_1_PERSISTENCE_UNFREEZE_20261003.md)
与 [恢复映射/完整 schema inventory 投影](DB_COMPATIBILITY_MAPPING_011.md)构成本次契约。
下方原 G0 报告全文保留为历史证据；其 BLOCKED/未批准/未开始实施等描述指解冻前状态。
原 G0 对 legacy schema 信息不足的判断仍成立，V1.1 测试不重建或伪造旧 32 表。
后续 #012 的 repository/正式 migration/真实库检查尚未实施；本轮的 READY 仅针对已授权的映射契约。
最终测试结果及可自动发现的契约测试入口置于末尾。inventory 本身不提交，schema 元数据投影已纳入映射文档。

## G0 原始阻塞审计（历史保留）

日期：2026-10-03。审计基线：`eaa928a2ad99121154563e8ecc660841beee5fcb`。

```text
PERSISTENCE_READINESS=BLOCKED
STOP_AND_REPORT=YES
STOP_REASON=REQUIRED_SCHEMA_MIGRATION_NOT_AUTHORIZED_IN_011
PERSISTENCE_MAPPING_GAP=YES
REAL_DB_WRITE=0
PRODUCTION_DB_WRITE=0
AWS_DB_WRITE=0
DDL=0
DML=0
MIGRATION_APPLY=0
DASHBOARD=NO
IMPLEMENTATION_STARTED=NO
```

本轮在 G0 确认必须新增/扩展事实库 schema 后停止实施，触发任务卡
STOP_AND_REPORT 条件 4。这两份文档是只读审计与待决要求，**不是已冻结、可直接编码的
Persistence Contract**；没有创建 persistence 模块、测试 schema、新增测试或 migration SQL。
逐字段结论见 [兼容映射报告](DB_COMPATIBILITY_MAPPING_011.md)。

## G0 输入及核验

唯一实际 schema 输入：`docs/freeze/DB_EXISTING_SCHEMA_INVENTORY_V1.json`，
118662 字节，SHA-256：
`c9eaf6d1bc8927c8c0dbdb6dd0404ddc52751b2bcefbbad1c5b66a406fbadbd2`。
该文件为任务卡批准的 Windows 事实库导出元数据，未跟踪、仅只读，不纳入交付或提交。
未连接 Windows、AWS 或本地事实数据库；未读取业务行，也未输出 row_count、账户值、
持仓数量、成本或其他业务记录。

| 核验 | 结果及证据边界 |
| --- | --- |
| 表数量 | 声明 table_count=32，实际 32 个独立表名 |
| 表级元数据 | 每表都有 name / row_count / columns / primary_key / indexes / foreign_keys；不导出 row_count |
| 列定义 | 每列包含 name / type / not_null / pk_position；列名无重复 |
| 主键 | columns.pk_position 的顺序与每表 primary_key 完全一致 |
| 索引 | 共 226 个；均有 name / unique / columns，引用列均存在 |
| 外键 | 共 11 条；from / table / to 均完整，源列、目标表与目标列均存在 |
| foreign_key_violations | inventory 记录为 0；没有在本轮重新运行真实库检查 |
| journal_mode | inventory 记录为 wal |
| sqlite_version / user_version | inventory 记录为 3.49.1 / 0；不能从 user_version=0 推断未做过历史迁移 |
| quick_check | inventory 记录为 ok |
| 完整 integrity_check | inventory 无独立 integrity_check 字段；quick_check=ok 不冒称完整 integrity_check 或本轮现场复核 |
| PRAGMA foreign_keys | inventory 未提供连接级外键启用状态；不能由违规数为 0 推断启用 |
| 三个关键物理表 | analysis_tasks / analysis_reports / evidence_records 均不存在 |
| 支撑实体 | users / stocks / collection_tasks / system_runs 也未发现同名表；不将旧业务表自动视为等价 |

冻结 `docs/frozen/DB_SCHEMA_V1.0.md` 第 2.12–2.14 节将任务/证据表列为缺失，
将 analysis_history 列为报告候选，允许后续兼容迁移。
因此本次发现是 **MISSING / PARTIAL_EQUIVALENT**，未发现需要判为 CONFLICT 的实证；
不能把冻结的逻辑实体列表解释为真实库已有同名物理表。

## 阻塞及待 qi 决策

1. 真实库没有冻结三实体的完整承载位置。analysis_history 只有旧历史分析字段，
   无七字段结构、task_id、human_confirm、确认日志、revision 或 supersedes 字段。
   不能无迁移实现要求的原子 finalization。
2. 冻结逻辑 schema 也没有 #010 ConfirmationEvent、HumanOverride、
   FinalAnalysisEvidence、完整快照/修订元数据和有序 evidence relations 的明确位置。
   通用 TEXT 或 JSON 列的存在不能证明兼容，不能借 reasoning、raw_result、
   context_snapshot 或 decision_signals.evidence_json 隐藏这些对象。
3. inventory 没有原始 CREATE TABLE / CREATE INDEX / trigger SQL、列默认值、
   CHECK/UNIQUE 表约束细节、索引表达式/条件/排序/排序规则、外键动作等元数据。
   足以证明上述缺失；不足以忠实重建实际 schema 做完整约束 round-trip/rollback 测试。
   不按猜测补齐，也不生成理想 schema。

```text
REQUIRES_QI_DECISION=
  A. 解冻/修订 DB Schema，批准承载 #010 对象的最小字段/实体及兼容迁移范围；或
  B. 批准专门的 sidecar persistence contract，包括身份、修订、历史与原子提交协议。
```

A/B 均由 qi 决定，本轮没有选定、设计或创建新表。
现有冻结实体缺失的问题仍需受控兼容迁移授权；**B 单独不能使三张缺失物理表出现，
也不能让文件写入自动获得 SQLite 事务原子性**。
解阻还需提供只含 schema 的实际 DDL/约束快照；无需任何业务行。
若要证明外键启用或完整 integrity_check，需补充相应只读检查结果。

## 后续合同必须满足的事务与历史约束（未实施）

未来原子单元必须是：

```text
BEGIN
  persist analysis task（已存在的同身份任务只校验/关联；不可覆盖）
  persist final report revision
  persist confirmation artifacts（完整有序事件、override）
  persist evidence links / final evidence metadata / approved sidecar bindings
COMMIT
任何步骤失败（含 COMMIT 失败）→ ROLLBACK；读模型看不到半成品
```

在同一个 SQLite transaction/connection 中校验身份、历史链接、引用证据和 hash 绑定。
相同 identity + 相同不可变内容的重试必须有明确幂等约定；同 identity + 不同内容必须拒绝。
task 与模型运行可以被多个报告 revision 共用；不得在每次 revision 静默替换 task。
需要额外批准的身份映射见兼容报告，当前不宣称 run_id 等于某个物理 PK。

```text
INSERT_ONLY_OBJECTS=confirmation_events / human_overrides / final revisions / evidence history
EVIDENCE_UPDATE=FORBIDDEN
EVIDENCE_DELETE=FORBIDDEN
CONFIRMATION_EVENT_UPDATE=FORBIDDEN
CONFIRMATION_EVENT_DELETE=FORBIDDEN
FINAL_SNAPSHOT_UPDATE=FORBIDDEN
OLD_CONCLUSION_UPDATE=FORBIDDEN
CORRECTION=new revision + explicit supersedes link
```

HumanConfirm 仅 PENDING / CONFIRMED / REJECTED；MODIFIED 是字段决定，不是 HumanConfirm。
纠错追加驳回事件，原 final snapshot / conclusion / override 不变。
新 revision 必须保留不同 report_id / revision_id / final_evidence_id 与两类 supersedes
链接；关联必须可验证，拒绝自引用、循环和无目标链接。并发分支处理规则需在合同中明确，
当前不能用时间戳或 ID 字典序擅自选择分支。

snapshot 中 human_confirm=CONFIRMED 是生成时的历史事实；当前认可状态必须由后续
append-only 事件推导。冻结 analysis_reports.human_confirm 单列没有说明它是历史事实
还是可变 projection。无追加日志承载时不能覆盖此列代替历史。
只有明确批准 projection 语义及可重建日志后，才可讨论更新非历史 projection；
本轮不批准 UPDATE final report。

若 B 被批准，必须定义文件预写/验证/发布、提交绑定和恢复协议，或选择同一原子
存储边界。文件写失败应阻止 DB COMMIT；事务回滚后的未引用文件不能被读模型暴露。
还要解决 DB COMMIT 后文件缺失/损坏与崩溃恢复。普通 BEGIN/COMMIT 不涵盖外部文件；
本轮未选协议、路径、manifest 或新表。

## Serialization 后续要求及既有 hash 算法

以下由 #007–#010 源码核对，作为后续实现必须保持的边界；没有声明已通过 DB round-trip：

| 值 | 规范 |
| --- | --- |
| 结构化字段、replacement、calculation、确认/快照元数据 | JSON object；有序事件/ID 集合的编码为 JSON array，顺序须保留 |
| HumanConfirm / EvidenceSource / 状态 / 决定 | TEXT enum，显式白名单验证；HUMAN 仅 origin，不是 EvidenceSource |
| ID | 保留原字符串，禁止转 INTEGER、改大小写、截断或重新生成 |
| timestamp | 有时区 ISO8601 TEXT，保留原字符串表示及 offset，不做隐式 SQLite DATETIME 转换 |
| hash | 64 位小写 SHA-256 hex；不能存 Python repr 或修改 hash 输入 |

canonical JSON 必须匹配 `analysis.contracts.stable_json`：UTF-8、ensure_ascii=False、
sort_keys=True、separators=(",", ":")、allow_nan=False。
保留 bool/int/float 区分、数值表示、null、Unicode 原文及数组顺序；不做 Unicode、
timestamp 或数字归一化改变已有 hash。拒绝 NaN / Infinity（含解码后溢出非有限值）、
重复解码键（包括转义后相同键）、非字符串键；不得通过 str(key) 或别名重命名
产生/吞掉语义重复键。

| hash | 已有重算输入 |
| --- | --- |
| model_output_hash | 原模型响应文本的原样 UTF-8 bytes；不能用 parse 后再序列化的 JSON 替代 |
| validated_output_hash | #009 draft.output 的 canonical JSON；FinalSnapshot.original_validated_output_hash 对应同一值 |
| final_snapshot_hash | FinalAnalysisSnapshot.to_dict() 去掉 final_snapshot_hash 的 canonical JSON |
| confirmation_log_hash | 快照生成时完整有序 confirmation_events 的 canonical JSON；后续事件不改旧快照日志/hash |
| input_hash | #007 PromptEnvelope rendered input（剔除 input_hash 及计数字段后）的 canonical JSON，遵循 output_validator.prompt_inputs |
| prepared_snapshot_hash | PreparedFinalSnapshot.to_dict() 去掉 prepared_snapshot_hash 的 canonical JSON |

重算 model_output_hash / input_hash 需要原响应/原 input envelope 的可靠归档位置。
只有 hash 字符串不够；冻结表没有 run provenance 的完整存储映射。
`_gate_digest` 是进程内 Gate 标记，不作存储认证凭证，不在反序列化时自行生成并
宣称上游 Gate 已通过。后续 read DTO 必须与可继续操作的 sealed domain 对象明确分开。

## 后续只读 repository 边界（要求，尚未实现）

| Repository | 应提供的查询及返回信息 |
| --- | --- |
| AnalysisReadRepository | latest_confirmed_analysis(stock_id)、current_effective_revision(report_id)、analysis_history(stock_id)：返回稳定身份、revision/supersedes、七字段原值/有效值/决定、生成时与当前状态、模型与 hash、rejected/superseded 历史；链异常不得宣称有效 |
| EvidenceReadRepository | evidence(evidence_id)、field_evidence_trace(report_id, field)：区分 input / model-used / human-reference IDs；返回三源、时间、原响应引用、calculation、hash 校验状态与缺失原因；拒绝把人工来源当 Provider |
| ConfirmationReadRepository | pending_confirmations(stock_id)、confirmation_history(report_id)、current_confirmation_state(report_id)：返回必要项、actor/reason、完整有序事件、override 与当前派生状态，含事后纠错；无日志不能推导已确认 |

未来 Dashboard 仅消费上述 DTO，不依赖 ConfirmationFlow tuple、`_gate_digest`、
SQLite 列名/类型或 sidecar 文件布局。pending 清单还依赖 #009 待审项/初始 review items
的保存或可验证重建机制，当前无承载，不得用 human_confirm 单列代替。
“latest”必须结合显式 revision 链和当前确认/证据有效性；被驳回的新 revision
不能使已被纠错驳回的旧 revision 自动恢复为有效。

## 后续 migration plan 的前置顺序（无 SQL）

1. qi 决定 A/B、run/task 与证券/用户身份映射、human_confirm 语义和分支规则。
2. 补齐只含 schema 的真实 DDL/约束快照及必要只读检查元数据。
3. 审批最小兼容迁移与数据归档/恢复合同，保留旧表、历史查询和外键；冻结版本同步修订
   由获授权任务承担。本轮不修改 docs/frozen/*。
4. 后续获授权任务先做可验证一致性备份（含 WAL/SHM），版本化增量迁移及历史兼容检查。
   不执行冻结文档已废弃的 001/002 SQL，不创建平行事实库。
5. 从获批真实 schema snapshot 构建临时 SQLite；验证 serialize→insert→read→deserialize
   的 ID / enum / timezone / seven_fields / 所有 hash / revision 链不变量。
   在每个事务步骤和 COMMIT 注入失败，确认所有行/关联均回滚，并覆盖并发与重试。
6. 通过后才允许 #012 实现生产适配；本报告不构成写库/迁移授权。

## 本轮验证与验收边界

`python3 -m unittest discover -s tests`：528 项，42.909 秒，OK。
本轮未新增代码，所以没有 #011 新增测试；没有临时 SQLite round-trip/rollback 实测，
这些验收项明确未完成，不能报告全 30 条验收通过。
inventory 结构/主键/索引一致性只读核验通过；缺失与阻塞判定以真实元数据为依据。
变更只包含本报告及兼容映射报告，原 untracked inventory 和 __pycache__ 不提交。

## RESUME 可复跑新增验收测试

GPT Review B 返修授权将原 18 项契约测试落成
[tests/test_persistence_schema_v1_1.py](../tests/test_persistence_schema_v1_1.py)，并增加 B2/B3 回归测试。
只使用既有离线 synthetic fixtures 与 :memory: 新七表契约，全局 sqlite3.connect guard 在
fixture/domain imports 前安装，拒绝所有非 :memory: 参数。不连接真实库、不重做 G0。

```bash
python3 -B -m unittest discover -s tests -p 'test_persistence_schema_v1_1.py'
python3 -B -m unittest discover -s tests
```

## RESUME 最终验证与验收结论（2026-10-03）

```text
PERSISTENCE_READINESS=READY
EXECUTION_STATUS=RESUME_AUTHORIZED
BASELINE_TESTS=528/528 PASS
RESUME_CONTRACT_TESTS=20/20 PASS
TOTAL_DISCOVERED_TESTS=548/548 PASS
DB_SCHEMA_V1_1=PASS
NEW_TABLES_EXACTLY=analysis_tasks,analysis_reports,evidence_records,analysis_confirmation_events,analysis_human_overrides,analysis_snapshots,analysis_final_evidence
LEGACY_32_TABLES_CHANGE=0
#011_SCHEMA_UNFREEZE_DECISION=APPROVED
QI_DECISION=OPTION_A
DB_SCHEMA_TARGET=V1.1
UNFREEZE_SCOPE=DB_SCHEMA_ONLY
REAL_DB_CONNECTION=FORBIDDEN
REAL_DB_WRITE=NO
REAL_DB_DDL=0
REAL_DB_DML=0
MIGRATION_APPLY=0
```

GPT Review B 返修验证：`python3 -B -m unittest discover -s tests`：548 tests，50.671 秒，OK；
其中原 main 基线 528/528、新契约测试 20/20 全过。契约测试单独运行：20 tests，9.143 秒，OK。
原 18 项已迁入可自动发现的 unittest 文件，另增 B2 重复 PREPARED / one-FINAL 与 B3 多后继
回归；FK 与 no-self-reference 负向验证覆盖三个 supersedes 字段。保留每个 INSERT 步骤失败
回滚及 deferred COMMIT 失败回滚、#009/#010 synthetic 对象 JSON/hash/ID/enum/timezone/revision 往返。
测试只在 :memory: 对明确获批的新七表执行 DDL/DML；生产 DDL/DML=0。

| Gate | 结果 / 证据 |
| --- | --- |
| 31 DB_SCHEMA_V1_1 | PASS：完整版本/状态/原因、七表 93 个字段定义、payload 公共字段及冻结约束；test_18 |
| 32 NEW_TABLES_EXACTLY | PASS：NEW_TABLES 与 sqlite_master 精确七表；test_02 |
| 33 旧 32 表零改动 | PASS：仅原五个 RESUME 白名单文件及获批新增测试文件；schema DDL 只 CREATE 新七表与其索引/触发器，无 DROP/RENAME/ALTER；V1.0 字节不变，inventory 原 hash 不变；test_17/18 |
| 34 READY 前置 | PASS：当前映射契约 READY，#012 正式 migration 只允许在该状态后定义；本轮没有 migration/*.sql、真实库连接或迁移应用；test_18 |
| 35 解冻决策 | PASS：APPROVED / OPTION_A / V1.1 / DB_SCHEMA_ONLY 均见独立决策记录；test_18 |

原 30 项中，基线、映射、serialization、append-only/revision、事务合同、临时 rollback 与
read repository 接口合同已给出可核验依据；完整生产 integrity_check/连接 FK 启用状态、
legacy DDL 约束重建、repository/read-model/迁移运行均非本轮实测，不冒称生产验收全过。
G0 inventory quick_check=ok 仍不能替代完整 integrity_check；此限度由获授权 #012 流程补齐。
READY 是最小 schema / 映射可实施，不改变 REAL_DB_CONNECTION=FORBIDDEN。

#012 必须实施的只读 repository contract：AnalysisReadRepository 提供 latest_confirmed_analysis /
current_effective_revision / analysis_history；EvidenceReadRepository 提供 evidence / field_evidence_trace；
ConfirmationReadRepository 提供 pending_confirmations / confirmation_history / current_confirmation_state。
返回只读 DTO，完整区分 original/effective/decision、Provider 与人工参考证据、旧历史 record_origin、
当前派生状态与生成时 CONFIRMED、拒绝/被取代 revision；链或 hash 异常不得标有效。
接口定义沿用上述 G0 要求并由 V1.1 提供明确 backing，实现属于 #012，#011 不建 Dashboard。

## GPT Review B 增量返修

B1：删除 evidence_records 数据库元数据列；endpoint_or_tool / request_params /
provider_timestamp / validation_result / hashes 的唯一权威载体仍为冻结 Evidence Pipeline
metadata sidecar。B2：恢复 UNIQUE(analysis_report_id, revision_id, snapshot_kind)，
snapshot_kind 是既有 snapshot_type 列名；保留 one-FINAL-per-report。B3：三个 supersedes
列删除 UNIQUE，保留 FK/no-self-reference，允许多后继，DOMAIN_LOGIC_CHANGE=NO。

只在 codex/v1-persistence-readiness-011 上基于 48ef1db 增量返修，沿用 PR #19，
不新建分支或 PR。提交仅含原五个 RESUME 白名单文件及本次授权新增的测试文件。
真实库连接/DDL/DML=0；仅 :memory: 执行测试 DDL/DML；旧 32 表、G0 inventory 投影、
DB_SCHEMA_V1.0、Evidence Pipeline 与 domain logic 均未修改。当前提交与 push 结果见执行总结。

## 上次交付环境状态（历史记录）

以下是返修前的交付记录；用户已提供现有 PR #19，本轮沿用，不再创建 PR。

```text
DELIVERY_STATUS=LOCAL_COMMITTED_REMOTE_PUBLICATION_BLOCKED
GIT_BRANCH=codex/v1-persistence-readiness-011
WRITABLE_GIT_METADATA=/tmp/task-011-git
GIT_PUSH=FAILED_DNS_RESOLUTION
GH_PR_CREATE=FAILED_API_CONNECTION
GITHUB_CONNECTOR_WRITE=REJECTED_APPROVAL_POLICY_NEVER
PR_URL=NOT_CREATED
```

主工作区 .git 挂载只读，git add 无法创建 index.lock；在 /tmp/task-011-git 复制可写 Git
元数据，沿用原分支名称完成五个授权文件的 add/commit。主工作区的受保护 .git 没有改动。
终端 git push 报 Could not resolve host: github.com；gh pr create 报 error connecting to api.github.com。
GitHub connector 可只读访问仓库，但创建 blob 的写调用被自动审批拒绝，原因为
“MCP tool call requires approval, but approval policy is never”。没有远端提交/PR 被创建，
不伪造 PR URL。PR 正文已准备于 /tmp/TASK_011_PR_BODY.md；后续须在允许写入并可联网的环境发布。
这项发布阻碍不改变本轮真实数据库连接/写入为零，也不授权扩大解冻范围。
