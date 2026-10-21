"""Frozen Stage 1 boundaries; no strategy parameters live here."""

from dataclasses import dataclass
from datetime import date
from decimal import Decimal


@dataclass(frozen=True)
class Stage1Config:
    development_start: date = date(2023, 10, 9)
    development_end: date = date(2025, 9, 30)
    out_of_sample_start: date = date(2025, 10, 1)
    commission_rate: Decimal = Decimal("0.00025")
    minimum_commission: Decimal = Decimal("5")
    sell_stamp_rate: Decimal = Decimal("0.0005")
    baseline_slippage: Decimal = Decimal("0.01")
    stress_slippage: Decimal = Decimal("0.02")
    optimistic_slippage: Decimal = Decimal("0")


STAGE1 = Stage1Config()
