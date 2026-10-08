"""Immutable, persistence-neutral contracts for the evidence pipeline."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Mapping


@dataclass(frozen=True, slots=True)
class TimeFact:
    """One independent clock and its semantic basis, never a fallback clock.

    value is an ISO date (period only) or an aware ISO timestamp. source_basis
    names the exact source field or caller context. Missing/ambiguous clocks
    remain UNVERIFIED; contradictory source clocks are CONFLICT.
    """

    value: str | None
    source_basis: str
    status: str = "UNVERIFIED"


@dataclass(frozen=True, slots=True)
class KnowabilityTimes:
    """JSON sidecar/read-model extension; does not change EvidenceRecord/DB.

    data_period: the period the observation belongs to, not publication.
    disclosed_at: when this version became public, not provider/request time.
    acquired_at: when this response was received, not request start time.
    judgment_cutoff: the caller's historical decision boundary.
    """

    data_period: TimeFact
    disclosed_at: TimeFact
    acquired_at: TimeFact
    judgment_cutoff: TimeFact


class EvidenceSource(str, Enum):
    TUSHARE_MCP = "TUSHARE_MCP"
    TDX_OFFICIAL_LOCAL_HTTP = "TDX_OFFICIAL_LOCAL_HTTP"
    WORKBUDDY_TDX_MCP = "WORKBUDDY_TDX_MCP"


class DurabilityLevel(str, Enum):
    """Archive sidecar/read-model state; never a database schema extension."""

    DIRECTORY_SYNC = "DIRECTORY_SYNC"
    FILE_SYNC_ONLY = "FILE_SYNC_ONLY"
    UNVERIFIED = "UNVERIFIED"


class ValidationStatus(str, Enum):
    PASS = "PASS"
    FAIL = "FAIL"
    NOT_VALIDATED = "NOT_VALIDATED"


class HumanConfirm(str, Enum):
    PENDING = "PENDING"
    CONFIRMED = "CONFIRMED"
    REJECTED = "REJECTED"


@dataclass(frozen=True, slots=True)
class EvidenceValidationResult:
    status: ValidationStatus
    failures: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class EvidenceRecord:
    """Append-only evidence metadata, including its three SHA-256 sidecar hashes."""

    source: EvidenceSource
    endpoint_or_tool: str
    request_params: Mapping[str, Any]
    request_timestamp: str
    provider_timestamp: str | None
    raw_response_path: str | None
    normalization_rule: tuple[str, ...] | None
    validation_result: EvidenceValidationResult
    collection_task_id: str
    system_run_id: str
    model_version: str | None
    human_confirm: HumanConfirm
    raw_response_hash: str | None
    normalized_data_hash: str | None
    analysis_output_hash: str | None
    evidence_id: str
    supersedes_evidence_id: str | None = None
    canonical_id: str | None = None
    raw_reference_id: str | None = None


@dataclass(frozen=True, slots=True)
class EvidenceMissingEvent:
    collection_task_id: str
    reason: str


@dataclass(frozen=True, slots=True)
class HumanConfirmationEvent:
    evidence_id: str
    previous: HumanConfirm
    current: HumanConfirm
    reason: str | None = None


@dataclass(frozen=True, slots=True)
class AnalysisConclusion:
    analysis_report_id: str
    evidence_ids: tuple[str, ...]
    structured_fields: Mapping[str, Any]
    model_version: str | None
    calculation: Mapping[str, Any] | None


@dataclass(frozen=True, slots=True)
class TraceResult:
    analysis_report_id: str
    complete: bool
    chain: tuple[Mapping[str, Any], ...]
    missing: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class ReviewDifferenceEvent:
    evidence_id: str
    reason: str


@dataclass(frozen=True, slots=True)
class ReviewRecord:
    analysis_report_id: str
    consistent: bool
    evidence_ids: tuple[str, ...]
    differences: tuple[ReviewDifferenceEvent, ...] = field(default_factory=tuple)
