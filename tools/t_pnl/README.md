# T 收益核算（T-PNL-CALC-01）

`calculate_t_pnl(trades, slippages=...)` 只核算按输入顺序已成交的 `SELL → BUY` 回补数量。输入为 `(date, side, price, shares)` 序列，或带实付费用的 `Trade`；日期可用 `date` 或 ISO 日期字符串，`side` 为大写 `SELL` / `BUY`，价格使用 `Decimal`、十进制字符串或整数，股数为正整数。价格、实付费用和滑点均拒绝 `float`、布尔值及非有限数，避免二进制浮点误差。交易按日期非递减排列，逆序报 `ValueError`，同日使用输入顺序；同日回补保留并标注 `t_plus_zero_warning`，此模块不判定 T+1 合规性。这里的价格是**实际成交价**，没有委托单或未成交量入口。

FIFO 先消耗最早未回补的卖出量。一笔成交可以分配给多笔配对，买入开仓或超额买入以及未回补卖出都列入 `unmatched`；它们不进入已实现盈亏。输出 `pairs` 逐项含卖买索引、日期、价格、数量、价差、实际成交价差毛利、费用及净值；`scenario_totals` 与明细使用相同档位。空输入返回零合计。

| 输出字段 | 口径 |
| --- | --- |
| `pairs` | FIFO 配对片段；索引从 0 开始，一笔原始成交可能出现在多行 |
| `pairs[i].scenarios[slip]` | 该片段的模型卖出佣金、买入佣金、卖出印花税、滑点成本、总成本、估算净盈亏 |
| `unmatched` | 按输入索引排列的剩余股数、成交价、日期、方向及原因；SELL 表示待回补敞口，BUY 表示无先前卖出的买入量 |
| `actual_gross_pnl` | 已配对部分的成交价差毛利合计，未扣费 |
| `actual_net_pnl` | 已配对部分的实付扣费净额；任一配对缺实付费用时为 `None`，不能将已知部分冒充完整合计 |
| `scenario_totals[slip]` | 各片段同档位模型费用及估算净盈亏之和 |

## 费用与实际收益

基础费用直接调用冻结的 `backtest.fees.fill`，沿用其 `money` 人民币分舍入（`ROUND_HALF_UP`）：每**原始成交笔**佣金 `max(5, round_cent(成交额 × 0.00025))`，卖出印花税 `round_cent(卖出成交额 × 0.0005)`，买入印花税为零，成交额本身也先按分舍入。每条输入是一笔独立的已成交记录，不推断同一委托或账单的合并计费方式。原始成交若被多个配对分割，成交金额和费用按累计数量比例分配到分，最后一段取得余数；最低佣金不因配对拆分重复收取。独立的多笔回补仍分别计收买入佣金。

具体分摊为 `round_cent(原笔金额 × 本次后累计配对股数 / 原笔股数) − round_cent(原笔金额 × 本次前累计配对股数 / 原笔股数)`，确保完整回补时每分均可对账。部分回补仅计入已配对部分的金额和费用；未配对量的费用份额不提前计入已实现盈亏。`unmatched` 列示剩余数量，不输出其费用份额。

每笔 `actual_gross_pnl = 已配对卖出成交金额 − 已配对买入成交金额`。若两侧每笔都提供已支付的佣金，且卖出同时提供已支付的印花税，`actual_net_pnl = actual_gross_pnl − 已支付佣金 − 已支付卖出印花税`。实付费用和模型费用始终分开；缺任一实付费用时 `actual_net_pnl` 为 `None`，不把估算费用冒充实付费用。买入 `Trade` 的印花税可省略（视为零）；卖出实付印花税必须明确提供。实付费用须为非负整分。

各滑点档是**估算情景**：`estimated_net_pnl = actual_gross_pnl − 模型卖出佣金 − 模型买入佣金 − 模型卖出印花税 − round_cent(2 × 每侧每股滑点 × 已配对股数)`。默认每侧每股 `0 / 0.01 / 0.02` 元，也可传入非空、互异的非负档位；每个配对片段的滑点成本按分舍入，再求合计。情景在已成交价格上单列额外滑点压力，费用基数保持输入成交额；实际净收益不会再扣情景滑点。这与 `backtest.fees.round_trip` 将滑点施加到报价、再重算成交额及税费的报价模拟用途不同，此处只复用 `fill` 的费用计算。

## 复算例

```python
from tools.t_pnl import calculate_t_pnl
from decimal import Decimal

result = calculate_t_pnl([
    ("2026-10-19", "SELL", "2.70", 2500),
    ("2026-10-20", "BUY", "2.65", 2500),
])
assert result.scenario_totals[Decimal("0")].total_cost == Decimal("13.38")
assert result.actual_net_pnl is None
```

成交价差毛利为 `(2.70 − 2.65) × 2500 = 125.00` 元。费用的**未舍入公式值**为两笔最低佣金 `5 + 5` 加卖出印花税 `6750 × 0.0005 = 3.375`，合计 **13.375** 元；`0.01` 档双侧滑点为 `2 × 0.01 × 2500 = 50` 元，未舍入合计 **63.375** 元。按逐笔人民币分舍入，印花税入账 `3.38` 元，所以输出零滑点费用 **13.38** 元、估算净收益 **111.62** 元；`0.01` 档费用 **63.38** 元、估算净收益 **61.62** 元。没有实付费用输入，因此实际净收益为 `None`。

| 每侧每股滑点（元） | 模型总成本（元） | 估算净盈亏（元） |
| --- | ---: | ---: |
| 0 | 13.38 | 111.62 |
| 0.01 | 63.38 | 61.62 |
| 0.02 | 113.38 | 11.62 |

实付费用示例同样只使用合成成交和假设账单费用：

```python
from tools.t_pnl import Trade

paid = calculate_t_pnl([
    Trade("2026-10-19", "SELL", Decimal("2.70"), 2500,
          commission=Decimal("5.00"), stamp_duty=Decimal("3.38")),
    Trade("2026-10-20", "BUY", Decimal("2.65"), 2500,
          commission=Decimal("5.00")),
])
assert paid.actual_net_pnl == Decimal("111.62")
assert paid.scenario_totals[Decimal("0.01")].estimated_net_pnl == Decimal("61.62")
```

模块是人工投研辅助纯计算：不读取持仓、交易记录或数据库，不连接券商，也不修改策略参数。

## 复跑与代码事实（审查 A 部分）

从仓库根目录运行，无第三方依赖：

```sh
python3 -B -m unittest discover -s tests -p test_t_pnl.py -v
python3 -B -m unittest discover -s tests -p test_backtest_stage1.py -v
```

- `TASK_ID`：T-PNL-CALC-01；基线分支 `feat/t-pnl-calc`，HEAD 与本地 `origin/main` 均为 `892d469e832c98fb58e88702ae1c615548ebb37c`。
- `CHANGE_SCOPE / CODE_DIFF_SUMMARY`：仅新增 `tools/t_pnl/calc.py`、`tools/t_pnl/__init__.py`、本 README 和 `tests/test_t_pnl.py`。接续会话逐件验真，保留原有核算实现和导出，补齐测试及口径说明。`TASK_CARD.md` 为派工单，不列入交付。
- `TEST_RESULT`：本次实跑专项 **21/21**、既有 Stage 1 相关回归 **20/20** 通过，均使用合成数据。专项覆盖佣金上下边界、仅卖侧印花税、三档及自定义滑点、零/负净额、多对多 FIFO、分币余数、部分成交、分批回补、未完成交易、T+0、实付费用隔离及异常输入。
- `STATIC_ANALYSIS_RESULT`：三个 Python 文件语法编译通过；README 中两段 Python 示例实际运行通过；四个新增文件逐项 `git diff --no-index --check` 无空白错误；敏感凭证模式及股票代码模式扫描无匹配。`git diff --exit-code HEAD` 通过，已有受控文件无改动，暂存区为空；工作区仅有派工单及上述四个交付文件未跟踪。
- `IMPACT_ANALYSIS`：仅标准库与既有 `backtest.config` / `backtest.fees` 只读导入；不接入日报或策略调用方，不修改数据库、接口、冻结策略、费用参数或封存数据。时间与输出规模随成交笔数及滑点档位数增长。
- `RISK_FOUND`：调用方须确保输入确为已成交记录并提供完整实付费用，缺费用时实际净额未知；同日配对提示不代表合规判定。上述检查属于工程验证，独立审查 B 部分、阿七核验及 qi 合并批准由相应人员执行；此处不宣告任务流程 DONE。
