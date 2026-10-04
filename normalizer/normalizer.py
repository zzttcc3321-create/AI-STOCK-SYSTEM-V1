"""Serial, offline conversion of provider envelopes into canonical records."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import asdict, is_dataclass
from typing import Any, Mapping, Sequence
from evidence.knowability import aware_clock, disclosure_fact, time_facts

from .contracts import (
    FRESHNESS_REQUEST_MAX_GAP_SECONDS, FRESHNESS_STALE_AFTER_SECONDS,
    ConversionRule, Freshness, FreshnessCheck, MissingData, NormalizationResult, NormalizationRule,
    NormalizedRecord, NumericField, UnitStatus,
)
from .semantics import metadata
from .validation import parse_trade_date, validate_daily, validate_quote

DAILY_KINDS = frozenset({"historical_daily", "daily_kline"})
QUOTE_KINDS = frozenset({"realtime_quote", "market_quotes"})
PRICE_FIELDS = ("open", "high", "low", "close", "pre_close")


def _get(source: Any, name: str, default: Any = None) -> Any:
    return source.get(name, default) if isinstance(source, Mapping) else getattr(source, name, default)


def _quote_freshness(source, envelope, acquired_at, judgment_cutoff):
    timestamp_source = "provider.row.quote_timestamp" if "quote_timestamp" in source else "envelope.provider_timestamp"
    timestamp = source.get("quote_timestamp", _get(envelope, "provider_timestamp"))
    requested = _get(envelope, "request_timestamp")
    reference, reference_source = next((value, basis) for value, basis in (
        (judgment_cutoff, "caller.judgment_cutoff"),
        (acquired_at, "caller.acquired_at"),
        (requested, "envelope.request_timestamp"),
        (None, "reference_missing"),
    ) if value is not None or basis == "reference_missing")
    declared = source.get("freshness")
    reasons = []
    if not isinstance(declared, str) or declared not in {item.value for item in Freshness}:
        reasons.append("FRESHNESS_UNDECLARED" if declared is None else "INVALID_FRESHNESS_DECLARATION")
        declared_state = Freshness.UNKNOWN.value
    else:
        declared_state = declared
        if declared != Freshness.REALTIME.value:
            reasons.append("DECLARED_" + declared)

    def clock(value, name):
        if value is None:
            reasons.append(name + "_MISSING")
            return None
        try:
            return aware_clock(value)
        except (ValueError, TypeError):
            reasons.append(name + "_INVALID")
            return None

    quote_clock = clock(timestamp, "QUOTE_TIMESTAMP")
    reference_clock = clock(reference, "REFERENCE_TIME")
    request_clock = clock(requested, "REQUEST_TIMESTAMP")
    age = None
    stale = False
    limited = quote_clock is None or reference_clock is None or request_clock is None
    max_age = FRESHNESS_STALE_AFTER_SECONDS
    max_gap = FRESHNESS_REQUEST_MAX_GAP_SECONDS
    if quote_clock is not None and reference_clock is not None:
        age = (reference_clock - quote_clock).total_seconds()
        if age < 0:
            reasons.append("FUTURE_QUOTE_TIMESTAMP")
            limited = True
        elif age > max_age:
            reasons.append("STALE_QUOTE_TIMESTAMP")
            stale = True
    if quote_clock is not None and acquired_at is not None:
        if quote_clock > aware_clock(acquired_at):
            reasons.append("QUOTE_AFTER_ACQUISITION")
            limited = True
    if request_clock is not None and reference_clock is not None:
        gap = (reference_clock - request_clock).total_seconds()
        if gap < 0 or gap > max_gap or (quote_clock is not None and
                (quote_clock - request_clock).total_seconds() > max_gap):
            reasons.append("REQUEST_TIME_MISMATCH")
            limited = True
    state = Freshness.UNKNOWN.value if limited else Freshness.STALE.value if stale else declared_state
    check = FreshnessCheck(timestamp, timestamp_source, declared, requested, acquired_at,
                           judgment_cutoff, reference, reference_source, age, tuple(reasons))
    return timestamp, state, asdict(check)


def _numeric(raw_value: Any, raw_unit: str | None, normalized_unit: str) -> NumericField:
    if raw_value is None:
        # Missing is not an unknown unit and must remain missing.
        return NumericField(None, raw_unit, None, normalized_unit, ConversionRule.IDENTITY, UnitStatus.VERIFIED)
    try:
        number = float(raw_value)
    except (TypeError, ValueError):
        return NumericField(raw_value, raw_unit, None, normalized_unit, None, UnitStatus.UNVERIFIED)
    unit = raw_unit.strip().lower() if isinstance(raw_unit, str) else None
    identities = {"元": "元", "yuan": "元", "cny": "元", "股": "股", "share": "股", "shares": "股"}
    if unit in identities and identities[unit] == normalized_unit:
        return NumericField(raw_value, raw_unit, number, normalized_unit, ConversionRule.IDENTITY, UnitStatus.VERIFIED)
    if normalized_unit == "股" and unit in {"手", "lot", "lots"}:
        return NumericField(raw_value, raw_unit, number * 100, normalized_unit, ConversionRule.LOT100_TO_SHARE, UnitStatus.VERIFIED)
    if normalized_unit == "元" and unit in {"万元", "万人民币", "10k yuan"}:
        return NumericField(raw_value, raw_unit, number * 10000, normalized_unit, ConversionRule.TEN_THOUSAND_YUAN_TO_YUAN, UnitStatus.VERIFIED)
    return NumericField(raw_value, raw_unit, None, normalized_unit, None, UnitStatus.UNVERIFIED)


class DataNormalizer:
    """Normalizer with no network or database dependencies.

    Unit declarations are mandatory inputs rather than inferred from provider
    identity. They may be passed explicitly or supplied as ``payload['units']``.
    """

    def __init__(self, *, price_change_warning_threshold: float = 0.30) -> None:
        if price_change_warning_threshold < 0:
            raise ValueError("price_change_warning_threshold must not be negative")
        self.warning_threshold = price_change_warning_threshold

    def normalize(self, envelope: Any, *, units: Mapping[str, str | None] | None = None,
                  acquired_at: str | None = None, judgment_cutoff: str | None = None) -> NormalizationResult:
        kind = _get(envelope, "data_kind")
        if kind in DAILY_KINDS:
            return self.normalize_daily(envelope, units=units, acquired_at=acquired_at, judgment_cutoff=judgment_cutoff)
        if kind in QUOTE_KINDS:
            return self.normalize_market_quotes(envelope, units=units, acquired_at=acquired_at, judgment_cutoff=judgment_cutoff)
        raise ValueError(f"unsupported data_kind: {kind!r}")

    def normalize_daily(self, envelope: Any, *, units: Mapping[str, str | None] | None = None,
                        acquired_at: str | None = None, judgment_cutoff: str | None = None) -> NormalizationResult:
        raw = deepcopy(envelope)
        payload = _get(envelope, "payload") or {}
        bars = payload.get("bars", []) if isinstance(payload, Mapping) else []
        declarations = dict(payload.get("units", {})) if isinstance(payload, Mapping) else {}
        declarations.update(units or {})
        provider = _get(envelope, "provider_name")
        provider_timestamp = _get(envelope, "provider_timestamp")
        records: list[NormalizedRecord] = []
        canonical: list[Mapping[str, Any]] = []
        seen: set[str] = set()
        for bar in bars if isinstance(bars, Sequence) and not isinstance(bars, (str, bytes)) else ():
            semantic_declarations = {key: payload[key] for key in ("period", "field_semantics", "rule_version") if key in payload}
            record_source = {**semantic_declarations, **bar} if isinstance(bar, Mapping) else bar
            record, output = self._daily_record(record_source, provider, provider_timestamp, declarations,
                                               acquired_at, judgment_cutoff)
            for step in record.values.get("conversion_chain", ()):
                if step["field"] in semantic_declarations and isinstance(bar, Mapping) and step["field"] not in bar:
                    step["source"] = f"{provider}.payload.{step['field']}"
                if "input_unit" in step:
                    step["unit_declaration_source"] = "caller.units+payload.units" if units else f"{provider}.payload.units"
            records.append(record)
            if output is not None:
                canonical.append(output)
            date = parse_trade_date(bar.get("trade_date")) if isinstance(bar, Mapping) else None
            if date:
                seen.add(date)
        expected_raw = payload.get("expected_trade_dates") or payload.get("validation", {}).get("missing_trade_dates", [])
        expected = {date for value in expected_raw if (date := parse_trade_date(value))}
        stock_id = payload.get("stock_id") or next((b.get("stock_id") for b in bars if isinstance(b, Mapping) and b.get("stock_id")), "")
        missing = tuple(MissingData(str(stock_id), date) for date in sorted(expected - seen))
        series = []
        for stock in sorted({row["stock_id"] for row in canonical}):
            bases = sorted({row["adjustment_basis"] for row in canonical if row["stock_id"] == stock})
            series.append({"stock_id": stock, "adjustment_bases": bases,
                "comparable": len(bases) == 1 and "UNKNOWN" not in bases,
                "reason_codes": [] if len(bases) == 1 and "UNKNOWN" not in bases else ["MIXED_OR_UNKNOWN_ADJUSTMENT_BASIS"]})
        return NormalizationResult(raw, tuple(records), tuple(canonical), missing,
            {"execution": "SERIAL", "price_series": series})

    def _daily_record(self, bar: Any, provider: Any, timestamp: Any, units: Mapping[str, str | None],
                      acquired_at=None, judgment_cutoff=None):
        source = bar if isinstance(bar, Mapping) else {}
        numeric: dict[str, NumericField] = {}
        for name in PRICE_FIELDS:
            numeric[name] = _numeric(source.get(name), units.get(name, units.get("price")), "元")
        numeric["volume"] = _numeric(source.get("volume", source.get("vol")), units.get("volume", units.get("vol")), "股")
        numeric["amount"] = _numeric(source.get("amount"), units.get("amount"), "元")
        declared_adjust = source.get("adjust_type")
        verified_adjust = source.get("adjust_status") == "VERIFIED"
        adjust_type = declared_adjust if declared_adjust == "UNADJUSTED" or (declared_adjust in {"QFQ", "HFQ"} and verified_adjust) else None
        values = {
            "stock_id": source.get("stock_id") or source.get("ts_code"),
            "trade_date": parse_trade_date(source.get("trade_date")) or source.get("trade_date"),
            "provider": provider,
            "provider_timestamp": timestamp,
            "adjust_type": adjust_type,
            "adjust_status": "VERIFIED" if adjust_type is not None else "UNVERIFIED",
        }
        values.update(metadata({**source, "adjust_type": adjust_type}, provider, "daily_kline", numeric))
        if acquired_at is not None or judgment_cutoff is not None:
            values["knowability"] = time_facts(period=values["trade_date"], period_basis="canonical.trade_date",
                disclosure=disclosure_fact(source, basis="provider.row"), acquired_at=acquired_at, judgment_cutoff=judgment_cutoff)
        result = validate_daily(values, numeric, warning_threshold=self.warning_threshold)
        rules = tuple(NormalizationRule(name, item.conversion_rule, item) for name, item in numeric.items())
        normalized = NormalizedRecord(values, numeric, rules, result)
        if not result.passed:
            return normalized, None
        output = dict(values)
        output.update({name: numeric[name].normalized_value for name in ("open", "high", "low", "close", "volume", "amount")})
        output["data_quality_status"] = result.status.value
        return normalized, output

    def normalize_market_quotes(self, envelope: Any, *, units: Mapping[str, str | None] | None = None,
                                acquired_at: str | None = None, judgment_cutoff: str | None = None) -> NormalizationResult:
        raw = deepcopy(envelope)
        payload = _get(envelope, "payload") or {}
        declarations = dict(payload.get("units", {})) if isinstance(payload, Mapping) else {}
        declarations.update(units or {})
        quotes = payload.get("quotes") if isinstance(payload, Mapping) else None
        if quotes is None:
            quotes = [payload]
        records, canonical = [], []
        for quote in quotes:
            semantic_declarations = {key: payload[key] for key in ("period", "field_semantics", "rule_version", "adjust_type", "adjust_status") if key in payload}
            if isinstance(quote, Mapping):
                for key in ("adjust_type", "adjust_status"):
                    if key in semantic_declarations and key in quote and quote[key] != semantic_declarations[key]:
                        raise ValueError(f"quote {key} conflicts with payload declaration")
            source = {**semantic_declarations, **quote} if isinstance(quote, Mapping) else {}
            numeric = {name: _numeric(source.get(name), declarations.get(name, declarations.get("price") if name in {"price", "bid", "ask"} else None), "股" if name == "volume" else "元") for name in ("price", "bid", "ask", "volume", "amount")}
            timestamp, freshness, check = _quote_freshness(source, envelope, acquired_at, judgment_cutoff)
            values = {"stock_id": source.get("stock_id"), "quote_timestamp": timestamp, "freshness": freshness,
                      "freshness_check": check, "provider": _get(envelope, "provider_name")}
            values.update(metadata(source, _get(envelope, "provider_name"), "market_quotes", numeric))
            for step in values["conversion_chain"]:
                if step["field"] == "adjustment_basis" and "adjust_type" in source:
                    step["source"] = (f"{_get(envelope, 'provider_name')}.payload.adjust_type"
                                      if "adjust_type" in semantic_declarations and "adjust_type" not in quote
                                      else f"{_get(envelope, 'provider_name')}.row.adjust_type")
                if step["field"] in semantic_declarations and isinstance(quote, Mapping) and step["field"] not in quote:
                    step["source"] = f"{_get(envelope, 'provider_name')}.payload.{step['field']}"
                if "input_unit" in step:
                    step["unit_declaration_source"] = "caller.units+payload.units" if units else f"{_get(envelope, 'provider_name')}.payload.units"
            if acquired_at is not None or judgment_cutoff is not None:
                values["knowability"] = time_facts(period=timestamp, period_basis="canonical.quote_timestamp",
                    disclosure=disclosure_fact(source, basis="provider.row"), acquired_at=acquired_at, judgment_cutoff=judgment_cutoff)
            validation = validate_quote(values, numeric)
            rules = tuple(NormalizationRule(name, item.conversion_rule, item) for name, item in numeric.items())
            records.append(NormalizedRecord(values, numeric, rules, validation))
            if validation.passed:
                item = dict(values)
                item.update({name: field.normalized_value for name, field in numeric.items()})
                item["data_quality_status"] = validation.status.value
                canonical.append(item)
        return NormalizationResult(raw, tuple(records), tuple(canonical), metadata={"execution": "SERIAL"})


Normalizer = DataNormalizer
