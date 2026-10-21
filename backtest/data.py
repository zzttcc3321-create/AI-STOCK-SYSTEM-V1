"""Load only Tushare candles, never the source JSON's other top-level fields."""

from collections import Counter
from dataclasses import dataclass
from datetime import date
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
import json
from pathlib import Path

from .calendar import closed_weekdays, trading_dates


REQUIRED = ("ts_code", "trade_date", "open", "high", "low", "close", "pre_close", "change", "pct_chg", "vol", "amount")
CENT = Decimal("0.01")


def dec(value: object) -> Decimal:
    try:
        number = Decimal(str(value))
    except (InvalidOperation, ValueError) as exc:
        raise ValueError("non-numeric candle field") from exc
    if not number.is_finite():
        raise ValueError("non-finite candle field")
    return number


@dataclass(frozen=True)
class Candle:
    ts_code: str
    trade_date: date
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    pre_close: Decimal
    change: Decimal
    pct_chg: Decimal
    vol: Decimal
    amount: Decimal


def load_daily(path: str | Path) -> tuple[Candle, ...]:
    """Explicitly select candles/fields; never serialize or log the containing object."""
    source = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(source, dict) or not isinstance(source.get("candles"), list):
        raise ValueError("expected Tushare candle envelope")
    fields = source.get("fields")
    if not isinstance(fields, list) or set(REQUIRED) - set(fields) or len(fields) != len(set(fields)):
        raise ValueError("missing or duplicate candle fields")
    parsed = []
    for row in source["candles"]:
        if isinstance(row, list) and len(row) == len(fields):
            item = dict(zip(fields, row))
        elif isinstance(row, dict):
            item = row
        else:
            raise ValueError("malformed candle row")
        try:
            parsed.append(Candle(
                ts_code=str(item["ts_code"]),
                trade_date=date.fromisoformat(str(item["trade_date"])[:4] + "-" + str(item["trade_date"])[4:6] + "-" + str(item["trade_date"])[6:]),
                **{key: dec(item[key]) for key in REQUIRED[2:]},
            ))
        except (KeyError, TypeError) as exc:
            raise ValueError("malformed candle row") from exc
    return tuple(sorted(parsed, key=lambda item: item.trade_date))


@dataclass(frozen=True)
class PrecloseGap:
    trade_date: date
    previous_close: Decimal
    pre_close: Decimal
    difference: Decimal
    dividend: Decimal | None
    reconciled: bool


@dataclass(frozen=True)
class Verification:
    first_date: date
    last_date: date
    row_count: int
    unique_date_count: int
    expected_session_count: int
    closed_weekdays: tuple[date, ...]
    missing_sessions: tuple[date, ...]
    unexpected_session_dates: tuple[date, ...]
    duplicate_dates: tuple[date, ...]
    ohlc_errors: tuple[date, ...]
    preclose_gaps: tuple[PrecloseGap, ...]

    @property
    def valid(self) -> bool:
        return not (
            self.missing_sessions or self.unexpected_session_dates or self.duplicate_dates
            or self.ohlc_errors or any(not gap.reconciled for gap in self.preclose_gaps)
        )


def verify_daily(candles: tuple[Candle, ...], dividends: dict[date, Decimal]) -> Verification:
    if not candles:
        raise ValueError("empty candle data")
    rows = tuple(sorted(candles, key=lambda item: item.trade_date))
    dates = Counter(row.trade_date for row in rows)
    first, last = rows[0].trade_date, rows[-1].trade_date
    expected = set(trading_dates(first, last))
    seen = set(dates)
    codes = {row.ts_code for row in rows}
    if codes != {"601518.SH"}:
        raise ValueError("unexpected security code")
    errors = []
    for row in rows:
        if not (Decimal(0) < row.low <= row.high and row.low <= row.open <= row.high
                and row.low <= row.close <= row.high and row.pre_close > 0
                and row.vol >= 0 and row.amount >= 0):
            errors.append(row.trade_date)
    gaps = []
    for previous, current in zip(rows, rows[1:]):
        difference = previous.close - current.pre_close
        if abs(difference) > Decimal("0.005") or current.trade_date in dividends:
            dividend = dividends.get(current.trade_date)
            expected_preclose = ((previous.close - dividend).quantize(CENT, rounding=ROUND_HALF_UP)
                                 if dividend is not None else None)
            gaps.append(PrecloseGap(current.trade_date, previous.close, current.pre_close,
                                    difference, dividend, expected_preclose == current.pre_close))
    return Verification(
        first, last, len(rows), len(seen), len(expected), closed_weekdays(first, last),
        tuple(sorted(expected - seen)), tuple(sorted(seen - expected)),
        tuple(sorted(day for day, count in dates.items() if count > 1)),
        tuple(sorted(set(errors))), tuple(gaps),
    )
