"""Pure FIFO accounting for completed sell-then-buy T trades.

Prices are executed prices. Model fees come from the frozen backtest fee
implementation; optional paid fees are kept separate from model estimates.
"""

from collections import deque
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Iterable

from backtest.config import STAGE1
from backtest.fees import fill, money


ZERO = Decimal("0.00")
DEFAULT_SLIPPAGES = (
    STAGE1.optimistic_slippage,
    STAGE1.baseline_slippage,
    STAGE1.stress_slippage,
)


@dataclass(frozen=True)
class Trade:
    date: date
    side: str
    price: Decimal
    shares: int
    commission: Decimal | None = None
    stamp_duty: Decimal | None = None


@dataclass(frozen=True)
class Scenario:
    """Model estimate: fees at executed prices plus explicit two-side slippage."""

    sell_commission: Decimal
    buy_commission: Decimal
    sell_stamp_duty: Decimal
    slippage_cost: Decimal
    total_cost: Decimal
    estimated_net_pnl: Decimal


@dataclass(frozen=True)
class PairDetail:
    sell_index: int
    buy_index: int
    sell_date: date
    buy_date: date
    sell_price: Decimal
    buy_price: Decimal
    shares: int
    price_spread: Decimal
    actual_gross_pnl: Decimal
    paid_sell_commission: Decimal | None
    paid_buy_commission: Decimal | None
    paid_sell_stamp_duty: Decimal | None
    actual_net_pnl: Decimal | None
    t_plus_zero_warning: bool
    scenarios: dict[Decimal, Scenario]


@dataclass(frozen=True)
class UnmatchedLeg:
    trade_index: int
    date: date
    side: str
    price: Decimal
    shares: int
    reason: str


@dataclass(frozen=True)
class CalculationResult:
    pairs: tuple[PairDetail, ...]
    unmatched: tuple[UnmatchedLeg, ...]
    actual_gross_pnl: Decimal
    actual_net_pnl: Decimal | None
    scenario_totals: dict[Decimal, Scenario]


def _decimal(value: object, name: str) -> Decimal:
    if isinstance(value, (float, bool)):
        raise ValueError(f"{name} must be a decimal string or Decimal")
    try:
        result = Decimal(value)
    except (TypeError, ValueError, ArithmeticError) as exc:
        raise ValueError(f"invalid {name}") from exc
    if not result.is_finite():
        raise ValueError(f"invalid {name}")
    return result


def _paid(value: object, name: str) -> Decimal:
    amount = _decimal(value, name)
    if amount < 0 or amount != money(amount):
        raise ValueError(f"{name} must be nonnegative whole cents")
    return money(amount)


def _trade(value: Trade | tuple, index: int) -> Trade:
    if not isinstance(value, Trade):
        if not isinstance(value, (tuple, list)) or len(value) != 4:
            raise ValueError(f"trade {index} must be Trade or (date, side, price, shares)")
        value = Trade(*value)
    day = value.date
    if isinstance(day, str):
        try:
            day = date.fromisoformat(day)
        except ValueError as exc:
            raise ValueError(f"invalid date for trade {index}") from exc
    if not isinstance(day, date) or type(day) is not date:
        raise ValueError(f"invalid date for trade {index}")
    if value.side not in ("SELL", "BUY"):
        raise ValueError(f"invalid side for trade {index}")
    if type(value.shares) is not int or value.shares <= 0:
        raise ValueError(f"invalid shares for trade {index}")
    price = _decimal(value.price, "price")
    if price <= 0:
        raise ValueError(f"invalid price for trade {index}")
    if value.commission is None:
        if value.stamp_duty is not None:
            raise ValueError("stamp duty requires paid commission")
        commission = stamp = None
    else:
        commission = _paid(value.commission, "commission")
        if value.side == "BUY":
            stamp = ZERO if value.stamp_duty is None else _paid(value.stamp_duty, "stamp duty")
            if stamp != ZERO:
                raise ValueError("buy-side stamp duty must be zero")
        else:
            if value.stamp_duty is None:
                raise ValueError("paid sell stamp duty is required with paid commission")
            stamp = _paid(value.stamp_duty, "stamp duty")
    return Trade(day, value.side, price, value.shares, commission, stamp)


class _Leg:
    def __init__(self, index: int, trade: Trade) -> None:
        self.index = index
        self.trade = trade
        self.used = 0
        modeled = fill(trade.side.lower(), trade.shares, trade.price)
        self.totals = (modeled.gross, modeled.commission, modeled.stamp_duty,
                       trade.commission, trade.stamp_duty)

    @property
    def remaining(self) -> int:
        return self.trade.shares - self.used

    def take(self, shares: int) -> tuple[Decimal | None, ...]:
        """Allocate original-fill cents cumulatively, with no extra minimum fee."""
        before = self.used
        self.used += shares
        return tuple(
            None if total is None else
            money(total * self.used / self.trade.shares)
            - money(total * before / self.trade.shares)
            for total in self.totals
        )


def _scenario(gross: Decimal, sell_commission: Decimal, buy_commission: Decimal,
              stamp: Decimal, shares: int, slip: Decimal) -> Scenario:
    slip_cost = money(2 * slip * shares)
    cost = sell_commission + buy_commission + stamp + slip_cost
    return Scenario(sell_commission, buy_commission, stamp, slip_cost, cost, gross - cost)


def calculate_t_pnl(
    trades: Iterable[Trade | tuple],
    slippages: Iterable[Decimal | str] = DEFAULT_SLIPPAGES,
) -> CalculationResult:
    """Match filled SELL lots to later BUY fills in input order using FIFO.

    Missing paid fees leave actual net P&L unknown (None); scenario net values
    are estimates. Unmatched quantities never enter either realized total.
    """
    slips = tuple(_decimal(value, "slippage") for value in slippages)
    if not slips or len(set(slips)) != len(slips) or any(s < 0 for s in slips):
        raise ValueError("slippages must be distinct nonnegative values")
    pending: deque[_Leg] = deque()
    pairs: list[PairDetail] = []
    unmatched: list[UnmatchedLeg] = []
    previous_day: date | None = None
    for index, value in enumerate(trades):
        trade = _trade(value, index)
        if previous_day is not None and trade.date < previous_day:
            raise ValueError("trades must be in nondecreasing date order")
        previous_day = trade.date
        leg = _Leg(index, trade)
        if trade.side == "SELL":
            pending.append(leg)
            continue
        while leg.remaining and pending:
            sale = pending[0]
            shares = min(sale.remaining, leg.remaining)
            sale_gross, sale_fee, sale_stamp, paid_sale_fee, paid_stamp = sale.take(shares)
            buy_gross, buy_fee, _, paid_buy_fee, _ = leg.take(shares)
            gross = sale_gross - buy_gross
            paid_net = (None if paid_sale_fee is None or paid_buy_fee is None
                        or paid_stamp is None else gross - paid_sale_fee - paid_buy_fee - paid_stamp)
            pairs.append(PairDetail(
                sale.index, index, sale.trade.date, trade.date,
                sale.trade.price, trade.price, shares,
                sale.trade.price - trade.price, gross,
                paid_sale_fee, paid_buy_fee, paid_stamp, paid_net,
                sale.trade.date == trade.date,
                {slip: _scenario(gross, sale_fee, buy_fee, sale_stamp, shares, slip)
                 for slip in slips},
            ))
            if not sale.remaining:
                pending.popleft()
        if leg.remaining:
            unmatched.append(UnmatchedLeg(index, trade.date, "BUY", trade.price,
                                          leg.remaining, "BUY without prior SELL"))
    for sale in pending:
        unmatched.append(UnmatchedLeg(sale.index, sale.trade.date, "SELL", sale.trade.price,
                                      sale.remaining, "SELL awaiting buyback"))
    unmatched.sort(key=lambda item: item.trade_index)
    gross_total = sum((pair.actual_gross_pnl for pair in pairs), ZERO)
    actual_total = (None if any(pair.actual_net_pnl is None for pair in pairs) else
                    sum((pair.actual_net_pnl for pair in pairs), ZERO))
    totals = {}
    for slip in slips:
        scenarios = [pair.scenarios[slip] for pair in pairs]
        totals[slip] = Scenario(
            *(sum((getattr(s, field) for s in scenarios), ZERO) for field in
              ("sell_commission", "buy_commission", "sell_stamp_duty",
               "slippage_cost", "total_cost", "estimated_net_pnl"))
        )
    return CalculationResult(tuple(pairs), tuple(unmatched), gross_total,
                             actual_total, totals)
