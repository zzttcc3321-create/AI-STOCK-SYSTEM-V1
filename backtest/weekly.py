"""Weekly bar availability gate; contains no signal or strategy rules."""

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from decimal import Decimal
from zoneinfo import ZoneInfo

from .adjustment import AdjustedBar
from .calendar import trading_dates


SHANGHAI = ZoneInfo("Asia/Shanghai")


@dataclass(frozen=True)
class WeeklyBar:
    week_monday: date
    confirmed_at: datetime
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal


def confirmed_weekly_bars(bars: tuple[AdjustedBar, ...], as_of: datetime) -> tuple[WeeklyBar, ...]:
    if as_of.tzinfo is None or as_of.utcoffset() is None:
        raise ValueError("as_of must be timezone aware")
    local_as_of = as_of.astimezone(SHANGHAI)
    by_date = {bar.trade_date: bar for bar in bars}
    if len(by_date) != len(bars):
        raise ValueError("duplicate adjusted bar date")
    result = []
    for monday in sorted({day - timedelta(days=day.weekday()) for day in by_date}):
        friday = monday + timedelta(days=4)
        sessions = trading_dates(monday, friday)
        if not sessions:
            continue
        confirmed_at = datetime.combine(sessions[-1], time(15, 0), SHANGHAI)
        if local_as_of <= confirmed_at:
            continue
        if any(day not in by_date for day in sessions):
            raise ValueError("completed week has missing trading session")
        week = [by_date[day] for day in sessions]
        result.append(WeeklyBar(monday, confirmed_at, week[0].open,
                                max(bar.high for bar in week), min(bar.low for bar in week),
                                week[-1].close))
    return tuple(result)
