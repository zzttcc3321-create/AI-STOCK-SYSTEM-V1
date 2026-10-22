"""Isolated V1.1 development research. No live execution interface."""

import argparse
from dataclasses import dataclass, field, replace
from datetime import date, datetime, time
from decimal import Decimal
import hashlib
import json
from pathlib import Path

from .config import STAGE1
from .adjustment import adjusted_bars, build_factors
from .data import Candle, verify_daily
from .development_data import DEV_DIVIDENDS, load_development
from .development_research import state_points
from .fees import Fill, fill
from .ledger import Portfolio
from .stage2a_policy import displayed_state
from .stage2b_r1_diagnostics import d1_states
from .weekly import SHANGHAI, confirmed_weekly_bars
from .v11_policy import normal_buyback_candidate, risk_exit_candidate, sell_candidate_v11
from .validate_rules_v1_1 import INPUT_HASH, ROOT, validate

UNITS = 10_000
BATCH = 2_500
LOT = 100
END = STAGE1.development_end


@dataclass
class Leg:
    batch: int
    sold_index: int
    sold_day: date
    sale: Fill
    remaining: int
    regular_shares: int = 0
    risk_shares: int = 0
    regular_net: Decimal = Decimal(0)
    risk_net: Decimal = Decimal(0)
    risk_pending: bool = False
    review: bool = False
    missed_dividend: Decimal = Decimal(0)

    def sale_cash(self, shares: int) -> Decimal:
        return self.sale.cash_change * Decimal(shares) / self.sale.shares


@dataclass(frozen=True)
class DayMark:
    day: date
    equity: Decimal
    benchmark: Decimal
    drawdown: Decimal
    benchmark_drawdown: Decimal
    open_shares: int
    open_fraction: Decimal
    repurchase_obligation: Decimal
    unrealized_net: Decimal
    max_open_days: int


@dataclass
class Result:
    label: str
    d_state: str
    breakout: Decimal
    wait_sessions: int
    threshold: Decimal
    slippage: Decimal
    funnel: dict[str, int] = field(default_factory=dict)
    legs: list[Leg] = field(default_factory=list)
    marks: list[DayMark] = field(default_factory=list)
    trades: list[dict] = field(default_factory=list)
    events: list[dict] = field(default_factory=list)
    state_events: list[dict] = field(default_factory=list)
    anomalies: tuple[str, ...] = ()
    fees: Decimal = Decimal(0)
    dividends: Decimal = Decimal(0)
    benchmark_dividends: Decimal = Decimal(0)
    max_open_shares: int = 0

    @property
    def sold(self):
        return sum(x.sale.shares for x in self.legs)

    @property
    def regular_shares(self):
        return sum(x.regular_shares for x in self.legs)

    @property
    def risk_shares(self):
        return sum(x.risk_shares for x in self.legs)

    @property
    def open_shares(self):
        return sum(x.remaining for x in self.legs)

    @property
    def regular_net(self):
        return sum((x.regular_net for x in self.legs), Decimal(0))

    @property
    def risk_net(self):
        return sum((x.risk_net for x in self.legs), Decimal(0))

    @property
    def realized(self):
        return self.regular_net + self.risk_net

    @property
    def regular_rate(self):
        return Decimal(self.regular_shares) / self.sold if self.sold else Decimal(0)

    @property
    def excess(self):
        return ((self.marks[-1].equity - self.marks[-1].benchmark)
                / self.marks[0].benchmark)

    @property
    def max_drawdown(self):
        return min(x.drawdown for x in self.marks)

    @property
    def benchmark_drawdown(self):
        return min(x.benchmark_drawdown for x in self.marks)

    @property
    def regular_completed_legs(self):
        return sum(x.remaining == 0 and x.risk_shares == 0 and x.regular_shares == x.sale.shares
                   and x.regular_net / x.regular_shares >= self.threshold
                   for x in self.legs if x.regular_shares)

    def audit(self):
        if self.sold != self.regular_shares + self.risk_shares + self.open_shares:
            raise AssertionError("FIFO share reconciliation")
        if self.max_open_shares > UNITS // 2 or any(x.open_shares > UNITS // 2 for x in self.marks):
            raise AssertionError("50 percent open cap")
        if any(x.day > END for x in self.marks + []):
            raise AssertionError("date boundary")
        if any(e["day"] > END for e in self.events + self.trades + self.state_events):
            raise AssertionError("event boundary")
        if any(x.remaining < 0 or x.regular_shares + x.risk_shares + x.remaining != x.sale.shares
               for x in self.legs):
            raise AssertionError("leg shares")
        if any(x.risk_pending and x.remaining == 0 for x in self.legs):
            raise AssertionError("stale risk pending")
        if self.funnel["orders"] != sum(t["side"] == "sell" for t in self.trades) + sum(
                e["kind"] == "CANCEL_SELL" for e in self.events):
            raise AssertionError("order reconciliation")
        if self.funnel["fills"] != len(self.legs) or self.funnel["regular_completed"] != self.regular_completed_legs:
            raise AssertionError("funnel reconciliation")


def _states(candles, d_state):
    if d_state == "D0":
        source, anomalies = state_points(candles)
    elif d_state in ("D1-N1", "D1-N2"):
        source, _, _ = d1_states(candles, int(d_state[-1]))
        _, anomalies = state_points(candles)
    else:
        raise ValueError("unregistered D scenario")
    result = []
    previous = "NO_ACTION"
    for row, point in zip(candles, source):
        if point.state == "WARMUP":
            result.append(point)
            continue
        active = {**point.active, "A": Decimal("2.64") <= row.close <= Decimal("3.00")}
        state = displayed_state(active)
        events = tuple(e for e in point.events if not e.startswith("state "))
        if state != previous:
            events += (f"state {previous}->{state}",)
            previous = state
        result.append(replace(point, active=active, state=state, events=events))
    return tuple(result), anomalies


def _event(result, day, kind, reason, batch=None, shares=0):
    result.events.append({"day": day, "kind": kind, "reason": reason,
                          "batch": batch, "shares": shares})


def _fill_record(result, day, leg, trade, category):
    result.fees += trade.fees
    result.trades.append({"day": day, "batch": leg.batch, "side": trade.side,
                          "category": category, "shares": trade.shares,
                          "quote": trade.quote_price, "executed": trade.executed_price,
                          "fees": trade.fees, "cash_change": trade.cash_change})


def _affordable(cash, shares, opening, slippage):
    """Largest legal A-share lot affordable including the rounded commission."""
    for quantity in range(shares // LOT * LOT, 0, -LOT):
        if cash + fill("buy", quantity, opening, slippage).cash_change >= 0:
            return quantity
    return 0


def run_v11(candles: tuple[Candle, ...], label="main", d_state="D0",
            breakout=Decimal("3.00"), wait_sessions=5,
            threshold=Decimal("0.010"), slippage=Decimal("0.01")) -> Result:
    if len(candles) != 485 or candles[0].trade_date != STAGE1.development_start or candles[-1].trade_date != END:
        raise ValueError("isolated complete 485-session development input required")
    if not verify_daily(candles, DEV_DIVIDENDS).valid:
        raise ValueError("invalid development candle history")
    if breakout not in (Decimal("3.00"), Decimal("2.95")) or wait_sessions not in (5, 10):
        raise ValueError("unregistered breakout or wait")
    if threshold not in (Decimal("0.010"), Decimal("0.000"), Decimal("0.020")) or slippage not in tuple(map(Decimal, ("0", "0.01", "0.02"))):
        raise ValueError("unregistered threshold or slippage")
    if (sum((breakout != Decimal("3.00"), wait_sessions != 5,
             threshold != Decimal("0.010"))) > 1 or d_state != "D0" and
            (breakout != Decimal("3.00") or wait_sessions != 5 or threshold != Decimal("0.010"))):
        raise ValueError("multi-factor or D1 combination forbidden")
    states, anomalies = _states(candles, d_state)
    result = Result(label, d_state, breakout, wait_sessions, threshold, slippage,
                    funnel=dict.fromkeys(("candidate_conditions", "state_allowed", "orders",
                                        "fills", "regular_completed"), 0), anomalies=anomalies)
    portfolio = Portfolio(Decimal(0), UNITS)
    filled_batches = set()
    pending = None
    peak = Decimal(0)
    benchmark_peak = Decimal(0)
    for index, (row, point) in enumerate(zip(candles, states)):
        day = row.trade_date
        if day in DEV_DIVIDENDS:
            amount = portfolio.credit_cash_dividend(day, DEV_DIVIDENDS[day])
            result.dividends += amount
            result.benchmark_dividends += UNITS * DEV_DIVIDENDS[day]
            for leg in result.legs:
                if leg.remaining:
                    leg.missed_dividend += leg.remaining * DEV_DIVIDENDS[day]
        if pending:
            side, leg = pending["side"], pending.get("leg")
            known = pending["state"]
            reason = None
            if side == "sell":
                batch = pending["batch"]
                if any(known.active.get(x) for x in ("B", "C", "D")):
                    reason = "known B/C/D pause"
                elif not Decimal("2.64") <= row.open <= Decimal("3.00"):
                    reason = "open outside sell band"
                elif portfolio.unreplenished_shares + BATCH > UNITS // 2:
                    reason = "50 percent open cap"
                else:
                    sale = portfolio.sell(day, BATCH, row.open, slippage)
                    leg = Leg(batch, index, day, sale, BATCH)
                    result.legs.append(leg)
                    filled_batches.add(batch)
                    _fill_record(result, day, leg, sale, "SELL")
            elif known.active.get("C") or known.active.get("D"):
                reason = "known C/D pause"
                _event(result, day, "RISK_FAILURE" if side == "risk" else "CANCEL_REGULAR",
                       reason, leg.batch, leg.remaining)
            elif leg.remaining <= 0:
                reason = "FIFO leg already closed"
            elif row.open <= 0:
                reason = "invalid open data"
            elif side == "regular" and not pending["band"][0] <= row.open <= pending["band"][1]:
                reason = "open outside regular band"
            else:
                wanted = leg.remaining
                quantity = _affordable(portfolio.cash, wanted, row.open, slippage) if side == "risk" else wanted
                if side == "regular" and (quantity % LOT or portfolio.cash + fill("buy", quantity, row.open, slippage).cash_change < 0):
                    reason = "insufficient cash for regular buy"
                elif quantity == 0:
                    reason = "no legal affordable quantity"
                else:
                    trade = portfolio.buy(day, quantity, row.open, slippage)
                    allocation = leg.sale_cash(quantity)
                    pnl = allocation + trade.cash_change
                    leg.remaining -= quantity
                    if side == "risk":
                        leg.risk_shares += quantity
                        leg.risk_net += pnl
                    else:
                        leg.regular_shares += quantity
                        leg.regular_net += pnl
                    if not leg.remaining:
                        leg.risk_pending = False
                    _fill_record(result, day, leg, trade, "RISK_REBOUGHT" if side == "risk" else "REBOUGHT")
                    if quantity < wanted:
                        _event(result, day, "RISK_PARTIAL", "cash-limited legal lot", leg.batch, wanted - quantity)
                        _event(result, day, "RISK_FAILURE", "insufficient cash for full risk buy", leg.batch, wanted - quantity)
            if reason and side == "sell":
                _event(result, day, "CANCEL_SELL", reason, pending["batch"], BATCH)
            elif reason and side != "sell" and reason != "known C/D pause":
                _event(result, day, "RISK_FAILURE" if side == "risk" else "CANCEL_REGULAR",
                       reason, leg.batch, leg.remaining)
            pending = None
        for event in point.events:
            result.state_events.append({"day": day, "event": event})
        if point.active.get("D") and any("invalidation review" in e for e in point.events):
            for leg in result.legs:
                if leg.remaining:
                    leg.review = True
        equity = portfolio.cash + portfolio.shares * row.close
        benchmark = UNITS * row.close + result.benchmark_dividends
        peak = max(peak, equity)
        benchmark_peak = max(benchmark_peak, benchmark)
        open_shares = sum(x.remaining for x in result.legs)
        obligation = sum((-fill("buy", x.remaining, row.close, slippage).cash_change
                          for x in result.legs if x.remaining), Decimal(0))
        unrealized = sum((x.sale_cash(x.remaining) +
                          fill("buy", x.remaining, row.close, slippage).cash_change
                          for x in result.legs if x.remaining), Decimal(0))
        longest = max((index - x.sold_index for x in result.legs if x.remaining), default=0)
        result.marks.append(DayMark(day, equity, benchmark, equity / peak - 1,
                                    benchmark / benchmark_peak - 1, open_shares,
                                    Decimal(open_shares) / UNITS, obligation, unrealized, longest))
        result.max_open_shares = max(result.max_open_shares, open_shares)
        if point.state == "WARMUP" or day == END:
            continue
        if Decimal("2.64") <= row.close <= Decimal("3.00") and row.close >= Decimal("2.95"):
            result.funnel["candidate_conditions"] += 1
            if not any(point.active.get(x) for x in ("B", "C", "D")):
                result.funnel["state_allowed"] += 1
        for open_leg in (x for x in result.legs if x.remaining):
            latched, _ = risk_exit_candidate(row.close, point.active,
                point.daily_b_gate, point.week_qualified, index - open_leg.sold_index,
                wait_sessions, open_leg.risk_pending)
            if latched and not open_leg.risk_pending:
                _event(result, day, "RISK_LATCH", "B/breakout/wait close trigger", open_leg.batch, open_leg.remaining)
            open_leg.risk_pending = latched
            if (point.active.get("C") or point.active.get("D")) and latched:
                _event(result, day, "RISK_FAILURE", "C/D blocked pending risk exit", open_leg.batch, open_leg.remaining)
        if point.active.get("C") or point.active.get("D"):
            continue
        leg = next((x for x in result.legs if x.remaining), None)
        if leg:
            _, executable = risk_exit_candidate(row.close, point.active,
                point.daily_b_gate, point.week_qualified, index - leg.sold_index,
                wait_sessions, leg.risk_pending)
            if executable:
                pending = {"side": "risk", "leg": leg, "state": point}
                continue
            band = normal_buyback_candidate(row.close, point.active, leg.remaining,
                                            leg.sale_cash(leg.remaining), threshold, slippage)
            if band:
                pending = {"side": "regular", "leg": leg, "band": band, "state": point}
                continue
        batch = sell_candidate_v11(row.close, point.active, filled_batches,
                                    point.daily_b_gate, breakout)
        if batch is not None and portfolio.unreplenished_shares + BATCH <= UNITS // 2:
            result.funnel["orders"] += 1
            pending = {"side": "sell", "batch": batch, "state": point}
    result.funnel["fills"] = len(result.legs)
    result.funnel["regular_completed"] = result.regular_completed_legs
    if portfolio.unreplenished_shares != result.open_shares or portfolio.cash < 0 or portfolio.shares < 0:
        raise AssertionError("portfolio reconciliation")
    result.audit()
    return result


def run_matrix(candles):
    rows = []
    for label, changes in (("main", {}), ("breakout_295", {"breakout": Decimal("2.95")}),
                           ("wait_10", {"wait_sessions": 10}),
                           ("net_000", {"threshold": Decimal("0.000")}),
                           ("net_020", {"threshold": Decimal("0.020")})):
        for slip in map(Decimal, ("0", "0.01", "0.02")):
            rows.append(run_v11(candles, label=label, slippage=slip, **changes))
    for d_state in ("D1-N1", "D1-N2"):
        for slip in map(Decimal, ("0", "0.01", "0.02")):
            rows.append(run_v11(candles, label="main", d_state=d_state, slippage=slip))
    return rows


def main():
    parser = argparse.ArgumentParser(description="V1.1 development-only offline validation")
    parser.add_argument("--source", type=Path, default=ROOT / "backtest/local_data/jlgs_dev_only.json")
    parser.add_argument("--report", type=Path, default=ROOT / "docs/backtest/V1_1_B_DEV_REPORT.md")
    args = parser.parse_args()
    rules, blobs = validate()
    source = args.source.resolve()
    if source != (ROOT / rules["source"]).resolve():
        raise ValueError("only the preregistered isolated development file may be read")
    digest = hashlib.sha256(source.read_bytes()).hexdigest()
    if digest != INPUT_HASH:
        raise ValueError("development-only source hash drift")
    candles = load_development(source)
    if len(candles) != rules["sessions"] or str(candles[0].trade_date) != rules["first_date"] or str(candles[-1].trade_date) != rules["last_date"]:
        raise ValueError("isolated source shape drift")
    adjusted = adjusted_bars(candles, build_factors(candles, DEV_DIVIDENDS))
    weeks = confirmed_weekly_bars(adjusted, datetime.combine(END, time(15, 1), SHANGHAI))
    if not weeks or any(bar.confirmed_at.date() > END for bar in weeks):
        raise ValueError("confirmed-week availability drift")
    results = run_matrix(candles)
    from .v11_report import render_report
    report = render_report(results, candles, rules, blobs, digest)
    args.report.write_text(report, encoding="utf-8")
    print(f"V1.1: {len(candles)} sessions, {len(weeks)} confirmed weeks, {len(results)} runs, SHA256={hashlib.sha256(report.encode()).hexdigest()}, report={args.report}")


if __name__ == "__main__":
    main()
