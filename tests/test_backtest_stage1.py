"""Stage 1 contract tests; use synthetic bars and never load a private source."""

from datetime import date, datetime, timedelta
from decimal import Decimal
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from zoneinfo import ZoneInfo

from backtest.adjustment import AdjustedBar, build_factors
from backtest.benchmark import buy_hold
from backtest.calendar import trading_dates
from backtest.data import Candle, load_daily, verify_daily
from backtest.fees import fill, round_trip
from backtest.ledger import Portfolio
from backtest.weekly import confirmed_weekly_bars


def candle(day: date, close: str, pre_close: str | None = None,
           high: str | None = None, low: str | None = None) -> Candle:
    value = Decimal(close)
    return Candle("601518.SH", day, value, Decimal(high or close), Decimal(low or close),
                  value, Decimal(pre_close or close), Decimal(0), Decimal(0), Decimal(1), Decimal(1))


class FeeTests(unittest.TestCase):
    def test_verified_round_trip(self):
        sale, buy, net = round_trip(Decimal("3.00"), Decimal("2.80"), 2000)
        self.assertEqual((sale.commission, buy.commission, sale.stamp_duty),
                         (Decimal(5), Decimal(5), Decimal("3.00")))
        self.assertEqual(sale.fees + buy.fees, Decimal("13.00"))
        self.assertEqual(net, Decimal("387.00"))

    def test_minimum_commission_boundary(self):
        self.assertEqual(fill("buy", 2000, Decimal("9.99")).commission, Decimal(5))
        self.assertEqual(fill("buy", 2000, Decimal("10.00")).commission, Decimal(5))
        self.assertEqual(fill("buy", 2000, Decimal("10.01")).commission, Decimal("5.01"))

    def test_three_slippage_scenarios(self):
        expected = {"0": "387.00", "0.01": "347.01", "0.02": "307.02"}
        for slip, net in expected.items():
            with self.subTest(slip=slip):
                sale, buy, result = round_trip(Decimal("3.00"), Decimal("2.80"), 2000, Decimal(slip))
                self.assertEqual(result, Decimal(net))
                self.assertEqual(sale.executed_price, Decimal("3.00") - Decimal(slip))
                self.assertEqual(buy.executed_price, Decimal("2.80") + Decimal(slip))

    def test_stamp_only_on_sell_and_invalid_fill_rejected(self):
        self.assertEqual(fill("buy", 2000, Decimal(3)).stamp_duty, 0)
        self.assertEqual(fill("sell", 2000, Decimal(3)).stamp_duty, Decimal(3))
        for side, shares, price, slip in [
            ("buy", 0, Decimal(3), Decimal(0)),
            ("sell", 1, Decimal(0), Decimal(0)),
            ("sell", 1, Decimal("0.01"), Decimal("0.02")),
        ]:
            with self.subTest(side=side, shares=shares):
                with self.assertRaises(ValueError):
                    fill(side, shares, price, slip)


class LedgerTests(unittest.TestCase):
    MON = date(2026, 10, 12)
    TUE = date(2026, 10, 13)

    def test_t_plus_one_rejects_same_day_new_lot(self):
        book = Portfolio(Decimal(1000))
        book.buy(self.MON, 100, Decimal(3))
        before = (book.cash, book.shares, book.unreplenished_shares)
        with self.assertRaises(ValueError):
            book.sell(self.MON, 100, Decimal(3))
        self.assertEqual((book.cash, book.shares, book.unreplenished_shares), before)
        book.sell(self.TUE, 100, Decimal(3))
        self.assertEqual(book.shares, 0)
        self.assertEqual(book.unreplenished_shares, 100)

    def test_no_same_day_sell_then_buy_even_with_old_shares(self):
        book = Portfolio(Decimal(0), 200)
        book.sell(self.MON, 100, Decimal(3))
        with self.assertRaisesRegex(ValueError, "same-day"):
            book.buy(self.MON, 100, Decimal(2))
        self.assertEqual(book.unreplenished_shares, 100)
        book.buy(self.TUE, 60, Decimal(2))
        self.assertEqual(book.unreplenished_shares, 40)
        self.assertEqual(book.shares, 160)

    def test_no_same_day_buy_then_sell_old_shares(self):
        book = Portfolio(Decimal(1000), 200)
        book.buy(self.MON, 100, Decimal(3))
        with self.assertRaisesRegex(ValueError, "same-day"):
            book.sell(self.MON, 100, Decimal(3))

    def test_insufficient_cash_or_shares_is_atomic(self):
        book = Portfolio(Decimal(0), 100)
        with self.assertRaises(ValueError):
            book.buy(self.MON, 100, Decimal(3))
        self.assertEqual((book.cash, book.shares), (Decimal(0), 100))
        with self.assertRaises(ValueError):
            book.sell(self.MON, 101, Decimal(3))
        self.assertEqual((book.cash, book.shares, book.unreplenished_shares), (Decimal(0), 100, 0))
        with self.assertRaises(ValueError):
            book.sell(self.MON, 1, Decimal("0.01"))
        self.assertEqual((book.cash, book.shares), (Decimal(0), 100))

    def test_partially_eligible_lots_and_chronology(self):
        book = Portfolio(Decimal(1000), 100)
        book.buy(self.MON, 100, Decimal(2))
        with self.assertRaises(ValueError):
            book.sell(self.MON, 101, Decimal(3))
        book.sell(self.TUE, 150, Decimal(3))
        self.assertEqual(book.shares, 50)
        with self.assertRaises(ValueError):
            book.sell(self.MON, 1, Decimal(3))

    def test_dividend_credits_prior_close_shares_once_before_ex_date_trades(self):
        book = Portfolio(Decimal(0), 100)
        self.assertEqual(book.credit_cash_dividend(self.MON, Decimal("0.086")), Decimal("8.60"))
        self.assertEqual(book.cash, Decimal("8.60"))
        book.sell(self.MON, 100, Decimal(3))
        with self.assertRaises(ValueError):
            book.credit_cash_dividend(self.MON, Decimal("0.086"))
        self.assertEqual(book.unreplenished_shares, 100)


class DataAndFactorTests(unittest.TestCase):
    def test_loader_selects_only_candles_from_envelope(self):
        with TemporaryDirectory() as folder:
            path = Path(folder) / "fixture.json"
            fields = ["ts_code", "trade_date", "open", "high", "low", "close",
                      "pre_close", "change", "pct_chg", "vol", "amount"]
            payload = {"fields": fields, "candles": [["601518.SH", "20261016", 3, 3, 3, 3, 3, 0, 0, 1, 1]],
                       "holdings": {"SECRET_MARKER": "must remain unread by module"}}
            path.write_text(json.dumps(payload))
            self.assertEqual(load_daily(path)[0].close, Decimal(3))

    def test_holiday_distinct_from_missing_trading_day(self):
        rows = (candle(date(2023, 12, 29), "3"), candle(date(2024, 1, 3), "3"))
        result = verify_daily(rows, {})
        self.assertIn(date(2024, 1, 1), result.closed_weekdays)
        self.assertEqual(result.missing_sessions, (date(2024, 1, 2),))
        self.assertFalse(result.valid)

    def test_duplicate_ohlc_and_unexplained_preclose_fail_closed(self):
        monday = date(2026, 10, 12)
        tuesday = date(2026, 10, 13)
        rows = (candle(monday, "3"), candle(tuesday, "3", "2.9", high="2.8", low="2.9"),
                candle(tuesday, "3", "3"))
        result = verify_daily(rows, {})
        self.assertEqual(result.duplicate_dates, (tuesday,))
        self.assertEqual(result.ohlc_errors, (tuesday,))
        self.assertTrue(any(not gap.reconciled for gap in result.preclose_gaps))
        self.assertFalse(result.valid)

    def test_precise_dividend_reconciles_rounded_preclose(self):
        wed, thu = date(2025, 7, 2), date(2025, 7, 3)
        rows = (candle(wed, "2.77"), candle(thu, "2.68", "2.68"))
        result = verify_daily(rows, {thu: Decimal("0.086")})
        self.assertTrue(result.valid)
        self.assertEqual(result.preclose_gaps[0].difference, Decimal("0.09"))
        factors = build_factors(rows, {thu: Decimal("0.086")})
        self.assertEqual(factors[0].factor, Decimal(1))
        self.assertEqual(factors[1].factor, Decimal("2.77") / Decimal("2.684"))

    def test_announced_dividend_without_reference_price_adjustment_fails(self):
        wed, thu = date(2025, 7, 2), date(2025, 7, 3)
        rows = (candle(wed, "2.77"), candle(thu, "2.68", "2.77"))
        result = verify_daily(rows, {thu: Decimal("0.086")})
        self.assertFalse(result.valid)
        self.assertEqual(len(result.preclose_gaps), 1)
        self.assertFalse(result.preclose_gaps[0].reconciled)
        with self.assertRaises(ValueError):
            build_factors(rows, {thu: Decimal("0.086")})

    def test_unannounced_gap_blocks_factors_and_benchmark(self):
        monday, tuesday = date(2026, 10, 12), date(2026, 10, 13)
        rows = (candle(monday, "3"), candle(tuesday, "3", "2.8"))
        with self.assertRaises(ValueError):
            build_factors(rows, {})
        with self.assertRaises(ValueError):
            buy_hold(rows, {}, 100, Decimal(4))

    def test_benchmark_dividend_is_cash_and_drawdown_includes_it(self):
        monday, tuesday, wednesday = [date(2026, 10, 12) + timedelta(days=i) for i in range(3)]
        rows = (candle(monday, "3"), candle(tuesday, "2.9", "3"),
                candle(wednesday, "3.1", "2.9"))
        result = buy_hold(rows, {}, 100, Decimal(4))
        self.assertEqual(result.window_pnl, Decimal(10))
        self.assertEqual(result.reference_cost_pnl, Decimal(-90))
        self.assertEqual(result.max_drawdown, Decimal("2.9") / Decimal(3) - 1)

    def test_benchmark_credits_announced_cash_without_reinvestment(self):
        wed, thu = date(2025, 7, 2), date(2025, 7, 3)
        rows = (candle(wed, "2.77"), candle(thu, "2.68", "2.68"))
        result = buy_hold(rows, {thu: Decimal("0.086")}, 100)
        self.assertEqual(result.total_dividend_cash, Decimal("8.600"))
        self.assertEqual(result.points[-1].equity, Decimal("276.600"))
        self.assertEqual(result.window_pnl, Decimal("-0.400"))


class WeeklyAvailabilityTests(unittest.TestCase):
    def test_future_friday_and_preclose_time_are_not_visible(self):
        days = trading_dates(date(2026, 10, 12), date(2026, 10, 16))
        bars = tuple(AdjustedBar(day, Decimal(3), Decimal(100 if day.weekday() == 4 else 3),
                                 Decimal(3), Decimal(3)) for day in days)
        tz = ZoneInfo("Asia/Shanghai")
        self.assertEqual(confirmed_weekly_bars(bars, datetime(2026, 10, 15, 15, tzinfo=tz)), ())
        self.assertEqual(confirmed_weekly_bars(bars, datetime(2026, 10, 16, 14, 59, tzinfo=tz)), ())
        self.assertEqual(confirmed_weekly_bars(bars, datetime(2026, 10, 16, 15, tzinfo=tz)), ())
        confirmed = confirmed_weekly_bars(bars, datetime(2026, 10, 16, 15, 1, tzinfo=tz))
        self.assertEqual(len(confirmed), 1)
        self.assertEqual(confirmed[0].high, Decimal(100))

    def test_missing_completed_week_fails_and_holiday_short_week_confirms(self):
        tz = ZoneInfo("Asia/Shanghai")
        dates = trading_dates(date(2026, 10, 5), date(2026, 10, 9))
        self.assertEqual(dates, (date(2026, 10, 8), date(2026, 10, 9)))
        bars = tuple(AdjustedBar(day, Decimal(3), Decimal(3), Decimal(3), Decimal(3)) for day in dates)
        self.assertEqual(len(confirmed_weekly_bars(bars, datetime(2026, 10, 9, 15, 1, tzinfo=tz))), 1)
        with self.assertRaises(ValueError):
            confirmed_weekly_bars(bars[:1], datetime(2026, 10, 9, 15, 1, tzinfo=tz))


if __name__ == "__main__":
    unittest.main()
