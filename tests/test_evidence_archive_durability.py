"""#014-R2: actual Linux durability and a Windows capability simulation."""
from dataclasses import replace
import os
from pathlib import Path
import stat
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from evidence import ArchiveError, ArchiveReader, ArchiveWriter
from evidence.archive_contracts import SIDECAR_NAME, sealed
from evidence.contracts import DurabilityLevel
from test_evidence_archive_writer import ID, write


class ArchiveDurabilityTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name) / 'archive'

    @unittest.skipUnless(os.name == 'posix', 'Requires actual Unix directory fsync')
    def test_linux_directory_fsync_available_archived_directory_sync(self):
        writer = ArchiveWriter(self.root)
        synced = []
        real_fsync = os.fsync

        def sync(fd):
            synced.append('directory' if stat.S_ISDIR(os.fstat(fd).st_mode) else 'file')
            return real_fsync(fd)

        with patch('evidence.archive_writer.os.fsync', side_effect=sync):
            receipt = write(writer)
        self.assertEqual(receipt['status'], 'ARCHIVED')
        self.assertIn('directory', synced)
        self.assertEqual(synced.count('file'), 2)
        self.assertEqual(receipt['durability_level'], 'DIRECTORY_SYNC')
        self.assertEqual(receipt['metadata']['durability_level'], 'DIRECTORY_SYNC')
        self.assertEqual(ArchiveReader(self.root).read(ID.uri)['durability_level'], 'DIRECTORY_SYNC')

        # Linux can explicitly select file durability too. A retry reports the
        # stored contract, without upgrading/downgrading an immutable archive.
        file_writer = ArchiveWriter(self.root, durability_level=DurabilityLevel.FILE_SYNC_ONLY)
        retry = write(file_writer)
        self.assertEqual(retry['status'], 'IDEMPOTENT_NOOP')
        self.assertEqual(retry['durability_level'], 'DIRECTORY_SYNC')
        receipt = write(file_writer, identity=replace(ID, evidence_id='file-only'))
        self.assertEqual(receipt['status'], 'ARCHIVED')
        self.assertEqual(receipt['durability_level'], 'FILE_SYNC_ONLY')

    def test_windows_directory_fsync_unavailable_archived_file_sync_only(self):
        windows_os = SimpleNamespace(**vars(os))
        windows_os.name = 'nt'
        # This is the real failing primitive on Windows. File handles are still
        # opened by pathlib; their flush/fsync and the atomic rename are real.
        windows_os.open = unittest.mock.Mock(side_effect=PermissionError('directory open unavailable'))
        synced = []
        real_fsync, real_rename = os.fsync, os.rename

        def sync(fd):
            self.assertTrue(stat.S_ISREG(os.fstat(fd).st_mode))
            synced.append(fd)
            return real_fsync(fd)

        def rename(source, target):
            self.assertEqual(len(synced), 2)  # Raw and sealed metadata synced first.
            return real_rename(source, target)

        windows_os.fsync = sync
        windows_os.rename = rename
        with patch('evidence.archive_writer.os', windows_os):
            writer = ArchiveWriter(self.root)
            receipt = write(writer)
            self.assertEqual(receipt['status'], 'ARCHIVED')
            self.assertEqual(receipt['durability_level'], 'FILE_SYNC_ONLY')
            result = ArchiveReader(self.root).read(ID.uri)
            self.assertEqual(result['durability_level'], 'FILE_SYNC_ONLY')
            self.assertEqual(result['metadata']['durability_level'], 'FILE_SYNC_ONLY')
            self.assertEqual(result['raw_parts'], {'response': b'\xff\x00binary\x80'})
            normalized = writer.append_normalized(ID.uri, normalized_data={'x': 1}, normalization_rule=[],
                                                  validation_result={'status': 'PASS', 'failures': []})
            self.assertEqual(normalized['durability_level'], 'FILE_SYNC_ONLY')
            self.assertEqual(writer.reader.normalized(ID.uri, normalized['normalized_data_hash']), normalized)
            binding = {'analysis_report_id': 'report'}
            self.assertEqual(writer.append_analysis_binding(ID.uri, binding=binding), 'APPENDED')
            self.assertEqual(writer.reader.binding(ID.uri, 'report', binding)['durability_level'], 'FILE_SYNC_ONLY')
            windows_os.open.assert_not_called()
        # Even a caller requesting a stronger contract cannot relabel this
        # committed data on idempotent retry or append retry.
        stronger = ArchiveWriter(self.root, durability_level=DurabilityLevel.DIRECTORY_SYNC)
        self.assertEqual(write(stronger)['durability_level'], 'FILE_SYNC_ONLY')
        retry = stronger.append_normalized(ID.uri, normalized_data={'x': 1}, normalization_rule=[],
                                           validation_result={'status': 'PASS', 'failures': []})
        self.assertEqual(retry['durability_level'], 'FILE_SYNC_ONLY')

    def test_file_fsync_failure_not_archived(self):
        real_fsync = os.fsync
        for contract in (DurabilityLevel.FILE_SYNC_ONLY, DurabilityLevel.DIRECTORY_SYNC):
            if contract == DurabilityLevel.DIRECTORY_SYNC and os.name == 'nt':
                continue  # Windows' supported file contract is exercised above.
            for failure_number in (1, 2):  # Raw file and commit sidecar.
                with self.subTest(contract=contract, file=failure_number):
                    writer = ArchiveWriter(self.root / (contract.value + str(failure_number)),
                                           durability_level=contract)
                    count = 0

                    def sync(fd):
                        nonlocal count
                        if stat.S_ISREG(os.fstat(fd).st_mode):
                            count += 1
                            if count == failure_number:
                                raise OSError('injected file fsync failure')
                        return real_fsync(fd)

                    with patch('evidence.archive_writer.os.fsync', side_effect=sync):
                        with self.assertRaisesRegex(OSError, 'file fsync failure'):
                            write(writer)
                    self.assertEqual(count, failure_number)
                    self.assertFalse(writer.resolver.resolve(ID.uri).exists())
                    self.assertFalse(list(writer.resolver.root.rglob('.pending-*')))
                    with self.assertRaisesRegex(ArchiveError, 'ARCHIVE_MISSING'):
                        writer.reader.read(ID.uri)

    def test_unverified_missing_and_invalid_durability_are_rejected(self):
        writer = ArchiveWriter(self.root, durability_level=DurabilityLevel.FILE_SYNC_ONLY)
        receipt = write(writer)
        sidecar = writer.resolver.resolve(ID.uri) / SIDECAR_NAME
        for level in ('UNVERIFIED', None, 'FILE_ONLY', ['DIRECTORY_SYNC']):
            meta = dict(receipt['metadata'])
            if level is None:
                meta.pop('durability_level')
            else:
                meta['durability_level'] = level
            sidecar.write_bytes(sealed(meta))
            with self.subTest(level=level), self.assertRaisesRegex(ArchiveError, 'ARCHIVE_DURABILITY'):
                writer.reader.read(ID.uri)
            with self.assertRaisesRegex(ArchiveError, 'ARCHIVE_DURABILITY'):
                write(writer)
            with self.assertRaisesRegex(ArchiveError, 'ARCHIVE_DURABILITY'):
                writer.append_analysis_binding(ID.uri, binding={'analysis_report_id': 'unverified'})
        with self.assertRaisesRegex(ArchiveError, 'ARCHIVE_DURABILITY_UNVERIFIED'):
            ArchiveWriter(self.root, durability_level=DurabilityLevel.UNVERIFIED)

    def test_file_flush_failure_not_archived(self):
        writer = ArchiveWriter(self.root, durability_level=DurabilityLevel.FILE_SYNC_ONLY)
        real_open = Path.open

        class FlushFailure:
            def __init__(self, path): self.file = real_open(path, 'xb')
            def __enter__(self): return self
            def __exit__(self, *args): self.file.close()
            def write(self, data): return self.file.write(data)
            def fileno(self): return self.file.fileno()
            def flush(self): raise OSError('injected file flush failure')

        with patch('pathlib.Path.open', side_effect=lambda path, *args, **kw: FlushFailure(path), autospec=True):
            with self.assertRaisesRegex(OSError, 'file flush failure'):
                write(writer)
        self.assertFalse(writer.resolver.resolve(ID.uri).exists())
        self.assertFalse(list(writer.resolver.root.rglob('.pending-*')))
