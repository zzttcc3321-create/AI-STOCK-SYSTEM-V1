"""Observed-window buy/hold mark-to-market with cash dividends, no trades."""

from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from .data import Candle, verify_daily


@dataclass(frozen=True)
class BenchmarkPoint:
    trade_date: date
    close: Decimal
    dividend_cash_cumulative: Decimal
    equity: Decimal
    drawdown: Decimal


@dataclass(frozen=True)
class Benchmark:
    points: tuple[BenchmarkPoint, ...]
    shares: int
    entry_price: Decimal
    reference_cost: Decimal | None
    total_dividend_cash: Decimal
    window_pnl: Decimal
    window_return: Decimal
    reference_cost_pnl: Decimal | None
    reference_cost_return: Decimal | None
    max_drawdown: Decimal
    peak_date: date
    trough_date: date


def buy_hold(candles: tuple[Candle, ...], dividends: dict[date, Decimal], shares: int,
             reference_cost: Decimal | None = None) -> Benchmark:
    checked = verify_daily(candles, dividends)
    if not checked.valid:
        raise ValueError("unverified price history; benchmark stopped")
    if shares <= 0 or (reference_cost is not None and reference_cost <= 0):
        raise ValueError("invalid reference position")
    rows = tuple(sorted(candles, key=lambda item: item.trade_date))
    entry = rows[0].close
    cash = Decimal(0)
    peak_equity = Decimal(0)
    peak_date = rows[0].trade_date
    worst_peak, worst_trough = peak_date, peak_date
    worst = Decimal(0)
    points = []
    for row in rows:
        cash += shares * dividends.get(row.trade_date, Decimal(0))
        equity = shares * row.close + cash
        if equity > peak_equity:
            peak_equity, peak_date = equity, row.trade_date
        drawdown = equity / peak_equity - 1
        if drawdown < worst:
            worst, worst_peak, worst_trough = drawdown, peak_date, row.trade_date
        points.append(BenchmarkPoint(row.trade_date, row.close, cash, equity, drawdown))
    final_equity = points[-1].equity
    window_basis = shares * entry
    cost_basis = shares * reference_cost if reference_cost is not None else None
    return Benchmark(tuple(points), shares, entry, reference_cost, cash,
                     final_equity - window_basis, final_equity / window_basis - 1,
                     final_equity - cost_basis if cost_basis is not None else None,
                     final_equity / cost_basis - 1 if cost_basis is not None else None,
                     worst, worst_peak, worst_trough)
