"""Cross-module regression for #014-R1, including actual SQLite projection."""
from dataclasses import replace
from pathlib import Path
import sqlite3
import tempfile
import unittest

from evidence import ArchiveIdentity, ArchiveWriter, EvidenceBuilder, ArchiveError
from evidence.archive_contracts import SIDECAR_NAME, plain, sealed
from normalizer import InformationNormalizer
from normalizer.information import validate_information_evidence
from persistence.connection import configure
from persistence.migration import apply_migration
from persistence.repository import SQLitePersistenceRepository, evidence_row
from persistence.read_models import EvidenceReadRepository
from test_information_normalizer import information_input, TIME
from windows_compat import use_simulated_directory_fsync


def archived_information(writer, *, tool='wenda_news_query', view=None, raw_id='abc123', canonical_id='EV-00001'):
    envelope, original, raw = information_input(tool, view=view)
    identity = ArchiveIdentity('run008', 'task008', 'request', raw_id)
    receipt = writer.write(identity=identity, source=original.source, raw_parts={'response': raw},
        endpoint_or_tool=original.endpoint_or_tool, request_params=original.request_params,
        request_timestamp=original.request_timestamp, evidence_id='RAW-' + raw_id)
    envelope = replace(envelope, raw_reference=identity.uri)
    result = InformationNormalizer().normalize(envelope, source_evidence=receipt['record'], raw_bytes=raw)
    canonical = EvidenceBuilder().build(fetch_result=envelope, endpoint_or_tool=original.endpoint_or_tool,
        request_params=original.request_params, system_run_id='run008', collection_task_id='task008',
        normalization_result=result, evidence_id=canonical_id, raw_response_hash=receipt['record'].raw_response_hash)
    validate_information_evidence(result, canonical)
    writer.append_normalized(identity.uri, normalized_data=result.canonical_data,
        normalization_rule=canonical.normalization_rule, validation_result=canonical.validation_result)
    writer.append_evidence(canonical)
    return envelope, result, canonical, raw


class InformationArchiveRoundtripTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        use_simulated_directory_fsync(self)
        self.writer = ArchiveWriter(Path(temporary.name) / 'archive')

    def test_information_normalizer_archive_roundtrip(self):
        _, result, record, raw = archived_information(self.writer)
        reader = self.writer.reader
        reader.verify_record(record)
        recovered = reader.evidence(record.raw_response_path, record.evidence_id)
        self.assertEqual(recovered['canonical_id'], 'EV-00001')
        self.assertEqual(recovered['raw_reference_id'], 'abc123')
        self.assertIsNone(recovered['supersedes_evidence_id'])
        self.assertEqual(reader.read(record.raw_response_path)['raw_parts']['response'], raw)
        self.assertEqual(reader.normalized(record.raw_response_path, record.normalized_data_hash)['normalized_data'][0]['title'],
                         result.canonical_data[0]['title'])

    def test_existing_writer_callers_also_get_separate_canonical_identity(self):
        envelope, original, raw = information_input()
        identity = ArchiveIdentity('run008', 'task008', 'request', 'legacy-raw')
        receipt = self.writer.write(identity=identity, source=original.source, raw_parts={'response': raw},
            endpoint_or_tool=original.endpoint_or_tool, request_params=original.request_params,
            request_timestamp=original.request_timestamp)
        envelope = replace(envelope, raw_reference=identity.uri)
        result = InformationNormalizer().normalize(envelope, source_evidence=receipt['record'], raw_bytes=raw)
        record = EvidenceBuilder().build(fetch_result=envelope, endpoint_or_tool=original.endpoint_or_tool,
            request_params=original.request_params, system_run_id='run008', collection_task_id='task008',
            normalization_result=result, evidence_id='EV-UPGRADED',
            supersedes_evidence_id=receipt['record'].evidence_id, raw_response_hash=receipt['record'].raw_response_hash)
        validate_information_evidence(result, record)
        self.writer.append_normalized(identity.uri, normalized_data=result.canonical_data,
            normalization_rule=record.normalization_rule, validation_result=record.validation_result)
        self.writer.append_evidence(record)
        self.writer.reader.verify_record(record)
        self.assertEqual(record.raw_reference_id, 'legacy-raw')
        self.assertIsNone(record.supersedes_evidence_id)

    def test_frozen_v1_sidecar_without_identity_extension_still_reads(self):
        envelope, original, raw = information_input()
        identity = ArchiveIdentity('run008', 'task008', 'request', 'old-raw')
        receipt = self.writer.write(identity=identity, source=original.source, raw_parts={'response': raw},
            endpoint_or_tool=original.endpoint_or_tool, request_params=original.request_params,
            request_timestamp=original.request_timestamp)
        meta = plain(receipt['metadata'])
        meta['record'].pop('canonical_id')
        meta['record'].pop('raw_reference_id')
        sidecar = self.writer.resolver.resolve(identity.uri) / SIDECAR_NAME
        sidecar.write_bytes(sealed(meta))  # Synthetic pre-V1.1 archive fixture only.
        before = sidecar.read_bytes()
        self.writer.reader.verify_record(replace(receipt['record'], raw_reference_id=None))
        self.assertEqual(sidecar.read_bytes(), before)

    def test_all_information_categories_pass_archive_and_db_guard(self):
        db = configure(sqlite3.connect(':memory:'))
        self.addCleanup(db.close)
        apply_migration(db)
        repo = SQLitePersistenceRepository(db, archive_reader=self.writer.reader, enable_real_provider_evidence=True)
        cases = [('wenda_notice_query', None), ('wenda_news_query', None), ('wenda_report_query', None),
                 *[('tdx_api_data', view) for view in ('profit', 'balance', 'cashflow')]]
        for n, (tool, view) in enumerate(cases):
            with self.subTest(tool=tool, view=view):
                _, _, record, _ = archived_information(self.writer, tool=tool, view=view,
                    raw_id='raw' + str(n), canonical_id='EV-' + str(n))
                repo._provider_guard((record,))
                repo._operation([('evidence_records', evidence_row(record, TIME))])
                recovered = EvidenceReadRepository(db, archive_reader=self.writer.reader).evidence(record.evidence_id)
                self.assertEqual(recovered['integrity_status'], 'VERIFIED')
                self.assertEqual(recovered['canonical_id'], record.evidence_id)
                self.assertEqual(recovered['raw_reference_id'], 'raw' + str(n))

    def test_upgrade_appends_and_preserves_all_existing_bytes(self):
        envelope, _, old, raw = archived_information(self.writer)
        directory = self.writer.resolver.resolve(old.raw_response_path)
        before = {p.name: p.read_bytes() for p in directory.iterdir()}
        result = InformationNormalizer().normalize(envelope, source_evidence=old, raw_bytes=raw)
        new = EvidenceBuilder().build(fetch_result=envelope, endpoint_or_tool=old.endpoint_or_tool,
            request_params=old.request_params, system_run_id=old.system_run_id, collection_task_id=old.collection_task_id,
            evidence_id='EV-00002', normalization_result=result, raw_response_hash=old.raw_response_hash,
            supersedes_evidence_id=old.evidence_id)
        validate_information_evidence(result, new)
        self.writer.append_normalized(old.raw_response_path, normalized_data=result.canonical_data,
            normalization_rule=new.normalization_rule, validation_result=new.validation_result)
        self.assertEqual(self.writer.append_evidence(new), 'APPENDED')
        self.assertEqual(self.writer.append_evidence(new), 'IDEMPOTENT_NOOP')
        self.writer.reader.verify_record(new)
        self.assertEqual(new.supersedes_evidence_id, 'EV-00001')
        self.assertEqual(before, {name: (directory / name).read_bytes() for name in before})

    def test_forged_context_hash_rules_or_identity_are_rejected(self):
        _, _, record, _ = archived_information(self.writer)
        for change in ({'raw_reference_id': 'wrong'}, {'canonical_id': 'abc123'},
                       {'evidence_id': 'other'}, {'system_run_id': 'other'},
                       {'raw_response_hash': '0' * 64}, {'normalized_data_hash': '0' * 64},
                       {'normalization_rule': ('wrong',)}, {'supersedes_evidence_id': 'missing'}):
            with self.subTest(change=change), self.assertRaises(ArchiveError):
                self.writer.reader.verify_record(replace(record, **change))

    def test_missing_canonical_sidecar_and_identity_reuse_fail_closed(self):
        _, _, record, _ = archived_information(self.writer)
        with self.assertRaises(ArchiveError):
            self.writer.append_evidence(replace(record, model_version='changed'))
        directory = self.writer.resolver.resolve(record.raw_response_path)
        next(directory.glob('evidence-*.json')).unlink()
        with self.assertRaises(ArchiveError):
            self.writer.reader.verify_record(record)
        self.assertTrue((directory / SIDECAR_NAME).is_file())
