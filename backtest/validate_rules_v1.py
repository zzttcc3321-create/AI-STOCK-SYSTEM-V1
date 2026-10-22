"""Validate Stage 2A R1 text, selected formulas and unresolved gates without candles."""

import argparse
from datetime import date
from decimal import Decimal, InvalidOperation
import json
from pathlib import Path
import re

from .adjustment import VERSION as FACTOR_VERSION
from .config import STAGE1

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CONFIG = ROOT / "backtest" / "rules_v1.json"
DEFAULT_DOC = ROOT / "docs" / "backtest" / "STRATEGY_RULES_V1.0.md"
DEFAULT_QUESTIONS = ROOT / "docs" / "backtest" / "STAGE2A_OPEN_QUESTIONS.md"
DEFAULT_CANDIDATES = ROOT / "docs" / "backtest" / "Q07_CANDIDATE_DEFINITIONS.md"
FACTOR_DATES = ROOT / "docs" / "backtest" / "ADJUSTMENT_FACTORS.json"


def require(ok, message):
    if not ok:
        raise ValueError(message)


def dec(value):
    require(isinstance(value, str), "numeric rule must be a decimal string")
    try:
        result = Decimal(value)
    except InvalidOperation as exc:
        raise ValueError("invalid decimal rule") from exc
    require(result.is_finite(), "non-finite threshold")
    return result


def snapshot(markdown):
    match = re.search(r"<!-- RULES_JSON_BEGIN -->\s*```json\s*([\s\S]*?)\s*```\s*<!-- RULES_JSON_END -->", markdown)
    require(match is not None, "missing JSON snapshot")
    return json.loads(match.group(1))


def validate(config_path=DEFAULT_CONFIG, doc_path=DEFAULT_DOC,
             questions_path=DEFAULT_QUESTIONS, candidates_path=DEFAULT_CANDIDATES):
    rules = json.loads(Path(config_path).read_text(encoding="utf-8"))
    doc = Path(doc_path).read_text(encoding="utf-8")
    questions = Path(questions_path).read_text(encoding="utf-8")
    candidates = Path(candidates_path).read_text(encoding="utf-8")
    require(snapshot(doc) == rules, "document snapshot differs from rules")
    require(rules["version"] == "jlgs-stage2a-rules-v1.0-r1-final-review" and
            rules["freeze_status"] == "PENDING_COMMANDER_FINAL_REVIEW" and
            rules["ready_for_stage2b"] is False, "final review gate drift")
    require("R1 返修送审" in doc and "待总指挥复审" in doc and "ready_for_stage2b=false" in doc,
            "document omits final review gate")

    source = rules["source"]
    require(source["rows"] == 733 and source["first_trade_date"] == STAGE1.development_start.isoformat() and
            source["development_end"] == STAGE1.development_end.isoformat() and
            source["out_of_sample_start"] == STAGE1.out_of_sample_start.isoformat(), "Stage 1 dates drift")
    require(source["development_end"] < source["out_of_sample_first_trade_date"] <= source["last_trade_date"],
            "invalid sample split")
    price = rules["prices_and_dividends"]
    require(price["execution_price"] == price["price_band_basis"] == "raw_unadjusted" and
            price["indicator_price"] == FACTOR_VERSION, "price basis drift")
    require(price["dividend_per_share"] == "announced_gross_cash" and
            price["personal_dividend_tax"] == "not_inferred_without_holding_period", "dividend basis drift")
    execution = rules["execution"]
    require(execution["signal_evaluation"] == "after_completed_daily_close_and_confirmed_weekly_close_only" and
            execution["earliest_fill"] == "next_trading_session_after_signal" and
            execution["fill_model"] == "daily_open_execution_approximation" and
            execution["fill_model_label"] == "日线开盘成交近似模型" and
            execution["fill_model_commander_confirmation"] == "resolved_R1_daily_open_execution_approximation" and
            execution["order_lifetime"] == "signal_close_to_next_session_open_only_check_open_band_and_fill_constraints" and
            execution["next_open_price_usage"] == ["check_allowed_price_band", "calculate_simulated_fill_price"] and
            execution["real_orderbook_fill_verified"] is False,
            "causal fill policy drift")
    require(dec(execution["commission_one_side_rate"]) == STAGE1.commission_rate and
            dec(execution["minimum_commission_rmb_per_fill"]) == STAGE1.minimum_commission and
            dec(execution["sell_stamp_duty_rate"]) == STAGE1.sell_stamp_rate, "fee rate drift")
    require([dec(x) for x in execution["slippage_rmb_per_share_per_side"]] ==
            [STAGE1.optimistic_slippage, STAGE1.baseline_slippage, STAGE1.stress_slippage], "slippage drift")
    require(execution["settlement"] == "T_plus_1_no_same_day_direction_reversal" and
            execution["cash_and_shares_nonnegative"] is True, "settlement drift")

    position = rules["position"]
    require(position["sell_batch_count"] == 4 and dec(position["sell_batch_fraction_of_mobile"]) == Decimal("0.25") and
            dec(position["mobile_fraction"]) == 1 and position["core_trading_allowed"] is False and
            position["net_addition_allowed"] is False and position["buyback_only_previously_sold_mobile_shares"] is True,
            "position limits drift")
    require(position["share_rounding_and_lot_size"] ==
            "private_mobile_shares_divisible_by_four_and_each_batch_valid_A_share_sell_lot_else_reject_before_run",
            "private share preflight drift")
    require(all(isinstance(value, str) and (value.endswith("_external") or "_external_" in value)
                for key, value in position.items() if key.endswith("_parameter")), "share parameters must remain external")

    states = rules["states"]
    a, b, c, d = (states[key] for key in "ABCD")
    low, high = dec(a["box_low_raw_rmb"]), dec(a["box_high_raw_rmb"])
    require((low, high) == (Decimal("2.64"), Decimal("3.00")) and
            dec(b["weekly_close_above_raw_rmb"]) == high and
            dec(c["daily_close_below_raw_rmb"]) == low, "A/B/C thresholds drift")
    require(dec(d["weekly_close_below_review_raw_rmb"]) == Decimal("2.30") and
            dec(d["weekly_close_below_stop_raw_rmb"]) == Decimal("2.40") and
            d["consecutive_weeks_below_review"] == 2 and d["review_logical_operator"] == "OR", "D thresholds drift")
    require(d["stop_transition"] == "first_weekly_cross_from_at_or_above_2_40_to_below_2_40_then_persist_while_below" and
            d["review_trigger"] == "first_weekly_cross_from_at_or_above_2_30_to_below_2_30_OR_two_consecutive_weekly_closes_below_2_30" and
            d["release_definition"] == "confirmed_weekly_raw_close_at_or_above_2_40_and_no_unresolved_invalidation_review_risk_then_reevaluate_without_restoring_orders",
            "D transition drift")
    require(states["priority"] == ["D", "C", "B", "A", "NO_ACTION"] and
            states["pause_actions_combine"] is True and states["unclassified_action"] == "NO_ACTION", "state priority drift")
    require(b["weekly_condition_latch"] == "confirmed_weekly_condition_until_next_completed_weekly_confirmation" and
            b["daily_execution_gate"] == "MA20_above_MA60_and_B_V1_daily_ratio_required_each_proposed_new_sell_day", "B latch drift")
    require(c["release_definition"] == "daily_raw_close_above_2_64_and_weak_MA_false_and_high_volume_false" and
            c["action"] == "pause_new_sells_cancel_pending_buybacks_and_pause_buybacks_until_release_then_recalculate_without_auto_buy",
            "C release drift")
    definitions = ((a, "flat_ma_definition", "A-F1"),
                   (b, "volume_comparison_definition", "B-V1"),
                   (c, "weak_ma_definition", "C-W1"),
                   (c, "high_volume_definition", "C-V1"))
    for group, field, identifier in definitions:
        selected = group[field]
        require(isinstance(selected, dict) and selected.get("id") == identifier and
                selected.get("selected_on") == "2026-10-22" and
                selected.get("selected_by") == "qi 授权阿七按推荐定版" and
                group["definition_candidates"] == "docs/backtest/Q07_CANDIDATE_DEFINITIONS.md",
                f"Q07 selected definition drift: {identifier}")
    flat, bvol, weak, cvol = (group[field] for group, field, _ in definitions)
    require(flat["formula"] == "abs(MA20_t/MA20_{t-1} - 1) <= 0.001 AND abs(MA60_t/MA60_{t-1} - 1) <= 0.0005" and
            dec(flat["ma20_max_relative_change"]) == Decimal("0.001") and
            dec(flat["ma60_max_relative_change"]) == Decimal("0.0005") and
            flat["daily_history_sessions"] == 61, "A-F1 drift")
    require(weak["formula"] == "MA20_t < MA20_{t-1} AND MA60_t < MA60_{t-1}" and
            weak["daily_history_sessions"] == 61, "C-W1 drift")
    require(bvol["confirmed_week_last_day_formula"] == "V_wlast / avgV(20,wlast-1) >= 1.30" and
            bvol["new_sell_day_formula"] == "V_t / avgV(20,t-1) >= 1.30" and
            bvol["prior_volume_sessions"] == 20 and dec(bvol["minimum_ratio"]) == Decimal("1.30") and
            bvol["weekly_qualification_latched_until_next_completed_week"] is True, "B-V1 drift")
    require(cvol["formula"] == "V_t / avgV(20,t-1) >= 1.30" and
            cvol["prior_volume_sessions"] == 20 and dec(cvol["minimum_ratio"]) == Decimal("1.30") and
            cvol["independent_of_B"] is True, "C-V1 drift")
    for token in ("A-F1", "A-F2", "A-F3", "C-W1", "C-W2", "C-W3",
                  "B-V1", "B-V2", "B-V3", "C-V1", "C-V2", "C-V3",
                  "V_wlast / avgV(20,wlast-1) >= 1.30",
                  "<= 0.001", "<= 0.0005", "<= 0.0025", "<= 0.005 * MA20_t", "样本外"):
        require(token in candidates, f"missing Q07 candidate or guard: {token}")
    require("已选定" in candidates and "未选，归档" in candidates and
            "qi 授权阿七按推荐定版" in doc, "Q07 decision record missing")

    orders = rules["orders"]
    sell, buy = orders["sell_bands_raw_rmb_inclusive"], orders["buyback_bands_raw_rmb_inclusive"]
    require(sell == [["2.64", "3.00"], ["2.64", "3.00"], None, None] and
            orders["sell_band_original_source_only"] == [["2.98", "3.03"], ["3.08", "3.12"]] and
            orders["sell_bands_three_four_null_reason"] == "freeze_after_development_backtest", "sell bands drift")
    triggers = orders["sell_triggers"]
    require(len(triggers) == 4 and triggers[2:] == [None, None] and
            [dec(x["min_signal_close_raw_rmb"]) for x in triggers[:2]] ==
            [Decimal("2.95"), Decimal("3.00")] and
            [x["requires_prior_batch_filled"] for x in triggers[:2]] == [False, True] and
            all(x["requires_state"] == "A" and x["once_filled_no_retrigger"] is True
                for x in triggers[:2]) and
            orders["sell_band_interpretation"] ==
            "first_two_batches_execution_scope_with_distinct_signal_close_trigger_thresholds" and
            orders["sell_trigger_priority"] ==
            "at_most_one_batch_candidate_per_signal_close_first_unfilled_batch_only" and
            orders["unfilled_sell_candidate_policy"] ==
            "cancel_after_next_open_if_unfilled_then_reevaluate_same_unfilled_batch_on_later_qualifying_close" and
            orders["sell_pause_states"] == ["B", "C", "D"] and
            orders["pending_sell_cancel_states"] == ["B", "C", "D"],
            "sell triggers drift")
    require(buy == [["2.76", "2.82"], ["2.62", "2.68"]] and
            dec(buy[1][0]) < low < dec(buy[1][1]), "buyback bands drift")
    require(orders["buyback_band_to_sold_batch_mapping"] == "FIFO_per_share_sold_to_rebought_ledger" and
            orders["band_trigger_and_fill_policy"] == "side_specific_signal_close_trigger_next_open_must_remain_in_allowed_band" and
            orders["pause_B_pending_sell_order_policy"] == "cancel_all_unfilled_sell_orders_partial_fills_count_actual_quantity",
            "order lifecycle drift")
    require(dec(orders["max_unreplenished_fraction_before_sell_stop"]) == Decimal("0.5") and
            orders["sell_cap_check_timing"] == "before_each_new_sell_fill_reject_if_resulting_unreplenished_exceeds_half_initial_mobile_shares" and
            orders["low_buyback_band_when_C_false"] == "allowed_subject_to_all_other_gates" and
            orders["below_2_64_high_volume_catch_falling_knife"] is False, "risk gate drift")

    gate = rules["net_gate"]
    require(gate["applies_before_buyback"] is True and gate["minimum_net_threshold"] is None and
            gate["minimum_net_threshold_null_reason"] == "numeric_value_pending_stage3_preregistered_grid_before_out_of_sample" and
            gate["threshold_unit"] == "RMB_per_share_approved_by_qi_delegation_to_Aqi" and
            gate["expected_buy_quote_estimator"] == "signal_day_raw_close_when_historical_bid_ask_unavailable" and
            gate["signal_time_slippage_added_to_buy_estimate"] is True and
            gate["signal_gate_uses_next_open_price"] is False and
            gate["signal_gate_retroactive_revision_allowed"] is False and
            gate["paid_sell_fee_allocation"] == "pro_rata_by_shares_within_each_realized_sell_fill_no_recalculated_minimum_commission" and
            gate["candidate_generation"] == "stage3_preregistered_grid_compare_on_development_only_then_lock_before_OOS" and
            gate["out_of_sample_tuning_allowed"] is False, "net gate drift")
    outcome = rules["outcomes"]
    require(outcome["categories"] == ["REBOUGHT", "OPEN_UNREBOUGHT", "INVALIDATION_REVIEW"] and
            outcome["open_unreplenished_success"] is False and
            outcome["realized_T_net_and_received_dividend_cash_separate"] is True and
            outcome["open_sell_leg_missed_dividend_memo"] is True and
            outcome["dividend_adjustment_double_count_allowed"] is False, "outcome or dividend drift")

    warmup = rules["warmup_proposal"]
    require(warmup["daily_ma20_sessions"] == 20 and warmup["daily_ma60_sessions"] == 60 and
            warmup["prior_ma_for_slope_sessions"] == 61 and warmup["volume_ratio_first_session"] == 21 and
            warmup["completed_weekly_bars"] == 1 and warmup["earliest_full_signal_session_index"] == 61 and
            warmup["approval"] == "selected_Q07_definitions_recalculated_R1" and
            warmup["selected_daily_window_sessions"] ==
            {"A-F1": 61, "C-W1": 61, "B-V1": 21, "C-V1": 21} and
            warmup["base_dates_are_theoretical_until_Q07_selected"] is False and
            warmup["dates_are_theoretical_signal_and_execution_capacity_not_trades"] is True,
            "warmup drift")
    require("date_of_session_61" in warmup["recalculation"] and
            "date_of_all_Q07_daily_window_completion" in warmup["recalculation"] and
            "date_of_required_completed_weekly_confirmation" in warmup["recalculation"], "warmup recalculation missing")
    # Stage 1's public factor manifest supplies dates only, never candles or returns.
    manifest = json.loads(FACTOR_DATES.read_text(encoding="utf-8"))
    require(manifest["version"] == FACTOR_VERSION, "factor manifest version drift")
    dates = [date.fromisoformat(row["trade_date"]) for row in manifest["rows"]]
    require(len(dates) == source["rows"] and dates == sorted(set(dates)), "factor date coverage drift")
    development = [day for day in dates if day <= date.fromisoformat(source["development_end"])]
    i = warmup["earliest_full_signal_session_index"] - 1
    selected_window = max(warmup["selected_daily_window_sessions"].values())
    completed_weeks = [development[j] for j in range(i)
                       if development[j].isocalendar()[:2] != development[j + 1].isocalendar()[:2]]
    require(selected_window == 61 and completed_weeks and
            completed_weeks[-1].isoformat() == warmup["latest_required_completed_week_date"] == "2023-12-29" and
            warmup["recalculation_result"] ==
            "max(session_61_2024-01-02,selected_daily_windows_at_or_before_session_61,completed_week_2023-12-29)=2024-01-02;next_actual_session=2024-01-03",
            "selected Q07 warmup recalculation drift")
    require(len(development) == warmup["development_sessions"] == 485 and
            development[i].isoformat() == warmup["earliest_full_signal_date"] == "2024-01-02" and
            development[i+1].isoformat() == warmup["earliest_execution_date"] == "2024-01-03" and
            len(development)-i == warmup["full_signal_sessions_in_development"] == 425 and
            len(development)-i-1 == warmup["earliest_execution_through_dev_sessions"] == 424,
            "theoretical warmup dates or capacity drift")

    require(rules["blocking_question_ids"] == ["Q01", "Q14"] and
            rules["blocking_gate_ids"] == ["ready_for_stage2b"], "unresolved ID drift")
    require(rules["unresolved_fields"] == {
        "Q01": ["orders.sell_bands_raw_rmb_inclusive[2]", "orders.sell_bands_raw_rmb_inclusive[3]",
                "orders.sell_triggers[2]", "orders.sell_triggers[3]"],
        "Q14": ["net_gate.minimum_net_threshold"],
        "ready_for_stage2b": ["ready_for_stage2b"]}, "unresolved field map drift")
    require(re.findall(r"^## (Q\d\d)\b", questions, re.MULTILINE) ==
            [f"Q{i:02d}" for i in range(1, 15)], "question status headings drift")
    for i in range(1, 15):
        section = questions.split(f"## Q{i:02d} ", 1)[1].split("\n## ", 1)[0]
        require("**状态：" in section, f"Q{i:02d} status missing")
    require("剩余未决仅为 Q01" in questions and "Q14 门槛数值" in questions and
            "ready_for_stage2b=false" in questions and "日线开盘成交近似模型" in questions and
            "**状态：已解决。**" in questions.split("## Q09 ", 1)[1].split("\n## ", 1)[0],
            "R1 question status drift")
    return rules


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    parser.add_argument("--doc", type=Path, default=DEFAULT_DOC)
    parser.add_argument("--questions", type=Path, default=DEFAULT_QUESTIONS)
    parser.add_argument("--candidates", type=Path, default=DEFAULT_CANDIDATES)
    args = parser.parse_args()
    rules = validate(args.config, args.doc, args.questions, args.candidates)
    print(f"RULES_STATIC_CHECK=PASS version={rules['version']} "
          f"blockers={','.join(rules['blocking_question_ids'] + rules['blocking_gate_ids'])} stage2b=false")


if __name__ == "__main__":
    main()
