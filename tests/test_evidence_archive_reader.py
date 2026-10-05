from dataclasses import replace
import gzip
import json
from pathlib import Path
import tempfile
import unittest

from evidence import ArchiveWriter, ArchiveReader, ArchiveError
from evidence.archive_contracts import (SIDECAR_NAME, canonical_json, sealed, sha256)
from test_evidence_archive_writer import write, ID
from windows_compat import marked_reparse_point, symlink_privilege_missing, use_simulated_directory_fsync


class ArchiveReaderTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        use_simulated_directory_fsync(self)
        self.writer = ArchiveWriter(Path(self.temp.name) / 'archive')
        self.reader = self.writer.reader
        self.receipt = write(self.writer)
        self.directory = self.writer.resolver.resolve(ID.uri)
        self.sidecar = self.directory / SIDECAR_NAME
        self.raw = self.directory / 'response.raw.gz'

    def test_verify_without_materializing_raw(self):
        self.assertEqual(self.reader.read(ID.uri, include_raw=False)['raw_parts'], {})
        self.reader.verify_record(self.receipt['record'])

    def test_all_identity_context_fields_checked(self):
        for key in ('system_run_id', 'collection_task_id', 'request_id', 'evidence_id'):
            with self.subTest(key=key), self.assertRaisesRegex(ArchiveError, 'CONTEXT_MISMATCH'):
                self.reader.read(ID.uri, expected=replace(ID, **{key: 'wrong'}))
        with self.assertRaisesRegex(ArchiveError, 'CONTEXT_MISMATCH'):
            self.reader.verify_record(replace(self.receipt['record'], evidence_id='wrong'))

    def test_sidecar_tamper(self):
        self.sidecar.write_bytes(self.sidecar.read_bytes().replace(b'get_market_snapshot', b'other_market_snapshot'))
        with self.assertRaisesRegex(ArchiveError, 'SIDECAR_HASH'):
            self.reader.read(ID.uri)

    def test_sidecar_noncanonical_duplicate_and_nonfinite(self):
        original = self.sidecar.read_bytes()
        for data in (b' ' + original, b'{"payload":{},"payload":{},"sha256":"x"}', b'{"payload":NaN,"sha256":"x"}'):
            self.sidecar.write_bytes(data)
            with self.subTest(data=data[:40]), self.assertRaises(ArchiveError):
                self.reader.read(ID.uri)

    def test_committed_metadata_missing_raw_is_corrupt(self):
        self.raw.unlink()
        with self.assertRaisesRegex(ArchiveError, 'CORRUPT_ARCHIVE'):
            self.reader.read(ID.uri)

    def test_missing_metadata_is_orphan(self):
        self.sidecar.unlink()
        with self.assertRaisesRegex(ArchiveError, 'ORPHAN_INCOMPLETE'):
            self.reader.read(ID.uri)

    def test_missing_identity(self):
        with self.assertRaisesRegex(ArchiveError, 'ARCHIVE_MISSING'):
            self.reader.read(replace(ID, request_id='missing').uri)

    def test_raw_tamper(self):
        data = bytearray(self.raw.read_bytes()); data[-1] ^= 1
        self.raw.write_bytes(data)
        with self.assertRaisesRegex(ArchiveError, 'STORED_HASH'):
            self.reader.read(ID.uri)

    def test_recomputed_stored_hash_still_checks_uncompressed_hash(self):
        data = gzip.compress(b'other-data', mtime=0)
        self.raw.write_bytes(data)
        meta = self.receipt['metadata']
        meta['parts'][0].update(stored_sha256=sha256(data), stored_size=len(data))
        self.sidecar.write_bytes(sealed(meta))
        with self.assertRaisesRegex(ArchiveError, 'RAW_HASH|RAW_SIZE'):
            self.reader.read(ID.uri)

    def test_bounded_gzip_bomb(self):
        data = gzip.compress(b'x' * 1000000, mtime=0)
        self.raw.write_bytes(data)
        meta = self.receipt['metadata']
        meta['parts'][0].update(stored_sha256=sha256(data), stored_size=len(data))
        self.sidecar.write_bytes(sealed(meta))
        with self.assertRaisesRegex(ArchiveError, 'RAW_SIZE'):
            self.reader.read(ID.uri)

    def test_duplicate_file_and_duplicate_manifest_part(self):
        for name in ('extra.raw.gz', 'normalized-copy.json', 'binding-copy.json'):
            extra = self.directory / name; extra.write_bytes(b'other')
            with self.subTest(name=name), self.assertRaisesRegex(ArchiveError, 'DUPLICATE_OR_UNKNOWN'):
                self.reader.read(ID.uri)
            extra.unlink()
        meta = self.receipt['metadata']; meta['parts'].append(meta['parts'][0])
        self.sidecar.write_bytes(sealed(meta))
        with self.assertRaisesRegex(ArchiveError, 'DUPLICATE_RAW_PART'):
            self.reader.read(ID.uri)

    def test_symlink_raw_and_metadata_rejected(self):
        for target in (self.raw, self.sidecar):
            original = target.read_bytes()
            outside = Path(self.temp.name) / 'outside'; outside.write_bytes(original)
            target.unlink()
            try:
                target.symlink_to(outside)
                with self.subTest(target=target.name), self.assertRaisesRegex(ArchiveError, 'SYMLINK_ESCAPE'):
                    self.reader.read(ID.uri)
                target.unlink(); target.write_bytes(original)
            except OSError as exc:
                if not symlink_privilege_missing(exc):
                    raise
                target.write_bytes(original)
                with marked_reparse_point(target), self.subTest(target=target.name), \
                        self.assertRaisesRegex(ArchiveError, 'SYMLINK_ESCAPE'):
                    self.reader.read(ID.uri)

    def test_metadata_identity_mismatch_even_after_hash_recomputed(self):
        for key in ('system_run_id', 'collection_task_id', 'request_id', 'evidence_id'):
            meta = json.loads(json.dumps(self.receipt['metadata']))
            meta['identity'][key] = 'wrong'
            self.sidecar.write_bytes(sealed(meta))
            with self.subTest(key=key), self.assertRaisesRegex(ArchiveError, 'CONTEXT_MISMATCH'):
                self.reader.read(ID.uri)

    def test_normalized_tamper_and_missing(self):
        norm = self.writer.append_normalized(ID.uri, normalized_data={'x': 1}, normalization_rule=[],
                                              validation_result={'status': 'PASS', 'failures': []})
        path = self.directory / ('normalized-' + norm['normalized_data_hash'] + '.json')
        path.write_bytes(path.read_bytes().replace(b'"x":1', b'"x":2'))
        with self.assertRaises(ArchiveError):
            self.reader.normalized(ID.uri, norm['normalized_data_hash'])
        path.unlink()
        with self.assertRaises(ArchiveError):
            self.reader.normalized(ID.uri, norm['normalized_data_hash'])

    def test_binding_mismatch_and_missing(self):
        binding = {'analysis_report_id': 'report', 'final_snapshot_hash': 'x'}
        with self.assertRaises(ArchiveError):
            self.reader.binding(ID.uri, 'report', binding)
        self.writer.append_analysis_binding(ID.uri, binding=binding)
        with self.assertRaisesRegex(ArchiveError, 'BINDING_MISMATCH'):
            self.reader.binding(ID.uri, 'report', {**binding, 'final_snapshot_hash': 'other'})

    def test_reader_does_not_modify_files(self):
        before = {p.name: (p.read_bytes(), p.stat().st_mtime_ns) for p in self.directory.iterdir()}
        self.reader.read(ID.uri)
        self.reader.read(ID.uri, include_raw=False)
        self.assertEqual(before, {p.name: (p.read_bytes(), p.stat().st_mtime_ns) for p in self.directory.iterdir()})
