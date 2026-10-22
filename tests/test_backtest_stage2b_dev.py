"""Development research boundaries and state/order behavior."""

from dataclasses import replace
from datetime import date
from decimal import Decimal, ROUND_HALF_UP
import json
from pathlib import Path
import tempfile
import unittest

from backtest.calendar import trading_dates
from backtest.config import STAGE1
from backtest.data import Candle
from backtest.development_data import DEV_DIVIDENDS, load_development
from backtest.development_report import render_report
from backtest.development_research import (END, SLIPPAGES, THRESHOLDS, choose,
                                           run, state_points)
from backtest.stage2a_policy import cancel_pending_sells_on_pause


def history(end: date = END, prices=None, opens=None, volumes=None):
    prices, opens, volumes = prices or {}, opens or {}, volumes or {}
    rows = []
    previous = None
    for i, day in enumerate(trading_dates(STAGE1.development_start, end)):
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


class DevelopmentResearchTests(unittest.TestCase):
    def test_loader_stops_before_future_ohlcv_and_rejects_gap(self):
        rows = history()
        raw_rows = [[r.ts_code, r.trade_date.strftime("%Y%m%d"),
                     *[str(getattr(r, field)) for field in
                       ("open", "high", "low", "close", "pre_close", "change", "pct_chg", "vol", "amount")]]
                    for r in rows]
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "raw.json"
            # Deliberately malformed and forbidden tail: a whole-file JSON parser fails.
            path.write_text('{"candles":' + json.dumps(raw_rows)[:-1]
                            + ',["601518.SH","20251009",FORBIDDEN_FUTURE_PRICE', encoding="utf-8")
            selected = load_development(path)
            self.assertEqual(len(selected), 485)
            self.assertEqual(selected[-1].trade_date, END)
            path.write_text('{"candles":' + json.dumps(raw_rows[:80] + raw_rows[81:])[:-1]
                            + ',["601518.SH","20251009",FORBIDDEN_FUTURE_PRICE', encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "gap|incomplete"):
                load_development(path)
        future_date_only = replace(rows[-1], trade_date=date(2025, 10, 9))
        with self.assertRaisesRegex(ValueError, "development-only"):
            state_points((future_date_only,))

    def test_completed_week_latch_requires_daily_b_gate(self):
        rows = history(date(2024, 1, 12), prices={59: "3.10", 60: "2.95", 61: "2.95"},
                       volumes={59: 500, 60: 100, 61: 500})
        states, _ = state_points(rows)
        self.assertEqual(rows[59].trade_date, date(2023, 12, 29))
        self.assertTrue(states[60].week_qualified)
        self.assertFalse(states[60].daily_b_gate)
        self.assertFalse(states[60].active["B"])
        self.assertTrue(states[61].week_qualified)
        self.assertTrue(states[61].daily_b_gate)
        self.assertEqual(states[61].state, "B")
        self.assertEqual(cancel_pending_sells_on_pause([1], states[61].active), [])
        self.assertFalse(states[60].active["D"])
        unfinished = history(date(2024, 1, 12), prices={60: "3.10"}, volumes={60: 500})
        unfinished_states, _ = state_points(unfinished)
        self.assertFalse(unfinished_states[60].week_qualified)
        self.assertFalse(unfinished_states[60].active["B"])

    def test_c_strict_release_and_d_priority(self):
        c_rows = history(date(2024, 1, 9),
                         prices={**{i: "2.50" for i in range(70)},
                                 60: "2.63", 61: "2.64", 62: "2.65"},
                         volumes={60: 500})
        states, _ = state_points(c_rows)
        self.assertEqual([states[i].active["C"] for i in (60, 61, 62)], [True, True, False])
        d_rows = history(date(2024, 1, 12),
                         prices={**{i: "2.50" for i in range(70)}, 59: "2.39", 60: "2.30"},
                         volumes={60: 500})
        d_states, _ = state_points(d_rows)
        self.assertTrue(d_states[60].active["C"])
        self.assertTrue(d_states[60].active["D"])
        self.assertEqual(d_states[60].state, "D")

    def test_synthetic_fifo_fill_gate_and_development_dates(self):
        rows = history(prices={61: "2.80", 62: "2.80"}, opens={61: "2.95", 62: "2.80"})
        result = run(rows, Decimal("0.010"), Decimal("0.01"))
        self.assertEqual([(t["side"], t["day"]) for t in result.trades[:2]],
                         [("sell", date(2024, 1, 3)), ("buy", date(2024, 1, 4))])
        self.assertEqual(result.completed, 1)
        self.assertEqual(result.rebought, 2500)
        self.assertGreater(result.realized, 0)
        self.assertEqual(result.fees, sum((t["fees"] for t in result.trades), Decimal(0)))
        self.assertEqual(len(result.equity), 485)
        self.assertTrue(all(t["day"] <= END for t in result.trades))
        self.assertTrue(all(p.day <= END for p in result.equity + list(result.states)))

    def test_open_band_cancellation_and_zero_volume_denominator(self):
        rows = history(prices={61: "2.80"}, opens={61: "3.01"})
        result = run(rows, Decimal("0.000"), Decimal("0"))
        self.assertEqual(result.cancellations[0]["reason"], "open outside band")
        self.assertEqual(result.cancellations[0]["day"], date(2024, 1, 3))
        self.assertTrue(all(t["day"] > date(2024, 1, 3) for t in result.trades))
        zero_volume = history(date(2024, 1, 12), volumes={i: 0 for i in range(70)})
        states, anomalies = state_points(zero_volume)
        self.assertTrue(any("volume denominator zero" in item for item in anomalies))
        self.assertTrue(all(not point.high_volume for point in states[60:]))

    def test_buy_gate_uses_signal_close_not_next_open(self):
        common = {61: "2.80", 62: "2.80"}
        flat_open = run(history(prices=common, opens={61: "2.95", 62: "2.80"}),
                        Decimal("0.090"), Decimal("0.01"))
        better_open = run(history(prices=common, opens={61: "2.95", 62: "2.76"}),
                          Decimal("0.090"), Decimal("0.01"))
        self.assertEqual(flat_open.completed, better_open.completed)
        self.assertEqual(flat_open.completed, 1)
        self.assertGreater(better_open.realized, flat_open.realized)

    def test_two_batched_sells_reach_but_do_not_exceed_cap(self):
        rows = history(prices={61: "3.00", 62: "3.00"},
                       opens={61: "2.95", 62: "3.00"})
        result = run(rows, Decimal("0.000"), Decimal(0))
        sales = [trade for trade in result.trades if trade["side"] == "sell"]
        self.assertEqual([trade["batch"] for trade in sales], [1, 2])
        self.assertEqual(result.sold, 5000)
        self.assertLessEqual(result.sold - result.rebought, 5000)
        self.assertEqual(sum(leg.remaining for leg in result.legs), result.sold - result.rebought)

    def test_zero_trade_grid_and_registered_choice(self):
        rows = history(prices={i: "2.50" for i in range(485)})
        results = [run(rows, t, s) for t in THRESHOLDS for s in SLIPPAGES]
        self.assertEqual(len(results), 21)
        self.assertTrue(all(r.sold == r.rebought == 0 for r in results))
        self.assertIsNone(choose(results))
        results[0].trades.append({"day": date(2025, 10, 9)})
        with self.assertRaisesRegex(ValueError, "post-boundary"):
            render_report(results, None)
        with self.assertRaisesRegex(ValueError, "unregistered"):
            run(rows, Decimal("0.123"), Decimal(0))


if __name__ == "__main__":
    unittest.main()
