"""Offline builder for immutable evidence metadata."""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, is_dataclass
from datetime import datetime, timedelta, timezone
from enum import Enum
from types import MappingProxyType
from typing import Any, Mapping
from uuid import uuid4

from .contracts import (
    EvidenceMissingEvent, EvidenceRecord, EvidenceSource, EvidenceValidationResult,
    HumanConfirm, ValidationStatus,
)

SENSITIVE_KEYS = frozenset({"token", "password", "cookie", "session", "apikey"})
SUCCESS_STATUSES = frozenset({"AVAILABLE", "PARTIAL"})


def _jsonable(value: Any) -> Any:
    if is_dataclass(value):
        return _jsonable(asdict(value))
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, Mapping):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(item) for item in value]
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    return repr(value)


def sha256_digest(value: Any) -> str:
    payload = json.dumps(_jsonable(value), ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def sanitize_request_params(params: Mapping[str, Any]) -> Mapping[str, Any]:
    """Recursively discard forbidden keys; no value or mask is retained."""

    def clean(value: Any) -> Any:
        if isinstance(value, Mapping):
            return MappingProxyType({
                str(k): clean(v) for k, v in value.items()
                if "".join(char for char in str(k).lower() if char.isalnum()) not in SENSITIVE_KEYS
            })
        if isinstance(value, (list, tuple)):
            return tuple(clean(item) for item in value)
        return value

    return clean(params)


def _get(value: Any, name: str, default: Any = None) -> Any:
    return value.get(name, default) if isinstance(value, Mapping) else getattr(value, name, default)


def _utc8(timestamp: str) -> str:
    parsed = datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("request_timestamp must include a timezone")
    return parsed.astimezone(timezone(timedelta(hours=8))).isoformat()


def _rules(result: Any) -> tuple[str, ...] | None:
    if result is None:
        return None
    rules: list[str] = []
    for record in _get(result, "normalized_data", ()):
        for item in _get(record, "normalization_rule", ()):
            rule = _get(item, "rule")
            rule_value = rule.value if isinstance(rule, Enum) else rule
            rules.append(f"{_get(item, 'field')}:{rule_value or 'UNVERIFIED'}")
    for row in _get(result, "canonical_data", ()):
        if "rule_version" in row:
            keys = ("period", "field_semantics", "conversion_source", "rule_version", "conversion_chain", "adjustment_basis")
            rules.append("CANONICAL_SEMANTICS:" + json.dumps(_jsonable({key: row[key] for key in keys if key in row}),
                ensure_ascii=False, sort_keys=True, separators=(",", ":")))
    series = _get(result, "metadata", {}).get("price_series")
    if series is not None:
        rules.append("CANONICAL_SEMANTICS:" + json.dumps({"price_series": series}, sort_keys=True, separators=(",", ":")))
    return tuple(rules) or None


def _validation(result: Any) -> EvidenceValidationResult:
    if result is None:
        return EvidenceValidationResult(ValidationStatus.NOT_VALIDATED)
    validations = [_get(record, "validation_result") for record in _get(result, "normalized_data", ())]
    failures = tuple(str(item) for validation in validations for item in _get(validation, "failures", ()))
    passed = bool(validations) and all(bool(_get(item, "passed")) for item in validations)
    return EvidenceValidationResult(ValidationStatus.PASS if passed else ValidationStatus.FAIL, failures)


class EvidenceBuilder:
    """Build records only; it deliberately has no update/delete or persistence API."""

    def __init__(self) -> None:
        self._missing_events: list[EvidenceMissingEvent] = []

    @property
    def missing_events(self) -> tuple[EvidenceMissingEvent, ...]:
        return tuple(self._missing_events)

    def build(
        self, *, fetch_result: Any, endpoint_or_tool: str, request_params: Mapping[str, Any],
        collection_task_id: str, system_run_id: str, normalization_result: Any = None,
        model_version: str | None = None, analysis_output: Any = None,
        human_confirm: HumanConfirm = HumanConfirm.PENDING,
        supersedes_evidence_id: str | None = None, evidence_id: str | None = None,
        raw_response_hash: str | None = None,
    ) -> EvidenceRecord:
        try:
            if analysis_output is not None and human_confirm is not HumanConfirm.CONFIRMED:
                raise ValueError("analysis output requires CONFIRMED human_confirm")
            source = EvidenceSource(_get(fetch_result, "provider_name"))
            raw_path = _get(fetch_result, "raw_reference")
            status = _get(fetch_result, "status")
            status = status.value if isinstance(status, Enum) else status
            if status in SUCCESS_STATUSES and not raw_path:
                raise ValueError("successful/PARTIAL fetch requires raw_response_path")
            if raw_path:
                expected_prefix = f"{system_run_id}/{collection_task_id}/"
                normalized_path = raw_path.removeprefix("file://").lstrip("/")
                if expected_prefix not in normalized_path:
                    raise ValueError("raw_response_path must contain system_run_id/collection_task_id/request_id")
            normalized = _get(normalization_result, "canonical_data") if normalization_result is not None else None
            return EvidenceRecord(
                source=source, endpoint_or_tool=endpoint_or_tool,
                request_params=sanitize_request_params(request_params),
                request_timestamp=_utc8(_get(fetch_result, "request_timestamp")),
                provider_timestamp=_get(fetch_result, "provider_timestamp"), raw_response_path=raw_path,
                normalization_rule=_rules(normalization_result), validation_result=_validation(normalization_result),
                collection_task_id=collection_task_id, system_run_id=system_run_id,
                model_version=model_version, human_confirm=human_confirm,
                raw_response_hash=(raw_response_hash or sha256_digest(_get(fetch_result, "payload"))) if raw_path else None,
                normalized_data_hash=sha256_digest(normalized) if normalized is not None else None,
                analysis_output_hash=sha256_digest(analysis_output) if analysis_output is not None else None,
                evidence_id=evidence_id or uuid4().hex, supersedes_evidence_id=supersedes_evidence_id,
            )
        except Exception as exc:
            self._missing_events.append(EvidenceMissingEvent(collection_task_id, str(exc)))
            raise
