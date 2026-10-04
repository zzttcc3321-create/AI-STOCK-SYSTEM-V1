# CODE_REVIEW_PACKAGE_A — R1-D01 + R1-D02

- 基线：main `4734387`（PR #24）；分支：`codex/v1-r1-d01-d02-adjustment-semantics`。
- 范围：代码、离线测试及本任务卡要求的审查记录；未 commit、未 push、未创建 PR。
- 本记录不替代独立代码审查。

## 范围与 diff

| 文件 | 修改 |
| --- | --- |
| normalizer/semantics.py | V1.0 周期、字段语义、复权口径、来源和转换链；拒绝缺失、冲突、未知版本与规则 |
| normalizer/normalizer.py | daily/quote canonical 行携带元数据；按股票声明序列口径；保留行及 payload 声明来源 |
| normalizer/information.py | 信息 canonical 行记录未知周期语义、原始 Evidence 来源、审计规则及版本；旧版本拒绝重新解释 |
| evidence/builder.py | 使用现有 normalization_rule 记录 JSON 语义侧车及序列声明；canonical 哈希覆盖行元数据 |
| analysis/package_builder.py | 验证语义与版本；排除混合序列及口径冲突快照；质量报告保留原因、Evidence 和比较声明 |
| tests/test_adjustment_semantics.py | 14 项专项测试，包括 D05 限制投影与旧版本保护 |
| tests/test_analysis_package.py | canonical 夹具补充元数据；调整递归冻结后的 JSON 比较 |
| tests/test_information_normalizer.py | 原规则 ID 断言保留，并验证新增 Evidence 语义侧车 |
| tests/test_prompt_builder.py | 未知复权夹具同步 UNKNOWN 声明 |

未修改冻结文档、DB schema、迁移、Provider、源白名单或依赖；无真实数据库写入。所有数据库测试使用既有测试夹具。

## R1-D01 约束

- 每条价格记录携带 adjustment_basis；支持 UNADJUSTED/QFQ/HFQ，无法验证的日线口径为 UNKNOWN。
- Normalizer 按 stock_id 声明序列口径和 comparable；Evidence 现有 normalization_rule 同步记录，无新增表列。
- 分析门控独立重算口径；同序列混排或多条 UNKNOWN 时，整段日线退出业务输入，块与包保持 UNVERIFIED，并标记 INCOMPLETE。
- 单条 UNKNOWN 可保留为既有 UNVERIFIED 事实，但明确 comparable=false；不推断复权因子、不修改价格。
- 日线与实时快照只在日线明确 UNADJUSTED 时允许共用；QFQ/HFQ/UNKNOWN 或混排时明确声明跨序列不匹配，并排除快照。
- 限制原因、口径集合与 Evidence 引用保留在 data_quality；Prompt 不携带被排除的价格，防止下游静默比较。
- 显式声明 QFQ 的 quote 不会被改成 UNADJUSTED；当前快照契约拒绝该口径。

## R1-D02 语义链

- 市场行携带 period、field_semantics、conversion_source、rule_version、conversion_chain；信息行携带同样五项。
- 日线 close 为 CLOSING_PRICE，快照 price 为 LAST_TRADED_PRICE；volume 为股、amount 为人民币成交额，语义属于版本化契约。
- 周线、结算价等显式声明原样保留；分析门控拒绝不支持的周期/字段语义，不隐式聚合或映射为日线收盘价。
- 转换链记录 vol/ts_code 字段映射、日期格式、单位转换、周期及复权来源，并逐步记录规则版本；单位声明和目标单位保留。
- 调用方和 payload 单位声明可混合，链标记 caller.units+payload.units 并保存实际采用的 input_unit；纯 payload 声明另标其来源。
- 信息周期声明为 REPORTING_PERIOD_UNVERIFIED/EVENT_PERIOD_UNVERIFIED；空值字段语义保持 UNVERIFIED，不推断披露时间或财务单位。
- RAW_DATA_NEVER_REPLACED：raw 深拷贝保留；canonical 元数据参与 normalized_data_hash；不会回填旧数据或旧 Evidence。
- 缺元数据、旧/未知版本、链版本冲突或未知规则都被拒绝；携带 rule_version 的 canonical 输入不得作为原始市场行重新标准化。
- 未来规则需要独立版本的实现与新 Evidence；本补丁没有复权转换、周期聚合或未知字段转换算法。

## D03/D04/D05 保持

- historical_gate、historical_identity、attach 的 AST 与基线一致。
- _quote_freshness、_numeric 的 AST 与基线一致；knowability、freshness_check、四时钟规则与阈值未修改。
- 不修改 D05 草稿、人工确认、finalization 或只读投影实现；新增比较报告沿现有 limitation_context 完整投影。
- 专项测试验证跨口径原因和 UNVERIFIED 状态进入密封草稿，不被模型概括或人工状态替换。

## 验证

- 最终专项：14/14 通过。
- 分析包 59/59、信息标准化 23/23、Prompt 29/29、市场标准化 11/11 均有通过记录。
- 修改/新增 Python 文件 AST 解析与 git diff --check 通过。
- 最终全量测试结果：待最终运行完成后记录。

## 影响与限制

- 新 canonical JSON、Evidence 归一化哈希及下游快照哈希会变化；不重写旧记录或原始模型输出。
- 旧市场 canonical 缺少元数据时停止于分析门控；须从保留的 raw 创建新标准化结果和新 Evidence，不能直接补标签。
- 同口径 QFQ/HFQ 依赖已有 VERIFIED 声明；本补丁不验证供应商复权因子或复权锚点。
- 跨口径采取保守排除，可能减少可用实时数据；不执行没有审计规则的转换。
- 元数据增加 JSON 体积和哈希工作量；无重依赖、网络访问、新增常驻进程或后台循环。
