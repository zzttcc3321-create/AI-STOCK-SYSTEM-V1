"""Small, candle-free Stage 2A order semantics for static and synthetic checks.

These helpers do not run a strategy or select the unresolved net threshold.
"""

from decimal import Decimal

from .fees import fill


def displayed_state(active):
    """Return the highest active state; lower-state pauses still apply."""
    return next((name for name in ("D", "C", "B", "A") if active.get(name)), "NO_ACTION")


def sell_candidate(rules, close, active, filled_batches):
    """Select at most one eligible batch at signal close, in strict order."""
    if displayed_state(active) != "A" or any(active.get(x) for x in ("B", "C", "D")):
        return None
    close = Decimal(close)
    filled = set(filled_batches)
    for batch, trigger in enumerate(rules["orders"]["sell_triggers"], start=1):
        if trigger is None:
            continue
        if batch == 2 and 1 not in filled:
            return None
        if batch in filled:
            continue
        lower, upper = map(Decimal, rules["orders"]["sell_bands_raw_rmb_inclusive"][batch - 1])
        if lower <= close <= upper and close >= Decimal(trigger["min_signal_close_raw_rmb"]):
            return batch
        return None
    return None


def cancel_pending_sells_on_pause(pending_batches, active):
    """A newly active B, C or D pause cancels unfilled sell candidates."""
    return [] if any(active.get(x) for x in ("B", "C", "D")) else list(pending_batches)


def buyback_signal_gate(signal_close, allocated_realized_sell_cash, shares,
                        threshold_per_share, slippage):
    """Evaluate the net gate using information available at signal close."""
    if shares <= 0:
        raise ValueError("shares must be positive")
    buy = fill("buy", shares, Decimal(signal_close), Decimal(slippage))
    expected_per_share = (Decimal(allocated_realized_sell_cash) + buy.cash_change) / shares
    return expected_per_share >= Decimal(threshold_per_share), expected_per_share


def next_open_approximate_fill(side, shares, open_price, band, slippage,
                               signal_gate_passed=True):
    """Use the next open only for the band test and simulated fill price."""
    opening = Decimal(open_price)
    lower, upper = map(Decimal, band)
    if not signal_gate_passed or not lower <= opening <= upper:
        return None
    return fill(side, shares, opening, Decimal(slippage))
