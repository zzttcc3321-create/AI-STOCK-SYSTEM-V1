"""Offline initialization gates; no database writes or runtime services."""
from __future__ import annotations
import argparse
from contextlib import closing
from datetime import date, datetime, timezone
from decimal import Decimal
import json
from pathlib import Path
import re
import stat
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
if __name__ == '__main__':
    sys.dont_write_bytecode = True
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
from persistence.backup import file_sha256
from persistence.connection import open_database
from persistence.migration import checks, schema_state, schema_fingerprint, legacy_digest, schema_inventory
from tools.e7_pre_apply_gate import GateError, Parser, require, git

REAL_DB_WRITE = False
APPLY_REAL_DB = "NOT_AUTHORIZED"
STEPS = tuple(f'015-{i:02}' for i in range(1, 7))
FLAGS = dict(zip(STEPS[3:], ('security_master_import', 'holding_config_write', 'dashboard_data_write')))


def require_safe_directory(path, *, must_exist=False):
    try:
        info = path.lstat()
    except FileNotFoundError:
        require(not must_exist, 'DIRECTORY_UNSAFE')
        return
    except OSError as exc:
        raise GateError('DIRECTORY_UNSAFE') from exc
    attributes = getattr(info, 'st_file_attributes', None)
    require(sys.platform != 'win32' or attributes is not None, 'DIRECTORY_UNSAFE')
    # lstat preserves junction/reparse metadata that is_symlink() misses.
    require(stat.S_ISDIR(info.st_mode) and not ((attributes or 0) &
            getattr(stat, 'FILE_ATTRIBUTE_REPARSE_POINT', 0x400)), 'DIRECTORY_UNSAFE')


def external_root(path):
    # Inspect original ancestors before resolve() can follow a junction/link.
    absolute = path.absolute()
    for component in reversed((absolute, *absolute.parents)):
        require_safe_directory(component)
    root = path.resolve()
    require(not root.is_relative_to(ROOT), 'BASE_DIR_IN_REPOSITORY')
    return root


def load(path):
    def unique(pairs):
        value = {}
        for key, item in pairs:
            require(key not in value, 'DUPLICATE_JSON_KEY')
            value[key] = item
        return value
    require(path is not None, 'INPUT_REQUIRED')
    return json.loads(Path(path).read_text(encoding='utf-8'), object_pairs_hook=unique)


def database(args, report):
    baseline = load(args.baseline)
    with closing(open_database(args.db_path, readonly=True)) as db:
        report.update(SCHEMA_STATE=schema_state(db), SCHEMA_FINGERPRINT=schema_fingerprint(db),
                      LEGACY_DIGEST=legacy_digest(db),
                      TOTAL_TABLE_COUNT=sum(r[0] == 'table' for r in schema_inventory(db)), CHECKS=checks(db))
    require(report['SCHEMA_STATE'] == 'B', 'SCHEMA_CHANGED')
    require(report['SCHEMA_FINGERPRINT'] == baseline.get('SCHEMA_AFTER'), 'SCHEMA_CHANGED')
    require(report['LEGACY_DIGEST'] == baseline.get('LEGACY_DIGEST_AFTER'), 'LEGACY_DIGEST_CHANGED')
    require(report['TOTAL_TABLE_COUNT'] == baseline.get('TABLE_COUNT_AFTER'), 'SCHEMA_CHANGED')


def directories(args, report):
    require(args.expected_head is None or git(ROOT, 'rev-parse', 'HEAD') == args.expected_head,
            'EXPECTED_HEAD_MISMATCH')
    root = external_root(args.base_dir)
    targets = [root / name for name in ('runtime', 'cache', 'logs', 'reports')]
    for path in [root, *targets]:
        require_safe_directory(path)
    created = []
    for path in [root, *targets]:
        if not path.exists():
            missing = []
            candidate = path
            while not candidate.exists():
                missing.append(candidate)
                candidate = candidate.parent
            path.mkdir(parents=True, exist_ok=False)
            created.extend(str(item) for item in reversed(missing))
    report['CREATED_DIRECTORIES'] = created


def config(args, report):
    value = load(args.config)
    require(type(value) is dict and all(value.get(k) is False for k in
            ('AUTO_TRADING', 'SCHEDULER', 'AUTO_REFRESH')), 'UNSAFE_RUNTIME_CONFIG')
    report['DISABLED_FEATURES'] = ['AUTO_TRADING', 'SCHEDULER', 'AUTO_REFRESH']


def security(args, report):
    require(args.security_master_import == 'yes', 'SECURITY_MASTER_IMPORT_NOT_AUTHORIZED')
    metadata = load(args.security_metadata)
    require(type(metadata) is dict, 'SECURITY_METADATA_INVALID')
    require(args.security_file is not None and args.security_sha256 is not None and
            re.fullmatch('[a-f0-9]{64}', args.security_sha256) is not None and
            file_sha256(args.security_file) == args.security_sha256 == metadata.get('sha256'),
            'SECURITY_HASH_MISMATCH')
    for key in ('source', 'updated_at', 'version'):
        expected = getattr(args, 'security_' + key)
        require(bool(expected and expected.strip()) and metadata.get(key) == expected, 'SECURITY_METADATA_MISMATCH')
    updated = datetime.fromisoformat(args.security_updated_at.replace('Z', '+00:00'))
    require(updated.tzinfo is not None and updated <= datetime.now(timezone.utc), 'SECURITY_TIMESTAMP_INVALID')
    report['SECURITY_MASTER_STATUS'] = 'VALIDATED_ONLY'


def holding(args, report):
    require(args.holding_config_write == 'yes', 'HOLDING_CONFIG_WRITE_NOT_AUTHORIZED')
    require(args.stock_code is not None and re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._-]{0,31}', args.stock_code)
            is not None, 'HOLDING_FIELDS_INVALID')
    require(args.shares is not None and args.cost is not None and args.date is not None,
            'HOLDING_FIELDS_INVALID')
    shares, cost = Decimal(args.shares), Decimal(args.cost)
    require(shares.is_finite() and shares > 0 and shares == shares.to_integral_value() and
            cost.is_finite() and cost >= 0, 'HOLDING_FIELDS_INVALID')
    day = date.fromisoformat(args.date)
    require(day.isoformat() == args.date and day <= date.today(), 'HOLDING_FIELDS_INVALID')
    # Never serialize holdings, including to stdout or report files.
    report['HOLDING_CONFIG_STATUS'] = 'VALIDATED_ONLY'


def dashboard(args, report):
    config(args, report)
    database(args, report)
    report['READ_ONLY_CHECK'] = 'PASS'
    require(args.dashboard_data_write == 'yes', 'DASHBOARD_DATA_WRITE_NOT_AUTHORIZED')
    root = external_root(args.base_dir)
    target = root / 'reports' / 'dashboard.html'
    require_safe_directory(target.parent, must_exist=True)
    page = '<!doctype html><html><meta charset="utf-8"><title>Runtime initialization</title><body><h1>Runtime initialization</h1><p>Data read: PASS</p><p>Background tasks: disabled</p><p>Tables: ' + str(report['TOTAL_TABLE_COUNT']) + '</p></body></html>'
    with target.open('x', encoding='utf-8') as stream:
        stream.write(page)
    report.update(CREATED_FILES=[str(target)], FILE_HASHES={str(target): file_sha256(target)})
    require(target.read_text(encoding='utf-8') == page, 'DASHBOARD_PAGE_UNREADABLE')
    report.update(PAGE_OPENABLE=True, DATA_READ='PASS', ENABLED_FEATURES=['STATIC_DASHBOARD'])


HANDLERS = dict(zip(STEPS, (directories, database, config, security, holding, dashboard)))


def parser():
    result = Parser(description=__doc__)
    children = result.add_subparsers(dest='command', required=True, parser_class=Parser)
    for step in (*STEPS, 'all'):
        child = children.add_parser(step)
        child.add_argument('--base-dir', type=Path, default=Path(tempfile.gettempdir()) / 'ai-stock-015-runtime')
        for name in ('db-path', 'baseline', 'config', 'security-file', 'security-metadata'):
            child.add_argument('--' + name, type=Path)
        for name in ('expected-head', 'security-sha256', 'security-source', 'security-updated-at',
                     'security-version', 'stock-code', 'shares', 'cost', 'date'):
            child.add_argument('--' + name)
        for name in FLAGS.values():
            child.add_argument('--' + name.replace('_', '-'), choices=['yes'])
    return result


def main(argv=None):
    try:
        args = parser().parse_args(argv)
    except GateError:
        print(json.dumps({'result': 'FAIL', 'error_code': 'CLI_ARGUMENTS_INVALID',
                          'RUNTIME_INITIALIZATION_STATUS': 'BLOCKED'}))
        return 1
    reports = []
    for step in (STEPS if args.command == 'all' else (args.command,)):
        report = dict(STEP=step, result='PASS', REAL_DB_WRITE=REAL_DB_WRITE, APPLY_REAL_DB=APPLY_REAL_DB,
                      NETWORK_CALLS=0, SCHEDULER_STARTED=False, LLM_CALLS=0,
                      CREATED_FILES=[], CREATED_DIRECTORIES=[], FILE_HASHES={}, ENABLED_FEATURES=[],
                      REAL_DB_WRITE_SCOPE=[], RUNTIME_INITIALIZATION_STATUS='PASS')
        if args.command == 'all' and step in FLAGS and getattr(args, FLAGS[step]) != 'yes':
            report.update(result='SKIPPED_PENDING_AUTHORIZATION', RUNTIME_INITIALIZATION_STATUS='SKIPPED_PENDING_AUTHORIZATION')
        else:
            try:
                HANDLERS[step](args, report)
            except Exception as exc:
                report.update(result='FAIL', RUNTIME_INITIALIZATION_STATUS='BLOCKED',
                              error_code=str(exc) if isinstance(exc, GateError) else 'STEP_EXECUTION_FAILED')
        reports.append(report)
        print(json.dumps(report, sort_keys=True))
        if report['result'] == 'FAIL':
            return 1
    if args.command == 'all':
        print(json.dumps({'RUNTIME_INITIALIZATION_STATUS': 'PENDING_AUTHORIZATION' if any(
            r['result'] == 'SKIPPED_PENDING_AUTHORIZATION' for r in reports) else 'PASS',
            'STEPS': {r['STEP']: r['result'] for r in reports}}))
    return 0


if __name__ == '__main__':
    sys.exit(main())
