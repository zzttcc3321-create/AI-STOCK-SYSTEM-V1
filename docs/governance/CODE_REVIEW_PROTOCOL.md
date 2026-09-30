# 代码审查协议 CODE_REVIEW_PROTOCOL V1.0

- STATUS: FROZEN
- VERSION: 1.0
- 适用范围：AI股票系统 V1 所有代码变更

## A 部分：代码事实（Codex/Sol 填写）

- TASK_ID
- CHANGE_SCOPE（改了哪些文件、哪些模块）
- CODE_DIFF_SUMMARY（diff 摘要）
- STATIC_ANALYSIS_RESULT（静态检查结果）
- TEST_RESULT（测试结果：用例数、通过率）
- IMPACT_ANALYSIS（影响面：数据库、接口、性能）
- RISK_FOUND（发现的风险）

## B 部分：规则判断（GPT 填写）

- ARCHITECTURE_COMPLIANCE（是否符合冻结架构）
- EIGHT_RULES_CHECK（八条铁律逐条过）
- EVIDENCE_CHAIN_CHECK（证据链完整性）
- TRUST_BOUNDARY_CHECK（信任边界：secrets、生产权限、数据隔离）
- FINAL_REVIEW = PASS / PASS_WITH_CHANGE / BLOCK

## 审查链

代码事实 → Sol 分析（填 A） → GPT 判断（填 B） → 阿七 AWS 验证 → qi 决策（必要时）

## 铁律

- Sol 不是最终审核人，没有审批权；只负责把代码讲清楚
- GPT 不直接读代码，只审 A 部分提交的事实是否符合冻结规则
- BLOCK 条件：违反八条铁律 / 改变架构边界 / 重大安全风险
- 同一 TASK_ID 下 A/B 缺一不可，不接受口头"没问题"
