"""Causal total-return price adjustment for indicators; never for fills."""

from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from .data import Candle, verify_daily


VERSION = "stage1-cash-dividend-v1"


@dataclass(frozen=True)
class Factor:
    trade_date: date
    factor: Decimal
    cash_dividend: Decimal


@dataclass(frozen=True)
class AdjustedBar:
    trade_date: date
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal


def build_factors(candles: tuple[Candle, ...], dividends: dict[date, Decimal]) -> tuple[Factor, ...]:
    checked = verify_daily(candles, dividends)
    if not checked.valid:
        raise ValueError("unverified price history; adjustment stopped")
    rows = tuple(sorted(candles, key=lambda item: item.trade_date))
    factor = Decimal(1)
    output = []
    for index, row in enumerate(rows):
        cash = dividends.get(row.trade_date, Decimal(0))
        if cash:
            if index == 0 or cash <= 0 or rows[index - 1].close <= cash:
                raise ValueError("invalid dividend adjustment")
            previous_close = rows[index - 1].close
            # Exact announced cash amount; the vendor's two-decimal pre_close is
            # used only for validation and can differ after rounding (2025: .086).
            factor *= previous_close / (previous_close - cash)
        output.append(Factor(row.trade_date, factor, cash))
    if set(dividends) - {row.trade_date for row in rows}:
        raise ValueError("dividend date absent from source history")
    return tuple(output)


def adjusted_bars(candles: tuple[Candle, ...], factors: tuple[Factor, ...]) -> tuple[AdjustedBar, ...]:
    if len(candles) != len(factors):
        raise ValueError("factor coverage mismatch")
    result = []
    for row, item in zip(sorted(candles, key=lambda x: x.trade_date), factors):
        if row.trade_date != item.trade_date:
            raise ValueError("factor date mismatch")
        result.append(AdjustedBar(row.trade_date, row.open * item.factor, row.high * item.factor,
                                  row.low * item.factor, row.close * item.factor))
    return tuple(result)
