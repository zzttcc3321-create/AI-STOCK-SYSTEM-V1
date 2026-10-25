"""Synthetic filled trades only; no holdings, broker, or database access."""

from datetime import date, datetime
from decimal import Decimal
import unittest

from tools.t_pnl import Trade, calculate_t_pnl


D = Decimal
MON = date(2026, 10, 19)
TUE = date(2026, 10, 20)


class TProfitTests(unittest.TestCase):
    def test_worked_example_minimum_fee_stamp_and_three_slippages(self):
        result = calculate_t_pnl([(MON, "SELL", "2.70", 2500),
                                  (TUE, "BUY", "2.65", 2500)])
        pair = result.pairs[0]
        self.assertEqual((pair.price_spread, pair.actual_gross_pnl), (D("0.05"), D("125.00")))
        self.assertEqual(set(pair.scenarios), {D("0"), D("0.01"), D("0.02")})
        self.assertEqual(pair.scenarios[D("0")].sell_commission, D("5.00"))
        self.assertEqual(pair.scenarios[D("0")].buy_commission, D("5.00"))
        self.assertEqual(pair.scenarios[D("0")].sell_stamp_duty, D("3.38"))
        self.assertEqual(pair.scenarios[D("0")].total_cost, D("13.38"))
        self.assertEqual(pair.scenarios[D("0.01")].total_cost, D("63.38"))
        self.assertEqual(pair.scenarios[D("0.02")].total_cost, D("113.38"))
        self.assertEqual(result.scenario_totals[D("0")].estimated_net_pnl, D("111.62"))
        self.assertIsNone(pair.actual_net_pnl)
        self.assertIsNone(result.actual_net_pnl)

    def test_fifo_partial_fills_and_split_buyback_do_not_repeat_sale_minimum(self):
        result = calculate_t_pnl([(MON, "SELL", "10", 1000),
                                  (TUE, "BUY", "9", 400),
                                  (TUE, "BUY", "8", 600)])
        self.assertEqual([(p.sell_index, p.buy_index, p.shares) for p in result.pairs],
                         [(0, 1, 400), (0, 2, 600)])
        self.assertEqual([p.scenarios[D("0")].sell_commission for p in result.pairs],
                         [D("2.00"), D("3.00")])
        self.assertEqual(result.scenario_totals[D("0")].sell_commission, D("5.00"))
        self.assertEqual(result.scenario_totals[D("0")].estimated_net_pnl, D("1580.00"))
        self.assertEqual(result.unmatched, ())

    def test_fifo_many_sales_one_buy_and_open_exposure(self):
        result = calculate_t_pnl([(MON, "SELL", "3.00", 100),
                                  (MON, "SELL", "2.90", 100),
                                  (TUE, "BUY", "2.80", 150)])
        self.assertEqual([(p.sell_index, p.shares) for p in result.pairs], [(0, 100), (1, 50)])
        self.assertEqual([(leg.trade_index, leg.side, leg.shares) for leg in result.unmatched],
                         [(1, "SELL", 50)])
        self.assertEqual(result.actual_gross_pnl, D("25.00"))

    def test_open_sale_has_no_realized_pnl_or_repeated_fee(self):
        result = calculate_t_pnl([
            Trade(MON, "SELL", D("10"), 1000, D("5"), D("5")),
            Trade(TUE, "BUY", D("9"), 400, D("5")),
        ])
        self.assertEqual(result.unmatched[0].shares, 600)
        self.assertEqual(result.pairs[0].paid_sell_commission, D("2.00"))
        self.assertEqual(result.pairs[0].paid_sell_stamp_duty, D("2.00"))
        self.assertEqual(result.actual_net_pnl, D("391.00"))
        self.assertEqual(result.scenario_totals[D("0")].sell_commission, D("2.00"))

    def test_commission_above_minimum_uses_frozen_fee_model(self):
        result = calculate_t_pnl([(MON, "SELL", "10.01", 2000),
                                  (TUE, "BUY", "10.01", 2000)])
        scenario = result.pairs[0].scenarios[D("0")]
        self.assertEqual((scenario.sell_commission, scenario.buy_commission),
                         (D("5.01"), D("5.01")))
        self.assertEqual(scenario.sell_stamp_duty, D("10.01"))

    def test_commission_boundary_on_both_sides(self):
        for price, commission in (("9.99", "5.00"), ("10.00", "5.00"),
                                  ("10.01", "5.01")):
            with self.subTest(price=price):
                result = calculate_t_pnl([(MON, "SELL", price, 2000),
                                          (TUE, "BUY", price, 2000)])
                scenario = result.scenario_totals[D("0")]
                self.assertEqual(scenario.sell_commission, D(commission))
                self.assertEqual(scenario.buy_commission, D(commission))

    def test_many_to_many_allocations_conserve_fee_cents(self):
        result = calculate_t_pnl([
            Trade(MON, "SELL", D("2.70"), 3, D("5.00"), D("0.01")),
            Trade(MON, "SELL", D("3.10"), 2, D("5.01"), D("0.01")),
            Trade(TUE, "BUY", D("2.60"), 2, D("5.03")),
            Trade(TUE, "BUY", D("2.50"), 3, D("5.02")),
        ])
        self.assertEqual([(p.sell_index, p.buy_index, p.shares) for p in result.pairs],
                         [(0, 2, 2), (0, 3, 1), (1, 3, 2)])
        self.assertEqual([p.paid_sell_commission for p in result.pairs],
                         [D("3.33"), D("1.67"), D("5.01")])
        self.assertEqual([p.paid_buy_commission for p in result.pairs],
                         [D("5.03"), D("1.67"), D("3.35")])
        self.assertEqual([p.paid_sell_stamp_duty for p in result.pairs],
                         [D("0.01"), D("0.00"), D("0.01")])
        self.assertEqual([p.actual_net_pnl for p in result.pairs],
                         [D("-8.17"), D("-3.14"), D("-7.17")])
        self.assertEqual(result.actual_gross_pnl, D("1.60"))
        self.assertEqual(result.actual_net_pnl, D("-18.48"))
        self.assertEqual(result.scenario_totals[D("0")].total_cost, D("20.00"))
        self.assertEqual(result.scenario_totals[D("0.01")].estimated_net_pnl,
                         D("-18.50"))
        self.assertEqual(result.unmatched, ())

    def test_excess_buy_only_allocates_matched_fraction_of_buy_fees(self):
        result = calculate_t_pnl([
            Trade(MON, "SELL", D("10"), 1, D("5"), D("0.01")),
            Trade(TUE, "BUY", D("9"), 3, D("5")),
        ])
        self.assertEqual(result.pairs[0].paid_buy_commission, D("1.67"))
        self.assertEqual(result.actual_net_pnl, D("-5.68"))
        self.assertEqual(result.scenario_totals[D("0")].buy_commission, D("1.67"))
        self.assertEqual([(leg.trade_index, leg.side, leg.shares) for leg in result.unmatched],
                         [(1, "BUY", 2)])

    def test_early_and_excess_buys_remain_unmatched(self):
        result = calculate_t_pnl([(MON, "BUY", "2", 100),
                                  (MON, "SELL", "3", 100),
                                  (TUE, "BUY", "2", 150)])
        self.assertEqual(len(result.pairs), 1)
        self.assertEqual(result.pairs[0].shares, 100)
        self.assertEqual([(leg.trade_index, leg.shares, leg.reason) for leg in result.unmatched],
                         [(0, 100, "BUY without prior SELL"),
                          (2, 50, "BUY without prior SELL")])
        self.assertEqual(result.actual_gross_pnl, D("100.00"))

    def test_zero_and_negative_results_are_retained(self):
        result = calculate_t_pnl([(MON, "SELL", "3.00", 100),
                                  (TUE, "BUY", "3.00", 100),
                                  (TUE, "SELL", "2.00", 100),
                                  (TUE, "BUY", "3.00", 100)])
        self.assertEqual([p.actual_gross_pnl for p in result.pairs],
                         [D("0.00"), D("-100.00")])
        self.assertEqual([p.scenarios[D("0")].estimated_net_pnl for p in result.pairs],
                         [D("-10.15"), D("-110.10")])

    def test_zero_net_and_positive_spread_with_net_loss(self):
        for buy_price, expected in (("1.989", "0.00"), ("1.99", "-1.00")):
            with self.subTest(buy_price=buy_price):
                result = calculate_t_pnl([
                    Trade(MON, "SELL", D("2.00"), 1000, D("5"), D("1")),
                    Trade(TUE, "BUY", D(buy_price), 1000, D("5")),
                ])
                self.assertGreater(result.actual_gross_pnl, 0)
                self.assertEqual(result.actual_net_pnl, D(expected))
                self.assertEqual(result.scenario_totals[D("0")].estimated_net_pnl,
                                 D(expected))

    def test_same_day_buyback_is_flagged_and_counted(self):
        result = calculate_t_pnl([(MON, "SELL", "3", 100), (MON, "BUY", "2", 100)])
        self.assertTrue(result.pairs[0].t_plus_zero_warning)
        self.assertEqual(result.actual_gross_pnl, D("100.00"))

    def test_t_plus_zero_warning_is_per_pair(self):
        result = calculate_t_pnl([(MON, "SELL", "3", 100),
                                  (TUE, "SELL", "3", 100),
                                  (TUE, "BUY", "2", 200)])
        self.assertEqual([p.t_plus_zero_warning for p in result.pairs], [False, True])
        self.assertEqual(result.actual_gross_pnl, D("200.00"))

    def test_paid_fees_are_separate_and_allocated_without_double_deduction(self):
        result = calculate_t_pnl([
            Trade(MON, "SELL", D("3"), 1000, D("7.00"), D("3.00")),
            Trade(TUE, "BUY", D("2.9"), 400, D("5.00")),
            Trade(TUE, "BUY", D("2.8"), 600, D("6.00")),
        ])
        self.assertEqual([p.paid_sell_commission for p in result.pairs],
                         [D("2.80"), D("4.20")])
        self.assertEqual([p.actual_net_pnl for p in result.pairs],
                         [D("31.00"), D("108.00")])
        self.assertEqual(result.actual_net_pnl, D("139.00"))
        self.assertNotEqual(result.actual_net_pnl,
                            result.scenario_totals[D("0")].estimated_net_pnl)

    def test_missing_paid_fees_leave_actual_net_unknown(self):
        result = calculate_t_pnl([
            Trade(MON, "SELL", D("3"), 100, D("5"), D("0.15")),
            (TUE, "BUY", "2", 100),
        ])
        self.assertIsNone(result.pairs[0].actual_net_pnl)
        self.assertIsNone(result.actual_net_pnl)

    def test_one_unknown_pair_leaves_aggregate_actual_net_unknown(self):
        result = calculate_t_pnl([
            Trade(MON, "SELL", D("3"), 100, D("5"), D("0.15")),
            Trade(TUE, "BUY", D("2"), 100, D("5")),
            (TUE, "SELL", "3", 100),
            (TUE, "BUY", "2", 100),
        ])
        self.assertEqual(result.pairs[0].actual_net_pnl, D("89.85"))
        self.assertIsNone(result.pairs[1].actual_net_pnl)
        self.assertIsNone(result.actual_net_pnl)
        self.assertEqual(result.scenario_totals[D("0")].estimated_net_pnl, D("179.70"))

    def test_only_unmatched_trades_have_zero_realized_totals(self):
        result = calculate_t_pnl([
            (MON, "BUY", "2", 100),
            Trade(TUE, "SELL", D("3"), 200, D("5"), D("0.30")),
        ])
        self.assertEqual(result.pairs, ())
        self.assertEqual([(leg.side, leg.shares) for leg in result.unmatched],
                         [("BUY", 100), ("SELL", 200)])
        self.assertEqual((result.actual_gross_pnl, result.actual_net_pnl),
                         (D("0.00"), D("0.00")))
        for scenario in result.scenario_totals.values():
            self.assertEqual(scenario.total_cost, D("0.00"))
            self.assertEqual(scenario.estimated_net_pnl, D("0.00"))

    def test_custom_scenarios_do_not_change_actual_net(self):
        result = calculate_t_pnl([
            Trade("2026-10-19", "SELL", D("3"), 100, D("0"), D("0")),
            Trade("2026-10-20", "BUY", D("2"), 100, D("0"), D("0")),
        ], ["0", "0.03"])
        self.assertEqual(result.actual_net_pnl, D("100.00"))
        self.assertEqual(result.pairs[0].sell_date, MON)
        self.assertEqual(set(result.scenario_totals), {D("0"), D("0.03")})
        self.assertEqual(result.scenario_totals[D("0.03")].slippage_cost, D("6.00"))
        self.assertEqual(result.scenario_totals[D("0.03")].estimated_net_pnl, D("83.85"))

    def test_iterable_inputs_are_repeatable_without_mutating_trades(self):
        trades = [["2026-10-19", "SELL", "3", 100],
                  ["2026-10-20", "BUY", "2", 100]]
        original = [row[:] for row in trades]
        expected = calculate_t_pnl(trades)
        result = calculate_t_pnl((row for row in trades), (s for s in ("0", "0.01", "0.02")))
        self.assertEqual(result, expected)
        self.assertEqual(trades, original)

    def test_custom_slippage_and_empty_input(self):
        result = calculate_t_pnl([], ["0.03"])
        self.assertEqual(result.pairs, ())
        self.assertEqual(result.actual_net_pnl, D("0.00"))
        self.assertEqual(result.scenario_totals[D("0.03")].estimated_net_pnl, D("0.00"))

    def test_invalid_inputs_fail_closed(self):
        bad_sequences = [
            [None],
            [(MON, "SELL", "3")],
            [("2026-02-30", "SELL", "3", 1)],
            [(datetime(2026, 10, 19), "SELL", "3", 1)],
            [(MON, "SELL", "3", 0)],
            [(MON, "SELL", "3", -1)],
            [(MON, "SELL", "3", True)],
            [(MON, "SELL", "3", 1.5)],
            [(MON, "SELL", "0", 1)],
            [(MON, "SELL", "-1", 1)],
            [(MON, "SELL", 2.7, 1)],
            [(MON, "SELL", True, 1)],
            [(MON, "SELL", None, 1)],
            [(MON, "SELL", "bad", 1)],
            [(MON, "SELL", "NaN", 1)],
            [(MON, "SELL", "Infinity", 1)],
            [(MON, "sell", "3", 1)],
            [(TUE, "SELL", "3", 1), (MON, "BUY", "2", 1)],
            [Trade(MON, "SELL", D("3"), 1, D("5"))],
            [Trade(MON, "BUY", D("3"), 1, D("5"), D("0.01"))],
            [Trade(MON, "SELL", D("3"), 1, D("5.001"), D("0.01"))],
            [Trade(MON, "SELL", D("3"), 1, D("-5"), D("0.01"))],
            [Trade(MON, "SELL", D("3"), 1, D("5"), D("NaN"))],
            [Trade(MON, "SELL", D("3"), 1, stamp_duty=D("0.01"))],
        ]
        for sequence in bad_sequences:
            with self.subTest(sequence=sequence), self.assertRaises(ValueError):
                calculate_t_pnl(sequence)
        for slips in ([], ["-0.01"], ["0", "0.00"], ["NaN"], ["Infinity"],
                      [0.01], [True]):
            with self.subTest(slips=slips), self.assertRaises(ValueError):
                calculate_t_pnl([], slips)


if __name__ == "__main__":
    unittest.main()
