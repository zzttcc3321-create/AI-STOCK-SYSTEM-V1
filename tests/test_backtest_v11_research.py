"""V1.1 synthetic execution, FIFO, cash and isolation evidence."""

from datetime import date, datetime, time
from decimal import Decimal, ROUND_HALF_UP
import hashlib
from pathlib import Path
import unittest

from backtest.calendar import trading_dates
from backtest.adjustment import adjusted_bars, build_factors
from backtest.config import STAGE1
from backtest.data import Candle
from backtest.development_data import DEV_DIVIDENDS, load_development
from backtest.v11_development_research import END, run_v11
from backtest.validate_rules_v1_1 import INPUT_HASH, ROOT, validate
from backtest.weekly import SHANGHAI, confirmed_weekly_bars


def history(prices=None, opens=None, volumes=None):
    prices, opens, volumes = prices or {}, opens or {}, volumes or {}
    rows = []
    previous = None
    for i, day in enumerate(trading_dates(STAGE1.development_start, END)):
        close = Decimal(str(prices.get(i, "2.95")))
        opening = Decimal(str(opens.get(i, close)))
        preclose = previous if previous is not None else close
        if day in DEV_DIVIDENDS:
            preclose = (preclose - DEV_DIVIDENDS[day]).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
        rows.append(Candle("601518.SH", day, opening, max(opening, close),
                           min(opening, close), close, preclose, Decimal(0), Decimal(0),
                           Decimal(str(volumes.get(i, 100))), Decimal(0)))
        previous = close
    return tuple(rows)


class V11ResearchTests(unittest.TestCase):
    def test_isolated_official_input_hash_and_v1_blobs(self):
        source = ROOT / "backtest/local_data/jlgs_dev_only.json"
        self.assertEqual(hashlib.sha256(source.read_bytes()).hexdigest(), INPUT_HASH)
        rows = load_development(source)
        self.assertEqual((len(rows), rows[0].trade_date, rows[-1].trade_date),
                         (485, date(2023, 10, 9), END))
        self.assertTrue(all(row.trade_date <= END for row in rows))
        rules, blobs = validate()
        self.assertFalse(rules["ready_for_stage2b"])
        self.assertEqual(len(blobs), 3)

    def test_completed_week_is_unavailable_at_1500_and_available_after(self):
        rows = tuple(row for row in history() if row.trade_date <= date(2024, 1, 5))
        bars = adjusted_bars(rows, build_factors(rows, {}))
        at_close = confirmed_weekly_bars(bars, datetime.combine(date(2024, 1, 5), time(15, 0), SHANGHAI))
        after_close = confirmed_weekly_bars(bars, datetime.combine(date(2024, 1, 5), time(15, 1), SHANGHAI))
        self.assertEqual(len(after_close), len(at_close) + 1)
        self.assertEqual(after_close[-1].confirmed_at.date(), date(2024, 1, 5))

    def test_regular_round_fee_allocation_and_no_same_day_reversal(self):
        rows = history(prices={61: "2.80", 62: "2.80"}, opens={61: "2.95", 62: "2.80"})
        result = run_v11(rows)
        self.assertEqual(result.sold, 2500)
        self.assertEqual(result.regular_shares, 2500)
        self.assertEqual(result.risk_shares, 0)
        self.assertEqual(result.regular_completed_legs, 1)
        self.assertGreater(result.regular_net, 0)
        self.assertEqual(result.sold, result.regular_shares + result.risk_shares + result.open_shares)
        self.assertTrue(all(result.trades[i]["day"] != result.trades[i+1]["day"]
                            for i in range(len(result.trades)-1)))
        self.assertEqual(result.funnel["orders"], 1)

    def test_next_open_band_cancel_and_reissue(self):
        rows = history(opens={61: "3.04", 62: "2.95"})
        result = run_v11(rows)
        self.assertEqual(result.funnel["orders"], 2)
        self.assertEqual(result.sold, 2500)
        self.assertEqual(result.legs[0].sold_day, rows[62].trade_date)
        self.assertTrue(any(e["kind"] == "CANCEL_SELL" and e["day"] == rows[61].trade_date
                            and e["reason"] == "open outside sell band" for e in result.events))

    def test_three_slippages_reprice_both_sides_and_future_close_does_not_change_past(self):
        prices = {61: "2.80", 62: "2.80"}
        opens = {61: "2.95", 62: "2.80"}
        rows = history(prices=prices, opens=opens)
        outcomes = [run_v11(rows, slippage=Decimal(s)) for s in ("0", "0.01", "0.02")]
        self.assertTrue(all(r.regular_completed_legs == 1 for r in outcomes))
        self.assertGreater(outcomes[0].realized, outcomes[1].realized)
        self.assertGreater(outcomes[1].realized, outcomes[2].realized)
        changed = history(prices={**prices, 63: "3.40"}, opens=opens)
        rerun = run_v11(changed)
        self.assertEqual(outcomes[1].trades[:2], rerun.trades[:2])
        self.assertEqual(outcomes[1].funnel["orders"], rerun.funnel["orders"])

    def test_risk_gap_partial_cash_and_persistent_pending(self):
        rows = history(prices={61: "3.10", 62: "3.10", 63: "3.10"},
                       opens={61: "2.95", 62: "3.20", 63: "3.20"}, volumes={61: 200})
        result = run_v11(rows)
        self.assertEqual(result.sold, 2500)
        self.assertGreater(result.risk_shares, 0)
        self.assertLess(result.risk_net, 0)
        self.assertEqual(result.marks[62].open_shares, 300)
        self.assertEqual(result.marks[63].open_shares, 300)
        self.assertEqual(result.open_shares, 0)  # Later dividend cash finally funds the remainder.
        self.assertTrue(any(e["kind"] == "RISK_PARTIAL" for e in result.events))
        self.assertTrue(any(e["kind"] == "RISK_FAILURE" and "legal affordable" in e["reason"]
                            for e in result.events))
        self.assertTrue(all(x.open_shares <= 5000 for x in result.marks))

    def test_wait_latch_survives_c_pause(self):
        prices = {i: "2.50" for i in range(61, 70)}
        prices[61] = "2.50"
        rows = history(prices=prices, opens={61: "2.95"}, volumes={61: 200})
        result = run_v11(rows)
        self.assertEqual(result.sold, 2500)
        self.assertTrue(any(e["kind"] == "RISK_LATCH" for e in result.events))
        self.assertTrue(any(e["kind"] == "RISK_FAILURE" and "C/D blocked" in e["reason"]
                            for e in result.events))

    def test_d0_zero_trade_on_official_data_and_forbidden_combinations(self):
        rows = load_development(ROOT / "backtest/local_data/jlgs_dev_only.json")
        result = run_v11(rows, slippage=Decimal("0.01"))
        self.assertEqual((result.sold, result.open_shares, result.realized),
                         (0, 0, Decimal(0)))
        self.assertEqual(result.funnel["candidate_conditions"], 2)
        self.assertEqual(result.funnel["state_allowed"], 0)
        with self.assertRaisesRegex(ValueError, "multi-factor"):
            run_v11(rows, breakout=Decimal("2.95"), wait_sessions=10)
        with self.assertRaisesRegex(ValueError, "D1 combination"):
            run_v11(rows, d_state="D1-N1", threshold=Decimal("0.020"))


if __name__ == "__main__":
    unittest.main()
