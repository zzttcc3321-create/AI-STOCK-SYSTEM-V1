"""In-memory append-only review revisions over a sealed #009 draft/run pair."""

from __future__ import annotations

from typing import Any, Mapping
from uuid import uuid4

from evidence.contracts import HumanConfirm

from .contracts import AnalysisInputError, jsonable, safe_json
from .confirmation_contracts import (
    ConfirmationError, ConfirmationEvent, ConfirmationItem, HumanOverride,
    aware_time, require, text_value, validate_confirmation_input, validate_prepared_snapshot,
)
from .output_contracts import ANALYSIS_FIELDS, AnalysisOutputError, digest, output_schema
from .output_validator import _schema_check

FINAL_REPORT_KEY = ("FINAL_REPORT", "REVIEW_FINAL_REPORT")
_ALLOWED = {
    (HumanConfirm.PENDING, HumanConfirm.CONFIRMED),
    (HumanConfirm.PENDING, HumanConfirm.REJECTED),
    (HumanConfirm.CONFIRMED, HumanConfirm.REJECTED),
}


def _plan(draft) -> tuple[ConfirmationItem, ...]:
    # Every field gets an explicit human result, in addition to frozen P3 reasons.
    items = [ConfirmationItem(name, "REVIEW_FIELD") for name in ANALYSIS_FIELDS]
    for item in draft.human_confirmation_items:
        items.append(ConfirmationItem(item["target"], item["reason"],
                                      {key: value for key, value in item.items()
                                       if key not in ("target", "reason")}))
    require(len({item.key for item in items}) == len(items), "CONFIRMATION_INPUT_INVALID")
    return tuple(items) + (ConfirmationItem(*FINAL_REPORT_KEY),)


def _validate_replacement(original: Mapping[str, Any], replacement: Mapping[str, Any], target: str) -> dict:
    """Closed structured judgments only; fact/quality/provenance edits fail closed.

    Free-text overrides could silently claim a new price or announcement. V1
    therefore uses the existing structured vocabulary, and preserves every fact,
    limitation, evidence reference, time range and condition baseline verbatim.
    """
    try:
        value = safe_json(replacement)
        _schema_check(value, output_schema()["properties"]["analysis"]["properties"][target])
    except (AnalysisInputError, AnalysisOutputError, TypeError, KeyError):
        raise ConfirmationError("HUMAN_OVERRIDE_INVALID") from None
    before = jsonable(original)
    require(digest({key: item for key, item in value.items() if key != "content"})
            == digest({key: item for key, item in before.items() if key != "content"}),
            "CANONICAL_FACT_MODIFICATION_FORBIDDEN")
    for key in ("facts", "limitations"):
        require(digest(value["content"][key]) == digest(before["content"][key]),
                "CANONICAL_FACT_MODIFICATION_FORBIDDEN")
    if target in ("operation_plan", "t_strategy"):
        old, new = before["content"]["advice"], value["content"]["advice"]
        require(int(new["level"][-1]) >= int(old["level"][-1]), "ADVICE_UPGRADE_REQUIRES_NEW_ANALYSIS")
        if new["level"] == "LEVEL_2":
            for key in ("condition", "observation_path", "verification"):
                require(digest(new[key]) == digest(old.get(key)), "CANONICAL_FACT_MODIFICATION_FORBIDDEN")
    if target == "reasoning":
        def refs(steps):
            return [{key: value for key, value in step.items() if key != "statement"} for step in steps]
        require(refs(value["content"]["steps"]) == refs(before["content"]["steps"]),
                "CANONICAL_FACT_MODIFICATION_FORBIDDEN")
    require(value != before, "HUMAN_OVERRIDE_UNCHANGED")
    return value


class ConfirmationFlow:
    """One report revision. Public events are immutable; no update/delete API.

    Actor IDs are supplied by the caller, not looked up or authenticated here.
    This process-local integrity gate is not a signature or a persistence layer.
    """

    def __init__(self, draft, run_evidence, *, analysis_report_id: str | None = None):
        validate_confirmation_input(draft, run_evidence)
        report_id = uuid4().hex if analysis_report_id is None else analysis_report_id
        text_value(report_id, "ANALYSIS_REPORT_ID_REQUIRED")
        self._draft, self._run_evidence = draft, run_evidence
        self._analysis_report_id, self._revision_id = report_id, uuid4().hex
        self._items = _plan(draft)
        self._events: tuple[ConfirmationEvent, ...] = ()
        self._overrides: tuple[HumanOverride, ...] = ()
        self._prepared = None
        self._final_evidence_id = None
        self._final_snapshot_hash = None
        self._supersedes_analysis_report_id = None
        self._supersedes_final_evidence_id = None
        self._seal()

    @property
    def draft(self):
        return self._draft

    @property
    def run_evidence(self):
        return self._run_evidence

    @property
    def analysis_report_id(self):
        return self._analysis_report_id

    @property
    def revision_id(self):
        return self._revision_id

    @property
    def items(self):
        return self._items

    @property
    def events(self):
        return self._events

    @property
    def human_overrides(self):
        return self._overrides

    @property
    def confirmation_log_hash(self):
        self.validate()
        return digest([event.to_dict() for event in self.events])

    def _payload(self):
        return {
            "analysis_report_id": self.analysis_report_id, "revision_id": self.revision_id,
            "draft_gate": self.draft._gate_digest, "run_evidence_gate": self.run_evidence._gate_digest,
            "items": [item.to_dict() for item in self.items],
            "events": [event.to_dict() for event in self.events],
            "human_overrides": [item.to_dict() for item in self.human_overrides],
            "prepared_gate": None if self._prepared is None else self._prepared._gate_digest,
            "final_evidence_id": self._final_evidence_id, "final_snapshot_hash": self._final_snapshot_hash,
            "supersedes_analysis_report_id": self._supersedes_analysis_report_id,
            "supersedes_final_evidence_id": self._supersedes_final_evidence_id,
        }

    def _seal(self):
        self._gate_digest = digest(self._payload())

    def validate(self):
        validate_confirmation_input(self.draft, self.run_evidence)
        require(self._gate_digest == digest(self._payload()), "CONFIRMATION_LOG_INVALID")
        if self._prepared is not None:
            validate_prepared_snapshot(self._prepared)

    def current(self, target: str, reason_code: str) -> HumanConfirm:
        self.validate()
        return self._current(target, reason_code)

    def _current(self, target: str, reason_code: str) -> HumanConfirm:
        """Internal read after a gate, without repeatedly hashing the full log."""
        key = target, reason_code
        require(key in {item.key for item in self.items}
                or key in {("HUMAN_OVERRIDE:" + item.override_id, "REVIEW_OVERRIDE")
                           for item in self.human_overrides}, "CONFIRMATION_TARGET_INVALID")
        return next((event.current_state for event in reversed(self.events) if event.key == key),
                    HumanConfirm.PENDING)

    def _event(self, target, reason_code, state, *, actor_id, reason, created_at,
               override_id=None, human_decision=None, reviewed_snapshot_hash=None):
        previous = self.current(target, reason_code)
        text_value(actor_id, "ACTOR_ID_REQUIRED")
        require((previous, state) in _ALLOWED, "CONFIRMATION_TRANSITION_FORBIDDEN")
        require(type(reason) is str, "DECISION_REASON_INVALID")
        if state is HumanConfirm.REJECTED:
            text_value(reason, "REJECTION_REASON_REQUIRED")
        safe_json(reason)
        timestamp = aware_time(created_at)
        return ConfirmationEvent(
            uuid4().hex, self.run_evidence.analysis_run_id, self.analysis_report_id, self.revision_id,
            target, reason_code, previous, state, actor_id, reason, timestamp, override_id,
            human_decision or ("CONFIRM" if state is HumanConfirm.CONFIRMED else "REJECT"),
            reviewed_snapshot_hash,
        )

    def transition(self, target: str, reason_code: str, state: HumanConfirm, *, actor_id: str,
                   reason: str = "", created_at: str | None = None) -> ConfirmationEvent:
        require(type(state) is HumanConfirm, "CONFIRMATION_STATE_INVALID")
        require((target, reason_code) != FINAL_REPORT_KEY, "FINAL_REPORT_REQUIRES_SNAPSHOT_REVIEW")
        # After preparation the human must review a stable candidate. Corrections
        # invalidate it and can only lead to a fresh revision, never re-finalize it.
        require(self._prepared is None or state is HumanConfirm.REJECTED,
                "PREPARED_SNAPSHOT_DECISIONS_FROZEN")
        event = self._event(target, reason_code, state, actor_id=actor_id, reason=reason,
                            created_at=created_at)
        self._events += (event,)
        self._seal()
        return event

    def confirm(self, target, reason_code, *, actor_id, reason="", created_at=None):
        return self.transition(target, reason_code, HumanConfirm.CONFIRMED,
                               actor_id=actor_id, reason=reason, created_at=created_at)

    def reject(self, target, reason_code, *, actor_id, reason, created_at=None):
        return self.transition(target, reason_code, HumanConfirm.REJECTED,
                               actor_id=actor_id, reason=reason, created_at=created_at)

    def modify(self, target: str, reason_code: str, replacement: Mapping[str, Any], *,
               actor_id: str, reason: str, referenced_evidence_ids=(), created_at=None) -> HumanOverride:
        self.validate()
        require(self._prepared is None, "PREPARED_SNAPSHOT_DECISIONS_FROZEN")
        require(target in ANALYSIS_FIELDS and reason_code != "REVIEW_DATA_QUALITY",
                "CANONICAL_FACT_MODIFICATION_FORBIDDEN")
        require(not any(item.target == target for item in self.human_overrides),
                "HUMAN_OVERRIDE_ALREADY_EXISTS")
        text_value(reason, "OVERRIDE_REASON_REQUIRED")
        value = _validate_replacement(self.draft.output[target], replacement, target)
        refs = tuple(referenced_evidence_ids)
        require(all(type(item) is str and bool(item) for item in refs)
                and len(set(refs)) == len(refs)
                and set(refs) <= set(self.run_evidence.input_evidence_ids), "OVERRIDE_EVIDENCE_INVALID")
        # Final Conclusion must use exactly the model's used IDs. Any additional
        # human-only reference is retained as context, not promoted to AI support.
        identifier = uuid4().hex
        rejected = self._event(target, reason_code, HumanConfirm.REJECTED, actor_id=actor_id,
                               reason=reason, created_at=created_at, override_id=identifier,
                               human_decision="MODIFY")
        override = HumanOverride(identifier, self.run_evidence.analysis_run_id, self.analysis_report_id,
                                 self.revision_id, target, reason_code, digest(self.draft.output[target]),
                                 value, reason, actor_id, rejected.created_at, refs)
        accepted = ConfirmationEvent(
            uuid4().hex, self.run_evidence.analysis_run_id, self.analysis_report_id, self.revision_id,
            "HUMAN_OVERRIDE:" + identifier, "REVIEW_OVERRIDE", HumanConfirm.PENDING,
            HumanConfirm.CONFIRMED, actor_id, reason, rejected.created_at, identifier,
        )
        # Both decisions commit together, after all validation, with no partial log.
        self._overrides += (override,)
        self._events += (rejected, accepted)
        self._seal()
        return override

    def prepare_final_snapshot(self):
        from .finalization import prepare_final_snapshot
        return prepare_final_snapshot(self)

    def confirm_final_report(self, candidate, *, actor_id, reason="", created_at=None):
        return self._decide_final_report(candidate, HumanConfirm.CONFIRMED, actor_id, reason, created_at)

    def reject_final_report(self, candidate, *, actor_id, reason, created_at=None):
        return self._decide_final_report(candidate, HumanConfirm.REJECTED, actor_id, reason, created_at)

    def _decide_final_report(self, candidate, state, actor_id, reason, created_at):
        self.validate()
        validate_prepared_snapshot(candidate)
        require(self._prepared is candidate, "FINAL_REPORT_SNAPSHOT_MISMATCH")
        base = candidate.confirmation_events
        events = tuple(event.to_dict() for event in self.events)
        if self.current(*FINAL_REPORT_KEY) is HumanConfirm.PENDING:
            require(jsonable(base) == jsonable(events), "PREPARED_SNAPSHOT_STALE")
        else:
            require(state is HumanConfirm.REJECTED, "CONFIRMATION_TRANSITION_FORBIDDEN")
        event = self._event(*FINAL_REPORT_KEY, state, actor_id=actor_id, reason=reason,
                            created_at=created_at, reviewed_snapshot_hash=candidate.prepared_snapshot_hash)
        self._events += (event,)
        self._seal()
        return event

    def new_revision(self, *, analysis_report_id: str | None = None):
        self.validate()
        require(any(event.current_state is HumanConfirm.REJECTED for event in self.events),
                "NEW_REVISION_REQUIRES_REJECTION")
        result = ConfirmationFlow(self.draft, self.run_evidence, analysis_report_id=analysis_report_id)
        finalized_at = next((index for index in range(len(self.events) - 1, -1, -1)
                             if self.events[index].key == FINAL_REPORT_KEY
                             and self.events[index].current_state is HumanConfirm.CONFIRMED), None)
        if finalized_at is not None:
            require(any(event.current_state is HumanConfirm.REJECTED
                        for event in self.events[finalized_at + 1:]),
                    "FINAL_REPORT_REVISION_REQUIRES_CORRECTION")
        require(result.analysis_report_id != self.analysis_report_id, "NEW_ANALYSIS_REPORT_ID_REQUIRED")
        result._supersedes_analysis_report_id = self.analysis_report_id
        result._supersedes_final_evidence_id = self._final_evidence_id or self._supersedes_final_evidence_id
        result._seal()
        return result

    def update_confirmation_event(self, *args, **kwargs):
        raise ConfirmationError("UPDATE_CONFIRMATION_EVENT=FORBIDDEN")

    def delete_confirmation_event(self, *args, **kwargs):
        raise ConfirmationError("DELETE_CONFIRMATION_EVENT=FORBIDDEN")
