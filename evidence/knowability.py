"""Pure, conservative historical knowability rules for canonical sidecars."""

from dataclasses import asdict
from datetime import date, datetime, tzinfo
from typing import Any, Mapping

from .contracts import KnowabilityTimes, TimeFact


def aware_clock(value: str) -> datetime:
    if not isinstance(value, str):
        raise ValueError("clock must be text")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        raise ValueError("clock must be a valid ISO timestamp") from None
    if parsed.tzinfo is None:
        raise ValueError("clock must include a valid timezone")
    return parsed


def clock_fact(value: Any, basis: str) -> TimeFact:
    """Preserve date-only clock candidates without inventing a timezone."""
    if value is None:
        return TimeFact(None, basis)
    try:
        aware_clock(value)
        return TimeFact(value, basis, "VERIFIED")
    except (ValueError, TypeError):
        if isinstance(value, str):
            try:
                if date.fromisoformat(value).isoformat() == value:
                    return TimeFact(value, basis)
            except ValueError:
                pass
        return TimeFact(None, basis)


def disclosure_fact(row: Mapping[str, Any], *, basis: str = "statement.row") -> TimeFact:
    # Only explicit publication semantics; 日期/时间/late/provider clocks are
    # never silently promoted. A date without timezone stays UNVERIFIED.
    names = ("disclosure_time", "publish_time", "披露时间", "披露日期", "公告日期")
    candidates = [clock_fact(row[name], basis + "." + name)
                  for name in names if row.get(name) is not None]
    if not candidates:
        return TimeFact(None, basis + ".disclosure_missing")
    def key(fact):
        return aware_clock(fact.value) if fact.status == "VERIFIED" else fact.value
    if any(key(fact) != key(candidates[0]) for fact in candidates[1:]):
        return TimeFact(None, ";".join(fact.source_basis for fact in candidates), "CONFLICT")
    first = candidates[0]
    return TimeFact(first.value, ";".join(fact.source_basis for fact in candidates), first.status)


def time_facts(*, period: str | None, period_basis: str, disclosure: TimeFact,
               acquired_at: str | None, judgment_cutoff: str | None) -> dict:
    for value in (acquired_at, judgment_cutoff):
        if value is not None:
            aware_clock(value)
    return asdict(KnowabilityTimes(
        TimeFact(period, period_basis, "VERIFIED" if period else "UNVERIFIED"),
        disclosure, clock_fact(acquired_at, "caller.acquired_at"),
        clock_fact(judgment_cutoff, "caller.judgment_cutoff")))


def validate_times(value: Mapping[str, Any]) -> None:
    if not isinstance(value, Mapping) or set(value) != set(KnowabilityTimes.__dataclass_fields__):
        raise ValueError("invalid knowability contract")
    for name, fact in value.items():
        if (not isinstance(fact, Mapping) or set(fact) != set(TimeFact.__dataclass_fields__)
                or fact["status"] not in {"VERIFIED", "UNVERIFIED", "CONFLICT"}
                or not isinstance(fact["source_basis"], str) or not fact["source_basis"].strip()
                or fact["value"] is not None and not isinstance(fact["value"], str)):
            raise ValueError("invalid time fact")
        if fact["status"] == "VERIFIED":
            if name == "data_period" and isinstance(fact["value"], str) and len(fact["value"]) == 10:
                if date.fromisoformat(fact["value"]).isoformat() != fact["value"]:
                    raise ValueError("invalid period")
            else:
                aware_clock(fact["value"])


def cutoff_reasons(times: Mapping[str, Any], cutoff: str, *,
                   period_timezone: tzinfo | None = None) -> tuple[str, ...]:
    """Compare date periods as calendar days in an explicitly supplied timezone.

    A date is not a publication timestamp or an end-of-day availability claim.
    Without a calendar contract, even a VERIFIED date remains unusable.
    """
    validate_times(times)
    boundary = aware_clock(cutoff)
    reasons = []
    for name, later in (("disclosed_at", "DISCLOSED_AFTER_CUTOFF"),
                        ("acquired_at", "ACQUIRED_AFTER_CUTOFF")):
        fact = times[name]
        if fact["status"] != "VERIFIED":
            reasons.append("SOURCE_TIME_CONFLICT" if fact["status"] == "CONFLICT"
                           else name.upper() + "_UNVERIFIED")
        elif aware_clock(fact["value"]) > boundary:
            reasons.append(later)
    period = times["data_period"]
    if period["status"] != "VERIFIED":
        reasons.append("DATA_PERIOD_UNVERIFIED")
    elif len(period["value"]) == 10:
        if period_timezone is None:
            reasons.append("DATA_PERIOD_UNVERIFIED")
        elif date.fromisoformat(period["value"]) > boundary.astimezone(period_timezone).date():
            reasons.append("DATA_PERIOD_AFTER_CUTOFF")
    elif aware_clock(period["value"]) > boundary:
        reasons.append("DATA_PERIOD_AFTER_CUTOFF")
    if all(times[name]["status"] == "VERIFIED" for name in ("disclosed_at", "acquired_at")):
        if aware_clock(times["disclosed_at"]["value"]) > aware_clock(times["acquired_at"]["value"]):
            reasons.append("SOURCE_TIME_CONFLICT")
    return tuple(dict.fromkeys(reasons))
