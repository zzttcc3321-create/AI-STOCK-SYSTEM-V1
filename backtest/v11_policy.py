"""Pure close-signal gates for the preregistered offline V1.1 candidate."""

from decimal import Decimal

from .fees import fill


def sell_candidate_v11(close, active, filled_batches, daily_b_gate, breakout=Decimal("3.00")):
    close = Decimal(close)
    if any(active.get(name) for name in ("B", "C", "D")):
        return None
    if not Decimal("2.64") <= close <= Decimal("3.00"):
        return None
    if close >= Decimal(breakout) and daily_b_gate:
        return None
    filled = set(filled_batches)
    if 1 not in filled and close >= Decimal("2.95"):
        return 1
    if 1 in filled and 2 not in filled and close >= Decimal("3.00"):
        return 2
    return None


def normal_buyback_candidate(close, active, remaining, allocated_sale_cash, threshold, slippage):
    if remaining <= 0 or active.get("C") or active.get("D"):
        return None
    close = Decimal(close)
    band = next((band for band in ((Decimal("2.76"), Decimal("2.82")),
                                   (Decimal("2.62"), Decimal("2.68")))
                 if band[0] <= close <= band[1]), None)
    if band is None:
        return None
    expected = (Decimal(allocated_sale_cash) + fill("buy", remaining, close, Decimal(slippage)).cash_change) / remaining
    return band if expected >= Decimal(threshold) else None


def risk_exit_candidate(close, active, daily_b_gate, week_qualified, full_days,
                        wait_sessions, pending=False):
    """Return (latched, executable); existing pending survives transient signals."""
    close = Decimal(close)
    latched = bool(pending or (week_qualified and daily_b_gate)
                   or (close > Decimal("3.00") and daily_b_gate)
                   or full_days >= wait_sessions)
    return latched, latched and not (active.get("C") or active.get("D"))
