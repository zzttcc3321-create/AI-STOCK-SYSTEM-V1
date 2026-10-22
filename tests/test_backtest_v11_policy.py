"""V1.1 close-signal and offline risk permission boundaries."""

from decimal import Decimal
import unittest

from backtest.v11_policy import (normal_buyback_candidate, risk_exit_candidate,
                                 sell_candidate_v11)


class V11PolicyTests(unittest.TestCase):
    def test_box_endpoints_batch_order_and_breakout(self):
        active = {"A": True, "B": False, "C": False, "D": False}
        self.assertEqual(sell_candidate_v11("2.95", active, (), False), 1)
        self.assertIsNone(sell_candidate_v11("2.94", active, (), False))
        self.assertEqual(sell_candidate_v11("3.00", active, (1,), False), 2)
        self.assertIsNone(sell_candidate_v11("3.00", active, (1,), True))
        self.assertIsNone(sell_candidate_v11("3.04", active, (), False))
        self.assertIsNone(sell_candidate_v11("2.95", active, (), True, Decimal("2.95")))

    def test_b_c_d_pause_priority(self):
        for state in ("B", "C", "D"):
            active = {"A": True, "B": False, "C": False, "D": False}
            active[state] = True
            self.assertIsNone(sell_candidate_v11("2.99", active, (), False))
        self.assertIsNone(normal_buyback_candidate("2.80", {"C": True}, 2500, 7400, "0.010", "0.01"))
        self.assertIsNone(normal_buyback_candidate("2.80", {"D": True}, 2500, 7400, "0.010", "0.01"))

    def test_regular_gate_and_a7_persistent_exception(self):
        active = {"B": False, "C": False, "D": False}
        band = normal_buyback_candidate("2.82", active, 2500, 7400, "0.010", "0.01")
        self.assertEqual(band, (Decimal("2.76"), Decimal("2.82")))
        self.assertIsNone(normal_buyback_candidate("2.83", active, 2500, 7400, "0.010", "0.01"))
        self.assertEqual(risk_exit_candidate("3.10", active, True, False, 1, 5), (True, True))
        self.assertEqual(risk_exit_candidate("2.90", active, False, False, 2, 5, pending=True), (True, True))
        self.assertEqual(risk_exit_candidate("2.90", {**active, "C": True}, False, False, 5, 5), (True, False))
        self.assertEqual(risk_exit_candidate("2.90", active, False, False, 5, 5), (True, True))
        self.assertEqual(risk_exit_candidate("2.90", active, False, False, 4, 5), (False, False))


if __name__ == "__main__":
    unittest.main()
