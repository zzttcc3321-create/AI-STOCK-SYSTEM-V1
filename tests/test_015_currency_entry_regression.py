"""015 currency entry-point regressions; CSV bytes and all facts are synthetic."""
from contextlib import redirect_stdout
import csv
import hashlib
import io
import json
from pathlib import Path
import sqlite3
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from tools import phase_c_contracts as contracts
from tools import phase_c_safety as safety
import test_015_phase_c_prep as fixtures


class CurrencyEntryRegressionTests(unittest.TestCase):
    def setUp(self):
        self.db = sqlite3.connect(
            ":memory:", isolation_level=None, factory=safety.GuardedConnection,
            cached_statements=0,
        )
        self.addCleanup(self.db.close)
        safety.configure(self.db)

    def source_bytes(self, currency="CNY"):
        output = io.StringIO(newline="")
        writer = csv.writer(output)
        writer.writerow(contracts.SECURITY_COLUMNS)
        writer.writerows((
            ("SSE", "999998", "SYNTHETIC_FIRST", "STOCK", "100", "CNY", "", "LISTED"),
            ("SSE", "999999", "SYNTHETIC_SECOND", "STOCK", "100", currency, "", "LISTED"),
        ))
        return output.getvalue().encode("utf-8")

    def prepare_source(self, payload):
        with patch.object(Path, "read_bytes", return_value=payload):
            return contracts.prepare("015-04", SimpleNamespace(source="/synthetic/master.csv"))

    def execute(self, step, prepared=None):
        self.db.install_policy(step, contracts)
        self.db.execute("BEGIN")
        try:
            result = safety.execute_checked(
                step, self.db, SimpleNamespace(), prepared, contracts,
            )
            self.db.commit()
            return result
        except BaseException:
            self.db.rollback()
            raise
        finally:
            # Python 3.10 cannot clear this callback with None.
            self.db.set_authorizer(lambda *args: sqlite3.SQLITE_OK)

    def seed_master(self, currency):
        self.db.execute(contracts.DDL_BY_STEP["015-04"][0])
        self.db.execute(
            "INSERT INTO security_master VALUES (?,?,?,?,?,?,?,?)",
            ("SSE", "999999", "SYNTHETIC_SECOND", "STOCK", 100, currency, None, "LISTED"),
        )

    def holding_input(self):
        return {
            "rows": [("SSE", "999999", 3, 0.101)],
            "as_of_date": "2026-10-16", "source_sha256": "a" * 64,
        }

    def seed_holdings(self):
        for ddl in contracts.DDL_BY_STEP["015-05"]:
            self.db.execute(ddl)
        self.db.execute("INSERT INTO holding_config VALUES (?,?,?,?)",
                        ("SSE", "999999", 3, 0.101))
        self.db.executemany("INSERT INTO holding_config_meta VALUES (?,?)", (
            ("as_of_date", "2026-10-16"), ("version", "1.0"), ("source_sha256", "a" * 64),
        ))

    def assert_currency_refused(self, step, prepared, scope):
        before = tuple(self.db.iterdump())
        with self.assertRaisesRegex(safety.GateError, "^INVALID_CURRENCY$") as caught:
            self.execute(step, prepared)
        self.assertEqual(tuple(self.db.iterdump()), before)
        self.assertEqual(caught.exception.context, {
            "field": "currency", "record_scope": scope,
            "record_number": 1, "source_sha256": "a" * 64,
        })

    def test_invalid_csv_currencies_are_rejected_with_source_record_coordinates(self):
        for value in ("ZZZ", "USD", "HKD", "cny", " CNY", "CNY ", " ", "\tCNY"):
            with self.subTest(currency=value):
                payload = self.source_bytes(value)
                with self.assertRaisesRegex(safety.GateError, "^INVALID_CURRENCY$") as caught:
                    self.prepare_source(payload)
                self.assertEqual(caught.exception.context, {
                    "field": "currency", "record_scope": "SECURITY_SOURCE", "record_number": 2,
                    "source_sha256": hashlib.sha256(payload).hexdigest(),
                })
                self.assertEqual(safety.inventory(self.db), ())

    def test_invalid_prepared_import_rejects_entire_batch_before_any_create(self):
        prepared = self.prepare_source(self.source_bytes())
        prepared["rows"][1] = (*prepared["rows"][1][:5], "ZZZ",
                               *prepared["rows"][1][6:])
        before = tuple(self.db.iterdump())
        with self.assertRaisesRegex(safety.GateError, "^INVALID_CURRENCY$") as caught:
            self.execute("015-04", prepared)
        self.assertEqual(tuple(self.db.iterdump()), before)
        self.assertEqual(caught.exception.context["record_scope"], "SECURITY_PREPARED")
        self.assertEqual(caught.exception.context["record_number"], 2)

    def test_cny_and_empty_csv_default_still_import_and_repeat_as_noop(self):
        prepared = self.prepare_source(self.source_bytes(""))
        self.assertEqual([row[5] for row in prepared["rows"]], ["CNY", "CNY"])
        self.assertEqual(self.execute("015-04", prepared)["inserted"], 2)
        before = tuple(self.db.iterdump())
        self.assertEqual(self.execute("015-04", prepared)["status"], "IDEMPOTENT_NOOP")
        self.assertEqual(tuple(self.db.iterdump()), before)

    def test_valid_cny_pipeline_writes_all_three_steps_and_repeats_dashboard(self):
        self.execute("015-04", self.prepare_source(self.source_bytes("")))
        self.execute("015-05", self.holding_input())
        self.assertEqual(self.execute("015-06")["status"], "APPLIED")
        self.assertEqual(self.db.execute(
            "SELECT total_positions,total_cost_value,currency FROM dashboard_overview",
        ).fetchall(), [(1, 0.303, "CNY")])
        self.assertEqual(self.execute("015-06")["status"], "IDEMPOTENT_NOOP")

    def test_holding_rejects_existing_invalid_master_before_creating_tables(self):
        self.seed_master("ZZZ")
        self.assert_currency_refused("015-05", self.holding_input(), "HOLDING_PREPARED")

    def test_dashboard_rejects_existing_invalid_master_before_creating_tables(self):
        self.seed_master("ZZZ")
        self.seed_holdings()
        self.assert_currency_refused("015-06", None, "DASHBOARD_PROJECTION")

    def test_existing_dashboard_cannot_bypass_current_currency_validation(self):
        self.seed_master("CNY")
        self.seed_holdings()
        self.execute("015-06")
        self.db.execute("UPDATE security_master SET currency='ZZZ'")
        self.assert_currency_refused("015-06", None, "DASHBOARD_PROJECTION")

    def test_stored_empty_currency_is_invalid_even_though_csv_empty_defaults(self):
        self.seed_master("")
        self.assert_currency_refused("015-05", self.holding_input(), "HOLDING_PREPARED")

    def test_cli_emits_traceable_redacted_failure_without_preflight_or_apply(self):
        payload = self.source_bytes("SECRET_NON_CNY")
        digest = hashlib.sha256(payload).hexdigest()
        output = io.StringIO()
        argv = [
            "--auth-flag", contracts.AUTH_BY_STEP["015-04"],
            "--target-db", "/synthetic/facts.sqlite", "--expected-db-sha256", "b" * 64,
            "--backup-dir", "/synthetic/backups", "--source", "/synthetic/PRIVATE_SOURCE.csv",
            "--dry-run",
        ]
        with patch.object(safety, "safe_path", side_effect=lambda p, **kw: Path(p)), \
                patch.object(safety, "check_sidecars"), \
                patch.object(safety, "file_sha256", return_value="b" * 64), \
                patch.object(safety, "validate_backup"), \
                patch.object(Path, "read_bytes", return_value=payload), \
                patch.object(safety, "memory_preflight",
                             return_value={"status": "APPLIED", "changed": 2}) as preflight, \
                patch.object(safety, "apply") as apply, redirect_stdout(output):
            status = safety.main("015-04", argv)
        self.assertEqual(status, 1)
        preflight.assert_not_called()
        apply.assert_not_called()
        report = json.loads(output.getvalue())
        self.assertEqual(report, {
            "step": "015-04", "result": "FAIL", "error": "INVALID_CURRENCY",
            "error_context": {
                "field": "currency", "record_scope": "SECURITY_SOURCE", "record_number": 2,
                "source_sha256": digest, "database_sha256": "b" * 64,
            },
        })
        for secret in ("SECRET_NON_CNY", "SYNTHETIC_FIRST", "SYNTHETIC_SECOND",
                       "999998", "999999", "/synthetic/", "PRIVATE_SOURCE"):
            self.assertNotIn(secret, output.getvalue())
        self.assertNotIn("POST_WRITE_SHA256=", output.getvalue())


class CurrencyCliRegressionTests(unittest.TestCase):
    """Exercise actual CLI preflight against temporary synthetic files only."""

    def setUp(self):
        self.fixture = fixtures.PhaseCPrepTests()
        self.addCleanup(self.fixture.doCleanups)
        self.fixture.setUp()

    def test_invalid_source_records_fail_without_writes_or_success_handoff(self):
        fixture = self.fixture
        rows = [row[:] for row in fixtures.MASTER_ROWS]
        rows[1][5] = "PRIVATE_NON_CNY"
        fixture.write_csv(fixture.master, fixtures.SECURITY_COLUMNS, rows)
        before = fixture.files()
        for dry in (True, False):
            with self.subTest(dry=dry):
                report, output = fixture.assert_refused_unchanged("015-04", dry=dry)
                self.assertEqual(report["error"], "INVALID_CURRENCY")
                self.assertEqual(report["error_context"], {
                    "field": "currency", "record_scope": "SECURITY_SOURCE",
                    "record_number": 2, "source_sha256": fixtures.sha(fixture.master),
                    "database_sha256": fixtures.sha(fixture.db),
                })
                self.assertNotIn("PRIVATE_NON_CNY", output)
                self.assertEqual(fixture.files(), before)

    def test_invalid_stored_currency_is_reported_before_holding_or_dashboard_writes(self):
        fixture = self.fixture
        fixture.seed_holdings()
        fixture.mutate("UPDATE security_master SET currency='PRIVATE_NON_CNY' WHERE market='SSE'")
        before = fixture.files()
        for step, scope in (("015-05", "HOLDING_PREPARED"),
                            ("015-06", "DASHBOARD_PROJECTION")):
            with self.subTest(step=step):
                report, output = fixture.assert_refused_unchanged(step)
                self.assertEqual(report["error"], "INVALID_CURRENCY")
                self.assertEqual(report["error_context"], {
                    "field": "currency", "record_scope": scope, "record_number": 1,
                    "source_sha256": fixtures.sha(fixture.holdings),
                    "database_sha256": fixtures.sha(fixture.db),
                })
                self.assertNotIn("PRIVATE_NON_CNY", output)
                self.assertEqual(fixture.files(), before)


if __name__ == "__main__":
    unittest.main()
