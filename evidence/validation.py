"""Append-only human confirmation state machine."""

from __future__ import annotations

from .contracts import EvidenceRecord, HumanConfirm, HumanConfirmationEvent

_ALLOWED = {
    (HumanConfirm.PENDING, HumanConfirm.CONFIRMED),
    (HumanConfirm.PENDING, HumanConfirm.REJECTED),
    (HumanConfirm.CONFIRMED, HumanConfirm.REJECTED),
}


class HumanConfirmationLedger:
    def __init__(self) -> None:
        self._events: list[HumanConfirmationEvent] = []

    @property
    def events(self) -> tuple[HumanConfirmationEvent, ...]:
        return tuple(self._events)

    def current(self, evidence: EvidenceRecord) -> HumanConfirm:
        matches = [event.current for event in self._events if event.evidence_id == evidence.evidence_id]
        return matches[-1] if matches else evidence.human_confirm

    def transition(self, evidence: EvidenceRecord, target: HumanConfirm, *, reason: str | None = None) -> HumanConfirmationEvent:
        previous = self.current(evidence)
        if (previous, target) not in _ALLOWED:
            raise ValueError(f"illegal human_confirm transition: {previous.value} -> {target.value}")
        if previous is HumanConfirm.CONFIRMED and target is HumanConfirm.REJECTED and not reason:
            raise ValueError("CONFIRMED -> REJECTED requires a reason")
        event = HumanConfirmationEvent(evidence.evidence_id, previous, target, reason)
        self._events.append(event)
        return event


def require_confirmed_conclusion(evidence: EvidenceRecord, ledger: HumanConfirmationLedger | None = None) -> None:
    state = ledger.current(evidence) if ledger else evidence.human_confirm
    if state is not HumanConfirm.CONFIRMED:
        raise ValueError("analysis conclusions and operation advice require CONFIRMED evidence")
