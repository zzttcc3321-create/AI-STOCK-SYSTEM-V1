"""#014-R3: Windows path policy, runnable without a Windows filesystem."""
from contextlib import contextmanager
import os
from pathlib import Path, PureWindowsPath
import stat
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from evidence import ArchiveError, ArchiveIdentity, ArchiveResolver, ArchiveWriter
from test_evidence_archive_writer import ID, write


PREFIX = "\\\\?\\"


class WindowsProbePath(PureWindowsPath):
    """Use Windows parsing while mocking only the filesystem inspection."""
    def lstat(self):
        raise FileNotFoundError(str(self))


@contextmanager
def windows_paths():
    windows_os = SimpleNamespace(**vars(os))
    windows_os.name = 'nt'
    with patch('evidence.archive_resolver.os', windows_os), \
            patch('evidence.archive_resolver.Path', WindowsProbePath):
        yield windows_os


class WindowsLongPathPolicyTests(unittest.TestCase):
    def setUp(self):
        # Avoid native __init__ filesystem calls; safe() needs no root state.
        self.resolver = object.__new__(ArchiveResolver)

    def test_absolute_drive_path_gets_prefix(self):
        with windows_paths():
            for path in (r'C:\archive\v1\run', 'D:\\' + 'a' * 200 + '\\' + 'b' * 100):
                with self.subTest(path=path):
                    self.assertEqual(str(self.resolver.safe(path)), PREFIX + path)

    def test_prefix_is_idempotent(self):
        path = PREFIX + r'C:\archive\v1\run'
        with windows_paths():
            once = self.resolver.safe(path)
            self.assertEqual(str(once), path)
            self.assertEqual(self.resolver.safe(once), once)

    def test_unc_is_rejected(self):
        with windows_paths():
            for path in (r'\\server\share\archive', r'//server/share/archive'):
                with self.subTest(path=path), self.assertRaisesRegex(ArchiveError, 'UNC'):
                    self.resolver.safe(path)

    def test_extended_unc_is_rejected(self):
        with windows_paths(), self.assertRaisesRegex(ArchiveError, 'UNC'):
            self.resolver.safe(PREFIX + r'UNC\server\share\archive')

    def test_relative_and_drive_relative_are_rejected(self):
        with windows_paths():
            for path in ('archive', r'archive\v1', r'C:archive', r'\archive'):
                with self.subTest(path=path), self.assertRaisesRegex(ArchiveError, 'RELATIVE'):
                    self.resolver.safe(path)

    def test_root_keeps_checked_prefix_for_direct_filesystem_calls(self):
        with windows_paths() as windows_os:
            with patch.object(windows_os.path, 'realpath', side_effect=lambda path: str(path)):
                resolver = ArchiveResolver(r'C:\archive')
            self.assertEqual(str(resolver.root), PREFIX + r'C:\archive')
            identity = ArchiveIdentity('run', 'task', 'request', 'raw')
            self.assertEqual(str(resolver.resolve(identity.uri)),
                             PREFIX + r'C:\archive\v1\run\task\request\raw')
            self.assertEqual(identity.uri, 'archive://v1/run/task/request/raw')

    def test_original_ancestors_checked_before_prefix(self):
        checked = []

        def inspect(path):
            checked.append(str(path))
            return SimpleNamespace(st_mode=stat.S_IFDIR, st_file_attributes=0)

        with windows_paths(), patch.object(WindowsProbePath, 'lstat', inspect):
            result = self.resolver.safe(r'C:\archive\child')
        self.assertEqual(checked, ['C:\\', r'C:\archive', r'C:\archive\child'])
        self.assertEqual(str(result), PREFIX + r'C:\archive\child')

    def test_reparse_ancestor_rejected_even_with_existing_prefix(self):
        def inspect(path):
            return SimpleNamespace(st_mode=stat.S_IFDIR,
                                   st_file_attributes=0x400 if path.name == 'junction' else 0)

        with windows_paths(), patch.object(WindowsProbePath, 'lstat', inspect):
            for path in (r'C:\junction\archive', PREFIX + r'C:\junction\archive'):
                with self.subTest(path=path), self.assertRaisesRegex(ArchiveError, 'SYMLINK_ESCAPE'):
                    self.resolver.safe(path)

    def test_special_file_is_rejected_before_prefix(self):
        with windows_paths(), patch.object(WindowsProbePath, 'lstat',
                return_value=SimpleNamespace(st_mode=stat.S_IFIFO, st_file_attributes=0)), \
                self.assertRaisesRegex(ArchiveError, 'SPECIAL_FILE'):
            self.resolver.safe(r'C:\archive')

    def test_lstat_error_fails_closed(self):
        with windows_paths(), patch.object(WindowsProbePath, 'lstat',
                side_effect=PermissionError('inspection denied')), \
                self.assertRaisesRegex(PermissionError, 'inspection denied'):
            self.resolver.safe(r'C:\archive')


class LongPathSafetyRegressionTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.base = Path(temporary.name)
        self.writer = ArchiveWriter(self.base / 'archive')
        self.resolver = self.writer.resolver

    @unittest.skipIf(os.name == 'nt', 'Native non-Windows path behavior')
    def test_non_windows_safe_returns_original_path(self):
        for path in (self.base / 'missing', Path('relative') / 'child'):
            with self.subTest(path=path):
                self.assertEqual(self.resolver.safe(path), path)
                self.assertFalse(str(self.resolver.safe(path)).startswith(PREFIX))
        self.assertEqual(self.resolver.root, self.base / 'archive')

    def test_illegal_uri_rejected_without_writes(self):
        for uri in ('file:///C:/archive', 'archive://v2/run/task/request/raw',
                    'archive://v1/run/task/request/raw/extra'):
            with self.subTest(uri=uri), self.assertRaises(ArchiveError):
                self.resolver.resolve(uri)
        self.assertFalse(self.resolver.root.exists())

    def test_parent_traversal_uri_and_filename_rejected(self):
        for uri in ('archive://v1/../task/request/raw', 'archive://v1/run/task/request/%2e%2e'):
            with self.subTest(uri=uri), self.assertRaises(ArchiveError):
                self.resolver.resolve(uri)
        for name in ('..', '../outside.json'):
            with self.subTest(name=name), self.assertRaises(ArchiveError):
                self.resolver.file(self.resolver.root, name)
        self.assertFalse(self.resolver.root.exists())

    @unittest.skipUnless(os.name == 'posix', 'Real Linux symlink security check')
    def test_symlink_ancestor_rejected_including_before_parent_traversal(self):
        outside = self.base / 'outside'
        outside.mkdir()
        link = self.base / 'link'
        link.symlink_to(outside, target_is_directory=True)
        for path in (link / 'child', link / '..' / 'archive'):
            with self.subTest(path=path), self.assertRaisesRegex(ArchiveError, 'SYMLINK_ESCAPE'):
                self.resolver.safe(path)
        self.assertEqual(list(outside.iterdir()), [])

    def test_existing_raw_rejected_and_bytes_preserved(self):
        write(self.writer)
        directory = self.resolver.resolve(ID.uri)
        before = {p.name: p.read_bytes() for p in directory.iterdir()}
        with self.assertRaisesRegex(ArchiveError, 'IDENTITY_CONFLICT'):
            write(self.writer, parts={'response': b'changed'})
        self.assertEqual(before, {p.name: p.read_bytes() for p in directory.iterdir()})
        self.assertEqual(write(self.writer)['status'], 'IDEMPOTENT_NOOP')

    def test_existing_append_rejected_and_bytes_preserved(self):
        write(self.writer)
        binding = {'analysis_report_id': 'report', 'final_snapshot_hash': 'original'}
        self.assertEqual(self.writer.append_analysis_binding(ID.uri, binding=binding), 'APPENDED')
        directory = self.resolver.resolve(ID.uri)
        before = {p.name: p.read_bytes() for p in directory.iterdir()}
        with self.assertRaisesRegex(ArchiveError, 'IDENTITY_CONFLICT'):
            self.writer.append_analysis_binding(ID.uri, binding={**binding, 'final_snapshot_hash': 'changed'})
        self.assertEqual(before, {p.name: p.read_bytes() for p in directory.iterdir()})
        self.assertEqual(self.writer.append_analysis_binding(ID.uri, binding=binding), 'IDEMPOTENT_NOOP')

    def test_link_failure_fails_closed_and_cleans_staging(self):
        write(self.writer)
        with patch('evidence.archive_writer.os.link', side_effect=FileNotFoundError('WinError 3 simulation')):
            with self.assertRaisesRegex(FileNotFoundError, 'WinError 3'):
                self.writer.append_analysis_binding(ID.uri, binding={'analysis_report_id': 'failure'})
        self.assertFalse(list(self.resolver.root.rglob('.pending-*')))
        self.assertFalse(list(self.resolver.resolve(ID.uri).glob('binding-*')))
        self.assertEqual(self.writer.reader.read(ID.uri)['integrity_status'], 'VERIFIED')
