from dataclasses import replace
from pathlib import Path
import os
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from evidence import ArchiveWriter
from evidence.archive_contracts import SIDECAR_NAME, json_hash, sealed, unseal
from persistence.connection import configure
from persistence.migration import apply_migration
from persistence.read_models import EvidenceReadRepository
from persistence.repository import SQLitePersistenceRepository, evidence_row
from test_information_archive_roundtrip import archived_information
from test_information_normalizer import TIME


class ReadModelArchiveDurabilityTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name) / 'archive'
        self.db = configure(sqlite3.connect(':memory:'))
        self.addCleanup(self.db.close)
        apply_migration(self.db)

    def archive(self, raw, normalized, canonical, *, canonical_id='EV-00001'):
        writers = [ArchiveWriter(self.root, durability_level=level)
                   for level in (raw, normalized, canonical)]
        with patch.object(writers[0], 'append_normalized', writers[1].append_normalized), \
                patch.object(writers[0], 'append_evidence', writers[2].append_evidence):
            _, _, record, _ = archived_information(writers[0], canonical_id=canonical_id)
        repo = SQLitePersistenceRepository(self.db, archive_reader=writers[0].reader,
                                           enable_real_provider_evidence=True)
        repo._provider_guard((record,))
        repo._operation([('evidence_records', evidence_row(record, TIME))])
        self.record = record
        self.writer = writers[0]
        return EvidenceReadRepository(self.db, archive_reader=self.writer.reader)

    def assert_projection(self, repo, stages, weakest):
        self.db.execute('PRAGMA query_only=ON')
        before_changes = self.db.total_changes
        before_archive = {p.relative_to(self.root): p.read_bytes()
                          for p in self.root.rglob('*') if p.is_file()}
        projected = repo.evidence(self.record.evidence_id)
        self.assertEqual(projected['integrity_status'], 'VERIFIED')
        self.assertEqual(projected['archive_durability_levels'], stages)
        self.assertEqual(projected['durability_level'], weakest)
        self.assertEqual(self.db.total_changes, before_changes)
        self.assertEqual(before_archive, {p.relative_to(self.root): p.read_bytes()
                                         for p in self.root.rglob('*') if p.is_file()})

    @unittest.skipIf(os.name == 'nt', 'DIRECTORY_SYNC requires actual directory fsync support')
    def test_all_directory_synced_stages(self):
        repo = self.archive(*(['DIRECTORY_SYNC'] * 3))
        self.assert_projection(repo, dict.fromkeys(('raw', 'normalized', 'canonical_evidence'),
                                                 'DIRECTORY_SYNC'), 'DIRECTORY_SYNC')

    def test_all_file_synced_stages_are_verified_with_explicit_weaker_durability(self):
        repo = self.archive(*(['FILE_SYNC_ONLY'] * 3))
        self.assert_projection(repo, dict.fromkeys(('raw', 'normalized', 'canonical_evidence'),
                                                 'FILE_SYNC_ONLY'), 'FILE_SYNC_ONLY')

    @unittest.skipIf(os.name == 'nt', 'Mixed real contracts require directory fsync support')
    def test_each_stage_can_independently_lower_overall_durability(self):
        for weak_stage in ('raw', 'normalized', 'canonical_evidence'):
            stages = dict.fromkeys(('raw', 'normalized', 'canonical_evidence'), 'DIRECTORY_SYNC')
            stages[weak_stage] = 'FILE_SYNC_ONLY'
            with self.subTest(stage=weak_stage):
                # Separate immutable identities/directories for each combination.
                self.root = self.root / weak_stage
                self.db.execute('PRAGMA query_only=OFF')
                repo = self.archive(*stages.values(), canonical_id='EV-' + weak_stage)
                self.assert_projection(repo, stages, 'FILE_SYNC_ONLY')

    def test_missing_unverified_and_invalid_stage_levels_fail_closed(self):
        repo = self.archive(*(['FILE_SYNC_ONLY'] * 3))
        directory = self.writer.resolver.resolve(self.record.raw_response_path)
        names = [SIDECAR_NAME, 'normalized-' + self.record.normalized_data_hash + '.json',
                 'evidence-' + json_hash(self.record.evidence_id) + '.json']
        for name in names:
            path = directory / name
            original = path.read_bytes()
            for level in (None, 'UNVERIFIED', 'FILE_ONLY'):
                payload = unseal(original)
                if level is None:
                    payload.pop('durability_level')
                else:
                    payload['durability_level'] = level
                path.write_bytes(sealed(payload))
                with self.subTest(stage=name, level=level):
                    projected = repo.evidence(self.record.evidence_id)
                    self.assertEqual(projected['integrity_status'], 'CORRUPT')
                    self.assertEqual(projected['durability_level'], 'UNVERIFIED')
                    self.assertEqual(projected['archive_durability_levels'], {})
                path.write_bytes(original)

    def test_no_reader_and_unsupported_reference_do_not_infer_durability(self):
        repo = self.archive(*(['FILE_SYNC_ONLY'] * 3))
        no_reader = EvidenceReadRepository(self.db).evidence(self.record.evidence_id)
        self.assertEqual(no_reader['durability_level'], 'UNVERIFIED')
        self.assertEqual(no_reader['archive_durability_levels'], {})
        legacy_record = replace(self.record, evidence_id='OLD-RAW', canonical_id=None,
                                raw_reference_id=None, raw_response_path='tdx-local/legacy/raw.json')
        SQLitePersistenceRepository(self.db, allow_synthetic_evidence=True)._operation(
            [('evidence_records', evidence_row(legacy_record, TIME))])
        with patch.object(self.writer.reader, 'read', side_effect=AssertionError('legacy must not be resolved')):
            legacy = repo.evidence(legacy_record.evidence_id)
        self.assertEqual(legacy['durability_level'], 'UNVERIFIED')
        self.assertEqual(legacy['archive_durability_levels'], {})
