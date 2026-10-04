"""Deterministic mapping of G0-audited TDX information to a read model.

No network, filesystem writes, model calls, summaries or metric derivations.
Original response bytes are supplied by the caller and remain the fact source.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import fields, is_dataclass
from datetime import date, datetime
from typing import Any, Mapping

from evidence.builder import sha256_digest
from evidence.contracts import EvidenceRecord, EvidenceSource, ValidationStatus
from evidence.contracts import TimeFact
from evidence.knowability import disclosure_fact, time_facts, aware_clock
from providers.contract import ProviderEnvelope, ProviderStatus
from providers.tdx_information import TdxInformationProvider, _json

from .semantics import RULE_VERSION
from .contracts import DataQualityStatus, NormalizedRecord, ValidationResult
from .information_contracts import (
    FINANCIAL_METRICS, SOURCE, STATEMENT_ENTRIES, TOOL_CATEGORIES, TOOL_KINDS,
    InformationNormalizationError, InformationNormalizationResult, freeze_information,
    InformationNormalizationRule, InformationRuleId,
)


def _reject(message: str) -> None:
    raise InformationNormalizationError(message)


def _clock(value: Any, *, nullable: bool = False) -> None:
    if nullable and value is None:
        return
    try:
        if not isinstance(value, str) or datetime.fromisoformat(value.replace("Z", "+00:00")).tzinfo is None:
            raise ValueError
    except ValueError:
        _reject("evidence clock must include a valid timezone")


def _response(raw_bytes: bytes) -> dict:
    """Decode JSON or a single archived SSE result without changing raw bytes.

    #006 R1 archives also contain literal backslash-n SSE separators. Decode
    only the outer framing; never unescape the JSON business content globally.
    Ambiguous/multiple results are rejected, not selected heuristically.
    """
    text = raw_bytes.decode("utf-8")
    if text.startswith(("event:", "data:")):
        starts = list(re.finditer(r"(?:^|\n|\\n)data: *", text))
        messages = []
        for start in starts:
            value, end = json.JSONDecoder().raw_decode(text[start.end():])
            tail = text[start.end() + end:]
            if tail.strip("\r\n ") not in ("", "\\n", "\\n\\n"):
                _reject("ambiguous archived SSE response")
            messages.append(value)
        if len(messages) != 1:
            _reject("archived SSE must contain exactly one response")
        # Apply the provider's duplicate-key / finite-number JSON checks too.
        value = _json(text[starts[0].end():starts[0].end() + end])
    else:
        value = _json(text)
    if not isinstance(value, dict):
        _reject("business response must be an object")
    if "jsonrpc" in value:
        if value.get("jsonrpc") != "2.0" or "error" in value or not isinstance(value.get("result"), dict):
            _reject("invalid archived RPC result")
        value = value["result"]
    return value


def _optional_text(value: Any) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        _reject("audited text field has an invalid type")
    return value


def _summary(value: Any) -> dict:
    summary = _optional_text(value)
    return {"source_supplied_summary": summary,
            "SOURCE_SUPPLIED_SUMMARY": "YES" if summary is not None else "NO",
            "MODEL_GENERATED_SUMMARY": "NO"}


def _items(data: Any, stock: str, *, research: bool) -> list[dict]:
    if not isinstance(data, list):
        _reject("information response must be the audited tabular array")
    if not data:
        return []
    header = data[0]
    if (not isinstance(header, list) or any(not isinstance(name, str) for name in header)
            or len(set(header)) != len(header) or "标题" not in header):
        _reject("audited title column is missing or ambiguous")
    records = []
    for row in data[1:]:
        if not isinstance(row, list) or len(row) != len(header):
            _reject("invalid information table row")
        fields = dict(zip(header, row))
        title = fields["标题"]
        if not isinstance(title, str) or not title.strip():
            _reject("canonical title must be nonempty text")
        # 时间 lacks publication semantics; 来源 lacks institution semantics.
        # Neither links, article provenance nor body text is repurposed.
        record = {"stock_id": stock, "title": title, "source": SOURCE,
                  "data_quality_status": "UNVERIFIED", **_summary(fields.get("摘要"))}
        if research:
            record.update(institution=None, rating=None, publish_date=None)
        else:
            record.update(content=None, publish_time=None)
        records.append(record)
    return records


def _financial(data: Any, stock: str, view: str, *, acquired_at=None, judgment_cutoff=None) -> list[dict]:
    if data == []:
        return []
    if not isinstance(data, dict):
        _reject("financial response must contain audited statements")
    transformed = data.get("transformed")
    if not isinstance(transformed, dict) or transformed.get("parser") != "statements":
        _reject("unsupported financial parser")
    tables = transformed.get("tables")
    if (not isinstance(tables, list) or len(tables) != 1
            or not isinstance(tables[0], dict) or tables[0].get("name") != "statements"
            or tables[0].get("layout") != "table" or not isinstance(tables[0].get("rows"), list)):
        _reject("unsupported financial table shape")
    summary = _summary(transformed.get("summary"))
    records, dates = [], set()
    for row in tables[0]["rows"]:
        if not isinstance(row, dict):
            _reject("invalid financial row")
        report_date = row.get("日期")
        try:
            if not isinstance(report_date, str) or date.fromisoformat(report_date).isoformat() != report_date:
                raise ValueError
        except ValueError:
            _reject("audited report_date is missing or invalid")
        if report_date in dates:
            _reject("duplicate statement report_date")
        dates.add(report_date)
        disclosure = disclosure_fact(row)
        # G0 supplies no units. No magnitude-based conversion or cash-flow
        # definition is safe. Nullable metrics stay NULL, even when raw has a
        # numeric candidate. Future mappings require a new audited contract.
        records.append({"stock_id": stock, "report_date": report_date,
                        "publish_time": disclosure.value,
                        "knowability": time_facts(period=report_date, period_basis="statement.row.日期",
                            disclosure=disclosure, acquired_at=acquired_at,
                            judgment_cutoff=judgment_cutoff),
                        **{metric: None for metric in FINANCIAL_METRICS},
                        "provider": SOURCE, "statement_view": view,
                        "data_quality_status": "UNVERIFIED", **summary})
    return records


def _seal_value(value: Any) -> Any:
    """Serialize frozen dataclasses without deepcopying their mapping proxies."""
    if is_dataclass(value):
        return {field.name: _seal_value(getattr(value, field.name)) for field in fields(value)}
    if isinstance(value, Mapping):
        return {key: _seal_value(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return tuple(_seal_value(item) for item in value)
    return value


def _digest(result: InformationNormalizationResult) -> str:
    return sha256_digest(_seal_value({"category": result.category,
                          "canonical_data": result.canonical_data,
                          "normalized_data": result.normalized_data,
                          "provenance": result.provenance}))


def _normalization_rules(tool: str, view: str | None) -> tuple[InformationNormalizationRule, ...]:
    rules = [("source_supplied_summary", InformationRuleId.INFO_SOURCE_SUMMARY_EXACT_COPY),
             ("knowability", InformationRuleId.KNOWABILITY_FOUR_CLOCKS)]
    if view:
        rules.extend([
            ("report_date", InformationRuleId.FIN_REPORT_DATE_EXACT_ISO_DATE),
            ("publish_time", InformationRuleId.FIN_DISCLOSURE_TIME_EXPLICIT_FIELD),
            ("statement_view", InformationRuleId.FIN_STATEMENT_VIEW_EXACT_ENTRY),
            *((metric, InformationRuleId.FIN_METRIC_UNIT_UNVERIFIED_NULL) for metric in FINANCIAL_METRICS),
        ])
    else:
        research = tool == "wenda_report_query"
        rules.extend([
            ("title", InformationRuleId.INFO_TITLE_EXACT_COPY),
            ("publish_date" if research else "publish_time",
             InformationRuleId.INFO_PUBLICATION_TIME_UNVERIFIED_NULL),
        ])
        if research:
            rules.append(("institution", InformationRuleId.INFO_INSTITUTION_UNVERIFIED_NULL))
    return tuple(InformationNormalizationRule(field, rule) for field, rule in rules)


def validate_information_result(result: Any) -> None:
    if (type(result) is not InformationNormalizationResult or result._gate_digest is None
            or result._gate_digest != _digest(result)):
        _reject("canonical information requires an unchanged InformationNormalizer gate result")
    for row in result.canonical_data:
        if row.get("rule_version") != RULE_VERSION:
            _reject("unsupported information rule version; canonical data must not be reinterpreted")


def validate_information_evidence(result: InformationNormalizationResult, evidence: Any) -> None:
    """Check the full batch hash and the append-only link to original #006 raw."""
    validate_information_result(result)
    if type(evidence) is not EvidenceRecord:
        _reject("information requires typed source Evidence")
    p = result.provenance
    if evidence.source != EvidenceSource.WORKBUDDY_TDX_MCP:
        _reject("information Evidence source mismatch")
    if evidence.evidence_id == p["source_evidence_id"]:
        _reject("canonical validation must append a new Evidence record")
    if (evidence.request_params != p["request_params"]
            or evidence.endpoint_or_tool != p["endpoint_or_tool"]):
        _reject("information Evidence stock/tool/category mismatch")
    if (evidence.raw_response_hash != p["raw_response_hash"]
            or evidence.raw_response_path != p["raw_response_path"]
            or evidence.request_timestamp != p["request_timestamp"]
            or evidence.provider_timestamp != p["provider_timestamp"]
            or evidence.supersedes_evidence_id != p["source_evidence_id"]
            or evidence.normalized_data_hash != sha256_digest(result.canonical_data)):
        _reject("information Evidence hash or original binding mismatch")


class InformationNormalizer:
    """Validate one business response against its original append-only Evidence."""

    def normalize(self, envelope: ProviderEnvelope, *, source_evidence: EvidenceRecord,
                  raw_bytes: bytes, acquired_at: str | None = None,
                  judgment_cutoff: str | None = None) -> InformationNormalizationResult:
        try:
            for value in (acquired_at, judgment_cutoff):
                if value is not None:
                    aware_clock(value)
            return self._normalize(envelope, source_evidence, raw_bytes, acquired_at, judgment_cutoff)
        except InformationNormalizationError:
            raise
        except Exception:
            # Never include raw content, request values or credentials in errors.
            raise InformationNormalizationError("information input failed shape validation") from None

    def _normalize(self, envelope, evidence, raw_bytes, acquired_at, judgment_cutoff):
        if (type(envelope) is not ProviderEnvelope or envelope.provider_name != SOURCE
                or envelope.status not in {ProviderStatus.AVAILABLE, ProviderStatus.NOT_AVAILABLE}):
            _reject("unsupported information Provider envelope")
        if (type(evidence) is not EvidenceRecord or evidence.source != EvidenceSource.WORKBUDDY_TDX_MCP
                or not isinstance(evidence.evidence_id, str) or not evidence.evidence_id.strip()
                or evidence.validation_result.status not in {ValidationStatus.PASS, ValidationStatus.NOT_VALIDATED}
                or evidence.validation_result.failures):
            _reject("invalid original source Evidence")
        if (type(raw_bytes) is not bytes or not raw_bytes or len(raw_bytes) > 1024 * 1024
                or hashlib.sha256(raw_bytes).hexdigest() != evidence.raw_response_hash
                or not evidence.raw_response_path or envelope.raw_reference != evidence.raw_response_path):
            _reject("original raw Evidence bytes/path/hash mismatch")
        _clock(evidence.request_timestamp)
        _clock(evidence.provider_timestamp, nullable=True)
        if (envelope.request_timestamp != evidence.request_timestamp
                or envelope.provider_timestamp != evidence.provider_timestamp):
            _reject("Provider and Evidence clocks mismatch")
        payload = envelope.payload
        if not isinstance(payload, Mapping):
            _reject("information payload must be structured")
        stock, tool = payload.get("stock_id"), payload.get("tool")
        if not isinstance(stock, str) or not re.fullmatch(r"[0-9]{6}\.(SH|SZ|BJ)", stock):
            _reject("canonical stock identity is required")
        if tool not in TOOL_CATEGORIES or envelope.data_kind != TOOL_KINDS[tool]:
            _reject("tool/category is outside the supported core")
        params = evidence.request_params
        call = params.get("params", {})
        if (params.get("method") != "tools/call" or params.get("stock_id") != stock
                or call.get("name") != tool or evidence.endpoint_or_tool.split("#")[-1] != tool):
            _reject("original Evidence stock/tool/category mismatch")
        arguments = call.get("arguments")
        if not isinstance(arguments, Mapping) or payload.get("arguments") != arguments:
            _reject("Provider and source Evidence arguments mismatch")
        view = None
        if tool == "tdx_api_data":
            view = STATEMENT_ENTRIES.get(arguments.get("entry"))
            if view is None or arguments.get("code") != stock[:6]:
                _reject("unsupported financial view or stock mismatch")
            expected_keys = {"entry", "code"} if view == "balance" else {"entry", "code", "fixedTag"}
            if (set(arguments) != expected_keys
                    or view != "balance" and arguments.get("fixedTag") != "00101"):
                _reject("financial arguments differ from the exact audited wire contract")
        elif "symbol" in arguments:
            if arguments["symbol"] != stock[:6]:
                _reject("wire stock identity mismatch")
        elif not (tool == "wenda_report_query" and arguments == {"query": "吉林高速"}
                  and stock == "601518.SH"):
            # The supplemental real G0 archive has a name query, not symbol.
            # This exact audited association is the only allowed exception.
            _reject("source request lacks an audited stock association")
        response = _response(raw_bytes)
        if response.get("tool_name", tool) != tool:
            _reject("response tool identity mismatch")
        if "query" in arguments and "query" in response and response["query"] != arguments["query"]:
            _reject("response query mismatch")
        data = TdxInformationProvider._data(response, tool=tool)
        if data != payload.get("data"):
            _reject("Provider data differs from original raw Evidence")
        records = (_financial(data, stock, view, acquired_at=acquired_at, judgment_cutoff=judgment_cutoff)
                   if view else _items(data, stock, research=tool == "wenda_report_query"))
        if not view:
            for row in records:
                row["knowability"] = time_facts(period=None, period_basis="information.period_missing",
                    disclosure=TimeFact(None, "information.publication_semantics_unverified"),
                    acquired_at=acquired_at, judgment_cutoff=judgment_cutoff)
        if acquired_at is not None and aware_clock(acquired_at) < aware_clock(evidence.request_timestamp):
            _reject("acquisition precedes source request")
        if (bool(records) and envelope.status != ProviderStatus.AVAILABLE
                or not records and envelope.status != ProviderStatus.NOT_AVAILABLE):
            _reject("information availability contradicts response records")
        rules = _normalization_rules(tool, view)
        for row in records:
            row.update(period="REPORTING_PERIOD_UNVERIFIED" if view else "EVENT_PERIOD_UNVERIFIED",
                field_semantics={key: "UNVERIFIED" if value is None else "AUDITED_" + key.upper()
                    for key, value in row.items() if key != "knowability"},
                conversion_source=evidence.evidence_id, rule_version=RULE_VERSION,
                conversion_chain=[{"field": rule.field, "rule": rule.rule.value,
                    "source": evidence.evidence_id, "rule_version": RULE_VERSION} for rule in rules])
        canonical = freeze_information(records)
        normalized = tuple(NormalizedRecord(row, freeze_information({}), rules,
                            ValidationResult(True, DataQualityStatus.UNVERIFIED,
                                             warnings=("Nullable semantics remain UNVERIFIED",)))
                           for row in canonical)
        provenance = {"stock_id": stock, "tool": tool, "view": view,
                      "judgment_cutoff": judgment_cutoff,
                      "source_evidence_id": evidence.evidence_id,
                      "endpoint_or_tool": evidence.endpoint_or_tool,
                      "request_params": evidence.request_params,
                      "request_timestamp": evidence.request_timestamp,
                      "provider_timestamp": evidence.provider_timestamp,
                      "raw_response_path": evidence.raw_response_path,
                      "raw_response_hash": evidence.raw_response_hash}
        result = InformationNormalizationResult(TOOL_CATEGORIES[tool], canonical, normalized, provenance)
        object.__setattr__(result, "_gate_digest", _digest(result))
        return result
