"""Phase C contracts, using only synthetic SQLite/CSV fixtures in temporary dirs.

security_master is created by 015-04 when absent, using the locked canonical DDL.
No selector, network connection, or fixture refers to the Windows fact database.
"""
from contextlib import closing, redirect_stderr, redirect_stdout
import csv
import hashlib
import importlib
import io
import json
import os
from pathlib import Path
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = {
    '015-04': ROOT / 'tools/015_04_security_master_import.py',
    '015-05': ROOT / 'tools/015_05_holding_config_write.py',
    '015-06': ROOT / 'tools/015_06_dashboard_seed.py',
}
AUTH = {
    '015-04': 'SECURITY_MASTER_IMPORT=YES',
    '015-05': 'HOLDING_CONFIG_WRITE=YES',
    '015-06': 'DASHBOARD_INITIAL_DATA_WRITE=YES',
}
SECURITY_COLUMNS = (
    'market', 'symbol', 'name', 'security_type', 'lot_size', 'currency',
    'list_date', 'status',
)
HOLDING_COLUMNS = ('market', 'symbol', 'quantity', 'avg_cost')
from tools.phase_c_contracts import DDL_BY_STEP

SECURITY_DDL = DDL_BY_STEP['015-04'][0]
HOLDING_DDL = """CREATE TABLE IF NOT EXISTS holding_config(
    market   TEXT NOT NULL,
    symbol   TEXT NOT NULL,
    quantity INTEGER NOT NULL CHECK(quantity >= 0),
    avg_cost REAL NOT NULL CHECK(avg_cost >= 0 AND avg_cost = ROUND(avg_cost, 2)),
    PRIMARY KEY(market, symbol)
  );
  CREATE TABLE IF NOT EXISTS holding_config_meta(
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
  );"""
DASHBOARD_DDL = """CREATE TABLE IF NOT EXISTS dashboard_position_snapshot(
    batch_id   TEXT NOT NULL,
    market     TEXT NOT NULL,
    symbol     TEXT NOT NULL,
    name       TEXT NOT NULL,
    quantity   INTEGER NOT NULL,
    avg_cost   REAL NOT NULL,
    cost_value REAL NOT NULL,
    as_of_date TEXT NOT NULL,
    PRIMARY KEY(batch_id, market, symbol)
  );
  CREATE TABLE IF NOT EXISTS dashboard_overview(
    batch_id         TEXT PRIMARY KEY,
    as_of_date       TEXT NOT NULL,
    total_positions  INTEGER NOT NULL,
    total_cost_value REAL NOT NULL,
    currency         TEXT NOT NULL DEFAULT 'CNY'
  );"""
MASTER_ROWS = [
    ['SSE', '999999', 'PRIVATE_FAKE_SECURITY_NAME', 'STOCK', '100', 'CNY', '2020-01-01', 'LISTED'],
    ['SZSE', '000099', 'PRIVATE_FAKE_SECURITY_NAME', 'ETF', '1', '', '', 'SUSPENDED'],
]
HOLDING_ROWS = [
    ['SSE', '999999', '947031', '123.47'],
    ['SZSE', '000099', '0', '0.00'],
]
DEFAULT = object()


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


class PhaseCPrepTests(unittest.TestCase):
    def setUp(self):
        # Linux dev-box artifacts live in /tmp; other CI platforms use their temp root.
        self.temp = tempfile.TemporaryDirectory(prefix='015-phase-c-test-',
                                                dir='/tmp' if Path('/tmp').is_dir() else None)
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.db = self.root / 'synthetic.sqlite'
        self.backups = self.root / 'backups'
        self.master = self.root / 'synthetic-master.csv'
        self.holdings = self.root / 'PRIVATE_POSITION_SOURCE.csv'
        self.as_of = '2026-10-16'
        with closing(sqlite3.connect(self.db)) as db:
            db.execute(SECURITY_DDL)
            db.execute('CREATE TABLE unrelated_fixture(value TEXT NOT NULL)')
            db.execute("INSERT INTO unrelated_fixture VALUES ('preserve synthetic baseline')")
            db.commit()
        self.write_csv(self.master, SECURITY_COLUMNS, MASTER_ROWS)
        self.write_csv(self.holdings, HOLDING_COLUMNS, HOLDING_ROWS)

    def write_csv(self, path, columns, rows):
        with Path(path).open('w', encoding='utf-8', newline='') as file:
            writer = csv.writer(file)
            writer.writerow(columns)
            writer.writerows(rows)

    def query(self, sql, values=()):
        with closing(sqlite3.connect(self.db)) as db:
            return db.execute(sql, values).fetchall()

    def mutate(self, sql, values=()):
        with closing(sqlite3.connect(self.db)) as db:
            db.execute(sql, values)
            db.commit()

    def schema(self):
        return self.query('SELECT type,name,tbl_name,sql FROM sqlite_master ORDER BY type,name')

    def seed_master(self):
        with closing(sqlite3.connect(self.db)) as db:
            for row in MASTER_ROWS:
                values = list(row)
                values[5] = values[5] or 'CNY'
                values[6] = values[6] or None
                db.execute('INSERT INTO security_master VALUES (?,?,?,?,?,?,?,?)', values)
            db.commit()

    def seed_holdings(self, rows=None, meta=None):
        self.seed_master()
        with closing(sqlite3.connect(self.db)) as db:
            db.executescript(HOLDING_DDL)
            db.executemany('INSERT INTO holding_config VALUES (?,?,?,?)',
                           HOLDING_ROWS if rows is None else rows)
            db.executemany('INSERT INTO holding_config_meta VALUES (?,?)', meta if meta is not None else [
                ('as_of_date', self.as_of), ('version', '1.0'), ('source_sha256', sha(self.holdings)),
            ])
            db.commit()

    def argv(self, step, *, auth=DEFAULT, expected_hash=None, target=None,
             backup=None, source=None, date=None, dry=False, extra=()):
        argv = ['--target-db', str(self.db if target is None else target),
                '--expected-db-sha256', sha(self.db) if expected_hash is None else expected_hash,
                '--backup-dir', str(self.backups if backup is None else backup)]
        if auth is DEFAULT:
            auth = AUTH[step]
        if auth is not None:
            argv += ['--auth-flag', auth]
        if step in ('015-04', '015-05'):
            argv += ['--source', str(source if source is not None else
                                    (self.master if step == '015-04' else self.holdings))]
        if step == '015-05':
            argv += ['--as-of-date', self.as_of if date is None else date]
        if dry:
            argv.append('--dry-run')
        return argv + list(extra)

    def assert_redacted(self, output):
        for sensitive in ('999999', '000099', 'PRIVATE_FAKE_SECURITY_NAME',
                          'PRIVATE_POSITION_SOURCE', '947031', '123.47', '116929917.57'):
            self.assertNotIn(sensitive, output, 'CLI leaked synthetic position/source details')

    def parse_output(self, stdout, *, success, dry=False):
        # The value-free JSON report remains first. Only successful non-dry
        # invocations publish the hash that a runner may hand to the next step.
        lines = stdout.splitlines()
        handoff_expected = success and not dry
        self.assertEqual(len(lines), 2 if handoff_expected else 1, stdout)
        report = json.loads(lines[0])
        self.assertEqual(report['result'], 'PASS' if success else 'FAIL')
        if handoff_expected:
            self.assertRegex(lines[1], r'^POST_WRITE_SHA256=[0-9a-f]{64}$')
            self.assertEqual(lines[1].split('=', 1)[1], sha(self.db))
        else:
            self.assertNotIn('POST_WRITE_SHA256=', stdout)
        if not success:
            self.assertTrue(report.get('error_code') or report.get('error'))
        return report

    def handoff_hash(self, output):
        lines = [line for line in output.splitlines() if line.startswith('POST_WRITE_SHA256=')]
        self.assertEqual(len(lines), 1, output)
        return lines[0].split('=', 1)[1]

    def invoke(self, step, *, success=True, **kwargs):
        result = subprocess.run([sys.executable, str(SCRIPTS[step]), *self.argv(step, **kwargs)],
                                cwd=ROOT, capture_output=True, text=True,
                                env={**os.environ, 'PYTHONDONTWRITEBYTECODE': '1'}, check=False)
        self.assert_redacted(result.stdout + result.stderr)
        if success:
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        else:
            self.assertNotEqual(result.returncode, 0, 'Unexpected success')
        report = self.parse_output(result.stdout, success=success, dry=kwargs.get('dry', False))
        return report, result.stdout + result.stderr

    def invoke_direct(self, step, *, success=True, **kwargs):
        safety = importlib.import_module('tools.phase_c_safety')
        output, errors = io.StringIO(), io.StringIO()
        with redirect_stdout(output), redirect_stderr(errors):
            result = safety.main(step, self.argv(step, **kwargs))
        raw = output.getvalue() + errors.getvalue()
        self.assert_redacted(raw)
        self.assertEqual(result, 0 if success else 1, raw)
        report = self.parse_output(output.getvalue(), success=success, dry=kwargs.get('dry', False))
        return report, raw

    def assert_refused_unchanged(self, step, **kwargs):
        before = self.db.read_bytes()
        before_schema = self.schema()
        result = self.invoke(step, success=False, **kwargs)
        self.assertEqual(self.db.read_bytes(), before, 'Failed batch changed database bytes')
        self.assertEqual(self.schema(), before_schema)
        return result

    def files(self):
        return {str(path.relative_to(self.root)): path.read_bytes()
                for path in self.root.rglob('*') if path.is_file()}

    def assert_schema_only_new_tables(self, before, allowed):
        after = self.schema()
        for existing in before:
            self.assertIn(existing, after)
        for item in after:
            if item not in before:
                self.assertIn(item[2], allowed)
                self.assertIn(item[0], ('table', 'index'))
                if item[0] == 'index':
                    self.assertTrue(item[1].startswith('sqlite_autoindex_'))
        self.assertEqual({row[1] for row in after if row[0] == 'table'} -
                         {row[1] for row in before if row[0] == 'table'}, set(allowed))
        self.assertEqual(self.query('SELECT * FROM unrelated_fixture'), [('preserve synthetic baseline',)])

    def test_all_scripts_auth_flags_are_required_exact_and_step_specific(self):
        for step in SCRIPTS:
            for auth in (None, '', AUTH[step].lower(), AUTH[step] + ' ', 'YES',
                         next(value for key, value in AUTH.items() if key != step)):
                with self.subTest(step=step, auth_case=auth is None):
                    self.assert_refused_unchanged(step, auth=auth)
                    self.assertFalse(self.backups.exists())

    def test_all_scripts_reject_wrong_or_malformed_database_hash(self):
        for step in SCRIPTS:
            for expected_hash in ('0' * 64, 'invalid', ''):
                with self.subTest(step=step):
                    self.assert_refused_unchanged(step, expected_hash=expected_hash)
                    self.assertFalse(self.backups.exists())

    def test_full_cli_chain_uses_previous_post_write_hash_and_noop_handoffs(self):
        self.mutate('DROP TABLE security_master')
        expected = sha(self.db)
        for step in SCRIPTS:
            with self.subTest(step=step):
                report, output = self.invoke(step, expected_hash=expected)
                self.assertEqual(report['status'], 'APPLIED')
                post_write = self.handoff_hash(output)
                self.assertNotEqual(post_write, expected)
                before_noop = self.files()
                report, output = self.invoke(step, expected_hash=post_write)
                self.assertEqual(report['status'], 'IDEMPOTENT_NOOP')
                expected = self.handoff_hash(output)
                self.assertEqual(expected, post_write)
                self.assertEqual(self.files(), before_noop)
                self.invoke(step, expected_hash=expected, dry=True)
                self.assertEqual(self.files(), before_noop)
        self.assertEqual(self.query('SELECT COUNT(*) FROM security_master'), [(len(MASTER_ROWS),)])
        self.assertEqual(self.query('SELECT COUNT(*) FROM holding_config'), [(len(HOLDING_ROWS),)])
        self.assertEqual(self.query('SELECT COUNT(*) FROM dashboard_position_snapshot'),
                         [(len(HOLDING_ROWS),)])
        self.assertEqual(self.query('SELECT COUNT(*) FROM dashboard_overview'), [(1,)])

    def test_chain_rejects_stale_or_tampered_handoff_at_both_boundaries(self):
        expected = sha(self.db)
        for step, next_step in (('015-04', '015-05'), ('015-05', '015-06')):
            report, output = self.invoke(step, expected_hash=expected)
            self.assertEqual(report['status'], 'APPLIED')
            handoff = self.handoff_hash(output)
            tampered = ('0' if handoff[0] != '0' else '1') + handoff[1:]
            for invalid in (expected, tampered):
                with self.subTest(boundary=step + ' -> ' + next_step):
                    before = self.files()
                    report, _ = self.assert_refused_unchanged(next_step, expected_hash=invalid)
                    self.assertEqual(report['error'], 'DATABASE_HASH_MISMATCH')
                    self.assertEqual(self.files(), before)
            expected = handoff
        self.invoke('015-06', expected_hash=expected)

    def test_chain_rejects_database_tampering_between_steps(self):
        baseline = self.db.read_bytes()
        for boundary in ('015-04', '015-05'):
            with self.subTest(boundary=boundary):
                self.db.write_bytes(baseline)
                expected = sha(self.db)
                for step in SCRIPTS:
                    _, output = self.invoke(step, expected_hash=expected)
                    expected = self.handoff_hash(output)
                    if step == boundary:
                        break
                self.mutate("INSERT INTO unrelated_fixture VALUES ('synthetic between-step change')")
                before = self.files()
                next_step = '015-05' if boundary == '015-04' else '015-06'
                report, _ = self.assert_refused_unchanged(next_step, expected_hash=expected)
                self.assertEqual(report['error'], 'DATABASE_HASH_MISMATCH')
                self.assertEqual(self.files(), before)

    def test_required_arguments_cannot_be_omitted_or_create_files(self):
        for step in SCRIPTS:
            required = ['--target-db', '--expected-db-sha256', '--backup-dir']
            if step != '015-06':
                required.append('--source')
            if step == '015-05':
                required.append('--as-of-date')
            for argument in required:
                with self.subTest(step=step, missing_argument=argument):
                    argv = self.argv(step)
                    index = argv.index(argument)
                    del argv[index:index + 2]
                    before = self.files()
                    with patch.object(self, 'argv', return_value=argv):
                        self.assert_refused_unchanged(step)
                    self.assertEqual(self.files(), before)

    def test_all_paths_must_be_absolute_and_missing_target_not_created(self):
        for step in SCRIPTS:
            for overrides in ({'target': 'relative.sqlite'}, {'backup': 'relative-backup'},
                              {'target': self.root / 'missing.sqlite'}):
                with self.subTest(step=step, path_kind=next(iter(overrides))):
                    self.assert_refused_unchanged(step, **overrides)
            self.assertFalse((self.root / 'missing.sqlite').exists())
        for step in ('015-04', '015-05'):
            self.assert_refused_unchanged(step, source='relative.csv')
        self.assertFalse(self.backups.exists())

    def test_backup_symlink_and_file_are_rejected_without_creation(self):
        occupied = self.root / 'occupied'
        occupied.write_text('preserve this file', encoding='utf-8')
        for step in SCRIPTS:
            self.assert_refused_unchanged(step, backup=occupied)
        linked = self.root / 'linked'
        try:
            linked.symlink_to(self.root, target_is_directory=True)
        except OSError:
            self.skipTest('Host does not permit symlinks')
        for step in SCRIPTS:
            self.assert_refused_unchanged(step, backup=linked / 'new-backup')
        self.assertFalse((self.root / 'new-backup').exists())
        self.assertEqual(occupied.read_text(encoding='utf-8'), 'preserve this file')

    def test_database_and_source_symlinks_are_rejected(self):
        link = self.root / 'db-link.sqlite'
        source_link = self.root / 'source-link.csv'
        try:
            link.symlink_to(self.db)
            source_link.symlink_to(self.master)
        except OSError:
            self.skipTest('Host does not permit symlinks')
        for step in SCRIPTS:
            self.assert_refused_unchanged(step, target=link)
        self.assert_refused_unchanged('015-04', source=source_link)

    def test_database_and_source_hard_links_are_rejected(self):
        db_link = self.root / 'hard-linked.sqlite'
        source_link = self.root / 'hard-linked.csv'
        try:
            os.link(self.db, db_link)
        except OSError:
            self.skipTest('Host does not permit hard links')
        for step in SCRIPTS:
            self.assert_refused_unchanged(step, target=db_link)
        db_link.unlink()
        for step, source in (('015-04', self.master), ('015-05', self.holdings)):
            os.link(source, source_link)
            self.assert_refused_unchanged(step, source=source_link)
            source_link.unlink()
        self.assertFalse(self.backups.exists())

    def test_symlinked_parent_of_database_or_source_is_rejected(self):
        linked = self.root / 'linked-parent'
        try:
            linked.symlink_to(self.root, target_is_directory=True)
        except OSError:
            self.skipTest('Host does not permit symlinks')
        for step in SCRIPTS:
            self.assert_refused_unchanged(step, target=linked / self.db.name)
        for step, source in (('015-04', self.master), ('015-05', self.holdings)):
            self.assert_refused_unchanged(step, source=linked / source.name)
        self.assertFalse(self.backups.exists())

    def test_each_step_dry_run_validates_without_any_filesystem_change(self):
        for step in SCRIPTS:
            if step == '015-05':
                self.seed_master()
            if step == '015-06':
                self.invoke('015-05')
                shutil.rmtree(self.backups)
            before = self.files()
            report, _ = self.invoke(step, dry=True)
            self.assertTrue(report['dry_run'])
            self.assertEqual(report['status'], 'DRY_RUN_VALIDATED')
            self.assertEqual(report['changed'], 0)
            self.assertGreater(report['planned_changed'], 0)
            self.assertNotIn('inserted', report)
            self.assertNotIn('deleted', report)
            self.assertEqual(self.files(), before)
            self.assertFalse(self.backups.exists())

    def test_dry_run_missing_master_rehearses_create_without_filesystem_changes(self):
        self.mutate('DROP TABLE security_master')
        before = self.files()
        report, _ = self.invoke('015-04', dry=True)
        self.assertEqual(report['status'], 'DRY_RUN_VALIDATED')
        self.assertEqual(self.files(), before)
        self.assertNotIn(('security_master',), self.query("SELECT name FROM sqlite_master WHERE type='table'"))

    def test_missing_master_is_created_and_imported_end_to_end(self):
        self.mutate('DROP TABLE security_master')
        before_schema = self.schema()
        before_hash = sha(self.db)
        report, output = self.invoke('015-04', expected_hash=before_hash)
        self.assertEqual(report['status'], 'APPLIED')
        self.assertEqual(report['inserted'], len(MASTER_ROWS))
        self.assert_schema_only_new_tables(before_schema, {'security_master'})
        self.assertEqual(tuple(row[1] for row in self.query('PRAGMA table_xinfo(security_master)')),
                         SECURITY_COLUMNS)
        expected = [tuple(row[:5] + [row[5] or 'CNY', row[6] or None, row[7]])
                    for row in MASTER_ROWS]
        observed = self.query('SELECT * FROM security_master ORDER BY market, symbol')
        self.assertTrue(observed == [(r[0], r[1], r[2], r[3], int(r[4]), *r[5:])
                                     for r in expected], 'Imported fields differ from source')
        self.assertTrue((self.backups / ('015-04-' + before_hash + '.sqlite')).is_file())
        after = self.files()
        report, _ = self.invoke('015-04', expected_hash=self.handoff_hash(output))
        self.assertEqual(report['status'], 'IDEMPOTENT_NOOP')
        self.assertEqual(self.files(), after)

    def test_missing_master_with_empty_source_still_commits_table_creation(self):
        self.mutate('DROP TABLE security_master')
        self.write_csv(self.master, SECURITY_COLUMNS, [])
        report, _ = self.invoke('015-04')
        self.assertEqual(report['status'], 'APPLIED')
        self.assertEqual(self.query('SELECT COUNT(*) FROM security_master'), [(0,)])
        after = self.files()
        report, _ = self.invoke('015-04')
        self.assertEqual(report['status'], 'IDEMPOTENT_NOOP')
        self.assertEqual(self.files(), after)

    def test_dry_run_validates_conflicts_and_invalid_sources(self):
        self.seed_master()
        rows = [list(row) for row in MASTER_ROWS]
        rows[0][2] = 'OTHER_PRIVATE_NAME'
        self.write_csv(self.master, SECURITY_COLUMNS, rows)
        before = self.files()
        self.assert_refused_unchanged('015-04', dry=True)
        self.assert_refused_unchanged('015-05', date='invalid', dry=True)
        self.assertEqual(self.files(), before)

    def test_all_steps_reject_sqlite_sidecars_without_modifying_them(self):
        for suffix in ('-wal', '-shm', '-journal'):
            sidecar = Path(str(self.db) + suffix)
            sidecar.write_bytes(b'synthetic unfinished journal')
            before = self.files()
            for step in SCRIPTS:
                # Do not query the fixture while a deliberately invalid sidecar exists.
                self.invoke(step, success=False, dry=True)
                self.assertEqual(self.files(), before)
            sidecar.unlink()

    def test_checkpointed_wal_database_without_sidecars_is_rejected(self):
        with closing(sqlite3.connect(self.db)) as db:
            self.assertEqual(db.execute('PRAGMA journal_mode=WAL').fetchone(), ('wal',))
        for suffix in ('-wal', '-shm'):
            self.assertFalse(Path(str(self.db) + suffix).exists())
        self.assertEqual(self.db.read_bytes()[18:20], b'\x02\x02')
        before = self.files()
        for step in SCRIPTS:
            for dry in (False, True):
                self.invoke(step, success=False, dry=dry)
                self.assertEqual(self.files(), before)

    def test_backup_destination_cannot_alias_database(self):
        for step in SCRIPTS:
            renamed = self.root / (step + '-' + sha(self.db) + '.sqlite')
            self.db.rename(renamed)
            self.db = renamed
            before = self.files()
            for dry in (False, True):
                self.assert_refused_unchanged(step, backup=self.root, dry=dry)
                self.assertEqual(self.files(), before)

    def test_corrupted_existing_snapshot_is_rejected_even_during_dry_run(self):
        self.backups.mkdir()
        for step in SCRIPTS:
            snapshot = self.backups / (step + '-' + sha(self.db) + '.sqlite')
            snapshot.write_bytes(b'preserve synthetic corrupted snapshot')
            before = self.files()
            for dry in (False, True):
                self.assert_refused_unchanged(step, dry=dry)
                self.assertEqual(self.files(), before)

    def test_unknown_cli_arguments_are_redacted(self):
        for step in SCRIPTS:
            self.assert_refused_unchanged(step, extra=('--PRIVATE_FAKE_SECURITY_NAME=999999',))

    def test_missing_sources_fail_without_creating_backup(self):
        for step in ('015-04', '015-05'):
            self.assert_refused_unchanged(step, source=self.root / 'missing.csv')
        self.assertFalse(self.backups.exists())

    def test_csv_headers_are_exact_without_missing_extra_duplicate_or_case_changes(self):
        for step, source, columns in (('015-04', self.master, SECURITY_COLUMNS),
                                       ('015-05', self.holdings, HOLDING_COLUMNS)):
            for invalid in (columns[:-1], (*columns, 'extra'),
                            ('Market', *columns[1:]), (*columns[:-1], columns[0])):
                with self.subTest(step=step, header_length=len(invalid)):
                    self.write_csv(source, invalid, [])
                    self.assert_refused_unchanged(step)
        self.assertFalse(self.backups.exists())

    def test_csv_requires_utf8_no_bom_and_rectangular_rows(self):
        for step, source, columns, rows in (
                ('015-04', self.master, SECURITY_COLUMNS, MASTER_ROWS),
                ('015-05', self.holdings, HOLDING_COLUMNS, HOLDING_ROWS)):
            for content in (b'\xef\xbb\xbf' + ','.join(columns).encode() + b'\n',
                            b'\xff\xfeinvalid', b'',
                            (','.join(columns) + '\n' + ','.join(rows[0][:-1]) + '\n').encode(),
                            (','.join(columns) + '\n' + ','.join([*rows[0], 'extra']) + '\n').encode()):
                with self.subTest(step=step):
                    source.write_bytes(content)
                    self.assert_refused_unchanged(step)

    def test_security_import_preserves_strings_duplicate_names_and_defaults(self):
        before = self.schema()
        self.invoke('015-04')
        self.assertEqual(self.schema(), before)
        self.assertEqual(self.query('SELECT symbol,name,currency,list_date FROM security_master ORDER BY market'),
                         [('999999', 'PRIVATE_FAKE_SECURITY_NAME', 'CNY', '2020-01-01'),
                          ('000099', 'PRIVATE_FAKE_SECURITY_NAME', 'CNY', None)])
        self.assertEqual(self.query('SELECT COUNT(*) FROM security_master'), [(2,)])
        self.assertEqual(self.query('SELECT * FROM unrelated_fixture'), [('preserve synthetic baseline',)])

    def test_security_column_order_is_irrelevant(self):
        order = (7, 6, 5, 4, 3, 2, 1, 0)
        self.write_csv(self.master, [SECURITY_COLUMNS[index] for index in order],
                       [[row[index] for index in order] for row in MASTER_ROWS])
        self.invoke('015-04')
        self.assertEqual(self.query('SELECT COUNT(*) FROM security_master'), [(2,)])

    def test_security_identical_reimport_is_byte_identical_noop(self):
        self.invoke('015-04')
        before = self.db.read_bytes()
        report, output = self.invoke('015-04')
        self.assertIn('IDEMPOTENT_NOOP', output)
        self.assertEqual(report['row_count'], len(MASTER_ROWS))
        self.assertEqual(report['skipped'], len(MASTER_ROWS))
        self.assertEqual(report['inserted'], 0)
        self.assertEqual(report['changed'], 0)
        self.assertEqual(self.db.read_bytes(), before)

    def test_security_all_market_and_status_enums_allow_same_symbol_across_markets(self):
        rows = [*MASTER_ROWS,
                ['BSE', '999999', 'PRIVATE_FAKE_SECURITY_NAME', 'STOCK', '1',
                 'CNY', '2024-02-29', 'DELISTED']]
        self.write_csv(self.master, SECURITY_COLUMNS, rows)
        report, _ = self.invoke('015-04')
        self.assertEqual(report['inserted'], 3)
        self.assertEqual(self.query('SELECT market,status FROM security_master ORDER BY market'),
                         [('BSE', 'DELISTED'), ('SSE', 'LISTED'), ('SZSE', 'SUSPENDED')])
        self.assertEqual(self.query('SELECT COUNT(*) FROM security_master WHERE symbol=?',
                                    ('999999',)), [(2,)])

    def test_security_duplicate_pk_fails_whole_batch(self):
        self.write_csv(self.master, SECURITY_COLUMNS, [*MASTER_ROWS, MASTER_ROWS[0]])
        self.assert_refused_unchanged('015-04')
        self.assertEqual(self.query('SELECT COUNT(*) FROM security_master'), [(0,)])

    def test_security_conflicting_fields_never_overwrite(self):
        self.seed_master()
        for index, value in ((2, 'CHANGED_PRIVATE_NAME'), (3, 'ETF'), (4, '101'),
                             (5, 'USD'), (6, '2021-01-01'), (7, 'SUSPENDED')):
            with self.subTest(field=SECURITY_COLUMNS[index]):
                rows = [list(row) for row in MASTER_ROWS]
                rows[0][index] = value
                self.write_csv(self.master, SECURITY_COLUMNS, rows)
                self.assert_refused_unchanged('015-04')

    def test_security_conflict_after_new_record_rolls_back_entire_batch(self):
        self.seed_master()
        rows = [list(row) for row in MASTER_ROWS]
        new_row = list(rows[0])
        new_row[1] = '999998'
        rows[0][2] = 'CHANGED_PRIVATE_NAME'
        self.write_csv(self.master, SECURITY_COLUMNS, [new_row, *rows])
        self.assert_refused_unchanged('015-04')
        self.assertEqual(self.query('SELECT COUNT(*) FROM security_master'), [(2,)])

    def test_security_full_snapshot_rejects_extra_existing_keys(self):
        self.seed_master()
        self.write_csv(self.master, SECURITY_COLUMNS, [MASTER_ROWS[0]])
        self.assert_refused_unchanged('015-04')

    def test_security_all_rows_are_validated_before_write(self):
        bad_fields = ((0, 'OTHER'), (0, ''), (3, 'BOND'), (3, ''),
                      (4, '0'), (4, '-1'), (4, '1.0'), (4, 'abc'), (4, ''),
                      (4, '9223372036854775808'),
                      (6, '2026-1-01'), (6, 'not-a-date'), (7, 'ACTIVE'), (7, ''))
        for index, value in bad_fields:
            with self.subTest(field=SECURITY_COLUMNS[index]):
                rows = [list(row) for row in MASTER_ROWS]
                rows[-1][index] = value
                self.write_csv(self.master, SECURITY_COLUMNS, rows)
                self.assert_refused_unchanged('015-04')
        self.assertFalse(self.backups.exists())

    def test_holding_creates_only_whitelisted_tables_and_exact_metadata(self):
        self.seed_master()
        before = self.schema()
        self.invoke('015-05')
        self.assert_schema_only_new_tables(before, ('holding_config', 'holding_config_meta'))
        self.assertEqual(self.query('SELECT * FROM holding_config ORDER BY market,symbol'),
                         [('SSE', '999999', 947031, 123.47), ('SZSE', '000099', 0, 0.0)])
        self.assertEqual(dict(self.query('SELECT key,value FROM holding_config_meta')),
                         {'as_of_date': self.as_of, 'version': '1.0', 'source_sha256': sha(self.holdings)})

    def test_holding_full_replace_deletes_closed_positions_and_extraneous_metadata(self):
        self.seed_holdings()
        self.mutate("INSERT INTO holding_config_meta VALUES ('stale','old')")
        rows = [['SSE', '999999', '7', '0.10']]
        self.write_csv(self.holdings, HOLDING_COLUMNS, rows)
        self.invoke('015-05', date='2026-10-17')
        self.assertEqual(self.query('SELECT * FROM holding_config'), [('SSE', '999999', 7, 0.1)])
        self.assertEqual(dict(self.query('SELECT key,value FROM holding_config_meta')),
                         {'as_of_date': '2026-10-17', 'version': '1.0', 'source_sha256': sha(self.holdings)})

    def test_holding_header_only_clears_portfolio_and_writes_meta(self):
        self.seed_holdings()
        self.write_csv(self.holdings, HOLDING_COLUMNS, [])
        self.invoke('015-05')
        self.assertEqual(self.query('SELECT COUNT(*) FROM holding_config'), [(0,)])
        self.assertEqual(dict(self.query('SELECT key,value FROM holding_config_meta')),
                         {'as_of_date': self.as_of, 'version': '1.0', 'source_sha256': sha(self.holdings)})
        _, output = self.invoke('015-05')
        self.assertIn('IDEMPOTENT_NOOP', output)

    def test_holding_dry_run_full_replace_preserves_existing_positions_meta_and_files(self):
        self.seed_holdings()
        self.write_csv(self.holdings, HOLDING_COLUMNS, [])
        before = self.files()
        report, _ = self.invoke('015-05', date='2026-10-17', dry=True)
        self.assertTrue(report['dry_run'])
        self.assertEqual(report['row_count'], 0)
        self.assertEqual(report['changed'], 0)
        self.assertEqual(report['planned_deleted'], len(HOLDING_ROWS))
        self.assertEqual(report['planned_inserted'], 0)
        self.assertEqual(self.files(), before)
        self.assertFalse(self.backups.exists())

    def test_holding_noop_compares_positions_and_date_not_csv_order(self):
        self.seed_holdings()
        before = self.db.read_bytes()
        old_sha = sha(self.holdings)
        self.write_csv(self.holdings, HOLDING_COLUMNS, list(reversed(HOLDING_ROWS)))
        self.assertNotEqual(sha(self.holdings), old_sha)
        _, output = self.invoke('015-05')
        self.assertIn('IDEMPOTENT_NOOP', output)
        self.assertEqual(self.db.read_bytes(), before)
        self.assertEqual(dict(self.query('SELECT key,value FROM holding_config_meta'))['source_sha256'], old_sha)

    def test_holding_removes_null_metadata_key_to_keep_exactly_three_rows(self):
        self.seed_holdings()
        self.mutate("INSERT INTO holding_config_meta VALUES (NULL,'old')")
        self.invoke('015-05')
        self.assertEqual(dict(self.query('SELECT key,value FROM holding_config_meta')),
                         {'as_of_date': self.as_of, 'version': '1.0', 'source_sha256': sha(self.holdings)})

    def test_holding_missing_table_is_created_when_metadata_would_otherwise_be_noop(self):
        self.seed_master()
        self.write_csv(self.holdings, HOLDING_COLUMNS, [])
        with closing(sqlite3.connect(self.db)) as db:
            db.execute(HOLDING_DDL.split(';')[1])
            db.executemany('INSERT INTO holding_config_meta VALUES (?,?)', [
                ('as_of_date', self.as_of), ('version', '1.0'), ('source_sha256', sha(self.holdings))])
            db.commit()
        _, output = self.invoke('015-05')
        self.assertNotIn('IDEMPOTENT_NOOP', output)
        self.assertEqual(self.query('SELECT COUNT(*) FROM holding_config'), [(0,)])

    def test_holding_changed_date_or_values_are_not_noops(self):
        self.seed_holdings()
        _, output = self.invoke('015-05', date='2026-10-17')
        self.assertNotIn('IDEMPOTENT_NOOP', output)
        self.assertEqual(dict(self.query('SELECT key,value FROM holding_config_meta'))['as_of_date'], '2026-10-17')
        rows = [list(row) for row in HOLDING_ROWS]
        rows[0][2] = '1'
        self.write_csv(self.holdings, HOLDING_COLUMNS, rows)
        _, output = self.invoke('015-05', date='2026-10-17')
        self.assertNotIn('IDEMPOTENT_NOOP', output)
        self.assertEqual(self.query("SELECT quantity FROM holding_config WHERE market='SSE'"), [(1,)])

    def test_holding_avg_cost_alone_triggers_replace(self):
        self.seed_holdings()
        rows = [list(row) for row in HOLDING_ROWS]
        rows[0][3] = '0.01'
        self.write_csv(self.holdings, HOLDING_COLUMNS, rows)
        report, output = self.invoke('015-05')
        self.assertNotIn('IDEMPOTENT_NOOP', output)
        self.assertGreater(report['changed'], 0)
        self.assertEqual(self.query("SELECT avg_cost FROM holding_config WHERE market='SSE'"), [(0.01,)])
        self.assertEqual(dict(self.query('SELECT key,value FROM holding_config_meta')),
                         {'as_of_date': self.as_of, 'version': '1.0', 'source_sha256': sha(self.holdings)})

    def test_holding_missing_delisted_or_wrong_market_symbol_fails(self):
        self.seed_holdings()
        for market, symbol in (('SSE', '999998'), ('BSE', '999999'), ('', '999999'), ('SSE', '')):
            self.write_csv(self.holdings, HOLDING_COLUMNS, [[market, symbol, '1', '1.00']])
            self.assert_refused_unchanged('015-05')
        self.write_csv(self.holdings, HOLDING_COLUMNS, HOLDING_ROWS)
        self.mutate("UPDATE security_master SET status='DELISTED' WHERE market='SSE'")
        self.assert_refused_unchanged('015-05')

    def test_holding_duplicate_pk_invalid_quantities_and_costs_preserve_old_data(self):
        self.seed_holdings()
        self.write_csv(self.holdings, HOLDING_COLUMNS, [*HOLDING_ROWS, HOLDING_ROWS[0]])
        self.assert_refused_unchanged('015-05')
        invalid = ((2, '-1'), (2, '1.5'), (2, 'abc'), (2, ''), (2, '1e2'),
                   (2, '9223372036854775808'), (3, '-0.01'), (3, '1.001'),
                   (3, 'nan'), (3, 'inf'), (3, ''), (3, 'abc'), (3, '9' * 400))
        for index, value in invalid:
            with self.subTest(field=HOLDING_COLUMNS[index]):
                rows = [list(row) for row in HOLDING_ROWS]
                rows[-1][index] = value
                self.write_csv(self.holdings, HOLDING_COLUMNS, rows)
                self.assert_refused_unchanged('015-05')

    def test_holding_invalid_dates_and_bad_row_leave_missing_tables_absent(self):
        self.seed_master()
        for date in ('2026-1-01', '2026-02-30', 'not-a-date', ''):
            self.assert_refused_unchanged('015-05', date=date)
        self.write_csv(self.holdings, HOLDING_COLUMNS, [['SSE', '999999', '-1', '0.00']])
        self.assert_refused_unchanged('015-05')
        tables = self.query("SELECT name FROM sqlite_master WHERE type='table'")
        self.assertNotIn(('holding_config',), tables)
        self.assertNotIn(('holding_config_meta',), tables)

    def test_holding_column_order_is_irrelevant(self):
        self.seed_master()
        self.write_csv(self.holdings, reversed(HOLDING_COLUMNS),
                       [list(reversed(row)) for row in HOLDING_ROWS])
        self.invoke('015-05')
        self.assertEqual(self.query('SELECT COUNT(*) FROM holding_config'), [(2,)])

    def test_dashboard_projection_is_exact_deterministic_and_whitelisted(self):
        rows = [['SZSE', '000099', '3', '0.20'], ['SSE', '999999', '3', '0.10']]
        self.seed_holdings(rows=rows)
        before = self.schema()
        self.invoke('015-06')
        self.assert_schema_only_new_tables(before, ('dashboard_position_snapshot', 'dashboard_overview'))
        batch = 'B20261016_' + sha(self.holdings)[:12]
        self.assertEqual(self.query('SELECT * FROM dashboard_overview'),
                         [(batch, self.as_of, 2, 0.9, 'CNY')])
        self.assertEqual(self.query('SELECT batch_id,market,symbol,name,quantity,avg_cost,cost_value,as_of_date '
                                    'FROM dashboard_position_snapshot ORDER BY rowid'),
                         [(batch, 'SSE', '999999', 'PRIVATE_FAKE_SECURITY_NAME', 3, 0.1, 0.3, self.as_of),
                          (batch, 'SZSE', '000099', 'PRIVATE_FAKE_SECURITY_NAME', 3, 0.2, 0.6, self.as_of)])
        self.assertEqual(self.query('SELECT ROUND(SUM(cost_value),2) FROM dashboard_position_snapshot'), [(0.9,)])

    def test_dashboard_identical_batch_is_byte_identical_noop(self):
        self.seed_holdings()
        self.invoke('015-06')
        before = self.db.read_bytes()
        _, output = self.invoke('015-06')
        self.assertIn('IDEMPOTENT_NOOP', output)
        self.assertEqual(self.db.read_bytes(), before)
        self.assertEqual(self.query('SELECT COUNT(*) FROM dashboard_overview'), [(1,)])

    def test_dashboard_appends_batches_and_latest_selection_uses_date_then_id(self):
        self.seed_holdings()
        self.invoke('015-06')
        old_snapshot = self.query('SELECT * FROM dashboard_position_snapshot ORDER BY rowid')
        old_overview = self.query('SELECT * FROM dashboard_overview')
        self.mutate("UPDATE holding_config_meta SET value='2026-10-17' WHERE key='as_of_date'")
        self.invoke('015-06')
        all_snapshot = self.query('SELECT * FROM dashboard_position_snapshot ORDER BY rowid')
        self.assertEqual(all_snapshot[:len(old_snapshot)], old_snapshot)
        self.assertIn(old_overview[0], self.query('SELECT * FROM dashboard_overview'))
        self.assertEqual(len(all_snapshot), 4)
        self.assertEqual(self.query('SELECT as_of_date FROM dashboard_overview '
                                    'ORDER BY as_of_date DESC,batch_id DESC LIMIT 1'), [('2026-10-17',)])
        self.mutate("UPDATE holding_config_meta SET value=? WHERE key='source_sha256'", ('f' * 64,))
        self.invoke('015-06')
        self.assertEqual(self.query('SELECT COUNT(*) FROM dashboard_overview'), [(3,)])
        self.assertEqual(self.query('SELECT batch_id FROM dashboard_overview '
                                    'ORDER BY as_of_date DESC,batch_id DESC LIMIT 1'), [('B20261017_ffffffffffff',)])

    def test_dashboard_empty_holdings_produces_zero_overview_and_no_snapshots(self):
        self.seed_holdings(rows=[])
        self.invoke('015-06')
        self.assertEqual(self.query('SELECT total_positions,total_cost_value,currency FROM dashboard_overview'),
                         [(0, 0.0, 'CNY')])
        self.assertEqual(self.query('SELECT COUNT(*) FROM dashboard_position_snapshot'), [(0,)])
        _, output = self.invoke('015-06')
        self.assertIn('IDEMPOTENT_NOOP', output)

    def test_dashboard_missing_snapshot_table_is_created_for_existing_empty_batch(self):
        self.seed_holdings(rows=[])
        with closing(sqlite3.connect(self.db)) as db:
            db.execute(DASHBOARD_DDL.split(';')[1])
            db.execute('INSERT INTO dashboard_overview VALUES (?,?,?,?,?)',
                       ('B20261016_' + sha(self.holdings)[:12], self.as_of, 0, 0, 'CNY'))
            db.commit()
        _, output = self.invoke('015-06')
        self.assertNotIn('IDEMPOTENT_NOOP', output)
        self.assertEqual(self.query('SELECT COUNT(*) FROM dashboard_position_snapshot'), [(0,)])
        self.assertEqual(self.query('SELECT COUNT(*) FROM dashboard_overview'), [(1,)])

    def test_dashboard_existing_batch_verification_detects_bad_totals_and_counts(self):
        self.seed_holdings(rows=[['SSE', '999999', '3', '0.10']])
        self.invoke('015-06')
        self.mutate('UPDATE dashboard_overview SET total_cost_value=1')
        self.assert_refused_unchanged('015-06')
        self.mutate('UPDATE dashboard_overview SET total_cost_value=0.3')
        self.mutate('DELETE FROM dashboard_position_snapshot')
        self.assert_refused_unchanged('015-06')

    def test_dashboard_refuses_cent_precision_loss_and_leaves_schema_unchanged(self):
        self.seed_holdings(rows=[['SSE', '999999', 9999999, 999999999999.99]])
        self.assert_refused_unchanged('015-06')

    def test_dashboard_missing_master_join_fails_even_for_existing_batch(self):
        self.seed_holdings()
        self.mutate("DELETE FROM security_master WHERE market='SSE'")
        self.assert_refused_unchanged('015-06')
        self.mutate('INSERT INTO security_master VALUES (?,?,?,?,?,?,?,?)', MASTER_ROWS[0])
        self.invoke('015-06')
        self.mutate("DELETE FROM security_master WHERE market='SSE'")
        self.assert_refused_unchanged('015-06')

    def test_dashboard_missing_or_invalid_metadata_refuses_without_ddl(self):
        self.seed_holdings()
        original = dict(self.query('SELECT key,value FROM holding_config_meta'))
        for key in ('as_of_date', 'source_sha256'):
            self.mutate('DELETE FROM holding_config_meta WHERE key=?', (key,))
            self.assert_refused_unchanged('015-06')
            self.mutate('INSERT INTO holding_config_meta VALUES (?,?)', (key, original[key]))
        for key, invalid in (('as_of_date', '2026-1-01'), ('source_sha256', 'invalid')):
            self.mutate('UPDATE holding_config_meta SET value=? WHERE key=?', (invalid, key))
            self.assert_refused_unchanged('015-06')
            self.mutate('UPDATE holding_config_meta SET value=? WHERE key=?', (original[key], key))

    def test_missing_required_source_tables_fail_before_creating_target_tables(self):
        self.seed_holdings()
        baseline = self.db.read_bytes()
        for step, table in (('015-05', 'security_master'),
                            ('015-06', 'security_master'),
                            ('015-06', 'holding_config'),
                            ('015-06', 'holding_config_meta')):
            with self.subTest(step=step, missing_table=table):
                self.db.write_bytes(baseline)
                self.mutate('DROP TABLE ' + table)
                before = self.files()
                for dry in (False, True):
                    self.assert_refused_unchanged(step, dry=dry)
                    self.assertEqual(self.files(), before)

    def test_dashboard_rejects_orphaned_snapshot_batch_and_rolls_back(self):
        self.seed_holdings()
        with closing(sqlite3.connect(self.db)) as db:
            db.executescript(DASHBOARD_DDL)
            db.execute('INSERT INTO dashboard_position_snapshot VALUES (?,?,?,?,?,?,?,?)',
                       ('B20261016_' + sha(self.holdings)[:12], 'SSE', '999999',
                        'PRIVATE_FAKE_SECURITY_NAME', 1, 1, 1, self.as_of))
            db.commit()
        self.assert_refused_unchanged('015-06')
        self.assertEqual(self.query('SELECT COUNT(*) FROM dashboard_overview'), [(0,)])

    def test_existing_wrong_target_schema_is_rejected_without_modification(self):
        self.seed_master()
        self.mutate('CREATE TABLE holding_config(market TEXT,symbol TEXT)')
        self.assert_refused_unchanged('015-05')
        self.mutate('DROP TABLE holding_config')
        with closing(sqlite3.connect(self.db)) as db:
            db.executescript(HOLDING_DDL)
            db.executemany('INSERT INTO holding_config_meta VALUES (?,?)', [
                ('as_of_date', self.as_of), ('version', '1.0'), ('source_sha256', sha(self.holdings))])
            db.execute('CREATE TABLE dashboard_overview(batch_id TEXT)')
            db.commit()
        self.assert_refused_unchanged('015-06')

    def test_security_noncanonical_uniqueness_or_missing_check_is_rejected(self):
        for ddl in (SECURITY_DDL.replace('name TEXT NOT NULL', 'name TEXT NOT NULL UNIQUE'),
                    SECURITY_DDL.replace(" CHECK (market IN ('SSE', 'SZSE', 'BSE'))", '')):
            self.mutate('DROP TABLE security_master')
            self.mutate(ddl)
            self.assert_refused_unchanged('015-04')

    def test_commented_check_clauses_do_not_satisfy_security_schema_contract(self):
        check = "CHECK (market IN ('SSE', 'SZSE', 'BSE'))"
        for replacement in ('/* ' + check + ' */', '-- ' + check + '\n'):
            with self.subTest(comment_style=replacement[:2]):
                self.mutate('DROP TABLE security_master')
                self.mutate(SECURITY_DDL.replace(check, replacement))
                before = self.files()
                for dry in (False, True):
                    self.assert_refused_unchanged('015-04', dry=dry)
                    self.assertEqual(self.files(), before)

    def test_trigger_cannot_write_outside_step_whitelist(self):
        self.mutate("CREATE TRIGGER unsafe_master AFTER INSERT ON security_master BEGIN "
                    "DELETE FROM unrelated_fixture; END")
        self.assert_refused_unchanged('015-04')
        self.mutate('DROP TRIGGER unsafe_master')
        self.seed_holdings()
        self.mutate("CREATE TRIGGER unsafe_holding AFTER DELETE ON holding_config BEGIN "
                    "DELETE FROM unrelated_fixture; END")
        self.write_csv(self.holdings, HOLDING_COLUMNS, [])
        self.assert_refused_unchanged('015-05')
        self.mutate('DROP TRIGGER unsafe_holding')
        with closing(sqlite3.connect(self.db)) as db:
            db.executescript(DASHBOARD_DDL)
            db.execute("CREATE TRIGGER unsafe_dashboard AFTER INSERT ON dashboard_overview BEGIN "
                       "DELETE FROM unrelated_fixture; END")
            db.commit()
        self.assert_refused_unchanged('015-06')

    def test_trigger_cannot_write_even_to_otherwise_allowed_table(self):
        self.seed_holdings()
        self.mutate("CREATE TRIGGER unsafe_meta AFTER INSERT ON holding_config BEGIN "
                    "UPDATE holding_config_meta SET value='unexpected' WHERE key='version'; END")
        self.write_csv(self.holdings, HOLDING_COLUMNS, [['SSE', '999999', '1', '0.10']])
        self.assert_refused_unchanged('015-05')

    def test_sql_authorizer_denies_ddl_and_writes_outside_each_step_contract(self):
        safety = importlib.import_module('tools.phase_c_safety')
        contracts = importlib.import_module('tools.phase_c_contracts')
        common_forbidden = (
            'CREATE TABLE unexpected(value TEXT)',
            'DROP TABLE security_master',
            'ALTER TABLE security_master ADD COLUMN unexpected TEXT',
            'CREATE INDEX unexpected ON security_master(symbol)',
            'CREATE VIEW unexpected AS SELECT symbol FROM security_master',
            "CREATE TRIGGER unexpected AFTER INSERT ON security_master BEGIN SELECT 1; END",
            "ATTACH DATABASE ':memory:' AS unexpected",
            'PRAGMA writable_schema=ON',
            'SELECT random()',
            'SELECT CURRENT_TIMESTAMP',
        )
        by_step = {
            '015-04': ('DELETE FROM security_master',
                       "UPDATE security_master SET name='changed'",
                       'DELETE FROM holding_config'),
            '015-05': ('DELETE FROM security_master',
                       'UPDATE holding_config SET quantity=0',
                       'DELETE FROM dashboard_overview'),
            '015-06': ('DELETE FROM holding_config',
                       'DELETE FROM dashboard_position_snapshot',
                       'DELETE FROM dashboard_overview',
                       'UPDATE dashboard_overview SET total_positions=0'),
        }
        for step in SCRIPTS:
            with closing(sqlite3.connect(':memory:', factory=safety.GuardedConnection,
                                        cached_statements=0)) as db:
                db.execute(SECURITY_DDL)
                for sql in HOLDING_DDL.split(';') + DASHBOARD_DDL.split(';'):
                    if sql.strip():
                        db.execute(sql)
                db.install_policy(step, contracts)
                for sql in (*common_forbidden, *by_step[step]):
                    with self.subTest(step=step, operation=sql.split()[0]):
                        with self.assertRaises((sqlite3.DatabaseError, safety.GateError)):
                            db.execute(sql)
                with self.assertRaises(safety.GateError):
                    db.executescript('SELECT 1;')

    def test_only_verbatim_whitelisted_creates_can_create_tables(self):
        safety = importlib.import_module('tools.phase_c_safety')
        contracts = importlib.import_module('tools.phase_c_contracts')
        for step in SCRIPTS:
            with closing(sqlite3.connect(':memory:', factory=safety.GuardedConnection,
                                        cached_statements=0)) as db:
                db.install_policy(step, contracts)
                for sql in contracts.DDL_BY_STEP[step]:
                    with self.assertRaises(sqlite3.DatabaseError):
                        db.execute(sql + ' ')
                    db.execute(sql)
                expected_names = set(contracts.WRITE_BY_STEP[step])
                self.assertEqual({row[0] for row in db.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'")}, expected_names)
        with closing(sqlite3.connect(':memory:', factory=safety.GuardedConnection,
                                    cached_statements=0)) as db:
            db.install_policy('015-04', contracts)
            with self.assertRaises(sqlite3.DatabaseError):
                db.execute(SECURITY_DDL.rstrip('\n'))

    def test_all_create_texts_match_independent_locked_hashes(self):
        safety = importlib.import_module('tools.phase_c_safety')
        for step, statements in DDL_BY_STEP.items():
            self.assertEqual(tuple(hashlib.sha256(sql.encode('utf-8')).hexdigest()
                                   for sql in statements), safety.CREATE_SHA256_BY_STEP[step])
        self.assertEqual(len(SECURITY_DDL.encode('utf-8')), 458)
        self.assertTrue(SECURITY_DDL.endswith(';\n'))

    def test_canonical_create_on_scratch_has_exact_contract_signature(self):
        safety = importlib.import_module('tools.phase_c_safety')
        contracts = importlib.import_module('tools.phase_c_contracts')
        scratch = self.root / 'canonical-only.sqlite'
        with closing(sqlite3.connect(scratch, factory=safety.GuardedConnection,
                                    cached_statements=0)) as db:
            db.install_policy('015-04', contracts)
            db.execute(SECURITY_DDL)
            columns = db.execute('PRAGMA table_xinfo(security_master)').fetchall()
            self.assertEqual(tuple(row[1] for row in columns), SECURITY_COLUMNS)
            self.assertEqual([(row[2], row[3], row[4], row[5], row[6]) for row in columns], [
                ('TEXT', 1, None, 1, 0), ('TEXT', 1, None, 2, 0),
                ('TEXT', 1, None, 0, 0), ('TEXT', 1, None, 0, 0),
                ('INTEGER', 1, None, 0, 0), ('TEXT', 1, "'CNY'", 0, 0),
                ('TEXT', 0, None, 0, 0), ('TEXT', 1, None, 0, 0),
            ])
            contracts._schema(db, 'security_master')

    def test_single_character_tampered_whitelisted_creates_are_hash_rejected(self):
        safety = importlib.import_module('tools.phase_c_safety')
        contracts = importlib.import_module('tools.phase_c_contracts')
        # A whitespace substitution is still valid SQL and would pass schema
        # normalization. Even replacing the exact allowlist must not allow it.
        for step, statements in DDL_BY_STEP.items():
            for index, statement in enumerate(statements):
                with self.subTest(step=step, create_index=index):
                    tampered = statement.replace(' ', '\t', 1)
                    altered = statements[:index] + (tampered,) + statements[index + 1:]
                    with patch.dict(contracts.DDL_BY_STEP, {step: altered}), \
                            closing(sqlite3.connect(self.root / 'tamper.sqlite',
                                                   factory=safety.GuardedConnection,
                                                   cached_statements=0)) as db:
                        db.install_policy(step, contracts)
                        traced = []
                        db.set_trace_callback(traced.append)
                        with self.assertRaisesRegex(safety.GateError, '^CREATE_SHA256_MISMATCH$'):
                            db.execute(tampered)
                        self.assertEqual(traced, [])
                        self.assertEqual(db.execute('SELECT name FROM sqlite_master').fetchall(), [])

    def test_tampered_canonical_create_fails_preflight_without_scratch_changes(self):
        contracts = importlib.import_module('tools.phase_c_contracts')
        self.mutate('DROP TABLE security_master')
        before = self.files()
        with patch.dict(contracts.DDL_BY_STEP, {'015-04': (SECURITY_DDL[:-1],)}):
            for dry in (True, False):
                report, _ = self.invoke_direct('015-04', success=False, dry=dry)
                self.assertEqual(report['error'], 'CREATE_SHA256_MISMATCH')
                self.assertEqual(self.files(), before)

    def test_schema_verifier_rejects_tampered_create_before_execution(self):
        safety = importlib.import_module('tools.phase_c_safety')
        with self.assertRaisesRegex(safety.GateError, '^CREATE_SHA256_MISMATCH$'):
            safety.verify_schema((), (), (SECURITY_DDL[:-1],), '015-04')

    def test_live_transaction_failure_restores_exact_bytes_including_new_tables(self):
        self.mutate('DROP TABLE security_master')
        safety = importlib.import_module('tools.phase_c_safety')
        contracts = importlib.import_module('tools.phase_c_contracts')
        original = contracts.execute
        for step in SCRIPTS:
            before = self.db.read_bytes()
            before_schema = self.schema()
            calls = []

            def fail_live(*args):
                result = original(*args)
                calls.append(args[0])
                if len(calls) == 2:
                    raise safety.GateError('SYNTHETIC_POST_WRITE_FAILURE')
                return result

            with patch.object(contracts, 'execute', side_effect=fail_live):
                self.invoke_direct(step, success=False)
            self.assertEqual(calls, [step, step], 'Failure must occur after live DDL/DML')
            self.assertEqual(self.db.read_bytes(), before)
            self.assertEqual(self.schema(), before_schema)
            self.assertTrue(any(path.read_bytes() == before for path in self.backups.iterdir()))
            self.invoke(step)

    def test_database_hash_change_after_preflight_refuses_without_snapshot_or_write(self):
        safety = importlib.import_module('tools.phase_c_safety')
        original = safety.memory_preflight
        baseline = self.db.read_bytes()
        for step in SCRIPTS:
            with self.subTest(step=step):
                self.db.write_bytes(baseline)
                if step == '015-05':
                    self.seed_master()
                elif step == '015-06':
                    self.seed_holdings()
                external_state = []

                def mutate_after_preflight(*args):
                    result = original(*args)
                    self.mutate("INSERT INTO unrelated_fixture VALUES ('synthetic concurrent change')")
                    external_state.append(self.db.read_bytes())
                    return result

                with patch.object(safety, 'memory_preflight', side_effect=mutate_after_preflight):
                    self.invoke_direct(step, success=False)
                self.assertEqual(len(external_state), 1)
                self.assertEqual(self.db.read_bytes(), external_state[0])
                self.assertFalse(self.backups.exists())

    def test_noop_hash_change_after_preflight_never_emits_success_handoff(self):
        safety = importlib.import_module('tools.phase_c_safety')
        original = safety.memory_preflight
        for step in SCRIPTS:
            self.invoke(step)
        for step in SCRIPTS:
            with self.subTest(step=step):
                external_state = []

                def mutate_after_preflight(*args):
                    result = original(*args)
                    self.assertEqual(result['status'], 'IDEMPOTENT_NOOP')
                    self.mutate("INSERT INTO unrelated_fixture VALUES ('synthetic no-op race')")
                    external_state.append(self.files())
                    return result

                with patch.object(safety, 'memory_preflight', side_effect=mutate_after_preflight):
                    report, _ = self.invoke_direct(step, success=False)
                self.assertEqual(report['error'], 'DATABASE_HASH_MISMATCH')
                self.assertEqual(len(external_state), 1)
                self.assertEqual(self.files(), external_state[0])

    def test_live_holding_replace_failure_restores_old_positions_and_metadata(self):
        safety = importlib.import_module('tools.phase_c_safety')
        contracts = importlib.import_module('tools.phase_c_contracts')
        self.seed_holdings()
        self.write_csv(self.holdings, HOLDING_COLUMNS, [])
        before = self.db.read_bytes()
        original = contracts.execute
        calls = []

        def fail_live(*args):
            result = original(*args)
            calls.append(args[0])
            if len(calls) == 2:
                raise safety.GateError('SYNTHETIC_POST_REPLACE_FAILURE')
            return result

        with patch.object(contracts, 'execute', side_effect=fail_live):
            self.invoke_direct('015-05', success=False)
        self.assertEqual(len(calls), 2)
        self.assertEqual(self.db.read_bytes(), before)

    def test_schema_fingerprint_detects_unapproved_live_schema_change_and_rolls_back(self):
        contracts = importlib.import_module('tools.phase_c_contracts')
        original = contracts.execute
        calls = []
        before = self.db.read_bytes()

        def change_live_schema(step, db, args, prepared):
            result = original(step, db, args, prepared)
            calls.append(step)
            if len(calls) == 2:
                # Fault injection bypasses the first guard to exercise the independent
                # post-write schema fingerprint guard before commit.
                db.set_authorizer(None)
                db.execute('CREATE TABLE unapproved_fixture(value TEXT)')
            return result

        with patch.object(contracts, 'execute', side_effect=change_live_schema):
            self.invoke_direct('015-04', success=False)
        self.assertEqual(len(calls), 2)
        self.assertEqual(self.db.read_bytes(), before)

    def test_scripts_perform_no_network_or_llm_calls(self):
        with patch('socket.socket.connect', side_effect=AssertionError('network forbidden')), \
                patch('socket.create_connection', side_effect=AssertionError('network forbidden')), \
                patch('dashboard.factory.create_runtime', side_effect=AssertionError('runtime forbidden')), \
                patch('dashboard.runtime.DashboardRuntime.generate', side_effect=AssertionError('LLM forbidden')):
            for step in SCRIPTS:
                self.invoke_direct(step)

    def test_prewrite_snapshot_is_exact_and_restorable_for_every_step(self):
        for step in SCRIPTS:
            before = self.db.read_bytes()
            previous_files = set(self.backups.rglob('*')) if self.backups.exists() else set()
            self.invoke(step)
            added = [path for path in self.backups.rglob('*') if path.is_file() and path not in previous_files]
            snapshots = [path for path in added if path.read_bytes() == before]
            self.assertEqual(len(snapshots), 1, 'Each write needs one exact prewrite SQLite snapshot')
            restored = self.root / ('restored-' + step + '.sqlite')
            shutil.copyfile(snapshots[0], restored)
            self.assertEqual(restored.read_bytes(), before)
            with closing(sqlite3.connect(restored)) as db:
                self.assertEqual(db.execute('PRAGMA integrity_check').fetchone(), ('ok',))
                self.assertEqual(db.execute('SELECT * FROM unrelated_fixture').fetchall(),
                                 [('preserve synthetic baseline',)])


if __name__ == '__main__':
    unittest.main()
