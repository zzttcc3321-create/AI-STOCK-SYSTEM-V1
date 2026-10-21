# 吉林高速（601518.SH）做 T 规则 V1.0：Stage 2A 送确认冻结稿

版本 `jlgs-stage2a-rules-v1.0-draft`；依据为总指挥 2026-10-22 四状态裁决及本任务卡。**冻结状态：待总指挥与 qi 确认；`ready_for_stage2b=false`。**本稿冻结已明确的数字、边界和禁止事项；空缺保留为 `null` 并列入 [待决清单](STAGE2A_OPEN_QUESTIONS.md)。未决项解决并经 qi 确认完整规则前，策略执行器必须拒绝启动 Stage 2B，不能据此选最优门槛、发布有效性或投资结论。本稿不含行情策略回测。

机器定义见 [`backtest/rules_v1.json`](../../backtest/rules_v1.json)。附录完整复制机器配置，由 `python3 -m backtest.validate_rules_v1` 逐字段比较，并检查阈值、优先级和未决项封锁。文中“候选”不表示已获批准。

## 1. 时间、数据与因果顺序

唯一输入为仓库外 `jlgs_3y_raw.json`：733 个交易日，2023-10-09 至 2026-10-16。开发段为 2023-10-09 至 2025-09-30，样本外段自 2025-10-01 起，首个实际行情日 2025-10-09；分段依据真实交易日，不能把休市日伪作交易日。Stage 1 已核对数据与公告，见 [方法说明](BACKTEST_METHOD.md)。2023-10-09 可用于指标预热；缺足够历史的日期不可产生交易信号。

价格带、2.64/3.00/2.40/2.30 元状态阈值及实际成交，均使用**原始未复权人民币股价**。MA20、MA60 及任何价格趋势指标，使用 Stage 1 `stage1-cash-dividend-v1` 的截至当日现金分红调整序列；绝不用复权价成交。成交量用原始日成交量。日信号在完整收盘后才可见；周线只在该周上交所最后交易日北京时间 15:00 **之后**确认；最早下一交易日执行，不使用当日收盘作可成交价。周线确认时点和缺行门禁沿用 Stage 1；周收盘价阈值须从**原始**周 K 取得，不能直接把 Stage 1 调整价 `WeeklyBar.close` 与 3.00/2.40/2.30 元比较。信号到实际成交的触发与填价细节见 Q09，未决时不可假设触价即成交。

## 2. 四状态与安全优先级

下表的严格大于/小于以原始收盘价判定，等号不满足“站上/跌破”。`MA20 > MA60` 在日线调整收盘计算。状态只用当时已确认的日线及周线信息。

| 状态 | 进入谓词 | 行为 | 退出/重评 |
| --- | --- | --- | --- |
| A 箱体 | 日收盘位于 **[2.64, 3.00]** 元且均线走平 | 观察并仅在所有批次及门禁条件成立时机械做 T | 每个收盘重评；“走平”的窗口与容差待 Q07 |
| B 转强 | 已确认周收盘 **>3.00** 元，日线 MA20 **>** MA60，成交量达到 20 日均量**约 1.3 倍** | 暂停新卖出；已挂单如何处理待 Q03 | 完成新日线或周线后重评；周状态是否锁存到下一周待 Q08，量比口径待 Q07 |
| C 转弱 | 日收盘 **<2.64** 元，且均线走弱**或**放量 | 暂停回补；跌破 2.64 且放量不接飞刀 | 解除条件待 Q04；“走弱/放量”待 Q07 |
| D 停摆/失效 | 已确认周收盘 **<2.40** 元则停止常规做 T；已确认周收盘 **<2.30** 元，**或**连续两周周收盘 <2.30 元则进入失效审查 | 不发常规卖出或回补；保留未回补敞口并单列审查 | 恢复条件待 Q08；2.30 的逻辑冗余与“跌破”含义待 Q13 |

一个收盘可能满足多个谓词，亦可能无谓词成立。例如日收盘 >3.00 而转强三条件不齐，不得自动归入 A。**待确认的安全显示优先级候选为 D > C > B > A > NO_ACTION**；无状态匹配即 `NO_ACTION`，且任何状态的暂停动作优先于常规订单。此优先级和状态锁存尚需 Q08 裁决，当前仅作为冲突检测的候选，不能让交易执行器据其下单。B 和 A 的价格区间存在衔接问题：第一卖出带有 3.00 以上部分，第二带完全高于 A 上沿，见 Q01/Q03/Q09。

## 3. 仓位、卖出与回补

核心仓始终不动。机动仓以 `1.0` 归一化，四个卖出批次各占**初始机动仓的 0.25**，只卖可卖的机动仓。实际 `core_shares_external`、`mobile_shares_external`、`batch_shares_external_derived_from_mobile_shares` 由仓库外本地配置在运行时注入；实际股数、持仓成本和可反推它们的金额不进入 Git/报告。不能净加仓；回补数量不超过对应已卖出的机动股数量；单日不能反向回转，买入股 T+1 才可卖。

原稿卖出第一批原始报价带 **[2.98, 3.03]** 元、第二批 **[3.08, 3.12]** 元，均含边界；第三、四批价格带缺失，保留 `null`（Q01）。原稿回补带 **[2.76, 2.82]**、**[2.62, 2.68]** 元，均含边界；两档对应卖出批次/数量未知（Q02）。第二回补带跨越 C 的 2.64 下界，满足 C 时必须暂停回补；C 未满足但低于箱体下界时是否允许回补仍需 Q09。报价进入区间的观察、挂单有效期、跳空、当日高低价与实际成交价之间的关系待 Q09；不允许以区间边界直接伪造成交。

未回补卖出量 **>机动仓 0.5** 后，停止继续卖出。按字面可能在第三批成交后到 0.75 才触发，与“成交前上限为 0.5”不同；检查时点需 Q10 裁决。未回补量不能因同日买回清零。批次数量须符合 A 股整手/零股规则，归一化 0.25 对真实股数如何取整待 Q11。B 时已挂批次处理见 Q03。

## 4. 净收益门禁及费用

每次拟回补前，对**拟回补的已卖股数**计算预计净额：该部分已实现卖出现金流（卖出成交额 − 已付卖佣 − 已付印花税，按分配给该数量的实际费用）− 预计买入成交额（报价 + 对应每股每边滑点）− 预计买佣。各成交逐笔人民币分四舍五入，不能将 5 元最低佣金跨成交合并。仅当预计净额达到**待定门槛**且现金/可买数量、状态 C/D、T+1 和同日方向检查均通过，才可回补。预计报价的取得方法、部分回补费用分配及门槛单位仍待 Q05/Q14；因此本机制当前**不能放行实际回补**。分红现金不用于制造做 T 成功，独立入账。

固定费用：佣金双边万 2.5、每边每笔至少 5 元；卖出印花税 0.05%，买入无；滑点乐观/基准/压力各 **0 / 0.01 / 0.02 元每股每边**，买入加、卖出减；现金和股数始终非负。与 Stage 1 `fees.py` 和 `ledger.py` 的逐笔模型一致。门槛数值本阶段不定：Stage 2B 只在开发段比较**预先登记**的候选值及三档滑点，锁定后由 Stage 3 样本外验证；不能从样本外反向挑选。候选网格、评价指标及决定者见 Q14，当前无任何候选数值或最优结果。

## 5. 卖出后结局与统计

以“机动仓卖出股数的批次份额”为统计单元；部分回补把该份额按数量拆开。每一已卖股在报表截点只能属于 **REBOUGHT**（已回补）、**OPEN_UNREBOUGHT**（未回补）、**INVALIDATION_REVIEW**（进入失效审查且尚未回补）之一；三类股数之和必须等于累计卖出股数。进入 D 的失效审查后，未回补份额转列审查类；若后续状态如何解除尚无裁决，不得把审查类自动转成成功。已回补交易可按实际卖买现金流和费用计算已实现净额；未回补或审查类**不得计作做 T 成功**。未实现盯市及被卖飞敞口单列，不混入已实现做 T 净额。计数同时报告完成/未完成/审查批次份额与对应股数比例；不同批次部分成交的费用分摊方式待 Q05。

## 6. 预热期建议及开发段影响

用完整历史而非日历天数预热。日线 MA20 需 20 个交易收盘，MA60 需 60 个；若“走平/走弱”比较当前和前一日 MA60，需 **61** 个。20 日均量若采用**不含当日**的前 20 个交易日均量，当日量比最早需第 **21** 个交易日；含当日方案见 Q07。周收盘至少需一个**完整、已确认**的上交所交易周；当前不设周均线，因此周线自身只需 1 根完整周 K，若后续增加周线均线必须重新计算。首周 2023-10-09 至 2023-10-13 完整，周线 2023-10-13 15:00 后可见。全部条件共同取最晚者，建议第 **61** 个交易日 2024-01-02 收盘后首次评价，**2024-01-03** 最早执行；该定义待 Q06/Q07 确认。

仅用 Stage 1 因子表的交易日期计数，开发段共有 **485** 个交易日。按 61 日建议，前 **60** 个只作预热；可评价信号日 **425** 个（2024-01-02 至 2025-09-30），若必须下一交易日且执行也留在开发段，潜在执行日 **424** 个（2024-01-03 至 2025-09-30）。这些是日历容量，**不是交易次数、收益或参数回测**。样本外指标可沿用此前真实历史预热，但不得沿用开发段未来信息；边界处理待 Q14。

## 7. 分红税、隐私与版本门禁

除息日交易前，对前收盘持有股数将公告**含税**每股现金额单独入现金；2024-07-04 为 0.09 元、2025-07-03 为 0.086 元、2026-07-06 为 0.13 元。指标调整因子仅用于价格序列，分红现金仅入账一次，不将调整价差当作再次分红。真实个人股息税由持有期决定，缺真实持有期便**不推算税后个人收益**；策略报告将含税现金分红与做 T 已实现净额分别展示，并注明税后未核算。卖出后跨除息日的权益归属与未回补敞口评价待 Q12。

当前版本的机器配置含 `null`、`blocking_question_ids` 和 `ready_for_stage2b=false`。任何正式回测或实盘接线，须先形成新版本，逐项记录总指挥与 qi 对 Q01–Q14 的裁决，并通过一致性校验；对开放问题的候选定义不自动生效。

## 附录：机器配置逐项快照

以下 JSON 必须与 `backtest/rules_v1.json` **完全一致**。修改任何一处必须同步两份并通过校验。

<!-- RULES_JSON_BEGIN -->
```json
{
  "version": "jlgs-stage2a-rules-v1.0-draft",
  "freeze_status": "PENDING_COMMANDER_AND_QI",
  "ready_for_stage2b": false,
  "symbol": "601518.SH",
  "source": {
    "filename": "jlgs_3y_raw.json",
    "rows": 733,
    "first_trade_date": "2023-10-09",
    "last_trade_date": "2026-10-16",
    "development_start": "2023-10-09",
    "development_end": "2025-09-30",
    "out_of_sample_start": "2025-10-01",
    "out_of_sample_first_trade_date": "2025-10-09"
  },
  "prices_and_dividends": {
    "execution_price": "raw_unadjusted",
    "indicator_price": "stage1-cash-dividend-v1",
    "price_band_basis": "raw_unadjusted",
    "dividend_per_share": "announced_gross_cash",
    "dividend_cash_booking": "ex_date_before_trades_on_prior_close_shares",
    "personal_dividend_tax": "not_inferred_without_holding_period",
    "strategy_dividend_tax_treatment": "gross_cash_separate_from_T_pnl"
  },
  "execution": {
    "signal_evaluation": "after_completed_daily_close_and_confirmed_weekly_close_only",
    "earliest_fill": "next_trading_session_after_signal",
    "weekly_confirmation": "after_15_00_Asia_Shanghai_on_final_SSE_session_of_week",
    "commission_one_side_rate": "0.00025",
    "minimum_commission_rmb_per_fill": "5.00",
    "sell_stamp_duty_rate": "0.0005",
    "slippage_rmb_per_share_per_side": ["0", "0.01", "0.02"],
    "money_rounding": "RMB_cent_HALF_UP_each_fill",
    "settlement": "T_plus_1_no_same_day_direction_reversal",
    "cash_and_shares_nonnegative": true
  },
  "position": {
    "core_shares_parameter": "core_shares_external",
    "mobile_shares_parameter": "mobile_shares_external",
    "batch_shares_parameter": "batch_shares_external_derived_from_mobile_shares",
    "mobile_fraction": "1.0",
    "core_trading_allowed": false,
    "net_addition_allowed": false,
    "buyback_only_previously_sold_mobile_shares": true,
    "sell_batch_count": 4,
    "sell_batch_fraction_of_mobile": "0.25",
    "share_rounding_and_lot_size": null
  },
  "states": {
    "A": {
      "box_low_raw_rmb": "2.64",
      "box_high_raw_rmb": "3.00",
      "flat_ma_definition": null,
      "action": "observe_mechanical_T_only_after_all_gates_resolved"
    },
    "B": {
      "weekly_close_above_raw_rmb": "3.00",
      "ma20_above_ma60": true,
      "volume_to_20_day_mean_approx": "1.3",
      "volume_comparison_definition": null,
      "action": "pause_new_sells"
    },
    "C": {
      "daily_close_below_raw_rmb": "2.64",
      "weak_ma_or_high_volume": true,
      "weak_ma_definition": null,
      "high_volume_definition": null,
      "release_definition": null,
      "action": "pause_buybacks"
    },
    "D": {
      "weekly_close_below_stop_raw_rmb": "2.40",
      "weekly_close_below_review_raw_rmb": "2.30",
      "consecutive_weeks_below_review": 2,
      "review_logical_operator": "OR",
      "release_definition": null,
      "action": "stop_regular_T_and_review_at_review_threshold"
    },
    "priority_candidate": ["D", "C", "B", "A", "NO_ACTION"],
    "priority_approval": null,
    "unclassified_action": "NO_ACTION"
  },
  "orders": {
    "sell_bands_raw_rmb_inclusive": [["2.98", "3.03"], ["3.08", "3.12"], null, null],
    "buyback_bands_raw_rmb_inclusive": [["2.76", "2.82"], ["2.62", "2.68"]],
    "buyback_band_to_sold_batch_mapping": null,
    "band_trigger_and_fill_policy": null,
    "pause_B_pending_sell_order_policy": null,
    "max_unreplenished_fraction_before_sell_stop": "0.5",
    "sell_cap_check_timing": null,
    "below_2_64_high_volume_catch_falling_knife": false,
    "only_one_direction_per_day": true
  },
  "net_gate": {
    "applies_before_buyback": true,
    "expected_net_rmb_formula": "allocated_realized_sell_cash_minus_expected_buy_gross_and_buy_commission",
    "sell_cash_includes_sell_commission_and_stamp": true,
    "expected_buy_price_includes_scenario_slippage": true,
    "dividends_excluded_from_T_gate": true,
    "minimum_net_threshold": null,
    "expected_buy_quote_estimator": null,
    "threshold_unit": null,
    "candidate_generation": "compare_pre_registered_candidates_on_development_only_then_lock_before_OOS",
    "out_of_sample_tuning_allowed": false
  },
  "outcomes": {
    "unit": "sold_mobile_share_lot",
    "categories": ["REBOUGHT", "OPEN_UNREBOUGHT", "INVALIDATION_REVIEW"],
    "partial_buyback": "split_lot_by_quantity",
    "reconciliation": "sold_quantity_equals_rebought_plus_open_plus_review_quantity",
    "success_category": "REBOUGHT_ONLY_IF_NET_GATE_MET",
    "open_unreplenished_success": false,
    "unrealized_mark_to_market_separate": true
  },
  "warmup_proposal": {
    "daily_ma20_sessions": 20,
    "daily_ma60_sessions": 60,
    "prior_ma_for_slope_sessions": 61,
    "volume_mean_prior_sessions": 20,
    "volume_ratio_first_session": 21,
    "completed_weekly_bars": 1,
    "earliest_full_signal_session_index": 61,
    "earliest_full_signal_date": "2024-01-02",
    "earliest_execution_date": "2024-01-03",
    "development_sessions": 485,
    "full_signal_sessions_in_development": 425,
    "earliest_execution_through_dev_sessions": 424,
    "approval": null
  },
  "blocking_question_ids": ["Q01", "Q02", "Q03", "Q04", "Q05", "Q06", "Q07", "Q08", "Q09", "Q10", "Q11", "Q12", "Q13", "Q14"]
}
```
<!-- RULES_JSON_END -->
