# 吉林高速（601518.SH）做 T 规则 V1.0：Stage 2A 送审定版

版本 `jlgs-stage2a-rules-v1.0-final-review`。**状态：送审定版，待总指挥终核；`ready_for_stage2b=false`。**依据为总指挥对 Q01–Q14 的裁决及 qi 于 2026-10-22 的拍板。本稿只定规则，不运行行情策略回测、不产出策略收益或投资结论。未定项和终核门禁见 [逐项状态](STAGE2A_OPEN_QUESTIONS.md)；指标候选见 [Q07 定义](Q07_CANDIDATE_DEFINITIONS.md)。机器配置是 [`rules_v1.json`](../../backtest/rules_v1.json)，附录与其逐项一致。

## 1. 数据、价格与成交时序

唯一行情输入为仓库外 `jlgs_3y_raw.json`（733 个交易日，2023-10-09 至 2026-10-16）。开发段至 2025-09-30，样本外段自 2025-10-01 起，样本外首个真实交易日为 2025-10-09。价格带、状态阈值及实际成交价用**原始未复权人民币价**；MA20、MA60 用 Stage 1 `stage1-cash-dividend-v1` 的截至当日现金分红调整序列；成交量用原始日成交量。周线只在该周上交所最后交易日北京时间 15:00 之后确认，周价格阈值从原始周 K 取，不用调整价周收盘直接比较。

**成交模型（Q09 总指挥部分，待总指挥终核）：**只用日线 OHLC，不假设当日高低价先后。完整日收盘确认信号，最早下一交易日开盘成交；不得用当日高/低价倒推触发或假设有利成交。价带按信号日**原始收盘**落在含边界区间发出次日开盘候选单；次日开盘价仍须在相应价带内且全部状态、数量、费用门禁通过，否则不成交。候选单只活到该次开盘，未成交即撤销，下个收盘重新评价；不自动跨日续挂。开盘实际价加减预登记滑点，不能用信号日价格伪作成交价。任何开盘重验失败均记录取消原因。

## 2. 四状态、优先级和暂停

| 状态 | 进入和保持 | 执行限制与解除 |
| --- | --- | --- |
| A 箱体 | 原始日收盘在含边界 `[2.64, 3.00]` 且“均线走平”（Q07 待选） | 仅在全部门禁通过时评价机械做 T；收盘逐日重评 |
| B 转强 | 最近完整周原始周收盘 `>3.00`，并按所选 Q07 量比及当日 `MA20>MA60` 检查。已确认周条件锁存至下次完整周确认 | 暂停新卖出，进入时撤销全部未成交卖单；既有部分成交按实际股数入账。即使周条件仍锁存，日线 MA/量比不满足当日执行条件也不得新增卖出；下次周确认后重评 |
| C 转弱 | 原始日收盘 `<2.64` 且“均线走弱”**或**“放量”（分别由 Q07 选定） | 撤销 C 期间回补候选单，全面禁止回补，包括 2.62–2.68 低档。解除须同一已完成日满足原始收盘 **>2.64**、走弱不成立、放量不成立；解除后重新算价位、每股预计净收益、可买数量和费用门禁，绝不自动买入 |
| D 停摆/失效 | 周原始收盘首次从 `>=2.40` 到 `<2.40` 进入常规做 T 停摆，低于时持续；失效审查另由 Q13 两条件触发 | 不发常规卖出或回补；完整周收盘回到 `>=2.40` 且审查无未解决风险后才可重新评价，原挂单不恢复 |

状态显示优先级固定为 **D > C > B > A > NO_ACTION**；多个状态的暂停动作合并生效，显示较高状态不解除较低状态的禁令。无状态匹配时 `NO_ACTION`，不得推断额外卖出状态。B 的周条件与日线执行条件分开记录；周条件锁存不授权在当日日线条件失效时卖出。

Q13 的“跌破”是**首次向下穿越**，不是任意低位周。`2.30` 审查触发条件彼此独立：① 上一已确认周收盘 `>=2.30` 且本周 `<2.30`；② 连续两根已确认完整周收盘均 `<2.30`。第二周可作为持续审查触发，避免第一条件持续重复告警。`2.40` 首次从 `>=2.40` 到 `<2.40` 是进入事件，随后每周 `<2.40` 为持续停摆；从下方回到 `>=2.40` 才进入恢复审查。首次可用完整周若已在阈值下方，直接记为低位持续状态，不能伪造向下穿越，但仍按停摆/连续周审查条件处置。

## 3. 仓位、批次、价带和数量

核心仓始终不动，机动仓按初始量归一为 `1.0`，四个名义卖出批次各为初始机动仓的 `0.25`。**本版只冻结第一、二批的执行范围**：两批都只能在 A 箱体的原始收盘 `[2.64, 3.00]` 内评价，未另设不同触发档；原稿第一批 `2.98–3.03`、第二批 `3.08–3.12` 只作历史来源记录，不能据其 `3.00` 以上部分自动卖出。第三、四批价格带保持 `null`，注明“**开发段回测后另行冻结**”，当前无执行许可。卖出次日开盘价若 `>3.00`，取消该笔；不从原稿高价带恢复权限。

回补带为原始价格 `[2.76,2.82]` 和 `[2.62,2.68]`，均含边界。每一已卖股按 FIFO 建立卖出—回补关联，两档均只可回补尚未回补的已卖机动股。低档带内而 C 未成立时准许评价第二带回补，即使原始价 `<2.64`；C 成立则两带一律禁买。不得净加仓，买回数量不得超过对应未回补量，单日不做反向回转，买入股到 T+1 才可再卖。

**成交前**逐笔预检：新卖出成交后的未回补余额不得超过**初始机动仓股数的 50%**，等于 50% 可以，超出必须拒绝。只接受可被 4 整除且每批符合 A 股交易单位的私有机动股数；不满足则运行前拒绝，不能自动取整或改批次比例。实际核心/机动股数、成本及可反推金额只由仓库外私有配置注入，不进 Git。

## 4. 回补净收益门禁与费用

回补订单提交时，用**当时可见报价**作为预计买入基价，按预登记滑点加价，禁止事后以当日实际最低价反推门禁通过。拟回补股数的预计每股净收益 = `（按 FIFO 对应的已实现卖出成交额 − 该份股数分摊的已付卖佣和印花税 − 预计买入成交额 − 预计买佣）÷ 拟回补股数`。同一卖出成交的已付费用按回补股数占该笔卖出股数的比例分摊；不会为分拆份额重新计算最低佣金。回补实成交前按可见开盘报价重验数量、现金、状态及预计净收益；达不到门槛就取消。

费用沿用 Stage 1：佣金双边万 2.5、**每边每笔最低 5 元**；卖出印花税 0.05%，买入无印花税；乐观/基准/压力滑点分别为 0、0.01、0.02 元每股每边。每笔费用按人民币分四舍五入，现金和股数非负。门槛单位定为**每股净收益（元/股）**，标记为“**qi 授权阿七选定、待总指挥终核确认**”；门槛**数值仍为 `null`**，Stage 2B 开发段预先登记候选并比较，锁定后 Stage 3 样本外一次性验证，禁止用样本外结果选值。数值未定时执行器不能放行回补。Q05 对“已实现卖出现金流按股数分摊”的理解需在 PR 说明单列待终核确认。

## 5. 结局、分红与审查

每笔已卖股在报表截点只属于 `REBOUGHT`、`OPEN_UNREBOUGHT`、`INVALIDATION_REVIEW` 之一；部分回补按股数拆分，三类股数合计等于累计卖出股数。未回补和审查中的卖出腿不得算做 T 成功；未实现盯市、卖飞敞口单列。实际收到的公告含税分红现金与**已实现做 T 净额分列**。跨除息日尚未回补的卖出腿附“错过分红”备查项，不混入已实现做 T 净额，也不与复权价差重复计算。分红现金只对除息日前收盘实际持股在除息日交易前入账；不推算个人分红税或税后个人收益。

## 6. 预热和版本门禁

基准预热为 **61 个交易日**：MA20 至少 20 日、MA60 至少 60 日、前一日 MA60 比较需 61 日、前 20 日均量比需 21 日、完整已确认周至少 1 根。第 61 个交易日 **2024-01-02 收盘后**才有完整基准信号，**2024-01-03** 是理论最早执行日。开发段 485 个交易日中，基准前 60 日只预热、425 日有理论信号评价容量、424 日有开发段内理论次日执行容量；这些不是交易次数或收益。

Q07 最终定义若需要更长日线或更多完整周，首信号日按 `max(61 日基准、所有选定指标所需的最晚日线交易日、所需最后完整周确认交易日)` 用真实交易日历重算；首执行日为其后的下一真实交易日，开发段容量同步重算。Q07 从候选中由 qi 选择、总指挥确认，不得用样本外结果倒选。第三、四批价带待开发段回测后另行冻结；净门槛数值待 Stage 2B 开发段选择并在 Stage 3 样本外前锁定；成交模型和门槛单位待总指挥终核。上述门禁未解除，`ready_for_stage2b=false`。

## 附录：机器配置逐项快照

以下 JSON 与 `backtest/rules_v1.json` 完全一致，由 `python3 -m backtest.validate_rules_v1` 静态核验。修改机器规则必须同步正文和快照。

<!-- RULES_JSON_BEGIN -->
```json
{
  "version": "jlgs-stage2a-rules-v1.0-final-review",
  "freeze_status": "PENDING_COMMANDER_FINAL_REVIEW",
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
    "fill_model": "daily_OHLC_signal_close_next_session_open_no_intraday_path_assumption_pending_commander_review",
    "fill_model_commander_confirmation": null,
    "fill_model_commander_confirmation_null_reason": "pending_commander_final_review",
    "order_lifetime": "signal_close_to_next_session_open_only_recheck_all_gates_before_fill",
    "weekly_confirmation": "after_15_00_Asia_Shanghai_on_final_SSE_session_of_week",
    "commission_one_side_rate": "0.00025",
    "minimum_commission_rmb_per_fill": "5.00",
    "sell_stamp_duty_rate": "0.0005",
    "slippage_rmb_per_share_per_side": [
      "0",
      "0.01",
      "0.02"
    ],
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
    "share_rounding_and_lot_size": "private_mobile_shares_divisible_by_four_and_each_batch_valid_A_share_sell_lot_else_reject_before_run"
  },
  "states": {
    "A": {
      "box_low_raw_rmb": "2.64",
      "box_high_raw_rmb": "3.00",
      "flat_ma_definition": null,
      "action": "observe_mechanical_T_only_after_all_gates_resolved",
      "definition_candidates": "docs/backtest/Q07_CANDIDATE_DEFINITIONS.md"
    },
    "B": {
      "weekly_close_above_raw_rmb": "3.00",
      "ma20_above_ma60": true,
      "volume_to_20_day_mean_approx": "1.3",
      "volume_comparison_definition": null,
      "action": "pause_new_sells",
      "weekly_condition_latch": "confirmed_weekly_condition_until_next_completed_weekly_confirmation",
      "daily_execution_gate": "MA20_above_MA60_and_selected_B_volume_definition_required_each_sell_day",
      "definition_candidates": "docs/backtest/Q07_CANDIDATE_DEFINITIONS.md"
    },
    "C": {
      "daily_close_below_raw_rmb": "2.64",
      "weak_ma_or_high_volume": true,
      "weak_ma_definition": null,
      "high_volume_definition": null,
      "release_definition": "daily_raw_close_above_2_64_and_weak_MA_false_and_high_volume_false",
      "action": "cancel_pending_buybacks_and_pause_buybacks_until_release_then_recalculate_without_auto_buy",
      "definition_candidates": "docs/backtest/Q07_CANDIDATE_DEFINITIONS.md"
    },
    "D": {
      "weekly_close_below_stop_raw_rmb": "2.40",
      "weekly_close_below_review_raw_rmb": "2.30",
      "consecutive_weeks_below_review": 2,
      "review_logical_operator": "OR",
      "stop_transition": "first_weekly_cross_from_at_or_above_2_40_to_below_2_40_then_persist_while_below",
      "review_trigger": "first_weekly_cross_from_at_or_above_2_30_to_below_2_30_OR_two_consecutive_weekly_closes_below_2_30",
      "release_definition": "confirmed_weekly_raw_close_at_or_above_2_40_and_no_unresolved_invalidation_review_risk_then_reevaluate_without_restoring_orders",
      "action": "stop_regular_T_and_review_at_review_threshold"
    },
    "priority": [
      "D",
      "C",
      "B",
      "A",
      "NO_ACTION"
    ],
    "pause_actions_combine": true,
    "unclassified_action": "NO_ACTION"
  },
  "orders": {
    "sell_bands_raw_rmb_inclusive": [
      [
        "2.64",
        "3.00"
      ],
      [
        "2.64",
        "3.00"
      ],
      null,
      null
    ],
    "sell_band_interpretation": "approved_close_execution_scope_for_first_two_batches_not_distinct_trigger_levels",
    "sell_band_original_source_only": [
      [
        "2.98",
        "3.03"
      ],
      [
        "3.08",
        "3.12"
      ]
    ],
    "sell_bands_three_four_null_reason": "freeze_after_development_backtest",
    "buyback_bands_raw_rmb_inclusive": [
      [
        "2.76",
        "2.82"
      ],
      [
        "2.62",
        "2.68"
      ]
    ],
    "buyback_band_to_sold_batch_mapping": "FIFO_per_share_sold_to_rebought_ledger",
    "band_trigger_and_fill_policy": "close_in_band_signal_next_session_open_if_open_still_in_band_and_all_gates_pass",
    "pause_B_pending_sell_order_policy": "cancel_all_unfilled_sell_orders_partial_fills_count_actual_quantity",
    "max_unreplenished_fraction_before_sell_stop": "0.5",
    "sell_cap_check_timing": "before_each_new_sell_fill_reject_if_resulting_unreplenished_exceeds_half_initial_mobile_shares",
    "low_buyback_band_when_C_false": "allowed_subject_to_all_other_gates",
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
    "expected_buy_quote_estimator": "visible_quote_at_order_submission_no_ex_post_daily_low",
    "paid_sell_fee_allocation": "pro_rata_by_shares_within_each_realized_sell_fill_no_recalculated_minimum_commission",
    "threshold_unit": "RMB_per_share_approved_by_qi_delegation_to_Aqi_pending_commander_final_review",
    "minimum_net_threshold_null_reason": "numeric_value_to_be_selected_in_stage2b_development_and_locked_before_stage3_OOS",
    "candidate_generation": "compare_pre_registered_candidates_on_development_only_then_lock_before_OOS",
    "out_of_sample_tuning_allowed": false
  },
  "outcomes": {
    "unit": "sold_mobile_share_lot",
    "categories": [
      "REBOUGHT",
      "OPEN_UNREBOUGHT",
      "INVALIDATION_REVIEW"
    ],
    "partial_buyback": "split_lot_by_quantity",
    "reconciliation": "sold_quantity_equals_rebought_plus_open_plus_review_quantity",
    "success_category": "REBOUGHT_ONLY_IF_NET_GATE_MET",
    "open_unreplenished_success": false,
    "unrealized_mark_to_market_separate": true,
    "realized_T_net_and_received_dividend_cash_separate": true,
    "open_sell_leg_missed_dividend_memo": true,
    "dividend_adjustment_double_count_allowed": false
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
    "approval": "approved_base_61_sessions_recalculate_if_Q07_requires_longer_history",
    "recalculation": "first_signal_date=max(date_of_session_61,date_of_all_Q07_daily_window_completion,date_of_required_completed_weekly_confirmation);first_execution=next_actual_trading_session",
    "base_dates_are_theoretical_until_Q07_selected": true
  },
  "blocking_question_ids": [
    "Q01",
    "Q07",
    "Q09",
    "Q14"
  ],
  "unresolved_fields": {
    "Q01": [
      "orders.sell_bands_raw_rmb_inclusive[2]",
      "orders.sell_bands_raw_rmb_inclusive[3]"
    ],
    "Q07": [
      "states.A.flat_ma_definition",
      "states.B.volume_comparison_definition",
      "states.C.weak_ma_definition",
      "states.C.high_volume_definition"
    ],
    "Q14": [
      "net_gate.minimum_net_threshold"
    ],
    "Q09": [
      "execution.fill_model_commander_confirmation"
    ]
  }
}
```
<!-- RULES_JSON_END -->
