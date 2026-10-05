from dataclasses import replace
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from evidence import ArchiveIdentity, ArchiveWriter
from evidence.archive_contracts import plain, SIDECAR_NAME
from persistence.connection import configure
from persistence.contracts import PersistenceError
from persistence.migration import apply_migration
from persistence.repository import SQLitePersistenceRepository, evidence_row
from persistence.read_models import EvidenceReadRepository, AnalysisReadRepository
from test_human_confirmation import STAMP, source_records
from test_persistence_repository import persist_run
from test_persistence_schema_v1_1 import fixture
from windows_compat import use_simulated_directory_fsync


class PersistenceArchiveIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        use_simulated_directory_fsync(self)
        self.root = Path(self.temp.name) / 'archive'
        self.writer = ArchiveWriter(self.root)
        self.db = configure(sqlite3.connect(':memory:'))
        self.addCleanup(self.db.close)
        apply_migration(self.db)
        self.execution, self.flow, self.prepared, self.final, self.rows = fixture()
        records = []
        for old in source_records(self.flow.run_evidence):
            identity = ArchiveIdentity('run', 'collection', 'request', old.evidence_id)
            receipt = self.writer.write(identity=identity, source=old.source, raw_parts={'response': b'\x00\xffMOCK'},
                endpoint_or_tool=old.endpoint_or_tool, request_params=old.request_params, request_timestamp=old.request_timestamp)
            norm = self.writer.append_normalized(identity.uri, normalized_data={'price': 10.5},
                normalization_rule=old.normalization_rule, validation_result=old.validation_result)
            records.append(replace(receipt['record'], normalization_rule=old.normalization_rule,
                                   normalized_data_hash=norm['normalized_data_hash'], validation_result=old.validation_result))
        self.records = tuple(records)
        self.binding = self.final.evidence.to_dict()
        for record in self.records:
            if record.evidence_id in self.binding['used_evidence_ids']:
                self.writer.append_analysis_binding(record.raw_response_path, binding=self.binding)
        self.repo = SQLitePersistenceRepository(self.db, archive_reader=self.writer.reader, enable_real_provider_evidence=True)
        persist_run(self.repo, self.execution, self.flow, self.rows, records=self.records)

    def persist_final(self, repo=None):
        return (repo or self.repo).persist_finalization(self.flow, self.prepared, self.final,
                                        evidence_records=self.records, evidence_created_at=STAMP)

    def test_default_disabled_even_with_reader_and_archives(self):
        for repo in (SQLitePersistenceRepository(self.db),
                     SQLitePersistenceRepository(self.db, archive_reader=self.writer.reader)):
            with self.assertRaisesRegex(PersistenceError, 'RUNTIME_WRITE_DISABLED'):
                self.persist_final(repo)
        self.assertEqual(self.db.execute('SELECT count(*) FROM analysis_reports').fetchone()[0], 0)

    def test_runtime_enable_without_reader_is_disabled(self):
        with self.assertRaisesRegex(PersistenceError, 'RUNTIME_WRITE_DISABLED'):
            self.persist_final(SQLitePersistenceRepository(self.db, enable_real_provider_evidence=True))

    def test_explicit_enable_verified_archives_and_bindings_allow_temp_db(self):
        self.assertEqual(self.persist_final(), 'PERSISTED')
        self.assertEqual(self.persist_final(), 'IDEMPOTENT_NOOP')
        for record in self.records:
            row = self.db.execute('SELECT * FROM evidence_records WHERE id=?', (record.evidence_id,)).fetchone()
            self.assertEqual(row['raw_data_path'], record.raw_response_path)
            self.assertTrue(row['raw_data_path'].startswith('archive://v1/'))
            self.assertNotIn(str(self.root), row['raw_data_path'])

    def test_missing_raw_or_sidecar_blocks_database_write(self):
        path = self.writer.resolver.resolve(self.records[0].raw_response_path) / 'response.raw.gz'
        path.unlink()
        with self.assertRaisesRegex(PersistenceError, 'ARCHIVE_NOT_VERIFIED'):
            self.persist_final()
        self.assertEqual(self.db.execute('SELECT count(*) FROM analysis_reports').fetchone()[0], 0)

    def test_wrong_record_id_or_run_blocks_write(self):
        for key in ('evidence_id', 'system_run_id', 'collection_task_id'):
            original = self.records
            self.records = (replace(original[0], **{key: 'wrong'}),) + original[1:]
            with self.subTest(key=key), self.assertRaises(PersistenceError):
                self.persist_final()
            self.records = original

    def test_wrong_normalization_or_validation_blocks_write(self):
        original = self.records
        self.records = (replace(original[0], normalization_rule=('wrong',)),) + original[1:]
        with self.assertRaisesRegex(PersistenceError, 'ARCHIVE_NOT_VERIFIED'):
            self.persist_final()

    def test_synthetic_flag_cannot_bypass_archive_verification(self):
        repo = SQLitePersistenceRepository(self.db, allow_synthetic_evidence=True)
        with self.assertRaisesRegex(PersistenceError, 'RUNTIME_WRITE_DISABLED'):
            self.persist_final(repo)

    def test_missing_analysis_binding_blocks_write(self):
        for record in self.records:
            if record.evidence_id in self.binding['used_evidence_ids']:
                directory = self.writer.resolver.resolve(record.raw_response_path)
                for path in directory.glob('binding-*.json'): path.unlink()
                break
        with self.assertRaisesRegex(PersistenceError, 'ARCHIVE_NOT_VERIFIED'):
            self.persist_final()

    def test_read_models_three_dynamic_states_and_full_trace(self):
        self.persist_final()
        self.assertEqual(EvidenceReadRepository(self.db).for_analysis(self.flow.analysis_report_id)['integrity_status'], 'NOT_VERIFIED')
        repo = EvidenceReadRepository(self.db, archive_reader=self.writer.reader)
        verified = repo.for_analysis(self.flow.analysis_report_id)
        self.assertTrue(verified['REPORT_FULL_TRACE'])
        self.assertEqual(verified['integrity_status'], 'VERIFIED')
        for item in verified['evidence']:
            self.assertEqual(item['metadata_availability'], 'AVAILABLE')
            self.assertEqual(item['validation_result']['status'], 'PASS')
            self.assertIsNotNone(item['hashes']['raw_response_hash'])
            self.assertIsNotNone(item['endpoint_or_tool'])
        report = AnalysisReadRepository(self.db, archive_reader=self.writer.reader).latest_confirmed_analysis(self.flow.run_evidence.stock_id)
        self.assertTrue(report['analysis']['REPORT_FULL_TRACE'])
        path = self.writer.resolver.resolve(self.records[0].raw_response_path) / SIDECAR_NAME
        path.write_bytes(path.read_bytes() + b' ')
        corrupt = repo.for_analysis(self.flow.analysis_report_id)
        self.assertFalse(corrupt['REPORT_FULL_TRACE'])
        self.assertEqual(corrupt['integrity_status'], 'CORRUPT')
        self.assertEqual(corrupt['metadata_availability'], 'AVAILABLE')

    def test_db_report_tamper_cannot_claim_full_trace(self):
        self.persist_final()
        reports = AnalysisReadRepository(self.db)._reports()
        reports[0]['model_version'] = 'wrong'
        with patch.object(AnalysisReadRepository, '_reports', return_value=reports):
            result = EvidenceReadRepository(self.db, archive_reader=self.writer.reader).for_analysis(self.flow.analysis_report_id)
        self.assertFalse(result['REPORT_FULL_TRACE'])
        self.assertEqual(result['integrity_status'], 'CORRUPT')

    def test_calculation_preserves_normalization_and_validation_without_schema_change(self):
        from persistence.serialization import loads
        before = list(self.db.execute("SELECT name,sql FROM sqlite_master ORDER BY name"))
        for record in self.records:
            calc = loads(evidence_row(record, STAMP)['calculation'])
            self.assertEqual(calc['normalization_rule'], list(record.normalization_rule))
            self.assertEqual(calc['validation_result'], plain(record.validation_result))
        self.persist_final()
        self.assertEqual(before, list(self.db.execute("SELECT name,sql FROM sqlite_master ORDER BY name")))

    def test_reader_remains_read_only_and_no_legacy_interpretation(self):
        self.persist_final()
        self.db.execute('PRAGMA query_only=ON')
        before = self.db.total_changes
        self.assertTrue(EvidenceReadRepository(self.db, archive_reader=self.writer.reader).for_analysis(self.flow.analysis_report_id)['REPORT_FULL_TRACE'])
        self.assertEqual(self.db.total_changes, before)
        with self.assertRaises(ValueError):
            self.writer.reader.read('tdx-local/run/collection/request/raw.json')

    def test_archive_corruption_during_inserts_rolls_back(self):
        original = self.repo._insert
        corrupted = [False]
        def inject(table, row):
            result = original(table, row)
            if table == 'analysis_reports' and not corrupted[0]:
                path = self.writer.resolver.resolve(self.records[0].raw_response_path) / 'response.raw.gz'
                path.write_bytes(b'corrupt')
                corrupted[0] = True
            return result
        with patch.object(self.repo, '_insert', side_effect=inject):
            with self.assertRaisesRegex(PersistenceError, 'ARCHIVE_NOT_VERIFIED'):
                self.persist_final()
        self.assertFalse(self.db.in_transaction)
        self.assertEqual(self.db.execute('SELECT count(*) FROM analysis_reports').fetchone()[0], 0)

    def test_analysis_binding_corruption_read_model_fails_closed(self):
        self.persist_final()
        directory = self.writer.resolver.resolve(self.records[0].raw_response_path)
        for path in directory.glob('binding-*.json'):
            path.write_bytes(path.read_bytes() + b' ')
        result = EvidenceReadRepository(self.db, archive_reader=self.writer.reader).for_analysis(self.flow.analysis_report_id)
        self.assertFalse(result['REPORT_FULL_TRACE'])
        self.assertEqual(result['integrity_status'], 'CORRUPT')

    def test_raw_model_output_hash_recalculated_before_full_trace(self):
        self.persist_final()
        from persistence.read_models import rows as real_rows
        def corrupt(db, sql, params=()):
            values = real_rows(db, sql, params)
            if sql.startswith('SELECT * FROM analysis_tasks WHERE id='):
                values[0]['raw_model_output'] += ' '
            return values
        with patch('persistence.read_models.rows', side_effect=corrupt):
            result = EvidenceReadRepository(self.db, archive_reader=self.writer.reader).for_analysis(self.flow.analysis_report_id)
        self.assertFalse(result['REPORT_FULL_TRACE'])
        self.assertEqual(result['integrity_status'], 'CORRUPT')

    def test_old_reference_reader_config_does_not_resolve_or_backfill(self):
        synthetic = source_records(self.flow.run_evidence)[0]
        row = evidence_row(synthetic, STAMP)
        row['id'] = 'OLD-SYNTHETIC'
        self.repo._operation([('evidence_records', row)])
        before = self.db.total_changes
        with patch.object(self.writer.reader, 'read', side_effect=AssertionError('legacy must not be resolved')):
            projected = EvidenceReadRepository(self.db, archive_reader=self.writer.reader).evidence('OLD-SYNTHETIC')
        self.assertEqual(projected['integrity_status'], 'NOT_VERIFIED')
        self.assertEqual(projected['metadata_availability'], 'UNAVAILABLE')
        self.assertEqual(self.db.total_changes, before)

    def test_absolute_raw_paths_rejected_even_for_synthetic_projection(self):
        record = source_records(self.flow.run_evidence)[0]
        for path in ('/tmp/synthetic-raw.json', 'C:\\synthetic\\raw.json', '\\\\synthetic\\share\\raw.json'):
            with self.subTest(path=path), self.assertRaisesRegex(PersistenceError, 'ABSOLUTE_RAW_PATH_FORBIDDEN'):
                evidence_row(replace(record, raw_response_path=path), STAMP)

    def test_field_trace_and_effective_evidence_use_dynamic_verification(self):
        self.persist_final()
        repo = EvidenceReadRepository(self.db, archive_reader=self.writer.reader)
        field = repo.field_evidence_trace(self.flow.analysis_report_id, 'market_view')
        self.assertTrue(field['REPORT_FULL_TRACE'])
        self.assertEqual(field['integrity_status'], 'VERIFIED')
        current = repo.current_effective_evidence(self.records[0].evidence_id)
        self.assertEqual(current['integrity_status'], 'VERIFIED')
