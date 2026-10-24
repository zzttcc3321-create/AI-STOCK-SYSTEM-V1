# P0 数据边界偏差记录

- 事件编号：`P0_OOS_ADJUSTMENT_FACTOR_READ_VIOLATION`
- 发生日期：2026-10-24（首轮执行）
- 任务：`JLGS-V2-P0-MARKET-STRUCTURE-01`
- 定性：高风险流程违规，永久留档。

## 发生经过与超界范围

首轮执行在核对 Stage 1 复权资料时，全文输出 `docs/backtest/ADJUSTMENT_FACTORS.json`。输出覆盖全文件 733 行，读取到 2025-10-01 之后的复权因子及现金分红记录，文件记录延续至 2026-10-16。这些超出开发段的记录被显示读取，未用于任何机会统计或研究产出。

## 停止动作与影响评估

发现越界读取后，执行者立即自报并停工。首轮未执行机会统计、未生成交付物。总指挥评估为未发现研究结果污染证据；这一评估不改变已经发生数据边界违规的事实。

## 整改与重启门禁

1. 行情只允许读取截断隔离文件 `backtest/local_data/jlgs_dev_only.json`；复权资料只允许读取截断隔离文件 `backtest/local_data/adjustment_factors_dev_only.json`。
2. 原始因子文件 `docs/backtest/ADJUSTMENT_FACTORS.json`、原始全量行情文件以及任何含 2025-10-01 起数据的文件列为禁读源；缺失隔离输入时停工，不从禁读源补建。
3. 开工先由 `python3` 校验两份隔离输入的 SHA256、485 条记录、字段完整性、日期边界、日期唯一递增和交易日一一对应。2026-10-24 重启门禁复核结果：两份 SHA256 分别为 `10ba156037d0ce2cec0c1b8489c78e38185e2e7de37d34a168e1693d508ac5b1`、`8718543ec2b15bf1010b4a6330357bad7b2504f69ccc9344d3ae57fe1fd651b2`，其余检查全部 PASS。

## 复审结论

总指挥 2026-10-24 裁决：`P0_RESTART=APPROVED_AFTER_ISOLATION_GATE`。事件按 `P0_OOS_ADJUSTMENT_FACTOR_READ_VIOLATION` 永久留档；隔离门禁通过后准许重启研究。未发现污染证据仅指首轮没有统计或交付物受到该读取影响，不表示未发生违规。
