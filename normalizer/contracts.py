"""In-memory contracts for the three normalizer layers.

These objects intentionally contain no persistence behaviour.  In particular,
``raw_data`` is a snapshot and is never replaced by either later layer.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Mapping

# Shared four-clock sidecar contract; canonical JSON participates in Evidence
# normalized_data_hash without adding table columns or changing raw metadata.
from evidence.contracts import KnowabilityTimes, TimeFact


class DataQualityStatus(str, Enum):
    PASS = "PASS"
    WARNING = "WARNING"
    UNVERIFIED = "UNVERIFIED"
    FAILED = "FAILED"


class UnitStatus(str, Enum):
    VERIFIED = "VERIFIED"
    UNVERIFIED = "UNVERIFIED"


class ConversionRule(str, Enum):
    IDENTITY = "IDENTITY"
    LOT100_TO_SHARE = "LOT100_TO_SHARE"
    TEN_THOUSAND_YUAN_TO_YUAN = "TEN_THOUSAND_YUAN_TO_YUAN"


class Freshness(str, Enum):
    REALTIME = "REALTIME"
    STALE = "STALE"
    UNKNOWN = "UNKNOWN"


FRESHNESS_STALE_AFTER_SECONDS = 300
FRESHNESS_REQUEST_MAX_GAP_SECONDS = 300


@dataclass(frozen=True, slots=True)
class FreshnessCheck:
    """Quote freshness contract V1.0 (collection clocks, not disclosure).

    Reference priority: caller judgment_cutoff, caller acquired_at, envelope
    request_timestamp. No wall clock is inferred. Quote clock: row's explicit
    quote_timestamp, otherwise envelope provider_timestamp; explicit NULL stays
    missing. All comparisons require ISO timestamps with explicit timezones.
    Age > 300 seconds is STALE; equality is allowed. Future tolerance is zero,
    including quotes after an explicitly supplied acquisition completion time.
    Reference must be within [request, request + 300s]; quote must not be more
    than 300s after request. Otherwise context is inconsistent and UNKNOWN.
    Missing/invalid clocks and future clocks are UNKNOWN. Only an explicit
    REALTIME declaration passing every check can remain REALTIME. STALE and
    UNKNOWN declarations are never upgraded. Reasons and original clocks stay
    readable in canonical JSON and therefore its existing Evidence hash.
    """

    quote_timestamp: Any
    timestamp_source: str
    declared_freshness: Any
    request_timestamp: Any
    acquired_at: str | None
    judgment_cutoff: str | None
    reference_time: Any
    reference_source: str
    age_seconds: float | None
    reason_codes: tuple[str, ...]
    contract_version: str = "QUOTE_FRESHNESS_V1.0"
    stale_after_seconds: int = FRESHNESS_STALE_AFTER_SECONDS
    future_tolerance_seconds: int = 0
    request_context_max_gap_seconds: int = FRESHNESS_REQUEST_MAX_GAP_SECONDS


@dataclass(frozen=True, slots=True)
class NumericField:
    """The auditable five-tuple plus its unit verification state."""

    raw_value: Any
    raw_unit: str | None
    normalized_value: float | None
    normalized_unit: str
    conversion_rule: ConversionRule | None
    unit_status: UnitStatus


@dataclass(frozen=True, slots=True)
class NormalizationRule:
    field: str
    rule: ConversionRule | None
    input_value: NumericField


@dataclass(frozen=True, slots=True)
class ValidationResult:
    passed: bool
    status: DataQualityStatus
    failures: tuple[str, ...] = ()
    warnings: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class NormalizedRecord:
    values: Mapping[str, Any]
    numeric_fields: Mapping[str, NumericField]
    normalization_rule: tuple[NormalizationRule, ...]
    validation_result: ValidationResult


@dataclass(frozen=True, slots=True)
class MissingData:
    stock_id: str
    trade_date: str
    status: str = "MISSING"
    reason: str = "provider did not return an expected trading date"


@dataclass(frozen=True, slots=True)
class NormalizationResult:
    raw_data: Any
    normalized_data: tuple[NormalizedRecord, ...]
    canonical_data: tuple[Mapping[str, Any], ...]
    missing_data: tuple[MissingData, ...] = ()
    metadata: Mapping[str, Any] = field(default_factory=dict)
