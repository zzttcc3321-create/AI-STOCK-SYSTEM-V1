"""Validate the Stage 2A frozen text and unresolved gates without reading candles."""

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
    require(rules["version"] == "jlgs-stage2a-rules-v1.0-final-review" and
            rules["freeze_status"] == "PENDING_COMMANDER_FINAL_REVIEW" and
            rules["ready_for_stage2b"] is False, "final review gate drift")
    require("送审定版" in doc and "待总指挥终核" in doc and "ready_for_stage2b=false" in doc,
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
            execution["fill_model"] == "daily_OHLC_signal_close_next_session_open_no_intraday_path_assumption_pending_commander_review" and
            execution["fill_model_commander_confirmation"] is None and
            execution["fill_model_commander_confirmation_null_reason"] == "pending_commander_final_review" and
            execution["order_lifetime"] == "signal_close_to_next_session_open_only_recheck_all_gates_before_fill",
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
            b["daily_execution_gate"] == "MA20_above_MA60_and_selected_B_volume_definition_required_each_sell_day", "B latch drift")
    require(c["release_definition"] == "daily_raw_close_above_2_64_and_weak_MA_false_and_high_volume_false" and
            c["action"] == "cancel_pending_buybacks_and_pause_buybacks_until_release_then_recalculate_without_auto_buy",
            "C release drift")
    for group, field in ((a,"flat_ma_definition"),(b,"volume_comparison_definition"),
                         (c,"weak_ma_definition"),(c,"high_volume_definition")):
        require(group[field] is None and group["definition_candidates"] ==
                "docs/backtest/Q07_CANDIDATE_DEFINITIONS.md", "Q07 must remain null with candidate pointer")
    for token in ("A-F1", "A-F2", "A-F3", "C-W1", "C-W2", "C-W3",
                  "B-V1", "B-V2", "B-V3", "C-V1", "C-V2", "C-V3",
                  "V_wlast / avgV(20,wlast-1) >= 1.30",
                  "<= 0.001", "<= 0.0005", "<= 0.0025", "<= 0.005 * MA20_t", "样本外"):
        require(token in candidates, f"missing Q07 candidate or guard: {token}")

    orders = rules["orders"]
    sell, buy = orders["sell_bands_raw_rmb_inclusive"], orders["buyback_bands_raw_rmb_inclusive"]
    require(sell == [["2.64", "3.00"], ["2.64", "3.00"], None, None] and
            orders["sell_band_original_source_only"] == [["2.98", "3.03"], ["3.08", "3.12"]] and
            orders["sell_bands_three_four_null_reason"] == "freeze_after_development_backtest", "sell bands drift")
    require(buy == [["2.76", "2.82"], ["2.62", "2.68"]] and
            dec(buy[1][0]) < low < dec(buy[1][1]), "buyback bands drift")
    require(orders["buyback_band_to_sold_batch_mapping"] == "FIFO_per_share_sold_to_rebought_ledger" and
            orders["band_trigger_and_fill_policy"] == "close_in_band_signal_next_session_open_if_open_still_in_band_and_all_gates_pass" and
            orders["pause_B_pending_sell_order_policy"] == "cancel_all_unfilled_sell_orders_partial_fills_count_actual_quantity",
            "order lifecycle drift")
    require(dec(orders["max_unreplenished_fraction_before_sell_stop"]) == Decimal("0.5") and
            orders["sell_cap_check_timing"] == "before_each_new_sell_fill_reject_if_resulting_unreplenished_exceeds_half_initial_mobile_shares" and
            orders["low_buyback_band_when_C_false"] == "allowed_subject_to_all_other_gates" and
            orders["below_2_64_high_volume_catch_falling_knife"] is False, "risk gate drift")

    gate = rules["net_gate"]
    require(gate["applies_before_buyback"] is True and gate["minimum_net_threshold"] is None and
            gate["minimum_net_threshold_null_reason"] == "numeric_value_to_be_selected_in_stage2b_development_and_locked_before_stage3_OOS" and
            gate["threshold_unit"] == "RMB_per_share_approved_by_qi_delegation_to_Aqi_pending_commander_final_review" and
            gate["expected_buy_quote_estimator"] == "visible_quote_at_order_submission_no_ex_post_daily_low" and
            gate["paid_sell_fee_allocation"] == "pro_rata_by_shares_within_each_realized_sell_fill_no_recalculated_minimum_commission" and
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
            warmup["approval"] == "approved_base_61_sessions_recalculate_if_Q07_requires_longer_history" and
            warmup["base_dates_are_theoretical_until_Q07_selected"] is True, "warmup drift")
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
    require(len(development) == warmup["development_sessions"] == 485 and
            development[i].isoformat() == warmup["earliest_full_signal_date"] == "2024-01-02" and
            development[i+1].isoformat() == warmup["earliest_execution_date"] == "2024-01-03" and
            len(development)-i == warmup["full_signal_sessions_in_development"] == 425 and
            len(development)-i-1 == warmup["earliest_execution_through_dev_sessions"] == 424,
            "theoretical warmup dates or capacity drift")

    require(rules["blocking_question_ids"] == ["Q01", "Q07", "Q09", "Q14"], "unresolved ID drift")
    require(rules["unresolved_fields"] == {
        "Q01": ["orders.sell_bands_raw_rmb_inclusive[2]", "orders.sell_bands_raw_rmb_inclusive[3]"],
        "Q07": ["states.A.flat_ma_definition", "states.B.volume_comparison_definition", "states.C.weak_ma_definition", "states.C.high_volume_definition"],
        "Q14": ["net_gate.minimum_net_threshold"],
        "Q09": ["execution.fill_model_commander_confirmation"]}, "unresolved field map drift")
    require(re.findall(r"^## (Q\d\d)\b", questions, re.MULTILINE) ==
            [f"Q{i:02d}" for i in range(1, 15)], "question status headings drift")
    for i in range(1, 15):
        section = questions.split(f"## Q{i:02d} ", 1)[1].split("\n## ", 1)[0]
        require("**状态：" in section, f"Q{i:02d} status missing")
    return rules


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    parser.add_argument("--doc", type=Path, default=DEFAULT_DOC)
    parser.add_argument("--questions", type=Path, default=DEFAULT_QUESTIONS)
    parser.add_argument("--candidates", type=Path, default=DEFAULT_CANDIDATES)
    args = parser.parse_args()
    rules = validate(args.config, args.doc, args.questions, args.candidates)
    print(f"RULES_STATIC_CHECK=PASS version={rules['version']} blockers={len(rules['blocking_question_ids'])} stage2b=false")


if __name__ == "__main__":
    main()
