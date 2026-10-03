# #012A Windows Local Migration Runbook

**本轮停在 REAL_DB_PREAPPLY_GATE。未收到 qi 原文 `APPLY_REAL_DB=YES`，不得对 SOURCE_OF_TRUTH 提交 DDL。**
本次 dev box 没有连接任何真实业务库。以下 Windows 命令/结果全部待本机执行，不是已完成的证据。

```text
WINDOWS_LOCAL_STEPS=TO_BE_EXECUTED_ON_WINDOWS
REAL_DB_APPLY=WAITING_QI
REAL_DB_MIGRATION=NOT_AUTHORIZED
REAL_PROVIDER_EVIDENCE_RUNTIME_WRITE=DISABLED
EVIDENCE_METADATA_READER=DEFERRED_TO_#012B
```

## WINDOWS_LOCAL_STEPS（待办）

1. 在 Windows 本机检出本 PR 的已审核 commit；保持现有应用停止写入，保存现场。使用**真实 Windows 配置的实际解析方式**得到事实库绝对路径，并记录配置来源/解析方式（本仓库没有该运行配置；禁止猜路径或硬编码 stock_analysis.db）。确认不是 dev box/AWS DB，也不是此前 inventory 的猜测路径。
2. G0 用 `open_database(path, readonly=True)`。记录 path/existence/file size/SQLite version/journal_mode/WAL-SHM presence/quick_check/integrity_check/foreign_key_check/sqlite_master/schema fingerprint。只允许 legacy=32、V1.1=0、STATE=A；部分表、未知漂移、integrity/FK 失败立即 STOP_AND_REPORT。STATE=B 在 runner 中为 ALREADY_APPLIED，但此首次真实应用 gate 必须停止报告已存在状态。
3. 用 `wal_safe_backup` 到 operator 选定的**全新**独立备份文件（不进入 Git），记录所有返回 metadata；备份是 Online Backup API，禁止文件复制源 DB 或自动覆盖备份。保持源读取连接只读。
4. 校验备份 SHA-256，并以另一份 online-backup rehearsal copy 迁移。重开独立备份做 integrity/fingerprint 恢复演练；备份原件 SHA-256 必须不变。rehearsal copy 可写，仅在系统临时目录执行离线合成 repository/read E2E；绝不向 SOURCE_OF_TRUTH 插入测试行。
5. 在 Windows 执行 `python -m unittest discover -s tests -q`，记录本机真实 FULL_TEST 与 rollback tests。下方脚本可用于 G0、backup、copy rehearsal；本机测试未通过、无法证明现场一致性，或任一检查异常即停止。
6. 用实际输出填完 REAL_DB_PREAPPLY_GATE；所有 Windows 值填完且通过才可在本机另报 READY_TO_APPLY_REAL_DB=YES，然后 STOP，等 qi 明确 `APPLY_REAL_DB=YES`。#012A readiness 仅指 schema/core，Evidence metadata/archive 仍未实现。
7. **只有 qi 明确批准后**：在同一 Windows 配置重新解析 source path，重新 G0 验证与比较之前源 schema/legacy digests/备份 hash，开启专用 writable connection，`apply_migration(db, require_legacy_32=True)` 一次。不得调用 repository 给真实库写任何合成数据。
8. 提交后立即检查 total=39、STATE=B、七表完全匹配、integrity/FK PASS；旧 32 表 schema/index/row-count digest 与应用前一致，只输出 UNCHANGED/CHANGED，不输出业务计数。失败后 `SYSTEM_STATUS=MIGRATION_FAILED / STOP_AND_REPORT / NO_FURTHER_WRITES`；不自动 restore 或覆盖源库，交 qi 决定。
9. 即使 schema 实际应用成功，分层报告仍须 `PROVIDER_EVIDENCE_ARCHIVE_IMPLEMENTED=NO / EVIDENCE_METADATA_RUNTIME_READ=NO / FULL_EVIDENCE_PERSISTENCE_READY=NO`；真实 Provider 写入仍禁用。

## 本机只读源 + 副本脚本（待执行）

在仓库根目录运行下方 Python。`input` 的 source path 必须已经从真实配置解析，绝不是该脚本的路径猜测；备份路径由 operator 指定，必须不存在。保存输出到 Git 之外，禁止提交业务 DB/backup。

```python
import json
from pathlib import Path
import sqlite3
import sys
import tempfile

from persistence.backup import file_sha256, wal_safe_backup, rehearse_backup
from persistence.connection import open_database
from persistence.contracts import require
from persistence.migration import (MIGRATION_FILE, checks, legacy_digest,
                                   preflight, schema_inventory, schema_fingerprint)

require(sys.platform == "win32", "WINDOWS_LOCAL_REQUIRED")
fact_db_path = Path(input("从真实 Windows 配置解析的 DB 绝对路径: "))
require(fact_db_path.is_absolute(), "ABSOLUTE_RESOLVED_CONFIG_PATH_REQUIRED")
fact_db_path = fact_db_path.resolve(strict=True)
require(fact_db_path.is_file(), "SOURCE_FILE_REQUIRED")
backup_path = Path(input("独立全新 WAL-safe 备份绝对路径: "))
require(backup_path.is_absolute(), "ABSOLUTE_BACKUP_PATH_REQUIRED")
source = open_database(fact_db_path, readonly=True)
try:
    g0 = preflight(source, require_legacy_32=True)
    require(g0["state"] == "A" and g0["v1_1_table_count"] == 0, "STOP_AND_REPORT_V1_1_PRESENT")
    require(g0["journal_mode"] == "wal", "STOP_AND_REPORT_EXPECTED_WAL")
    source_before = legacy_digest(source)
    source_hash_before = file_sha256(fact_db_path)
    inventory = schema_inventory(source)  # schema metadata only, no row data/counts
    physical = {
        "FACT_DB_PATH": str(fact_db_path), "FILE_EXISTS": True,
        "FILE_SIZE": fact_db_path.stat().st_size, "SQLITE_VERSION": sqlite3.sqlite_version,
        "WAL_PRESENT": Path(str(fact_db_path) + "-wal").exists(),
        "SHM_PRESENT": Path(str(fact_db_path) + "-shm").exists(),
        "SOURCE_DB_SHA256": source_hash_before,
    }
    backup = wal_safe_backup(source, backup_path)
    with tempfile.TemporaryDirectory(prefix="persistence012-rehearsal-") as directory:
        rehearsal_path = Path(directory) / "rehearsal.sqlite"
        rehearsal = rehearse_backup(backup_path, rehearsal_path,
                                    expected_sha256=backup["backup_sha256"])
        copy = open_database(rehearsal_path)
        try:
            # Offline synthetic fixture writes exclusively to the rehearsal copy.
            sys.path.insert(0, str(Path("tests").resolve()))
            from test_persistence_schema_v1_1 import fixture
            from test_persistence_repository import persist_run, persist_final
            from persistence.repository import SQLitePersistenceRepository
            from persistence.read_models import (AnalysisReadRepository, EvidenceReadRepository,
                                                 ConfirmationReadRepository)
            execution, flow, prepared, final, fixture_rows = fixture()
            before = legacy_digest(copy)
            repo = SQLitePersistenceRepository(copy, allow_synthetic_evidence=True)
            persist_run(repo, execution, flow, fixture_rows)
            persist_final(repo, flow, prepared, final)
            require(AnalysisReadRepository(copy).latest_confirmed_analysis(flow.run_evidence.stock_id) is not None,
                    "REPOSITORY_E2E_FAILED")
            require(EvidenceReadRepository(copy).for_analysis(flow.analysis_report_id)["integrity_status"] == "NOT_VERIFIED",
                    "EVIDENCE_LIMITATION_FAILED")
            require(ConfirmationReadRepository(copy).confirmation_state(flow.analysis_report_id, flow.revision_id)
                    ["current_confirm_state"] == "CONFIRMED", "CONFIRMATION_E2E_FAILED")
            require(before == legacy_digest(copy), "REHEARSAL_LEGACY_CHANGED")
            checks(copy)
        finally:
            copy.close()
    restore = open_database(backup_path, readonly=True)
    try:
        checks(restore)
        require(schema_fingerprint(restore) == backup["backup_schema_fingerprint"], "RESTORE_REHEARSAL_FAILED")
    finally:
        restore.close()
    require(file_sha256(backup_path) == backup["backup_sha256"], "BACKUP_HASH_CHANGED")
    require(legacy_digest(source) == source_before and file_sha256(fact_db_path) == source_hash_before,
            "SOURCE_CHANGED_DURING_PREFLIGHT")
    print(json.dumps({"G0": g0, "physical": physical, "checks": checks(source),
                      "sqlite_master": inventory, "backup": backup, "rehearsal": rehearsal,
                      "REPOSITORY_E2E": "PASS", "EVIDENCE_INTEGRITY_STATUS": "NOT_VERIFIED",
                      "REAL_DB_APPLY": "WAITING_QI", "FULL_TEST": "RUN_SEPARATELY_ON_WINDOWS",
                      "READY_TO_APPLY_REAL_DB": "NO_UNTIL_WINDOWS_TESTS_AND_QI_GATE"},
                     ensure_ascii=False, indent=2))
finally:
    source.close()
```

SOURCE_DB_SHA256 是 DB 文件自身指纹，不代表独立 WAL 的合成逻辑数据 hash；恢复依据是经 Online Backup API 完整验证的备份。若现场仍有并发写入或无法证明稳定性，停止并重新做 G0/备份/gate。

## 当前交付 REAL_DB_PREAPPLY_GATE

```text
REAL_DB_PREAPPLY_GATE
FACT_DB_PATH=TO_BE_EXECUTED_ON_WINDOWS
SOURCE_DB_SHA256=TO_BE_EXECUTED_ON_WINDOWS
JOURNAL_MODE=TO_BE_EXECUTED_ON_WINDOWS
QUICK_CHECK=TO_BE_EXECUTED_ON_WINDOWS
INTEGRITY_CHECK=TO_BE_EXECUTED_ON_WINDOWS
FOREIGN_KEY_CHECK=TO_BE_EXECUTED_ON_WINDOWS
LEGACY_TABLE_COUNT=TO_BE_EXECUTED_ON_WINDOWS
V1_1_TABLE_COUNT_BEFORE=TO_BE_EXECUTED_ON_WINDOWS
SOURCE_SCHEMA_FINGERPRINT=TO_BE_EXECUTED_ON_WINDOWS
WAL_SHM_PRESENCE=TO_BE_EXECUTED_ON_WINDOWS
BACKUP_PATH=TO_BE_EXECUTED_ON_WINDOWS
BACKUP_SIZE=TO_BE_EXECUTED_ON_WINDOWS
BACKUP_TIMESTAMP=TO_BE_EXECUTED_ON_WINDOWS
BACKUP_SHA256=TO_BE_EXECUTED_ON_WINDOWS
BACKUP_SCHEMA_FINGERPRINT=TO_BE_EXECUTED_ON_WINDOWS
BACKUP_INTEGRITY_CHECK=TO_BE_EXECUTED_ON_WINDOWS
BACKUP_FOREIGN_KEY_CHECK=TO_BE_EXECUTED_ON_WINDOWS
RESTORE_REHEARSAL=TO_BE_EXECUTED_ON_WINDOWS
MIGRATION_FILE=migration/003_db_schema_v1_1_additive.sql
MIGRATION_SHA256=0b8055ce4326f40ff0245e8be2e9f45946ee6fa576c219c0bd499ec1848b6d1d
REHEARSAL_ON_BACKUP_COPY=TO_BE_EXECUTED_ON_WINDOWS
FULL_TEST=584/584_PASS_TEMPORARY_SQLITE
WINDOWS_FULL_TEST=TO_BE_EXECUTED_ON_WINDOWS
REPOSITORY_E2E=PASS_TEMPORARY_SYNTHETIC_SQLITE
WINDOWS_REPOSITORY_E2E=TO_BE_EXECUTED_ON_WINDOWS
ROLLBACK_INJECTION=PASS_TEMPORARY_SYNTHETIC_SQLITE
WINDOWS_ROLLBACK_INJECTION=TO_BE_EXECUTED_ON_WINDOWS
LEGACY_SCHEMA_DIFF=TO_BE_EXECUTED_ON_WINDOWS
LEGACY_ROW_COUNT_DIGEST=TO_BE_EXECUTED_ON_WINDOWS
EXPECTED_NEW_TABLES=7
READY_TO_APPLY_REAL_DB=NO_WINDOWS_GATE_PENDING
DB_SCHEMA_V1_1_APPLIED=TEMPORARY_TEST_DATABASES_ONLY
REPOSITORY_CORE_IMPLEMENTED=YES
READ_MODEL_CORE_IMPLEMENTED=YES
PROVIDER_EVIDENCE_ARCHIVE_IMPLEMENTED=NO
EVIDENCE_METADATA_RUNTIME_READ=NO
FULL_EVIDENCE_TRACE_COMPLETE=NO
FULL_EVIDENCE_PERSISTENCE_READY=NO
REAL_DB_APPLY=WAITING_QI
APPLY_REAL_DB=NOT_RECEIVED
STOP=YES
```
