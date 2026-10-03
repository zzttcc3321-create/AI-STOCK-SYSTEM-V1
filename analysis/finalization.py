"""Sealed final reports and the existing EvidenceTrace/AnalysisConclusion bridge.

Factories are entirely in memory. No model, provider, raw reader, persistence,
or trading adapter is imported or invoked by this module.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Iterable, Mapping
from uuid import uuid4

from evidence.contracts import (
    AnalysisConclusion, EvidenceRecord, EvidenceSource, EvidenceValidationResult, HumanConfirm, ValidationStatus,
)
from evidence.trace import EvidenceTrace

from .confirmation_contracts import (
    ConfirmationError, PreparedFinalSnapshot, _Contract, aware_time, prepared_content_hash,
    require, text_value, validate_confirmation_input, validate_prepared_snapshot,
)
from .confirmation_flow import FINAL_REPORT_KEY, ConfirmationFlow
from .contracts import freeze, jsonable, safe_json
from .output_contracts import ANALYSIS_FIELDS, digest


def _freeze_fields(value, names):
    for name in names:
        object.__setattr__(value, name, freeze(safe_json(getattr(value, name))))


@dataclass(frozen=True, slots=True)
class FinalAnalysisSnapshot(_Contract):
    analysis_report_id: str
    analysis_run_id: str
    revision_id: str
    stock_id: str
    input_hash: str
    model_output_hash: str
    original_validated_output_hash: str
    model_version: str
    seven_fields: Mapping[str, Any]
    confirmation_decisions: tuple[Mapping[str, Any], ...]
    human_overrides: tuple[Mapping[str, Any], ...]
    watch_items: tuple[Mapping[str, Any], ...]
    confirmation_events: tuple[Mapping[str, Any], ...]
    confirmation_log_hash: str
    prepared_snapshot_hash: str
    confirmed_by: str
    confirmed_at: str
    supersedes_analysis_report_id: str | None
    final_snapshot_hash: str
    human_confirm: HumanConfirm = field(default=HumanConfirm.CONFIRMED, init=False)
    final_state: str = field(default="FINAL", init=False)
    _gate_digest: str | None = field(default=None, init=False, repr=False, compare=False)

    def __post_init__(self):
        _freeze_fields(self, ("seven_fields", "confirmation_decisions", "human_overrides",
                              "watch_items", "confirmation_events"))


@dataclass(frozen=True, slots=True)
class FinalAnalysisEvidence(_Contract):
    final_evidence_id: str
    analysis_report_id: str
    analysis_run_id: str
    stock_id: str
    model_version: str
    input_hash: str
    model_output_hash: str
    validated_output_hash: str
    final_snapshot_hash: str
    confirmation_log_hash: str
    input_evidence_ids: tuple[str, ...]
    used_evidence_ids: tuple[str, ...]
    confirmed_by: str
    confirmed_at: str
    supersedes_final_evidence_id: str | None = None
    human_confirm: HumanConfirm = field(default=HumanConfirm.CONFIRMED, init=False)
    _gate_digest: str | None = field(default=None, init=False, repr=False, compare=False)

    def __post_init__(self):
        _freeze_fields(self, ("input_evidence_ids", "used_evidence_ids"))


@dataclass(frozen=True, slots=True)
class FinalizationResult:
    snapshot: FinalAnalysisSnapshot
    evidence: FinalAnalysisEvidence
    conclusion: AnalysisConclusion


def final_snapshot_content_hash(snapshot: FinalAnalysisSnapshot) -> str:
    value = snapshot.to_dict()
    value.pop("final_snapshot_hash")
    return digest(value)


def validate_final_snapshot(snapshot: FinalAnalysisSnapshot) -> None:
    require(type(snapshot) is FinalAnalysisSnapshot, "FINAL_SNAPSHOT_INVALID")
    require(snapshot._gate_digest == digest(snapshot.to_dict()), "FINAL_SNAPSHOT_INVALID")
    require(snapshot.final_snapshot_hash == final_snapshot_content_hash(snapshot)
            and snapshot.confirmation_log_hash == digest(snapshot.confirmation_events)
            and set(snapshot.seven_fields) == set(ANALYSIS_FIELDS)
            and snapshot.human_confirm is HumanConfirm.CONFIRMED and snapshot.final_state == "FINAL",
            "FINAL_SNAPSHOT_INVALID")


def validate_final_analysis_evidence(evidence: FinalAnalysisEvidence) -> None:
    require(type(evidence) is FinalAnalysisEvidence, "FINAL_EVIDENCE_INVALID")
    require(evidence._gate_digest == digest(evidence.to_dict()), "FINAL_EVIDENCE_INVALID")
    require(evidence.human_confirm is HumanConfirm.CONFIRMED
            and set(evidence.used_evidence_ids) <= set(evidence.input_evidence_ids), "FINAL_EVIDENCE_INVALID")
    for name in ("final_evidence_id", "analysis_report_id", "analysis_run_id", "stock_id",
                 "model_version", "confirmed_by"):
        text_value(getattr(evidence, name), "FINAL_EVIDENCE_INVALID")
    for name in ("input_hash", "model_output_hash", "validated_output_hash", "final_snapshot_hash",
                 "confirmation_log_hash"):
        require(re.fullmatch(r"[a-f0-9]{64}", getattr(evidence, name)) is not None, "FINAL_EVIDENCE_INVALID")
    aware_time(evidence.confirmed_at)


def _quality_review_affects_field(original: Mapping[str, Any], target: str) -> bool:
    # #009 does not require a field to repeat every anomaly in limitations.
    # Facts still identify block dependencies when that optional prose is absent.
    paths = [item["input_path"] for item in original["content"]["limitations"]]
    paths.extend(item["input_path"] for item in original["content"]["facts"])
    if any(path == target or path.startswith(target + ".") for path in paths):
        return True
    # The sealed draft/run pair lacks a complete Evidence-to-source/block map.
    # Conservatively associate every degraded field with quality reviews; for
    # aggregate/source reviews, also suppress all evidenced fields. Human review
    # cannot turn an unresolved dependency into an effective confirmed output.
    if original["data_quality_status"] != "PASS":
        return True
    return bool(original["evidence_ids"]) and (
        target == "data_quality_report" or target.startswith("data_quality_report.source_status."))


def _review_content(flow: ConfirmationFlow) -> dict:
    flow.validate()
    decisions = []
    for item in flow.items:
        if item.key == FINAL_REPORT_KEY:
            continue
        state = flow._current(*item.key)
        require(state is not HumanConfirm.PENDING, "REQUIRED_CONFIRMATIONS_PENDING")
        events = [event for event in flow.events if event.key == item.key]
        last = events[-1]
        decisions.append({**item.to_dict(), "state": state.value, "actor_id": last.actor_id,
                          "decision_reason": last.decision_reason, "human_decision": last.human_decision,
                          "override_id": last.override_id, "event_id": last.event_id})
    overrides = [item.to_dict() for item in flow.human_overrides]
    fields = {}
    for target in ANALYSIS_FIELDS:
        original = flow.draft.output[target]
        related = [item for item in decisions if item["target"] == target or (
            item["reason_code"] == "REVIEW_DATA_QUALITY"
            and _quality_review_affects_field(original, item["target"]))]
        modified = [item for item in flow.human_overrides if item.target == target]
        override = modified[0] if modified else None
        ordinary_rejections = [item for item in related if item["state"] == "REJECTED"
                               and item["human_decision"] != "MODIFY"]
        override_accepted = override is not None and flow._current(
            "HUMAN_OVERRIDE:" + override.override_id, "REVIEW_OVERRIDE") is HumanConfirm.CONFIRMED
        decision = ("REJECTED" if ordinary_rejections or (override and not override_accepted)
                    else "MODIFIED" if override else "CONFIRMED")
        effective = None if decision == "REJECTED" else override.replacement if override else original
        fields[target] = {
            "AI_ORIGINAL": original, "AI_OUTPUT_HASH": flow.run_evidence.model_output_hash,
            "DECISION": decision, "EFFECTIVE_VALUE": effective,
            "HUMAN_DECISION": "MODIFY" if override else decision,
            "HUMAN_OVERRIDE": None if override is None else override.to_dict(),
            "ORIGIN": "HUMAN" if override and decision == "MODIFIED" else "AI",
            "decision_reasons": related,
        }
    watch = [item for item in decisions if item["reason_code"] == "REVIEW_DATA_QUALITY"
             or item["state"] == "REJECTED"]
    for override in flow.human_overrides:
        state = flow._current("HUMAN_OVERRIDE:" + override.override_id, "REVIEW_OVERRIDE")
        if state is HumanConfirm.REJECTED:
            last = next(event for event in reversed(flow.events)
                        if event.target == "HUMAN_OVERRIDE:" + override.override_id)
            watch.append({"target": last.target, "reason_code": last.reason_code,
                          "state": state.value, "decision_reason": last.decision_reason})
    return {"seven_fields": fields, "confirmation_decisions": decisions,
            "human_overrides": overrides, "watch_items": watch}


def prepare_final_snapshot(flow: ConfirmationFlow) -> PreparedFinalSnapshot:
    flow.validate()
    require(flow._prepared is None, "FINAL_SNAPSHOT_ALREADY_PREPARED")
    content = _review_content(flow)
    candidate = PreparedFinalSnapshot(
        flow.analysis_report_id, flow.run_evidence.analysis_run_id, flow.revision_id,
        flow.run_evidence.stock_id, flow.run_evidence.validated_output_hash,
        flow.run_evidence.model_version, **content,
        confirmation_events=tuple(event.to_dict() for event in flow.events),
        confirmation_log_hash=flow.confirmation_log_hash,
        supersedes_analysis_report_id=flow._supersedes_analysis_report_id,
        supersedes_final_evidence_id=flow._supersedes_final_evidence_id,
        prepared_snapshot_hash="",
    )
    object.__setattr__(candidate, "prepared_snapshot_hash", prepared_content_hash(candidate))
    object.__setattr__(candidate, "_gate_digest", digest(candidate.to_dict()))
    validate_prepared_snapshot(candidate)
    flow._prepared = candidate
    flow._seal()
    return candidate


def _calculation(snapshot: FinalAnalysisSnapshot, evidence: FinalAnalysisEvidence) -> dict:
    return {"analysis_run_id": evidence.analysis_run_id, "input_hash": evidence.input_hash,
            "model_output_hash": evidence.model_output_hash, "validated_output_hash": evidence.validated_output_hash,
            "final_snapshot_hash": evidence.final_snapshot_hash,
            "confirmation_log_hash": evidence.confirmation_log_hash, "final_evidence_id": evidence.final_evidence_id,
            "prepared_snapshot_hash": snapshot.prepared_snapshot_hash,
            "confirmation_events": snapshot.confirmation_events,
            "supersedes_analysis_report_id": snapshot.supersedes_analysis_report_id}


def _supporting_records(run, evidence_records: Iterable[EvidenceRecord], *, human_reference_ids=()) -> tuple[EvidenceRecord, ...]:
    records = tuple(evidence_records)
    require(all(type(item) is EvidenceRecord for item in records), "CONCLUSION_EVIDENCE_INVALID")
    require(len({item.evidence_id for item in records}) == len(records), "CONCLUSION_EVIDENCE_INVALID")
    index = {item.evidence_id: item for item in records}
    selected = []
    # Human context must also be valid when its Override is effective. It never
    # expands Conclusion.evidence_ids or the model's declared used Evidence set.
    required_ids = tuple(dict.fromkeys(run.used_evidence_ids + tuple(human_reference_ids)))
    for identifier in required_ids:
        item = index.get(identifier)
        require(item is not None, "CONCLUSION_EVIDENCE_MISSING")
        require(type(item.validation_result) is EvidenceValidationResult
                and item.validation_result.status is ValidationStatus.PASS
                and type(item.source) is EvidenceSource
                and type(item.human_confirm) is HumanConfirm
                and item.human_confirm in (HumanConfirm.PENDING, HumanConfirm.CONFIRMED),
                "CONCLUSION_EVIDENCE_REJECTED_OR_INVALID")
        for name in ("raw_response_path", "request_timestamp"):
            value = getattr(item, name)
            require(type(value) is str and bool(value.strip()), "CONCLUSION_EVIDENCE_INVALID")
        aware_time(item.request_timestamp)
        for name in ("raw_response_hash", "normalized_data_hash"):
            value = getattr(item, name)
            require(type(value) is str and re.fullmatch(r"[a-f0-9]{64}", value) is not None,
                    "CONCLUSION_EVIDENCE_INVALID")
        if identifier in run.used_evidence_ids:
            selected.append(item)
    return tuple(selected)


def _human_reference_ids(seven_fields):
    return tuple(sorted({identifier for value in seven_fields.values() if value["DECISION"] == "MODIFIED"
                         for identifier in value["HUMAN_OVERRIDE"]["referenced_evidence_ids"]}))


def finalize_analysis(flow: ConfirmationFlow, candidate: PreparedFinalSnapshot, *,
                      evidence_records: Iterable[EvidenceRecord],
                      final_evidence_id: str | None = None) -> FinalizationResult:
    flow.validate()
    validate_prepared_snapshot(candidate)
    require(flow._prepared is candidate, "FINAL_REPORT_SNAPSHOT_MISMATCH")
    require(flow._final_evidence_id is None, "FINAL_ANALYSIS_ALREADY_CREATED")
    require(flow.current(*FINAL_REPORT_KEY) is HumanConfirm.CONFIRMED, "FINAL_REPORT_NOT_CONFIRMED")
    event = flow.events[-1]
    require(event.key == FINAL_REPORT_KEY and event.current_state is HumanConfirm.CONFIRMED
            and event.reviewed_snapshot_hash == candidate.prepared_snapshot_hash
            and jsonable(candidate.confirmation_events) == [item.to_dict() for item in flow.events[:-1]],
            "PREPARED_SNAPSHOT_STALE")
    require(jsonable(_review_content(flow)) == {
        name: jsonable(getattr(candidate, name))
        for name in ("seven_fields", "confirmation_decisions", "human_overrides", "watch_items")
    }, "PREPARED_SNAPSHOT_STALE")
    require(bool(flow.run_evidence.used_evidence_ids), "MODEL_OUTPUT_NEEDS_EVIDENCE")
    records = _supporting_records(flow.run_evidence, evidence_records,
                                  human_reference_ids=_human_reference_ids(candidate.seven_fields))
    identifier = uuid4().hex if final_evidence_id is None else final_evidence_id
    text_value(identifier, "FINAL_EVIDENCE_ID_REQUIRED")
    require(identifier != flow._supersedes_final_evidence_id, "NEW_FINAL_EVIDENCE_ID_REQUIRED")
    report_decision = {"target": event.target, "reason_code": event.reason_code,
                       "state": event.current_state.value, "actor_id": event.actor_id,
                       "decision_reason": event.decision_reason, "event_id": event.event_id,
                       "reviewed_snapshot_hash": event.reviewed_snapshot_hash}
    snapshot = FinalAnalysisSnapshot(
        candidate.analysis_report_id, candidate.analysis_run_id, candidate.revision_id, candidate.stock_id,
        flow.run_evidence.input_hash, flow.run_evidence.model_output_hash,
        candidate.original_validated_output_hash, candidate.model_version, candidate.seven_fields,
        candidate.confirmation_decisions + (report_decision,), candidate.human_overrides, candidate.watch_items,
        tuple(item.to_dict() for item in flow.events), flow.confirmation_log_hash,
        candidate.prepared_snapshot_hash, event.actor_id, event.created_at,
        candidate.supersedes_analysis_report_id, "",
    )
    object.__setattr__(snapshot, "final_snapshot_hash", final_snapshot_content_hash(snapshot))
    object.__setattr__(snapshot, "_gate_digest", digest(snapshot.to_dict()))
    run = flow.run_evidence
    evidence = FinalAnalysisEvidence(
        identifier, snapshot.analysis_report_id, run.analysis_run_id, run.stock_id, run.model_version,
        run.input_hash, run.model_output_hash, run.validated_output_hash, snapshot.final_snapshot_hash,
        snapshot.confirmation_log_hash, run.input_evidence_ids, run.used_evidence_ids,
        snapshot.confirmed_by, snapshot.confirmed_at, candidate.supersedes_final_evidence_id,
    )
    object.__setattr__(evidence, "_gate_digest", digest(evidence.to_dict()))
    conclusion = AnalysisConclusion(snapshot.analysis_report_id, run.used_evidence_ids, snapshot.seven_fields,
                                    run.model_version, freeze(_calculation(snapshot, evidence)))
    result = FinalizationResult(snapshot, evidence, conclusion)
    validate_finalization_result(result, flow.draft, run)
    trace = EvidenceTrace(records, (conclusion,)).query(snapshot.analysis_report_id)
    require(trace.complete, "FINAL_CONCLUSION_TRACE_INCOMPLETE")
    # Mark this revision only after all gates pass. Historical artifacts never change.
    flow._final_evidence_id, flow._final_snapshot_hash = evidence.final_evidence_id, snapshot.final_snapshot_hash
    flow._seal()
    return result


def validate_finalization_result(result: FinalizationResult, draft, run_evidence) -> None:
    validate_confirmation_input(draft, run_evidence)
    require(type(result) is FinalizationResult, "FINALIZATION_RESULT_INVALID")
    snapshot, evidence, conclusion = result.snapshot, result.evidence, result.conclusion
    validate_final_snapshot(snapshot)
    validate_final_analysis_evidence(evidence)
    for name in ("analysis_run_id", "stock_id", "model_version", "input_hash", "model_output_hash"):
        require(getattr(snapshot, name) == getattr(evidence, name) == getattr(run_evidence, name),
                "FINALIZATION_BINDING_INVALID")
    require(snapshot.original_validated_output_hash == evidence.validated_output_hash == draft.validated_output_hash,
            "FINALIZATION_BINDING_INVALID")
    for name in ("analysis_report_id", "confirmed_by", "confirmed_at", "confirmation_log_hash", "final_snapshot_hash"):
        require(getattr(snapshot, name) == getattr(evidence, name), "FINALIZATION_BINDING_INVALID")
    require(evidence.input_evidence_ids == run_evidence.input_evidence_ids
            and evidence.used_evidence_ids == run_evidence.used_evidence_ids, "FINALIZATION_BINDING_INVALID")
    require(type(conclusion) is AnalysisConclusion
            and conclusion.analysis_report_id == snapshot.analysis_report_id
            and conclusion.model_version == run_evidence.model_version
            and conclusion.evidence_ids == run_evidence.used_evidence_ids
            and conclusion.structured_fields == snapshot.seven_fields
            and conclusion.calculation == _calculation(snapshot, evidence), "FINAL_CONCLUSION_INVALID")


def trace_final_analysis(result: FinalizationResult, draft, run_evidence, *, evidence_records):
    """Validate the final bridge before querying the unchanged original Trace API."""
    validate_finalization_result(result, draft, run_evidence)
    records = _supporting_records(run_evidence, evidence_records,
                                  human_reference_ids=_human_reference_ids(result.snapshot.seven_fields))
    trace = EvidenceTrace(records, (result.conclusion,)).query(result.snapshot.analysis_report_id)
    require(trace.complete, "FINAL_CONCLUSION_TRACE_INCOMPLETE")
    return trace
