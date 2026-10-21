"""Cash and share ledger with T+1, daily direction lock and open sell quantity."""

from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from .fees import Fill, fill, money


@dataclass
class Lot:
    acquired: date
    shares: int


class Portfolio:
    def __init__(self, cash: Decimal, initial_shares: int = 0,
                 acquired: date = date.min):
        self.cash = Decimal(cash)
        if self.cash < 0 or initial_shares < 0 or not isinstance(initial_shares, int):
            raise ValueError("negative cash or shares")
        self.lots = [Lot(acquired, initial_shares)] if initial_shares else []
        self.unreplenished_shares = 0
        self._last_date: date | None = None
        self._side_today: str | None = None
        self._dividend_dates: set[date] = set()

    @property
    def shares(self) -> int:
        return sum(lot.shares for lot in self.lots)

    def _check_day(self, trade_date: date, side: str) -> None:
        if self._last_date is not None and trade_date < self._last_date:
            raise ValueError("trade dates must be chronological")
        if trade_date == self._last_date and self._side_today not in (None, side):
            raise ValueError("same-day reversal forbidden")

    def _record_day(self, trade_date: date, side: str) -> None:
        self._last_date, self._side_today = trade_date, side

    def credit_cash_dividend(self, ex_date: date, cash_per_share: Decimal) -> Decimal:
        """Credit prior-close shares before any ex-date trade, once per event."""
        amount = Decimal(cash_per_share)
        if amount <= 0 or ex_date in self._dividend_dates:
            raise ValueError("invalid or duplicate cash dividend")
        if self._last_date is not None and ex_date <= self._last_date:
            raise ValueError("credit dividend before ex-date trades, in date order")
        credit = money(self.shares * amount)
        self.cash += credit
        self._dividend_dates.add(ex_date)
        self._last_date, self._side_today = ex_date, None
        return credit

    def buy(self, trade_date: date, shares: int, quote_price: Decimal,
            slippage: Decimal = Decimal(0)) -> Fill:
        self._check_day(trade_date, "buy")
        trade = fill("buy", shares, quote_price, slippage)
        if self.cash + trade.cash_change < 0:
            raise ValueError("insufficient cash")
        self.cash += trade.cash_change
        self.lots.append(Lot(trade_date, shares))
        self.unreplenished_shares = max(0, self.unreplenished_shares - shares)
        self._record_day(trade_date, "buy")
        return trade

    def sell(self, trade_date: date, shares: int, quote_price: Decimal,
             slippage: Decimal = Decimal(0)) -> Fill:
        self._check_day(trade_date, "sell")
        trade = fill("sell", shares, quote_price, slippage)
        if sum(lot.shares for lot in self.lots if lot.acquired < trade_date) < shares:
            raise ValueError("T+1 or insufficient eligible shares")
        if self.cash + trade.cash_change < 0:
            raise ValueError("sale fees exceed available cash and proceeds")
        remaining = shares
        for lot in self.lots:
            if lot.acquired < trade_date:
                taken = min(remaining, lot.shares)
                lot.shares -= taken
                remaining -= taken
                if not remaining:
                    break
        self.lots = [lot for lot in self.lots if lot.shares]
        self.cash += trade.cash_change
        self.unreplenished_shares += shares
        self._record_day(trade_date, "sell")
        return trade
