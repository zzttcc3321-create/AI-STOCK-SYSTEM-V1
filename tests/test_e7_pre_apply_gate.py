"""Offline E7 acceptance: synthetic legacy schema, never a real DB replica."""
from contextlib import closing, redirect_stdout
from datetime import datetime, timezone
import io
import json
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from persistence.backup import file_sha256
from persistence.connection import open_database
from persistence.migration import apply_migration, legacy_digest, preflight
from test_persistence_migration import legacy_fixture
from tools import e7_pre_apply_gate as gate


class E7GateTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory(prefix="e7-test-")
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        self.repo = self.root / "repo"
        self.repo.mkdir()
        self.git("init", "-b", "main")
        self.git("config", "user.email", "synthetic@example.invalid")
        self.git("config", "user.name", "Synthetic Test")
        (self.repo / "tracked.txt").write_text("synthetic\n", encoding="utf-8")
        (self.repo / ".gitignore").write_text("*probe*\ne2_*_tmp*\n", encoding="utf-8")
        self.git("add", ".")
        self.git("commit", "-m", "synthetic baseline")
        self.db = self.root / "synthetic.db"
        self.make_db(self.db)
        self.output = self.root / "reports"
        self.work = self.root / "work"

    def git(self, *args):
        return subprocess.check_output(["git", "-C", str(self.repo), *args],
                                       text=True, stderr=subprocess.DEVNULL).strip()

    def make_db(self, path, state="B"):
        with closing(sqlite3.connect(path, isolation_level=None)) as db:
            legacy_fixture(db)
            if state == "B":
                apply_migration(db, require_legacy_32=True)
            elif state == "C":
                db.execute("CREATE TABLE analysis_tasks (id TEXT)")

    def invoke(self, step, *extra, output=None, db=None, success=True, code=None):
        output = output or self.output
        command = [step, "--db-path", str(db or self.db), "--output-dir", str(output),
                   "--repo-root", str(self.repo), *map(str, extra)]
        if step == "e7-07" and "--output" not in command:
            command += ["--output", str(output / "audit.json")]
        stream = io.StringIO()
        with redirect_stdout(stream):
            status = gate.main(command)
        reports = [json.loads(line) for line in stream.getvalue().splitlines()]
        self.assertEqual(status, 0 if success else 1, reports)
        self.assertEqual(reports[-1]["result"], "PASS" if success else "FAIL")
        if code:
            self.assertEqual(reports[-1]["error_code"], code)
        if step == "e7-07" and not success:
            self.assertEqual(reports[-1]["E7_STATUS"], "BLOCKED")
            self.assertIs(reports[-1]["REAL_DB_WRITE"], False)
            self.assertEqual(reports[-1]["APPLY_REAL_DB"], "NOT_AUTHORIZED")
        return reports[-1]

    def backup(self, state="B"):
        if state != "B":
            self.db.unlink()
            self.make_db(self.db, state)
        self.invoke("e7-03")
        return self.invoke("e7-04")

    def rollback(self, backup, **kwargs):
        return self.invoke("e7-05", "--backup-path", backup["BACKUP_PATH"],
                           "--backup-sha256", backup["BACKUP_SHA256"],
                           "--work-dir", self.work, **kwargs)

    def audit_inputs(self):
        self.invoke("all")
        (self.output / "e7-07.json").unlink()

    def change_report(self, step, **values):
        path = self.output / (step + ".json")
        report = json.loads(path.read_text(encoding="utf-8"))
        report.update(values)
        path.write_text(json.dumps(report), encoding="utf-8")

    def test_01_clean_and_expected_version(self):
        head = self.git("rev-parse", "HEAD")
        report = self.invoke("e7-01", "--expected-head", head, "--expected-branch", "main")
        self.assertEqual(report["GIT_HEAD"], head)
        self.assertEqual(report["GIT_STATUS"], "")
        self.assertEqual(report["GIT_DIFF_CHECK"], "PASS")

    def test_01_dirty_tracked_file(self):
        (self.repo / "tracked.txt").write_text("changed\n", encoding="utf-8")
        self.invoke("e7-01", success=False, code="WORKSPACE_NOT_CLEAN")

    def test_01_untracked_file(self):
        (self.repo / "untracked.txt").write_text("synthetic", encoding="utf-8")
        self.invoke("e7-01", success=False, code="WORKSPACE_NOT_CLEAN")

    def test_01_ignored_probe_files_and_directory(self):
        for index, name in enumerate(("probe_test", "hidden_probe.db", "e2_case_tmp1", "nested_probe")):
            with self.subTest(name=name):
                path = self.repo / name
                if index == 3:
                    path.mkdir()
                else:
                    path.write_bytes(b"synthetic")
                self.assertEqual(self.git("status", "--porcelain"), "")
                self.invoke("e7-01", output=self.root / str(index), success=False, code="TEMP_PROBE_FOUND")
                path.rmdir() if path.is_dir() else path.unlink()

    def test_01_expected_head_mismatch(self):
        self.invoke("e7-01", "--expected-head", "0" * 40, success=False, code="EXPECTED_HEAD_MISMATCH")

    def test_01_expected_branch_mismatch(self):
        self.invoke("e7-01", "--expected-branch", "wrong", success=False, code="EXPECTED_BRANCH_MISMATCH")

    def test_01_diff_check_nonzero(self):
        original = subprocess.run
        def fail_diff(command, **kwargs):
            if command[-2:] == ["diff", "--check"]:
                return subprocess.CompletedProcess(command, 2)
            return original(command, **kwargs)
        with patch.object(gate.subprocess, "run", side_effect=fail_diff):
            self.invoke("e7-01", success=False, code="GIT_DIFF_CHECK_FAILED")

    def test_01_workspace_scan_error_cannot_silently_skip_probes(self):
        def unreadable_walk(*args, **kwargs):
            kwargs["onerror"](PermissionError("private directory details"))
            yield  # Preserve os.walk's generator interface.
        with patch.object(gate.os, "walk", side_effect=unreadable_walk):
            report = self.invoke("e7-01", success=False, code="WORKSPACE_SCAN_FAILED")
        self.assertNotIn("private directory", json.dumps(report))

    def test_02_b_39_tables_and_readonly(self):
        before = file_sha256(self.db)
        with patch.object(gate, "open_database", wraps=open_database) as opened:
            report = self.invoke("e7-02")
        self.assertTrue(all(call.kwargs == {"readonly": True} for call in opened.call_args_list))
        self.assertEqual(report["SCHEMA_STATE"], "B")
        self.assertEqual((report["TOTAL_TABLE_COUNT"], report["LEGACY_TABLE_COUNT"],
                          report["V1_1_TABLE_COUNT"]), (39, 32, 7))
        self.assertEqual(file_sha256(self.db), before)

    def test_02_a_rejected(self):
        path = self.root / "a.db"
        self.make_db(path, "A")
        self.invoke("e7-02", db=path, success=False, code="SCHEMA_STATE_NOT_B")

    def test_02_c_drift_rejected(self):
        path = self.root / "c.db"
        self.make_db(path, "C")
        self.invoke("e7-02", db=path, success=False, code="SCHEMA_DRIFT")

    def test_02_wrong_legacy_count(self):
        with closing(open_database(self.db)) as db:
            db.execute("CREATE TABLE extra_legacy (id INTEGER)")
        self.invoke("e7-02", success=False, code="LEGACY_TABLE_COUNT_NOT_32")

    def test_02_integrity_failure(self):
        with closing(sqlite3.connect(self.db, isolation_level=None)) as db:
            db.execute("DROP TABLE legacy_0")
            db.execute("CREATE TABLE legacy_0 (id INTEGER REFERENCES legacy_1(id))")
            db.execute("INSERT INTO legacy_0 VALUES(999)")
        self.invoke("e7-02", success=False, code="DATABASE_INTEGRITY_FAILURE")

    def test_02_missing_database_never_created(self):
        path = self.root / "missing.db"
        self.invoke("e7-02", db=path, success=False)
        self.assertFalse(path.exists())

    def test_03_records_matching_preflight_and_legacy_without_writes(self):
        before = file_sha256(self.db)
        with closing(open_database(self.db, readonly=True)) as db:
            expected = preflight(db, require_legacy_32=True)
            legacy = legacy_digest(db)
        with patch.object(gate, "open_database", wraps=open_database) as opened:
            report = self.invoke("e7-03")
        self.assertTrue(all(call.kwargs.get("readonly") is True for call in opened.call_args_list))
        self.assertEqual(report["SCHEMA_FINGERPRINT"], expected["schema_fingerprint"])
        self.assertEqual(report["JOURNAL_MODE"], expected["journal_mode"])
        self.assertEqual(report["LEGACY_SCHEMA_FINGERPRINT"], legacy["schema"])
        self.assertEqual(report["LEGACY_ROW_COUNT_DIGEST"], legacy["row_count_digest"])
        self.assertEqual(file_sha256(self.db), before)
        self.assertEqual(json.loads((self.output / "e7-03.json").read_text(encoding="utf-8")), report)

    def test_03_drift_fail_closed(self):
        with closing(open_database(self.db)) as db:
            db.execute("CREATE TRIGGER extra AFTER INSERT ON analysis_tasks BEGIN SELECT 1; END")
        self.invoke("e7-03", success=False, code="SCHEMA_DRIFT")

    def test_04_backup_matches_and_checks(self):
        before = file_sha256(self.db)
        report = self.backup()
        backup = Path(report["BACKUP_PATH"])
        self.assertRegex(backup.name, r"^stock_analysis_pre_apply_e7_\d{8}T\d{6}Z\.db$")
        self.assertEqual(backup.parent, self.output)
        self.assertIs(report["BACKUP_FINGERPRINT_MATCH"], True)
        self.assertEqual(report["BACKUP_SHA256"], file_sha256(backup))
        self.assertEqual(report["BACKUP_SIZE"], backup.stat().st_size)
        self.assertEqual(report["CHECKS"]["foreign_key_check"], "PASS")
        self.assertEqual(file_sha256(self.db), before)

    def test_04_live_wal_commits_preserved(self):
        with closing(open_database(self.db)) as db:
            db.execute("PRAGMA journal_mode=WAL")
            db.execute("PRAGMA wal_autocheckpoint=0")
            db.execute("INSERT INTO legacy_0 VALUES(2,'synthetic-in-wal')")
            self.assertGreater(Path(str(self.db) + "-wal").stat().st_size, 0)
            expected = legacy_digest(db)
            report = self.backup()
            with closing(open_database(report["BACKUP_PATH"], readonly=True)) as backup:
                self.assertEqual(legacy_digest(backup), expected)

    def test_04_existing_backup_rejected_without_overwrite(self):
        self.invoke("e7-03")
        now = datetime(2026, 1, 1, tzinfo=timezone.utc)
        backup = self.output / "stock_analysis_pre_apply_e7_20260101T000000Z.db"
        backup.write_bytes(b"synthetic-existing")
        before = file_sha256(backup)
        with patch.object(gate, "datetime") as clock:
            clock.now.return_value = now
            self.invoke("e7-04", success=False, code="BACKUP_OVERWRITE_FORBIDDEN")
        self.assertEqual(file_sha256(backup), before)

    def test_04_source_alias_rejected(self):
        self.output.mkdir()
        path = self.output / "stock_analysis_pre_apply_e7_20260101T000000Z.db"
        self.make_db(path)
        self.invoke("e7-03", db=path)
        before = file_sha256(path)
        with patch.object(gate, "datetime") as clock:
            clock.now.return_value = datetime(2026, 1, 1, tzinfo=timezone.utc)
            self.invoke("e7-04", db=path, success=False, code="BACKUP_SOURCE_ALIAS")
        self.assertEqual(file_sha256(path), before)

    def test_04_missing_fingerprint_report(self):
        self.invoke("e7-04", success=False, code="REPORT_MISSING_OR_CORRUPT")
        self.assertFalse(list(self.output.glob("*.db")))

    def test_04_changed_source_after_baseline(self):
        self.invoke("e7-03")
        with closing(open_database(self.db)) as db:
            db.execute("INSERT INTO legacy_0 VALUES(2,'synthetic-change')")
        self.invoke("e7-04", success=False, code="SOURCE_BASELINE_MISMATCH")
        self.assertFalse(list(self.output.glob("*.db")))

    def test_04_backup_timeout_fail_closed(self):
        self.invoke("e7-03")
        with patch("persistence.backup.time.monotonic", side_effect=[0, 100]):
            self.invoke("e7-04", success=False, code="ONLINE_BACKUP_TIMEOUT")

    def test_05_a_applied_and_restore_to_a(self):
        backup = self.backup("A")
        source_hash = file_sha256(self.db)
        with patch.object(gate, "open_database", wraps=open_database) as opened:
            report = self.rollback(backup)
        self.assertEqual(report["MIGRATION_RESULT"], "APPLIED")
        self.assertEqual(report["SCHEMA_STATE"], "A")
        self.assertIs(report["LEGACY_DIGEST_UNCHANGED"], True)
        self.assertEqual(report["ROLLBACK_RESULT"], "PASS")
        self.assertEqual(report["LEGACY_DIGEST"], backup["LEGACY_DIGEST"])
        self.assertEqual(file_sha256(backup["BACKUP_PATH"]), backup["BACKUP_SHA256"])
        self.assertEqual(file_sha256(self.db), source_hash)
        for call in opened.call_args_list:
            path = Path(call.args[0])
            self.assertNotEqual(path, self.db)
            if not call.kwargs.get("readonly", False):
                self.assertTrue(path.is_relative_to(self.work))
        self.assertEqual(list(self.work.iterdir()), [])

    def test_05_b_already_applied_and_restore_to_b(self):
        backup = self.backup()
        report = self.rollback(backup)
        self.assertEqual(report["MIGRATION_RESULT"], "ALREADY_APPLIED")
        self.assertEqual(report["SCHEMA_STATE"], "B")
        self.assertEqual(report["SCHEMA_FINGERPRINT"], backup["SCHEMA_FINGERPRINT"])
        self.assertEqual(report["LEGACY_DIGEST"], backup["LEGACY_DIGEST"])
        self.assertEqual(file_sha256(backup["BACKUP_PATH"]), backup["BACKUP_SHA256"])

    def test_05_hash_mismatch_rejects_before_copy_or_apply(self):
        backup = self.backup()
        backup["BACKUP_SHA256"] = "0" * 64
        with patch.object(gate, "apply_migration", side_effect=AssertionError("must not apply")) as apply:
            self.rollback(backup, success=False, code="BACKUP_HASH_MISMATCH")
        apply.assert_not_called()
        self.assertFalse(self.work.exists())

    def test_05_source_database_cannot_be_backup(self):
        self.invoke("e7-05", "--backup-path", self.db, "--backup-sha256", file_sha256(self.db),
                    "--work-dir", self.work, success=False, code="BACKUP_SOURCE_ALIAS")

    def test_05_required_arguments(self):
        self.invoke("e7-05", success=False, code="ROLLBACK_ARGUMENTS_REQUIRED")

    def test_05_migration_failure_no_false_rollback_pass(self):
        backup = self.backup("A")
        with patch.object(gate, "apply_migration", side_effect=RuntimeError("private exception text")):
            report = self.rollback(backup, success=False, code="STEP_EXECUTION_FAILED")
        self.assertEqual(report["ROLLBACK_RESULT"], "FAIL")
        self.assertNotIn("private exception", json.dumps(report))
        self.assertEqual(file_sha256(backup["BACKUP_PATH"]), backup["BACKUP_SHA256"])

    def test_05_restore_corruption_fail_closed(self):
        backup = self.backup()
        original = gate.shutil.copyfileobj
        count = 0
        def corrupt_restore(source, destination):
            nonlocal count
            count += 1
            original(source, destination)
            if count == 2:
                destination.write(b"synthetic-corruption")
        with patch.object(gate.shutil, "copyfileobj", side_effect=corrupt_restore):
            report = self.rollback(backup, success=False, code="RESTORE_HASH_MISMATCH")
        self.assertEqual(report["ROLLBACK_RESULT"], "FAIL")
        self.assertEqual(file_sha256(backup["BACKUP_PATH"]), backup["BACKUP_SHA256"])

    def test_06_smoke_has_no_database_network_or_runtime_side_effects(self):
        from dashboard.runtime import DashboardRuntime
        from providers import TushareMCPProvider, TdxLocalHttpProvider
        before = file_sha256(self.db)
        with patch.object(gate, "open_database", side_effect=AssertionError("DB forbidden")), \
             patch("socket.socket.connect", side_effect=AssertionError("network forbidden")), \
             patch("dashboard.factory.create_runtime", side_effect=AssertionError("factory forbidden")), \
             patch.object(DashboardRuntime, "__init__", side_effect=AssertionError("runtime forbidden")), \
             patch.object(TushareMCPProvider, "__init__", side_effect=AssertionError("provider forbidden")), \
             patch.object(TdxLocalHttpProvider, "__init__", side_effect=AssertionError("provider forbidden")):
            report = self.invoke("e7-06")
        self.assertEqual(file_sha256(self.db), before)
        self.assertEqual(report["SMOKE_CHECKS"]["TEMP_ARCHIVE_READ"], "PASS")
        self.assertEqual(report["SMOKE_CHECKS"]["PROVIDER_CONNECTIONS"], "NOT_AVAILABLE")
        self.assertIs(report["DATABASE_OPENED"], False)
        self.assertEqual(report["NETWORK_CALLS"], 0)

    def test_06_does_not_even_require_database_to_exist(self):
        missing = self.root / "not-opened.db"
        self.invoke("e7-06", db=missing)
        self.assertFalse(missing.exists())

    def test_06_import_failure_reported(self):
        with patch.object(gate.importlib, "import_module", side_effect=ImportError("private details")):
            report = self.invoke("e7-06", success=False)
        self.assertEqual(report["SMOKE_CHECKS"]["providers"], "FAIL")
        self.assertNotIn("private details", json.dumps(report))

    def test_07_all_pass_required_fields_and_constants(self):
        self.audit_inputs()
        report = self.invoke("e7-07", "--input-dir", self.output)
        required = {"TASK_ID", "GIT_HEAD", "DATABASE_PATH", "BACKUP_PATH", "BACKUP_SHA256",
                    "SCHEMA_BEFORE", "SCHEMA_AFTER", "LEGACY_DIGEST", "ROLLBACK_RESULT",
                    "REAL_DB_WRITE", "APPLY_REAL_DB", "E7_STATUS", "STEPS"}
        self.assertTrue(required.issubset(report))
        self.assertEqual(report["E7_STATUS"], "PASS")
        self.assertEqual(report["SCHEMA_BEFORE"], report["SCHEMA_AFTER"])
        self.assertIs(report["REAL_DB_WRITE"], False)
        self.assertEqual(report["APPLY_REAL_DB"], "NOT_AUTHORIZED")
        self.assertEqual(json.loads((self.output / "audit.json").read_text(encoding="utf-8")), report)

    def test_07_explicit_input_files(self):
        self.audit_inputs()
        arguments = []
        for step in reversed(gate.STEPS[:6]):
            arguments += ["--input", self.output / (step + ".json")]
        self.assertEqual(self.invoke("e7-07", *arguments)["E7_STATUS"], "PASS")

    def test_07_any_failed_step_blocked(self):
        self.audit_inputs()
        self.change_report("e7-06", result="FAIL")
        self.invoke("e7-07", success=False, code="AUDIT_STEP_FAILED")

    def test_07_missing_input_blocked(self):
        self.audit_inputs()
        (self.output / "e7-04.json").unlink()
        self.invoke("e7-07", success=False, code="REPORT_MISSING_OR_CORRUPT")

    def test_07_corrupt_input_blocked(self):
        self.audit_inputs()
        (self.output / "e7-03.json").write_text("{", encoding="utf-8")
        self.invoke("e7-07", success=False, code="REPORT_MISSING_OR_CORRUPT")

    def test_07_missing_required_field_not_fabricated(self):
        self.audit_inputs()
        self.change_report("e7-04", BACKUP_SHA256=None)
        self.invoke("e7-07", success=False, code="AUDIT_BACKUP_MISMATCH")

    def test_07_schema_mismatch_blocked(self):
        self.audit_inputs()
        self.change_report("e7-05", SCHEMA_FINGERPRINT="0" * 64)
        self.invoke("e7-07", success=False, code="AUDIT_SCHEMA_MISMATCH")

    def test_07_mixed_database_reports_blocked(self):
        self.audit_inputs()
        self.change_report("e7-06", DATABASE_PATH="synthetic-other.db")
        self.invoke("e7-07", success=False, code="AUDIT_DATABASE_MISMATCH")

    def test_07_changed_legacy_digest_blocked(self):
        self.audit_inputs()
        self.change_report("e7-05", LEGACY_DIGEST={"schema": "0" * 64, "row_count_digest": "0" * 64})
        self.invoke("e7-07", success=False, code="AUDIT_LEGACY_MISMATCH")

    def test_07_duplicate_inputs_blocked(self):
        self.audit_inputs()
        path = self.output / "e7-01.json"
        self.invoke("e7-07", "--input", path, "--input", path,
                    success=False, code="AUDIT_DUPLICATE_STEP")

    def test_07_duplicate_json_key_blocked(self):
        self.audit_inputs()
        path = self.output / "e7-01.json"
        value = path.read_text(encoding="utf-8")
        path.write_text(value[:-2] + ', "result": "PASS"}', encoding="utf-8")
        self.invoke("e7-07", success=False, code="REPORT_MISSING_OR_CORRUPT")

    def test_07_untrusted_result_payload_is_never_echoed(self):
        self.audit_inputs()
        self.change_report("e7-06", result={"private": "synthetic-private-payload"})
        report = self.invoke("e7-07", success=False, code="AUDIT_STEP_FAILED")
        self.assertEqual(report["STEPS"]["e7-06"]["result"], "INVALID")
        self.assertNotIn("synthetic-private-payload", json.dumps(report))

    def test_all_order_and_end_to_end(self):
        before = file_sha256(self.db)
        report = self.invoke("all")
        self.assertEqual(report["STEP"], "e7-07")
        self.assertEqual(report["E7_STATUS"], "PASS")
        self.assertEqual(file_sha256(self.db), before)
        self.assertTrue(all((self.output / (step + ".json")).exists() for step in gate.STEPS))

    def test_all_stops_immediately_at_first_failure(self):
        (self.repo / "untracked.txt").write_text("synthetic", encoding="utf-8")
        self.invoke("all", success=False, code="WORKSPACE_NOT_CLEAN")
        self.assertEqual(sorted(path.name for path in self.output.iterdir()), ["e7-01.json"])

    def test_all_stops_at_database_precheck(self):
        path = self.root / "a.db"
        self.make_db(path, "A")
        self.invoke("all", db=path, success=False, code="SCHEMA_STATE_NOT_B")
        self.assertEqual(sorted(path.name for path in self.output.iterdir()), ["e7-01.json", "e7-02.json"])

    def test_reports_refuse_overwrite(self):
        self.invoke("e7-03")
        path = self.output / "e7-03.json"
        before = file_sha256(path)
        self.invoke("e7-03", success=False, code="REPORT_OVERWRITE_FORBIDDEN")
        self.assertEqual(file_sha256(path), before)

    def test_report_write_failure_cannot_be_pass(self):
        original = Path.open
        def unwritable(path, *args, **kwargs):
            if path.name == "e7-01.json":
                raise PermissionError("private output details")
            return original(path, *args, **kwargs)
        with patch.object(Path, "open", new=unwritable):
            report = self.invoke("e7-01", success=False, code="REPORT_WRITE_FAILED")
        self.assertNotIn("private output", json.dumps(report))

    def test_audit_output_must_be_inside_output_directory(self):
        outside = self.root / "outside.json"
        self.invoke("e7-07", "--output", outside, success=False, code="REPORT_OUTSIDE_OUTPUT_DIR")
        self.assertFalse(outside.exists())

    def test_report_cannot_alias_database(self):
        before = file_sha256(self.db)
        self.invoke("e7-07", "--output", self.db, output=self.root,
                    success=False, code="REPORT_SOURCE_ALIAS")
        self.assertEqual(file_sha256(self.db), before)

    def test_cli_requires_explicit_paths_and_emits_safe_json(self):
        for arguments in (["e7-02"], ["e7-02", "--db-path", str(self.db)],
                          ["e7-06", "--private-argument", "synthetic-secret"]):
            with self.subTest(arguments=arguments), redirect_stdout(io.StringIO()) as output:
                self.assertEqual(gate.main(arguments), 1)
                self.assertEqual(json.loads(output.getvalue()),
                                 {"result": "FAIL", "error_code": "CLI_ARGUMENTS_INVALID"})

    def test_subprocess_cli_works_from_another_directory(self):
        result = subprocess.run([sys.executable, str(gate.ROOT / "tools/e7_pre_apply_gate.py"),
            "all", "--db-path", str(self.db), "--output-dir", str(self.output),
            "--repo-root", str(self.repo)], cwd=self.root, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
        reports = [json.loads(line) for line in result.stdout.splitlines()]
        self.assertEqual([report["STEP"] for report in reports], list(gate.STEPS))
        self.assertEqual(reports[-1]["E7_STATUS"], "PASS")


if __name__ == "__main__":
    unittest.main()
