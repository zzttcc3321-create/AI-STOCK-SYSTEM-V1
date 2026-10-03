from copy import deepcopy
import sqlite3
import unittest

from analysis import finalize_analysis
from persistence.connection import configure
from persistence.contracts import EVIDENCE_LIMITATIONS, PersistenceError, replay_events
from persistence.migration import apply_migration
from persistence.read_models import (AnalysisReadRepository, ConfirmationReadRepository,
                                     EvidenceReadRepository, revision_chain)
from persistence.repository import SQLitePersistenceRepository, evidence_row
from test_human_confirmation import ACTOR, STAMP, confirm_remaining, source_records
from test_persistence_repository import persist_final, persist_run
from test_persistence_schema_v1_1 import fixture


class ReadModelTests(unittest.TestCase):
    def setUp(self):
        self.db = configure(sqlite3.connect(":memory:"))
        self.addCleanup(self.db.close)
        self.db.execute("CREATE TABLE analysis_history(id INTEGER PRIMARY KEY, conclusion TEXT)")
        self.db.execute("INSERT INTO analysis_history VALUES(1,'synthetic legacy')")
        self.db.commit()
        apply_migration(self.db)
        self.repo = SQLitePersistenceRepository(self.db, allow_synthetic_evidence=True)
        self.execution, self.flow, self.prepared, self.final, self.rows = fixture()
        persist_run(self.repo, self.execution, self.flow, self.rows)
        persist_final(self.repo, self.flow, self.prepared, self.final)
        self.analysis = AnalysisReadRepository(self.db)
        self.evidence = EvidenceReadRepository(self.db)
        self.confirmation = ConfirmationReadRepository(self.db)

    def correction(self, report_id):
        flow = self.flow.new_revision(analysis_report_id=report_id)
        confirm_remaining(flow)
        prepared = flow.prepare_final_snapshot()
        flow.confirm_final_report(prepared, actor_id=ACTOR, created_at=STAMP)
        final = finalize_analysis(flow, prepared, evidence_records=source_records(flow.run_evidence), final_evidence_id="FE-" + report_id)
        persist_final(self.repo, flow, prepared, final)
        return flow, prepared, final

    def reject_original(self):
        self.flow.reject("market_view", "REVIEW_FIELD", actor_id=ACTOR, reason="需修订", created_at=STAMP)
        self.repo.persist_confirmation_progress(self.flow, prepared=self.prepared)

    def test_analysis_original_effective_decisions_hashes_timezone(self):
        before = self.db.total_changes
        result = self.analysis.latest_confirmed_analysis(self.flow.run_evidence.stock_id)
        self.assertEqual(result["status"], "OK")
        analysis = result["analysis"]
        self.assertEqual(analysis["seven_fields"], self.final.snapshot.to_dict()["seven_fields"])
        self.assertEqual(analysis["seven_fields"]["risk_level"]["DECISION"], "MODIFIED")
        self.assertIsNone(analysis["seven_fields"]["operation_plan"]["EFFECTIVE_VALUE"])
        self.assertEqual(analysis["revision"], self.flow.revision_id)
        self.assertEqual(analysis["created_at"], STAMP)
        self.assertEqual(analysis["hashes"]["prepared_snapshot_hash"], self.prepared.prepared_snapshot_hash)
        self.assertEqual(analysis["hashes"]["final_snapshot_hash"], self.final.snapshot.final_snapshot_hash)
        self.assertEqual(analysis["generated_confirm_state"], "CONFIRMED")
        self.assertEqual(analysis["current_confirm_state"], "CONFIRMED")
        self.assertEqual(analysis["integrity_status"], "NOT_VERIFIED")
        self.assertEqual(self.db.total_changes, before)

    def test_confirmation_replays_postfinal_rejection_and_does_not_restore_predecessor(self):
        self.reject_original()
        state = self.confirmation.confirmation_state(self.flow.analysis_report_id, self.flow.revision_id)
        self.assertEqual(state["generated_confirm_state"], "CONFIRMED")
        self.assertEqual(state["current_confirm_state"], "REJECTED")
        rejected = self.analysis.latest_confirmed_analysis(self.flow.run_evidence.stock_id)
        self.assertEqual(rejected["status"], "CURRENT_REVISION_REJECTED")
        self.assertEqual(rejected["current_confirm_state"], "REJECTED")
        self.assertIsNone(rejected["CURRENT_EFFECTIVE_REVISION"])
        self.assertIsNone(rejected["analysis"])
        flow, prepared, final = self.correction("CORRECTION")
        self.assertEqual(self.analysis.current_effective_revision(flow.analysis_report_id)["CURRENT_EFFECTIVE_REVISION"], "CORRECTION")
        flow.reject("market_view", "REVIEW_FIELD", actor_id=ACTOR, reason="再次修订", created_at=STAMP)
        self.repo.persist_confirmation_progress(flow, prepared=prepared)
        rejected2 = self.analysis.latest_confirmed_analysis(flow.run_evidence.stock_id)
        self.assertEqual(rejected2["status"], "CURRENT_REVISION_REJECTED")
        self.assertIsNone(rejected2["analysis"])
        self.assertEqual(len(self.analysis.analysis_history(flow.run_evidence.stock_id)), 2)

    def test_branch_returns_no_current_revision_without_timestamp_tiebreak(self):
        self.reject_original()
        self.correction("BRANCH-A")
        self.correction("BRANCH-B")
        result = self.analysis.current_effective_revision(self.flow.analysis_report_id)
        self.assertEqual(result["status"], "REVISION_CHAIN_AMBIGUOUS")
        self.assertEqual(result["BRANCH_COUNT"], 2)
        self.assertIsNone(result["CURRENT_EFFECTIVE_REVISION"])
        self.assertIsNone(result["analysis"])
        self.assertEqual(len(self.analysis.analysis_history(self.flow.run_evidence.stock_id)), 3)

    def test_partial_evidence_always_exposes_unavailable_unverified_metadata(self):
        result = self.evidence.for_analysis(self.flow.analysis_report_id)
        for value in [result] + result["evidence"]:
            for key, expected in EVIDENCE_LIMITATIONS.items():
                self.assertEqual(value[key], expected)
            self.assertEqual(value["evidence_completeness"], "PARTIAL_DB_PROJECTION")
        for value in result["evidence"]:
            self.assertIsNone(value["validation_result"])
            self.assertEqual(value["current_confirm_state"], "UNKNOWN")
            self.assertTrue(all(h is None for h in value["hashes"].values()))
        self.assertIsNone(self.evidence.evidence("missing"))
        self.assertIsNone(self.evidence.for_analysis("missing"))

    def test_evidence_branch_projection_fails_closed(self):
        from dataclasses import replace
        original = source_records(self.flow.run_evidence)[0]
        for ordinal in (1, 2):
            record = replace(original, evidence_id=f"EV-SUCCESSOR-{ordinal}", supersedes_evidence_id=original.evidence_id)
            self.repo._operation([("evidence_records", evidence_row(record, STAMP))])
        result = self.evidence.current_effective_evidence(original.evidence_id)
        self.assertEqual(result["status"], "REVISION_CHAIN_AMBIGUOUS")
        self.assertIsNone(result["evidence"])
        self.assertEqual(result["integrity_status"], "NOT_VERIFIED")

    def test_legacy_history_is_separate_and_ids_preserved(self):
        before = self.db.total_changes
        legacy = self.analysis.legacy_history()
        self.assertEqual(legacy[0]["record_origin"], "LEGACY_HISTORY")
        self.assertEqual(legacy[0]["evidence_completeness"], "LEGACY_UNKNOWN")
        self.assertEqual(legacy[0]["legacy_record"]["id"], 1)
        self.assertNotIn("generated_confirm_state", legacy[0])
        self.assertEqual(len(self.analysis.analysis_history(self.flow.run_evidence.stock_id)), 1)
        self.assertEqual(self.db.total_changes, before)

    def test_event_sequence_previous_state_and_binding_corruption_rejected(self):
        original = self.confirmation.confirmation_state(self.flow.analysis_report_id, self.flow.revision_id)["events"]
        for key, invalid in (("event_sequence", 99), ("previous_state", "CONFIRMED"),
                             ("analysis_run_id", "other"), ("human_decision", "REJECT")):
            broken = deepcopy(original)
            broken[1][key] = invalid
            with self.subTest(key=key), self.assertRaises(PersistenceError):
                replay_events(broken)

    def test_read_models_work_on_query_only_connection(self):
        self.db.execute("PRAGMA query_only=ON")
        self.assertIsNotNone(self.analysis.latest_confirmed_analysis(self.flow.run_evidence.stock_id))
        self.assertIsNotNone(self.evidence.for_analysis(self.flow.analysis_report_id))
        self.assertEqual(self.confirmation.confirmation_state(self.flow.analysis_report_id, self.flow.revision_id)["current_confirm_state"], "CONFIRMED")


class RevisionGraphTests(unittest.TestCase):
    def report(self, identity, predecessor=None):
        return dict(id=identity, task_id="run", stock_id="stock", final_evidence_id="FE-" + identity,
                    supersedes_analysis_report_id=predecessor,
                    supersedes_final_evidence_id=None if predecessor is None else "FE-" + predecessor)

    def test_cycle_missing_stock_run_and_final_evidence_predecessor_mismatch(self):
        scenarios = [([self.report("a", "b"), self.report("b", "a")], "CYCLE"),
                     ([self.report("a", "missing")], "MISSING"),
                     ([self.report("a"), {**self.report("b", "a"), "supersedes_final_evidence_id": "wrong"}], "BINDING_MISMATCH"),
                     ([{**self.report("a"), "stock_id": "other"}, self.report("b", "a")], "STOCK_OR_RUN_MISMATCH"),
                     ([self.report("a"), {**self.report("b", "a"), "task_id": "other"}], "STOCK_OR_RUN_MISMATCH")]
        for records, status in scenarios:
            with self.subTest(status=status):
                result = revision_chain(records, "stock")
                self.assertIn(status, result["status"])
                self.assertIsNone(result["CURRENT_EFFECTIVE_REVISION"])

    def test_no_analysis_and_multiple_roots_are_conservative(self):
        self.assertEqual(revision_chain([], "stock")["status"], "NO_ANALYSIS")
        result = revision_chain([self.report("a"), self.report("b")], "stock")
        self.assertEqual(result["status"], "REVISION_CHAIN_AMBIGUOUS")
        self.assertIsNone(result["CURRENT_EFFECTIVE_REVISION"])
