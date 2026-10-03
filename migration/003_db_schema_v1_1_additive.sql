-- #012A production additive migration; exactly the frozen V1.1 contract.
CREATE TABLE analysis_tasks (
    id TEXT NOT NULL PRIMARY KEY,
    stock_id TEXT NOT NULL,
    task_date TEXT NOT NULL,
    prompt_version TEXT NOT NULL,
    model_version TEXT NOT NULL,
    status TEXT NOT NULL CHECK(length(status) > 0),
    created_by TEXT NOT NULL,
    created_at TEXT NOT NULL,
    run_evidence_json TEXT NOT NULL,
    validated_draft_json TEXT NOT NULL,
    prompt_envelope_json TEXT NOT NULL,
    raw_model_output TEXT NOT NULL
);

CREATE TABLE analysis_reports (
    id TEXT NOT NULL PRIMARY KEY,
    task_id TEXT NOT NULL REFERENCES analysis_tasks(id),
    market_view TEXT NOT NULL,
    position_status TEXT NOT NULL,
    risk_level TEXT NOT NULL,
    operation_plan TEXT NOT NULL,
    t_strategy TEXT NOT NULL,
    reasoning TEXT NOT NULL,
    confidence TEXT NOT NULL,
    human_confirm TEXT NOT NULL CHECK(human_confirm = 'CONFIRMED'),
    created_at TEXT NOT NULL,
    revision_id TEXT NOT NULL UNIQUE,
    model_version TEXT NOT NULL,
    calculation TEXT NOT NULL,
    evidence_ids TEXT NOT NULL,
    supersedes_analysis_report_id TEXT REFERENCES analysis_reports(id),
    final_snapshot_hash TEXT NOT NULL UNIQUE REFERENCES analysis_snapshots(snapshot_hash) DEFERRABLE INITIALLY DEFERRED,
    final_evidence_id TEXT NOT NULL UNIQUE REFERENCES analysis_final_evidence(final_evidence_id) DEFERRABLE INITIALLY DEFERRED,
    CHECK(supersedes_analysis_report_id IS NULL OR supersedes_analysis_report_id <> id)
);

CREATE TABLE evidence_records (
    id TEXT NOT NULL PRIMARY KEY,
    analysis_report_id TEXT REFERENCES analysis_reports(id) DEFERRABLE INITIALLY DEFERRED,
    source TEXT NOT NULL CHECK(source IN ('TUSHARE_MCP', 'TDX_OFFICIAL_LOCAL_HTTP', 'WORKBUDDY_TDX_MCP')),
    timestamp TEXT NOT NULL,
    raw_data_path TEXT,
    calculation TEXT,
    model_version TEXT,
    human_confirm TEXT NOT NULL CHECK(human_confirm IN ('PENDING', 'CONFIRMED', 'REJECTED')),
    collection_task_id TEXT,
    system_run_id TEXT,
    created_at TEXT NOT NULL,
    supersedes_evidence_id TEXT REFERENCES evidence_records(id),
    CHECK(supersedes_evidence_id IS NULL OR supersedes_evidence_id <> id)
);

CREATE TABLE analysis_confirmation_events (
    event_id TEXT NOT NULL PRIMARY KEY,
    analysis_run_id TEXT NOT NULL REFERENCES analysis_tasks(id),
    analysis_report_id TEXT NOT NULL,
    revision_id TEXT NOT NULL,
    target TEXT NOT NULL,
    reason_code TEXT NOT NULL,
    previous_state TEXT NOT NULL CHECK(previous_state IN ('PENDING', 'CONFIRMED', 'REJECTED')),
    current_state TEXT NOT NULL CHECK(current_state IN ('PENDING', 'CONFIRMED', 'REJECTED')),
    actor_id TEXT NOT NULL,
    decision_reason TEXT NOT NULL,
    created_at TEXT NOT NULL,
    override_id TEXT REFERENCES analysis_human_overrides(override_id) DEFERRABLE INITIALLY DEFERRED,
    human_decision TEXT NOT NULL CHECK(human_decision IN ('CONFIRM', 'REJECT', 'MODIFY')),
    reviewed_snapshot_hash TEXT REFERENCES analysis_snapshots(snapshot_hash) DEFERRABLE INITIALLY DEFERRED,
    event_sequence INTEGER NOT NULL CHECK(event_sequence >= 0),
    UNIQUE(analysis_report_id, revision_id, event_sequence)
);

CREATE TABLE analysis_human_overrides (
    override_id TEXT NOT NULL PRIMARY KEY,
    analysis_run_id TEXT NOT NULL REFERENCES analysis_tasks(id),
    analysis_report_id TEXT NOT NULL,
    revision_id TEXT NOT NULL,
    target TEXT NOT NULL,
    reason_code TEXT NOT NULL,
    original_value_hash TEXT NOT NULL,
    replacement TEXT NOT NULL,
    reason TEXT NOT NULL,
    actor_id TEXT NOT NULL,
    created_at TEXT NOT NULL,
    referenced_evidence_ids TEXT NOT NULL,
    origin TEXT NOT NULL CHECK(origin = 'HUMAN'),
    human_confirm TEXT NOT NULL CHECK(human_confirm = 'CONFIRMED')
);

CREATE TABLE analysis_snapshots (
    snapshot_hash TEXT NOT NULL PRIMARY KEY,
    snapshot_kind TEXT NOT NULL CHECK(snapshot_kind IN ('PREPARED', 'FINAL')),
    analysis_run_id TEXT NOT NULL REFERENCES analysis_tasks(id),
    analysis_report_id TEXT NOT NULL,
    revision_id TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    UNIQUE(analysis_report_id, revision_id, snapshot_kind),
    CHECK(length(snapshot_hash) = 64 AND snapshot_hash NOT GLOB '*[^0-9a-f]*')
);

CREATE TABLE analysis_final_evidence (
    final_evidence_id TEXT NOT NULL PRIMARY KEY,
    analysis_report_id TEXT NOT NULL UNIQUE REFERENCES analysis_reports(id) DEFERRABLE INITIALLY DEFERRED,
    analysis_run_id TEXT NOT NULL REFERENCES analysis_tasks(id),
    stock_id TEXT NOT NULL,
    model_version TEXT NOT NULL,
    input_hash TEXT NOT NULL,
    model_output_hash TEXT NOT NULL,
    validated_output_hash TEXT NOT NULL,
    final_snapshot_hash TEXT NOT NULL UNIQUE REFERENCES analysis_snapshots(snapshot_hash) DEFERRABLE INITIALLY DEFERRED,
    confirmation_log_hash TEXT NOT NULL,
    input_evidence_ids TEXT NOT NULL,
    used_evidence_ids TEXT NOT NULL,
    confirmed_by TEXT NOT NULL,
    confirmed_at TEXT NOT NULL,
    supersedes_final_evidence_id TEXT REFERENCES analysis_final_evidence(final_evidence_id),
    human_confirm TEXT NOT NULL CHECK(human_confirm = 'CONFIRMED'),
    CHECK(supersedes_final_evidence_id IS NULL OR supersedes_final_evidence_id <> final_evidence_id)
);

CREATE UNIQUE INDEX analysis_snapshots_one_final_per_report ON analysis_snapshots(analysis_report_id) WHERE snapshot_kind = 'FINAL';

CREATE TRIGGER analysis_tasks_no_duplicate_identity BEFORE INSERT ON analysis_tasks WHEN EXISTS (SELECT 1 FROM analysis_tasks WHERE id = NEW.id) BEGIN SELECT RAISE(ABORT, 'IMMUTABLE_IDENTITY'); END;

CREATE TRIGGER analysis_tasks_no_delete BEFORE DELETE ON analysis_tasks BEGIN SELECT RAISE(ABORT, 'DELETE_FORBIDDEN'); END;

CREATE TRIGGER analysis_tasks_no_update BEFORE UPDATE OF id, stock_id, task_date, prompt_version, model_version, created_by, created_at, run_evidence_json, validated_draft_json, prompt_envelope_json, raw_model_output ON analysis_tasks BEGIN SELECT RAISE(ABORT, 'UPDATE_FORBIDDEN'); END;

CREATE TRIGGER analysis_reports_no_duplicate_identity BEFORE INSERT ON analysis_reports WHEN EXISTS (SELECT 1 FROM analysis_reports WHERE id = NEW.id) BEGIN SELECT RAISE(ABORT, 'IMMUTABLE_IDENTITY'); END;

CREATE TRIGGER analysis_reports_no_delete BEFORE DELETE ON analysis_reports BEGIN SELECT RAISE(ABORT, 'DELETE_FORBIDDEN'); END;

CREATE TRIGGER analysis_reports_no_update BEFORE UPDATE ON analysis_reports BEGIN SELECT RAISE(ABORT, 'UPDATE_FORBIDDEN'); END;

CREATE TRIGGER evidence_records_no_duplicate_identity BEFORE INSERT ON evidence_records WHEN EXISTS (SELECT 1 FROM evidence_records WHERE id = NEW.id) BEGIN SELECT RAISE(ABORT, 'IMMUTABLE_IDENTITY'); END;

CREATE TRIGGER evidence_records_no_delete BEFORE DELETE ON evidence_records BEGIN SELECT RAISE(ABORT, 'DELETE_FORBIDDEN'); END;

CREATE TRIGGER evidence_records_no_update BEFORE UPDATE ON evidence_records BEGIN SELECT RAISE(ABORT, 'UPDATE_FORBIDDEN'); END;

CREATE TRIGGER analysis_confirmation_events_no_duplicate_identity BEFORE INSERT ON analysis_confirmation_events WHEN EXISTS (SELECT 1 FROM analysis_confirmation_events WHERE event_id = NEW.event_id) BEGIN SELECT RAISE(ABORT, 'IMMUTABLE_IDENTITY'); END;

CREATE TRIGGER analysis_confirmation_events_no_delete BEFORE DELETE ON analysis_confirmation_events BEGIN SELECT RAISE(ABORT, 'DELETE_FORBIDDEN'); END;

CREATE TRIGGER analysis_confirmation_events_no_update BEFORE UPDATE ON analysis_confirmation_events BEGIN SELECT RAISE(ABORT, 'UPDATE_FORBIDDEN'); END;

CREATE TRIGGER analysis_human_overrides_no_duplicate_identity BEFORE INSERT ON analysis_human_overrides WHEN EXISTS (SELECT 1 FROM analysis_human_overrides WHERE override_id = NEW.override_id) BEGIN SELECT RAISE(ABORT, 'IMMUTABLE_IDENTITY'); END;

CREATE TRIGGER analysis_human_overrides_no_delete BEFORE DELETE ON analysis_human_overrides BEGIN SELECT RAISE(ABORT, 'DELETE_FORBIDDEN'); END;

CREATE TRIGGER analysis_human_overrides_no_update BEFORE UPDATE ON analysis_human_overrides BEGIN SELECT RAISE(ABORT, 'UPDATE_FORBIDDEN'); END;

CREATE TRIGGER analysis_snapshots_no_duplicate_identity BEFORE INSERT ON analysis_snapshots WHEN EXISTS (SELECT 1 FROM analysis_snapshots WHERE snapshot_hash = NEW.snapshot_hash) BEGIN SELECT RAISE(ABORT, 'IMMUTABLE_IDENTITY'); END;

CREATE TRIGGER analysis_snapshots_no_delete BEFORE DELETE ON analysis_snapshots BEGIN SELECT RAISE(ABORT, 'DELETE_FORBIDDEN'); END;

CREATE TRIGGER analysis_snapshots_no_update BEFORE UPDATE ON analysis_snapshots BEGIN SELECT RAISE(ABORT, 'UPDATE_FORBIDDEN'); END;

CREATE TRIGGER analysis_final_evidence_no_duplicate_identity BEFORE INSERT ON analysis_final_evidence WHEN EXISTS (SELECT 1 FROM analysis_final_evidence WHERE final_evidence_id = NEW.final_evidence_id) BEGIN SELECT RAISE(ABORT, 'IMMUTABLE_IDENTITY'); END;

CREATE TRIGGER analysis_final_evidence_no_delete BEFORE DELETE ON analysis_final_evidence BEGIN SELECT RAISE(ABORT, 'DELETE_FORBIDDEN'); END;

CREATE TRIGGER analysis_final_evidence_no_update BEFORE UPDATE ON analysis_final_evidence BEGIN SELECT RAISE(ABORT, 'UPDATE_FORBIDDEN'); END;
