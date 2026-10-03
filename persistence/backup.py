"""WAL-safe online backups and copy-only rehearsal. No file-copy fallback."""
from datetime import datetime, timezone
import hashlib
from pathlib import Path
import sqlite3
import time

from .connection import configure, open_database
from .contracts import require
from .migration import apply_migration, checks, legacy_digest, preflight, schema_fingerprint


def file_sha256(path):
    result = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            result.update(chunk)
    return result.hexdigest()


def wal_safe_backup(source, destination, *, timeout_seconds=30):
    require(not source.in_transaction, "BACKUP_SOURCE_TRANSACTION_FORBIDDEN")
    require(type(timeout_seconds) in (int, float) and 0 < timeout_seconds <= 300,
            "BOUNDED_BACKUP_TIMEOUT_REQUIRED")
    destination = Path(destination).resolve()
    require(not destination.exists(), "BACKUP_OVERWRITE_FORBIDDEN")
    checks(source)
    before = schema_fingerprint(source)
    # Exclusive creation prevents accidental overwriting or source aliasing.
    with destination.open("xb"):
        pass
    target = None
    deadline = time.monotonic() + timeout_seconds

    def progress(status, remaining, total):
        require(time.monotonic() < deadline, "ONLINE_BACKUP_TIMEOUT")

    try:
        target = sqlite3.connect(destination, isolation_level=None)
        source.backup(target, pages=128, progress=progress, sleep=0.05)
        configure(target)
        result = checks(target)
        after = schema_fingerprint(target)
        require(before == after == schema_fingerprint(source), "BACKUP_SCHEMA_MISMATCH")
        target.close()
        target = None
        result.update(backup_path=str(destination), backup_size=destination.stat().st_size,
                      backup_sha256=file_sha256(destination),
                      backup_timestamp=datetime.now(timezone.utc).isoformat(),
                      source_schema_fingerprint=before, backup_schema_fingerprint=after)
        # Reopen after close: also proves the backup is independently restorable.
        restored = open_database(destination, readonly=True)
        try:
            checks(restored)
            require(schema_fingerprint(restored) == before, "BACKUP_REOPEN_MISMATCH")
        finally:
            restored.close()
        require(file_sha256(destination) == result["backup_sha256"], "BACKUP_HASH_MISMATCH")
        return result
    except BaseException:
        if target is not None:
            target.close()
        # Leave a failed artifact for inspection, never claim it is a backup.
        raise


def rehearse_backup(backup_path, rehearsal_path, *, expected_sha256, require_legacy_32=True):
    require(file_sha256(backup_path) == expected_sha256, "BACKUP_HASH_MISMATCH")
    backup = open_database(backup_path, readonly=True)
    try:
        preflight(backup, require_legacy_32=require_legacy_32)
        wal_safe_backup(backup, rehearsal_path)
    finally:
        backup.close()
    rehearsal = open_database(rehearsal_path)
    try:
        before = legacy_digest(rehearsal)
        result = apply_migration(rehearsal, require_legacy_32=require_legacy_32)
        require(before == legacy_digest(rehearsal), "LEGACY_CHANGED")
        return {"migration": result, "legacy_schema": "UNCHANGED",
                "legacy_row_count_digest": "UNCHANGED", **checks(rehearsal)}
    finally:
        rehearsal.close()
