"""Pure assembly at the canonical/Evidence boundary; no provider access."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from hashlib import sha256
from typing import Any, Mapping, Sequence

from evidence.contracts import EvidenceRecord
from evidence.contracts import TimeFact
from evidence.knowability import aware_clock, cutoff_reasons, time_facts, validate_times
from normalizer.contracts import ConversionRule, NumericField, UnitStatus
from normalizer.validation import parse_trade_date, validate_daily, validate_quote
from normalizer.information import validate_information_evidence, validate_information_result
from normalizer.information_contracts import (
    SOURCE as INFORMATION_SOURCE, InformationNormalizationError,
)

from .contracts import (
    AVAILABILITY_STATUSES, INFORMATION_FIELDS, QUALITY_STATUSES, SOURCES,
    AnalysisInputError, AnalysisPackage, safe_json,
    stable_json,
)

DAILY_SOURCE = "TUSHARE_MCP"
QUOTE_SOURCE = "TDX_OFFICIAL_LOCAL_HTTP"
# Canonical A-share trade/report dates are China calendar days, independent of
# the cutoff's textual offset. This does not supply a missing disclosure clock.
_PERIOD_TIMEZONE = timezone(timedelta(hours=8))
_EVIDENCE_FIELDS = frozenset({"evidence_id", "source", "request_timestamp",
                              "provider_timestamp", "validation_result", "human_confirm"})
_DAILY_FIELDS = frozenset({"stock_id", "trade_date", "open", "high", "low", "close",
                           "volume", "amount", "adjust_type", "data_quality_status",
                           "provider", "provider_timestamp"})
_QUOTE_FIELDS = frozenset({"stock_id", "quote_timestamp", "price", "bid", "ask",
                           "volume", "amount", "freshness", "data_quality_status", "provider"})
_PERSISTENCE_FIELDS = frozenset({"id", "created_at", "updated_at", "request_timestamp"})


def _mapping(value: Any, label: str) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise AnalysisInputError(f"{label} must be a canonical mapping")
    return safe_json(value)


def _sequence(value: Any, label: str) -> list[Any]:
    if not isinstance(value, (tuple, list)):
        raise AnalysisInputError(f"{label} must be a sequence")
    return safe_json(value)


def _text(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise AnalysisInputError(f"{label} must be nonempty text")
    return value


def _text_set(value: Any, label: str) -> set[str]:
    return {_text(item, label) for item in _sequence(value, label)}


def _timestamp(value: Any, *, nullable: bool = False, date_allowed: bool = False) -> None:
    if value is None and nullable:
        return
    if not isinstance(value, str):
        raise AnalysisInputError("timestamp must be text")
    if date_allowed and len(value) == 10 and parse_trade_date(value) == value:
        return
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            raise ValueError
    except ValueError:
        raise AnalysisInputError("timestamp must include a valid timezone") from None


def _number(value: Any, label: str, *, nullable: bool = False, negative: bool = False) -> None:
    if value is None and nullable:
        return
    if type(value) not in (int, float) or (value < 0 and not negative):
        raise AnalysisInputError(f"{label} must be a valid canonical number")


def _evidence_index(metadata: Sequence[Any]) -> dict[str, dict[str, Any]]:
    if not isinstance(metadata, (tuple, list)):
        raise AnalysisInputError("evidence_metadata must be a sequence")
    index = {}
    for item in metadata:
        # Only a typed EvidenceRecord may carry the wider sidecar metadata.
        # Never inspect its request_params, raw paths, hashes, or raw content.
        if isinstance(item, EvidenceRecord):
            validation = {"status": item.validation_result.status.value,
                          "failures": list(item.validation_result.failures)}
            value = {name: getattr(item, name) for name in _EVIDENCE_FIELDS - {"validation_result"}}
            value["validation_result"] = validation
            value = safe_json(value)
        else:
            value = _mapping(item, "evidence metadata")
        if set(value) != _EVIDENCE_FIELDS:
            raise AnalysisInputError("evidence metadata must contain the frozen six fields")
        identifier = _text(value["evidence_id"], "evidence_id")
        if identifier in index:
            raise AnalysisInputError("duplicate evidence_id")
        if _text(value["source"], "evidence source") not in SOURCES:
            raise AnalysisInputError("FOURTH_SOURCE is forbidden")
        _timestamp(value["request_timestamp"])
        _timestamp(value["provider_timestamp"], nullable=True, date_allowed=True)
        validation = _mapping(value["validation_result"], "validation_result")
        if set(validation) - {"status", "failures"} or _text(validation.get("status"), "evidence validation status") not in {"PASS", "FAIL", "NOT_VALIDATED"}:
            raise AnalysisInputError("invalid evidence validation_result")
        for failure in _sequence(validation.get("failures", []), "validation failures"):
            _text(failure, "validation failure")
        if validation["status"] == "PASS" and validation.get("failures"):
            raise AnalysisInputError("PASS evidence cannot contain validation failures")
        if _text(value["human_confirm"], "human_confirm") not in {"PENDING", "CONFIRMED", "REJECTED"}:
            raise AnalysisInputError("invalid human_confirm")
        index[identifier] = value
    return index


def _fallback(value: Any) -> dict[str, Any] | None:
    if value is None:
        return None
    result = _mapping(value, "fallback_used")
    if set(result) != {"from_source", "to_source", "reason", "validated"}:
        raise AnalysisInputError("fallback must be explicit, reasoned, and validated")
    if result["from_source"] != DAILY_SOURCE or result["to_source"] != QUOTE_SOURCE or result["validated"] is not True:
        raise AnalysisInputError("only validated official daily fallback is allowed")
    _text(result["reason"], "fallback reason")
    return result


def _validate_market(row: dict[str, Any], kind: str, stock_id: str) -> tuple[str, ...]:
    required = _DAILY_FIELDS if kind == "daily_kline" else _QUOTE_FIELDS
    optional = _PERSISTENCE_FIELDS | {"knowability"} | ({"adjust_status"} if kind == "daily_kline" else {"freshness_check"})
    if not required <= set(row) or set(row) - required - optional:
        raise AnalysisInputError("record does not match the existing canonical contract")
    if row["stock_id"] != stock_id:
        raise AnalysisInputError("canonical record stock identity mismatch")
    if _text(row["provider"], "canonical source") not in SOURCES:
        raise AnalysisInputError("FOURTH_SOURCE is forbidden")
    if _text(row["data_quality_status"], "canonical quality") not in QUALITY_STATUSES:
        raise AnalysisInputError("FAILED or unvalidated data cannot enter analysis")
    for name in _PERSISTENCE_FIELDS & set(row):
        if name == "id":
            if not (type(row[name]) is int and row[name] > 0) and not (isinstance(row[name], str) and row[name].strip()):
                raise AnalysisInputError("canonical persistence id must be a scalar identifier")
        else:
            _timestamp(row[name])
    names = ("open", "high", "low", "close", "volume", "amount") if kind == "daily_kline" else ("price", "bid", "ask", "volume", "amount")
    numeric = {}
    for name in names:
        _number(row[name], name, nullable=name not in ({"open", "high", "low", "close", "volume"} if kind == "daily_kline" else {"price"}))
        unit = "股" if name == "volume" else "元"
        numeric[name] = NumericField(row[name], unit, row[name], unit,
                                     ConversionRule.IDENTITY, UnitStatus.VERIFIED)
    if kind == "daily_kline":
        if parse_trade_date(row["trade_date"]) != row["trade_date"]:
            raise AnalysisInputError("trade_date must already be canonical YYYY-MM-DD")
        _timestamp(row["provider_timestamp"], nullable=True, date_allowed=True)
        if row["adjust_type"] is not None:
            _text(row["adjust_type"], "adjust_type")
        if "adjust_status" in row and _text(row["adjust_status"], "adjust_status") not in {"VERIFIED", "UNVERIFIED"}:
            raise AnalysisInputError("invalid canonical adjustment status")
        if row["adjust_type"] is not None and row.get("adjust_status", "VERIFIED") != "VERIFIED":
            raise AnalysisInputError("known adjustment must have VERIFIED status")
        if row["adjust_type"] in {"QFQ", "HFQ"} and row.get("adjust_status") != "VERIFIED":
            raise AnalysisInputError("adjusted canonical data requires verified adjustment")
        # This is validation of already canonical numbers, never normalization
        # of provider/raw values. Reuse frozen hard invariants without edits.
        result = validate_daily(row, numeric, warning_threshold=0.30)
    else:
        _timestamp(row["quote_timestamp"], nullable=True)
        if _text(row["freshness"], "freshness") not in {"REALTIME", "STALE", "UNKNOWN"}:
            raise AnalysisInputError("invalid canonical freshness")
        if row["quote_timestamp"] is None and row["freshness"] != "UNKNOWN":
            raise AnalysisInputError("missing quote clock requires UNKNOWN freshness")
        result = validate_quote(row, numeric)
    if not result.passed:
        raise AnalysisInputError("canonical market hard validation failed")
    ranks = {"PASS": 0, "WARNING": 1, "UNVERIFIED": 2}
    if ranks[row["data_quality_status"]] < ranks[result.status.value]:
        raise AnalysisInputError("canonical quality status hides a validation warning")
    return result.warnings


def _block(records: list[dict[str, Any]], *, reason: str | None = None,
           absent_status: str = "MISSING") -> dict[str, Any]:
    statuses = {entry["data"].get("data_quality_status", "PASS") for entry in records}
    status = "UNVERIFIED" if "UNVERIFIED" in statuses else "WARNING" if "WARNING" in statuses else "PASS"
    return {"status": status if records else absent_status, "records": records, "reason": reason,
            "aggregation_rule": "WORST_RECORD_QUALITY: UNVERIFIED > WARNING > PASS; EMPTY_USES_ABSENT_STATUS"}


class AnalysisPackageBuilder:
    """Accept only named canonical collections, snapshots and Evidence metadata.

    Information accepts only sealed #008 gate batches. ``evidence_links``
    addresses records by ``daily_kline.N`` / ``market_quotes.N`` or information
    category and index, and identity by ``stock_identity``.
    Holdings are explicit portfolio_positions business input, without Provider
    Evidence. No store or Provider object is accepted or called.
    """

    def build(
        self, *, stock_identity: Mapping[str, Any],
        canonical_data: Mapping[str, Any] | None = None,
        canonical_information: Sequence[Any] = (),
        holding_snapshot: Mapping[str, Any] | None = None,
        evidence_metadata: Sequence[Any] = (),
        evidence_links: Mapping[str, Any] | None = None,
        source_status: Mapping[str, Any] | None = None,
        information_availability: Mapping[str, Any] | None = None,
        missing_fields: Sequence[str] = (), stale_fields: Sequence[str] = (),
        validation_warnings: Sequence[str] = (), fallback_used: Mapping[str, Any] | None = None,
        judgment_cutoff: str | None = None,
    ) -> AnalysisPackage:
        identity = _mapping(stock_identity, "stock_identity")
        if set(identity) - {"stock_id", "symbol", "market", "name", "industry"}:
            raise AnalysisInputError("unknown stock_identity field")
        stock_id = _text(identity.get("stock_id"), "stock_id")
        for value in identity.values():
            if value is not None:
                _text(value, "stock identity")
        market = _mapping(canonical_data if canonical_data is not None else {}, "canonical_data")
        if set(market) - {"daily_kline", "market_quotes"}:
            raise AnalysisInputError("only existing market canonical contracts are supported")
        sources = _mapping(source_status if source_status is not None else {}, "source_status")
        for source, status in sources.items():
            if source not in SOURCES:
                raise AnalysisInputError("FOURTH_SOURCE is forbidden")
            if _text(status, "source availability status") not in AVAILABILITY_STATUSES:
                raise AnalysisInputError("invalid source availability status")
        sources = {source: sources.get(source, "MISSING") for source in sorted(SOURCES)}
        fallback = _fallback(fallback_used)
        links = _mapping(evidence_links if evidence_links is not None else {}, "evidence_links")
        if "holding_snapshot" in links:
            raise AnalysisInputError("holding_snapshot cannot reference Provider Evidence")
        evidence = _evidence_index(evidence_metadata)
        used, addressed = set(), set()
        missing = _text_set(missing_fields, "missing_fields")
        reported_missing = set(missing)
        stale = _text_set(stale_fields, "stale_fields")
        warnings = _text_set(validation_warnings, "validation_warnings")
        incomplete = []
        restrictions = []
        excluded_record_quality = []
        # A cutoff sealed by normalization cannot silently disappear downstream.
        # All supplied contexts must agree; no choice between source cutoffs.
        embedded = []
        context_cutoffs = []
        if not isinstance(canonical_information, (tuple, list)):
            raise AnalysisInputError("canonical_information must be a sequence of gate batches")
        for result in canonical_information:
            try:
                validate_information_result(result)
            except InformationNormalizationError as exc:
                raise AnalysisInputError(str(exc)) from None
            embedded.extend(row.get("knowability") for row in result.canonical_data)
            if result.provenance.get("judgment_cutoff") is not None:
                context_cutoffs.append(result.provenance["judgment_cutoff"])
        for rows in market.values():
            embedded.extend(row.get("knowability") for row in _sequence(rows, "canonical records")
                            if isinstance(row, Mapping))
        cutoffs = context_cutoffs
        try:
            if judgment_cutoff is not None:
                aware_clock(judgment_cutoff)
                cutoffs.append(judgment_cutoff)
            for times in embedded:
                if times is not None:
                    validate_times(times)
                    fact = times["judgment_cutoff"]
                    if fact["value"] is not None:
                        if fact["status"] != "VERIFIED":
                            raise ValueError("unverified judgment cutoff")
                        cutoffs.append(fact["value"])
            if cutoffs and any(aware_clock(value) != aware_clock(cutoffs[0]) for value in cutoffs):
                raise ValueError("conflicting judgment cutoffs")
        except (ValueError, TypeError) as exc:
            raise AnalysisInputError(str(exc)) from None
        judgment_cutoff = cutoffs[0] if cutoffs else None

        def historical_gate(row, path, identifiers, matched):
            if judgment_cutoff is None:
                return None
            times = row.get("knowability") or time_facts(
                period=row.get("report_date", row.get("trade_date", row.get("quote_timestamp"))),
                period_basis="canonical.data_period", disclosure=TimeFact(None, "canonical.disclosure_missing"),
                acquired_at=None, judgment_cutoff=judgment_cutoff)
            times = safe_json(times)
            times["judgment_cutoff"] = {"value": judgment_cutoff,
                "source_basis": "caller.judgment_cutoff", "status": "VERIFIED"}
            try:
                calendar = _PERIOD_TIMEZONE if "report_date" in row or "trade_date" in row else None
                reasons = list(cutoff_reasons(times, judgment_cutoff, period_timezone=calendar))
                period = row.get("report_date", row.get("trade_date", row.get("quote_timestamp")))
                if period is not None and times["data_period"]["value"] != period:
                    reasons.append("SOURCE_TIME_CONFLICT")
                acquired = times["acquired_at"]
                if acquired["status"] == "VERIFIED" and any(
                        aware_clock(item["request_timestamp"]) > aware_clock(acquired["value"])
                        for item in matched):
                    reasons.append("SOURCE_TIME_CONFLICT")
            except (ValueError, TypeError) as exc:
                raise AnalysisInputError(str(exc)) from None
            if reasons:
                restriction = {"path": path, "status": "UNVERIFIED",
                    "source": row.get("provider", row.get("source")),
                    "usable_at_cutoff": False, "reason_codes": sorted(set(reasons)),
                    "knowability": times, "evidence_ids": identifiers}
                restrictions.append(restriction)
                missing.add(f"{path}.knowability")
                incomplete.append(f"{path}: UNVERIFIED historical knowability; data excluded")
                warnings.add(f"{path}: UNVERIFIED historical knowability")
                return restriction
            row["knowability"] = times
            return None

        def historical_identity(row, identifiers):
            # stock_id is the caller's lookup key, not a claim that the security
            # existed at cutoff. The current identity contract has no historical
            # validity proof for any other field, including name and industry.
            for field in sorted(set(row) - {"stock_id"}):
                value = row.pop(field)
                if value is None:
                    continue
                path = f"stock_identity.{field}"
                restrictions.append({"path": path, "status": "UNVERIFIED",
                    "source": None, "usable_at_cutoff": False,
                    "reason_codes": ["IDENTITY_ATTRIBUTE_TIME_UNVERIFIED"],
                    "evidence_ids": identifiers})
                missing.add(path)
                warnings.add(f"{path}: UNVERIFIED historical validity")
                incomplete.append(f"{path}: historical validity unverified; attribute excluded")

        def attach(row: dict[str, Any], path: str, source: str | None = None,
                   *, information_result: Any = None) -> dict[str, Any] | None:
            record_status = row.get("data_quality_status")
            addressed.add(path)
            identifiers = _sequence(links.get(path, []), "evidence links")
            for identifier in identifiers:
                _text(identifier, "evidence reference")
            identifiers = sorted(set(identifiers))
            matched = [evidence[identifier] for identifier in identifiers if identifier in evidence]
            if information_result is None and any(item["validation_result"]["status"] == "FAIL" for item in matched):
                raise AnalysisInputError("FAILED evidence cannot support business input")
            if source is not None and any(item["source"] != source for item in matched):
                raise AnalysisInputError("evidence source does not match canonical source")
            if not identifiers or len(matched) != len(identifiers) or any(item["validation_result"]["status"] != "PASS" for item in matched):
                if record_status in QUALITY_STATUSES:
                    excluded_record_quality.append({"path": path, "status": record_status,
                        "reason": "evidence missing or not validated; data excluded"})
                missing.add(f"{path}.evidence")
                incomplete.append(f"{path}: evidence missing or not validated; data excluded")
                return None
            if information_result is not None:
                typed = {item.evidence_id: item for item in evidence_metadata if isinstance(item, EvidenceRecord)}
                for identifier in identifiers:
                    try:
                        validate_information_evidence(information_result, typed.get(identifier))
                    except InformationNormalizationError as exc:
                        raise AnalysisInputError(str(exc)) from None
            if path == "stock_identity" and judgment_cutoff is not None:
                historical_identity(row, identifiers)
            elif historical_gate(row, path, identifiers, matched) is not None:
                return None
            used.update(identifiers)
            entry = {"data": row, "evidence_ids": identifiers}
            if path == "stock_identity" and judgment_cutoff is not None:
                entry["identity_scope"] = "LOOKUP_KEY_ONLY"
            return entry

        identity_entry = attach(identity, "stock_identity")
        identity_block = _block([identity_entry] if identity_entry else [], reason=None if identity_entry else "Evidence unavailable")
        holding_block = None
        if holding_snapshot is not None:
            holding = _mapping(holding_snapshot, "holding_snapshot")
            required = {"stock_id", "quantity", "cost_price", "snapshot_time"}
            optional = {"market_value", "profit_loss", "profit_loss_pct"}
            if not required <= set(holding) or set(holding) - required - optional:
                raise AnalysisInputError("holding snapshot must use explicit frozen holding fields")
            if holding["stock_id"] != stock_id:
                raise AnalysisInputError("holding stock identity mismatch")
            _timestamp(holding["snapshot_time"])
            for name in required - {"stock_id", "snapshot_time"} | optional & set(holding):
                _number(holding[name], name, nullable=True, negative=name in {"profit_loss", "profit_loss_pct"})
                if holding[name] is None:
                    missing.add(f"holding_snapshot.{name}")
                    incomplete.append(f"holding_snapshot.{name}: missing snapshot value")
            # Business input provenance is distinct from the three external
            # Evidence sources; never use Provider Evidence for quantity/cost.
            holding_block = _block([{"data": holding, "input_source": "portfolio_positions"}])
            if judgment_cutoff is not None and aware_clock(holding["snapshot_time"]) > aware_clock(judgment_cutoff):
                holding_block = _block([], reason="Snapshot after judgment cutoff", absent_status="UNVERIFIED")
                missing.add("holding_snapshot")
                incomplete.append("holding_snapshot: snapshot after judgment cutoff; data excluded")

        blocks = {}
        for kind in ("daily_kline", "market_quotes"):
            records = _sequence(market.get(kind, []), kind)
            if kind == "market_quotes" and len(records) > 1:
                raise AnalysisInputError("market_quotes must be an explicitly selected single snapshot")
            entries, unique = [], set()
            for number, row in enumerate(records):
                row = _mapping(row, "canonical record")
                path = f"{kind}.{number}"
                warnings.update(f"{path}: {warning}" for warning in _validate_market(row, kind, stock_id))
                provider = row["provider"]
                if sources[provider] not in QUALITY_STATUSES:
                    raise AnalysisInputError("data contradicts its missing, unavailable or FAILED source")
                if kind == "daily_kline" and provider != DAILY_SOURCE:
                    if provider != QUOTE_SOURCE or fallback is None:
                        raise AnalysisInputError("implicit daily fallback or cross-role source is forbidden")
                key = (row["trade_date"], provider) if kind == "daily_kline" else row["quote_timestamp"]
                if key in unique:
                    raise AnalysisInputError("duplicate canonical record identity")
                unique.add(key)
                for name, value in row.items():
                    if value is None:
                        missing.add(f"{path}.{name}")
                if row.get("freshness") in {"STALE", "UNKNOWN"}:
                    stale.add(path)
                if row["data_quality_status"] != "PASS":
                    warnings.add(f"{path}: {row['data_quality_status']}")
                entry = attach(row, path, provider)
                if entry:
                    entries.append(entry)
            primary = DAILY_SOURCE if kind == "daily_kline" else QUOTE_SOURCE
            absent_status = sources[primary] if sources[primary] in {"MISSING", "NOT_AVAILABLE"} else "MISSING"
            blocks[kind] = _block(entries, reason=None if entries else "No evidenced canonical data", absent_status=absent_status)
        if fallback is not None and not any(entry["data"]["provider"] == QUOTE_SOURCE for entry in blocks["daily_kline"]["records"]) and not any(
                item["path"].startswith("daily_kline.") and item["source"] == QUOTE_SOURCE for item in restrictions):
            raise AnalysisInputError("fallback_used must describe retained official daily data")
        availability = _mapping(information_availability if information_availability is not None else {}, "information_availability")
        if set(availability) - set(INFORMATION_FIELDS):
            raise AnalysisInputError("unknown information category")
        if not isinstance(canonical_information, (tuple, list)):
            raise AnalysisInputError("canonical_information must be a sequence of gate batches")
        batches = {field: [] for field in INFORMATION_FIELDS}
        for result in canonical_information:
            try:
                validate_information_result(result)
            except InformationNormalizationError as exc:
                raise AnalysisInputError(str(exc)) from None
            if result.provenance["stock_id"] != stock_id:
                raise AnalysisInputError("information stock identity mismatch")
            batches[result.category].append(result)
        information = {}
        for field in INFORMATION_FIELDS:
            entries, unique, number = [], set(), 0
            has_records = any(result.canonical_data for result in batches[field])
            if has_records and sources[INFORMATION_SOURCE] not in QUALITY_STATUSES:
                raise AnalysisInputError("information contradicts its source availability")
            if has_records and field in availability:
                raise AnalysisInputError("canonical information conflicts with missing availability")
            for result in batches[field]:
                for row in result.canonical_data:
                    row = _mapping(row, "canonical information record")
                    path = f"{field}.{number}"
                    number += 1
                    key = sha256(stable_json(row).encode("utf-8")).hexdigest()
                    if key in unique:
                        raise AnalysisInputError("duplicate canonical information record")
                    unique.add(key)
                    for name, value in row.items():
                        if value is None:
                            missing.add(f"{path}.{name}")
                    warnings.add(f"{path}: {row['data_quality_status']}")
                    entry = attach(row, path, INFORMATION_SOURCE, information_result=result)
                    if entry:
                        entries.append(entry)
            if has_records:
                information[field] = _block(entries, reason=None if entries else "No PASS information Evidence")
                if not entries:
                    missing.add(field)
                continue
            declaration = availability.get(field, {"status": "MISSING", "reason": "No evidenced canonical information"})
            declaration = _mapping(declaration, "information availability")
            if set(declaration) != {"status", "reason"} or _text(declaration["status"], "information availability status") not in {"MISSING", "NOT_AVAILABLE"}:
                raise AnalysisInputError("absent information requires MISSING/NOT_AVAILABLE")
            _text(declaration["reason"], "missing information reason")
            information[field] = _block([], reason=declaration["reason"], absent_status=declaration["status"])
            missing.add(field)
            incomplete.append(f"{field}: {declaration['status']}; {declaration['reason']}")

        if set(links) - addressed:
            raise AnalysisInputError("evidence link does not address an input block")

        for field, block in blocks.items():
            if not block["records"]:
                missing.add(field)
        if not blocks["daily_kline"]["records"]:
            incomplete.append("daily_kline: historical canonical data missing")
        if any(item.startswith("daily_kline") for item in reported_missing):
            # Conservatively fail closed on reported historical gaps. No
            # trading calendar, interpolation, or completeness inference.
            incomplete.append("daily_kline: historical gaps explicitly reported")
        quotes = blocks["market_quotes"]["records"]
        realtime = "MISSING"
        if quotes and quotes[0]["data"]["freshness"] == "REALTIME" and "market_quotes.0" not in stale:
            realtime = quotes[0]["data"]["data_quality_status"]
        for source, status in sources.items():
            if status in {"WARNING", "UNVERIFIED"}:
                warnings.add(f"{source}: {status}")
        # Aggregate typed statuses; prose warnings are reasons, not a status parser.
        quality_statuses = set(sources.values())
        quality_statuses.update(block["status"] for block in blocks.values())
        quality_statuses.update(block["status"] for block in information.values())
        quality_statuses.update(item["status"] for item in restrictions)
        quality_statuses.update(item["status"] for item in excluded_record_quality)
        quality = {
            "aggregation_rule": "UNVERIFIED > WARNING > PASS; WARNINGS_IMPLY_WARNING; AVAILABILITY_RETAINED_SEPARATELY",
            "source_status": sources, "DATA_AVAILABILITY_REPORT": sources,
            "missing_fields": sorted(missing), "stale_fields": sorted(stale),
            "fallback_used": fallback, "validation_warnings": sorted(warnings),
            "REALTIME_QUOTE_STATUS": realtime,
            "data_quality_status": "UNVERIFIED" if "UNVERIFIED" in quality_statuses else "WARNING" if warnings or "WARNING" in quality_statuses else "PASS",
            "package_status": "INCOMPLETE" if incomplete else "COMPLETE",
            "complete_analysis_input": not incomplete and not any(field in missing for field in INFORMATION_FIELDS),
            "incomplete_reasons": sorted(set(incomplete)),
            "excluded_record_quality": excluded_record_quality,
        }
        if judgment_cutoff is not None:
            quality["data_period_calendar"] = "UTC+08:00; canonical trade/report calendar dates"
            quality["judgment_cutoff"] = {"value": judgment_cutoff,
                "source_basis": "caller.judgment_cutoff", "status": "VERIFIED"}
            quality["knowability_restrictions"] = restrictions
        # Missing information keeps the full seven-input package incomplete,
        # while evidenced historical analysis remains usable.
        if not quality["complete_analysis_input"]:
            quality["package_status"] = "INCOMPLETE"
        package = AnalysisPackage(
            stock_id=stock_id, position_mode="WATCH_ONLY" if holding_snapshot is None else "HOLDING_ANALYSIS",
            stock_identity=identity_block, holding_snapshot=holding_block,
            latest_quote=blocks["market_quotes"], daily_kline_summary=blocks["daily_kline"],
            data_quality=quality, evidence_index=tuple(evidence[identifier] for identifier in sorted(used)),
            **information,
        )
        object.__setattr__(package, "_gate_digest", sha256(package.to_json().encode("utf-8")).hexdigest())
        return package


def validate_package(package: AnalysisPackage) -> None:
    """Only render an immutable package actually returned by this gate.

    The private digest is excluded from the public contract and cannot be set
    through the dataclass constructor or carried by dataclasses.replace(). It
    is an in-process integrity check, not authentication of external data.
    Persisted input must re-enter AnalysisPackageBuilder, never be deserialized
    directly into a trusted package. Recursive immutability prevents changes
    after the gate (including mutations of the caller's original mappings).
    """
    if type(package) is not AnalysisPackage or package._gate_digest is None:
        raise AnalysisInputError("PromptBuilder requires a builder-validated AnalysisPackage")
    if sha256(package.to_json().encode("utf-8")).hexdigest() != package._gate_digest:
        raise AnalysisInputError("package changed after canonical assembly")
