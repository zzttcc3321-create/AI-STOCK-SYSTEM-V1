"""Immutable, JSON-only contracts for offline analysis input assembly."""

from __future__ import annotations

import json
import math
import re
from dataclasses import dataclass, field, fields
from enum import Enum
from types import MappingProxyType
from typing import Any, Mapping


SOURCES = frozenset({"TUSHARE_MCP", "TDX_OFFICIAL_LOCAL_HTTP", "WORKBUDDY_TDX_MCP"})
INFORMATION_FIELDS = (
    "recent_announcements", "recent_news", "financial_summary",
    "valuation_summary", "sector_context", "research_summary",
)
QUALITY_STATUSES = frozenset({"PASS", "WARNING", "UNVERIFIED"})
AVAILABILITY_STATUSES = QUALITY_STATUSES | {"MISSING", "NOT_AVAILABLE", "FAILED"}
_FORBIDDEN = re.compile(
    r"raw(?:response|bytes|data|value|unit|reference)|payload|provider(?:envelope|name)|"
    r"normalizeddata|authorization|credential|apikey|cookie|session|password|secret|token",
    re.IGNORECASE,
)
_SENSITIVE_TEXT = re.compile(
    r"raw[_ -]?(?:response|bytes|data)|credential|api[_ -]?key|cookie|session|"
    r"token|authorization|bearer\s+\S+|password|secret|"
    r"-----BEGIN [A-Z ]*PRIVATE KEY-----|"
    r"\b(?:AKIA|ASIA)[A-Z0-9]{16}\b|\bsk-[A-Za-z0-9_-]{12,}|"
    r"\bgh[pousr]_[A-Za-z0-9]{20,}",
    re.IGNORECASE,
)


class AnalysisInputError(ValueError):
    """Input crosses the canonical, source, validation, or privacy boundary."""


def safe_json(value: Any) -> Any:
    """Copy JSON values, rejecting opaque objects, raw fields and sensitive text.

    No repr(), coercion, provider object inspection, or byte decoding is allowed.
    Errors deliberately omit input values.
    """
    if isinstance(value, Enum):
        return safe_json(value.value)
    if isinstance(value, Mapping):
        result = {}
        for key, item in value.items():
            if not isinstance(key, str) or _FORBIDDEN.search(re.sub(r"[^a-zA-Z0-9]", "", key)):
                raise AnalysisInputError("forbidden or non-text input field")
            result[key] = safe_json(item)
        return result
    if isinstance(value, (tuple, list)):
        return [safe_json(item) for item in value]
    if isinstance(value, str):
        if _SENSITIVE_TEXT.search(value):
            raise AnalysisInputError("sensitive or raw text is forbidden")
        return value
    if value is None or type(value) in (bool, int):
        return value
    if type(value) is float and math.isfinite(value):
        return value
    raise AnalysisInputError("only finite JSON canonical values are accepted")


def freeze(value: Any) -> Any:
    if isinstance(value, Mapping):
        return MappingProxyType({key: freeze(item) for key, item in value.items()})
    if isinstance(value, (tuple, list)):
        return tuple(freeze(item) for item in value)
    return value


def jsonable(value: Any) -> Any:
    if isinstance(value, (AnalysisPackage, PromptEnvelope)):
        return {field.name: jsonable(getattr(value, field.name)) for field in fields(value) if not field.name.startswith("_")}
    if isinstance(value, Mapping):
        return {key: jsonable(item) for key, item in value.items()}
    if isinstance(value, (tuple, list)):
        return [jsonable(item) for item in value]
    return value


def stable_json(value: Any) -> str:
    return json.dumps(jsonable(value), ensure_ascii=False, sort_keys=True,
                      separators=(",", ":"), allow_nan=False)


@dataclass(frozen=True, slots=True)
class AnalysisPackage:
    stock_id: str
    position_mode: str
    stock_identity: Mapping[str, Any]
    holding_snapshot: Mapping[str, Any] | None
    latest_quote: Mapping[str, Any]
    daily_kline_summary: Mapping[str, Any]
    recent_announcements: Mapping[str, Any]
    recent_news: Mapping[str, Any]
    financial_summary: Mapping[str, Any]
    valuation_summary: Mapping[str, Any]
    sector_context: Mapping[str, Any]
    research_summary: Mapping[str, Any]
    data_quality: Mapping[str, Any]
    evidence_index: tuple[Mapping[str, Any], ...]
    _gate_digest: str | None = field(default=None, init=False, repr=False, compare=False)

    def __post_init__(self) -> None:
        for field in fields(self):
            object.__setattr__(self, field.name, freeze(safe_json(getattr(self, field.name))))

    @property
    def analysis_mode(self) -> str:
        return "WATCH_ANALYSIS" if self.position_mode == "WATCH_ONLY" else "HOLDING_ANALYSIS"

    def to_dict(self) -> dict[str, Any]:
        return jsonable(self)

    def to_json(self) -> str:
        return stable_json(self)


@dataclass(frozen=True, slots=True)
class PromptEnvelope:
    prompt_version: str
    stock_id: str
    position_mode: str
    analysis_mode: str
    system_prompt: str
    user_prompt: str
    evidence_ids: tuple[str, ...]
    input_hash: str
    template_hash: str
    NETWORK_CALL_COUNT: int = 0
    MODEL_CALL_COUNT: int = 0
    DATABASE_WRITE: int = 0

    def to_dict(self) -> dict[str, Any]:
        return jsonable(self)

    def to_json(self) -> str:
        return stable_json(self)
