# 吉林高速 V1.0 试验登记表

本阶段仅起表，未执行策略信号、参数回测、优化或投资判断。后续试验必须先获总指挥审查进入第二阶段。

| 编号 | 阶段 | 内容 | 输入段 | 固定参数/版本 | 执行 | 结果位置 | 审查状态 |
| --- | --- | --- | --- | --- | --- | --- | --- |
| S1-DATA-001 | Stage 1 | 数据完整性与公司行动核验 | 全窗口，仅完整性 | 上交所 2023–2026 休市日历；公告分红；因子 `stage1-cash-dividend-v1` | 1 次正式复算 | `STAGE1_DATA_VERIFICATION.md`、`ADJUSTMENT_FACTORS.json` | 待总指挥审查 |
| S1-FEE-001 | Stage 1 | 已验 387 元案例复现 | 合成价位，不使用行情调参 | 万 2.5、每边最低 5 元、卖出印花税 0.05%；滑点 0/0.01/0.02 元 | 1 次正式复算 | `STAGE1_REPRODUCTION.md` | 待总指挥审查 |
| S1-HOLD-001 | Stage 1 | 买入持有基准 | 全窗口，仅基准 | 公告现金分红、未复权收盘价；私有成本输入不入库 | 1 次正式复算 | `STAGE1_REPRODUCTION.md` | 待总指挥审查 |
| S2A-RULE-001 | Stage 2A | 四状态与批次、净收益门禁、结局、预热的静态规则整理 | 无行情策略试验；仅 Stage 1 因子表交易日期核对预热容量 | `jlgs-stage2a-rules-v1.0-draft`；`ready_for_stage2b=false`；Q01–Q14 待裁决 | 2 次静态一致性校验 PASS；0 次策略回测、0 次参数比较 | `STRATEGY_RULES_V1.0.md`、`STAGE2A_OPEN_QUESTIONS.md`、`backtest/rules_v1.json`、`backtest/validate_rules_v1.py` | 待总指挥与 qi 确认完整规则 |

未来登记至少包含：试验编号、预先登记的开发段参数、因子版本、费用档、代码提交、开发段结果、锁定时间、样本外执行与审查结论。样本外结果不能用于改写既定口径。

| S2A-RULE-002 | Stage 2A 定版送终核 | 落实总指挥 Q01–Q14 裁决与 qi 2026-10-22 拍板；新增 Q07 候选，更新静态校验 | 不读取行情蜡烛；仅用 Stage 1 公开因子日期核对 61 日理论边界 | `jlgs-stage2a-rules-v1.0-final-review`；Q01 A（前两批箱体内、后三四批待开发段）、Q09 低档 A、Q10 A（成交前 50%）、Q11 可整除且合法交易单位、Q14 qi 授权阿七选元/股；Q02–Q06/Q08/Q12/Q13 按裁决 | 静态校验 7 次 PASS；全量测试 1 次，1177 项通过（577.586 秒）；行情策略回测 0 次、参数比较 0 次 | `STRATEGY_RULES_V1.0.md`、`STAGE2A_OPEN_QUESTIONS.md`、`Q07_CANDIDATE_DEFINITIONS.md`、`backtest/rules_v1.json` | 待 Q07 选择、第三/四批开发段后冻结、门槛数值后续确定、成交模型及门槛单位总指挥终核；`ready_for_stage2b=false` |
