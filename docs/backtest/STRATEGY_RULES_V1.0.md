# 吉林高速（601518.SH）做 T 规则 V1.0：Stage 2A 送审定版

版本 `jlgs-stage2a-rules-v1.0-r1-final-review`。**状态：R1 返修送审，待总指挥复审；`ready_for_stage2b=false`。**依据为总指挥 Stage 2A 终核 `PASS_WITH_CHANGE`、R1 任务卡，以及 qi 于 2026-10-22 以“你决定”授权阿七按送审推荐项定版 Q07 与卖出触发。本稿只定规则，不运行行情策略回测、不产出策略收益或投资结论。未定项和复审门禁见 [逐项状态](STAGE2A_OPEN_QUESTIONS.md)；指标选择及归档候选见 [Q07 定义](Q07_CANDIDATE_DEFINITIONS.md)。机器配置是 [`rules_v1.json`](../../backtest/rules_v1.json)，附录与其逐项一致。

## 1. 数据、价格与成交时序

唯一行情输入为仓库外 `jlgs_3y_raw.json`（733 个交易日，2023-10-09 至 2026-10-16）。开发段至 2025-09-30，样本外段自 2025-10-01 起，样本外首个真实交易日为 2025-10-09。价格带、状态阈值及模拟成交价用**原始未复权人民币价**；MA20、MA60 用 Stage 1 `stage1-cash-dividend-v1` 的截至当日现金分红调整序列；成交量用原始日成交量。周线只在该周上交所最后交易日北京时间 15:00 之后确认，周价格阈值从原始周 K 取，不用调整价周收盘直接比较。

**日线开盘成交近似模型（Q09 已解决）：**只用日线 OHLC，不假设当日高低价先后，也不声称验证真实盘口成交。完整日收盘确认信号，最早下一交易日开盘模拟成交；不得用当日高/低价倒推触发。卖出在信号日按批次触发价判断，回补在信号日判定价带与净收益门禁。次日开盘价**仅用于**确认仍在该单允许价带，以及按预登记滑点计算模拟成交价；开盘跳空超出价带则未成交。开盘跳空导致的模拟成交净额差异进入成交结果，不回改前一日的门禁判定。候选单只活到该次开盘，未成交即撤销，下个收盘重新评价；不自动跨日续挂。非价格约束（如剩余股数、现金、暂停状态）仍须满足。取消原因逐笔记录。

## 2. 四状态、优先级和暂停

| 状态 | 进入和保持 | 执行限制与解除 |
| --- | --- | --- |
| A 箱体 | 原始日收盘在含边界 `[2.64, 3.00]` 且 A-F1 走平成立 | 仅在全部门禁通过时评价机械做 T；收盘逐日重评 |
| B 转强 | 最近完整周原始周收盘 `>3.00`，确认周最后交易日 B-V1 量比达标，周资格锁存至下次完整周确认；拟新增卖出日另须 `MA20>MA60` 且 B-V1 当日量比达标 | 暂停新卖出，进入时撤销全部未成交卖单；既有部分成交按实际股数入账。周资格锁存不取代当日条件；下次周确认后重评 |
| C 转弱 | 原始日收盘 `<2.64`，且 C-W1 走弱或独立的 C-V1 放量成立 | 暂停新增卖出，撤销 C 期间回补候选单，全面禁止回补，包括 2.62–2.68 低档。解除须同一已完成日满足原始收盘 **>2.64**、走弱不成立、放量不成立；解除后重新算价位、每股预计净收益、可买数量和费用门禁，绝不自动买入 |
| D 停摆/失效 | 周原始收盘首次从 `>=2.40` 到 `<2.40` 进入常规做 T 停摆，低于时持续；失效审查另由 Q13 两条件触发 | 不发常规卖出或回补；完整周收盘回到 `>=2.40` 且审查无未解决风险后才可重新评价，原挂单不恢复 |

状态显示优先级固定为 **D > C > B > A > NO_ACTION**；多个状态的暂停动作合并生效，显示较高状态不解除较低状态的禁令。无状态匹配时 `NO_ACTION`，不得推断额外卖出状态。B 的周条件与日线执行条件分开记录；周条件锁存不授权在当日日线条件失效时卖出。

**Q07 已选定义（2026-10-22；选择人：qi 授权阿七按推荐定版）：**`P_t` 为截至 `t` 的 Stage 1 现金分红调整收盘价，`MAk_t=mean(P_{t-k+1}…P_t)`；`V_t` 为原始日量，`avgV(20,t-1)=mean(V_{t-20}…V_{t-1})`。A-F1 走平：`abs(MA20_t/MA20_{t-1} − 1) <= 0.001` 且 `abs(MA60_t/MA60_{t-1} − 1) <= 0.0005`。C-W1 走弱：`MA20_t < MA20_{t-1}` 且 `MA60_t < MA60_{t-1}`。B-V1：确认周最后交易日 `wlast` 的 `V_wlast / avgV(20,wlast-1) >= 1.30`，该周资格锁存至下一完整周确认；拟新增卖出日还须 `V_t / avgV(20,t-1) >= 1.30` 逐日重验。C-V1 放量独立定义为 `V_t / avgV(20,t-1) >= 1.30`，不从 B 周资格推导。量比分母为零或均线比值分母为零时对应条件不成立并记数据异常；未确认周的数据不得提前使用。

Q13 的“跌破”是**首次向下穿越**，不是任意低位周。`2.30` 审查触发条件彼此独立：① 上一已确认周收盘 `>=2.30` 且本周 `<2.30`；② 连续两根已确认完整周收盘均 `<2.30`。第二周可作为持续审查触发，避免第一条件持续重复告警。`2.40` 首次从 `>=2.40` 到 `<2.40` 是进入事件，随后每周 `<2.40` 为持续停摆；从下方回到 `>=2.40` 才进入恢复审查。首次可用完整周若已在阈值下方，直接记为低位持续状态，不能伪造向下穿越，但仍按停摆/连续周审查条件处置。

## 3. 仓位、批次、价带和数量

核心仓始终不动，机动仓按初始量归一为 `1.0`，四个名义卖出批次各为初始机动仓的 `0.25`。**第一、二批卖出触发与执行范围：**状态 A 生效且当日原始收盘 `>=2.95`，产生第一批卖出候选；**第一批实际成交后**，状态 A 生效且当日原始收盘 `>=3.00`，才产生第二批卖出候选。两批执行范围均为原始收盘及次日开盘含边界 `[2.64,3.00]`，因此第二批信号收盘恰为 `3.00`；`3.00` 以上不自动卖出。每次收盘最多一个批次候选，第一批未成交时第二批不得触发；每批发生实际成交（包括部分成交）后不再重复触发，该笔只按实际股数入账。未成交候选单在次日开盘撤销，下个合格收盘只重评尚未成交的同一批次。原稿 `2.98–3.03`、`3.08–3.12` 仅作历史来源记录。第三、四批价带及触发均保持 `null`，**开发段回测后另行冻结**，当前无执行许可。次日开盘价超出 `[2.64,3.00]` 时该单未成交。

回补带为原始价格 `[2.76,2.82]` 和 `[2.62,2.68]`，均含边界。每一已卖股按 FIFO 建立卖出—回补关联，两档均只可回补尚未回补的已卖机动股。低档带内而 C 未成立时准许评价第二带回补，即使原始价 `<2.64`；C 成立则两带一律禁买。不得净加仓，买回数量不得超过对应未回补量，单日不做反向回转，买入股到 T+1 才可再卖。

**成交前**逐笔预检：新卖出成交后的未回补余额不得超过**初始机动仓股数的 50%**，等于 50% 可以，超出必须拒绝。只接受可被 4 整除且每批符合 A 股交易单位的私有机动股数；不满足则运行前拒绝，不能自动取整或改批次比例。实际核心/机动股数、成本及可反推金额只由仓库外私有配置注入，不进 Git。

## 4. 回补净收益门禁与费用

回补门禁只使用**信号日收盘时及此前可得信息**。历史日线数据无买一/卖一报价，用信号日原始收盘价作预计买入基价，叠加预登记的相应滑点档位；不能用次日开盘价或事后日内低价判定前一日门禁。拟回补股数的预计每股净收益 = `（按 FIFO 对应的已实现卖出成交额 − 该份股数分摊的已付卖佣和印花税 − 预计买入成交额 − 预计买佣）÷ 拟回补股数`。同一卖出成交的已付费用按回补股数占该笔卖出股数的比例分摊，不为分拆份额重新计算最低佣金。次日开盘仅检验仍在原允许价带并计算模拟成交价；跳空造成的实际净额差异如实记入结果，不追溯修改信号日门禁通过与否。剩余股数、现金和暂停状态仍须满足，失败则不成交。

费用沿用 Stage 1：佣金双边万 2.5、**每边每笔最低 5 元**；卖出印花税 0.05%，买入无印花税；乐观/基准/压力滑点分别为 0、0.01、0.02 元每股每边。每笔费用按人民币分四舍五入，现金和股数非负。门槛单位定为**每股净收益（元/股）**，选择依据为 qi 授权阿七定版；门槛**数值仍为 `null`**，待 Stage 3 预登记网格确定。数值未定时执行器不能放行回补。开发段候选须预登记后比较，并在 Stage 3 样本外使用前锁定，禁止用样本外结果选值。

## 5. 结局、分红与审查

每笔已卖股在报表截点只属于 `REBOUGHT`、`OPEN_UNREBOUGHT`、`INVALIDATION_REVIEW` 之一；部分回补按股数拆分，三类股数合计等于累计卖出股数。未回补和审查中的卖出腿不得算做 T 成功；未实现盯市、卖飞敞口单列。实际收到的公告含税分红现金与**已实现做 T 净额分列**。跨除息日尚未回补的卖出腿附“错过分红”备查项，不混入已实现做 T 净额，也不与复权价差重复计算。分红现金只对除息日前收盘实际持股在除息日交易前入账；不推算个人分红税或税后个人收益。

## 6. 预热和版本门禁

按已选 Q07 公式重算，预热为 **61 个交易日**：A-F1 和 C-W1 各需当日与前一日 MA60，共 61 日；B-V1 与 C-V1 各需当日量及之前 20 日均量，共 21 日；B-V1 另需至少 1 根已确认完整周。第 60 日为 **2023-12-29**，已有完整周确认；最大需求落在第 61 日 **2024-01-02**，故首次完整信号可在该日收盘后评价，下一真实交易日 **2024-01-03** 为理论最早执行日。开发段 485 个交易日中，前 60 日只预热，425 日有理论信号评价容量，424 日有开发段内理论次日执行容量；这些不是交易次数或收益。

重算式为 `max(第 61 个交易日、A-F1/C-W1/B-V1/C-V1 所需最晚日线交易日、B-V1 所需最后完整周确认交易日)`；结果没有延长 61 日基准。第三、四批价带及触发待开发段回测后另行冻结；Q14 门槛数值待 Stage 3 预登记网格；总指挥仍须复审本 R1，`ready_for_stage2b=false`。

## 附录：机器配置逐项快照

以下 JSON 与 `backtest/rules_v1.json` 完全一致，由 `python3 -m backtest.validate_rules_v1` 静态核验。修改机器规则必须同步正文和快照。

<!-- RULES_JSON_BEGIN -->
```json
{
  "version": "jlgs-stage2a-rules-v1.0-r1-final-review",
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
    "fill_model": "daily_open_execution_approximation",
    "fill_model_commander_confirmation": "resolved_R1_daily_open_execution_approximation",
    "order_lifetime": "signal_close_to_next_session_open_only_check_open_band_and_fill_constraints",
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
    "cash_and_shares_nonnegative": true,
    "fill_model_label": "日线开盘成交近似模型",
    "next_open_price_usage": [
      "check_allowed_price_band",
      "calculate_simulated_fill_price"
    ],
    "real_orderbook_fill_verified": false
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
      "flat_ma_definition": {
        "id": "A-F1",
        "formula": "abs(MA20_t/MA20_{t-1} - 1) <= 0.001 AND abs(MA60_t/MA60_{t-1} - 1) <= 0.0005",
        "ma20_max_relative_change": "0.001",
        "ma60_max_relative_change": "0.0005",
        "daily_history_sessions": 61,
        "selected_on": "2026-10-22",
        "selected_by": "qi 授权阿七按推荐定版"
      },
      "action": "observe_mechanical_T_only_after_all_gates_resolved",
      "definition_candidates": "docs/backtest/Q07_CANDIDATE_DEFINITIONS.md"
    },
    "B": {
      "weekly_close_above_raw_rmb": "3.00",
      "ma20_above_ma60": true,
      "volume_comparison_definition": {
        "id": "B-V1",
        "confirmed_week_last_day_formula": "V_wlast / avgV(20,wlast-1) >= 1.30",
        "new_sell_day_formula": "V_t / avgV(20,t-1) >= 1.30",
        "prior_volume_sessions": 20,
        "minimum_ratio": "1.30",
        "weekly_qualification_latched_until_next_completed_week": true,
        "selected_on": "2026-10-22",
        "selected_by": "qi 授权阿七按推荐定版"
      },
      "action": "pause_new_sells",
      "weekly_condition_latch": "confirmed_weekly_condition_until_next_completed_weekly_confirmation",
      "daily_execution_gate": "MA20_above_MA60_and_B_V1_daily_ratio_required_each_proposed_new_sell_day",
      "definition_candidates": "docs/backtest/Q07_CANDIDATE_DEFINITIONS.md"
    },
    "C": {
      "daily_close_below_raw_rmb": "2.64",
      "weak_ma_or_high_volume": true,
      "weak_ma_definition": {
        "id": "C-W1",
        "formula": "MA20_t < MA20_{t-1} AND MA60_t < MA60_{t-1}",
        "daily_history_sessions": 61,
        "selected_on": "2026-10-22",
        "selected_by": "qi 授权阿七按推荐定版"
      },
      "high_volume_definition": {
        "id": "C-V1",
        "formula": "V_t / avgV(20,t-1) >= 1.30",
        "prior_volume_sessions": 20,
        "minimum_ratio": "1.30",
        "independent_of_B": true,
        "selected_on": "2026-10-22",
        "selected_by": "qi 授权阿七按推荐定版"
      },
      "release_definition": "daily_raw_close_above_2_64_and_weak_MA_false_and_high_volume_false",
      "action": "pause_new_sells_cancel_pending_buybacks_and_pause_buybacks_until_release_then_recalculate_without_auto_buy",
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
    "sell_band_interpretation": "first_two_batches_execution_scope_with_distinct_signal_close_trigger_thresholds",
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
    "band_trigger_and_fill_policy": "side_specific_signal_close_trigger_next_open_must_remain_in_allowed_band",
    "pause_B_pending_sell_order_policy": "cancel_all_unfilled_sell_orders_partial_fills_count_actual_quantity",
    "max_unreplenished_fraction_before_sell_stop": "0.5",
    "sell_cap_check_timing": "before_each_new_sell_fill_reject_if_resulting_unreplenished_exceeds_half_initial_mobile_shares",
    "low_buyback_band_when_C_false": "allowed_subject_to_all_other_gates",
    "below_2_64_high_volume_catch_falling_knife": false,
    "only_one_direction_per_day": true,
    "sell_triggers": [
      {
        "batch": 1,
        "min_signal_close_raw_rmb": "2.95",
        "requires_state": "A",
        "requires_prior_batch_filled": false,
        "once_filled_no_retrigger": true
      },
      {
        "batch": 2,
        "min_signal_close_raw_rmb": "3.00",
        "requires_state": "A",
        "requires_prior_batch_filled": true,
        "once_filled_no_retrigger": true
      },
      null,
      null
    ],
    "sell_trigger_priority": "at_most_one_batch_candidate_per_signal_close_first_unfilled_batch_only",
    "unfilled_sell_candidate_policy": "cancel_after_next_open_if_unfilled_then_reevaluate_same_unfilled_batch_on_later_qualifying_close",
    "sell_pause_states": [
      "B",
      "C",
      "D"
    ],
    "pending_sell_cancel_states": [
      "B",
      "C",
      "D"
    ]
  },
  "net_gate": {
    "applies_before_buyback": true,
    "expected_net_rmb_formula": "allocated_realized_sell_cash_minus_expected_buy_gross_and_buy_commission",
    "sell_cash_includes_sell_commission_and_stamp": true,
    "expected_buy_price_includes_scenario_slippage": true,
    "dividends_excluded_from_T_gate": true,
    "minimum_net_threshold": null,
    "expected_buy_quote_estimator": "signal_day_raw_close_when_historical_bid_ask_unavailable",
    "paid_sell_fee_allocation": "pro_rata_by_shares_within_each_realized_sell_fill_no_recalculated_minimum_commission",
    "threshold_unit": "RMB_per_share_approved_by_qi_delegation_to_Aqi",
    "minimum_net_threshold_null_reason": "numeric_value_pending_stage3_preregistered_grid_before_out_of_sample",
    "candidate_generation": "stage3_preregistered_grid_compare_on_development_only_then_lock_before_OOS",
    "out_of_sample_tuning_allowed": false,
    "signal_time_slippage_added_to_buy_estimate": true,
    "signal_gate_uses_next_open_price": false,
    "signal_gate_retroactive_revision_allowed": false
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
    "approval": "selected_Q07_definitions_recalculated_R1",
    "recalculation": "first_signal_date=max(date_of_session_61,date_of_all_Q07_daily_window_completion,date_of_required_completed_weekly_confirmation);first_execution=next_actual_trading_session",
    "base_dates_are_theoretical_until_Q07_selected": false,
    "selected_daily_window_sessions": {
      "A-F1": 61,
      "C-W1": 61,
      "B-V1": 21,
      "C-V1": 21
    },
    "latest_required_completed_week_date": "2023-12-29",
    "recalculation_result": "max(session_61_2024-01-02,selected_daily_windows_at_or_before_session_61,completed_week_2023-12-29)=2024-01-02;next_actual_session=2024-01-03",
    "dates_are_theoretical_signal_and_execution_capacity_not_trades": true
  },
  "blocking_question_ids": [
    "Q01",
    "Q14"
  ],
  "unresolved_fields": {
    "Q01": [
      "orders.sell_bands_raw_rmb_inclusive[2]",
      "orders.sell_bands_raw_rmb_inclusive[3]",
      "orders.sell_triggers[2]",
      "orders.sell_triggers[3]"
    ],
    "Q14": [
      "net_gate.minimum_net_threshold"
    ],
    "ready_for_stage2b": [
      "ready_for_stage2b"
    ]
  },
  "blocking_gate_ids": [
    "ready_for_stage2b"
  ]
}
```
<!-- RULES_JSON_END -->
