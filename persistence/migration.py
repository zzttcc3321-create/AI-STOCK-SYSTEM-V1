"""Fail-closed A/B/C schema migration, with no implicit real DB connection."""
from pathlib import Path
import re
import sqlite3

from .connection import transaction
from .contracts import require
from .schema_v1_1 import NEW_TABLES, TEST_SCHEMA_DDL
from .serialization import digest

MIGRATION_FILE = Path(__file__).resolve().parents[1] / "migration/003_db_schema_v1_1_additive.sql"


def quote(name):
    return '"' + name.replace('"', '""') + '"'


def statements(sql):
    pending = ""
    for line in sql.splitlines():
        if line.lstrip().startswith("--"):
            continue
        for char in line + "\n":
            pending += char
            if char == ";" and sqlite3.complete_statement(pending):
                yield pending.strip()
                pending = ""
    require(not pending.strip(), "INCOMPLETE_MIGRATION_SQL")


def schema_inventory(db, *, new_only=False, legacy_only=False):
    rows = []
    for kind, name, table, sql in db.execute(
            "SELECT type,name,tbl_name,sql FROM sqlite_master ORDER BY type,name"):
        if name.startswith("sqlite_"):
            continue
        if new_only and table not in NEW_TABLES:
            continue
        if legacy_only and table in NEW_TABLES:
            continue
        # Normalize formatting outside SQL literals only. CHECK enum literals
        # containing whitespace must remain semantically distinct.
        if sql:
            fragments = re.split(r"('(?:''|[^'])*'|\"(?:\"\"|[^\"])*\")", sql.strip())
            sql = "".join(part if i % 2 else re.sub(r"\s+", " ", part)
                          for i, part in enumerate(fragments))
        rows.append([kind, name, table, sql])
    return rows


def schema_fingerprint(db, **kwargs):
    return digest(schema_inventory(db, **kwargs))


def expected_inventory():
    db = sqlite3.connect(":memory:")
    try:
        db.executescript(TEST_SCHEMA_DDL)
        return schema_inventory(db, new_only=True)
    finally:
        db.close()


def schema_state(db):
    expected = expected_inventory()
    # Include collisions involving any expected index/trigger/table name.
    expected_names = {r[1].lower() for r in expected}
    actual = [r for r in schema_inventory(db) if r[2].lower() in NEW_TABLES or r[1].lower() in expected_names]
    if not actual:
        return "A"
    return "B" if actual == expected else "C"


def checks(db):
    quick = [r[0] for r in db.execute("PRAGMA quick_check")]
    integrity = [r[0] for r in db.execute("PRAGMA integrity_check")]
    foreign = list(db.execute("PRAGMA foreign_key_check"))
    require(quick == ["ok"] and integrity == ["ok"] and not foreign, "DATABASE_INTEGRITY_FAILURE")
    return {"quick_check": "ok", "integrity_check": "ok", "foreign_key_check": "PASS"}


def legacy_digest(db):
    # No business counts are returned; opaque digest is used only for comparison.
    counts = [[name, db.execute(f"SELECT count(*) FROM {quote(name)}").fetchone()[0]]
              for kind, name, _, _ in schema_inventory(db, legacy_only=True) if kind == "table"]
    return {"schema": schema_fingerprint(db, legacy_only=True), "row_count_digest": digest(counts)}


def preflight(db, *, require_legacy_32=False):
    checks(db)
    state = schema_state(db)
    require(state != "C", "SCHEMA_DRIFT")
    count = sum(r[0] == "table" for r in schema_inventory(db, legacy_only=True))
    require(not require_legacy_32 or count == 32, "LEGACY_TABLE_COUNT_NOT_32")
    return {"state": state, "legacy_table_count": count,
            "v1_1_table_count": 7 if state == "B" else 0,
            "schema_fingerprint": schema_fingerprint(db),
            "journal_mode": db.execute("PRAGMA journal_mode").fetchone()[0]}


def validate_sql(sql):
    commands = list(statements(sql))
    require(commands and all(re.match(r"CREATE\s+(?:UNIQUE\s+)?(?:TABLE|INDEX|TRIGGER)\s+", s, re.I)
                             for s in commands), "MIGRATION_NOT_ADDITIVE")
    require(not re.search(r"\bIF\s+NOT\s+EXISTS\b", sql, re.I), "IF_NOT_EXISTS_FORBIDDEN")
    db = sqlite3.connect(":memory:")
    try:
        for command in commands:
            db.execute(command)
        require(schema_inventory(db) == expected_inventory(), "SCHEMA_CONTRACT_PARITY_FAILURE")
    finally:
        db.close()
    return commands


def apply_migration(db, *, require_legacy_32=False):
    commands = validate_sql(MIGRATION_FILE.read_text(encoding="utf-8"))

    def apply():
        before = preflight(db, require_legacy_32=require_legacy_32)
        if before["state"] == "B":
            return "ALREADY_APPLIED"
        legacy = legacy_digest(db)
        for command in commands:
            db.execute(command)  # executescript would implicitly commit: forbidden.
        require(schema_state(db) == "B", "SCHEMA_DRIFT")
        require(legacy_digest(db) == legacy, "LEGACY_CHANGED")
        checks(db)
        return "APPLIED"

    return transaction(db, apply)
