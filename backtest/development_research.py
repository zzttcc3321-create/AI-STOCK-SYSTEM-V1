"""Isolated Stage 2B-D development research; never imports a live executor."""

from dataclasses import dataclass, field
from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path
import argparse
import json

from .adjustment import adjusted_bars, build_factors
from .calendar import trading_dates
from .config import STAGE1
from .data import Candle, verify_daily
from .development_data import DEV_DIVIDENDS, load_development
from .fees import Fill
from .ledger import Portfolio
from .stage2a_policy import (buyback_signal_gate, displayed_state,
                             next_open_approximate_fill, sell_candidate)


MODE = "development_research"
UNITS = 10_000  # Artificial fee-accounting scale, never a private share count.
BATCH = UNITS // 4
THRESHOLDS = tuple(Decimal(x) for x in ("0.000", "0.004", "0.010", "0.020", "0.040", "0.060", "0.090"))
SLIPPAGES = tuple(Decimal(x) for x in ("0", "0.01", "0.02"))
END = STAGE1.development_end
RULES_PATH = Path(__file__).with_name("rules_v1.json")


@dataclass(frozen=True)
class StatePoint:
    day: date
    state: str
    active: dict[str, bool]
    week_qualified: bool
    daily_b_gate: bool
    weak: bool
    high_volume: bool
    events: tuple[str, ...]


def state_points(candles: tuple[Candle, ...]) -> tuple[tuple[StatePoint, ...], tuple[str, ...]]:
    """Compute completed-close states, retaining weekly and C/D latches."""
    if not candles or candles[0].trade_date < STAGE1.development_start or any(row.trade_date > END for row in candles):
        raise ValueError("development-only state input required")
    if any(a.trade_date >= b.trade_date for a, b in zip(candles, candles[1:])):
        raise ValueError("state input must be chronologically unique")
    dividends = {d: v for d, v in DEV_DIVIDENDS.items() if d <= candles[-1].trade_date}
    factors = build_factors(candles, dividends)
    prices = [bar.close for bar in adjusted_bars(candles, factors)]
    anomalies = []
    volumes = [row.vol for row in candles]
    week_close: Decimal | None = None
    previous_week_close: Decimal | None = None
    week_qualified = False
    below_review_weeks = 0
    review_risk = False
    c_latched = False
    previous_state = "NO_ACTION"
    points = []
    for i, row in enumerate(candles):
        day = row.trade_date
        events = []
        if i < 60:
            monday = day - timedelta(days=day.weekday())
            week_sessions = trading_dates(monday, monday + timedelta(days=4))
            if week_sessions and day == week_sessions[-1]:
                previous_week_close, week_close = week_close, row.close
                if i >= 20:
                    prior_volume = sum(volumes[i-20:i]) / 20
                    if prior_volume == 0:
                        anomalies.append(f"{day}: volume denominator zero")
                    week_qualified = (prior_volume > 0 and week_close > Decimal("3.00")
                                      and row.vol / prior_volume >= Decimal("1.30"))
                if week_close < Decimal("2.30"):
                    below_review_weeks += 1
                else:
                    below_review_weeks = 0
                if ((previous_week_close is not None and previous_week_close >= Decimal("2.30")
                     and week_close < Decimal("2.30")) or below_review_weeks >= 2):
                    review_risk = True
            points.append(StatePoint(day, "WARMUP", {}, False, False, False, False, ()))
            continue
        ma20 = sum(prices[i-19:i+1]) / 20
        ma20_prev = sum(prices[i-20:i]) / 20
        ma60 = sum(prices[i-59:i+1]) / 60
        ma60_prev = sum(prices[i-60:i]) / 60
        flat = False
        if ma20_prev and ma60_prev:
            flat = (abs(ma20 / ma20_prev - 1) <= Decimal("0.001")
                    and abs(ma60 / ma60_prev - 1) <= Decimal("0.0005"))
        else:
            anomalies.append(f"{day}: MA denominator zero")
        weak = ma20 < ma20_prev and ma60 < ma60_prev
        prior_volume = sum(volumes[i-20:i]) / 20
        if prior_volume == 0:
            high_volume = False
            anomalies.append(f"{day}: volume denominator zero")
        else:
            high_volume = row.vol / prior_volume >= Decimal("1.30")

        monday = day - timedelta(days=day.weekday())
        week_sessions = trading_dates(monday, monday + timedelta(days=4))
        if week_sessions and day == week_sessions[-1]:
            previous_week_close, week_close = week_close, row.close
            week_qualified = week_close > Decimal("3.00") and high_volume
            events.append(f"week confirmed close={week_close}; B weekly={week_qualified}")
            if week_close < Decimal("2.30"):
                below_review_weeks += 1
            else:
                below_review_weeks = 0
            if ((previous_week_close is not None and previous_week_close >= Decimal("2.40")
                 and week_close < Decimal("2.40"))):
                events.append("D stop downward cross 2.40")
            if ((previous_week_close is not None and previous_week_close >= Decimal("2.30")
                 and week_close < Decimal("2.30")) or below_review_weeks >= 2):
                review_risk = True
                events.append("D invalidation review required")
        daily_b_gate = ma20 > ma60 and high_volume
        if row.close < Decimal("2.64") and (weak or high_volume):
            c_latched = True
        elif c_latched and row.close > Decimal("2.64") and not weak and not high_volume:
            c_latched = False
            events.append("C released")
        active = {
            "A": Decimal("2.64") <= row.close <= Decimal("3.00") and flat,
            "B": week_qualified and daily_b_gate,
            "C": c_latched,
            "D": review_risk or (week_close is not None and week_close < Decimal("2.40")),
        }
        state = displayed_state(active)
        if state != previous_state:
            events.append(f"state {previous_state}->{state}")
            previous_state = state
        points.append(StatePoint(day, state, active, week_qualified, daily_b_gate,
                                 weak, high_volume, tuple(events)))
    return tuple(points), tuple(anomalies)


@dataclass
class SoldLeg:
    batch: int
    day: date
    sale: Fill
    remaining: int
    buy_day: date | None = None
    buy: Fill | None = None
    realized: Decimal = Decimal(0)
    missed_dividend: Decimal = Decimal(0)
    review: bool = False


@dataclass(frozen=True)
class EquityPoint:
    day: date
    equity: Decimal
    benchmark_equity: Decimal
    drawdown: Decimal


@dataclass
class Result:
    threshold: Decimal
    slippage: Decimal
    legs: list[SoldLeg] = field(default_factory=list)
    trades: list[dict] = field(default_factory=list)
    cancellations: list[dict] = field(default_factory=list)
    equity: list[EquityPoint] = field(default_factory=list)
    states: tuple[StatePoint, ...] = ()
    anomalies: tuple[str, ...] = ()
    dividends: Decimal = Decimal(0)
    fees: Decimal = Decimal(0)
    commission: Decimal = Decimal(0)
    stamp: Decimal = Decimal(0)
    last_close: Decimal = Decimal(0)
    first_trigger_price_days: int = 0
    funnel: dict[str, int] = field(default_factory=dict)

    @property
    def sold(self) -> int:
        return sum(leg.sale.shares for leg in self.legs)

    @property
    def rebought(self) -> int:
        return sum(leg.sale.shares - leg.remaining for leg in self.legs)

    @property
    def completed(self) -> int:
        return sum(leg.remaining == 0 for leg in self.legs)

    @property
    def realized(self) -> Decimal:
        return sum((leg.realized for leg in self.legs), Decimal(0))

    @property
    def completion_rate(self) -> Decimal:
        return Decimal(self.rebought) / self.sold if self.sold else Decimal(0)

    @property
    def return_difference(self) -> Decimal:
        basis = self.equity[0].benchmark_equity
        return (self.equity[-1].equity - self.equity[-1].benchmark_equity) / basis

    @property
    def max_drawdown(self) -> Decimal:
        return min(point.drawdown for point in self.equity)


def _record_fill(result: Result, day: date, batch: int, trade: Fill) -> None:
    result.fees += trade.fees
    result.commission += trade.commission
    result.stamp += trade.stamp_duty
    result.trades.append({"day": day, "batch": batch, "side": trade.side,
                          "quantity": Decimal(trade.shares) / UNITS,
                          "open": trade.quote_price, "price": trade.executed_price,
                          "fees": trade.fees, "cash": trade.cash_change})


def run(candles: tuple[Candle, ...], threshold: Decimal, slippage: Decimal,
        rules: dict | None = None, *,
        diagnostic_states: tuple[StatePoint, ...] | None = None) -> Result:
    if UNITS % 4 or BATCH * 4 != UNITS or BATCH % 100:
        raise ValueError("research unit scale violates Q11 lot constraint")
    if threshold not in THRESHOLDS or slippage not in SLIPPAGES:
        raise ValueError("unregistered research parameter")
    if len(candles) != 485 or candles[0].trade_date != STAGE1.development_start or candles[-1].trade_date != END:
        raise ValueError("development-only 485-session input required")
    if any(row.trade_date > END for row in candles):
        raise ValueError("post-boundary candle forbidden")
    dividends = DEV_DIVIDENDS
    if not verify_daily(candles, dividends).valid:
        raise ValueError("unverified development candles")
    rules = rules or json.loads(RULES_PATH.read_text(encoding="utf-8"))
    if rules["ready_for_stage2b"] is not False or rules["net_gate"]["minimum_net_threshold"] is not None:
        raise ValueError("formal gate or threshold drift")
    if rules["orders"]["sell_triggers"][2:] != [None, None]:
        raise ValueError("research batches three/four must be disabled")
    states, anomalies = state_points(candles)
    if diagnostic_states is not None:
        if (len(diagnostic_states) != len(candles)
                or any(point.day != row.trade_date for point, row in zip(diagnostic_states, candles))):
            raise ValueError("diagnostic state dates must match development candles")
        if any(any(point.active.get(key) != original.active.get(key) for key in ("A", "B", "C"))
               or (point.state != ("WARMUP" if original.state == "WARMUP"
                                   else displayed_state(point.active)))
               for point, original in zip(diagnostic_states, states)):
            raise ValueError("diagnostic override may only change D")
        states = diagnostic_states
    portfolio = Portfolio(Decimal(0), UNITS)
    result = Result(threshold, slippage, states=states, anomalies=anomalies)
    result.funnel = dict.fromkeys(("candidate_conditions", "state_allowed",
                                   "orders", "fills", "net_profitable_completed"), 0)
    pending: dict | None = None
    filled_batches = set()
    benchmark_dividends = Decimal(0)
    peak = Decimal(0)
    for row, point in zip(candles, states):
        day = row.trade_date
        result.last_close = row.close
        if point.active.get("A") and row.close >= Decimal("2.95"):
            result.first_trigger_price_days += 1
        if day in dividends:
            amount = portfolio.credit_cash_dividend(day, dividends[day])
            result.dividends += amount
            benchmark_dividends += UNITS * dividends[day]
            for leg in result.legs:
                if leg.remaining:
                    leg.missed_dividend += leg.remaining * dividends[day]
        if pending is not None:
            side, batch = pending["side"], pending["batch"]
            signal_state: StatePoint = pending["state"]
            if (side == "sell" and any(signal_state.active.get(s) for s in ("B", "C", "D"))
                    or side == "buy" and any(signal_state.active.get(s) for s in ("C", "D"))):
                reason = "pause"
            else:
                band = pending["band"]
                shares = pending["shares"]
                projected = portfolio.unreplenished_shares + shares
                if side == "sell" and projected > UNITS // 2:
                    reason = "unreplenished cap"
                elif side == "buy" and shares > pending["leg"].remaining:
                    reason = "FIFO remaining quantity"
                elif next_open_approximate_fill(side, shares, row.open, band, slippage) is None:
                    reason = "open outside band"
                else:
                    simulated = next_open_approximate_fill(side, shares, row.open, band, slippage)
                    if side == "buy" and portfolio.cash + simulated.cash_change < 0:
                        reason = "insufficient cash"
                    else:
                        trade = (portfolio.sell(day, shares, row.open, slippage) if side == "sell"
                                 else portfolio.buy(day, shares, row.open, slippage))
                        _record_fill(result, day, batch, trade)
                        if side == "sell":
                            result.legs.append(SoldLeg(batch, day, trade, shares))
                            filled_batches.add(batch)
                        else:
                            leg = pending["leg"]
                            leg.remaining -= shares
                            leg.buy_day, leg.buy = day, trade
                            leg.realized += (leg.sale.cash_change * Decimal(shares) / leg.sale.shares
                                             + trade.cash_change)
                        reason = ""
            if reason:
                result.cancellations.append({"day": day, "side": side, "batch": batch, "reason": reason})
            pending = None
        if point.active.get("D") and any("invalidation review" in event for event in point.events):
            for leg in result.legs:
                if leg.remaining:
                    leg.review = True
        equity = portfolio.cash + portfolio.shares * row.close
        benchmark = UNITS * row.close + benchmark_dividends
        peak = max(peak, equity)
        result.equity.append(EquityPoint(day, equity, benchmark, equity / peak - 1))
        if point.state == "WARMUP" or day == END:
            continue
        if point.active.get("A") and row.close >= Decimal("2.95"):
            result.funnel["candidate_conditions"] += 1
            if not any(point.active.get(s) for s in ("B", "C", "D")):
                result.funnel["state_allowed"] += 1
        batch = sell_candidate(rules, row.close, point.active, filled_batches)
        if batch is not None:
            result.funnel["orders"] += 1
            pending = {"side": "sell", "batch": batch, "shares": BATCH,
                       "band": rules["orders"]["sell_bands_raw_rmb_inclusive"][batch-1], "state": point}
            continue
        if point.active.get("C") or point.active.get("D") or not result.legs:
            continue
        leg = next((item for item in result.legs if item.remaining), None)
        if leg is None:
            continue
        band = next((b for b in rules["orders"]["buyback_bands_raw_rmb_inclusive"]
                     if Decimal(b[0]) <= row.close <= Decimal(b[1])), None)
        if band is None:
            continue
        shares = leg.remaining
        allocated_cash = leg.sale.cash_change * Decimal(shares) / leg.sale.shares
        passed, _ = buyback_signal_gate(row.close, allocated_cash, shares, threshold, slippage)
        if passed:
            pending = {"side": "buy", "batch": leg.batch, "shares": shares,
                       "band": band, "state": point, "leg": leg}
    assert all(t["day"] <= END for t in result.trades)
    assert all(p.day <= END for p in result.equity)
    assert all(p.day <= END for p in result.states)
    assert result.sold == result.rebought + sum(leg.remaining for leg in result.legs)
    assert portfolio.unreplenished_shares == sum(leg.remaining for leg in result.legs)
    assert portfolio.shares >= 0 and portfolio.cash >= 0
    result.funnel["fills"] = sum(trade["side"] == "sell" for trade in result.trades)
    result.funnel["net_profitable_completed"] = sum(
        leg.remaining == 0 and leg.realized > 0 for leg in result.legs)
    assert all(a >= b for a, b in zip(result.funnel.values(), list(result.funnel.values())[1:]))
    return result


def choose(results: list[Result]) -> Decimal | None:
    by_threshold = {t: {r.slippage: r for r in results if r.threshold == t} for t in THRESHOLDS}
    eligible = []
    for threshold, scenarios in by_threshold.items():
        if set(scenarios) != set(SLIPPAGES):
            raise ValueError("incomplete comparison grid")
        cases = list(scenarios.values())
        if all(r.completed >= 1 and r.realized >= 0 and r.completion_rate >= Decimal("0.5") for r in cases):
            median = sorted(r.realized / UNITS for r in cases)[1]
            eligible.append((median, min(r.completion_rate for r in cases), -threshold, threshold))
    return max(eligible)[3] if eligible else None


def main() -> None:
    parser = argparse.ArgumentParser(description="Stage 2B-D development_research only")
    parser.add_argument("--mode", choices=[MODE], default=MODE)
    parser.add_argument("--source", type=Path, default=Path("backtest/local_data/jlgs_3y_raw.json"))
    parser.add_argument("--report", type=Path, default=Path("docs/backtest/STAGE2B_DEV_REPORT.md"))
    args = parser.parse_args()
    candles = load_development(args.source)
    results = [run(candles, t, s) for t in THRESHOLDS for s in SLIPPAGES]
    from .development_report import render_report
    args.report.write_text(render_report(results, choose(results)), encoding="utf-8")
    print(f"{MODE}: {len(candles)} sessions, {len(results)} scenarios, report={args.report}")


if __name__ == "__main__":
    main()
