"""Phase C path checks with native files and host-independent Windows parsing."""
from pathlib import Path, PureWindowsPath
import tempfile
import unittest
from unittest.mock import patch

from tools import phase_c_safety as safety


class PhaseCSafePathTests(unittest.TestCase):
    def test_native_file_and_directory_can_be_validated_again(self):
        with tempfile.TemporaryDirectory(prefix='015-phase-c-path-') as root:
            directory = Path(root)
            file = directory / 'synthetic.sqlite'
            file.write_bytes(b'synthetic file')
            for expected, is_directory in ((file, False), (directory, True)):
                with self.subTest(directory=is_directory):
                    result = safety.safe_path(str(expected), directory=is_directory)
                    self.assertEqual(result, expected)
                    self.assertEqual(safety.safe_path(result, directory=is_directory), expected)

    def test_native_unc_relative_and_traversal_paths_are_rejected(self):
        with tempfile.TemporaryDirectory(prefix='015-phase-c-path-') as root:
            for raw in ('relative/path', str(Path(root) / '..' / 'other'),
                        r'\\server\share\file', '//server/share/file',
                        '/\\server/share/file'):
                for value in (raw, Path(raw)):
                    with self.subTest(value=value):
                        with self.assertRaisesRegex(safety.GateError,
                                                    '^ABSOLUTE_LOCAL_PATH_REQUIRED$'):
                            safety.safe_path(value, directory=True)


class PhaseCWindowsSafePathTests(unittest.TestCase):
    def setUp(self):
        # Exercise Windows lexical rules on every host; no Windows filesystem IO.
        path_patch = patch.object(safety, 'Path', PureWindowsPath)
        root_patch = patch.object(safety, 'ROOT', PureWindowsPath('C:/repo'))
        directory_patch = patch.object(safety, 'require_safe_directory')
        for patcher in (path_patch, root_patch, directory_patch):
            patcher.start()
            self.addCleanup(patcher.stop)

    def test_windows_local_paths_can_be_validated_again(self):
        for raw in ('D:/foo/bar', r'D:\foo\bar', r'D:\foo\\bar'):
            with self.subTest(raw=raw):
                result = safety.safe_path(raw, directory=True)
                self.assertEqual(result, PureWindowsPath('D:/foo/bar'))
                self.assertEqual(safety.safe_path(result, directory=True), result)

    def test_windows_unc_relative_and_traversal_paths_are_rejected(self):
        for raw in (r'\\server\share\file', '//server/share/file',
                    '/\\server/share/file', r'\\?\D:\foo',
                    r'D:\foo\..\bar', 'D:/foo/../bar',
                    r'D:foo\bar', r'\foo\bar', r'foo\bar'):
            for value in (raw, PureWindowsPath(raw)):
                with self.subTest(value=value):
                    with self.assertRaisesRegex(safety.GateError,
                                                '^ABSOLUTE_LOCAL_PATH_REQUIRED$'):
                        safety.safe_path(value, directory=True)
        safety.require_safe_directory.assert_not_called()
