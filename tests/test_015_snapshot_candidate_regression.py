"""015 snapshot candidate regressions, using synthetic memory and temporary databases."""
import sqlite3
from types import SimpleNamespace
import unittest

from tools import phase_c_contracts as contracts
from tools import phase_c_safety as safety
import test_015_phase_c_prep as fixtures


class SnapshotCandidateRegressionTests(unittest.TestCase):
    def setUp(self):
        self.db = sqlite3.connect(
            ":memory:", isolation_level=None, factory=safety.GuardedConnection,
            cached_statements=0,
        )
        self.addCleanup(self.db.close)
        safety.configure(self.db)
        for step in ("015-04", "015-05"):
            for ddl in contracts.DDL_BY_STEP[step]:
                self.db.execute(ddl)
        self.db.execute(
            "INSERT INTO security_master VALUES (?,?,?,?,?,?,?,?)",
            ("SSE", "999999", "SYNTHETIC_NAME", "STOCK", 100, "CNY", None, "LISTED"),
        )
        self.db.execute("INSERT INTO holding_config VALUES (?,?,?,?)",
                        ("SSE", "999999", 3, 0.101))
        self.db.executemany("INSERT INTO holding_config_meta VALUES (?,?)", (
            ("as_of_date", "2026-10-16"), ("version", "1.0"), ("source_sha256", "f" * 64),
        ))

    def run_dashboard(self):
        self.db.install_policy("015-06", contracts)
        self.db.execute("BEGIN")
        try:
            result = safety.execute_checked(
                "015-06", self.db, SimpleNamespace(), None, contracts,
            )
            self.db.commit()
            return result
        except BaseException:
            self.db.rollback()
            raise
        finally:
            # Python 3.10 cannot clear this callback with None.
            self.db.set_authorizer(lambda *args: sqlite3.SQLITE_OK)

    def state(self):
        return tuple(self.db.iterdump())

    def assert_refused_unchanged(self, error):
        before = self.state()
        with self.assertRaisesRegex(safety.GateError, "^" + error + "$"):
            self.run_dashboard()
        self.assertEqual(self.state(), before)

    def test_changed_holdings_with_same_metadata_are_not_current_snapshot(self):
        self.run_dashboard()
        self.db.execute("UPDATE holding_config SET quantity=4")
        self.assert_refused_unchanged("DASHBOARD_EXISTING_METADATA_MISMATCH")

    def test_equal_total_but_changed_current_name_is_not_noop(self):
        self.run_dashboard()
        self.db.execute("UPDATE security_master SET name='CHANGED_SYNTHETIC_NAME'")
        self.assert_refused_unchanged("DASHBOARD_EXISTING_PROJECTION_MISMATCH")

    def test_self_consistent_old_snapshot_date_must_match_metadata(self):
        self.run_dashboard()
        self.db.execute("UPDATE dashboard_overview SET as_of_date='2026-10-15'")
        self.db.execute("UPDATE dashboard_position_snapshot SET as_of_date='2026-10-15'")
        self.assert_refused_unchanged("DASHBOARD_EXISTING_METADATA_MISMATCH")

    def test_empty_existing_batch_still_requires_matching_business_date(self):
        self.db.execute("DELETE FROM holding_config")
        self.run_dashboard()
        self.db.execute("UPDATE dashboard_overview SET as_of_date='2026-10-15'")
        self.assert_refused_unchanged("DASHBOARD_EXISTING_METADATA_MISMATCH")

    def test_identical_batch_remains_noop_without_content_changes(self):
        self.run_dashboard()
        before = self.state()
        result = self.run_dashboard()
        self.assertEqual(result["status"], "IDEMPOTENT_NOOP")
        self.assertEqual(result["changed"], 0)
        self.assertEqual(self.state(), before)

    def test_new_business_date_appends_even_with_lexically_smaller_source_hash(self):
        self.run_dashboard()
        old = self.db.execute("SELECT * FROM dashboard_position_snapshot").fetchall()
        self.db.execute(
            "UPDATE holding_config_meta SET value='2026-10-17' WHERE key='as_of_date'",
        )
        self.db.execute(
            "UPDATE holding_config_meta SET value=? WHERE key='source_sha256'", ("a" * 64,),
        )
        self.assertEqual(self.run_dashboard()["status"], "APPLIED")
        self.assertEqual(self.db.execute(
            "SELECT batch_id,as_of_date FROM dashboard_overview ORDER BY as_of_date",
        ).fetchall(), [
            ("B20261016_ffffffffffff", "2026-10-16"),
            ("B20261017_aaaaaaaaaaaa", "2026-10-17"),
        ])
        self.assertEqual(self.db.execute(
            "SELECT * FROM dashboard_position_snapshot WHERE batch_id=?",
            ("B20261016_ffffffffffff",),
        ).fetchall(), old)

    def test_same_day_different_source_appends_without_ordering_hashes(self):
        self.run_dashboard()
        self.db.execute(
            "UPDATE holding_config_meta SET value=? WHERE key='source_sha256'", ("a" * 64,),
        )
        self.assertEqual(self.run_dashboard()["status"], "APPLIED")
        self.assertEqual(self.db.execute(
            "SELECT COUNT(*) FROM dashboard_overview",
        ).fetchone()[0], 2)

    def test_unsupported_metadata_version_is_refused_for_existing_batch(self):
        self.run_dashboard()
        self.db.execute("UPDATE holding_config_meta SET value='2.0' WHERE key='version'")
        self.assert_refused_unchanged("HOLDING_METADATA_INVALID")


class SnapshotCandidateCliRegressionTests(unittest.TestCase):
    def test_self_consistent_old_snapshot_fails_without_files_or_success_handoff(self):
        fixture = fixtures.PhaseCPrepTests()
        self.addCleanup(fixture.doCleanups)
        fixture.setUp()
        fixture.seed_holdings()
        fixture.invoke("015-06")
        fixture.mutate("UPDATE dashboard_overview SET as_of_date='2026-10-15'")
        fixture.mutate("UPDATE dashboard_position_snapshot SET as_of_date='2026-10-15'")
        before = fixture.files()
        for dry in (True, False):
            with self.subTest(dry=dry):
                report, _ = fixture.assert_refused_unchanged("015-06", dry=dry)
                self.assertEqual(report["error"], "DASHBOARD_EXISTING_METADATA_MISMATCH")
                self.assertEqual(fixture.files(), before)


if __name__ == "__main__":
    unittest.main()
