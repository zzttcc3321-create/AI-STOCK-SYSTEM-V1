"""Frozen V1.1 schema contract, exclusively for tests using SQLite :memory:.

This module exports DDL text; it imports no database driver, opens no connection,
and applies no migration. Production SQL/repositories belong to #012. These are
the seven explicitly authorized NEW tables, never a reconstruction of G0's 32
legacy tables. See docs/frozen/DB_SCHEMA_V1.1.md for payload/transaction rules.
"""

SCHEMA_VERSION = "V1.1"
MIGRATION_POLICY = "ADDITIVE_ONLY"
TEST_DATABASE = ":memory:"
NEW_TABLES = (
    "analysis_tasks", "analysis_reports", "evidence_records",
    "analysis_confirmation_events", "analysis_human_overrides",
    "analysis_snapshots", "analysis_final_evidence",
)
INSERT_ONLY_TABLES = (
    "evidence_records", "analysis_confirmation_events", "analysis_human_overrides",
    "analysis_snapshots", "analysis_final_evidence",
)

# Each entry is an ordered column definition, followed by table constraints.
# JSON is canonical UTF-8 TEXT, validated by #012 before entering a transaction;
# SQLite's json_valid alone cannot enforce duplicate-key/hash/domain invariants.
TABLE_DEFINITIONS = {
    "analysis_tasks": (
        "id TEXT NOT NULL PRIMARY KEY",
        "stock_id TEXT NOT NULL",
        "task_date TEXT NOT NULL",
        "prompt_version TEXT NOT NULL",
        "model_version TEXT NOT NULL",
        "status TEXT NOT NULL CHECK(length(status) > 0)",
        "created_by TEXT NOT NULL",
        "created_at TEXT NOT NULL",
        "run_evidence_json TEXT NOT NULL",
        "validated_draft_json TEXT NOT NULL",
        "prompt_envelope_json TEXT NOT NULL",
        "raw_model_output TEXT NOT NULL",
    ),
    "analysis_reports": (
        "id TEXT NOT NULL PRIMARY KEY",
        "task_id TEXT NOT NULL REFERENCES analysis_tasks(id)",
        "market_view TEXT NOT NULL",
        "position_status TEXT NOT NULL",
        "risk_level TEXT NOT NULL",
        "operation_plan TEXT NOT NULL",
        "t_strategy TEXT NOT NULL",
        "reasoning TEXT NOT NULL",
        "confidence TEXT NOT NULL",
        "human_confirm TEXT NOT NULL CHECK(human_confirm = 'CONFIRMED')",
        "created_at TEXT NOT NULL",
        "revision_id TEXT NOT NULL UNIQUE",
        "model_version TEXT NOT NULL",
        "calculation TEXT NOT NULL",
        "evidence_ids TEXT NOT NULL",
        "supersedes_analysis_report_id TEXT REFERENCES analysis_reports(id)",
        "final_snapshot_hash TEXT NOT NULL UNIQUE REFERENCES analysis_snapshots(snapshot_hash) DEFERRABLE INITIALLY DEFERRED",
        "final_evidence_id TEXT NOT NULL UNIQUE REFERENCES analysis_final_evidence(final_evidence_id) DEFERRABLE INITIALLY DEFERRED",
        "CHECK(supersedes_analysis_report_id IS NULL OR supersedes_analysis_report_id <> id)",
    ),
    "evidence_records": (
        "id TEXT NOT NULL PRIMARY KEY",
        "analysis_report_id TEXT REFERENCES analysis_reports(id) DEFERRABLE INITIALLY DEFERRED",
        "source TEXT NOT NULL CHECK(source IN ('TUSHARE_MCP', 'TDX_OFFICIAL_LOCAL_HTTP', 'WORKBUDDY_TDX_MCP'))",
        "timestamp TEXT NOT NULL",
        "raw_data_path TEXT",
        "calculation TEXT",
        "model_version TEXT",
        "human_confirm TEXT NOT NULL CHECK(human_confirm IN ('PENDING', 'CONFIRMED', 'REJECTED'))",
        "collection_task_id TEXT",
        "system_run_id TEXT",
        "created_at TEXT NOT NULL",
        "supersedes_evidence_id TEXT REFERENCES evidence_records(id)",
        "CHECK(supersedes_evidence_id IS NULL OR supersedes_evidence_id <> id)",
    ),
    "analysis_confirmation_events": (
        "event_id TEXT NOT NULL PRIMARY KEY",
        "analysis_run_id TEXT NOT NULL REFERENCES analysis_tasks(id)",
        "analysis_report_id TEXT NOT NULL",
        "revision_id TEXT NOT NULL",
        "target TEXT NOT NULL",
        "reason_code TEXT NOT NULL",
        "previous_state TEXT NOT NULL CHECK(previous_state IN ('PENDING', 'CONFIRMED', 'REJECTED'))",
        "current_state TEXT NOT NULL CHECK(current_state IN ('PENDING', 'CONFIRMED', 'REJECTED'))",
        "actor_id TEXT NOT NULL",
        "decision_reason TEXT NOT NULL",
        "created_at TEXT NOT NULL",
        "override_id TEXT REFERENCES analysis_human_overrides(override_id) DEFERRABLE INITIALLY DEFERRED",
        "human_decision TEXT NOT NULL CHECK(human_decision IN ('CONFIRM', 'REJECT', 'MODIFY'))",
        "reviewed_snapshot_hash TEXT REFERENCES analysis_snapshots(snapshot_hash) DEFERRABLE INITIALLY DEFERRED",
        "event_sequence INTEGER NOT NULL CHECK(event_sequence >= 0)",
        "UNIQUE(analysis_report_id, revision_id, event_sequence)",
    ),
    "analysis_human_overrides": (
        "override_id TEXT NOT NULL PRIMARY KEY",
        "analysis_run_id TEXT NOT NULL REFERENCES analysis_tasks(id)",
        "analysis_report_id TEXT NOT NULL",
        "revision_id TEXT NOT NULL",
        "target TEXT NOT NULL",
        "reason_code TEXT NOT NULL",
        "original_value_hash TEXT NOT NULL",
        "replacement TEXT NOT NULL",
        "reason TEXT NOT NULL",
        "actor_id TEXT NOT NULL",
        "created_at TEXT NOT NULL",
        "referenced_evidence_ids TEXT NOT NULL",
        "origin TEXT NOT NULL CHECK(origin = 'HUMAN')",
        "human_confirm TEXT NOT NULL CHECK(human_confirm = 'CONFIRMED')",
    ),
    "analysis_snapshots": (
        "snapshot_hash TEXT NOT NULL PRIMARY KEY",
        "snapshot_kind TEXT NOT NULL CHECK(snapshot_kind IN ('PREPARED', 'FINAL'))",
        "analysis_run_id TEXT NOT NULL REFERENCES analysis_tasks(id)",
        "analysis_report_id TEXT NOT NULL",
        "revision_id TEXT NOT NULL",
        "payload_json TEXT NOT NULL",
        "UNIQUE(analysis_report_id, revision_id, snapshot_kind)",
        "CHECK(length(snapshot_hash) = 64 AND snapshot_hash NOT GLOB '*[^0-9a-f]*')",
    ),
    "analysis_final_evidence": (
        "final_evidence_id TEXT NOT NULL PRIMARY KEY",
        "analysis_report_id TEXT NOT NULL UNIQUE REFERENCES analysis_reports(id) DEFERRABLE INITIALLY DEFERRED",
        "analysis_run_id TEXT NOT NULL REFERENCES analysis_tasks(id)",
        "stock_id TEXT NOT NULL",
        "model_version TEXT NOT NULL",
        "input_hash TEXT NOT NULL",
        "model_output_hash TEXT NOT NULL",
        "validated_output_hash TEXT NOT NULL",
        "final_snapshot_hash TEXT NOT NULL UNIQUE REFERENCES analysis_snapshots(snapshot_hash) DEFERRABLE INITIALLY DEFERRED",
        "confirmation_log_hash TEXT NOT NULL",
        "input_evidence_ids TEXT NOT NULL",
        "used_evidence_ids TEXT NOT NULL",
        "confirmed_by TEXT NOT NULL",
        "confirmed_at TEXT NOT NULL",
        "supersedes_final_evidence_id TEXT REFERENCES analysis_final_evidence(final_evidence_id)",
        "human_confirm TEXT NOT NULL CHECK(human_confirm = 'CONFIRMED')",
        "CHECK(supersedes_final_evidence_id IS NULL OR supersedes_final_evidence_id <> final_evidence_id)",
    ),
}


def _test_ddl() -> str:
    """Build text only. Deliberately no API accepting a connection or DB path."""
    statements = [
        "-- TEST CONTRACT ONLY: execute exclusively on SQLite :memory:.",
        "PRAGMA foreign_keys = ON;",
        "PRAGMA recursive_triggers = ON;",
    ]
    for table in NEW_TABLES:
        statements.append(
            "CREATE TABLE " + table + " (\n    "
            + ",\n    ".join(TABLE_DEFINITIONS[table]) + "\n);"
        )
    statements.append(
        "CREATE UNIQUE INDEX analysis_snapshots_one_final_per_report "
        "ON analysis_snapshots(analysis_report_id) WHERE snapshot_kind = 'FINAL';"
    )
    # Reports are immutable too; the named five-table INSERT_ONLY list is exact.
    # Blocking duplicate identities BEFORE INSERT also forbids REPLACE bypasses.
    keys = {"analysis_confirmation_events": "event_id",
            "analysis_human_overrides": "override_id",
            "analysis_snapshots": "snapshot_hash",
            "analysis_final_evidence": "final_evidence_id"}
    for table in NEW_TABLES:
        key = keys.get(table, "id")
        statements.append(
            f"CREATE TRIGGER {table}_no_duplicate_identity BEFORE INSERT ON {table} "
            f"WHEN EXISTS (SELECT 1 FROM {table} WHERE {key} = NEW.{key}) "
            "BEGIN SELECT RAISE(ABORT, 'IMMUTABLE_IDENTITY'); END;"
        )
        statements.append(
            f"CREATE TRIGGER {table}_no_delete BEFORE DELETE ON {table} "
            "BEGIN SELECT RAISE(ABORT, 'DELETE_FORBIDDEN'); END;"
        )
        if table == "analysis_tasks":
            immutable = ", ".join(
                column.split()[0] for column in TABLE_DEFINITIONS[table]
                if column.split()[0] != "status"
            )
            update = f"UPDATE OF {immutable}"
        else:
            update = "UPDATE"
        statements.append(
            f"CREATE TRIGGER {table}_no_update BEFORE {update} ON {table} "
            "BEGIN SELECT RAISE(ABORT, 'UPDATE_FORBIDDEN'); END;"
        )
    return "\n\n".join(statements) + "\n"


TEST_SCHEMA_DDL = _test_ddl()
