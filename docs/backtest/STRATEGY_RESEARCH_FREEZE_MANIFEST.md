# 吉林高速 V1.0 / V1.1 研究冻结清单

任务 `JLGS-V1.1-RESEARCH-CLOSEOUT-01`；冻结核对日 2026-10-24。以下提交 ID 由本地 Git 复算，文件哈希由 `git hash-object` / `sha256sum` 复算。本清单只冻结既有研究事实；没有新增行情研究。最终裁决 **`ENGINEERING_PASS + STRATEGY_FAIL`**；V1.1 状态 **`CLOSED_STRATEGY_FAIL`**，**`LIVE_STRATEGY_AUTHORIZATION = NO`**，`ready_for_stage2b=false`。合并仅为失败证据归档。

## PR 与提交

| PR | 归档内容 | 实际合并提交 |
| --- | --- | --- |
| #50 | Stage 1 数据、费用及持有基准 | `207f7e115b27b92dc6823273f77993985f229c82` |
| #51 | Stage 2A V1.0 规则 | `7198cd93bfaae110214454846d7a5c0ba5ea9445` |
| #54 | Stage 2B-D V1.0 开发段研究 | `d36307965798a5eeb56af51f8f75127f141c8ca1` |
| #55 | Stage 2B R1 隔离复验与诊断 | `90cbc71ebe4bb03ddc8b861b01bf7d681a39024c` |
| #56 | V1.1-A 设计 | `3cd106e9d921ea1a9c7b3263236729b352867a48` |
| #57 | V1.1-B 隔离验证；PR HEAD `392464fad06774a49ce68e96fc20c1cb02697234` | **`101698822030c68ce7979d5b3784127f608ed98f`** |

## 文件与数据校验

| 对象 | 类型 | 复算值及冻结意义 |
| --- | --- | --- |
| `docs/backtest/STAGE2B_DEV_PREREGISTRATION.md` | SHA256 | `e3794842640489e4b0e7ac14680c6447c02c7d90deeed99a6cf38cfb3a67bcfa`；V1.0 事前 7×3 网格 |
| `docs/backtest/STAGE2B_DEV_REPORT.md` | SHA256 | `59a80affcf6d178ead77e9d8725e02017e33b47b010a9b8cd43178e8f7074420`；21 组零交易 |
| `docs/backtest/V1_1_B_PREREGISTRATION.md` | SHA256 | `c84e93b85bc580f0194a6a0de9fa994978dd3e1568f45eda9b2112735f373d5b`；V1.1 事前硬门槛 |
| `docs/backtest/V1_1_B_DEV_REPORT.md` | SHA256 | `fc60ca4a8a12cb4f8ac009c6b607f883b3668569861a1bb9929dfac3c39568ca`；V1.1 研究结果 |
| `backtest/rules_v1_1_research.json` | SHA256 | `7598894128114f82a17c4830683d2f75261b85366419813ddfe5038c6b6db642`；只供研究 |
| `backtest/local_data/jlgs_dev_only.json` | SHA256 | `10ba156037d0ce2cec0c1b8489c78e38185e2e7de37d34a168e1693d508ac5b1`；被 Git 忽略的独立开发段输入，485 日，2023-10-09 至 2025-09-30 |
| `backtest/rules_v1.json` | Git blob | `021422a216d7cc72934c848c3e06d19be9f0dc40` |
| `backtest/stage2a_policy.py` | Git blob | `61b44ea948522868028f8787467bf9d63f0353f2` |
| `backtest/development_research.py` | Git blob | `5b15452feff65c3feaa286083c855530a958ea9d` |

三份 V1.0 受保护文件的工作区 blob 与 `origin/main` 的 Git 树逐一相同，未被 V1.1 改动。开发段文件只计算文件哈希并核对既有隔离报告，不读取其中行情内容。2025-10-01 起样本外段继续封存，未进行策略验证；第三、四批禁用，正式 V1.0 门槛仍为 `null`。

## 固定研究口径与结果

- **费用、时序和数据**：双边佣金各笔成交额万分之 2.5、每边最低 5 元；卖出印花税 0.05%；每边滑点 0 / 0.01 / 0.02 元；T+1；原始未复权价用于成交，因果现金分红调整序列用于指标。三次除息：2024-07-04 每股 0.09 元、2025-07-03 每股 0.086 元、2026-07-06 每股 0.13 元。来源为[方法口径](BACKTEST_METHOD.md)及[数据核验](STAGE1_DATA_VERIFICATION.md)；2026 年事件不进入开发段。
- **V1.0**：[开发段报告](STAGE2B_DEV_REPORT.md)预登记 21 组均零交易、无合格建议门槛；A-F1 与第一批价位无同日交集，D 人工锁存进一步阻断。A 箱体 2.64–3.00 元，B 趋势暂停卖出，C 跌破 2.64 元并满足弱势条件暂停回补，D 跌破 2.40/2.30 元进入停摆/失效审查。
- **V1.1**：[预登记](V1_1_B_PREREGISTRATION.md)规定仅 D0 主方案三档决定策略验收；[报告](V1_1_B_DEV_REPORT.md)显示三档均零交易，常规完成率、净额和超额均为零。开发段仅有的 3 个高价日均处 D0 锁存期。D1-N1/N2 机械代理仅作敏感性，唯一完成轮次是亏损风险回补：10,000 研究单位尺度净额 −288.73 / −338.71 / −388.70 元，超额 −1.11% / −1.30% / −1.49%，常规完成率 0%。不得将 D1 代替正式结论。
- **测试出处**：PR #57 合并门禁首跑连接中断，记录为 `INCONCLUSIVE`；阿七合并核对取得的重跑 pytest 汇总原文：`1205 passed, 2304 subtests passed in 710.52s`（**合并门禁重跑汇总**）。[试验登记](EXPERIMENT_LOG.md)的 `1205 tests OK` 是 V1.1-B 原有本地全量回归记录，运行环境与耗时另计。本次仅核对记录，未重跑测试或回测。

## 数据纪律与关闭状态

Stage 2B-D 曾越界用 `json.load` 解析含封存段的整份原始文件，定性 **`OOS_DATA_BOUNDARY_VIOLATION`**；没有计算或报告样本外行情。后续加载器改为截断读取并以独立开发段文件复验。违规永久留档，隔离复验不撤销违规事实。详见[原偏差记录](OOS_BOUNDARY_DEVIATION_RECORD.md)。

**补审已由总指挥 R1 裁决（2026-10-22，`PASS_WITH_LIMITATIONS`）关闭，违规永久留档。**裁决于当日 21:51 作出，经总指挥对话渠道送达，先前未入仓；[试验登记](EXPERIMENT_LOG.md)末尾新增关闭记录。原偏差记录和登记表旧条目中的“待总指挥补审关闭”为裁决前表述，现由 R1 裁决取代，历史正文保持不变。此裁决不构成策略通过或实盘授权。
