"""B01 regression coverage using only synthetic databases in temporary directories."""
import os
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from dashboard.runtime import DashboardRuntime
from evidence import ArchiveWriter, HumanConfirm
from persistence.connection import configure, open_database, transaction
from persistence.contracts import PersistenceError
from persistence.migration import apply_migration
from persistence.repository import SQLitePersistenceRepository
from test_analysis_runner import FakeModelExecutor
from test_dashboard_runtime import confirm_items
from test_indicator_calculator import daily_evidence
from windows_compat import symlink_privilege_missing, use_simulated_directory_fsync


class RealDatabaseWriteGateTests(unittest.TestCase):
    def setUp(self):
        environment = patch.dict(os.environ)
        environment.start()
        self.addCleanup(environment.stop)
        for name in ('REAL_DB_PROTECTED_PATHS', 'REAL_DB_WRITE', 'APPLY_REAL_DB'):
            os.environ.pop(name, None)

        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.db_path = self.root / 'runtime.sqlite'
        use_simulated_directory_fsync(self)
        seed = configure(sqlite3.connect(self.db_path, isolation_level=None))
        try:
            apply_migration(seed)
        finally:
            seed.close()
        self.db = open_database(self.db_path)
        self.addCleanup(self.db.close)
        self.writer = ArchiveWriter(self.root / 'archive')
        self.record, _ = daily_evidence(self.writer, self.db)
        self.runtime = DashboardRuntime(self.db, archive_writer=self.writer,
                                        executor=FakeModelExecutor())
        self.runtime.prepare(self.record.evidence_id)

    def protect(self):
        os.environ['REAL_DB_PROTECTED_PATHS'] = str(self.db_path)

    def assert_denied_without_database_changes(self, operation, error_code='REAL_DB_WRITE_NOT_AUTHORIZED'):
        contents = tuple(self.db.iterdump())
        file_bytes = self.db_path.read_bytes()
        changes = self.db.total_changes
        with self.assertRaisesRegex(PersistenceError, '^' + error_code):
            operation()
        self.assertEqual(tuple(self.db.iterdump()), contents)
        self.assertEqual(self.db_path.read_bytes(), file_bytes)
        self.assertEqual(self.db.total_changes, changes)
        self.assertFalse(self.db.in_transaction)

    def generate(self):
        self.runtime.generate(actor_id='test-reviewer')

    def first_item(self):
        return next(item for item in self.runtime.flow.items if item.target != 'FINAL_REPORT')

    def assert_complete_archive(self):
        self.generate()
        confirm_items(self.runtime)
        self.runtime.prepare_archive()
        self.runtime.confirm_and_archive(actor_id='test-reviewer')
        self.assertEqual(self.runtime.status, 'ARCHIVED')
        self.assertEqual(self.db.execute('SELECT status FROM analysis_tasks').fetchone()[0], 'ARCHIVED')
        self.assertEqual(self.db.execute('SELECT count(*) FROM analysis_reports').fetchone()[0], 1)

    def test_protected_database_rejects_generation_without_authorization(self):
        self.protect()
        self.assert_denied_without_database_changes(self.generate)
        self.assertIsNone(self.runtime.flow)

    def test_protected_database_rejects_confirmation_without_authorization(self):
        self.generate()
        item = self.first_item()
        self.protect()
        self.assert_denied_without_database_changes(
            lambda: self.runtime.decide(*item.key, actor_id='test-reviewer'))
        self.assertIs(self.runtime.flow.current(*item.key), HumanConfirm.PENDING)

    def test_protected_database_rejects_archive_preparation_without_authorization(self):
        self.generate()
        confirm_items(self.runtime)
        self.protect()
        self.assert_denied_without_database_changes(self.runtime.prepare_archive)
        self.assertIsNone(self.runtime.prepared)

    def test_protected_database_rejects_final_archive_without_authorization(self):
        self.generate()
        confirm_items(self.runtime)
        self.runtime.prepare_archive()
        self.protect()
        self.assert_denied_without_database_changes(
            lambda: self.runtime.confirm_and_archive(actor_id='test-reviewer'))
        self.assertIsNone(self.runtime.final)
        self.assertEqual(self.runtime.status, 'READY_FOR_REVIEW')

    def test_protected_database_rejects_task_status_updates_without_authorization(self):
        self.generate()
        self.protect()
        self.assert_denied_without_database_changes(lambda: self.runtime.repo.update_task_status(
            self.runtime.flow.run_evidence.analysis_run_id, 'FAILED'))

    def test_real_db_write_yes_allows_generation_confirmation_and_archive(self):
        self.protect()
        os.environ['REAL_DB_WRITE'] = 'YES'
        self.assert_complete_archive()

    def test_apply_real_db_yes_allows_generation_confirmation_and_archive(self):
        self.protect()
        os.environ['APPLY_REAL_DB'] = 'YES'
        self.assert_complete_archive()

    def test_unprotected_database_allows_generation_confirmation_and_archive(self):
        os.environ['REAL_DB_PROTECTED_PATHS'] = str(self.db_path.with_suffix('.sqlite.other'))
        self.assert_complete_archive()

    def test_protected_readonly_open_and_dashboard_reads_are_unaffected(self):
        self.protect()
        contents = tuple(self.db.iterdump())
        file_bytes = self.db_path.read_bytes()
        readonly = open_database(self.db_path, readonly=True)
        self.addCleanup(readonly.close)
        runtime = DashboardRuntime(readonly, archive_writer=self.writer)
        runtime.prepare(self.record.evidence_id)
        self.assertEqual(runtime.status, 'READY_FOR_REVIEW')
        self.assertEqual(runtime.indicators['ma60'], 129.5)
        self.assertEqual(readonly.execute('SELECT count(*) FROM evidence_records').fetchone()[0], 1)
        with self.assertRaisesRegex(sqlite3.OperationalError, 'readonly'):
            readonly.execute('CREATE TABLE forbidden_write (id INTEGER)')
        self.assertEqual(tuple(self.db.iterdump()), contents)
        self.assertEqual(self.db_path.read_bytes(), file_bytes)

    def test_authorization_requires_exact_yes_value(self):
        self.protect()
        for name in ('REAL_DB_WRITE', 'APPLY_REAL_DB'):
            for value in ('', 'NO', 'yes', 'true', '1', 'YES ', ' YES'):
                with self.subTest(name=name, value=value), patch.dict(os.environ, {name: value}):
                    self.assert_denied_without_database_changes(self.generate)

    def test_multiple_protected_paths_normalize_relative_paths_and_whitespace(self):
        relative = os.path.relpath(self.db_path, Path.cwd())
        os.environ['REAL_DB_PROTECTED_PATHS'] = f' , {self.root / "unused.sqlite"}, {relative} , '
        self.assert_denied_without_database_changes(self.generate)

    def test_protected_symlink_path_resolves_to_database(self):
        alias = self.root / 'database-alias.sqlite'
        try:
            alias.symlink_to(self.db_path)
        except OSError as exc:
            if not symlink_privilege_missing(exc):
                raise
            self.skipTest('Windows symlink privilege unavailable')
        os.environ['REAL_DB_PROTECTED_PATHS'] = str(alias)
        self.assert_denied_without_database_changes(self.generate)

    def test_repository_cannot_bypass_gate_with_direct_sqlite_connection(self):
        self.generate()
        direct = configure(sqlite3.connect(self.db_path, isolation_level=None))
        self.addCleanup(direct.close)
        repo = SQLitePersistenceRepository(direct)
        self.protect()
        self.assert_denied_without_database_changes(lambda: repo.update_task_status(
            self.runtime.flow.run_evidence.analysis_run_id, 'FAILED'))
        self.assertEqual(direct.total_changes, 0)
        self.assertFalse(direct.in_transaction)

    def test_direct_transaction_is_denied_before_callback_or_begin(self):
        direct = sqlite3.connect(self.db_path, isolation_level=None)
        self.addCleanup(direct.close)
        statements = []
        direct.set_trace_callback(statements.append)
        called = []

        def write():
            called.append(True)
            direct.execute('CREATE TABLE forbidden_write (id INTEGER)')

        self.protect()
        self.assert_denied_without_database_changes(lambda: transaction(direct, write))
        self.assertEqual(called, [])
        self.assertFalse(any(statement.lstrip().upper().startswith('BEGIN') for statement in statements))
        self.assertEqual(direct.total_changes, 0)
        self.assertFalse(direct.in_transaction)

    def test_revoked_authorization_is_rechecked_for_next_write(self):
        self.protect()
        with patch.dict(os.environ, {'REAL_DB_WRITE': 'YES'}):
            self.generate()
        item = self.first_item()
        self.assert_denied_without_database_changes(
            lambda: self.runtime.decide(*item.key, actor_id='test-reviewer'))
        self.assertIs(self.runtime.flow.current(*item.key), HumanConfirm.PENDING)

    def test_protected_attached_database_cannot_bypass_transaction_gate(self):
        attached_path = self.root / 'attached.sqlite'
        attached = sqlite3.connect(attached_path)
        try:
            attached.execute('CREATE TABLE synthetic_marker (value INTEGER)')
            attached.commit()
        finally:
            attached.close()
        self.db.execute('ATTACH DATABASE ? AS protected', (str(attached_path),))
        os.environ['REAL_DB_PROTECTED_PATHS'] = str(attached_path)
        attached_bytes = attached_path.read_bytes()
        self.assert_denied_without_database_changes(lambda: transaction(
            self.db, lambda: self.db.execute('INSERT INTO protected.synthetic_marker VALUES (1)')))
        self.assertEqual(attached_path.read_bytes(), attached_bytes)
        self.assertEqual(self.db.execute('SELECT count(*) FROM protected.synthetic_marker').fetchone()[0], 0)

    def test_path_resolution_failure_denies_write_even_with_authorization(self):
        self.protect()
        os.environ['REAL_DB_WRITE'] = 'YES'
        called = []
        with patch('persistence.connection.Path.resolve', side_effect=OSError('synthetic resolution failure')):
            self.assert_denied_without_database_changes(
                lambda: transaction(self.db, lambda: called.append(True)),
                error_code='REAL_DB_PROTECTED_PATHS_INVALID')
        self.assertEqual(called, [])
