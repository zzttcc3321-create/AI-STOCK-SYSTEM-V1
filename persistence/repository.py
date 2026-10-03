"""Append-only core. Synthetic Provider projections require a temporary DB.

Real Provider Evidence writes are disabled until #012B supplies the archive
contract. No API here reads, writes or resolves sidecars.
"""
from datetime import date
import hashlib

from analysis.analysis_evidence import validate_analysis_run_evidence
from analysis.confirmation_contracts import aware_time, validate_confirmation_input, validate_prepared_snapshot
from analysis.finalization import trace_final_analysis, validate_finalization_result
from analysis.output_contracts import validate_draft
from analysis.output_validator import prompt_inputs
from evidence.contracts import EvidenceRecord, EvidenceSource, HumanConfirm

from .connection import is_temporary_database, transaction
from .contracts import require, replay_events, text
from .migration import NEW_TABLES, checks, quote, schema_state
from .serialization import digest, dumps, loads

KEYS = {"analysis_confirmation_events": "event_id", "analysis_human_overrides": "override_id",
        "analysis_snapshots": "snapshot_hash", "analysis_final_evidence": "final_evidence_id"}


def task_row(draft, run, envelope, *, task_date, created_by, raw_model_output, evidence_created_at):
    validate_draft(draft)
    validate_analysis_run_evidence(run)
    validate_confirmation_input(draft, run)
    prompt_inputs(envelope)
    require(type(task_date) is str and date.fromisoformat(task_date).isoformat() == task_date,
            "TASK_DATE_REQUIRED")
    text(created_by)
    aware_time(evidence_created_at)
    require(type(raw_model_output) is str, "RAW_MODEL_OUTPUT_REQUIRED")
    loads(raw_model_output)  # Validate JSON, but store and hash the original bytes.
    require(hashlib.sha256(raw_model_output.encode("utf-8")).hexdigest() == run.model_output_hash,
            "MODEL_OUTPUT_HASH_MISMATCH")
    require(envelope.stock_id == run.stock_id and envelope.input_hash == run.input_hash and
            envelope.template_hash == run.template_hash and envelope.evidence_ids == run.input_evidence_ids
            and envelope.prompt_version == run.prompt_version, "PROMPT_BINDING_INVALID")
    return dict(id=run.analysis_run_id, stock_id=run.stock_id, task_date=task_date,
                prompt_version=run.prompt_version, model_version=run.model_version,
                status=run.analysis_status, created_by=created_by, created_at=run.created_at,
                run_evidence_json=dumps(run.to_dict()), validated_draft_json=dumps(draft.to_dict()),
                prompt_envelope_json=dumps(envelope.to_dict()), raw_model_output=raw_model_output)


def evidence_row(record, created_at):
    require(type(record) is EvidenceRecord and type(record.source) is EvidenceSource and
            type(record.human_confirm) is HumanConfirm, "PROVIDER_EVIDENCE_INVALID")
    for value in (record.evidence_id, record.collection_task_id, record.system_run_id):
        text(value)
    aware_time(record.request_timestamp)
    aware_time(created_at)
    for value in (record.raw_response_path, record.model_version, record.supersedes_evidence_id):
        require(value is None or type(value) is str, "ID_COERCION_FORBIDDEN")
    return dict(id=record.evidence_id, analysis_report_id=None, source=record.source.value,
                timestamp=record.request_timestamp, raw_data_path=record.raw_response_path,
                calculation=None if record.normalization_rule is None else
                dumps({"normalization_rule": record.normalization_rule}), model_version=record.model_version,
                human_confirm=record.human_confirm.value, collection_task_id=record.collection_task_id,
                system_run_id=record.system_run_id, created_at=created_at,
                supersedes_evidence_id=record.supersedes_evidence_id)


def snapshot_row(candidate, kind):
    return dict(snapshot_hash=getattr(candidate, "prepared_snapshot_hash" if kind == "PREPARED"
                                     else "final_snapshot_hash"), snapshot_kind=kind,
                analysis_run_id=candidate.analysis_run_id, analysis_report_id=candidate.analysis_report_id,
                revision_id=candidate.revision_id, payload_json=dumps(candidate.to_dict()))


class SQLitePersistenceRepository:
    def __init__(self, db, *, allow_synthetic_evidence=False):
        require(type(allow_synthetic_evidence) is bool, "EXPLICIT_SYNTHETIC_FLAG_REQUIRED")
        require(schema_state(db) == "B", "SCHEMA_DRIFT")
        self.db = db
        self.allow_synthetic_evidence = allow_synthetic_evidence

    def _provider_guard(self):
        require(self.allow_synthetic_evidence and is_temporary_database(self.db),
                "REAL_PROVIDER_EVIDENCE_RUNTIME_WRITE_DISABLED")

    def _insert(self, table, row):
        require(table in NEW_TABLES, "TABLE_NOT_AUTHORIZED")
        key = KEYS.get(table, "id")
        text(row[key])
        existing = self.db.execute(f"SELECT * FROM {quote(table)} WHERE {quote(key)}=?",
                                   (row[key],)).fetchone()
        if existing is not None:
            expected, actual = dict(row), dict(existing)
            if table == "analysis_tasks":
                # Only the independent status API may update this mutable column.
                expected.pop("status")
                actual.pop("status")
            require(actual == expected, "IDENTITY_CONFLICT")
            return False
        self.db.execute(f"INSERT INTO {quote(table)} ({','.join(quote(k) for k in row)}) "
                        f"VALUES ({','.join('?' for _ in row)})", tuple(row.values()))
        return True

    def _operation(self, rows, validate=lambda: None, before=lambda: None):
        def operation():
            before()
            changed = False
            for table, row in rows:
                changed = self._insert(table, row) or changed
            validate()
            return "PERSISTED" if changed else "IDEMPOTENT_NOOP"
        return transaction(self.db, operation)

    def persist_analysis_run(self, draft, run, envelope, *, task_date, created_by,
                             raw_model_output, evidence_created_at, evidence_records=()):
        row = task_row(draft, run, envelope, task_date=task_date, created_by=created_by,
                       raw_model_output=raw_model_output, evidence_created_at=evidence_created_at)
        records = tuple(evidence_records)
        if records:
            self._provider_guard()
        identifiers = [r.evidence_id for r in records]
        require(len(set(identifiers)) == len(identifiers) and
                set(identifiers) <= set(run.input_evidence_ids), "EVIDENCE_BINDING_INVALID")
        return self._operation([("analysis_tasks", row)] +
                               [("evidence_records", evidence_row(r, evidence_created_at)) for r in records])

    def _check_task(self, draft, run):
        validate_confirmation_input(draft, run)
        task = self.db.execute("SELECT * FROM analysis_tasks WHERE id=?", (run.analysis_run_id,)).fetchone()
        require(task is not None, "TASK_MISSING")
        require(loads(task["run_evidence_json"]) == run.to_dict() and
                loads(task["validated_draft_json"]) == draft.to_dict() and
                task["stock_id"] == run.stock_id and task["model_version"] == run.model_version and
                task["prompt_version"] == run.prompt_version and task["created_at"] == run.created_at,
                "TASK_BINDING_INVALID")
        envelope = loads(task["prompt_envelope_json"])
        for key in ("input_hash", "NETWORK_CALL_COUNT", "MODEL_CALL_COUNT", "DATABASE_WRITE"):
            envelope.pop(key)
        require(digest(envelope) == run.input_hash and
                digest(draft.output) == run.validated_output_hash and
                hashlib.sha256(task["raw_model_output"].encode()).hexdigest() == run.model_output_hash,
                "TASK_HASH_MISMATCH")

    def _progress_rows(self, flow, prepared):
        flow.validate()
        rows = []
        for ordinal, event in enumerate(flow.events):
            rows.append(("analysis_confirmation_events", dict(**event.to_dict(), event_sequence=ordinal)))
        for override in flow.human_overrides:
            row = override.to_dict()
            for key in ("replacement", "referenced_evidence_ids"):
                row[key] = dumps(row[key])
            rows.append(("analysis_human_overrides", row))
        if prepared is not None:
            validate_prepared_snapshot(prepared)
            require((prepared.analysis_run_id, prepared.analysis_report_id, prepared.revision_id,
                     prepared.stock_id, prepared.model_version) ==
                    (flow.run_evidence.analysis_run_id, flow.analysis_report_id, flow.revision_id,
                     flow.run_evidence.stock_id, flow.run_evidence.model_version), "PREPARED_BINDING_INVALID")
            require(prepared.original_validated_output_hash == flow.draft.validated_output_hash and
                    prepared.to_dict()["confirmation_events"] ==
                    [e.to_dict() for e in flow.events[:len(prepared.confirmation_events)]], "PREPARED_LOG_INVALID")
            rows.append(("analysis_snapshots", snapshot_row(prepared, "PREPARED")))
        return rows

    def _check_progress(self, flow):
        events = [dict(r) for r in self.db.execute(
            "SELECT * FROM analysis_confirmation_events WHERE analysis_report_id=? AND revision_id=? "
            "ORDER BY event_sequence", (flow.analysis_report_id, flow.revision_id))]
        require(events == [dict(**e.to_dict(), event_sequence=n) for n, e in enumerate(flow.events)],
                "EVENT_LOG_CONFLICT")
        replay_events(events)
        overrides = [dict(r) for r in self.db.execute(
            "SELECT * FROM analysis_human_overrides WHERE analysis_report_id=? AND revision_id=?",
            (flow.analysis_report_id, flow.revision_id))]
        require({r["override_id"] for r in overrides} == {o.override_id for o in flow.human_overrides},
                "OVERRIDE_BINDING_INVALID")
        for event in events:
            if event["override_id"]:
                require(event["override_id"] in {r["override_id"] for r in overrides}, "OVERRIDE_BINDING_INVALID")
            if event["reviewed_snapshot_hash"]:
                candidate = self.db.execute("SELECT * FROM analysis_snapshots WHERE snapshot_hash=?",
                                            (event["reviewed_snapshot_hash"],)).fetchone()
                require(candidate is not None and candidate["snapshot_kind"] == "PREPARED" and
                        (candidate["analysis_run_id"], candidate["analysis_report_id"], candidate["revision_id"]) ==
                        (event["analysis_run_id"], event["analysis_report_id"], event["revision_id"]),
                        "REVIEWED_SNAPSHOT_BINDING_INVALID")
        for override in flow.human_overrides:
            require(set(override.referenced_evidence_ids) <= set(flow.run_evidence.input_evidence_ids),
                    "OVERRIDE_EVIDENCE_INVALID")
            require(all(self.db.execute("SELECT 1 FROM evidence_records WHERE id=?", (i,)).fetchone()
                        for i in override.referenced_evidence_ids), "OVERRIDE_EVIDENCE_MISSING")

    def persist_confirmation_progress(self, flow, *, prepared=None):
        rows = self._progress_rows(flow, prepared)
        def validate():
            self._check_task(flow.draft, flow.run_evidence)
            self._check_progress(flow)
        return self._operation(rows, validate, before=lambda: self._check_task(flow.draft, flow.run_evidence))

    def _predecessor(self, final):
        snapshot, evidence = final.snapshot, final.evidence
        report_id = snapshot.supersedes_analysis_report_id
        evidence_id = evidence.supersedes_final_evidence_id
        require((report_id is None) == (evidence_id is None), "PREDECESSOR_BINDING_INVALID")
        seen = {snapshot.analysis_report_id}
        while report_id is not None:
            require(report_id not in seen, "REVISION_CYCLE")
            seen.add(report_id)
            previous = self.db.execute(
                "SELECT r.*,t.stock_id FROM analysis_reports r JOIN analysis_tasks t ON t.id=r.task_id "
                "WHERE r.id=?", (report_id,)).fetchone()
            previous_evidence = self.db.execute(
                "SELECT * FROM analysis_final_evidence WHERE final_evidence_id=?", (evidence_id,)).fetchone()
            require(previous is not None and previous_evidence is not None, "PREDECESSOR_MISSING")
            require(previous["stock_id"] == previous_evidence["stock_id"] == snapshot.stock_id and
                    previous["task_id"] == previous_evidence["analysis_run_id"] == snapshot.analysis_run_id and
                    previous["final_evidence_id"] == evidence_id and
                    previous_evidence["analysis_report_id"] == report_id, "PREDECESSOR_BINDING_INVALID")
            report_id = previous["supersedes_analysis_report_id"]
            evidence_id = previous_evidence["supersedes_final_evidence_id"]
            require((report_id is None) == (evidence_id is None), "PREDECESSOR_BINDING_INVALID")

    def persist_finalization(self, flow, prepared, final, *, evidence_records, evidence_created_at):
        self._provider_guard()  # No real finalization may bypass the deferred metadata contract.
        validate_prepared_snapshot(prepared)
        validate_finalization_result(final, flow.draft, flow.run_evidence)
        flow.validate()
        snapshot = final.snapshot
        require((snapshot.analysis_report_id, snapshot.revision_id, snapshot.analysis_run_id) ==
                (flow.analysis_report_id, flow.revision_id, flow.run_evidence.analysis_run_id),
                "FINALIZATION_BINDING_INVALID")
        require(snapshot.prepared_snapshot_hash == prepared.prepared_snapshot_hash and
                snapshot.to_dict()["confirmation_events"] == [e.to_dict() for e in flow.events] and
                snapshot.to_dict()["seven_fields"] == prepared.to_dict()["seven_fields"] and
                prepared.to_dict()["confirmation_events"] == snapshot.to_dict()["confirmation_events"][:-1] and
                snapshot.supersedes_analysis_report_id == prepared.supersedes_analysis_report_id and
                final.evidence.supersedes_final_evidence_id == prepared.supersedes_final_evidence_id,
                "FINALIZATION_BINDING_INVALID")
        records = tuple(evidence_records)
        require(len({r.evidence_id for r in records}) == len(records) and
                {r.evidence_id for r in records} == set(flow.run_evidence.input_evidence_ids),
                "INPUT_EVIDENCE_MISSING_OR_EXTRA")
        # This is the existing in-memory domain gate over synthetic records.
        # It does not establish persisted raw/sidecar integrity.
        trace_final_analysis(final, flow.draft, flow.run_evidence, evidence_records=records)
        report = dict(id=snapshot.analysis_report_id, task_id=snapshot.analysis_run_id,
                      **{k: dumps(v) for k, v in snapshot.seven_fields.items()},
                      human_confirm="CONFIRMED", created_at=snapshot.confirmed_at,
                      revision_id=snapshot.revision_id, model_version=snapshot.model_version,
                      calculation=dumps(final.conclusion.calculation), evidence_ids=dumps(final.conclusion.evidence_ids),
                      supersedes_analysis_report_id=snapshot.supersedes_analysis_report_id,
                      final_snapshot_hash=snapshot.final_snapshot_hash, final_evidence_id=final.evidence.final_evidence_id)
        evidence = final.evidence.to_dict()
        for key in ("input_evidence_ids", "used_evidence_ids"):
            evidence[key] = dumps(evidence[key])
        rows = [("evidence_records", evidence_row(r, evidence_created_at)) for r in records]
        rows += self._progress_rows(flow, prepared)
        rows += [("analysis_snapshots", snapshot_row(snapshot, "FINAL")),
                 ("analysis_reports", report), ("analysis_final_evidence", evidence)]

        def validate():
            self._check_task(flow.draft, flow.run_evidence)
            self._check_progress(flow)
            self._predecessor(final)
            require(all(self.db.execute("SELECT 1 FROM evidence_records WHERE id=?", (i,)).fetchone()
                        for i in flow.run_evidence.input_evidence_ids), "INPUT_EVIDENCE_MISSING")
            checks(self.db)
        def before():
            self._check_task(flow.draft, flow.run_evidence)
            self._predecessor(final)
        return self._operation(rows, validate, before=before)

    def update_task_status(self, analysis_run_id, status):
        text(analysis_run_id)
        text(status)
        def update():
            existing = self.db.execute("SELECT status FROM analysis_tasks WHERE id=?", (analysis_run_id,)).fetchone()
            require(existing is not None, "TASK_MISSING")
            if existing[0] == status:
                return "IDEMPOTENT_NOOP"
            self.db.execute("UPDATE analysis_tasks SET status=? WHERE id=?", (status, analysis_run_id))
            return "UPDATED"
        return transaction(self.db, update)
