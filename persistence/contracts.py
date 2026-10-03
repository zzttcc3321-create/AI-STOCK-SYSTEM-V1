"""Persistence values are DTOs, never reconstructed sealed domain objects."""

class PersistenceError(ValueError):
    pass


def require(condition, code):
    if not condition:
        raise PersistenceError(code)


def text(value):
    require(type(value) is str and bool(value.strip()), "EXPLICIT_TEXT_REQUIRED")
    return value


EVIDENCE_LIMITATIONS = {
    "metadata_availability": "UNAVAILABLE",
    "metadata_reason": "SIDECAR_PHYSICAL_CONTRACT_NOT_IMPLEMENTED",
    "integrity_status": "NOT_VERIFIED",
}


def replay_events(events):
    states = {}
    seen = set()
    scope = None
    for sequence, event in enumerate(events):
        require(event["event_sequence"] == sequence, "EVENT_SEQUENCE_INVALID")
        identity = tuple(event[k] for k in ("analysis_run_id", "analysis_report_id", "revision_id"))
        scope = identity if scope is None else scope
        require(identity == scope and event["event_id"] not in seen, "EVENT_BINDING_INVALID")
        seen.add(event["event_id"])
        key = (event["target"], event["reason_code"])
        previous = states.get(key, "PENDING")
        current = event["current_state"]
        require(event["previous_state"] == previous and (previous, current) in {
            ("PENDING", "CONFIRMED"), ("PENDING", "REJECTED"), ("CONFIRMED", "REJECTED")},
            "EVENT_STATE_CHAIN_INVALID")
        require((event["human_decision"], current) in {
            ("CONFIRM", "CONFIRMED"), ("REJECT", "REJECTED"), ("MODIFY", "REJECTED")},
            "EVENT_DECISION_INVALID")
        states[key] = current
    return states
