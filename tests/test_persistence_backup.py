from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from persistence.backup import file_sha256, rehearse_backup, wal_safe_backup
from persistence.connection import configure, open_database
from persistence.contracts import PersistenceError
from persistence.migration import checks, legacy_digest, schema_fingerprint, schema_state
from test_persistence_migration import legacy_fixture


class BackupTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory(prefix="persistence012-")
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.source = configure(sqlite3.connect(self.root / "source.sqlite", isolation_level=None))
        self.addCleanup(self.source.close)
        self.source.execute("PRAGMA journal_mode=WAL")
        self.source.execute("PRAGMA wal_autocheckpoint=0")
        legacy_fixture(self.source)

    def test_wal_committed_rows_backup_hash_restore_and_rehearsal(self):
        self.source.execute("INSERT INTO legacy_0 VALUES(2,'committed-in-WAL')")
        self.assertGreater((self.root / "source.sqlite-wal").stat().st_size, 0)
        before = legacy_digest(self.source)
        backup = self.root / "backup.sqlite"
        metadata = wal_safe_backup(self.source, backup)
        self.assertEqual(metadata["backup_sha256"], file_sha256(backup))
        self.assertEqual(metadata["source_schema_fingerprint"], metadata["backup_schema_fingerprint"])
        self.assertGreater(metadata["backup_size"], 0)
        restored = open_database(backup, readonly=True)
        try:
            self.assertEqual(restored.execute("SELECT value FROM legacy_0 WHERE id=2").fetchone()[0], "committed-in-WAL")
            self.assertEqual(legacy_digest(restored), before)
            checks(restored)
        finally:
            restored.close()
        result = rehearse_backup(backup, self.root / "rehearsal.sqlite", expected_sha256=metadata["backup_sha256"])
        self.assertEqual(result["migration"], "APPLIED")
        self.assertEqual(result["legacy_schema"], "UNCHANGED")
        self.assertEqual(schema_state(self.source), "A")
        self.assertEqual(legacy_digest(self.source), before)
        self.assertEqual(file_sha256(backup), metadata["backup_sha256"])
        rehearsal = open_database(self.root / "rehearsal.sqlite", readonly=True)
        try:
            self.assertEqual(schema_state(rehearsal), "B")
        finally:
            rehearsal.close()
        # Entire repository/read-model chain on the temporary migrated copy.
        from persistence.repository import SQLitePersistenceRepository
        from persistence.read_models import AnalysisReadRepository, EvidenceReadRepository, ConfirmationReadRepository
        from test_persistence_repository import persist_run, persist_final
        from test_persistence_schema_v1_1 import fixture
        rehearsal = open_database(self.root / "rehearsal.sqlite")
        try:
            execution, flow, prepared, final, fixture_rows = fixture()
            repo = SQLitePersistenceRepository(rehearsal, allow_synthetic_evidence=True)
            persist_run(repo, execution, flow, fixture_rows)
            persist_final(repo, flow, prepared, final)
            self.assertIsNotNone(AnalysisReadRepository(rehearsal).latest_confirmed_analysis(flow.run_evidence.stock_id))
            self.assertEqual(EvidenceReadRepository(rehearsal).for_analysis(flow.analysis_report_id)["integrity_status"], "NOT_VERIFIED")
            self.assertEqual(ConfirmationReadRepository(rehearsal).confirmation_state(flow.analysis_report_id, flow.revision_id)["current_confirm_state"], "CONFIRMED")
            self.assertEqual(legacy_digest(rehearsal), before)
        finally:
            rehearsal.close()
        self.assertEqual(legacy_digest(self.source), before)
        self.assertEqual(file_sha256(backup), metadata["backup_sha256"])

    def test_backup_overwrite_hash_and_schema_mismatch_stop(self):
        backup = self.root / "backup.sqlite"
        metadata = wal_safe_backup(self.source, backup)
        with self.assertRaisesRegex(PersistenceError, "OVERWRITE"):
            wal_safe_backup(self.source, backup)
        with self.assertRaisesRegex(PersistenceError, "HASH_MISMATCH"):
            rehearse_backup(backup, self.root / "copy.sqlite", expected_sha256="0" * 64)
        self.assertFalse((self.root / "copy.sqlite").exists())
        original = schema_fingerprint
        with patch("persistence.backup.schema_fingerprint", side_effect=[original(self.source), "mismatch"]):
            with self.assertRaisesRegex(PersistenceError, "SCHEMA_MISMATCH"):
                wal_safe_backup(self.source, self.root / "bad.sqlite")
        self.assertEqual(file_sha256(backup), metadata["backup_sha256"])

    def test_online_backup_timeout_has_no_fallback(self):
        with patch("persistence.backup.time.monotonic", side_effect=[0, 100]):
            with self.assertRaisesRegex(PersistenceError, "TIMEOUT"):
                wal_safe_backup(self.source, self.root / "timeout.sqlite", timeout_seconds=1)

    def test_active_source_transaction_rejected(self):
        self.source.execute("BEGIN")
        try:
            with self.assertRaisesRegex(PersistenceError, "TRANSACTION"):
                wal_safe_backup(self.source, self.root / "no.sqlite")
        finally:
            self.source.rollback()
