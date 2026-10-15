"""B01 regression coverage using only synthetic databases in temporary directories."""
import ast
from collections import Counter
import os
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from dashboard.runtime import DashboardRuntime
from evidence import ArchiveWriter, HumanConfirm
from persistence.connection import configure, is_temporary_database, open_database, transaction
from persistence.contracts import PersistenceError
from persistence.migration import apply_migration
from persistence.repository import SQLitePersistenceRepository
from test_analysis_runner import FakeModelExecutor
from test_dashboard_runtime import confirm_items
from test_indicator_calculator import daily_evidence
from test_human_confirmation import STAMP, source_records
from test_persistence_schema_v1_1 import fixture
from windows_compat import symlink_privilege_missing, use_simulated_directory_fsync


class RealDatabaseWriteGateTests(unittest.TestCase):
    def setUp(self):
        environment = patch.dict(os.environ)
        environment.start()
        self.addCleanup(environment.stop)
        for name in ('REAL_DB_PROTECTED_PATHS', 'REAL_DB_WRITE', 'APPLY_REAL_DB'):
            os.environ.pop(name, None)

        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.db_path = self.root / 'runtime.sqlite'
        use_simulated_directory_fsync(self)
        seed = configure(sqlite3.connect(self.db_path, isolation_level=None))
        try:
            apply_migration(seed)
        finally:
            seed.close()
        self.db = open_database(self.db_path)
        self.addCleanup(self.db.close)
        self.writer = ArchiveWriter(self.root / 'archive')
        self.record, _ = daily_evidence(self.writer, self.db)
        self.runtime = DashboardRuntime(self.db, archive_writer=self.writer,
                                        executor=FakeModelExecutor())
        self.runtime.prepare(self.record.evidence_id)
        # Exercise real-path policy without ever opening a real database. Fixture
        # creation above still uses the genuine temporary-database exemption.
        non_temporary = patch('persistence.connection.is_temporary_database', return_value=False)
        non_temporary.start()
        self.addCleanup(non_temporary.stop)
        os.environ['REAL_DB_PROTECTED_PATHS'] = str(self.root / 'other.sqlite')

    def protect(self):
        os.environ['REAL_DB_PROTECTED_PATHS'] = str(self.db_path)

    def assert_denied_without_database_changes(self, operation, error_code='REAL_DB_WRITE_NOT_AUTHORIZED',
                                              *, db=None, db_path=None):
        db, db_path = (self.db if db is None else db), (self.db_path if db_path is None else db_path)
        contents = tuple(db.iterdump())
        file_bytes = db_path.read_bytes()
        changes = db.total_changes
        with self.assertRaisesRegex(PersistenceError, '^' + error_code):
            operation()
        self.assertEqual(tuple(db.iterdump()), contents)
        self.assertEqual(db_path.read_bytes(), file_bytes)
        self.assertEqual(db.total_changes, changes)
        self.assertFalse(db.in_transaction)

    def generate(self):
        self.runtime.generate(actor_id='test-reviewer')

    def first_item(self):
        return next(item for item in self.runtime.flow.items if item.target != 'FINAL_REPORT')

    def assert_complete_archive(self):
        self.generate()
        confirm_items(self.runtime)
        self.runtime.prepare_archive()
        self.runtime.confirm_and_archive(actor_id='test-reviewer')
        self.assertEqual(self.runtime.status, 'ARCHIVED')
        self.assertEqual(self.db.execute('SELECT status FROM analysis_tasks').fetchone()[0], 'ARCHIVED')
        self.assertEqual(self.db.execute('SELECT count(*) FROM analysis_reports').fetchone()[0], 1)

    def test_protected_database_rejects_generation_without_authorization(self):
        self.protect()
        self.assert_denied_without_database_changes(self.generate)
        self.assertIsNone(self.runtime.flow)

    def test_protected_database_rejects_confirmation_without_authorization(self):
        self.generate()
        item = self.first_item()
        self.protect()
        self.assert_denied_without_database_changes(
            lambda: self.runtime.decide(*item.key, actor_id='test-reviewer'))
        self.assertIs(self.runtime.flow.current(*item.key), HumanConfirm.PENDING)

    def test_protected_database_rejects_archive_preparation_without_authorization(self):
        self.generate()
        confirm_items(self.runtime)
        self.protect()
        self.assert_denied_without_database_changes(self.runtime.prepare_archive)
        self.assertIsNone(self.runtime.prepared)

    def test_protected_database_rejects_final_archive_without_authorization(self):
        self.generate()
        confirm_items(self.runtime)
        self.runtime.prepare_archive()
        self.protect()
        self.assert_denied_without_database_changes(
            lambda: self.runtime.confirm_and_archive(actor_id='test-reviewer'))
        self.assertIsNone(self.runtime.final)
        self.assertEqual(self.runtime.status, 'READY_FOR_REVIEW')

    def test_protected_database_rejects_task_status_updates_without_authorization(self):
        self.generate()
        self.protect()
        self.assert_denied_without_database_changes(lambda: self.runtime.repo.update_task_status(
            self.runtime.flow.run_evidence.analysis_run_id, 'FAILED'))

    def test_real_db_write_yes_allows_generation_confirmation_and_archive(self):
        self.protect()
        os.environ['REAL_DB_WRITE'] = 'YES'
        self.assert_complete_archive()

    def test_all_public_repository_writes_reject_missing_or_migration_only_authorization(self):
        db_path = self.root / 'repository.sqlite'
        db = configure(sqlite3.connect(db_path, isolation_level=None))
        self.addCleanup(db.close)
        with patch('persistence.connection.is_temporary_database', side_effect=is_temporary_database):
            apply_migration(db)
        execution, flow, prepared, final, rows = fixture()
        repo = SQLitePersistenceRepository(db, allow_synthetic_evidence=True)

        def persist_run(execution, flow, rows):
            return repo.persist_analysis_run(
                flow.draft, flow.run_evidence, execution.prompt_envelope,
                task_date=rows[0][1]['task_date'], created_by='synthetic-reviewer',
                raw_model_output=rows[0][1]['raw_model_output'], evidence_created_at=STAMP,
                evidence_records=source_records(flow.run_evidence))

        self.assertEqual(persist_run(execution, flow, rows), 'PERSISTED')
        new_execution, new_flow, _, _, new_rows = fixture()
        self.assertNotEqual(new_flow.run_evidence.analysis_run_id, flow.run_evidence.analysis_run_id)
        operations = {
            'persist_analysis_run': lambda: persist_run(new_execution, new_flow, new_rows),
            'persist_confirmation_progress': lambda: repo.persist_confirmation_progress(flow, prepared=prepared),
            'persist_finalization': lambda: repo.persist_finalization(
                flow, prepared, final, evidence_records=source_records(flow.run_evidence),
                evidence_created_at=STAMP),
            'update_task_status': lambda: repo.update_task_status(flow.run_evidence.analysis_run_id, 'FAILED'),
        }
        # A new public entry point must acquire explicit coverage here.
        self.assertEqual(set(operations), {name for name, value in vars(SQLitePersistenceRepository).items()
                                          if not name.startswith('_') and callable(value)})
        os.environ['REAL_DB_PROTECTED_PATHS'] = str(db_path)
        for authorization in ({}, {'APPLY_REAL_DB': 'YES'}):
            with patch.dict(os.environ, authorization):
                for name, operation in operations.items():
                    with self.subTest(method=name, authorization=authorization):
                        self.assert_denied_without_database_changes(operation, db=db, db_path=db_path)

    def test_unconfigured_non_temporary_database_denies_transaction_before_callback(self):
        for value in (None, '', ' , , '):
            with self.subTest(configuration=value), patch.dict(os.environ):
                if value is None:
                    os.environ.pop('REAL_DB_PROTECTED_PATHS', None)
                else:
                    os.environ['REAL_DB_PROTECTED_PATHS'] = value
                called = []

                def write():
                    called.append(True)
                    self.db.execute('CREATE TABLE forbidden_write (id INTEGER)')

                self.assert_denied_without_database_changes(
                    lambda: transaction(self.db, write), 'REAL_DB_PROTECTED_PATHS_UNCONFIGURED')
                self.assertEqual(called, [])

    def test_authorization_cannot_override_unconfigured_runtime_paths(self):
        os.environ.pop('REAL_DB_PROTECTED_PATHS')
        os.environ['REAL_DB_WRITE'] = os.environ['APPLY_REAL_DB'] = 'YES'
        self.assert_denied_without_database_changes(self.generate, 'REAL_DB_PROTECTED_PATHS_UNCONFIGURED')

    def test_temporary_database_is_exempt_even_when_unconfigured_or_protected(self):
        with patch('persistence.connection.is_temporary_database', side_effect=is_temporary_database):
            self.assertTrue(is_temporary_database(self.db))
            os.environ.pop('REAL_DB_PROTECTED_PATHS')
            transaction(self.db, lambda: self.db.execute('CREATE TABLE synthetic_marker (value INTEGER)'))
            self.protect()
            transaction(self.db, lambda: self.db.execute('INSERT INTO synthetic_marker VALUES (1)'))
            self.assertEqual(self.db.execute('SELECT count(*) FROM synthetic_marker').fetchone()[0], 1)

    def test_non_temporary_migration_requires_its_own_authorization(self):
        migration_path = self.root / 'migration.sqlite'
        db = configure(sqlite3.connect(migration_path, isolation_level=None))
        self.addCleanup(db.close)
        initial_bytes = migration_path.read_bytes()
        os.environ['REAL_DB_PROTECTED_PATHS'] = str(migration_path)
        for authorization in ({}, {'REAL_DB_WRITE': 'YES'}, {'APPLY_REAL_DB': 'yes'}):
            with self.subTest(authorization=authorization), patch.dict(os.environ, authorization):
                with self.assertRaisesRegex(PersistenceError, '^APPLY_REAL_DB_NOT_AUTHORIZED'):
                    apply_migration(db)
                self.assertEqual(migration_path.read_bytes(), initial_bytes)
                self.assertEqual(db.total_changes, 0)
                self.assertFalse(db.in_transaction)
        # Migration authorization is independent of runtime path configuration.
        os.environ.pop('REAL_DB_PROTECTED_PATHS')
        os.environ['APPLY_REAL_DB'] = 'YES'
        self.assertEqual(apply_migration(db), 'APPLIED')
        self.assertEqual(db.execute('SELECT count(*) FROM analysis_tasks').fetchone()[0], 0)
        self.assertNotEqual(migration_path.read_bytes(), initial_bytes)

    def test_temporary_migration_needs_neither_authorization_nor_path_configuration(self):
        os.environ.pop('REAL_DB_PROTECTED_PATHS')
        with patch('persistence.connection.is_temporary_database', side_effect=is_temporary_database):
            for path in (':memory:', self.root / 'temporary-migration.sqlite'):
                with self.subTest(path=str(path)):
                    db = configure(sqlite3.connect(path, isolation_level=None))
                    try:
                        self.assertTrue(is_temporary_database(db))
                        self.assertEqual(apply_migration(db), 'APPLIED')
                    finally:
                        db.close()

    def test_unprotected_database_allows_generation_confirmation_and_archive(self):
        os.environ['REAL_DB_PROTECTED_PATHS'] = str(self.db_path.with_suffix('.sqlite.other'))
        self.assert_complete_archive()

    def test_protected_readonly_open_and_dashboard_reads_are_unaffected(self):
        self.protect()
        contents = tuple(self.db.iterdump())
        file_bytes = self.db_path.read_bytes()
        readonly = open_database(self.db_path, readonly=True)
        self.addCleanup(readonly.close)
        runtime = DashboardRuntime(readonly, archive_writer=self.writer)
        runtime.prepare(self.record.evidence_id)
        self.assertEqual(runtime.status, 'READY_FOR_REVIEW')
        self.assertEqual(runtime.indicators['ma60'], 129.5)
        self.assertEqual(readonly.execute('SELECT count(*) FROM evidence_records').fetchone()[0], 1)
        with self.assertRaisesRegex(sqlite3.OperationalError, 'readonly'):
            readonly.execute('CREATE TABLE forbidden_write (id INTEGER)')
        self.assertEqual(tuple(self.db.iterdump()), contents)
        self.assertEqual(self.db_path.read_bytes(), file_bytes)

    def test_unconfigured_readonly_open_and_dashboard_reads_are_unaffected(self):
        os.environ.pop('REAL_DB_PROTECTED_PATHS')
        readonly = open_database(self.db_path, readonly=True)
        self.addCleanup(readonly.close)
        runtime = DashboardRuntime(readonly, archive_writer=self.writer)
        runtime.prepare(self.record.evidence_id)
        self.assertEqual(runtime.indicators['ma60'], 129.5)

    def test_authorization_requires_exact_yes_value(self):
        self.protect()
        for name in ('REAL_DB_WRITE', 'APPLY_REAL_DB'):
            for value in ('', 'NO', 'yes', 'true', '1', 'YES ', ' YES'):
                with self.subTest(name=name, value=value), patch.dict(os.environ, {name: value}):
                    self.assert_denied_without_database_changes(self.generate)

    def test_multiple_protected_paths_normalize_relative_paths_and_whitespace(self):
        relative = os.path.relpath(self.db_path, Path.cwd())
        os.environ['REAL_DB_PROTECTED_PATHS'] = f' , {self.root / "unused.sqlite"}, {relative} , '
        self.assert_denied_without_database_changes(self.generate)

    def test_protected_symlink_path_resolves_to_database(self):
        alias = self.root / 'database-alias.sqlite'
        try:
            alias.symlink_to(self.db_path)
        except OSError as exc:
            if not symlink_privilege_missing(exc):
                raise
            self.skipTest('Windows symlink privilege unavailable')
        os.environ['REAL_DB_PROTECTED_PATHS'] = str(alias)
        self.assert_denied_without_database_changes(self.generate)

    def test_repository_cannot_bypass_gate_with_direct_sqlite_connection(self):
        self.generate()
        direct = configure(sqlite3.connect(self.db_path, isolation_level=None))
        self.addCleanup(direct.close)
        repo = SQLitePersistenceRepository(direct)
        self.protect()
        self.assert_denied_without_database_changes(lambda: repo.update_task_status(
            self.runtime.flow.run_evidence.analysis_run_id, 'FAILED'))
        self.assertEqual(direct.total_changes, 0)
        self.assertFalse(direct.in_transaction)

    def test_direct_transaction_is_denied_before_callback_or_begin(self):
        direct = sqlite3.connect(self.db_path, isolation_level=None)
        self.addCleanup(direct.close)
        statements = []
        direct.set_trace_callback(statements.append)
        called = []

        def write():
            called.append(True)
            direct.execute('CREATE TABLE forbidden_write (id INTEGER)')

        self.protect()
        self.assert_denied_without_database_changes(lambda: transaction(direct, write))
        self.assertEqual(called, [])
        self.assertFalse(any(statement.lstrip().upper().startswith('BEGIN') for statement in statements))
        self.assertEqual(direct.total_changes, 0)
        self.assertFalse(direct.in_transaction)

    def test_revoked_authorization_is_rechecked_for_next_write(self):
        self.protect()
        with patch.dict(os.environ, {'REAL_DB_WRITE': 'YES'}):
            self.generate()
        item = self.first_item()
        self.assert_denied_without_database_changes(
            lambda: self.runtime.decide(*item.key, actor_id='test-reviewer'))
        self.assertIs(self.runtime.flow.current(*item.key), HumanConfirm.PENDING)

    def test_protected_attached_database_cannot_bypass_transaction_gate(self):
        attached_path = self.root / 'attached.sqlite'
        attached = sqlite3.connect(attached_path)
        try:
            attached.execute('CREATE TABLE synthetic_marker (value INTEGER)')
            attached.commit()
        finally:
            attached.close()
        self.db.execute('ATTACH DATABASE ? AS protected', (str(attached_path),))
        os.environ['REAL_DB_PROTECTED_PATHS'] = str(attached_path)
        attached_bytes = attached_path.read_bytes()
        with patch('persistence.connection.is_temporary_database', side_effect=is_temporary_database):
            self.assertFalse(is_temporary_database(self.db))
            self.assert_denied_without_database_changes(lambda: transaction(
                self.db, lambda: self.db.execute('INSERT INTO protected.synthetic_marker VALUES (1)')))
        self.assertEqual(attached_path.read_bytes(), attached_bytes)
        self.assertEqual(self.db.execute('SELECT count(*) FROM protected.synthetic_marker').fetchone()[0], 0)

    def test_path_resolution_failure_denies_write_even_with_authorization(self):
        self.protect()
        os.environ['REAL_DB_WRITE'] = 'YES'
        called = []
        with patch('persistence.connection.Path.resolve', side_effect=OSError('synthetic resolution failure')):
            self.assert_denied_without_database_changes(
                lambda: transaction(self.db, lambda: called.append(True)),
                error_code='REAL_DB_PROTECTED_PATHS_INVALID')
        self.assertEqual(called, [])


def sql_policy_violations(sources):
    """Conservative inventory: new write/dynamic SQL requires explicit review.

    Exact calls and counts prevent an existing writer from becoming a blanket
    exception. The separate route checks pin its callback and helper callers;
    read forwarding and the two memory-only DDL builders are checked as well.
    This deliberately rejects unfamiliar SQL rather than attempting SQL parsing.
    """
    reviewed = {
        ('persistence/repository.py', 'SQLitePersistenceRepository._insert'):
            '''self.db.execute(f"INSERT INTO {quote(table)} ({','.join(quote(k) for k in row)}) "
                               f"VALUES ({','.join('?' for _ in row)})", tuple(row.values()))''',
        ('persistence/repository.py', 'SQLitePersistenceRepository.update_task_status.update'):
            "self.db.execute('UPDATE analysis_tasks SET status=? WHERE id=?', (status, analysis_run_id))",
        ('persistence/migration.py', 'apply_migration.apply'): 'db.execute(command)',
        ('persistence/migration.py', 'expected_inventory'): 'db.executescript(TEST_SCHEMA_DDL)',
        ('persistence/migration.py', 'validate_sql'): 'db.execute(command)',
        ('persistence/read_models.py', 'rows'): 'db.execute(sql, params)',
        ('persistence/connection.py', 'transaction'): "db.execute('BEGIN IMMEDIATE')",
    }
    expected = Counter((path, scope, ast.dump(ast.parse(code.strip(), mode='eval').body))
                       for (path, scope), code in reviewed.items())
    actual, violations = Counter(), []
    callbacks = {
        ('persistence/repository.py', 'SQLitePersistenceRepository._operation'): 'operation',
        ('persistence/repository.py', 'SQLitePersistenceRepository.update_task_status'): 'update',
        ('persistence/migration.py', 'apply_migration'): 'apply',
    }

    def sql_text(node):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            return node.value
        if isinstance(node, ast.JoinedStr):
            return ''.join(part.value if isinstance(part, ast.Constant) else '{}' for part in node.values)
        return ''

    allowed_pragmas = {'PRAGMA foreign_keys=ON', 'PRAGMA recursive_triggers=ON',
                       'PRAGMA busy_timeout={}', 'PRAGMA foreign_keys', 'PRAGMA recursive_triggers',
                       'PRAGMA database_list', 'PRAGMA quick_check', 'PRAGMA integrity_check',
                       'PRAGMA foreign_key_check', 'PRAGMA journal_mode'}
    callback_uses, insert_uses = Counter(), []
    for path, source in sources.items():
        tree = ast.parse(source)
        parents, scopes = {}, {}
        imported_rows_names = {alias.asname or alias.name for node in ast.walk(tree)
                               if isinstance(node, ast.ImportFrom)
                               and (node.module or '').split('.')[-1] == 'read_models'
                               for alias in node.names if alias.name == 'rows'}
        rows_names = {'rows'} | imported_rows_names

        def index(node, scope=''):
            scopes[node] = scope
            if isinstance(node, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
                scope = '.'.join(filter(None, (scope, node.name)))
            for child in ast.iter_child_nodes(node):
                parents[child] = node
                index(child, scope)

        index(tree)
        for node in ast.walk(tree):
            scope, parent = scopes[node], parents.get(node)
            location = (path, scope)
            if isinstance(node, ast.Attribute) and node.attr in ('execute', 'executemany', 'executescript'):
                if not isinstance(parent, ast.Call) or parent.func is not node:
                    violations.append((path, node.lineno, 'SQL execution alias'))
                    continue
                sql = sql_text(parent.args[0]) if parent.args else ''
                if node.attr == 'execute' and (sql.lstrip().upper().startswith('SELECT ') or
                                                sql in allowed_pragmas):
                    continue
                actual[(path, scope, ast.dump(parent))] += 1
            if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Load) and node.id == callbacks.get(location):
                callback_uses[location] += 1
                if not (isinstance(parent, ast.Call) and isinstance(parent.func, ast.Name) and
                        parent.func.id == 'transaction' and len(parent.args) == 2 and parent.args[1] is node and
                        [ast.unparse(keyword) for keyword in parent.keywords] ==
                        (['_migration=True'] if location == ('persistence/migration.py', 'apply_migration') else [])):
                    violations.append((path, node.lineno, 'unguarded callback'))
            if isinstance(node, ast.Call) and isinstance(node.func, (ast.Name, ast.Attribute)) and \
                    (node.func.id if isinstance(node.func, ast.Name) else node.func.attr) == 'transaction':
                if any(keyword.arg in ('_migration', None) for keyword in node.keywords) and \
                        location != ('persistence/migration.py', 'apply_migration'):
                    violations.append((path, node.lineno, 'migration authorization outside migration entry'))
            if isinstance(node, ast.Attribute) and node.attr == '_insert':
                insert_uses.append((path, scope, isinstance(parent, ast.Call) and parent.func is node))
            if (isinstance(node, ast.Attribute) and node.attr == 'rows' or
                    isinstance(node, ast.Name) and isinstance(node.ctx, ast.Load) and node.id in rows_names and
                    (path == 'persistence/read_models.py' or node.id in imported_rows_names or
                     isinstance(parent, ast.Call) and parent.func is node)):
                if not (isinstance(parent, ast.Call) and parent.func is node and len(parent.args) >= 2 and
                        sql_text(parent.args[1]).lstrip().upper().startswith('SELECT ')):
                    violations.append((path, node.lineno, 'non-read rows forwarding'))
            if path == 'persistence/migration.py' and isinstance(node, ast.FunctionDef) and \
                    node.name in ('expected_inventory', 'validate_sql'):
                bindings = [part for part in ast.walk(node) if isinstance(part, ast.Name) and
                            part.id == 'db' and isinstance(part.ctx, ast.Store)]
                connections = [part for part in ast.walk(node) if isinstance(part, ast.Call) and
                               isinstance(part.func, ast.Attribute) and part.func.attr == 'connect']
                if len(bindings) != 1 or [ast.unparse(call) for call in connections] != ["sqlite3.connect(':memory:')"]:
                    violations.append((path, node.lineno, 'DDL validation must remain memory-only'))
    if actual != expected:
        violations.append(('unreviewed/missing SQL', actual - expected, expected - actual))
    if callback_uses != Counter({location: 1 for location in callbacks}):
        violations.append(('transaction callback references changed', callback_uses))
    if insert_uses != [('persistence/repository.py', 'SQLitePersistenceRepository._operation.operation', True)]:
        violations.append(('insert helper callers changed', insert_uses))
    return violations


class ProductionSqlRouteTests(unittest.TestCase):
    rows_import_forms = (
        ('from persistence.read_models import rows', 'rows'),
        ('from persistence.read_models import rows as rows', 'rows'),
        ('from persistence.read_models import rows as read', 'read'),
        ('from .read_models import rows as read', 'read'),
        ('from persistence import read_models', 'read_models.rows'),
        ('from persistence import read_models as reads', 'reads.rows'),
        ('import persistence.read_models', 'persistence.read_models.rows'),
        ('import persistence.read_models as reads', 'reads.rows'),
        ('from . import read_models as reads', 'reads.rows'),
    )

    def setUp(self):
        root = Path(__file__).resolve().parents[1]
        self.sources = {path.relative_to(root).as_posix(): path.read_text(encoding='utf-8')
                        for directory in ('persistence', 'dashboard')
                        for path in (root / directory).rglob('*.py')}

    def test_all_production_sql_writes_have_reviewed_transaction_routes(self):
        self.assertEqual(sql_policy_violations(self.sources), [])

    def test_rows_import_forms_allow_select_but_detect_write_and_dynamic_sql(self):
        path = 'persistence/rows_probe.py'
        for import_statement, callee in self.rows_import_forms:
            source = f'{import_statement}\ndef probe(db, sql):\n    return {callee}(db, {{}})\n'
            with self.subTest(import_statement=import_statement, sql='SELECT'):
                self.assertEqual(sql_policy_violations({**self.sources, path: source.format(
                    repr('SELECT id FROM synthetic_marker WHERE id=?') + ', (1,)')}), [])
            for sql in (repr('INSERT INTO synthetic_marker VALUES (1) RETURNING id'),
                        repr('UPDATE synthetic_marker SET id=2 RETURNING id'),
                        repr('DELETE FROM synthetic_marker RETURNING id'),
                        repr('WITH ids AS (SELECT 1) DELETE FROM synthetic_marker RETURNING id'),
                        'sql'):
                with self.subTest(import_statement=import_statement, sql=sql):
                    self.assertEqual(sql_policy_violations({**self.sources, path: source.format(sql)}),
                                     [(path, 3, 'non-read rows forwarding')])

    def test_rows_function_references_cannot_hide_writes_behind_another_alias(self):
        path = 'persistence/rows_probe.py'
        for import_statement, callee in self.rows_import_forms:
            with self.subTest(import_statement=import_statement):
                source = (f'{import_statement}\ndef probe(db):\n    forward = {callee}\n'
                          '    return forward(db, "DELETE FROM synthetic_marker RETURNING id")\n')
                self.assertEqual(sql_policy_violations({**self.sources, path: source}),
                                 [(path, 3, 'non-read rows forwarding')])

    def test_new_direct_writes_in_any_production_module_are_detected(self):
        for sql in ('INSERT INTO t VALUES (1)', 'UPDATE t SET value=1', 'DELETE FROM t',
                    'CREATE TABLE t (value)', 'DROP TABLE t', 'ALTER TABLE t ADD COLUMN value',
                    'WITH value AS (SELECT 1) DELETE FROM t'):
            for api in ('execute', 'executemany', 'executescript'):
                with self.subTest(sql=sql, api=api):
                    sources = dict(self.sources, **{'dashboard/new_writer.py':
                        f'def bypass(db):\n    db.{api}({sql!r})\n'})
                    self.assertTrue(sql_policy_violations(sources))

    def test_existing_helpers_and_dynamic_sql_cannot_hide_new_bypasses(self):
        mutations = [
            ('persistence/repository.py', '        require(table in NEW_TABLES,',
             "        self.db.execute('DELETE FROM analysis_tasks')\n        require(table in NEW_TABLES,"),
            ('persistence/repository.py', 'return transaction(self.db, operation)', 'return operation()'),
            ('persistence/repository.py', 'return transaction(self.db, operation)',
             'return transaction(self.db, operation, _migration=True)'),
            ('persistence/repository.py', 'return transaction(self.db, update)', 'return update()'),
            ('persistence/migration.py', 'return transaction(db, apply, _migration=True)', 'return apply()'),
            ('persistence/migration.py', 'return transaction(db, apply, _migration=True)',
             'return transaction(db, apply)'),
            ('persistence/migration.py', 'sqlite3.connect(":memory:")', 'sqlite3.connect("runtime.sqlite")'),
        ]
        for path, before, after in mutations:
            with self.subTest(path=path, mutation=after):
                self.assertIn(before, self.sources[path])
                sources = {**self.sources, path: self.sources[path].replace(before, after)}
                self.assertTrue(sql_policy_violations(sources))
        for body in ('db.execute(sql)', 'writer = db.execute', 'repo._insert("analysis_tasks", {})',
                     'rows(db, "DELETE FROM analysis_tasks")',
                     'read_models.rows(db, "DELETE FROM analysis_tasks RETURNING id")',
                     'from persistence.read_models import rows as write; write(db, "DELETE FROM analysis_tasks RETURNING id")',
                     'from persistence.read_models import rows as read; write = read',
                     'transaction(db, lambda: None, _migration=True)'):
            with self.subTest(body=body):
                sources = {**self.sources, 'dashboard/new_writer.py': f'def bypass(db, repo, sql):\n    {body}\n'}
                self.assertTrue(sql_policy_violations(sources))
