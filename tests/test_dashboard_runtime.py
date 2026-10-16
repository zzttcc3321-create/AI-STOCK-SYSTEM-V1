from concurrent.futures import ThreadPoolExecutor
import os
from pathlib import Path
import sqlite3
import tempfile
from threading import Event
import unittest
from unittest.mock import patch

from dashboard.factory import create_runtime
from dashboard.runtime import RuntimeErrorV1
from evidence import ArchiveWriter, HumanConfirm
from persistence.connection import configure
from persistence.contracts import PersistenceError
from persistence.migration import apply_migration
from persistence.read_models import EvidenceReadRepository, ConfirmationReadRepository
from test_indicator_calculator import daily_evidence
from windows_compat import use_simulated_directory_fsync


def runtime_inputs(root):
    Path(root).mkdir(parents=True, exist_ok=True)
    db_path = Path(root) / 'runtime.sqlite'
    writer = ArchiveWriter(Path(root) / 'archive')
    db = configure(sqlite3.connect(db_path, isolation_level=None))
    try:
        apply_migration(db)
        record, _ = daily_evidence(writer, db)
    finally:
        db.close()
    return {'V1_RUNTIME_FACTORY': 'dashboard.factory:create_runtime',
            'V1_RUNTIME_DB_SELECTOR': str(db_path),
            'V1_RUNTIME_ARCHIVE_ROOT': str(Path(root) / 'archive'),
            'V1_RUNTIME_EXECUTOR_FACTORY': 'test_analysis_runner:FakeModelExecutor'}, record


def runtime_fixture(root):
    environment, record = runtime_inputs(root)
    with patch.dict(os.environ, environment):
        runtime = create_runtime()
    return runtime.db, runtime, record


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

    def test_factory_connection_runs_complete_review_on_another_thread(self):
        runtime = self.runtime
        with ThreadPoolExecutor(max_workers=1) as worker:
            worker.submit(runtime.prepare, self.record.evidence_id).result(timeout=10)
            worker.submit(runtime.generate, actor_id='test-reviewer').result(timeout=10)
            worker.submit(confirm_items, runtime).result(timeout=10)
            worker.submit(runtime.prepare_archive).result(timeout=10)
            worker.submit(runtime.confirm_and_archive, actor_id='test-reviewer').result(timeout=10)
        self.assertEqual(runtime.status, 'ARCHIVED')
        self.assertEqual(self.db.execute('SELECT count(*) FROM analysis_reports').fetchone()[0], 1)

    def test_overlapping_prepare_waits_for_full_write_transaction_and_state_update(self):
        runtime = self.runtime
        runtime.prepare(self.record.evidence_id)
        entered, release, second_started, read_started = (Event() for _ in range(4))
        original_insert = runtime.repo._insert

        def hold_insert(*args):
            self.assertTrue(self.db.in_transaction)
            entered.set()
            if not release.wait(10):
                raise AssertionError('write operation did not resume')
            return original_insert(*args)

        def prepare_again():
            second_started.set()
            runtime.prepare(self.record.evidence_id)

        with patch.object(runtime.repo, '_insert', side_effect=hold_insert), \
             patch('dashboard.runtime.IndicatorCalculator.calculate', side_effect=lambda *args: read_started.set()), \
             ThreadPoolExecutor(max_workers=2) as workers:
            generation = workers.submit(runtime.generate, actor_id='test-reviewer')
            try:
                self.assertTrue(entered.wait(10))
                preparation = workers.submit(prepare_again)
                self.assertTrue(second_started.wait(10))
                self.assertFalse(read_started.wait(.1))
                self.assertFalse(preparation.done())
            finally:
                release.set()
            generation.result(timeout=10)
            with self.assertRaisesRegex(RuntimeErrorV1, 'ACTIVE_REVIEW_EXISTS'):
                preparation.result(timeout=10)
        self.assertIsNotNone(runtime.flow)
        self.assertFalse(self.db.in_transaction)
        self.assertEqual(self.db.execute('SELECT status FROM analysis_tasks').fetchone()[0], 'READY_FOR_REVIEW')

    def test_factory_cross_thread_write_gate_rejection_releases_lock_without_state_changes(self):
        runtime = self.runtime
        runtime.prepare(self.record.evidence_id)
        db_path = Path(self.db.execute('PRAGMA database_list').fetchone()[2])
        before, changes = db_path.read_bytes(), self.db.total_changes
        # Classify only this synthetic temporary fixture as protected; never open a real DB.
        with patch('persistence.connection.is_temporary_database', return_value=False), \
             patch.dict(os.environ, {'REAL_DB_PROTECTED_PATHS': str(db_path),
                                     'REAL_DB_WRITE': '', 'APPLY_REAL_DB': 'YES'}):
            with ThreadPoolExecutor(max_workers=1) as worker:
                with self.assertRaisesRegex(PersistenceError, 'REAL_DB_WRITE_NOT_AUTHORIZED'):
                    worker.submit(runtime.generate, actor_id='test-reviewer').result(timeout=10)
            self.assertEqual(db_path.read_bytes(), before)
            self.assertEqual(self.db.total_changes, changes)
            self.assertFalse(self.db.in_transaction)
            self.assertIsNone(runtime.flow)
            self.assertEqual(runtime.status, 'READY_FOR_REVIEW')
            os.environ['REAL_DB_WRITE'] = 'YES'
            runtime.generate(actor_id='test-reviewer')
        self.assertIsNotNone(runtime.flow)

    def test_prepare_never_displays_indicators_from_after_judgment_cutoff(self):
        record, _ = daily_evidence(self.runtime.writer, self.db, identifier='EV-HISTORICAL',
                                  judgment_cutoff='2026-01-31T23:59:59+08:00',
                                  acquired_at='2026-03-02T09:00:00+08:00',
                                  disclosure_time='2026-03-02T09:00:00+08:00',
                                  request_timestamp='2026-03-02T08:59:59+08:00')
        self.runtime.prepare(record.evidence_id)
        self.assertEqual(self.runtime.indicators['sample_count'], 0)
        self.assertIsNone(self.runtime.indicators['as_of'])
        for metric in ('ma5', 'ma20', 'ma60', 'volume_change'):
            self.assertIsNone(self.runtime.indicators[metric])
        self.assertEqual(self.runtime.indicators['DATA_STATUS'], 'PARTIAL')
        self.assertIn('JUDGMENT_CUTOFF_EXCLUDED_ROWS', self.runtime.indicators['limitations'])
        self.assertEqual(self.runtime.package.daily_kline_summary['records'], ())
        self.assertEqual(self.runtime.data_status, 'PARTIAL')
        self.assertEqual(self.runtime.executor.calls, [])

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
