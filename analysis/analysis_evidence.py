"""Immutable pending run provenance, separate from final EvidenceRecord."""

from __future__ import annotations

import re
from dataclasses import dataclass, field, fields
from datetime import datetime, timezone
from uuid import uuid4

from .contracts import AnalysisInputError, PromptEnvelope, jsonable, safe_json
from .output_contracts import AnalysisOutputError, ValidatedAnalysisDraft, digest, validate_draft
from .output_validator import prompt_inputs


def validate_model_version(value: str) -> None:
    try:
        if type(value) is not str or not value.strip():
            raise AnalysisOutputError("explicit model_version required")
        safe_json(value)
    except AnalysisInputError:
        raise AnalysisOutputError("unsafe model_version") from None


@dataclass(frozen=True, slots=True)
class AnalysisRunEvidence:
    analysis_run_id: str
    stock_id: str
    prompt_version: str
    template_hash: str
    input_hash: str
    output_schema_version: str
    model_version: str
    model_output_hash: str
    validated_output_hash: str
    input_evidence_ids: tuple[str, ...]
    used_evidence_ids: tuple[str, ...]
    created_at: str
    analysis_status: str = field(default="UNCONFIRMED", init=False)
    human_confirm: str = field(default="PENDING", init=False)
    final_conclusion: bool = field(default=False, init=False)
    _gate_digest: str | None = field(default=None, init=False, repr=False, compare=False)

    def __post_init__(self) -> None:
        validate_model_version(self.model_version)
        for name in ("analysis_run_id", "stock_id", "prompt_version", "output_schema_version"):
            value = getattr(self, name)
            if type(value) is not str or not value.strip():
                raise AnalysisOutputError("run identity must be nonempty text")
            safe_json(value)
        for name in ("template_hash", "input_hash", "model_output_hash", "validated_output_hash"):
            if not re.fullmatch(r"[a-f0-9]{64}", getattr(self, name)):
                raise AnalysisOutputError("invalid run provenance hash")
        for name in ("input_evidence_ids", "used_evidence_ids"):
            identifiers = tuple(safe_json(getattr(self, name)))
            if any(type(item) is not str or not item for item in identifiers) or len(set(identifiers)) != len(identifiers):
                raise AnalysisOutputError("invalid run evidence references")
            object.__setattr__(self, name, identifiers)
        if not set(self.used_evidence_ids) <= set(self.input_evidence_ids):
            raise AnalysisOutputError("run used Evidence outside input")
        try:
            created = datetime.fromisoformat(self.created_at.replace("Z", "+00:00"))
            if created.tzinfo is None:
                raise ValueError
        except (ValueError, TypeError, AttributeError):
            raise AnalysisOutputError("created_at requires a timezone") from None

    @classmethod
    def from_draft(cls, envelope: PromptEnvelope, draft: ValidatedAnalysisDraft,
                   *, model_version: str, analysis_run_id: str | None = None,
                   created_at: str | None = None) -> AnalysisRunEvidence:
        validate_draft(draft)
        prompt_inputs(envelope)
        if (draft.input_hash != envelope.input_hash or draft.template_hash != envelope.template_hash
                or draft.input_evidence_ids != envelope.evidence_ids):
            raise AnalysisOutputError("draft belongs to a different prompt")
        used = tuple(sorted({identifier for output in draft.output.values() for identifier in output["evidence_ids"]}))
        if used != draft.used_evidence_ids:
            raise AnalysisOutputError("used Evidence set mismatch")
        evidence = cls(
            analysis_run_id=uuid4().hex if analysis_run_id is None else analysis_run_id,
            stock_id=envelope.stock_id, prompt_version=envelope.prompt_version,
            template_hash=envelope.template_hash, input_hash=envelope.input_hash,
            output_schema_version=draft.output_schema_version, model_version=model_version,
            model_output_hash=draft.model_output_hash, validated_output_hash=draft.validated_output_hash,
            input_evidence_ids=envelope.evidence_ids, used_evidence_ids=used,
            created_at=datetime.now(timezone.utc).isoformat() if created_at is None else created_at,
        )
        object.__setattr__(evidence, "_gate_digest", digest(evidence.to_dict()))
        validate_analysis_run_evidence(evidence)
        return evidence

    def to_dict(self) -> dict:
        return {item.name: jsonable(getattr(self, item.name)) for item in fields(self)
                if not item.name.startswith("_")}


def validate_analysis_run_evidence(evidence: AnalysisRunEvidence) -> None:
    if (type(evidence) is not AnalysisRunEvidence or evidence._gate_digest is None
            or evidence._gate_digest != digest(evidence.to_dict())):
        raise AnalysisOutputError("run evidence has not passed validation or changed after validation")
