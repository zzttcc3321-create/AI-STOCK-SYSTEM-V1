"""Persistence values are DTOs, never reconstructed sealed domain objects."""

class PersistenceError(ValueError):
    pass


def require(condition, code):
    if not condition:
        raise PersistenceError(code)


def text(value):
    require(type(value) is str and bool(value.strip()), "EXPLICIT_TEXT_REQUIRED")
    return value


def evidence_limitations(status="NO_READER_CONFIG"):
    if status == "VERIFIED_ARCHIVE":
        return {"metadata_availability": "AVAILABLE", "metadata_reason": "VERIFIED_ARCHIVE",
                "integrity_status": "VERIFIED"}
    if status == "CORRUPT_ARCHIVE":
        return {"metadata_availability": "AVAILABLE", "metadata_reason": "CORRUPT_ARCHIVE",
                "integrity_status": "CORRUPT"}
    return {"metadata_availability": "UNAVAILABLE", "metadata_reason": status,
            "integrity_status": "NOT_VERIFIED"}


# Compatibility projection for callers without a reader. Live read models use
# evidence_limitations() for each result, never this default as an integrity gate.
EVIDENCE_LIMITATIONS = evidence_limitations()


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
