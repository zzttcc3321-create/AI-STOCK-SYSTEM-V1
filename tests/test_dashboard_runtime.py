from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from dashboard.runtime import DashboardRuntime, RuntimeErrorV1
from evidence import ArchiveWriter, HumanConfirm
from persistence.connection import configure
from persistence.migration import apply_migration
from persistence.read_models import EvidenceReadRepository, ConfirmationReadRepository
from test_analysis_runner import FakeModelExecutor
from test_indicator_calculator import daily_evidence
from windows_compat import use_simulated_directory_fsync


def runtime_fixture(root):
    db = configure(sqlite3.connect(':memory:', check_same_thread=False))
    apply_migration(db)
    writer = ArchiveWriter(Path(root) / 'archive')
    record, _ = daily_evidence(writer, db)
    runtime = DashboardRuntime(db, archive_writer=writer, executor=FakeModelExecutor())
    return db, runtime, record


def confirm_items(runtime):
    for item in runtime.flow.items:
        if item.target != 'FINAL_REPORT' and runtime.flow.current(*item.key) is HumanConfirm.PENDING:
            runtime.decide(*item.key, actor_id='test-reviewer')


class DashboardRuntimeTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        use_simulated_directory_fsync(self)
        self.db, self.runtime, self.record = runtime_fixture(temporary.name)
        self.addCleanup(self.db.close)

    def test_db_evidence_indicators_prompt_status_and_no_implicit_model(self):
        runtime = self.runtime
        runtime.prepare(self.record.evidence_id)
        self.assertEqual(runtime.history, ['COLLECTING', 'NORMALIZING', 'READY_FOR_REVIEW'])
        self.assertEqual(runtime.indicators['ma60'], 129.5)
        self.assertEqual(runtime.data_status, 'PARTIAL')
        self.assertEqual(runtime.prompt.evidence_ids, (self.record.evidence_id,))
        self.assertEqual(runtime.executor.calls, [])
        self.assertIsNone(runtime.flow)

    def test_human_review_final_archive_and_full_readback_trace(self):
        runtime = self.runtime
        runtime.prepare(self.record.evidence_id)
        runtime.generate(actor_id='test-reviewer')
        with self.assertRaises(ValueError):
            runtime.prepare_archive()
        confirm_items(runtime)
        runtime.prepare_archive()
        self.assertEqual(self.db.execute('SELECT count(*) FROM analysis_reports').fetchone()[0], 0)
        result = runtime.confirm_and_archive(actor_id='test-reviewer')
        self.assertEqual(runtime.status, 'ARCHIVED')
        self.assertIs(runtime.confirm_and_archive(actor_id='test-reviewer'), result)
        projection = EvidenceReadRepository(self.db, archive_reader=runtime.writer.reader).for_analysis(result.snapshot.analysis_report_id)
        self.assertTrue(projection['REPORT_FULL_TRACE'])
        confirmation = ConfirmationReadRepository(self.db).current_confirmation_state(result.snapshot.analysis_report_id)
        self.assertEqual(confirmation['current_confirm_state'], 'CONFIRMED')
        self.assertEqual(self.db.execute('SELECT status FROM analysis_tasks').fetchone()[0], 'ARCHIVED')
        self.assertEqual(runtime.data_status, 'PARTIAL')

    def test_no_model_no_report_or_archive(self):
        self.runtime.executor = None
        self.runtime.prepare(self.record.evidence_id)
        with self.assertRaises(RuntimeErrorV1):
            self.runtime.generate(actor_id='test-reviewer')
        with self.assertRaises(RuntimeErrorV1):
            self.runtime.confirm_and_archive(actor_id='test-reviewer')
        self.assertEqual(self.db.execute('SELECT count(*) FROM analysis_reports').fetchone()[0], 0)

    def test_failed_prepare_clears_old_prompt_and_does_not_claim_ready(self):
        self.runtime.prepare(self.record.evidence_id)
        with self.assertRaises(ValueError):
            self.runtime.prepare('absent')
        self.assertIsNone(self.runtime.prompt)
        self.assertEqual(self.runtime.status, 'NORMALIZING')
        self.assertEqual(self.runtime.data_status, 'PARTIAL')

    def test_failed_progress_does_not_advance_in_memory_confirmation(self):
        runtime = self.runtime
        runtime.prepare(self.record.evidence_id)
        runtime.generate(actor_id='test-reviewer')
        item = next(i for i in runtime.flow.items if i.target != 'FINAL_REPORT')
        with patch.object(runtime.repo, 'persist_confirmation_progress', side_effect=ValueError('injected')):
            with self.assertRaises(ValueError):
                runtime.decide(*item.key, actor_id='test-reviewer')
        self.assertIs(runtime.flow.current(*item.key), HumanConfirm.PENDING)

    def test_archive_failure_retry_reuses_final_ids(self):
        runtime = self.runtime
        runtime.prepare(self.record.evidence_id)
        runtime.generate(actor_id='test-reviewer')
        confirm_items(runtime)
        runtime.prepare_archive()
        with patch.object(runtime.repo, 'persist_finalization', side_effect=ValueError('injected')):
            with self.assertRaises(ValueError):
                runtime.confirm_and_archive(actor_id='test-reviewer')
        pending_id = runtime._pending_final[1].evidence.final_evidence_id
        self.assertEqual(runtime.status, 'READY_FOR_REVIEW')
        result = runtime.confirm_and_archive(actor_id='test-reviewer')
        self.assertEqual(result.evidence.final_evidence_id, pending_id)
        self.assertEqual(runtime.status, 'ARCHIVED')
