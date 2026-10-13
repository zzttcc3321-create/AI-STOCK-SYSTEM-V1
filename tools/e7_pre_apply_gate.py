"""Read-only E7 gate; migration writes are confined to newly owned copies.

Reports contain summaries and opaque digests, never database rows or exception
messages. No real database selector is built into this tool.
"""
from __future__ import annotations

import argparse
from contextlib import closing
from datetime import datetime, timezone
import fnmatch
import importlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
if __name__ == "__main__":
    sys.dont_write_bytecode = True
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from persistence.backup import file_sha256, wal_safe_backup
from persistence.connection import open_database
from persistence.contracts import PersistenceError
from persistence.migration import (apply_migration, checks, legacy_digest,
                                   preflight, schema_inventory)
from persistence.schema_v1_1 import NEW_TABLES

TASK_ID = "AI-STOCK-V1-014-E7-PRE-APPLY-RUNTIME-GATE"
STEPS = tuple("e7-" + str(i).zfill(2) for i in range(1, 8))
HASH = re.compile(r"[a-f0-9]{64}\Z")
LIBRARY_CODES = {"SCHEMA_DRIFT", "LEGACY_TABLE_COUNT_NOT_32",
                 "DATABASE_INTEGRITY_FAILURE", "BACKUP_OVERWRITE_FORBIDDEN",
                 "ONLINE_BACKUP_TIMEOUT", "BACKUP_SCHEMA_MISMATCH",
                 "BACKUP_REOPEN_MISMATCH", "BACKUP_HASH_MISMATCH", "LEGACY_CHANGED"}


class GateError(ValueError):
    pass


def require(condition, code):
    if not condition:
        raise GateError(code)


def git(repo, *command):
    return subprocess.check_output(["git", "-C", str(repo), *command],
                                   text=True, stderr=subprocess.DEVNULL, timeout=30).strip()


def snapshot(path):
    with closing(open_database(path, readonly=True)) as db:
        info = preflight(db, require_legacy_32=True)
        legacy = legacy_digest(db)
        return {"SCHEMA_STATE": info["state"],
                "SCHEMA_FINGERPRINT": info["schema_fingerprint"],
                "LEGACY_SCHEMA_FINGERPRINT": legacy["schema"],
                "LEGACY_ROW_COUNT_DIGEST": legacy["row_count_digest"],
                "LEGACY_DIGEST": legacy, "JOURNAL_MODE": info["journal_mode"]}


def load_report(path, step):
    # Strict duplicate-key rejection avoids ambiguous audit inputs.
    def unique(pairs):
        value = {}
        for key, item in pairs:
            require(key not in value, "REPORT_DUPLICATE_KEY")
            value[key] = item
        return value
    try:
        value = json.loads(Path(path).read_text(encoding="utf-8"), object_pairs_hook=unique)
    except (OSError, UnicodeError, ValueError):
        raise GateError("REPORT_MISSING_OR_CORRUPT") from None
    require(type(value) is dict and value.get("STEP") == step and
            value.get("TASK_ID") == TASK_ID, "REPORT_IDENTITY_INVALID")
    return value


def e7_01(args, report):
    repo = Path(args.repo_root).resolve()
    report.update(GIT_HEAD=git(repo, "rev-parse", "HEAD"),
                  GIT_BRANCH=git(repo, "branch", "--show-current"),
                  GIT_STATUS=git(repo, "status", "--porcelain", "--untracked-files=all"))
    probes = 0
    def scan_error(error):
        raise GateError("WORKSPACE_SCAN_FAILED")
    for directory, dirs, files in os.walk(repo, followlinks=False, onerror=scan_error):
        dirs[:] = [name for name in dirs if name != ".git"]
        probes += sum(any(fnmatch.fnmatchcase(name.lower(), pattern) for pattern in
                          ("*probe*", "e2_*_tmp*")) for name in files + dirs)
    report["TEMP_PROBE_COUNT"] = probes
    require(probes == 0, "TEMP_PROBE_FOUND")
    require(report["GIT_STATUS"] == "", "WORKSPACE_NOT_CLEAN")
    require(args.expected_head is None or report["GIT_HEAD"] == args.expected_head,
            "EXPECTED_HEAD_MISMATCH")
    require(args.expected_branch is None or report["GIT_BRANCH"] == args.expected_branch,
            "EXPECTED_BRANCH_MISMATCH")
    result = subprocess.run(["git", "-C", str(repo), "diff", "--check"],
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=30)
    require(result.returncode == 0, "GIT_DIFF_CHECK_FAILED")
    report["GIT_DIFF_CHECK"] = "PASS"


def e7_02(args, report):
    with closing(open_database(args.db_path, readonly=True)) as db:
        report.update(snapshot(args.db_path))
        tables = [row[1] for row in schema_inventory(db) if row[0] == "table"]
        report.update(TOTAL_TABLE_COUNT=len(tables),
                      LEGACY_TABLE_COUNT=sum(name not in NEW_TABLES for name in tables),
                      V1_1_TABLE_COUNT=sum(name in NEW_TABLES for name in tables),
                      CHECKS=checks(db))
        require(report["SCHEMA_STATE"] == "B", "SCHEMA_STATE_NOT_B")
        require((report["TOTAL_TABLE_COUNT"], report["LEGACY_TABLE_COUNT"],
                 report["V1_1_TABLE_COUNT"]) == (39, 32, 7), "TABLE_COUNT_MISMATCH")


def e7_03(args, report):
    report.update(snapshot(args.db_path))


def e7_04(args, report):
    baseline = load_report(Path(args.output_dir) / "e7-03.json", "e7-03")
    require(baseline.get("result") == "PASS" and baseline.get("DATABASE_PATH") ==
            report["DATABASE_PATH"], "FINGERPRINT_REPORT_INVALID")
    before = snapshot(args.db_path)
    require(all(before[key] == baseline.get(key) for key in before), "SOURCE_BASELINE_MISMATCH")
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    target = Path(args.output_dir).resolve() / ("stock_analysis_pre_apply_e7_" + timestamp + ".db")
    require(target != Path(args.db_path).resolve(), "BACKUP_SOURCE_ALIAS")
    with closing(open_database(args.db_path, readonly=True)) as source:
        metadata = wal_safe_backup(source, target)
    require(target.is_file(), "BACKUP_MISSING")
    report.update(BACKUP_PATH=str(target), BACKUP_SHA256=file_sha256(target),
                  BACKUP_SIZE=target.stat().st_size,
                  BACKUP_FINGERPRINT_MATCH=(metadata["source_schema_fingerprint"] ==
                                            metadata["backup_schema_fingerprint"]))
    require(report["BACKUP_FINGERPRINT_MATCH"], "BACKUP_SCHEMA_MISMATCH")
    with closing(open_database(target, readonly=True)) as db:
        report["CHECKS"] = checks(db)
    backup = snapshot(target)
    require(all(backup[key] == before[key] for key in
                ("SCHEMA_STATE", "SCHEMA_FINGERPRINT", "LEGACY_DIGEST")), "BACKUP_BASELINE_MISMATCH")
    require(snapshot(args.db_path) == before, "SOURCE_BASELINE_MISMATCH")
    report.update(SCHEMA_FINGERPRINT=backup["SCHEMA_FINGERPRINT"], LEGACY_DIGEST=backup["LEGACY_DIGEST"])


def e7_05(args, report):
    report["ROLLBACK_RESULT"] = "FAIL"
    require(args.backup_path is not None and args.backup_sha256 is not None and
            args.work_dir is not None, "ROLLBACK_ARGUMENTS_REQUIRED")
    backup = Path(args.backup_path).resolve(strict=True)
    require(backup != Path(args.db_path).resolve(), "BACKUP_SOURCE_ALIAS")
    require(HASH.fullmatch(args.backup_sha256) is not None and
            file_sha256(backup) == args.backup_sha256, "BACKUP_HASH_MISMATCH")
    before = snapshot(backup)
    work = Path(args.work_dir).resolve()
    work.mkdir(parents=True, exist_ok=True)
    # A private, unique subdirectory makes deletion ownership explicit even when
    # --work-dir is on another Windows drive outside the OS temp directory.
    with tempfile.TemporaryDirectory(prefix="e7-rollback-", dir=work) as directory:
        copy = Path(directory) / "rehearsal.db"
        with backup.open("rb") as source, copy.open("xb") as destination:
            shutil.copyfileobj(source, destination)
        require(file_sha256(copy) == args.backup_sha256, "COPY_HASH_MISMATCH")
        with closing(open_database(copy)) as db:
            migration = apply_migration(db, require_legacy_32=True)
            require(migration == ("APPLIED" if before["SCHEMA_STATE"] == "A" else
                                  "ALREADY_APPLIED"), "MIGRATION_RESULT_MISMATCH")
            checks(db)
            require(preflight(db, require_legacy_32=True)["state"] == "B", "SCHEMA_STATE_NOT_B")
            require(legacy_digest(db) == before["LEGACY_DIGEST"], "LEGACY_CHANGED")
        report["MIGRATION_RESULT"] = migration
        require(file_sha256(backup) == args.backup_sha256, "BACKUP_HASH_MISMATCH")
        copy.unlink()
        # Remove only sidecars belonging to the tool-created copy before restore.
        for suffix in ("-wal", "-shm", "-journal"):
            sidecar = Path(str(copy) + suffix)
            if sidecar.exists():
                sidecar.unlink()
        with backup.open("rb") as source, copy.open("xb") as destination:
            shutil.copyfileobj(source, destination)
        require(file_sha256(copy) == args.backup_sha256, "RESTORE_HASH_MISMATCH")
        restored = snapshot(copy)
        require(restored == before, "RESTORE_BASELINE_MISMATCH")
        with closing(open_database(copy, readonly=True)) as db:
            report["CHECKS"] = checks(db)
        require(file_sha256(backup) == args.backup_sha256, "BACKUP_HASH_MISMATCH")
        report.update(restored, ROLLBACK_RESULT="PASS", LEGACY_DIGEST_UNCHANGED=True,
                      BACKUP_PATH=str(backup), BACKUP_SHA256=args.backup_sha256,
                      BACKUP_HASH_UNCHANGED=True)


def tree_hashes(root):
    return {str(path.relative_to(root)): file_sha256(path)
            for path in root.rglob("*") if path.is_file()}


def e7_06(args, report):
    items = report.setdefault("SMOKE_CHECKS", {})
    for name in ("providers", "dashboard.runtime", "dashboard.factory", "evidence.archive_reader"):
        items[name] = "FAIL"
        importlib.import_module(name)
        items[name] = "PASS"
    from dashboard.runtime import DashboardRuntime
    from evidence import ArchiveIdentity, ArchiveReader, ArchiveWriter
    from providers import ProviderStatus
    require(isinstance(DashboardRuntime, type), "RUNTIME_CLASS_UNAVAILABLE")
    require(ProviderStatus.NOT_AVAILABLE.value == "NOT_AVAILABLE", "PROVIDER_STATUS_UNAVAILABLE")
    items.update(DashboardRuntime="PASS", ProviderStatus="PASS",
                 PROVIDER_CONNECTIONS="NOT_AVAILABLE")
    items["TEMP_ARCHIVE_READ"] = "FAIL"
    with tempfile.TemporaryDirectory(prefix="e7-smoke-") as directory:
        root = Path(directory)
        identity = ArchiveIdentity("synthetic", "synthetic", "synthetic", "synthetic")
        ArchiveWriter(root).write(identity=identity, source="TUSHARE_MCP",
            raw_parts={"response": b'{}'}, endpoint_or_tool="synthetic_smoke",
            request_params={}, request_timestamp="2026-01-01T00:00:00Z")
        before = tree_hashes(root)
        result = ArchiveReader(root).read(identity.uri, include_raw=False)
        require(result["integrity_status"] == "VERIFIED" and tree_hashes(root) == before,
                "ARCHIVE_READ_SIDE_EFFECT")
    items["TEMP_ARCHIVE_READ"] = "PASS"
    report.update(DATABASE_OPENED=False, NETWORK_CALLS=0, SERVICES_STARTED=0)


def e7_07(args, report):
    report.update(E7_STATUS="BLOCKED", REAL_DB_WRITE=False, APPLY_REAL_DB="NOT_AUTHORIZED")
    if args.input:
        require(args.input_dir is None, "AUDIT_INPUT_AMBIGUOUS")
        inputs = {}
        for path in args.input:
            # Find a unique step without trusting its payload for the output.
            for step in STEPS[:6]:
                try:
                    value = load_report(path, step)
                except GateError:
                    continue
                require(step not in inputs, "AUDIT_DUPLICATE_STEP")
                inputs[step] = value
                break
            else:
                raise GateError("REPORT_MISSING_OR_CORRUPT")
    else:
        directory = Path(args.input_dir or args.output_dir)
        inputs = {step: load_report(directory / (step + ".json"), step) for step in STEPS[:6]}
    require(set(inputs) == set(STEPS[:6]), "AUDIT_INPUT_MISSING")
    report["STEPS"] = {step: {"result": (value.get("result") if
                      value.get("result") in ("PASS", "FAIL") else "INVALID")}
                      for step, value in inputs.items()}
    require(all(value.get("result") == "PASS" for value in inputs.values()), "AUDIT_STEP_FAILED")
    require(all(value.get("DATABASE_PATH") == report["DATABASE_PATH"] for value in inputs.values()),
            "AUDIT_DATABASE_MISMATCH")
    one, two, three, four, five, six = (inputs[step] for step in STEPS[:6])
    require(type(one.get("GIT_HEAD")) is str and re.fullmatch(r"[a-f0-9]{40,64}", one["GIT_HEAD"]) and
            one.get("GIT_STATUS") == "" and one.get("TEMP_PROBE_COUNT") == 0 and
            one.get("GIT_DIFF_CHECK") == "PASS", "AUDIT_WORKSPACE_INVALID")
    require(two.get("SCHEMA_STATE") == three.get("SCHEMA_STATE") == five.get("SCHEMA_STATE") == "B" and
            (two.get("TOTAL_TABLE_COUNT"), two.get("LEGACY_TABLE_COUNT"), two.get("V1_1_TABLE_COUNT")) ==
            (39, 32, 7), "AUDIT_SCHEMA_INVALID")
    fingerprints = [value.get("SCHEMA_FINGERPRINT") for value in (two, three, four, five)]
    require(all(type(value) is str and HASH.fullmatch(value) for value in fingerprints) and
            len(set(fingerprints)) == 1, "AUDIT_SCHEMA_MISMATCH")
    legacy = three.get("LEGACY_DIGEST")
    require(type(legacy) is dict and set(legacy) == {"schema", "row_count_digest"} and
            all(type(value) is str and HASH.fullmatch(value) for value in legacy.values()) and
            all(value.get("LEGACY_DIGEST") == legacy for value in (two, four, five)) and
            three.get("LEGACY_SCHEMA_FINGERPRINT") == legacy["schema"] and
            three.get("LEGACY_ROW_COUNT_DIGEST") == legacy["row_count_digest"], "AUDIT_LEGACY_MISMATCH")
    require(type(four.get("BACKUP_PATH")) is str and bool(four["BACKUP_PATH"]) and
            four.get("BACKUP_PATH") == five.get("BACKUP_PATH") and
            type(four.get("BACKUP_SHA256")) is str and HASH.fullmatch(four["BACKUP_SHA256"]) and
            four.get("BACKUP_SHA256") == five.get("BACKUP_SHA256") and
            four.get("BACKUP_FINGERPRINT_MATCH") is True and
            type(four.get("BACKUP_SIZE")) is int and four["BACKUP_SIZE"] > 0,
            "AUDIT_BACKUP_MISMATCH")
    require(five.get("ROLLBACK_RESULT") == "PASS" and five.get("LEGACY_DIGEST_UNCHANGED") is True and
            five.get("BACKUP_HASH_UNCHANGED") is True and five.get("MIGRATION_RESULT") == "ALREADY_APPLIED",
            "AUDIT_ROLLBACK_INVALID")
    expected_checks = {"quick_check": "ok", "integrity_check": "ok", "foreign_key_check": "PASS"}
    require(all(value.get("CHECKS") == expected_checks for value in (two, four, five)),
            "AUDIT_CHECKS_INVALID")
    smoke = six.get("SMOKE_CHECKS")
    required_smoke = {"providers", "dashboard.runtime", "dashboard.factory", "evidence.archive_reader",
                      "DashboardRuntime", "ProviderStatus", "TEMP_ARCHIVE_READ"}
    require(type(smoke) is dict and required_smoke.issubset(smoke) and
            all(smoke[key] == "PASS" for key in required_smoke) and
            all(value in ("PASS", "NOT_AVAILABLE") for value in smoke.values()) and
            six.get("DATABASE_OPENED") is False and six.get("NETWORK_CALLS") == 0 and
            six.get("SERVICES_STARTED") == 0, "AUDIT_SMOKE_INVALID")
    report.update(GIT_HEAD=one["GIT_HEAD"], BACKUP_PATH=four["BACKUP_PATH"],
                  BACKUP_SHA256=four["BACKUP_SHA256"], SCHEMA_BEFORE=three["SCHEMA_FINGERPRINT"],
                  SCHEMA_AFTER=five["SCHEMA_FINGERPRINT"], LEGACY_DIGEST=legacy,
                  ROLLBACK_RESULT=five["ROLLBACK_RESULT"], E7_STATUS="PASS")


HANDLERS = dict(zip(STEPS, (e7_01, e7_02, e7_03, e7_04, e7_05, e7_06, e7_07)))


class Parser(argparse.ArgumentParser):
    def error(self, message):
        # argparse diagnostics may echo supplied credentials; emit a fixed code.
        raise GateError("CLI_ARGUMENTS_INVALID")


def parser():
    result = Parser(description=__doc__)
    children = result.add_subparsers(dest="command", required=True, parser_class=Parser)
    for step in (*STEPS, "all"):
        child = children.add_parser(step)
        child.add_argument("--db-path", required=True, type=Path)
        child.add_argument("--output-dir", required=True, type=Path)
        child.add_argument("--repo-root", type=Path, default=ROOT)
        child.add_argument("--expected-head")
        child.add_argument("--expected-branch")
        child.add_argument("--backup-path", type=Path)
        child.add_argument("--backup-sha256")
        child.add_argument("--work-dir", type=Path)
        child.add_argument("--input-dir", type=Path)
        child.add_argument("--input", type=Path, action="append")
        child.add_argument("--output", type=Path, required=(step == "e7-07"))
    return result


def run_step(args, step):
    report = {"TASK_ID": TASK_ID, "STEP": step, "result": "FAIL",
              "DATABASE_PATH": str(Path(args.db_path).resolve())}
    if step == "e7-07":
        report.update(E7_STATUS="BLOCKED", REAL_DB_WRITE=False, APPLY_REAL_DB="NOT_AUTHORIZED")
    target = Path(args.output_dir).resolve() / (step + ".json")
    if step == "e7-07" and args.output is not None:
        target = Path(args.output).resolve()
    writable = False
    try:
        require(target.is_relative_to(Path(args.output_dir).resolve()), "REPORT_OUTSIDE_OUTPUT_DIR")
        require(target != Path(args.db_path).resolve(), "REPORT_SOURCE_ALIAS")
        require(not target.exists(), "REPORT_OVERWRITE_FORBIDDEN")
        target.parent.mkdir(parents=True, exist_ok=True)
        writable = True
        HANDLERS[step](args, report)
        report["result"] = "PASS"
    except Exception as exc:
        code = "STEP_EXECUTION_FAILED"
        if isinstance(exc, GateError):
            code = str(exc)
        elif isinstance(exc, PersistenceError) and str(exc) in LIBRARY_CODES:
            code = str(exc)
        report.update(result="FAIL", error_code=code)
        if step == "e7-07":
            report["E7_STATUS"] = "BLOCKED"
    if writable:
        try:
            with target.open("x", encoding="utf-8") as stream:
                json.dump(report, stream, ensure_ascii=False, indent=2)
                stream.write("\n")
        except OSError:
            report.update(result="FAIL", error_code="REPORT_WRITE_FAILED")
            if step == "e7-07":
                report["E7_STATUS"] = "BLOCKED"
    return report


def main(argv=None):
    try:
        args = parser().parse_args(argv)
    except GateError:
        print(json.dumps({"result": "FAIL", "error_code": "CLI_ARGUMENTS_INVALID"}))
        return 1
    steps = STEPS if args.command == "all" else (args.command,)
    for step in steps:
        report = run_step(args, step)
        print(json.dumps(report, ensure_ascii=False))
        if report["result"] != "PASS":
            return 1
        if args.command == "all" and step == "e7-04":
            args.backup_path = Path(report["BACKUP_PATH"])
            args.backup_sha256 = report["BACKUP_SHA256"]
            args.work_dir = args.work_dir or Path(args.output_dir) / "work"
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
