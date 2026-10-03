"""Persistence-neutral human review contracts; HUMAN is never a data source."""

from __future__ import annotations

from dataclasses import dataclass, field, fields
from datetime import datetime, timezone
from typing import Any, Mapping

from evidence.contracts import HumanConfirm

from .analysis_evidence import AnalysisRunEvidence, validate_analysis_run_evidence
from .contracts import AnalysisInputError, freeze, jsonable, safe_json
from .output_contracts import AnalysisOutputError, ValidatedAnalysisDraft, digest, validate_draft


class ConfirmationError(ValueError):
    """Stable error codes without copying human or model content into errors."""


def require(condition: bool, code: str) -> None:
    if not condition:
        raise ConfirmationError(code)


def text_value(value: str, code: str) -> None:
    require(type(value) is str and bool(value.strip()), code)
    try:
        safe_json(value)
    except AnalysisInputError:
        raise ConfirmationError(code) from None


def aware_time(value: str | None = None) -> str:
    value = datetime.now(timezone.utc).isoformat() if value is None else value
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        require(parsed.utcoffset() is not None, "TIMESTAMP_TIMEZONE_REQUIRED")
    except (ValueError, TypeError, AttributeError):
        raise ConfirmationError("TIMESTAMP_TIMEZONE_REQUIRED") from None
    return value


def validate_confirmation_input(draft: ValidatedAnalysisDraft, run: AnalysisRunEvidence) -> None:
    # Call both upstream gates independently, even when the first fails.
    valid = True
    for validator, value in ((validate_draft, draft), (validate_analysis_run_evidence, run)):
        try:
            validator(value)
        except (AnalysisOutputError, TypeError, AttributeError, ValueError):
            valid = False
    require(valid, "CONFIRMATION_INPUT_INVALID")
    for name in ("input_hash", "validated_output_hash", "model_output_hash", "template_hash",
                 "output_schema_version", "input_evidence_ids", "used_evidence_ids"):
        require(getattr(draft, name) == getattr(run, name), "CONFIRMATION_INPUT_INVALID")


class _Contract:
    __slots__ = ()

    def to_dict(self) -> dict[str, Any]:
        return {item.name: jsonable(getattr(self, item.name)) for item in fields(self)
                if not item.name.startswith("_")}


@dataclass(frozen=True, slots=True)
class ConfirmationItem(_Contract):
    target: str
    reason_code: str
    details: Mapping[str, Any] = field(default_factory=dict)
    required: bool = True

    def __post_init__(self) -> None:
        object.__setattr__(self, "details", freeze(safe_json(self.details)))

    @property
    def key(self) -> tuple[str, str]:
        return self.target, self.reason_code


@dataclass(frozen=True, slots=True)
class ConfirmationEvent(_Contract):
    event_id: str
    analysis_run_id: str
    analysis_report_id: str
    revision_id: str
    target: str
    reason_code: str
    previous_state: HumanConfirm
    current_state: HumanConfirm
    actor_id: str
    decision_reason: str
    created_at: str
    override_id: str | None = None
    human_decision: str = "CONFIRM"
    reviewed_snapshot_hash: str | None = None

    def __post_init__(self) -> None:
        for name in ("event_id", "analysis_run_id", "analysis_report_id", "revision_id",
                     "target", "reason_code", "actor_id"):
            text_value(getattr(self, name), "CONFIRMATION_EVENT_INVALID")
        aware_time(self.created_at)
        require(type(self.previous_state) is HumanConfirm and type(self.current_state) is HumanConfirm,
                "CONFIRMATION_STATE_INVALID")
        require(self.human_decision in ("CONFIRM", "REJECT", "MODIFY"), "CONFIRMATION_DECISION_INVALID")
        safe_json(self.decision_reason)

    @property
    def key(self) -> tuple[str, str]:
        return self.target, self.reason_code


@dataclass(frozen=True, slots=True)
class HumanOverride(_Contract):
    override_id: str
    analysis_run_id: str
    analysis_report_id: str
    revision_id: str
    target: str
    reason_code: str
    original_value_hash: str
    replacement: Mapping[str, Any]
    reason: str
    actor_id: str
    created_at: str
    referenced_evidence_ids: tuple[str, ...]
    origin: str = field(default="HUMAN", init=False)
    human_confirm: HumanConfirm = field(default=HumanConfirm.CONFIRMED, init=False)

    def __post_init__(self) -> None:
        object.__setattr__(self, "replacement", freeze(safe_json(self.replacement)))
        object.__setattr__(self, "referenced_evidence_ids", tuple(self.referenced_evidence_ids))


@dataclass(frozen=True, slots=True)
class PreparedFinalSnapshot(_Contract):
    """Reviewable candidate only. It is not a FINAL snapshot or conclusion."""

    analysis_report_id: str
    analysis_run_id: str
    revision_id: str
    stock_id: str
    original_validated_output_hash: str
    model_version: str
    seven_fields: Mapping[str, Any]
    confirmation_decisions: tuple[Mapping[str, Any], ...]
    human_overrides: tuple[Mapping[str, Any], ...]
    watch_items: tuple[Mapping[str, Any], ...]
    confirmation_events: tuple[Mapping[str, Any], ...]
    confirmation_log_hash: str
    supersedes_analysis_report_id: str | None
    supersedes_final_evidence_id: str | None
    prepared_snapshot_hash: str
    human_confirm: HumanConfirm = field(default=HumanConfirm.PENDING, init=False)
    final_state: str = field(default="PREPARED", init=False)
    _gate_digest: str | None = field(default=None, init=False, repr=False, compare=False)

    def __post_init__(self) -> None:
        for name in ("seven_fields", "confirmation_decisions", "human_overrides", "watch_items",
                     "confirmation_events"):
            object.__setattr__(self, name, freeze(safe_json(getattr(self, name))))


def prepared_content_hash(candidate: PreparedFinalSnapshot) -> str:
    value = candidate.to_dict()
    value.pop("prepared_snapshot_hash")
    return digest(value)


def validate_prepared_snapshot(candidate: PreparedFinalSnapshot) -> None:
    require(type(candidate) is PreparedFinalSnapshot, "PREPARED_SNAPSHOT_INVALID")
    require(candidate._gate_digest == digest(candidate.to_dict())
            and candidate.prepared_snapshot_hash == prepared_content_hash(candidate),
            "PREPARED_SNAPSHOT_INVALID")
