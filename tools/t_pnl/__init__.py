"""Manual research accounting for completed sell-then-buy T trades."""

from .calc import CalculationResult, PairDetail, Scenario, Trade, UnmatchedLeg, calculate_t_pnl

__all__ = ["CalculationResult", "PairDetail", "Scenario", "Trade", "UnmatchedLeg",
           "calculate_t_pnl"]
