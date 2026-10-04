# CODE_REVIEW_PACKAGE_A — R1-D05

- 基线：main `da16336`（PR #23）；分支：`codex/v1-r1-d05-limitation-fidelity`。
- 范围：代码修复、离线测试、本审查事实记录；未 push、未创建 PR。
- 本记录不替代独立代码审查。

## 范围与 diff

| 文件 | 修改 |
| --- | --- |
| analysis/package_builder.py | 声明块/包聚合规则；按结构化状态聚合，避免从警告文本猜测状态 |
| analysis/output_contracts.py | 草稿增加递归冻结、纳入 gate digest 的 limitation_context |
| analysis/output_validator.py | 从密封 Prompt 输入复制完整质量报告与限制目标、状态、原因 |
| analysis/confirmation_flow.py | 复核计划 details 明确携带字段质量、限制及输入限制上下文 |
| analysis/finalization.py | 每个最终字段保留 input_limitation_context，包括人工修改和拒绝字段 |
| persistence/read_models.py | 报告暴露 confirmation_decisions/watch_items；事件附独立限制明细投影 |
| tests/test_limitation_fidelity.py | 4 项离线回归测试，WARNING/UNVERIFIED 参数化及 cutoff/freshness 场景 |

没有修改冻结文档、DB schema、迁移、Provider、三源白名单、阈值或底层时钟门控。

## 投影路径盘点

| 路径 | 保真载体及规则 |
| --- | --- |
| 标准化 → Evidence/canonical → package | canonical 行质量、freshness_check、knowability 与验证警告沿用；块明确最差质量优先 |
| package → P3 Prompt 输入 | PromptBuilder 原样携带块和完整 data_quality_report，新增聚合规则随输入哈希传递 |
| Prompt → 模型七字段 → validated draft | 原七字段及模型哈希不改写；独立 limitation_context 保留所有异常输入目标和完整报告 |
| draft → confirmation 计划/事件 | 计划 details 保留数据质量；事件通过 target/reason_code 关联计划，读取另附 event_limitation_details，人工状态独立于数据质量 |
| draft → override | 既有结构化覆盖禁止更改质量、事实、限制、证据和时间范围；最终字段独立保存输入限制上下文 |
| review → PREPARED → FINAL/最终 Evidence calculation | 字段上下文、确认详情和 watch_items 纳入快照密封与哈希；事件原始 schema/hash 保持不变 |
| 持久化 → 报告历史/当前有效报告 | seven_fields 完整保留，新增确认详情和 watch_items；CONFIRMED 不表示数据质量 PASS |
| 持久化 → confirmation/read/字段证据追溯 | 事件 sidecar 在 PREPARED 前也能从任务草稿取得限制；字段追溯保留完整 field_value |
| 旧记录/缺失上下文 | 草稿缺少新增字段时事件 sidecar 明示 null；不补 PASS、不重建可信 gate |

仓库没有可执行的 P3 dashboard 前端或独立报告模板；现有展示入口是 Prompt 输入与只读 DTO。
冻结 P3 模板保持不变，没有新增展示系统或修改其输出 JSON schema。

## 显式聚合

- 块：UNVERIFIED > WARNING > PASS；空块使用原 absent_status；完整 records 不删改。
- 包：按源、块、knowability restriction 的结构化状态取最差质量；任意警告至少 WARNING。
- MISSING/NOT_AVAILABLE 是可用性汇总，七字段仍允许该状态；WARNING/UNVERIFIED 和原因在独立上下文中完整保留。
- validation_warnings、stale_fields、incomplete_reasons、source_status、knowability_restrictions 和 cutoff 报告完整复制，不将人工接受解释为质量升级。

## 静态分析

- 7 个修改/新增 Python 文件 AST 解析通过；git diff --check 通过。
- historical_gate / historical_identity / attach 的 AST 与基线一致。
- normalizer 的 normalization/validation/contracts、evidence/knowability.py 和 DB schema 与基线逐字节一致。
- 无新增依赖、外部源、后台循环、网络调用或真实 DB 写入；新增测试仅 SQLite :memory:。

## 测试

- 新增专项：4/4 通过（含 WARNING/UNVERIFIED 参数化、override、拒绝、最终确认、历史/当前报告、事件/字段追溯、query_only、不可变性、cutoff 与 freshness 原因保真）。
- 初轮全量：686/686 通过，342.815 秒。
- 最终全量运行：688/688 通过，358.454 秒（发现测试时新增专项为 2 项）。
- 全量运行期间专项增至 4 项，最后单独重跑全部 4 项通过；既有 686 项与最终新增 4 项均有通过记录。
- 最后专项复核：4/4 通过，14.071 秒；包含最后新增的拒绝及全部事件限制上下文断言。

## 影响与风险

- additive JSON 载体使用现有 TEXT 存储，不变更数据库物理 schema。
- 新草稿、计划和快照的 hash 因新增明确上下文自然变化；模型原始输出与 validated_output_hash 不被重写。
- 限制上下文在字段/计划中重复，增大 JSON 和哈希处理量；无 raw payload、重依赖或额外常驻进程。
- 旧快照不会被回填；旧草稿未记录的新上下文明确不可得。读取 DTO 不构成重新通过门控的分析对象。
- 未提交 commit；按用户“只做代码修改和测试”要求，改动留在本地分支供阿七审查、提交和 push。
