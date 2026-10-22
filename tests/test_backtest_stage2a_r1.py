"""Synthetic R1 rule checks; no market candles or strategy backtest."""

from decimal import Decimal
import json
from pathlib import Path
import unittest

from backtest.stage2a_policy import (
    buyback_signal_gate,
    cancel_pending_sells_on_pause,
    displayed_state,
    next_open_approximate_fill,
    sell_candidate,
)
from backtest.validate_rules_v1 import validate


RULES = json.loads((Path(__file__).resolve().parents[1] / "backtest" / "rules_v1.json").read_text())


class Stage2AR1Tests(unittest.TestCase):
    def test_static_rules_and_remaining_gates(self):
        rules = validate()
        self.assertEqual(rules["blocking_question_ids"], ["Q01", "Q14"])
        self.assertEqual(rules["blocking_gate_ids"], ["ready_for_stage2b"])
        self.assertFalse(rules["ready_for_stage2b"])
        self.assertEqual(rules["execution"]["fill_model_label"], "日线开盘成交近似模型")

    def test_sell_triggers_in_box_and_in_order(self):
        a = {"A": True}
        cases = [
            ("2.75", (), None),
            ("2.95", (), 1),
            ("3.00", (), 1),  # First batch remains unfilled.
            ("2.95", (1,), None),
            ("3.00", (1,), 2),
            ("3.01", (), None),
            ("3.01", (1,), None),
            ("3.00", (1, 2), None),
        ]
        for close, filled, expected in cases:
            with self.subTest(close=close, filled=filled):
                self.assertEqual(sell_candidate(RULES, Decimal(close), a, filled), expected)

    def test_higher_priority_pauses_and_B_cancels_pending(self):
        for higher in ("B", "C", "D"):
            with self.subTest(higher=higher):
                active = {"A": True, higher: True}
                self.assertEqual(displayed_state(active), higher)
                self.assertIsNone(sell_candidate(RULES, Decimal("2.95"), active, ()))
                self.assertEqual(cancel_pending_sells_on_pause([1], active), [])
        self.assertEqual(cancel_pending_sells_on_pause([1], {"A": True}), [1])
        self.assertEqual(displayed_state({"A": True, "B": True, "C": True, "D": True}), "D")

    def test_open_outside_band_cancels_candidate(self):
        sell_band = RULES["orders"]["sell_bands_raw_rmb_inclusive"][0]
        buy_band = RULES["orders"]["buyback_bands_raw_rmb_inclusive"][0]
        self.assertIsNone(next_open_approximate_fill("sell", 100, "3.01", sell_band, "0.01"))
        self.assertIsNone(next_open_approximate_fill("buy", 100, "2.90", buy_band, "0.01"))
        self.assertEqual(next_open_approximate_fill("sell", 100, "3.00", sell_band, "0.01").executed_price,
                         Decimal("2.99"))

    def test_signal_gate_is_independent_of_next_open_and_gap_changes_realized_result(self):
        # 100 sold shares brought 300 RMB after sell fees. This is synthetic cash.
        threshold = Decimal("0.10")  # Test input only; production Q14 remains null.
        passed, estimate = buyback_signal_gate("2.80", "300", 100, threshold, "0.01")
        self.assertTrue(passed)
        self.assertEqual(estimate, Decimal("0.14"))
        band = RULES["orders"]["buyback_bands_raw_rmb_inclusive"][0]
        cheaper = next_open_approximate_fill("buy", 100, "2.76", band, "0.01", passed)
        dearer = next_open_approximate_fill("buy", 100, "2.82", band, "0.01", passed)
        self.assertIsNotNone(cheaper)
        self.assertIsNotNone(dearer)
        self.assertNotEqual(Decimal("300") + cheaper.cash_change,
                            Decimal("300") + dearer.cash_change)
        self.assertEqual(buyback_signal_gate("2.80", "300", 100, threshold, "0.01"),
                         (passed, estimate))  # Next-day prices cannot change yesterday's decision.
        self.assertIsNone(next_open_approximate_fill("buy", 100, "9.99", band, "0.01", passed))
        failed, _ = buyback_signal_gate("2.82", "300", 100, Decimal("0.20"), "0.01")
        self.assertFalse(failed)
        self.assertIsNone(next_open_approximate_fill("buy", 100, "2.76", band, "0.01", failed))


if __name__ == "__main__":
    unittest.main()
