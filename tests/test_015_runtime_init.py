"""Offline synthetic fixtures only; no real DB selectors or holdings."""
from contextlib import closing, redirect_stdout
import importlib.util
import io
import json
from pathlib import Path
import sqlite3
import stat
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from persistence.schema_v1_1 import TEST_SCHEMA_DDL
from persistence.migration import schema_fingerprint, legacy_digest, schema_inventory
from persistence.backup import file_sha256
from windows_compat import marked_reparse_point

spec = importlib.util.spec_from_file_location('runtime_init_015', Path(__file__).resolve().parents[1] / 'tools/015_runtime_init.py')
gate = importlib.util.module_from_spec(spec)
spec.loader.exec_module(gate)


class RuntimeInitTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.db = self.root / 'synthetic.db'
        with closing(sqlite3.connect(self.db)) as db:
            db.executescript(TEST_SCHEMA_DDL)
            db.execute('CREATE TABLE fictional_legacy (id INTEGER)')
            self.baseline = self.root / 'baseline.json'
            self.baseline.write_text(json.dumps(dict(SCHEMA_AFTER=schema_fingerprint(db),
                LEGACY_DIGEST_AFTER=legacy_digest(db), TABLE_COUNT_AFTER=sum(
                    r[0] == 'table' for r in schema_inventory(db)))), encoding='utf-8')
        self.config = self.root / 'config.json'
        self.config.write_text(json.dumps(dict(AUTO_TRADING=False, SCHEDULER=False, AUTO_REFRESH=False)))
        self.base = self.root / 'runtime-root'
        self.common = ['--base-dir', str(self.base), '--db-path', str(self.db), '--baseline',
                       str(self.baseline), '--config', str(self.config)]

    def invoke(self, step, *extra, success=True, code=None):
        with redirect_stdout(io.StringIO()) as output:
            result = gate.main([step, *self.common, *map(str, extra)])
        values = [json.loads(line) for line in output.getvalue().splitlines()]
        self.assertEqual(result, 0 if success else 1, values)
        if code:
            self.assertEqual(values[-1]['error_code'], code)
            self.assertEqual(values[-1]['RUNTIME_INITIALIZATION_STATUS'], 'BLOCKED')
        return values

    def security_args(self):
        path = self.root / 'synthetic-master.txt'
        path.write_text('fictional reference data')
        metadata = dict(sha256=file_sha256(path), source='synthetic-approved',
                        updated_at='2026-01-01T00:00:00Z', version='synthetic-v1')
        meta = self.root / 'metadata.json'
        meta.write_text(json.dumps(metadata))
        return ['--security-master-import=yes', '--security-file', path, '--security-metadata', meta,
                '--security-sha256', metadata['sha256'], '--security-source', metadata['source'],
                '--security-updated-at', metadata['updated_at'], '--security-version', metadata['version']]

    def holding_args(self):
        return ['--holding-config-write=yes', '--stock-code', 'SYNTHETIC_015', '--shares', '7',
                '--cost', '2.5', '--date', '2026-01-01']

    def test_each_step_pass_and_no_network_or_database_write(self):
        before = file_sha256(self.db)
        with patch('socket.socket.connect', side_effect=AssertionError('network forbidden')), \
             patch('dashboard.factory.create_runtime', side_effect=AssertionError('runtime forbidden')), \
             patch('dashboard.runtime.DashboardRuntime.generate', side_effect=AssertionError('LLM forbidden')), \
             patch.object(gate, 'open_database', wraps=gate.open_database) as opened:
            for step, extra in zip(gate.STEPS, [[], [], [], self.security_args(),
                                                self.holding_args(), ['--dashboard-data-write=yes']]):
                self.assertEqual(self.invoke(step, *extra)[0]['result'], 'PASS')
            self.assertTrue(all(c.kwargs == {'readonly': True} for c in opened.call_args_list))
        self.assertEqual(before, file_sha256(self.db))
        self.assertTrue((self.base / 'reports/dashboard.html').is_file())

    def test_authorization_fail_closed(self):
        for step, code in zip(gate.STEPS[3:], ['SECURITY_MASTER_IMPORT_NOT_AUTHORIZED',
                'HOLDING_CONFIG_WRITE_NOT_AUTHORIZED', 'DASHBOARD_DATA_WRITE_NOT_AUTHORIZED']):
            self.invoke(step, success=False, code=code)
        self.assertFalse(self.base.exists())

    def test_all_order_skips_and_summary(self):
        values = self.invoke('all')
        self.assertEqual([r['STEP'] for r in values[:-1]], list(gate.STEPS))
        self.assertEqual([r['result'] for r in values[:-1]], ['PASS'] * 3 +
                         ['SKIPPED_PENDING_AUTHORIZATION'] * 3)
        self.assertEqual(values[-1]['RUNTIME_INITIALIZATION_STATUS'], 'PENDING_AUTHORIZATION')

    def test_all_explicit_authorizations(self):
        values = self.invoke('all', *self.security_args(), *self.holding_args(), '--dashboard-data-write=yes')
        self.assertEqual(values[-1]['RUNTIME_INITIALIZATION_STATUS'], 'PASS')

    def test_schema_change_stops_all(self):
        with closing(sqlite3.connect(self.db)) as db:
            db.execute('CREATE TABLE changed (id INTEGER)')
        values = self.invoke('all', success=False, code='SCHEMA_CHANGED')
        self.assertEqual([r['STEP'] for r in values], ['015-01', '015-02'])

    def test_legacy_change_blocks(self):
        with closing(sqlite3.connect(self.db)) as db:
            db.execute('INSERT INTO fictional_legacy VALUES (1)')
            db.commit()
        self.invoke('015-02', success=False, code='LEGACY_DIGEST_CHANGED')

    def test_non_b_and_missing_baseline_block(self):
        with closing(sqlite3.connect(self.db)) as db:
            db.execute('DROP TABLE analysis_snapshots')
        self.invoke('015-02', success=False, code='SCHEMA_CHANGED')
        self.baseline.unlink()
        self.invoke('015-02', success=False, code='STEP_EXECUTION_FAILED')

    def test_config_requires_literal_false(self):
        for value in (True, 'false', 0, None):
            self.config.write_text(json.dumps(dict(AUTO_TRADING=value, SCHEDULER=False, AUTO_REFRESH=False)))
            self.invoke('015-03', success=False, code='UNSAFE_RUNTIME_CONFIG')

    def test_directory_preserves_important_files_and_rejects_collisions(self):
        nested = self.root / 'new-parent' / 'nested-root'
        report = self.invoke('015-01', '--base-dir', nested)[0]
        self.assertIn(str(nested.parent), report['CREATED_DIRECTORIES'])
        self.assertIn(str(nested), report['CREATED_DIRECTORIES'])
        self.invoke('015-01')
        important = self.base / 'runtime/important.txt'
        important.write_text('synthetic preserved')
        self.invoke('015-01')
        self.assertEqual(important.read_text(), 'synthetic preserved')
        (self.base / 'logs').rmdir()
        (self.base / 'logs').write_text('synthetic collision')
        self.invoke('015-01', success=False, code='DIRECTORY_UNSAFE')
        self.assertEqual((self.base / 'logs').read_text(), 'synthetic collision')

    def test_default_outside_repo_and_expected_head(self):
        self.assertFalse(gate.parser().parse_args(['015-01']).base_dir.resolve().is_relative_to(gate.ROOT))
        self.invoke('015-01', '--base-dir', gate.ROOT, success=False, code='BASE_DIR_IN_REPOSITORY')
        with patch.object(gate, 'git', return_value='synthetic-head'):
            self.invoke('015-01', '--expected-head', 'other', success=False, code='EXPECTED_HEAD_MISMATCH')
        self.assertFalse(self.base.exists())

    def test_symlinked_directory_rejected(self):
        link = self.root / 'linked'
        link.symlink_to(self.root, target_is_directory=True)
        self.invoke('015-01', '--base-dir', link / 'nested', success=False, code='DIRECTORY_UNSAFE')
        self.assertFalse((self.root / 'nested').exists())

    def test_reparse_root_and_original_ancestor_rejected(self):
        self.base.mkdir()
        for path in (self.base, self.base / 'nested', self.base / '..' / 'other'):
            with self.subTest(path=path), marked_reparse_point(self.base):
                self.invoke('015-01', '--base-dir', path, success=False, code='DIRECTORY_UNSAFE')
        self.assertEqual(list(self.base.iterdir()), [])
        self.assertFalse((self.root / 'other').exists())

    def test_reparse_runtime_subdirectories_rejected_before_creation(self):
        for name in ('runtime', 'cache', 'logs', 'reports'):
            path = self.base / name
            path.mkdir(parents=True)
            with self.subTest(name=name), marked_reparse_point(path):
                self.invoke('015-01', success=False, code='DIRECTORY_UNSAFE')
            self.assertEqual(list(self.base.iterdir()), [path])
            path.rmdir()

    def test_dashboard_reparse_reports_directory_rejected_before_write(self):
        self.invoke('015-01')
        reports = self.base / 'reports'
        with marked_reparse_point(reports):
            self.invoke('015-06', '--dashboard-data-write=yes', success=False, code='DIRECTORY_UNSAFE')
        self.assertEqual(list(reports.iterdir()), [])

    def test_directory_inspection_errors_fail_closed(self):
        self.base.mkdir()
        original_lstat = Path.lstat

        def unreadable(path, *args, **kwargs):
            if path == self.base:
                raise PermissionError('synthetic inspection failure')
            return original_lstat(path, *args, **kwargs)

        with patch.object(Path, 'lstat', new=unreadable):
            self.invoke('015-01', success=False, code='DIRECTORY_UNSAFE')
        self.assertEqual(list(self.base.iterdir()), [])

    def test_windows_missing_reparse_metadata_fails_closed(self):
        with patch.object(gate.sys, 'platform', 'win32'), \
             patch.object(Path, 'lstat', return_value=SimpleNamespace(st_mode=stat.S_IFDIR)):
            with self.assertRaisesRegex(gate.GateError, 'DIRECTORY_UNSAFE'):
                gate.require_safe_directory(self.base)
        self.assertFalse(self.base.exists())

    def test_security_rejects_mismatch(self):
        args = self.security_args()
        self.invoke('015-04', *args, '--security-sha256', '0' * 64, success=False, code='SECURITY_HASH_MISMATCH')
        for field in ('source', 'updated-at', 'version'):
            self.invoke('015-04', *args, '--security-' + field, 'mismatch', success=False,
                        code='SECURITY_METADATA_MISMATCH')

    def test_holding_invalid_and_never_echoed(self):
        for field, value in [('stock-code', ''), ('shares', '-1'), ('shares', 'NaN'),
                             ('shares', '1.5'), ('cost', '-1'), ('date', 'invalid')]:
            values = self.invoke('015-05', *self.holding_args(), '--' + field, value, success=False)
            self.assertNotIn('SYNTHETIC_015', json.dumps(values))
        self.invoke('015-05', '--holding-config-write=yes', success=False, code='HOLDING_FIELDS_INVALID')

    def test_dashboard_no_overwrite_or_readonly_write(self):
        self.invoke('015-01')
        self.invoke('015-06', success=False, code='DASHBOARD_DATA_WRITE_NOT_AUTHORIZED')
        self.assertEqual(list((self.base / 'reports').iterdir()), [])
        self.invoke('015-06', '--dashboard-data-write=yes')
        target = self.base / 'reports/dashboard.html'
        before = file_sha256(target)
        self.invoke('015-06', '--dashboard-data-write=yes', success=False)
        self.assertEqual(file_sha256(target), before)

    def test_missing_database_never_created(self):
        missing = self.root / 'missing.db'
        self.invoke('015-02', '--db-path', missing, success=False)
        self.assertFalse(missing.exists())

    def test_cli_error_redacts_arguments(self):
        with redirect_stdout(io.StringIO()) as output:
            self.assertEqual(gate.main(['015-01', '--unknown', 'synthetic-private']), 1)
        self.assertNotIn('synthetic-private', output.getvalue())


if __name__ == '__main__':
    unittest.main()
