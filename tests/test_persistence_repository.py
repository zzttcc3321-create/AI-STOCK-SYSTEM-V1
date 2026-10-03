from dataclasses import replace
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from analysis import ConfirmationFlow, finalize_analysis
from persistence.connection import configure, transaction
from persistence.contracts import PersistenceError
from persistence.migration import apply_migration
from persistence.repository import SQLitePersistenceRepository
from persistence.schema_v1_1 import NEW_TABLES
from persistence.serialization import digest, dumps, loads
from test_human_confirmation import ACTOR, STAMP, confirm_remaining, source_records
from test_persistence_schema_v1_1 import fixture


def persist_run(repo, execution, flow, rows, *, records=()):
    task = rows[0][1]
    return repo.persist_analysis_run(flow.draft, flow.run_evidence, execution.prompt_envelope,
        task_date=task["task_date"], created_by=task["created_by"], raw_model_output=task["raw_model_output"],
        evidence_created_at=STAMP, evidence_records=records)


def persist_final(repo, flow, prepared, final):
    return repo.persist_finalization(flow, prepared, final,
                                    evidence_records=source_records(flow.run_evidence), evidence_created_at=STAMP)


class DeferredCommitConnection(sqlite3.Connection):
    fail_commit = False

    def commit(self):
        if self.fail_commit:
            self.execute("INSERT INTO legacy_child VALUES('missing')")
        return super().commit()


class SerializationTests(unittest.TestCase):
    def test_canonical_unicode_numbers_timezone_and_enums(self):
        from evidence import HumanConfirm
        value = {"中文": [None, True, 1, 1.0, STAMP], "state": HumanConfirm.PENDING}
        encoded = dumps(value)
        self.assertIn("中文", encoded)
        self.assertEqual(encoded, dumps(dict(reversed(list(value.items())))))
        self.assertEqual(loads(encoded)["中文"][-1], STAMP)
        self.assertEqual(type(loads(encoded)["中文"][2]), int)
        self.assertEqual(type(loads(encoded)["中文"][3]), float)
        self.assertEqual(digest(value), digest(loads(encoded)))

    def test_duplicates_nonfinite_implicit_repr_nonstring_keys_rejected(self):
        for value in ('{"a":1,"a":2}', '{"a":1,"\\u0061":2}', '{"x":NaN}',
                      '{"x":Infinity}', '{"x":-Infinity}', '{"x":1e999}', b'{}'):
            with self.subTest(value=value), self.assertRaises(PersistenceError):
                loads(value)
        for value in ({1: "bad"}, {"x": float("nan")}, {"x": float("inf")}, {"x": object()}, {"x": Path("bad")}):
            with self.assertRaises(PersistenceError):
                dumps(value)


class RepositoryTests(unittest.TestCase):
    def setUp(self):
        self.db = configure(sqlite3.connect(":memory:", factory=DeferredCommitConnection))
        self.addCleanup(self.db.close)
        apply_migration(self.db)
        self.repo = SQLitePersistenceRepository(self.db, allow_synthetic_evidence=True)
        self.execution, self.flow, self.prepared, self.final, self.rows = fixture()
        persist_run(self.repo, self.execution, self.flow, self.rows)

    def assert_core_empty(self):
        for table in NEW_TABLES:
            if table != "analysis_tasks":
                self.assertEqual(self.db.execute(f"SELECT count(*) FROM {table}").fetchone()[0], 0, table)
        self.assertFalse(self.db.in_transaction)

    def test_e2e_hash_roundtrip_idempotency_and_only_status_update(self):
        self.assertEqual(persist_final(self.repo, self.flow, self.prepared, self.final), "PERSISTED")
        before = self.db.total_changes
        self.assertEqual(persist_final(self.repo, self.flow, self.prepared, self.final), "IDEMPOTENT_NOOP")
        self.assertEqual(self.db.total_changes, before)
        task = self.db.execute("SELECT * FROM analysis_tasks").fetchone()
        run = loads(task["run_evidence_json"])
        draft = loads(task["validated_draft_json"])
        self.assertEqual(run, self.flow.run_evidence.to_dict())
        self.assertEqual(draft, self.flow.draft.to_dict())
        self.assertNotIn("_gate_digest", run)
        self.assertEqual(digest(draft["output"]), run["validated_output_hash"])
        for candidate, key in ((self.prepared, "prepared_snapshot_hash"), (self.final.snapshot, "final_snapshot_hash")):
            restored = loads(self.db.execute("SELECT payload_json FROM analysis_snapshots WHERE snapshot_hash=?",
                             (getattr(candidate, key),)).fetchone()[0])
            self.assertEqual(restored, candidate.to_dict())
            self.assertEqual(restored["confirmation_log_hash"], digest(restored["confirmation_events"]))
            self.assertEqual(restored.pop(key), digest(restored))
            self.assertNotIn("_gate_digest", restored)
        self.assertEqual(self.repo.update_task_status(self.flow.run_evidence.analysis_run_id, "ARCHIVED"), "UPDATED")
        self.assertEqual(persist_run(self.repo, self.execution, self.flow, self.rows), "IDEMPOTENT_NOOP")
        self.assertEqual(self.repo.update_task_status(self.flow.run_evidence.analysis_run_id, "ARCHIVED"), "IDEMPOTENT_NOOP")
        self.assertEqual(self.db.execute("SELECT status FROM analysis_tasks").fetchone()[0], "ARCHIVED")

    def test_all_immutable_row_identity_conflicts_fail_closed(self):
        persist_final(self.repo, self.flow, self.prepared, self.final)
        for table, column in (("analysis_tasks", "created_by"), ("evidence_records", "created_at"),
                              ("analysis_confirmation_events", "decision_reason"),
                              ("analysis_human_overrides", "reason"), ("analysis_snapshots", "payload_json"),
                              ("analysis_reports", "model_version"), ("analysis_final_evidence", "model_version")):
            row = dict(self.db.execute(f"SELECT * FROM {table} LIMIT 1").fetchone())
            row[column] += "changed"
            with self.subTest(table=table), self.assertRaisesRegex(PersistenceError, "IDENTITY_CONFLICT"):
                self.repo._operation([(table, row)])

    def test_failure_after_every_finalization_insert_rolls_back(self):
        steps = []
        original = self.repo._insert
        def observe(table, row):
            steps.append(table)
            return original(table, row)
        with patch.object(self.repo, "_insert", side_effect=observe):
            persist_final(self.repo, self.flow, self.prepared, self.final)
        # Fresh memory DB for each failure; task is the only preexisting row.
        for failure in range(len(steps)):
            with self.subTest(step=failure, table=steps[failure]):
                db = configure(sqlite3.connect(":memory:"))
                try:
                    apply_migration(db)
                    repo = SQLitePersistenceRepository(db, allow_synthetic_evidence=True)
                    persist_run(repo, self.execution, self.flow, self.rows)
                    original_insert = repo._insert
                    count = [0]
                    def injected(table, row):
                        result = original_insert(table, row)
                        ordinal = count[0]
                        count[0] += 1
                        if ordinal == failure:
                            raise RuntimeError("injected insert failure")
                        return result
                    with patch.object(repo, "_insert", side_effect=injected):
                        with self.assertRaisesRegex(RuntimeError, "injected"):
                            persist_final(repo, self.flow, self.prepared, self.final)
                    self.assertFalse(db.in_transaction)
                    for table in NEW_TABLES:
                        self.assertEqual(db.execute(f"SELECT count(*) FROM {table}").fetchone()[0],
                                         1 if table == "analysis_tasks" else 0)
                finally:
                    db.close()

    def test_deferred_fk_failure_at_actual_commit_rolls_back(self):
        self.db.executescript("CREATE TABLE legacy_parent(id TEXT PRIMARY KEY); "
                             "CREATE TABLE legacy_child(id TEXT REFERENCES legacy_parent(id) DEFERRABLE INITIALLY DEFERRED);")
        self.db.fail_commit = True
        with self.assertRaisesRegex(sqlite3.IntegrityError, "FOREIGN KEY"):
            persist_final(self.repo, self.flow, self.prepared, self.final)
        self.db.fail_commit = False
        self.assert_core_empty()
        self.assertEqual(self.db.execute("SELECT count(*) FROM legacy_child").fetchone()[0], 0)

    def test_pending_progress_then_finalization_and_post_final_rejection(self):
        persist_run(self.repo, self.execution, self.flow, self.rows, records=source_records(self.flow.run_evidence))
        self.repo.persist_confirmation_progress(self.flow, prepared=self.prepared)
        self.assertEqual(self.db.execute("SELECT count(*) FROM analysis_reports").fetchone()[0], 0)
        self.assertEqual(persist_final(self.repo, self.flow, self.prepared, self.final), "PERSISTED")
        before = self.db.execute("SELECT payload_json FROM analysis_snapshots WHERE snapshot_kind='FINAL'").fetchone()[0]
        self.flow.reject("market_view", "REVIEW_FIELD", actor_id=ACTOR, reason="修订", created_at=STAMP)
        self.repo.persist_confirmation_progress(self.flow, prepared=self.prepared)
        self.assertEqual(before, self.db.execute("SELECT payload_json FROM analysis_snapshots WHERE snapshot_kind='FINAL'").fetchone()[0])

    def test_real_provider_disabled_even_with_flag_outside_temporary_directory(self):
        runtime = SQLitePersistenceRepository(self.db)
        with self.assertRaisesRegex(PersistenceError, "RUNTIME_WRITE_DISABLED"):
            persist_final(runtime, self.flow, self.prepared, self.final)
        with patch("persistence.repository.is_temporary_database", return_value=False):
            with self.assertRaisesRegex(PersistenceError, "RUNTIME_WRITE_DISABLED"):
                persist_final(self.repo, self.flow, self.prepared, self.final)
            with self.assertRaisesRegex(PersistenceError, "RUNTIME_WRITE_DISABLED"):
                persist_run(self.repo, self.execution, self.flow, self.rows, records=source_records(self.flow.run_evidence))
        self.assert_core_empty()

    def test_explicit_envelope_required_raw_hash_unsealed_domain_and_ids_rejected(self):
        values = dict(task_date="2026-10-03", created_by="creator", raw_model_output=self.rows[0][1]["raw_model_output"],
                      evidence_created_at=STAMP)
        for name in values:
            incomplete = dict(values)
            del incomplete[name]
            with self.assertRaises(TypeError):
                self.repo.persist_analysis_run(self.flow.draft, self.flow.run_evidence, self.execution.prompt_envelope, **incomplete)
        for name, bad in (("task_date", "2026-10-3"), ("created_by", 1),
                          ("raw_model_output", "{}"), ("evidence_created_at", "2026-10-03T00:00:00")):
            with self.assertRaises(ValueError):
                self.repo.persist_analysis_run(self.flow.draft, self.flow.run_evidence, self.execution.prompt_envelope,
                                               **{**values, name: bad})
        with self.assertRaises(ValueError):
            self.repo.persist_analysis_run(replace(self.flow.draft), self.flow.run_evidence, self.execution.prompt_envelope, **values)
        with self.assertRaises(ValueError):
            self.repo.persist_analysis_run(self.flow.draft, replace(self.flow.run_evidence), self.execution.prompt_envelope, **values)
        with self.assertRaisesRegex(PersistenceError, "TEXT"):
            self.repo.update_task_status(123, "READY")

    def test_missing_evidence_or_foreign_prepared_fail_without_partial_rows(self):
        with self.assertRaisesRegex(PersistenceError, "INPUT_EVIDENCE"):
            self.repo.persist_finalization(self.flow, self.prepared, self.final, evidence_records=(), evidence_created_at=STAMP)
        _, _, other_prepared, _, _ = fixture()
        with self.assertRaises(ValueError):
            persist_final(self.repo, self.flow, other_prepared, self.final)
        self.assert_core_empty()

    def test_branches_are_stored_but_wrong_predecessor_pair_is_rejected(self):
        persist_final(self.repo, self.flow, self.prepared, self.final)
        self.flow.reject("market_view", "REVIEW_FIELD", actor_id=ACTOR, reason="修订", created_at=STAMP)
        self.repo.persist_confirmation_progress(self.flow, prepared=self.prepared)
        for ordinal in (1, 2):
            flow = self.flow.new_revision(analysis_report_id=f"REV-{ordinal}")
            confirm_remaining(flow)
            prepared = flow.prepare_final_snapshot()
            flow.confirm_final_report(prepared, actor_id=ACTOR, created_at=STAMP)
            final = finalize_analysis(flow, prepared, evidence_records=source_records(flow.run_evidence), final_evidence_id=f"FINAL-{ordinal}")
            persist_final(self.repo, flow, prepared, final)
        self.assertEqual(self.db.execute("SELECT count(*) FROM analysis_reports WHERE supersedes_analysis_report_id=?",
                                        (self.final.snapshot.analysis_report_id,)).fetchone()[0], 2)
        # Isolate DB predecessor checks with simple DTOs (no reconstructed seals).
        from types import SimpleNamespace
        corrupt = SimpleNamespace(snapshot=SimpleNamespace(analysis_report_id="new", analysis_run_id=self.flow.run_evidence.analysis_run_id,
            stock_id=self.flow.run_evidence.stock_id, supersedes_analysis_report_id=self.final.snapshot.analysis_report_id),
            evidence=SimpleNamespace(supersedes_final_evidence_id="FINAL-1"))
        with self.assertRaisesRegex(PersistenceError, "PREDECESSOR_BINDING"):
            self.repo._predecessor(corrupt)

    def test_active_transaction_is_not_committed_or_rolled_back_by_repository(self):
        self.db.execute("BEGIN")
        try:
            with self.assertRaisesRegex(PersistenceError, "ACTIVE_TRANSACTION"):
                self.repo.update_task_status(self.flow.run_evidence.analysis_run_id, "x")
            self.assertTrue(self.db.in_transaction)
        finally:
            self.db.rollback()
