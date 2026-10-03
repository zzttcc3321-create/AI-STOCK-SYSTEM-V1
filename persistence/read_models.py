"""Read-only DTO projections; no reconstructed gates or archive claims."""
from .contracts import EVIDENCE_LIMITATIONS, PersistenceError, require, replay_events, text
from .serialization import digest, loads


def rows(db, sql, params=()):
    cursor = db.execute(sql, params)
    names = [d[0] for d in cursor.description]
    return [dict(zip(names, row)) for row in cursor]


class ConfirmationReadRepository:
    def __init__(self, db):
        self.db = db

    def _scopes(self):
        # Events/PREPARED can identify a report before a FINAL report exists.
        return rows(self.db, "SELECT id AS analysis_report_id,revision_id,task_id AS analysis_run_id "
                    "FROM analysis_reports UNION SELECT analysis_report_id,revision_id,analysis_run_id "
                    "FROM analysis_snapshots UNION SELECT analysis_report_id,revision_id,analysis_run_id "
                    "FROM analysis_confirmation_events UNION SELECT analysis_report_id,revision_id,analysis_run_id "
                    "FROM analysis_human_overrides")

    def confirmation_history(self, report_id):
        return self.current_confirmation_state(report_id)

    def current_confirmation_state(self, report_id):
        text(report_id)
        scopes = [s for s in self._scopes() if s["analysis_report_id"] == report_id]
        if not scopes:
            return self._missing("REPORT_NOT_FOUND", report_id, None)
        require(len(scopes) == 1, "REPORT_REVISION_AMBIGUOUS")
        return self.confirmation_state(report_id, scopes[0]["revision_id"])

    def _missing(self, status, report_id, revision_id):
        return {"status": status, "analysis_report_id": report_id, "revision": revision_id,
                "generated_confirm_state": "UNKNOWN", "current_confirm_state": "UNKNOWN",
                "states": [], "events": [], "overrides": [], "required_items": None,
                "record_origin": "V1_1_PERSISTENCE"}

    def pending_confirmations(self, stock_id):
        text(stock_id)
        tasks = rows(self.db, "SELECT id FROM analysis_tasks WHERE stock_id=?", (stock_id,))
        task_ids = {t["id"] for t in tasks}
        scopes = [s for s in self._scopes() if s["analysis_run_id"] in task_ids]
        states = [self.confirmation_state(s["analysis_report_id"], s["revision_id"]) for s in scopes]
        unresolved = sorted(task_ids - {s["analysis_run_id"] for s in scopes})
        # Initial review plans have no physical carrier before PREPARED. Never
        # present an empty pending list as a complete or confirmed projection.
        complete = not unresolved and all(s["required_items"] is not None for s in states)
        return {"status": "OK" if complete else "CONFIRMATION_PLAN_UNAVAILABLE",
                "stock_id": stock_id, "unresolved_task_ids": unresolved,
                "pending": [s for s in states if s["current_confirm_state"] != "CONFIRMED"],
                "record_origin": "V1_1_PERSISTENCE"}

    def confirmation_state(self, analysis_report_id, revision_id):
        text(analysis_report_id)
        text(revision_id)
        scopes = [s for s in self._scopes() if s["analysis_report_id"] == analysis_report_id]
        if not scopes:
            return self._missing("REPORT_NOT_FOUND", analysis_report_id, revision_id)
        selected = [s for s in scopes if s["revision_id"] == revision_id]
        if not selected:
            return self._missing("REVISION_NOT_FOUND", analysis_report_id, revision_id)
        require(len(selected) == 1, "EVENT_BINDING_INVALID")
        events = rows(self.db, "SELECT * FROM analysis_confirmation_events WHERE analysis_report_id=? "
                      "AND revision_id=? ORDER BY event_sequence", (analysis_report_id, revision_id))
        states = replay_events(events)
        require(all(e["analysis_run_id"] == selected[0]["analysis_run_id"] for e in events),
                "EVENT_BINDING_INVALID")
        overrides = rows(self.db, "SELECT * FROM analysis_human_overrides WHERE analysis_report_id=? "
                         "AND revision_id=? ORDER BY override_id", (analysis_report_id, revision_id))
        for override in overrides:
            require(override["analysis_run_id"] == selected[0]["analysis_run_id"], "OVERRIDE_BINDING_INVALID")
            for key in ("replacement", "referenced_evidence_ids"):
                override[key] = loads(override[key])
        final = rows(self.db, "SELECT payload_json FROM analysis_snapshots WHERE analysis_report_id=? "
                     "AND revision_id=? AND snapshot_kind='FINAL'", (analysis_report_id, revision_id))
        generated = "PENDING"
        if final:
            snapshot = loads(final[0]["payload_json"])
            content = dict(snapshot)
            claimed = content.pop("final_snapshot_hash")
            require(claimed == digest(content) and snapshot["analysis_report_id"] == analysis_report_id
                    and snapshot["revision_id"] == revision_id, "FINAL_BINDING_INVALID")
            prefix = snapshot["confirmation_events"]
            require([dict((k, v) for k, v in e.items() if k != "event_sequence")
                     for e in events[:len(prefix)]] == prefix, "SNAPSHOT_LOG_PREFIX_INVALID")
            generated = snapshot["human_confirm"]
            current = states.get(("FINAL_REPORT", "REVIEW_FINAL_REPORT"), "PENDING")
            if any(e["current_state"] == "REJECTED" for e in events[len(prefix):]):
                current = "REJECTED"
        else:
            current = states.get(("FINAL_REPORT", "REVIEW_FINAL_REPORT"), "PENDING")
            if any(state == "REJECTED" for state in states.values()):
                current = "REJECTED"
        candidates = rows(self.db, "SELECT * FROM analysis_snapshots WHERE analysis_report_id=? "
                          "AND revision_id=? ORDER BY snapshot_kind", (analysis_report_id, revision_id))
        required = None
        if candidates:
            stored = candidates[0]
            plan = loads(stored["payload_json"])
            content = dict(plan)
            hash_key = "final_snapshot_hash" if stored["snapshot_kind"] == "FINAL" else "prepared_snapshot_hash"
            require(content.pop(hash_key) == digest(content) == stored["snapshot_hash"] and
                    plan["analysis_report_id"] == analysis_report_id and plan["revision_id"] == revision_id and
                    plan["analysis_run_id"] == selected[0]["analysis_run_id"], "SNAPSHOT_HASH_INVALID")
            required = [{"target": d["target"], "reason_code": d["reason_code"],
                         "details": d.get("details", {}), "required": d.get("required", True)}
                        for d in plan["confirmation_decisions"] if d["target"] != "FINAL_REPORT"]
            required += [{"target": "FINAL_REPORT", "reason_code": "REVIEW_FINAL_REPORT", "required": True}]
            required += [{"target": "HUMAN_OVERRIDE:" + o["override_id"],
                          "reason_code": "REVIEW_OVERRIDE", "required": True} for o in overrides]
            for item in required:
                item["current_state"] = states.get((item["target"], item["reason_code"]), "PENDING")
        return {"status": "OK" if required is not None else "CONFIRMATION_PLAN_UNAVAILABLE",
                "analysis_report_id": analysis_report_id, "revision": revision_id,
                "generated_confirm_state": generated, "current_confirm_state": current,
                "states": [{"target": target, "reason_code": reason, "current_state": state}
                           for (target, reason), state in states.items()], "events": events,
                "overrides": overrides, "required_items": required,
                "record_origin": "V1_1_PERSISTENCE"}


class EvidenceReadRepository:
    def __init__(self, db):
        self.db = db

    def evidence(self, evidence_id):
        text(evidence_id)
        found = rows(self.db, "SELECT * FROM evidence_records WHERE id=?", (evidence_id,))
        if not found:
            return None
        result = found[0]
        if result["calculation"] is not None:
            result["calculation"] = loads(result["calculation"])
        result.update(EVIDENCE_LIMITATIONS, record_origin="V1_1_DB_PROJECTION",
                      evidence_completeness="PARTIAL_DB_PROJECTION",
                      generated_confirm_state=result["human_confirm"],
                      current_confirm_state="UNKNOWN", revision=result["id"],
                      supersedes=result["supersedes_evidence_id"],
                      hashes={"raw_response_hash": None, "normalized_data_hash": None,
                              "analysis_output_hash": None})
        for key in ("endpoint_or_tool", "request_params", "provider_timestamp", "validation_result"):
            result[key] = None
        return result

    def for_analysis(self, analysis_report_id):
        text(analysis_report_id)
        final = rows(self.db, "SELECT input_evidence_ids,used_evidence_ids FROM analysis_final_evidence "
                     "WHERE analysis_report_id=?", (analysis_report_id,))
        if not final:
            return None
        inputs, used = (loads(final[0][key]) for key in ("input_evidence_ids", "used_evidence_ids"))
        return {"analysis_report_id": analysis_report_id, "input_evidence_ids": inputs,
                "used_evidence_ids": used, "evidence": [self.evidence(i) for i in inputs],
                "evidence_completeness": "PARTIAL_DB_PROJECTION", **EVIDENCE_LIMITATIONS}

    def field_evidence_trace(self, report_id, field):
        text(report_id)
        text(field)
        reports = AnalysisReadRepository(self.db)._reports()
        report = next((r for r in reports if r["id"] == report_id), None)
        if report is None:
            return {"status": "REPORT_NOT_FOUND", "analysis_report_id": report_id,
                    "field": field, "evidence": [], **EVIDENCE_LIMITATIONS}
        analysis = AnalysisReadRepository(self.db)._project(report)
        require(field in analysis["seven_fields"], "ANALYSIS_FIELD_INVALID")
        value = analysis["seven_fields"][field]
        projection = self.for_analysis(report_id)
        model_used = list(value["AI_ORIGINAL"]["evidence_ids"])
        override = value["HUMAN_OVERRIDE"]
        human_refs = [] if override is None else list(override["referenced_evidence_ids"])
        require(set(model_used) <= set(projection["used_evidence_ids"]) and
                set(human_refs) <= set(projection["input_evidence_ids"]), "EVIDENCE_BINDING_INVALID")
        ids = list(dict.fromkeys(model_used + human_refs))
        evidence = [self.evidence(i) for i in ids]
        return {"status": "OK" if all(e is not None for e in evidence) else "EVIDENCE_MISSING",
                "analysis_report_id": report_id, "field": field, "field_value": value,
                "input_evidence_ids": projection["input_evidence_ids"],
                "model_used_evidence_ids": model_used, "human_reference_evidence_ids": human_refs,
                "human_reference_origin": "HUMAN", "evidence": evidence,
                "evidence_completeness": "PARTIAL_DB_PROJECTION", **EVIDENCE_LIMITATIONS}

    def current_effective_evidence(self, evidence_id):
        text(evidence_id)
        records = {r["id"]: r for r in rows(self.db, "SELECT id,supersedes_evidence_id FROM evidence_records")}
        if evidence_id not in records:
            return {"status": "EVIDENCE_MISSING", "evidence": None, **EVIDENCE_LIMITATIONS}
        seen = set()
        node = evidence_id
        while node is not None:
            if node in seen:
                return {"status": "REVISION_CHAIN_CYCLE", "evidence": None, **EVIDENCE_LIMITATIONS}
            if node not in records:
                return {"status": "REVISION_PREDECESSOR_MISSING", "evidence": None, **EVIDENCE_LIMITATIONS}
            seen.add(node)
            root = node
            node = records[node]["supersedes_evidence_id"]
        node = root
        seen = set()
        while True:
            if node in seen:
                return {"status": "REVISION_CHAIN_CYCLE", "evidence": None, **EVIDENCE_LIMITATIONS}
            seen.add(node)
            successors = [r["id"] for r in records.values() if r["supersedes_evidence_id"] == node]
            if len(successors) > 1:
                return {"status": "REVISION_CHAIN_AMBIGUOUS", "BRANCH_COUNT": len(successors),
                        "evidence": None, **EVIDENCE_LIMITATIONS}
            if not successors:
                return {"status": "OK", "evidence": self.evidence(node), **EVIDENCE_LIMITATIONS}
            node = successors[0]


def revision_chain(reports, stock_id, *, report_id=None):
    """Pure graph check, independently testable even for corrupt external data."""
    index = {r["id"]: r for r in reports}
    relevant = [r for r in reports if r["stock_id"] == stock_id]
    if report_id is not None:
        if report_id not in index:
            return {"status": "REPORT_NOT_FOUND", "CURRENT_EFFECTIVE_REVISION": None}
        # Entire connected supersedes component, including siblings. A selected
        # branch cannot hide another successor, or an invalid cross-stock link.
        component = {report_id}
        while True:
            expanded = component | {r["id"] for r in reports
                                    if r["supersedes_analysis_report_id"] in component}
            expanded |= {index[i]["supersedes_analysis_report_id"] for i in component
                         if index[i]["supersedes_analysis_report_id"] in index}
            if expanded == component:
                break
            component = expanded
        relevant = [r for r in reports if r["id"] in component]
    children = {}
    for report in relevant:
        predecessor = report["supersedes_analysis_report_id"]
        if predecessor is not None:
            previous = index.get(predecessor)
            if previous is None:
                return {"status": "REVISION_PREDECESSOR_MISSING", "CURRENT_EFFECTIVE_REVISION": None}
            if previous["stock_id"] != report["stock_id"] or previous["task_id"] != report["task_id"]:
                return {"status": "REVISION_STOCK_OR_RUN_MISMATCH", "CURRENT_EFFECTIVE_REVISION": None}
            if report["supersedes_final_evidence_id"] != previous["final_evidence_id"]:
                return {"status": "REVISION_PREDECESSOR_BINDING_MISMATCH", "CURRENT_EFFECTIVE_REVISION": None}
            children.setdefault(predecessor, []).append(report["id"])
        elif report["supersedes_final_evidence_id"] is not None:
            return {"status": "REVISION_PREDECESSOR_BINDING_MISMATCH", "CURRENT_EFFECTIVE_REVISION": None}
        seen = set()
        node = report
        while node is not None:
            if node["id"] in seen:
                return {"status": "REVISION_CHAIN_CYCLE", "CURRENT_EFFECTIVE_REVISION": None}
            seen.add(node["id"])
            node = index.get(node["supersedes_analysis_report_id"])
    branch_count = max((len(v) for v in children.values()), default=0)
    leaves = [r["id"] for r in relevant if r["id"] not in children]
    if branch_count > 1 or len(leaves) > 1:
        return {"status": "REVISION_CHAIN_AMBIGUOUS", "BRANCH_COUNT": branch_count,
                "CURRENT_EFFECTIVE_REVISION": None}
    return {"status": "OK" if leaves else "NO_ANALYSIS", "BRANCH_COUNT": branch_count,
            "CURRENT_EFFECTIVE_REVISION": leaves[0] if leaves else None}


class AnalysisReadRepository:
    def __init__(self, db):
        self.db = db

    def _reports(self):
        return rows(self.db, "SELECT r.*,t.stock_id,f.supersedes_final_evidence_id "
                    "FROM analysis_reports r JOIN analysis_tasks t ON t.id=r.task_id "
                    "LEFT JOIN analysis_final_evidence f ON f.final_evidence_id=r.final_evidence_id")

    def _project(self, report):
        final = rows(self.db, "SELECT * FROM analysis_final_evidence WHERE final_evidence_id=?",
                     (report["final_evidence_id"],))
        snapshots = rows(self.db, "SELECT * FROM analysis_snapshots WHERE snapshot_hash=?",
                         (report["final_snapshot_hash"],))
        require(len(final) == len(snapshots) == 1, "FINAL_BINDING_MISSING")
        evidence, stored = final[0], snapshots[0]
        snapshot = loads(stored["payload_json"])
        content = dict(snapshot)
        claimed = content.pop("final_snapshot_hash")
        require(claimed == digest(content) == report["final_snapshot_hash"] and
                snapshot["confirmation_log_hash"] == digest(snapshot["confirmation_events"]), "SNAPSHOT_HASH_INVALID")
        require(stored["snapshot_kind"] == snapshot["final_state"] == "FINAL" and
                stored["analysis_run_id"] == evidence["analysis_run_id"] == report["task_id"] and
                stored["analysis_report_id"] == evidence["analysis_report_id"] == report["id"] and
                stored["revision_id"] == snapshot["revision_id"] == report["revision_id"] and
                evidence["stock_id"] == snapshot["stock_id"] == report["stock_id"] and
                evidence["final_snapshot_hash"] == claimed and
                snapshot["model_version"] == evidence["model_version"] == report["model_version"],
                "FINAL_BINDING_INVALID")
        for key, snapshot_key in (("input_hash", "input_hash"), ("model_output_hash", "model_output_hash"),
                                  ("validated_output_hash", "original_validated_output_hash"),
                                  ("confirmation_log_hash", "confirmation_log_hash"),
                                  ("confirmed_by", "confirmed_by"), ("confirmed_at", "confirmed_at")):
            require(evidence[key] == snapshot[snapshot_key], "FINAL_BINDING_INVALID")
        require(snapshot["analysis_run_id"] == report["task_id"] and
                snapshot["analysis_report_id"] == report["id"] and
                report["human_confirm"] == evidence["human_confirm"] == snapshot["human_confirm"] == "CONFIRMED" and
                snapshot["supersedes_analysis_report_id"] == report["supersedes_analysis_report_id"] and
                snapshot["confirmed_at"] == report["created_at"], "FINAL_BINDING_INVALID")
        inputs, used = (loads(evidence[k]) for k in ("input_evidence_ids", "used_evidence_ids"))
        require(len(set(inputs)) == len(inputs) and len(set(used)) == len(used) and set(used) <= set(inputs)
                and loads(report["evidence_ids"]) == used, "EVIDENCE_BINDING_INVALID")
        fields = {k: loads(report[k]) for k in snapshot["seven_fields"]}
        require(fields == snapshot["seven_fields"], "REPORT_FIELDS_INVALID")
        confirmation = ConfirmationReadRepository(self.db).confirmation_state(report["id"], report["revision_id"])
        hashes = {k: evidence[k] for k in ("input_hash", "model_output_hash", "validated_output_hash",
                                         "final_snapshot_hash", "confirmation_log_hash")}
        hashes["prepared_snapshot_hash"] = snapshot["prepared_snapshot_hash"]
        return {"analysis_report_id": report["id"], "analysis_run_id": report["task_id"],
                "stock_id": report["stock_id"], "revision": report["revision_id"],
                "supersedes": report["supersedes_analysis_report_id"], "model_version": report["model_version"],
                "seven_fields": fields, "hashes": hashes, "created_at": report["created_at"],
                "generated_confirm_state": confirmation["generated_confirm_state"],
                "current_confirm_state": confirmation["current_confirm_state"],
                "record_origin": "V1_1_PERSISTENCE", "evidence_completeness": "PARTIAL_DB_PROJECTION",
                **EVIDENCE_LIMITATIONS}

    def analysis_history(self, stock_id):
        text(stock_id)
        return [self._project(r) for r in self._reports() if r["stock_id"] == stock_id]

    def legacy_history(self):
        exists = self.db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='analysis_history'").fetchone()
        if not exists:
            return []
        # Legacy IDs are returned unchanged, without guessed stock/run mappings.
        return [{"legacy_record": r, "record_origin": "LEGACY_HISTORY",
                 "evidence_completeness": "LEGACY_UNKNOWN"} for r in rows(self.db, "SELECT * FROM analysis_history")]

    def current_effective_revision(self, report_id):
        """Resolve only the supersedes component containing this report."""
        text(report_id)
        reports = self._reports()
        report = next((r for r in reports if r["id"] == report_id), None)
        if report is None:
            return {"status": "REPORT_NOT_FOUND", "CURRENT_EFFECTIVE_REVISION": None, "analysis": None}
        return self._effective(reports, revision_chain(reports, report["stock_id"], report_id=report_id))

    def current_effective_analysis_for_stock(self, stock_id):
        text(stock_id)
        reports = self._reports()
        return self._effective(reports, revision_chain(reports, stock_id))

    def _effective(self, reports, chain):
        identity = chain["CURRENT_EFFECTIVE_REVISION"]
        if identity is None:
            return {**chain, "analysis": None}
        try:
            report = next(r for r in reports if r["id"] == identity)
            analysis = self._project(report)
        except PersistenceError as exc:
            return {"status": str(exc), "CURRENT_EFFECTIVE_REVISION": None, "analysis": None}
        if analysis["current_confirm_state"] != "CONFIRMED":
            return {"status": "CURRENT_REVISION_REJECTED" if analysis["current_confirm_state"] == "REJECTED"
                    else "CURRENT_REVISION_NOT_CONFIRMED", "current_confirm_state": analysis["current_confirm_state"],
                    "CURRENT_EFFECTIVE_REVISION": None, "analysis": None}
        return {**chain, "analysis": analysis}

    def latest_confirmed_analysis(self, stock_id):
        # Conservative: no timestamp tie-break across ambiguous revisions/roots.
        return self.current_effective_analysis_for_stock(stock_id)
