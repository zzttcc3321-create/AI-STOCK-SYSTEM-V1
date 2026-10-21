"""A-share fees and explicit per-side slippage, in RMB."""

from dataclasses import dataclass
from decimal import Decimal, ROUND_HALF_UP
from typing import Literal

from .config import STAGE1, Stage1Config


Side = Literal["buy", "sell"]
CENT = Decimal("0.01")


def money(value: Decimal) -> Decimal:
    return value.quantize(CENT, rounding=ROUND_HALF_UP)


@dataclass(frozen=True)
class Fill:
    side: Side
    shares: int
    quote_price: Decimal
    executed_price: Decimal
    gross: Decimal
    commission: Decimal
    stamp_duty: Decimal
    cash_change: Decimal

    @property
    def fees(self) -> Decimal:
        return self.commission + self.stamp_duty


def fill(side: Side, shares: int, quote_price: Decimal, slippage: Decimal = Decimal(0),
         config: Stage1Config = STAGE1) -> Fill:
    if side not in ("buy", "sell") or isinstance(shares, bool) or not isinstance(shares, int) or shares <= 0:
        raise ValueError("invalid trade")
    price = Decimal(quote_price)
    slip = Decimal(slippage)
    if price <= 0 or slip < 0:
        raise ValueError("invalid price or slippage")
    executed = price + slip if side == "buy" else price - slip
    if executed <= 0:
        raise ValueError("non-positive executed price")
    gross = money(executed * shares)
    commission = max(config.minimum_commission, money(gross * config.commission_rate))
    stamp = money(gross * config.sell_stamp_rate) if side == "sell" else Decimal("0.00")
    cash_change = gross - commission - stamp if side == "sell" else -gross - commission
    return Fill(side, shares, price, executed, gross, commission, stamp, cash_change)


def round_trip(sell_quote: Decimal, buy_quote: Decimal, shares: int,
               slippage: Decimal = Decimal(0), config: Stage1Config = STAGE1) -> tuple[Fill, Fill, Decimal]:
    sale = fill("sell", shares, sell_quote, slippage, config)
    purchase = fill("buy", shares, buy_quote, slippage, config)
    return sale, purchase, sale.cash_change + purchase.cash_change
