"""C01 and configuration regression with synthetic files and no database IO."""
from contextlib import ExitStack
import importlib.util
import json
from pathlib import Path
import sqlite3
import stat
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from windows_compat import marked_reparse_point, symlink_privilege_missing


SPEC = importlib.util.spec_from_file_location(
    'phase_b_safety_gate',
    Path(__file__).resolve().parents[1] / 'tools/015_runtime_init.py',
)
gate = importlib.util.module_from_spec(SPEC)
with patch.object(sqlite3, 'connect', side_effect=AssertionError('database forbidden')):
    SPEC.loader.exec_module(gate)


class PhaseBSafetyTests(unittest.TestCase):
    def setUp(self):
        guards = ExitStack()
        self.addCleanup(guards.close)
        guards.enter_context(patch.object(sqlite3, 'connect',
                                         side_effect=AssertionError('database forbidden')))
        guards.enter_context(patch.object(gate, 'open_database',
                                         side_effect=AssertionError('database forbidden')))
        self.temp = tempfile.TemporaryDirectory(prefix='015-phase-b-safety-')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.config_path = self.root / 'synthetic-config.json'

    def validate_config(self, value):
        self.config_path.write_text(json.dumps(value), encoding='utf-8')
        report = {}
        gate.config(SimpleNamespace(config=self.config_path), report)
        return report

    def test_normal_directory_and_missing_target_are_checked_without_creation(self):
        gate.require_safe_directory(self.root, must_exist=True)
        target = self.root / 'future-runtime'
        gate.require_safe_directory(target)
        self.assertEqual(gate.external_root(target), target)
        self.assertFalse(target.exists())
        with self.assertRaisesRegex(gate.GateError, '^DIRECTORY_UNSAFE$'):
            gate.require_safe_directory(target, must_exist=True)

    def test_regular_file_is_not_a_safe_directory(self):
        target = self.root / 'synthetic-file'
        target.write_text('synthetic contents', encoding='utf-8')
        with self.assertRaisesRegex(gate.GateError, '^DIRECTORY_UNSAFE$'):
            gate.require_safe_directory(target)
        self.assertEqual(target.read_text(encoding='utf-8'), 'synthetic contents')

    def test_symlink_and_original_symlink_ancestor_are_rejected(self):
        link = self.root / 'linked-directory'
        try:
            link.symlink_to(self.root, target_is_directory=True)
        except OSError as exc:
            if symlink_privilege_missing(exc):
                self.skipTest('Windows symlink privilege is unavailable')
            raise
        with self.assertRaisesRegex(gate.GateError, '^DIRECTORY_UNSAFE$'):
            gate.require_safe_directory(link)
        for target in (link, link / 'future-runtime', link / '..' / 'other-runtime'):
            with self.subTest(target=target), \
                    patch.object(Path, 'resolve', side_effect=AssertionError('resolved unsafe path')):
                with self.assertRaisesRegex(gate.GateError, '^DIRECTORY_UNSAFE$'):
                    gate.external_root(target)

    def test_reparse_directory_and_original_reparse_ancestor_are_rejected(self):
        directory = self.root / 'marked-directory'
        directory.mkdir()
        with marked_reparse_point(directory):
            with self.assertRaisesRegex(gate.GateError, '^DIRECTORY_UNSAFE$'):
                gate.require_safe_directory(directory)
            for target in (directory, directory / 'nested', directory / '..' / 'other'):
                with self.subTest(target=target), \
                        patch.object(Path, 'resolve', side_effect=AssertionError('resolved unsafe path')):
                    with self.assertRaisesRegex(gate.GateError, '^DIRECTORY_UNSAFE$'):
                        gate.external_root(target)
        self.assertEqual(list(directory.iterdir()), [])
        self.assertFalse((self.root / 'other').exists())

    def test_each_runtime_child_rejects_reparse_metadata(self):
        for name in ('runtime', 'cache', 'logs', 'reports'):
            directory = self.root / name
            directory.mkdir()
            with self.subTest(name=name), marked_reparse_point(directory):
                with self.assertRaisesRegex(gate.GateError, '^DIRECTORY_UNSAFE$'):
                    gate.require_safe_directory(directory, must_exist=True)

    def test_windows_missing_reparse_metadata_fails_closed(self):
        with patch.object(gate.sys, 'platform', 'win32'), \
                patch.object(Path, 'lstat', return_value=SimpleNamespace(st_mode=stat.S_IFDIR)):
            with self.assertRaisesRegex(gate.GateError, '^DIRECTORY_UNSAFE$'):
                gate.require_safe_directory(self.root, must_exist=True)

    def test_windows_explicit_non_reparse_metadata_is_accepted(self):
        with patch.object(gate.sys, 'platform', 'win32'), \
                patch.object(Path, 'lstat', return_value=SimpleNamespace(
                    st_mode=stat.S_IFDIR, st_file_attributes=0)):
            gate.require_safe_directory(self.root, must_exist=True)

    def test_unreadable_metadata_fails_closed(self):
        with patch.object(Path, 'lstat', side_effect=PermissionError('synthetic denial')):
            with self.assertRaisesRegex(gate.GateError, '^DIRECTORY_UNSAFE$'):
                gate.require_safe_directory(self.root, must_exist=True)

    def test_repository_root_is_rejected_without_creation(self):
        with self.assertRaisesRegex(gate.GateError, '^BASE_DIR_IN_REPOSITORY$'):
            gate.external_root(gate.ROOT)

    def test_configuration_accepts_three_literal_false_values(self):
        value = {'AUTO_TRADING': False, 'SCHEDULER': False, 'AUTO_REFRESH': False}
        report = self.validate_config(value)
        self.assertEqual(report, {
            'DISABLED_FEATURES': ['AUTO_TRADING', 'SCHEDULER', 'AUTO_REFRESH'],
        })
        self.assertEqual(json.loads(self.config_path.read_text(encoding='utf-8')), value)

    def test_each_configuration_switch_requires_literal_boolean_false(self):
        safe = {'AUTO_TRADING': False, 'SCHEDULER': False, 'AUTO_REFRESH': False}
        for key in safe:
            for invalid in (True, 0, 0.0, 'false', '', None, [], {}):
                with self.subTest(key=key, invalid=invalid):
                    with self.assertRaisesRegex(gate.GateError, '^UNSAFE_RUNTIME_CONFIG$'):
                        self.validate_config({**safe, key: invalid})
            with self.subTest(missing=key):
                with self.assertRaisesRegex(gate.GateError, '^UNSAFE_RUNTIME_CONFIG$'):
                    self.validate_config({field: value for field, value in safe.items() if field != key})

    def test_configuration_rejects_non_objects_and_duplicate_keys(self):
        for value in ([], False, None, 'synthetic'):
            with self.subTest(value=value):
                with self.assertRaisesRegex(gate.GateError, '^UNSAFE_RUNTIME_CONFIG$'):
                    self.validate_config(value)
        self.config_path.write_text(
            '{"AUTO_TRADING":false,"AUTO_TRADING":false,"SCHEDULER":false,"AUTO_REFRESH":false}',
            encoding='utf-8',
        )
        with self.assertRaisesRegex(gate.GateError, '^DUPLICATE_JSON_KEY$'):
            gate.config(SimpleNamespace(config=self.config_path), {})


if __name__ == '__main__':
    unittest.main()
