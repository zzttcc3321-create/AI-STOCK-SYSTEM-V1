"""SSE published closing periods for the bounded Stage 1 data window.

Sources and the exact weekday closures are recorded in BACKTEST_METHOD.md.
Weekend make-up workdays remain closed for trading.
"""

from datetime import date, timedelta


_CLOSURES = {
    2023: [("2023-09-29", "2023-10-06")],
    2024: [
        ("2024-01-01", "2024-01-01"),
        ("2024-02-09", "2024-02-17"),
        ("2024-04-04", "2024-04-06"),
        ("2024-05-01", "2024-05-05"),
        ("2024-06-10", "2024-06-10"),
        ("2024-09-15", "2024-09-17"),
        ("2024-10-01", "2024-10-07"),
    ],
    2025: [
        ("2025-01-01", "2025-01-01"),
        ("2025-01-28", "2025-02-04"),
        ("2025-04-04", "2025-04-06"),
        ("2025-05-01", "2025-05-05"),
        ("2025-05-31", "2025-06-02"),
        ("2025-10-01", "2025-10-08"),
    ],
    2026: [
        ("2026-01-01", "2026-01-03"),
        ("2026-02-15", "2026-02-23"),
        ("2026-04-04", "2026-04-06"),
        ("2026-05-01", "2026-05-05"),
        ("2026-06-19", "2026-06-21"),
        ("2026-09-25", "2026-09-27"),
        ("2026-10-01", "2026-10-07"),
    ],
}


def _each_day(start: date, end: date):
    day = start
    while day <= end:
        yield day
        day += timedelta(days=1)


def closure_dates(year: int) -> frozenset[date]:
    if year not in _CLOSURES:
        raise ValueError(f"SSE calendar not verified for {year}")
    return frozenset(
        day
        for first, last in _CLOSURES[year]
        for day in _each_day(date.fromisoformat(first), date.fromisoformat(last))
    )


def trading_dates(start: date, end: date) -> tuple[date, ...]:
    if end < start:
        raise ValueError("end precedes start")
    closed = set().union(*(closure_dates(year) for year in range(start.year, end.year + 1)))
    return tuple(day for day in _each_day(start, end) if day.weekday() < 5 and day not in closed)


def closed_weekdays(start: date, end: date) -> tuple[date, ...]:
    closed = set().union(*(closure_dates(year) for year in range(start.year, end.year + 1)))
    return tuple(day for day in _each_day(start, end) if day.weekday() < 5 and day in closed)
