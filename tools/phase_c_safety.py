"""Offline Phase C transaction boundary; no implicit target or runtime services.

Only the named target, its rollback journal, and a verified preimage in the
explicit backup directory may be written. Dry runs execute on a memory clone.
Successful non-dry runs emit POST_WRITE_SHA256 after closing SQLite; runners
must pass that value as the next step's --expected-db-sha256.
"""
from __future__ import annotations

import argparse
from contextlib import closing
import csv
from datetime import date
from decimal import Decimal
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import re
import sqlite3
import stat
import sys

ROOT = Path(__file__).resolve().parents[1]
_spec = importlib.util.spec_from_file_location('phase_c_directory_gate', ROOT / 'tools/015_runtime_init.py')
_gate = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_gate)
require_safe_directory = _gate.require_safe_directory
GateError = _gate.GateError
require = _gate.require


# Independent pins over exact UTF-8 text, without stripping or normalization.
# security_master includes the canonical source file's final LF byte.
CREATE_SHA256_BY_STEP = {
    '015-04': ('20ee80403e222acb544790e8df9cea8bad6143777998a89c4c5320fd1c661565',),
    '015-05': (
        'b1eb4797677f786c61e1b413686edc80fdcbca62e75dabc67016b11de176717b',
        'e81abd28d2917ec1a631e005fbf66d23ace5dfc9cfa34eb7db7a7bc5cdf72de2',
    ),
    '015-06': (
        'a19aacc38e712e1b105b8280d97e93cc9ce4106df59d362aa55663c17e169589',
        'f81b9634b58a44d8359f07dbe0da93aed306ad22851707fbd8d6b09536eb4fd8',
    ),
}


def verify_create_sha256(statement, step):
    require(hashlib.sha256(statement.encode('utf-8')).hexdigest()
            in CREATE_SHA256_BY_STEP.get(step, ()), 'CREATE_SHA256_MISMATCH')


class Parser(argparse.ArgumentParser):
    def error(self, message):
        # argparse and SQLite diagnostics can contain private input values.
        raise GateError('CLI_ARGUMENTS_INVALID')


def file_sha256(path):
    digest = hashlib.sha256()
    with path.open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(block)
    return digest.hexdigest()


def safe_path(value, *, directory=False, must_exist=True):
    require(isinstance(value, (str, Path)), 'ABSOLUTE_PATH_REQUIRED')
    raw = str(value)
    path = Path(raw)
    require(path.is_absolute() and not raw.startswith('//') and '\\' not in raw
            and '..' not in path.parts, 'ABSOLUTE_LOCAL_PATH_REQUIRED')
    for parent in reversed(path.parents):
        require_safe_directory(parent, must_exist=must_exist)
    if directory:
        require_safe_directory(path, must_exist=must_exist)
    else:
        info = path.lstat()
        require(stat.S_ISREG(info.st_mode) and info.st_nlink == 1 and not
                (getattr(info, 'st_file_attributes', 0) & 0x400), 'FILE_UNSAFE')
    require(not path.is_relative_to(ROOT), 'RUNTIME_PATH_IN_REPOSITORY')
    return path


def create_safe_directory(path):
    for component in reversed((path, *path.parents)):
        require_safe_directory(component)
        if not component.exists():
            component.mkdir(mode=0o700)
        require_safe_directory(component, must_exist=True)


def valid_date(value):
    require(isinstance(value, str) and re.fullmatch(r'[0-9]{4}-[0-9]{2}-[0-9]{2}', value)
            is not None, 'DATE_INVALID')
    try:
        date.fromisoformat(value)
    except ValueError:
        raise GateError('DATE_INVALID') from None
    return value


def integer(value, minimum=0):
    require(isinstance(value, str) and re.fullmatch(r'[0-9]+', value) is not None,
            'INTEGER_INVALID')
    digits = value.lstrip('0') or '0'
    require(len(digits) <= 19, 'INTEGER_INVALID')
    result = int(digits)
    require(minimum <= result <= 2**63 - 1, 'INTEGER_INVALID')
    return result


def money(value):
    require(isinstance(value, str) and re.fullmatch(r'[0-9]+(?:\.[0-9]{1,2})?', value)
            is not None, 'MONEY_INVALID')
    result = Decimal(value)
    require(result.is_finite() and result >= 0, 'MONEY_INVALID')
    return result


def read_csv(path, columns):
    payload = Path(path).read_bytes()
    require(not payload.startswith(b'\xef\xbb\xbf'), 'CSV_BOM_FORBIDDEN')
    content = payload.decode('utf-8', errors='strict')
    require('\x00' not in content, 'CSV_INVALID')
    reader = csv.reader(io.StringIO(content, newline=''), strict=True)
    header = next(reader, None)
    require(header is not None and len(header) == len(columns)
            and set(header) == set(columns), 'CSV_HEADER_MISMATCH')
    rows = []
    for fields in reader:
        require(len(fields) == len(header), 'CSV_ROW_INVALID')
        rows.append(dict(zip(header, fields)))
    return rows, hashlib.sha256(payload).hexdigest()


def check_sidecars(path):
    for suffix in ('-wal', '-shm', '-journal'):
        require(not os.path.lexists(str(path) + suffix), 'SQLITE_SIDECAR_PRESENT')
    # immutable readers report journal_mode=delete even for checkpointed WAL
    # files. Refuse WAL format before opening SQLite, including on dry runs.
    with path.open('rb') as stream:
        header = stream.read(20)
    require(header[:16] == b'SQLite format 3\x00', 'SQLITE_HEADER_INVALID')
    require(header[18:20] == b'\x01\x01', 'ROLLBACK_JOURNAL_REQUIRED')


def inventory(db):
    return tuple(db.execute('SELECT type,name,tbl_name,sql FROM sqlite_master ORDER BY type,name'))


def schema_fingerprint(rows):
    return hashlib.sha256(json.dumps(rows, separators=(',', ':')).encode()).hexdigest()


class GuardedConnection(sqlite3.Connection):
    """Exact SQL gate for DDL plus SQLite's compiler gate for indirect writes."""

    def install_policy(self, step, contracts):
        self.step = step
        self.allowed_ddl = contracts.DDL_BY_STEP[step]
        self.read_tables = set(contracts.READ_BY_STEP[step]) | set(contracts.WRITE_BY_STEP[step])
        self.write_tables = contracts.WRITE_BY_STEP[step]
        self.active_ddl = False
        self.set_authorizer(self.authorize)

    def execute(self, sql, parameters=()):
        approved = sql in getattr(self, 'allowed_ddl', ())
        if approved:
            verify_create_sha256(sql, self.step)
        self.active_ddl = approved
        try:
            return super().execute(sql, parameters)
        finally:
            self.active_ddl = False

    def executescript(self, sql_script):
        raise GateError('SQL_SCRIPT_FORBIDDEN')

    def authorize(self, action, first, second, database, origin):
        allowed = False
        if origin is not None or database not in (None, 'main'):
            return sqlite3.SQLITE_DENY
        if action in (sqlite3.SQLITE_SELECT, sqlite3.SQLITE_TRANSACTION):
            allowed = True
        elif action == sqlite3.SQLITE_READ:
            allowed = first in self.read_tables | {'sqlite_master', 'sqlite_schema'}
        elif action in (sqlite3.SQLITE_INSERT, sqlite3.SQLITE_UPDATE, sqlite3.SQLITE_DELETE):
            allowed = action in self.write_tables.get(first, set())
            if first in ('sqlite_master', 'sqlite_schema'):
                allowed = self.active_ddl
        elif action == sqlite3.SQLITE_CREATE_TABLE:
            allowed = self.active_ddl and first in self.write_tables
        elif action == sqlite3.SQLITE_CREATE_INDEX:
            allowed = (self.active_ddl and first.startswith('sqlite_autoindex_')
                       and second in self.write_tables)
        elif action == sqlite3.SQLITE_PRAGMA:
            allowed = ((first in ('table_info', 'table_xinfo', 'index_list') and second in self.read_tables)
                       or (first == 'index_info' and second is not None and
                           second.startswith('sqlite_autoindex_security_master_') and
                           'security_master' in self.read_tables)
                       or (first in ('integrity_check', 'foreign_key_check') and second is None))
        elif action == sqlite3.SQLITE_FUNCTION:
            allowed = second in {'count', 'sum', 'round', 'coalesce', 'length', 'replace',
                                 'substr', 'min', 'max', 'typeof', 'abs'}
        return sqlite3.SQLITE_OK if allowed else sqlite3.SQLITE_DENY


def configure(db):
    db.execute('PRAGMA temp_store=MEMORY')
    db.execute('PRAGMA foreign_keys=ON')
    db.execute('PRAGMA recursive_triggers=ON')
    db.execute('PRAGMA trusted_schema=OFF')
    return db


def check_integrity(db):
    require(db.execute('PRAGMA integrity_check').fetchall() == [('ok',)], 'DATABASE_INTEGRITY_FAILURE')
    require(not db.execute('PRAGMA foreign_key_check').fetchall(), 'DATABASE_FOREIGN_KEY_FAILURE')


def verify_schema(before, after, ddls, step):
    # Build the canonical additions in memory. No baseline table DDL is issued.
    with closing(sqlite3.connect(':memory:')) as expected:
        for sql in ddls:
            verify_create_sha256(sql, step)
            expected.execute(sql)
        canonical = inventory(expected)
    require(set(before).issubset(set(after)), 'SCHEMA_CHANGED')
    additions = set(after) - set(before)
    new_tables = {row[1] for row in additions if row[0] == 'table'}
    required = {row for row in canonical if row[2] in new_tables}
    require(additions == required, 'SCHEMA_CHANGED')
    return schema_fingerprint(after)


def execute_checked(step, db, args, prepared, contracts):
    before = inventory(db)
    result = contracts.execute(step, db, args, prepared)
    verify_schema(before, inventory(db), contracts.DDL_BY_STEP[step], step)
    check_integrity(db)
    return result


def memory_preflight(step, path, args, prepared, contracts):
    check_sidecars(path)
    with closing(sqlite3.connect(path.as_uri() + '?mode=ro&immutable=1', uri=True)) as source, \
            closing(sqlite3.connect(':memory:', isolation_level=None,
                                    factory=GuardedConnection, cached_statements=0)) as memory:
        require(source.execute('PRAGMA journal_mode').fetchone()[0] == 'delete',
                'ROLLBACK_JOURNAL_REQUIRED')
        source.backup(memory)
        configure(memory)
        check_integrity(memory)
        memory.install_policy(step, contracts)
        memory.execute('BEGIN')
        try:
            result = execute_checked(step, memory, args, prepared, contracts)
        finally:
            memory.rollback()
    check_sidecars(path)
    require(file_sha256(path) == args.expected_db_sha256, 'DATABASE_HASH_MISMATCH')
    return result


def snapshot(path, backup_dir, step, expected_hash):
    target = backup_dir / f'{step}-{expected_hash}.sqlite'
    require(target != path, 'BACKUP_SOURCE_ALIAS')
    create_safe_directory(backup_dir)
    if not target.exists():
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, 'O_NOFOLLOW', 0)
        with os.fdopen(os.open(target, flags, 0o600), 'wb') as output, path.open('rb') as source:
            for block in iter(lambda: source.read(1024 * 1024), b''):
                output.write(block)
            output.flush()
            os.fsync(output.fileno())
    safe_path(target)
    check_sidecars(target)
    require(file_sha256(target) == expected_hash, 'BACKUP_HASH_MISMATCH')
    with closing(sqlite3.connect(target.as_uri() + '?mode=ro&immutable=1', uri=True)) as backup:
        check_integrity(backup)
    return target


def validate_backup(path, backup_dir, step, expected_hash, source=None):
    target = backup_dir / f'{step}-{expected_hash}.sqlite'
    require(target != path and target != source, 'BACKUP_SOURCE_ALIAS')
    if os.path.lexists(target):
        safe_path(target)
        check_sidecars(target)
        require(file_sha256(target) == expected_hash, 'BACKUP_HASH_MISMATCH')


def apply(step, path, args, prepared, contracts):
    check_sidecars(path)
    before_identity = path.stat()
    with closing(sqlite3.connect(path.as_uri() + '?mode=rw', uri=True, isolation_level=None,
                                timeout=0, factory=GuardedConnection, cached_statements=0)) as db:
        configure(db)
        require(db.execute('PRAGMA journal_mode').fetchone()[0] == 'delete',
                'ROLLBACK_JOURNAL_REQUIRED')
        db.execute('BEGIN EXCLUSIVE')
        try:
            safe_path(path)
            identity = path.stat()
            require((identity.st_dev, identity.st_ino) == (before_identity.st_dev, before_identity.st_ino),
                    'DATABASE_REPLACED')
            check_sidecars(path)
            require(file_sha256(path) == args.expected_db_sha256, 'DATABASE_HASH_MISMATCH')
            snapshot(path, args.backup_dir, step, args.expected_db_sha256)
            db.install_policy(step, contracts)
            result = execute_checked(step, db, args, prepared, contracts)
            db.commit()
            return result
        except BaseException:
            db.rollback()
            # The rollback journal restores the exact preimage, including DDL.
            require(file_sha256(path) == args.expected_db_sha256, 'ROLLBACK_VERIFICATION_FAILED')
            raise


def main(step, argv=None):
    from tools import phase_c_contracts as contracts
    try:
        parser = Parser(description='Offline Phase C engineering preparation')
        parser.add_argument('--auth-flag')
        parser.add_argument('--target-db')
        parser.add_argument('--expected-db-sha256')
        parser.add_argument('--backup-dir')
        parser.add_argument('--dry-run', action='store_true')
        if step != '015-06':
            parser.add_argument('--source')
        if step == '015-05':
            parser.add_argument('--as-of-date')
        args = parser.parse_args(argv)
        require(args.auth_flag == contracts.AUTH_BY_STEP[step],
                contracts.AUTH_BY_STEP[step].split('=')[0] + '_NOT_AUTHORIZED')
        require(isinstance(args.expected_db_sha256, str) and
                re.fullmatch('[a-f0-9]{64}', args.expected_db_sha256) is not None,
                'DATABASE_HASH_REQUIRED')
        path = safe_path(args.target_db)
        args.backup_dir = safe_path(args.backup_dir, directory=True, must_exist=False)
        check_sidecars(path)
        require(file_sha256(path) == args.expected_db_sha256, 'DATABASE_HASH_MISMATCH')
        if step != '015-06':
            args.source = safe_path(args.source)
            require(args.source != path, 'SOURCE_TARGET_COLLISION')
        validate_backup(path, args.backup_dir, step, args.expected_db_sha256,
                        getattr(args, 'source', None))
        prepared = contracts.prepare(step, args)
        result = memory_preflight(step, path, args, prepared, contracts)
        if not args.dry_run and result.get('status') != 'IDEMPOTENT_NOOP':
            result = apply(step, path, args, prepared, contracts)
        if args.dry_run and result.get('status') != 'IDEMPOTENT_NOOP':
            # These counts describe the memory rehearsal, never a target write.
            result = dict(result)
            for key in ('changed', 'inserted', 'deleted'):
                if key in result:
                    result['planned_' + key] = result.pop(key)
            result.update(status='DRY_RUN_VALIDATED', changed=0)
        post_write_sha256 = None
        if not args.dry_run:
            # apply() and memory_preflight() have closed all SQLite connections.
            # No-op successes also provide a handoff, but must remain unchanged.
            safe_path(path)
            check_sidecars(path)
            post_write_sha256 = file_sha256(path)
            if result.get('status') == 'IDEMPOTENT_NOOP':
                require(post_write_sha256 == args.expected_db_sha256, 'DATABASE_HASH_MISMATCH')
        print(json.dumps({'step': step, 'result': 'PASS', 'dry_run': args.dry_run, **result}, sort_keys=True))
        if post_write_sha256 is not None:
            print('POST_WRITE_SHA256=' + post_write_sha256)
        return 0
    except Exception as exc:
        code = str(exc) if isinstance(exc, GateError) and re.fullmatch('[A-Z0-9_]+', str(exc)) else 'VALIDATION_OR_WRITE_FAILED'
        print(json.dumps({'step': step, 'result': 'FAIL', 'error': code}, sort_keys=True))
        return 1
