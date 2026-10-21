"""Static Stage 2A rule consistency check; never reads candles or simulates trades."""

import argparse
from datetime import date
from decimal import Decimal
import json
from pathlib import Path
import re

from .adjustment import VERSION as FACTOR_VERSION
from .config import STAGE1


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CONFIG = ROOT / "backtest" / "rules_v1.json"
DEFAULT_DOC = ROOT / "docs" / "backtest" / "STRATEGY_RULES_V1.0.md"
DEFAULT_QUESTIONS = ROOT / "docs" / "backtest" / "STAGE2A_OPEN_QUESTIONS.md"
FACTOR_DATES = ROOT / "docs" / "backtest" / "ADJUSTMENT_FACTORS.json"


def _require(ok: bool, message: str) -> None:
    if not ok:
        raise ValueError(message)


def _decimal(value: str) -> Decimal:
    _require(isinstance(value, str), "numeric rule must be a decimal string")
    number = Decimal(value)
    _require(number.is_finite(), "non-finite threshold")
    return number


def _snapshot(markdown: str) -> dict:
    match = re.search(r"<!-- RULES_JSON_BEGIN -->\s*```json\s*([\s\S]*?)\s*```\s*<!-- RULES_JSON_END -->", markdown)
    _require(match is not None, "missing complete JSON snapshot in rule document")
    return json.loads(match.group(1))


def validate(config_path: Path = DEFAULT_CONFIG, doc_path: Path = DEFAULT_DOC,
             questions_path: Path = DEFAULT_QUESTIONS) -> dict:
    """Return the checked configuration, or reject inconsistent or executable drafts."""
    rules = json.loads(config_path.read_text(encoding="utf-8"))
    doc = doc_path.read_text(encoding="utf-8")
    questions = questions_path.read_text(encoding="utf-8")
    _require(_snapshot(doc) == rules, "document JSON snapshot differs from machine rules")
    _require(rules["freeze_status"] == "PENDING_COMMANDER_AND_QI", "unexpected freeze status")
    _require(rules["ready_for_stage2b"] is False, "Stage 2B must remain blocked")
    _require("待总指挥与 qi 确认" in doc and "ready_for_stage2b=false" in doc,
             "document must disclose the pending gate")

    source = rules["source"]
    _require(source["rows"] == 733, "source row count drift")
    _require(source["first_trade_date"] == source["development_start"] == STAGE1.development_start.isoformat(),
             "development start differs from Stage 1")
    _require(source["development_end"] == STAGE1.development_end.isoformat(), "development end differs from Stage 1")
    _require(source["out_of_sample_start"] == STAGE1.out_of_sample_start.isoformat(), "OOS start differs from Stage 1")
    _require(source["development_end"] < source["out_of_sample_first_trade_date"] <= source["last_trade_date"],
             "invalid real-session split")

    price = rules["prices_and_dividends"]
    _require(price["execution_price"] == price["price_band_basis"] == "raw_unadjusted", "adjusted execution forbidden")
    _require(price["indicator_price"] == FACTOR_VERSION, "factor version differs from Stage 1")
    _require(price["dividend_per_share"] == "announced_gross_cash", "dividend source drift")
    _require(price["personal_dividend_tax"] == "not_inferred_without_holding_period", "private tax inference forbidden")

    execution = rules["execution"]
    _require(_decimal(execution["commission_one_side_rate"]) == STAGE1.commission_rate, "commission drift")
    _require(_decimal(execution["minimum_commission_rmb_per_fill"]) == STAGE1.minimum_commission, "minimum commission drift")
    _require(_decimal(execution["sell_stamp_duty_rate"]) == STAGE1.sell_stamp_rate, "stamp duty drift")
    _require([_decimal(v) for v in execution["slippage_rmb_per_share_per_side"]] ==
             [STAGE1.optimistic_slippage, STAGE1.baseline_slippage, STAGE1.stress_slippage], "slippage drift")
    _require(execution["cash_and_shares_nonnegative"] is True and
             execution["settlement"] == "T_plus_1_no_same_day_direction_reversal", "settlement drift")

    position = rules["position"]
    _require(_decimal(position["mobile_fraction"]) == 1 and position["sell_batch_count"] == 4 and
             _decimal(position["sell_batch_fraction_of_mobile"]) * position["sell_batch_count"] == 1,
             "batch fractions do not cover the mobile position")
    _require(position["core_trading_allowed"] is False and position["net_addition_allowed"] is False and
             position["buyback_only_previously_sold_mobile_shares"] is True, "position restrictions drift")
    _require(all(value.endswith("_external") or "_external_" in value for key, value in position.items()
                 if key.endswith("_parameter")), "share counts must be external parameters")
    _require(position["share_rounding_and_lot_size"] is None, "unapproved lot rule")

    states = rules["states"]
    a, b, c, d = (states[key] for key in "ABCD")
    low, high = _decimal(a["box_low_raw_rmb"]), _decimal(a["box_high_raw_rmb"])
    _require(low < high and _decimal(b["weekly_close_above_raw_rmb"]) == high and
             _decimal(c["daily_close_below_raw_rmb"]) == low, "A/B/C price boundaries conflict")
    _require(_decimal(d["weekly_close_below_review_raw_rmb"]) <
             _decimal(d["weekly_close_below_stop_raw_rmb"]) < low, "D thresholds conflict")
    _require(d["consecutive_weeks_below_review"] == 2 and d["review_logical_operator"] == "OR",
             "review clause changed")
    priority = states["priority_candidate"]
    _require(priority == ["D", "C", "B", "A", "NO_ACTION"] and len(set(priority)) == len(priority),
             "priority ambiguous or risk order changed")
    _require(states["priority_approval"] is None and states["unclassified_action"] == "NO_ACTION",
             "unapproved priority or unclassified trading")
    for hits in (("A",), ("B", "A"), ("C", "B", "A"), ("D", "C", "B", "A"), ()):
        winner = next((state for state in priority if state in hits), "NO_ACTION")
        _require(winner in priority, "uncovered state combination")

    orders = rules["orders"]
    sell, buy = orders["sell_bands_raw_rmb_inclusive"], orders["buyback_bands_raw_rmb_inclusive"]
    _require(sell == [["2.98", "3.03"], ["3.08", "3.12"], None, None], "unapproved sell band")
    _require(buy == [["2.76", "2.82"], ["2.62", "2.68"]], "buyback band drift")
    for band in [item for item in sell + buy if item is not None]:
        _require(_decimal(band[0]) <= _decimal(band[1]), "inverted price band")
    _require(_decimal(sell[0][0]) <= high < _decimal(sell[0][1]) < _decimal(sell[1][0]),
             "sell bands no longer expose the documented A/B transition")
    _require(_decimal(buy[1][0]) < low < _decimal(buy[1][1]),
             "second buyback band no longer crosses C boundary")
    _require(_decimal(orders["max_unreplenished_fraction_before_sell_stop"]) == Decimal("0.5"), "sell cap drift")
    _require(orders["below_2_64_high_volume_catch_falling_knife"] is False, "C risk block drift")
    for key in ("buyback_band_to_sold_batch_mapping", "band_trigger_and_fill_policy",
                "pause_B_pending_sell_order_policy", "sell_cap_check_timing"):
        _require(orders[key] is None, f"unapproved order choice: {key}")

    gate = rules["net_gate"]
    _require(gate["applies_before_buyback"] is True and gate["minimum_net_threshold"] is None and
             gate["expected_buy_quote_estimator"] is None and gate["threshold_unit"] is None and
             gate["out_of_sample_tuning_allowed"] is False, "unapproved net gate")
    _require(rules["outcomes"]["open_unreplenished_success"] is False, "sold-away shares counted as success")
    _require(rules["outcomes"]["categories"] == ["REBOUGHT", "OPEN_UNREBOUGHT", "INVALIDATION_REVIEW"],
             "outcome partition drift")

    warmup = rules["warmup_proposal"]
    _require(warmup["daily_ma20_sessions"] == 20 and warmup["daily_ma60_sessions"] == 60 and
             warmup["prior_ma_for_slope_sessions"] == 61 and warmup["volume_mean_prior_sessions"] == 20 and
             warmup["volume_ratio_first_session"] == 21 and warmup["completed_weekly_bars"] == 1,
             "warmup indicator requirements drift")
    _require(warmup["earliest_full_signal_session_index"] == 61 and warmup["development_sessions"] == 485 and
             warmup["full_signal_sessions_in_development"] == 425 and
             warmup["earliest_execution_through_dev_sessions"] == 424 and warmup["approval"] is None,
             "warmup proposal inconsistent")
    # Read dates only from Stage 1's public factor manifest. No candles, signal
    # generation, simulated orders, returns, or parameter search occur here.
    factor_manifest = json.loads(FACTOR_DATES.read_text(encoding="utf-8"))
    _require(factor_manifest["version"] == FACTOR_VERSION, "factor manifest version drift")
    trade_dates = [date.fromisoformat(row["trade_date"]) for row in factor_manifest["rows"]]
    _require(len(trade_dates) == source["rows"] and trade_dates == sorted(set(trade_dates)),
             "factor date coverage inconsistent")
    development = [day for day in trade_dates if day <= date.fromisoformat(source["development_end"])]
    _require(len(development) == warmup["development_sessions"], "development calendar length drift")
    first_signal_index = warmup["earliest_full_signal_session_index"] - 1
    _require(development[first_signal_index].isoformat() == warmup["earliest_full_signal_date"] and
             development[first_signal_index + 1].isoformat() == warmup["earliest_execution_date"],
             "warmup first dates drift")
    _require(len(development) - first_signal_index == warmup["full_signal_sessions_in_development"] and
             len(development) - first_signal_index - 1 == warmup["earliest_execution_through_dev_sessions"],
             "warmup capacity drift")

    ids = rules["blocking_question_ids"]
    _require(ids == [f"Q{i:02d}" for i in range(1, 15)], "missing or unordered blocking question")
    found = re.findall(r"^## (Q\d\d)\b", questions, re.MULTILINE)
    _require(found == ids, "open question document differs from machine blocker list")
    for question_id in ids:
        section = questions.split(f"## {question_id} ", 1)[1].split("\n## ", 1)[0]
        _require(all(label in section for label in ("**问题/", "**候选：**", "**影响：**", "**拍板：**")),
                 f"incomplete open question {question_id}")
    return rules


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    parser.add_argument("--doc", type=Path, default=DEFAULT_DOC)
    parser.add_argument("--questions", type=Path, default=DEFAULT_QUESTIONS)
    args = parser.parse_args()
    rules = validate(args.config, args.doc, args.questions)
    print(f"RULES_STATIC_CHECK=PASS version={rules['version']} blockers={len(rules['blocking_question_ids'])} stage2b=false")


if __name__ == "__main__":
    main()
