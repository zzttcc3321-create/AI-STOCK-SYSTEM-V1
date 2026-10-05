# R1-FINAL-01：cutoff freshness 重核验代码事实

- TASK_ID：AI-STOCK-V1-R1-FINAL-01
- BASELINE：main `757449b3179af22382e6850ff3cb1a34ff571907`
- BRANCH：`codex/r1-final-01-freshness-cutoff`
- 本文是实现代码事实与执行者自查；不代替阿七 Review A、GPT Review B 或 qi 合并批准。

## CHANGE_SCOPE / CODE_DIFF_SUMMARY

| 文件 | 改动 |
| --- | --- |
| `normalizer/normalizer.py` | 将现有 freshness 时钟规则提取为纯函数，供 normalizer 和 builder 共用；规则与阈值保持原样 |
| `analysis/package_builder.py` | 使用最终解析出的本次 judgment_cutoff 重核验行情；检查原 sidecar、canonical/knowability 与 Evidence 请求时间的一致性；独立保存分析上下文评估 |
| `tests/test_freshness.py` | 新增 8 个离线测试方法，保留原 9 个方法 |
| 本文 | 反例、验证结果、兼容性和红线自查，供审查交接 |

采集时的 `data.freshness`、`data.freshness_check` 不覆盖。分析记录 wrapper 新增
`freshness_at_cutoff = {freshness, freshness_check, data_quality_status}`，标明本次 cutoff、
参考时点、年龄及原因。限制进入 `stale_fields`、`validation_warnings`、行情块质量和
`REALTIME_QUOTE_STATUS`，并通过现有 PromptBuilder 透传。

原 canonical 输入、Normalizer raw/normalized 结果与 EvidenceRecord 均不写入或改写，
原 `normalized_data_hash` 保持不变。本次上下文评估进入分析包及其既有 input hash，
不是对历史 Evidence 的纠错，因此不生成替代采集 Evidence。

builder 重核验只读取 canonical 时钟事实及已允许的 Evidence 六字段；不读 Provider
envelope、raw 文件、Evidence request_params 或其它元数据。无 cutoff 时保持原采集判断。
从其它 canonical 记录继承 cutoff 时同样重核验；已封存的显式 cutoff 冲突仍由原 gate 拒绝。
无可用 Evidence 或已被历史可知性 gate 排除的数据保持原排除行为；仅对获准进入分析的
行情要求完整、相互一致的 freshness 依据，避免把原来的明确受限路径改成提前报错。

## 反例与对照

合成行情时间 `2026-09-30T16:55:50Z`，请求 `17:00:00Z`，获取 `17:00:01Z`；
通过正式 DataNormalizer 得到年龄 251 秒、REALTIME。测试显式提供同一行情的披露时间，
确保原 knowability gate 放行，从而独立验证 freshness 缺陷。

本次 builder cutoff 为 `17:01:00Z`：修复前实时资格为 PASS；修复后独立评估年龄
310 秒、STALE，实时资格 MISSING，原因 `STALE_QUOTE_TIMESTAMP`。
按同一 cutoff 重新标准化的对照也为 STALE。

正常对照：年龄 299 秒、300 秒均为 REALTIME；300.000001 秒为 STALE；等价的
UTC/+08:00 cutoff 结果一致；无 cutoff 仍沿用年龄 251 秒的采集时判断。
原有 STALE/UNKNOWN 限制不得借重核验升级为 REALTIME。

## STATIC_ANALYSIS_RESULT / TEST_RESULT

| 检查 | 结果 |
| --- | --- |
| 基线全量 `PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover -s tests -q` | 708/708 PASS，345.614 秒 |
| 新反例在未修复基线上的独立复跑 | 预期 FAIL：实际 PASS，断言要求 MISSING |
| freshness 专项 `PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover -s tests -p test_freshness.py -q` | 17/17 PASS |
| knowability 兼容性 `PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover -s tests -p test_knowability.py -q` | 12/12 PASS |
| 最终全量 `PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover -s tests -v` | 716/716 PASS（708 + 新增 8），306.970 秒 |
| 三个变更 Python 文件编译、`git diff --check` | PASS |
| 提取后的时钟判定主体与基线 AST 比较 | PASS；仅提取输入读取与函数边界 |
| 四个交付文件的 AWS/GitHub/OpenAI key、私钥模式扫描 | PASS，0 命中；不等同独立 Review A 凭据扫描 |

最终全量在 `/tmp/r1-final-01-review-checkout` 执行，三个变更 Python 文件与原工作区
逐字节一致；该检出的 HEAD 是指定基线，使用原有测试与 git 历史。编译缓存写入
`/tmp/r1-final-01-pycache`；原工作区既有未跟踪缓存未提交或清理。

## IMPACT_ANALYSIS / RISK_FOUND

- 数据库、schema、正式源、Provider 职责、网络采集接口、交易接口均无变更。
- 新评估是串行纯计算；无新依赖、后台进程或服务。
- 获准进入分析且提供 cutoff 的旧 canonical 行情若没有完整 V1.0 freshness_check，或依据冲突，
  现在明确抛出 AnalysisInputError。调用方需提供可核验的采集依据；不可默认为实时。
- 原采集 freshness 仍可能标为 REALTIME。消费本次分析须使用分析评估与
  REALTIME_QUOTE_STATUS；受限原因已进入现有 Prompt 输入和质量报告。
- 测试仅使用合成数据、临时目录和测试 SQLite；不访问真实 Windows 数据库或持仓。

## 红线逐条自查

| 规则 | 本次事实 |
| --- | --- |
| CANONICAL_ONLY_FOR_BUSINESS | builder 仅用 canonical 与冻结的 Evidence 元数据 |
| PROVIDER_HAS_NO_BUSINESS_LOGIC | 无 Provider 改动 |
| NO_SILENT_DEGRADATION | cutoff 限制显式记录；时钟依据缺失/冲突拒绝 |
| EVIDENCE_IMMUTABLE | 原 Evidence/hash 不覆盖，原采集 freshness sidecar 保留 |
| SOURCE_IDENTITY_LOCKED | 三源身份与职责保持原样，无第四源 |
| RAW_DATA_NEVER_REPLACED | raw 数据未读取、未替代、未改写 |
| MODEL_OUTPUT_NEEDS_EVIDENCE | 原 Evidence 绑定继续保留，无证据时原 gate 排除数据 |
| NO_RUNTIME_COMPLEXITY_GROWTH | 纯函数、串行、无重依赖或常驻组件 |

禁止自动交易、真实凭据/持仓入 Git 的要求保持；原工作区 origin 未修改或删除。

## 交付状态

原工作区 `.git` 被执行环境挂载为只读，`git add` 无法创建 index.lock；
`gh api user` 无法连接 api.github.com。临时检出从指定基线创建，用于最终验证与
可交接提交。PR 尚未创建，任务保持 ACTIVE；后续按阿七 Review A → GPT Review B
（FINAL_REVIEW）→ qi 批准合并推进。
