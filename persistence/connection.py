"""Explicit connections; importing this module performs no database IO."""
import os
from pathlib import Path
import sqlite3
import tempfile

from .contracts import require


def configure(db, busy_timeout=5000):
    require(not db.in_transaction, "ACTIVE_TRANSACTION_FORBIDDEN")
    require(type(busy_timeout) is int and 0 < busy_timeout <= 60000, "BOUNDED_TIMEOUT_REQUIRED")
    db.execute("PRAGMA foreign_keys=ON")
    db.execute("PRAGMA recursive_triggers=ON")
    db.execute(f"PRAGMA busy_timeout={busy_timeout}")
    require(db.execute("PRAGMA foreign_keys").fetchone()[0] == 1 and
            db.execute("PRAGMA recursive_triggers").fetchone()[0] == 1, "SQLITE_GUARDS_REQUIRED")
    db.row_factory = sqlite3.Row
    return db


def open_database(path, *, readonly=False):
    if str(path) == ":memory:":
        require(not readonly, "MEMORY_READONLY_UNSUPPORTED")
        db = sqlite3.connect(":memory:", isolation_level=None)
    else:
        resolved = Path(path).resolve(strict=True)
        db = sqlite3.connect(resolved.as_uri() + ("?mode=ro" if readonly else "?mode=rw"),
                             uri=True, isolation_level=None)
    try:
        return configure(db)
    except BaseException:
        db.close()
        raise


def is_temporary_database(db):
    # SQLite can materialize its internal ephemeral temp schema during checks.
    databases = [r for r in db.execute("PRAGMA database_list") if not (r[1] == "temp" and not r[2])]
    if len(databases) != 1 or databases[0][1] != "main":
        return False
    filename = databases[0][2]
    if not filename:
        return True
    return Path(filename).resolve().is_relative_to(Path(tempfile.gettempdir()).resolve())


def _require_real_db_write(db):
    """Check actual connected files on every write, including caller-opened DBs."""
    try:
        protected = {Path(value.strip()).expanduser().resolve()
                     for value in os.environ.get("REAL_DB_PROTECTED_PATHS", "").split(",")
                     if value.strip()}
        filenames = {Path(row[2]).resolve() for row in db.execute("PRAGMA database_list") if row[2]}
    except (OSError, RuntimeError, ValueError):
        require(False, "REAL_DB_PROTECTED_PATHS_INVALID")
    if protected & filenames:
        require(os.environ.get("REAL_DB_WRITE") == "YES" or os.environ.get("APPLY_REAL_DB") == "YES",
                "REAL_DB_WRITE_NOT_AUTHORIZED: requires REAL_DB_WRITE=YES or APPLY_REAL_DB=YES")


def transaction(db, operation):
    _require_real_db_write(db)
    configure(db)
    db.execute("BEGIN IMMEDIATE")
    try:
        result = operation()
        db.commit()  # Includes deferred FK checks; failures take the rollback path.
        return result
    except BaseException:
        db.rollback()
        raise
