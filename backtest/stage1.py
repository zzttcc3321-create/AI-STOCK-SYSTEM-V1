"""Reproducible Stage 1 summary. Reads the external source; writes no source data."""

import argparse
from dataclasses import asdict
from decimal import Decimal, ROUND_HALF_UP
import hashlib
import json
from pathlib import Path

from .adjustment import VERSION, build_factors
from .benchmark import buy_hold
from .data import load_daily, verify_daily
from .dividends import DIVIDENDS
from .fees import round_trip


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", required=True, help="external Tushare JSON; never copied")
    parser.add_argument("--factor-output", help="derived factor table only; no holdings or raw prices")
    parser.add_argument("--shares", type=int, default=1, help="runtime share count; omit for per-share public report")
    parser.add_argument("--reference-cost", type=Decimal, help="optional private cost basis; never write its output to Git")
    args = parser.parse_args()
    path = Path(args.source)
    candles = load_daily(path)
    verified = verify_daily(candles, DIVIDENDS)
    if not verified.valid:
        raise SystemExit("verification failed; benchmark and factors not generated")
    factors = build_factors(candles, DIVIDENDS)
    benchmark = buy_hold(candles, DIVIDENDS, args.shares, args.reference_cost)
    candle_digest = hashlib.sha256(json.dumps(
        [[row.ts_code, row.trade_date.isoformat(), *[str(getattr(row, key)) for key in
          ("open", "high", "low", "close", "pre_close", "change", "pct_chg", "vol", "amount")]]
         for row in candles], separators=(",", ":")).encode("utf-8")).hexdigest()
    if args.factor_output:
        output = {
            "version": VERSION,
            "candles_sha256": candle_digest,
            "method": "cumulative prior_close/(prior_close-announced_cash_dividend), as of ex-date",
            "rows": [{"trade_date": row.trade_date.isoformat(),
                      "factor": str(row.factor.quantize(Decimal("0.000000000001"), rounding=ROUND_HALF_UP)),
                      "cash_dividend": str(row.cash_dividend)} for row in factors],
        }
        output_path = Path(args.factor_output)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(json.dumps(output, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    sale, purchase, net = round_trip(Decimal("3.00"), Decimal("2.80"), 2000)
    summary = {
        "candles_sha256": candle_digest,
        "first_date": verified.first_date.isoformat(), "last_date": verified.last_date.isoformat(),
        "rows": verified.row_count, "unique_dates": verified.unique_date_count,
        "expected_sessions": verified.expected_session_count,
        "closed_weekdays": [day.isoformat() for day in verified.closed_weekdays],
        "missing_sessions": [day.isoformat() for day in verified.missing_sessions],
        "unexpected_dates": [day.isoformat() for day in verified.unexpected_session_dates],
        "duplicate_dates": [day.isoformat() for day in verified.duplicate_dates],
        "ohlc_errors": [day.isoformat() for day in verified.ohlc_errors],
        "preclose_gaps": [asdict(gap) for gap in verified.preclose_gaps],
        "old_case": {"fees": str(sale.fees + purchase.fees), "net": str(net)},
        "benchmark": {
            "entry_price": str(benchmark.entry_price), "last_close": str(benchmark.points[-1].close),
            "dividend_cash": str(benchmark.total_dividend_cash),
            "window_pnl": str(benchmark.window_pnl), "window_return": str(benchmark.window_return),
            "max_drawdown": str(benchmark.max_drawdown),
            "peak_date": benchmark.peak_date.isoformat(), "trough_date": benchmark.trough_date.isoformat(),
        },
    }
    if args.reference_cost is not None:
        summary["private_reference"] = {
            "shares": args.shares, "cost": str(args.reference_cost),
            "pnl": str(benchmark.reference_cost_pnl),
            "return": str(benchmark.reference_cost_return),
        }
    print(json.dumps(summary, default=str, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
