import sqlite3
import unittest
from unittest.mock import patch

from persistence.connection import configure
from persistence.contracts import PersistenceError
from persistence.migration import (MIGRATION_FILE, apply_migration, checks, legacy_digest,
                                   preflight, schema_inventory, schema_state, validate_sql)
from persistence.schema_v1_1 import NEW_TABLES, TEST_SCHEMA_DDL


def legacy_fixture(db):
    # Synthetic 32-table fixture, deliberately not a reconstruction of fact DB.
    for ordinal in range(32):
        db.execute(f"CREATE TABLE legacy_{ordinal} (id INTEGER PRIMARY KEY, value TEXT)")
        db.execute(f"INSERT INTO legacy_{ordinal} VALUES (1,?)", (f"synthetic-{ordinal}",))
    db.execute("CREATE INDEX legacy_index ON legacy_0(value)")
    db.commit()


class MigrationTests(unittest.TestCase):
    def setUp(self):
        self.db = configure(sqlite3.connect(":memory:"))
        self.addCleanup(self.db.close)

    def test_static_contract_double_database_introspection(self):
        other = sqlite3.connect(":memory:")
        self.addCleanup(other.close)
        other.executescript(TEST_SCHEMA_DDL)
        commands = validate_sql(MIGRATION_FILE.read_text())
        for command in commands:
            self.db.execute(command)
        self.assertEqual(schema_inventory(self.db), schema_inventory(other))
        for table in NEW_TABLES:
            for pragma in ("table_info", "foreign_key_list", "index_list"):
                self.assertEqual([tuple(r) for r in self.db.execute(f"PRAGMA {pragma}({table})")],
                                 list(other.execute(f"PRAGMA {pragma}({table})")))
            for index in other.execute(f"PRAGMA index_list({table})"):
                for pragma in ("index_info", "index_xinfo"):
                    self.assertEqual([tuple(r) for r in self.db.execute(f"PRAGMA {pragma}('{index[1]}')")],
                                     list(other.execute(f"PRAGMA {pragma}('{index[1]}')")))

    def test_a_b_legacy_unchanged_and_double_apply_noop(self):
        legacy_fixture(self.db)
        before = legacy_digest(self.db)
        self.assertEqual(preflight(self.db, require_legacy_32=True)["state"], "A")
        self.assertEqual(apply_migration(self.db, require_legacy_32=True), "APPLIED")
        self.assertEqual(preflight(self.db, require_legacy_32=True)["state"], "B")
        self.assertEqual(apply_migration(self.db, require_legacy_32=True), "ALREADY_APPLIED")
        self.assertEqual(before, legacy_digest(self.db))
        self.assertEqual(len(list(self.db.execute("SELECT name FROM sqlite_master WHERE type='table'"))), 39)

    def test_partial_incompatible_and_object_collision_fail_closed(self):
        for sql in ("CREATE TABLE analysis_tasks (id TEXT)",
                    "CREATE TABLE ANALYSIS_TASKS (id TEXT)",
                    "CREATE TABLE evidence_records (id TEXT)",
                    "CREATE TABLE analysis_tasks_no_delete (id TEXT)"):
            with self.subTest(sql=sql):
                db = configure(sqlite3.connect(":memory:"))
                try:
                    db.execute(sql)
                    self.assertEqual(schema_state(db), "C")
                    before = schema_inventory(db)
                    with self.assertRaisesRegex(PersistenceError, "SCHEMA_DRIFT"):
                        apply_migration(db)
                    self.assertEqual(before, schema_inventory(db))
                finally:
                    db.close()

    def test_changed_check_or_extra_trigger_is_drift(self):
        self.db.executescript(TEST_SCHEMA_DDL.replace("event_sequence >= 0", "event_sequence >= 1"))
        self.assertEqual(schema_state(self.db), "C")
        db = configure(sqlite3.connect(":memory:"))
        self.addCleanup(db.close)
        apply_migration(db)
        db.execute("CREATE TRIGGER unexpected AFTER INSERT ON analysis_tasks BEGIN SELECT 1; END")
        self.assertEqual(schema_state(db), "C")

    def test_sql_literal_whitespace_cannot_hide_schema_drift(self):
        self.db.executescript(TEST_SCHEMA_DDL.replace("'CONFIRMED'", "'CONFIRMED  '"))
        self.assertEqual(schema_state(self.db), "C")

    def test_migration_commit_failure_rolls_back_schema(self):
        class FailCommit(sqlite3.Connection):
            def commit(self):
                raise sqlite3.OperationalError("injected COMMIT failure")
        db = configure(sqlite3.connect(":memory:", factory=FailCommit))
        self.addCleanup(db.close)
        with self.assertRaisesRegex(sqlite3.OperationalError, "COMMIT"):
            apply_migration(db)
        self.assertEqual(schema_state(db), "A")
        self.assertFalse(db.in_transaction)

    def test_any_migration_failure_rolls_back_all_ddl(self):
        legacy_fixture(self.db)
        before = schema_inventory(self.db)
        original = checks
        calls = []
        def fail_after_ddl(db):
            calls.append(1)
            if len(calls) == 2:
                raise RuntimeError("post-DDL failure")
            return original(db)
        with patch("persistence.migration.checks", side_effect=fail_after_ddl):
            with self.assertRaisesRegex(RuntimeError, "post-DDL"):
                apply_migration(self.db)
        self.assertEqual(schema_inventory(self.db), before)
        self.assertFalse(self.db.in_transaction)

    def test_sql_injection_contract_drift_nonadditive_rejected(self):
        original = MIGRATION_FILE.read_text()
        for invalid in (original + "DROP TABLE analysis_tasks;", original + "CREATE TABLE extra (id TEXT);",
                        original.replace("CREATE TABLE analysis_tasks", "CREATE TABLE IF NOT EXISTS analysis_tasks"),
                        original.replace("event_sequence >= 0", "event_sequence >= 1"),
                        original + "UPDATE analysis_tasks SET status='x';"):
            with self.assertRaises(PersistenceError):
                validate_sql(invalid)

    def test_wrong_legacy_count_and_fk_failure_stop(self):
        with self.assertRaisesRegex(PersistenceError, "NOT_32"):
            apply_migration(self.db, require_legacy_32=True)
        self.db.execute("PRAGMA foreign_keys=OFF")
        self.db.executescript("CREATE TABLE parent(id TEXT PRIMARY KEY); CREATE TABLE child(id TEXT REFERENCES parent); INSERT INTO child VALUES('missing');")
        with self.assertRaisesRegex(PersistenceError, "INTEGRITY"):
            apply_migration(self.db)
