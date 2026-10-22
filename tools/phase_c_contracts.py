"""Commander-defined, offline Phase C storage contracts.

All mutations run in the transaction owned by ``phase_c_safety``. Errors carry
fixed codes only: source values and holdings must never escape in diagnostics.
"""
from __future__ import annotations

from decimal import Decimal, InvalidOperation
import math
import re
import sqlite3

from tools.phase_c_safety import (
    GateError, integer, money, read_csv, require, valid_date, verify_create_sha256,
)


SECURITY_COLUMNS = (
    "market", "symbol", "name", "security_type", "lot_size", "currency",
    "list_date", "status",
)
HOLDING_COLUMNS = ("market", "symbol", "quantity", "avg_cost")
# 015 prices and dashboard totals are in yuan; no FX conversion is defined.
SUPPORTED_CURRENCIES = frozenset({"CNY"})


class CurrencyValidationError(GateError):
    """A fixed error code plus value-free, reproducible record coordinates."""

    def __init__(self, record_scope, record_number, source_sha256):
        super().__init__("INVALID_CURRENCY")
        self.context = {
            "field": "currency",
            "record_scope": record_scope,
            # One-based logical record number; a CSV header is not a record.
            "record_number": record_number,
        }
        if source_sha256 is not None:
            self.context["source_sha256"] = source_sha256


def _currency(value, *, record_scope, record_number, source_sha256=None):
    if value not in SUPPORTED_CURRENCIES:
        raise CurrencyValidationError(record_scope, record_number, source_sha256)
    return value


AUTH_BY_STEP = {
    "015-04": "SECURITY_MASTER_IMPORT=YES",
    "015-05": "HOLDING_CONFIG_WRITE=YES",
    "015-06": "DASHBOARD_INITIAL_DATA_WRITE=YES",
}

# Commander's CREATE statements, including the authorized 3-decimal avg_cost revision.
DDL_BY_STEP = {
    "015-04": (
        """CREATE TABLE security_master (
    market TEXT NOT NULL CHECK (market IN ('SSE', 'SZSE', 'BSE')),
    symbol TEXT NOT NULL,
    name TEXT NOT NULL,
    security_type TEXT NOT NULL CHECK (security_type IN ('STOCK', 'ETF')),
    lot_size INTEGER NOT NULL CHECK (lot_size > 0),
    currency TEXT NOT NULL DEFAULT 'CNY',
    list_date TEXT NULL,
    status TEXT NOT NULL CHECK (status IN ('LISTED', 'SUSPENDED', 'DELISTED')),
    PRIMARY KEY (market, symbol)
);
""",
    ),
    "015-05": (
        """CREATE TABLE IF NOT EXISTS holding_config(
    market   TEXT NOT NULL,
    symbol   TEXT NOT NULL,
    quantity INTEGER NOT NULL CHECK(quantity >= 0),
    avg_cost REAL NOT NULL CHECK(avg_cost >= 0 AND avg_cost = ROUND(avg_cost, 3)),
    PRIMARY KEY(market, symbol)
  );""",
        """CREATE TABLE IF NOT EXISTS holding_config_meta(
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
  );""",
    ),
    "015-06": (
        """CREATE TABLE IF NOT EXISTS dashboard_position_snapshot(
    batch_id   TEXT NOT NULL,
    market     TEXT NOT NULL,
    symbol     TEXT NOT NULL,
    name       TEXT NOT NULL,
    quantity   INTEGER NOT NULL,
    avg_cost   REAL NOT NULL,
    cost_value REAL NOT NULL,
    as_of_date TEXT NOT NULL,
    PRIMARY KEY(batch_id, market, symbol)
  );""",
        """CREATE TABLE IF NOT EXISTS dashboard_overview(
    batch_id         TEXT PRIMARY KEY,
    as_of_date       TEXT NOT NULL,
    total_positions  INTEGER NOT NULL,
    total_cost_value REAL NOT NULL,
    currency         TEXT NOT NULL DEFAULT 'CNY'
  );""",
    ),
}
READ_BY_STEP = {
    "015-04": {"security_master"},
    "015-05": {"security_master", "holding_config", "holding_config_meta"},
    "015-06": {
        "security_master", "holding_config", "holding_config_meta",
        "dashboard_position_snapshot", "dashboard_overview",
    },
}
WRITE_BY_STEP = {
    "015-04": {"security_master": {sqlite3.SQLITE_INSERT}},
    "015-05": {
        "holding_config": {sqlite3.SQLITE_INSERT, sqlite3.SQLITE_DELETE},
        "holding_config_meta": {
            sqlite3.SQLITE_INSERT, sqlite3.SQLITE_UPDATE, sqlite3.SQLITE_DELETE,
        },
    },
    "015-06": {
        "dashboard_position_snapshot": {sqlite3.SQLITE_INSERT},
        "dashboard_overview": {sqlite3.SQLITE_INSERT},
    },
}

_TABLE_DDL = {
    "holding_config": DDL_BY_STEP["015-05"][0],
    "holding_config_meta": DDL_BY_STEP["015-05"][1],
    "dashboard_position_snapshot": DDL_BY_STEP["015-06"][0],
    "dashboard_overview": DDL_BY_STEP["015-06"][1],
}
_SECURITY_SIGNATURE = {
    "market": ("TEXT", 1, None, 1),
    "symbol": ("TEXT", 1, None, 2),
    "name": ("TEXT", 1, None, 0),
    "security_type": ("TEXT", 1, None, 0),
    "lot_size": ("INTEGER", 1, None, 0),
    "currency": ("TEXT", 1, "'CNY'", 0),
    "list_date": ("TEXT", 0, None, 0),
    "status": ("TEXT", 1, None, 0),
}


def _text(value):
    require(isinstance(value, str) and bool(value.strip()), "EMPTY_REQUIRED_FIELD")
    require("\x00" not in value, "INVALID_TEXT_FIELD")
    return value


def _unique(rows):
    require(len({row[:2] for row in rows}) == len(rows), "DUPLICATE_SOURCE_KEY")


def _mills(value):
    """Convert a finite decimal monetary amount to integer thousandths of yuan exactly."""
    try:
        decimal = value if isinstance(value, Decimal) else Decimal(str(value))
        require(decimal.is_finite() and decimal >= 0, "INVALID_MONEY")
        # Decimal's default precision must not round a large product or amount.
        numerator, denominator = decimal.as_integer_ratio()
        numerator *= 1000
        require(numerator % denominator == 0, "INVALID_MONEY_PRECISION")
        return numerator // denominator
    except (InvalidOperation, ValueError, OverflowError):
        require(False, "INVALID_MONEY")


def _real(mills):
    """SQLite REAL must preserve the intended thousandths of yuan on round-trip."""
    try:
        result = float(f"{mills // 1000}.{mills % 1000:03d}")
        require(math.isfinite(result) and _mills(result) == mills,
                "SQLITE_REAL_PRECISION_LOSS")
        return result
    except (ValueError, OverflowError):
        require(False, "SQLITE_REAL_PRECISION_LOSS")


_SQL_QUOTED = r"'(?:''|[^'])*'|\"(?:\"\"|[^\"])*\"|`(?:``|[^`])*`|\[[^\]]*\]"
_SQL_COMMENT = r"--[^\r\n]*|/\*[\s\S]*?(?:\*/|$)"


def _strip_sql_comments(value):
    """Treat comments as whitespace without touching quoted SQL tokens."""
    return re.sub(
        _SQL_QUOTED + "|" + _SQL_COMMENT,
        lambda match: " " if match.group().startswith(("--", "/*")) else match.group(),
        value,
    )


def _normalize_ddl(value):
    # Preserve literal content: e.g. DEFAULT 'cny' must never bind as 'CNY'.
    tokens = re.split("(" + _SQL_QUOTED + ")", _strip_sql_comments(value))
    normalized = []
    for index, token in enumerate(tokens):
        if index % 2:
            normalized.append(token)
        else:
            token = re.sub(r"\bIF\s+NOT\s+EXISTS\b", "", token, flags=re.IGNORECASE)
            normalized.append(re.sub(r"\s+", "", token).upper())
    return "".join(normalized).rstrip(";")


def _checks(sql):
    """Extract executable CHECK clauses, ignoring comments and quoted tokens."""
    sql = _strip_sql_comments(sql)
    checks = []
    for match in re.finditer(_SQL_QUOTED + r"|\bCHECK\s*\(", sql, flags=re.IGNORECASE):
        if not match.group().upper().startswith("CHECK"):
            continue
        depth = 1
        for token in re.finditer(_SQL_QUOTED + r"|[()]", sql[match.end():]):
            if token.group() == "(":
                depth += 1
            elif token.group() == ")":
                depth -= 1
                if depth == 0:
                    checks.append(_normalize_ddl(sql[match.start():match.end() + token.end()]))
                    break
        require(depth == 0, "SECURITY_SCHEMA_CHECK_MISMATCH")
    return checks


def _schema(db, table):
    # table is always a module-owned constant, never a source value.
    row = db.execute(
        "SELECT type, sql FROM sqlite_master WHERE name = ?", (table,),
    ).fetchone()
    require(row is not None and row[0] == "table" and isinstance(row[1], str),
            "REQUIRED_TABLE_MISSING_OR_INVALID")
    if table == "security_master":
        columns = db.execute("PRAGMA table_xinfo(security_master)").fetchall()
        observed = {
            column[1]: (column[2].upper(), column[3], column[4], column[5])
            for column in columns
        }
        require(observed == _SECURITY_SIGNATURE and all(column[6] == 0 for column in columns),
                "SECURITY_SCHEMA_MISMATCH")
        require(sorted(_checks(row[1])) == sorted((
            "CHECK(MARKETIN('SSE','SZSE','BSE'))",
            "CHECK(SECURITY_TYPEIN('STOCK','ETF'))",
            "CHECK(LOT_SIZE>0)",
            "CHECK(STATUSIN('LISTED','SUSPENDED','DELISTED'))",
        )), "SECURITY_SCHEMA_CHECK_MISMATCH")
        unique_indexes = [item for item in db.execute(
            "PRAGMA index_list(security_master)"
        ).fetchall() if item[2]]
        require(len(unique_indexes) == 1 and unique_indexes[0][3] == "pk",
                "SECURITY_EXTRA_UNIQUE_CONSTRAINT")
        index_name = unique_indexes[0][1]
        require(index_name.startswith("sqlite_autoindex_security_master_"),
                "SECURITY_PRIMARY_KEY_INVALID")
        quoted_name = '"' + index_name.replace('"', '""') + '"'
        index_columns = [item[2] for item in db.execute(
            "PRAGMA index_info(" + quoted_name + ")"
        ).fetchall()]
        require(index_columns == ["market", "symbol"], "SECURITY_PRIMARY_KEY_INVALID")
    else:
        require(_normalize_ddl(row[1]) == _normalize_ddl(_TABLE_DDL[table]),
                "CANONICAL_SCHEMA_MISMATCH")


def _create(db, step):
    missing = sum(db.execute("SELECT 1 FROM sqlite_master WHERE name = ?", (table,))
                  .fetchone() is None for table in WRITE_BY_STEP[step])
    for statement in DDL_BY_STEP[step]:
        verify_create_sha256(statement, step)
        db.execute(statement)
    for table in WRITE_BY_STEP[step]:
        _schema(db, table)
    return missing


def prepare(step, args):
    """Read each CSV once, validate every row, and return only in-memory data."""
    if step == "015-06":
        return None
    columns = SECURITY_COLUMNS if step == "015-04" else HOLDING_COLUMNS
    source, source_sha256 = read_csv(args.source, columns)
    rows = []
    for record_number, record in enumerate(source, start=1):
        market, symbol = _text(record["market"]), _text(record["symbol"])
        if step == "015-04":
            require(market in {"SSE", "SZSE", "BSE"}, "INVALID_MARKET")
            require(record["security_type"] in {"STOCK", "ETF"},
                    "INVALID_SECURITY_TYPE")
            require(record["status"] in {"LISTED", "SUSPENDED", "DELISTED"},
                    "INVALID_SECURITY_STATUS")
            rows.append((
                market, symbol, _text(record["name"]), record["security_type"],
                integer(record["lot_size"], minimum=1),
                _currency(record["currency"] or "CNY",
                          record_scope="SECURITY_SOURCE", record_number=record_number,
                          source_sha256=source_sha256),
                valid_date(record["list_date"]) if record["list_date"] else None,
                record["status"],
            ))
        else:
            rows.append((market, symbol, integer(record["quantity"]),
                         _real(_mills(money(record["avg_cost"])))))
    _unique(rows)
    prepared = {"rows": sorted(rows), "source_sha256": source_sha256}
    if step == "015-05":
        prepared["as_of_date"] = valid_date(args.as_of_date)
    return prepared


def _security(db, prepared):
    # Recheck at the fact-table boundary, including callers of execute().
    for record_number, row in enumerate(prepared["rows"], start=1):
        _currency(row[5], record_scope="SECURITY_PREPARED", record_number=record_number,
                  source_sha256=prepared["source_sha256"])
    # The canonical CREATE has no IF NOT EXISTS; preserve validated repeats.
    created = 0
    if db.execute("SELECT 1 FROM sqlite_master WHERE name = ?",
                  ("security_master",)).fetchone() is None:
        created = _create(db, "015-04")
    else:
        _schema(db, "security_master")
    expected = {row[:2]: row for row in prepared["rows"]}
    current = {
        row[:2]: tuple(row) for row in db.execute(
            "SELECT market, symbol, name, security_type, lot_size, currency, "
            "list_date, status FROM security_master ORDER BY market, symbol"
        ).fetchall()
    }
    # The source is a complete snapshot; this contract grants no DELETE/UPDATE.
    require(set(current) <= set(expected), "SECURITY_SNAPSHOT_COUNT_MISMATCH")
    require(all(row == expected[key] for key, row in current.items()),
            "SECURITY_EXISTING_ROW_CONFLICT")
    inserts = [row for row in prepared["rows"] if row[:2] not in current]
    db.executemany(
        "INSERT INTO security_master "
        "(market, symbol, name, security_type, lot_size, currency, list_date, status) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?)", inserts,
    )
    observed = [tuple(row) for row in db.execute(
        "SELECT market, symbol, name, security_type, lot_size, currency, "
        "list_date, status FROM security_master ORDER BY market, symbol"
    ).fetchall()]
    require(observed == prepared["rows"], "SECURITY_POST_WRITE_MISMATCH")
    require(db.execute("SELECT COUNT(*) FROM security_master").fetchone()[0]
            == len(prepared["rows"]), "SECURITY_POST_WRITE_COUNT_MISMATCH")
    return {
        "status": "APPLIED" if inserts or created else "IDEMPOTENT_NOOP",
        "row_count": len(observed), "inserted": len(inserts),
        "skipped": len(current), "changed": len(inserts),
        "source_sha256": prepared["source_sha256"],
    }


def _metadata(db):
    return dict(db.execute("SELECT key, value FROM holding_config_meta").fetchall())


def _valid_metadata(meta):
    return (set(meta) == {"as_of_date", "version", "source_sha256"}
            and meta["version"] == "1.0"
            and isinstance(meta["source_sha256"], str)
            and re.fullmatch(r"[0-9a-f]{64}", meta["source_sha256"]) is not None)


def _holding(db, prepared):
    _schema(db, "security_master")
    for record_number, row in enumerate(prepared["rows"], start=1):
        security = db.execute(
            "SELECT status, currency FROM security_master WHERE market = ? AND symbol = ?",
            row[:2],
        ).fetchone()
        require(security is not None, "HOLDING_SECURITY_MISSING")
        require(security[0] != "DELISTED", "HOLDING_SECURITY_DELISTED")
        _currency(security[1], record_scope="HOLDING_PREPARED", record_number=record_number,
                  source_sha256=prepared["source_sha256"])
    created = _create(db, "015-05")
    current = [tuple(row) for row in db.execute(
        "SELECT market, symbol, quantity, avg_cost FROM holding_config "
        "ORDER BY market, symbol"
    ).fetchall()]
    old_meta = _metadata(db)
    same = (current == prepared["rows"] and _valid_metadata(old_meta)
            and old_meta["as_of_date"] == prepared["as_of_date"])
    if same:
        return {
            "status": "APPLIED" if created else "IDEMPOTENT_NOOP",
            "row_count": len(current),
            "changed": 0, "as_of_date": prepared["as_of_date"],
            "source_sha256": old_meta["source_sha256"],
        }
    db.execute("DELETE FROM holding_config")
    db.executemany(
        "INSERT INTO holding_config(market, symbol, quantity, avg_cost) "
        "VALUES (?, ?, ?, ?)", prepared["rows"],
    )
    meta = {"as_of_date": prepared["as_of_date"], "version": "1.0",
            "source_sha256": prepared["source_sha256"]}
    db.execute("DELETE FROM holding_config_meta WHERE key IS NULL OR key NOT IN (?, ?, ?)",
               tuple(meta))
    db.executemany(
        "INSERT INTO holding_config_meta(key, value) VALUES (?, ?) "
        "ON CONFLICT(key) DO UPDATE SET value = excluded.value", list(meta.items()),
    )
    observed = [tuple(row) for row in db.execute(
        "SELECT market, symbol, quantity, avg_cost FROM holding_config "
        "ORDER BY market, symbol"
    ).fetchall()]
    require(observed == prepared["rows"], "HOLDING_POST_WRITE_MISMATCH")
    require(_metadata(db) == meta, "HOLDING_META_POST_WRITE_MISMATCH")
    require(db.execute("SELECT COUNT(*) FROM holding_config").fetchone()[0]
            == len(prepared["rows"]), "HOLDING_POST_WRITE_COUNT_MISMATCH")
    return {
        "status": "APPLIED", "row_count": len(observed),
        "deleted": len(current), "inserted": len(observed),
        "changed": len(current) + len(observed),
        "as_of_date": prepared["as_of_date"],
        "source_sha256": prepared["source_sha256"],
    }


def _verify_batch(db, batch_id):
    overview = db.execute(
        "SELECT as_of_date, total_positions, total_cost_value, currency "
        "FROM dashboard_overview WHERE batch_id = ?", (batch_id,),
    ).fetchone()
    require(overview is not None, "DASHBOARD_OVERVIEW_MISSING")
    rows = [tuple(row) for row in db.execute(
        "SELECT market, symbol, name, quantity, avg_cost, cost_value, as_of_date "
        "FROM dashboard_position_snapshot WHERE batch_id = ? ORDER BY market, symbol",
        (batch_id,),
    ).fetchall()]
    require(overview[1] == len(rows) and overview[3] == "CNY",
            "DASHBOARD_POST_WRITE_COUNT_MISMATCH")
    total_mills = 0
    for row in rows:
        require(type(row[3]) is int and row[3] >= 0, "INVALID_STORED_QUANTITY")
        cost_mills = row[3] * _mills(row[4])
        require(_mills(row[5]) == cost_mills and row[6] == overview[0],
                "DASHBOARD_POST_WRITE_ROW_MISMATCH")
        total_mills += cost_mills
    require(_mills(overview[2]) == total_mills, "DASHBOARD_POST_WRITE_TOTAL_MISMATCH")
    sql_total = db.execute(
        "SELECT ROUND(COALESCE(SUM(cost_value), 0), 3) "
        "FROM dashboard_position_snapshot WHERE batch_id = ?", (batch_id,),
    ).fetchone()[0]
    require(_mills(sql_total) == total_mills, "DASHBOARD_SQL_SUM_PRECISION_LOSS")
    return overview, rows


def _dashboard(db):
    for table in ("security_master", "holding_config", "holding_config_meta"):
        _schema(db, table)
    meta = _metadata(db)
    require(_valid_metadata(meta), "HOLDING_METADATA_INVALID")
    as_of_date = valid_date(meta["as_of_date"])
    batch_id = "B" + as_of_date.replace("-", "") + "_" + meta["source_sha256"][:12]
    holding_count = db.execute("SELECT COUNT(*) FROM holding_config").fetchone()[0]
    joined = db.execute(
        "SELECT h.market, h.symbol, s.name, h.quantity, h.avg_cost, s.currency "
        "FROM holding_config AS h INNER JOIN security_master AS s "
        "ON h.market = s.market AND h.symbol = s.symbol ORDER BY h.market, h.symbol"
    ).fetchall()
    require(len(joined) == holding_count, "DASHBOARD_SECURITY_MISSING")
    projected = []
    total_mills = 0
    for record_number, (market, symbol, name, quantity, avg_cost, currency) in enumerate(
            joined, start=1):
        _currency(currency, record_scope="DASHBOARD_PROJECTION", record_number=record_number,
                  source_sha256=meta["source_sha256"])
        _text(market)
        _text(symbol)
        _text(name)
        require(type(quantity) is int and quantity >= 0, "INVALID_STORED_QUANTITY")
        avg_mills = _mills(avg_cost)
        cost_mills = quantity * avg_mills
        projected.append((market, symbol, name, quantity, _real(avg_mills),
                          _real(cost_mills), as_of_date))
        total_mills += cost_mills
    total_cost = _real(total_mills)
    created = _create(db, "015-06")
    exists = db.execute("SELECT 1 FROM dashboard_overview WHERE batch_id = ?",
                        (batch_id,)).fetchone()
    if exists:
        overview, observed = _verify_batch(db, batch_id)
        # Metadata's date/version/source identity selects this batch. A
        # self-consistent historical projection is not proof of current content.
        require(overview[0] == as_of_date and overview[1] == holding_count
                and _mills(overview[2]) == total_mills,
                "DASHBOARD_EXISTING_METADATA_MISMATCH")
        require(observed == projected, "DASHBOARD_EXISTING_PROJECTION_MISMATCH")
        return {
            "status": "APPLIED" if created else "IDEMPOTENT_NOOP",
            "row_count": holding_count, "changed": 0,
            "as_of_date": as_of_date, "source_sha256": meta["source_sha256"],
        }
    require(db.execute(
        "SELECT COUNT(*) FROM dashboard_position_snapshot WHERE batch_id = ?",
        (batch_id,),
    ).fetchone()[0] == 0, "DASHBOARD_ORPHAN_BATCH")
    # joined already has the contract's explicit ORDER BY market, symbol.
    db.executemany(
        "INSERT INTO dashboard_position_snapshot "
        "(batch_id, market, symbol, name, quantity, avg_cost, cost_value, as_of_date) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        [(batch_id, *row) for row in projected],
    )
    db.execute(
        "INSERT INTO dashboard_overview "
        "(batch_id, as_of_date, total_positions, total_cost_value, currency) "
        "VALUES (?, ?, ?, ?, ?)", (batch_id, as_of_date, holding_count, total_cost, "CNY"),
    )
    overview, observed = _verify_batch(db, batch_id)
    require(observed == projected and overview[1] == holding_count,
            "DASHBOARD_POST_WRITE_PROJECTION_MISMATCH")
    require(overview[0] == as_of_date and _mills(overview[2]) == total_mills,
            "DASHBOARD_POST_WRITE_OVERVIEW_MISMATCH")
    return {
        "status": "APPLIED", "row_count": holding_count, "changed": holding_count + 1,
        "as_of_date": as_of_date, "source_sha256": meta["source_sha256"],
    }


def execute(step, db, args, prepared):
    """Apply and verify one step; the caller commits or rolls back atomically."""
    if step == "015-04":
        return _security(db, prepared)
    if step == "015-05":
        return _holding(db, prepared)
    require(step == "015-06", "UNKNOWN_STEP")
    return _dashboard(db)
