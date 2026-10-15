"""B04: canonical Evidence and DB projections must bind the same normalized data."""
from dataclasses import replace
from pathlib import Path
import sqlite3
import tempfile
import unittest

from analysis.indicator_calculator import IndicatorCalculator, IndicatorError
from evidence import ArchiveWriter
from persistence.connection import configure
from persistence.migration import apply_migration
from persistence.read_models import EvidenceReadRepository
from persistence.repository import SQLitePersistenceRepository, evidence_row
from test_indicator_calculator import daily_evidence
from windows_compat import use_simulated_directory_fsync


class ReadModelNormalizedHashTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        use_simulated_directory_fsync(self)
        self.writer = ArchiveWriter(Path(temporary.name) / 'archive')
        self.db = self.memory_database()

    def memory_database(self):
        db = configure(sqlite3.connect(':memory:'))
        self.addCleanup(db.close)
        apply_migration(db)
        return db

    def project_hash(self, record, normalized_hash):
        # Model an incorrect import into a separate synthetic database without
        # modifying immutable records or disabling their production triggers.
        db = self.memory_database()
        projection = replace(record, normalized_data_hash=normalized_hash)
        SQLitePersistenceRepository(db, allow_synthetic_evidence=True)._operation([
            ('evidence_records', evidence_row(projection, record.request_timestamp))])
        return db

    def test_legal_sidecar_for_same_raw_uri_cannot_replace_canonical_hash(self):
        record, _ = daily_evidence(self.writer, self.db)
        other, normalized = daily_evidence(self.writer, self.db, direction=-1, identifier='EV-OTHER')
        original = IndicatorCalculator(self.db, archive_reader=self.writer.reader).calculate(record.evidence_id)
        alternative = IndicatorCalculator(self.db, archive_reader=self.writer.reader).calculate(other.evidence_id)
        self.assertEqual((original['ma5'], original['trend']), (157.0, 'UP'))
        self.assertEqual((alternative['ma5'], alternative['trend']), (43.0, 'DOWN'))
        self.assertEqual(record.normalization_rule, other.normalization_rule)
        self.assertEqual(record.validation_result, other.validation_result)
        sidecar = self.writer.append_normalized(record.raw_response_path,
            normalized_data=normalized.canonical_data, normalization_rule=record.normalization_rule,
            validation_result=record.validation_result)
        replacement_hash = sidecar['normalized_data_hash']
        self.assertNotEqual(replacement_hash, record.normalized_data_hash)
        self.assertEqual(self.writer.reader.normalized(record.raw_response_path, replacement_hash), sidecar)
        self.writer.reader.verify_record(record)

        corrupted_db = self.project_hash(record, replacement_hash)
        projected = EvidenceReadRepository(corrupted_db, archive_reader=self.writer.reader).evidence(record.evidence_id)
        self.assertEqual(projected['integrity_status'], 'CORRUPT')
        self.assertNotEqual(projected['evidence_completeness'], 'VERIFIED_ARCHIVE')
        with self.assertRaisesRegex(IndicatorError, 'EVIDENCE_NOT_VERIFIED'):
            IndicatorCalculator(corrupted_db, archive_reader=self.writer.reader).calculate(record.evidence_id)

    def test_missing_projection_hash_cannot_verify_canonical_evidence(self):
        record, _ = daily_evidence(self.writer, self.db)
        corrupted_db = self.project_hash(record, None)
        projected = EvidenceReadRepository(corrupted_db, archive_reader=self.writer.reader).evidence(record.evidence_id)
        self.assertEqual(projected['integrity_status'], 'CORRUPT')
        self.assertNotEqual(projected['evidence_completeness'], 'VERIFIED_ARCHIVE')
