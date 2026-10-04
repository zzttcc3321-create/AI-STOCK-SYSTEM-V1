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
