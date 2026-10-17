"""One-shot offline 015-01/02/03 retry; never connect a database or run 04/05/06.

Run in a disposable Python process. The process audit hook cannot be removed.
Approved reference validation is not a measurement of current database state.
"""
from __future__ import annotations

import argparse
from contextlib import redirect_stdout
from datetime import datetime, timezone
import hashlib
import importlib.util
import inspect
import io
import json
import os
from pathlib import Path
import re
import stat
import sys

sys.dont_write_bytecode = True
ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.phase_b_monitor import PhaseBMonitor, PhaseBStop

BASE = Path('/tmp/015-phase-b-20261016')
DIRECTORY_NAMES = ('runtime', 'cache', 'logs', 'reports')
FILE_NAMES = ('runtime/offline-reference.json', 'runtime/config.json',
              'logs/phase-b-retry.jsonl', 'reports/retry-result.json')
APPROVED = {
    'schema_fingerprint': 'f0f32924c129d986595817a5a88f3d50cf0a80fcf1394a24854b34e7082b21d0',
    'legacy_digest': {
        'schema_digest': '33a48e82dce954a9f6e9b59e9a56199f3314f7d5e592bb3116f21ec705cd5f90',
        'row_count_digest': 'ab2597853b857f9e2e6d10b58bed4dd6e7a7da6f4d6698ca0e5d216674677ccd',
    },
    'table_count': 32,
}
SAFE_CONFIG = {'AUTO_TRADING': False, 'SCHEDULER': False, 'AUTO_REFRESH': False}
OPTIONAL_CONTRACTS = {
    '015-04': ('security_master_import', ('security_file', 'security_metadata',
        'security_sha256', 'security_source', 'security_updated_at', 'security_version')),
    '015-05': ('holding_config_write', ('stock_code', 'shares', 'cost', 'date')),
    '015-06': ('dashboard_data_write', ('base_dir', 'db_path', 'baseline', 'config')),
}


def load_gate():
    spec = importlib.util.spec_from_file_location('phase_b_runtime_gate', ROOT / 'tools/015_runtime_init.py')
    gate = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(gate)
    return gate


def canonical_hash(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':')).encode()).hexdigest()


def file_hash(path):
    digest = hashlib.sha256()
    with path.open('rb') as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(chunk)
    return digest.hexdigest()


def repository_manifest(root):
    """Include ignored files and directory/link entries without following links."""
    manifest = {}

    def scan_error(error):
        raise RuntimeError('REPOSITORY_SCAN_FAILED') from error

    for directory, directories, files in os.walk(root, followlinks=False, onerror=scan_error):
        directories[:] = sorted(name for name in directories if name != '.git')
        for name in sorted([*directories, *files]):
            if name == '.git':
                continue
            path = Path(directory) / name
            relative = path.relative_to(root).as_posix()
            info = path.lstat()
            if stat.S_ISLNK(info.st_mode):
                entry = {'kind': 'symlink', 'target_digest': canonical_hash(os.readlink(path))}
            elif stat.S_ISDIR(info.st_mode):
                entry = {'kind': 'directory'}
            elif stat.S_ISREG(info.st_mode):
                entry = {'kind': 'file', 'sha256': file_hash(path)}
            else:
                raise RuntimeError('REPOSITORY_SPECIAL_FILE')
            manifest[relative] = entry
    return manifest


def assert_unchanged(gate, before, after):
    gate.require(not (set(after) - set(before)), 'UNKNOWN_FILE_CREATED')
    gate.require(before == after, 'UNEXPECTED_WRITE')


def validate_offline_reference(gate, path):
    """Compare a materialized reference with approved literals, never DB state."""
    value = gate.load(path)
    gate.require(type(value) is dict and set(value) == set(APPROVED), 'OFFLINE_REFERENCE_INVALID')
    fingerprint = value['schema_fingerprint']
    gate.require(type(fingerprint) is str and fingerprint == APPROVED['schema_fingerprint'],
                 'SCHEMA_CHANGED')
    legacy = value['legacy_digest']
    gate.require(type(legacy) is dict and set(legacy) == set(APPROVED['legacy_digest'])
                 and all(type(item) is str for item in legacy.values())
                 and legacy == APPROVED['legacy_digest'], 'LEGACY_DIGEST_CHANGED')
    gate.require(type(value['table_count']) is int and value['table_count'] == APPROVED['table_count'],
                 'SCHEMA_CHANGED')
    return {
        'STEP': '015-02', 'result': 'PASS',
        'VALIDATION_MODE': 'OFFLINE_APPROVED_REFERENCE_ONLY',
        'EXPECTED_SOURCE': 'USER_PROVIDED_APPROVED_CONSTANTS_2026-10-16',
        'OBSERVED_SOURCE': 'MATERIALIZED_OFFLINE_JSON_REFERENCE',
        'EXPECTED_REFERENCE_SHA256': canonical_hash(APPROVED),
        'OBSERVED_REFERENCE_SHA256': canonical_hash(value),
        'APPROVED_SCHEMA_FINGERPRINT': fingerprint,
        'APPROVED_LEGACY_DIGEST': {'schema': legacy['schema_digest'],
                                   'row_count_digest': legacy['row_count_digest']},
        'LEGACY_FIELD_MAPPING': 'schema_digest -> schema; values unchanged',
        'APPROVED_TABLE_COUNT': value['table_count'],
        'TABLE_COUNT_SCOPE': 'USER_APPROVED_OFFLINE_INVENTORY',
        'DATABASE_CONNECTED': False, 'CURRENT_DATABASE_STATE': 'NOT_MEASURED',
        'CURRENT_SCHEMA_FINGERPRINT': 'NOT_MEASURED', 'CURRENT_LEGACY_DIGEST': 'NOT_MEASURED',
        'CURRENT_TOTAL_TABLE_COUNT': 'NOT_MEASURED', 'DATABASE_INTEGRITY': 'NOT_MEASURED',
        'SOURCE_FILES_READ': False,
    }


def dry_run_plan(gate, *, original_handlers=None):
    """Inspect dispatch and parser contracts; never invoke optional handlers."""
    handlers = gate.HANDLERS if original_handlers is None else original_handlers
    parser = gate.parser()
    subparsers = next(action for action in parser._actions
                      if isinstance(action, argparse._SubParsersAction))
    plan = []
    for step in gate.STEPS[3:]:
        handler = handlers[step]
        gate.require(callable(handler) and tuple(inspect.signature(handler).parameters) == ('args', 'report'),
                     'DRY_RUN_HANDLER_INVALID')
        flag, required_inputs = OPTIONAL_CONTRACTS[step]
        gate.require(gate.FLAGS[step] == flag, 'DRY_RUN_AUTHORIZATION_GATE_INVALID')
        options = subparsers.choices[step]
        option = next((action for action in options._actions if action.dest == flag), None)
        gate.require(option is not None and option.choices == ['yes'], 'DRY_RUN_AUTHORIZATION_GATE_INVALID')
        # No authorization flags are supplied, even to the parser.
        args = parser.parse_args([step])
        gate.require(getattr(args, flag) is None, 'DRY_RUN_AUTHORIZATION_DEFAULT_INVALID')
        gate.require(all(hasattr(args, name) for name in required_inputs), 'DRY_RUN_INPUT_ROUTE_INVALID')
        plan.append({'STEP': step, 'STATUS': 'SKIPPED_PENDING_AUTHORIZATION',
                     'HANDLER': handler.__name__, 'HANDLER_EXECUTED': False,
                     'AUTHORIZATION_OPTION': option.option_strings[0],
                     'AUTHORIZATION_SUPPLIED': False, 'ROUTE_AND_SIGNATURE': 'PASS',
                     'REQUIRED_INPUT_OPTIONS': ['--' + name.replace('_', '-') for name in required_inputs]})
    return {'MODE': 'STATIC_DISPATCH_AND_AUTHORIZATION_PREFLIGHT', 'result': 'PASS',
            'FUTURE_READINESS': 'ROUTES_PRESENT_INPUT_VALIDATION_DEFERRED', 'PLAN': plan,
            'LIMITATION': 'Future approved inputs must pass their handlers at execution time.'}


def prepare_parser_tempdir(gate):
    # The parser's unused default otherwise probes temp directories by writing a
    # file on the first gettempdir(). Set the verified parent directly instead.
    gate.require_safe_directory(BASE.parent, must_exist=True)
    gate.tempfile.tempdir = str(BASE.parent)


def guard_optional_handlers(gate):
    counts = {step: 0 for step in gate.STEPS[3:]}

    def guard(step, original):
        def denied(args, report):
            counts[step] += 1
            raise gate.GateError('UNAUTHORIZED_STEP')
        denied.__name__ = original.__name__
        return denied

    for step in counts:
        gate.HANDLERS[step] = guard(step, gate.HANDLERS[step])
    return counts


def runtime_inventory(gate, base):
    expected_directories = {str(base / name) for name in DIRECTORY_NAMES}
    expected_files = {str(base / name) for name in FILE_NAMES}
    files = []
    for directory, directories, names in os.walk(base, followlinks=False):
        for name in directories:
            path = Path(directory) / name
            gate.require(str(path) in expected_directories, 'UNKNOWN_FILE_CREATED')
            gate.require_safe_directory(path, must_exist=True)
        for name in names:
            path = Path(directory) / name
            gate.require(str(path) in expected_files and stat.S_ISREG(path.lstat().st_mode),
                         'UNKNOWN_FILE_CREATED')
            files.append(path.relative_to(base).as_posix())
    return sorted(files)


class ExecutionLog:
    def __init__(self):
        self.records = []
        self.stream = None

    def emit(self, event):
        record = {'timestamp_utc': datetime.now(timezone.utc).isoformat(), **event}
        self.records.append(record)
        line = json.dumps(record, sort_keys=True)
        print(line, flush=True)
        if self.stream is not None:
            self.stream.write(line + '\n')
            self.stream.flush()

    def open(self, path):
        self.stream = path.open('x', encoding='utf-8')
        for record in self.records:
            self.stream.write(json.dumps(record, sort_keys=True) + '\n')
        self.stream.flush()

    def close(self):
        if self.stream is not None:
            self.stream.close()


def write_exclusive(path, value):
    with path.open('x', encoding='utf-8') as stream:
        json.dump(value, stream, sort_keys=True, indent=2)
        stream.write('\n')


def invoke_step(gate, monitor, step, arguments, log):
    gate.require(step in ('015-01', '015-03'), 'UNAUTHORIZED_STEP')
    with redirect_stdout(io.StringIO()) as output:
        exit_code = gate.main([step, *arguments])
    monitor.raise_if_stopped()
    values = [json.loads(line) for line in output.getvalue().splitlines()]
    gate.require(len(values) == 1, 'STEP_OUTPUT_INVALID')
    report = values[0]
    log.emit({'event': 'STEP_RESULT', **report})
    gate.require(exit_code == 0 and report['result'] == 'PASS', report.get('error_code', 'STEP_FAILED'))
    return report


def execute(expected_head):
    log = ExecutionLog()
    monitor = None
    gate = None
    try:
        gate = load_gate()
        gate.require(re.fullmatch(r'[a-f0-9]{40}', expected_head) is not None, 'EXPECTED_HEAD_INVALID')
        gate.require(not BASE.exists() and not BASE.is_symlink(), 'RUNTIME_ROOT_ALREADY_EXISTS')
        prepare_parser_tempdir(gate)
        original_handlers = dict(gate.HANDLERS)
        optional_calls = guard_optional_handlers(gate)
        before = repository_manifest(ROOT)
        monitor = PhaseBMonitor(
            allowed_directories=[BASE, *(BASE / name for name in DIRECTORY_NAMES)],
            allowed_files=[BASE / name for name in FILE_NAMES],
            allowed_git_command=('git', '-C', str(ROOT), 'rev-parse', 'HEAD'),
            event_sink=lambda event: log.emit({'event': 'MONITOR_STOP', **event}), scope='LIVE').install()
        log.emit({'event': 'BEGIN', 'EXPECTED_HEAD': expected_head,
                  'SCOPE': '015-01/02/03_OFFLINE_ONLY', 'REAL_DB_WRITE': 'NOT_STARTED',
                  'APPLY_REAL_DB': 'NOT_AUTHORIZED'})
        # Check original ancestors and all intended directories using unchanged C01.
        gate.require(gate.external_root(BASE) == BASE, 'DIRECTORY_UNSAFE')
        for path in [BASE, *(BASE / name for name in DIRECTORY_NAMES)]:
            gate.require_safe_directory(path)
        log.emit({'event': 'C01_PRECHECK', 'result': 'PASS'})
        first = invoke_step(gate, monitor, '015-01',
                            ['--base-dir', str(BASE), '--expected-head', expected_head], log)
        log.open(BASE / 'logs/phase-b-retry.jsonl')
        write_exclusive(BASE / 'runtime/offline-reference.json', APPROVED)
        second = validate_offline_reference(gate, BASE / 'runtime/offline-reference.json')
        log.emit({'event': 'STEP_RESULT', **second})
        write_exclusive(BASE / 'runtime/config.json', SAFE_CONFIG)
        third = invoke_step(gate, monitor, '015-03', ['--config', str(BASE / 'runtime/config.json')], log)
        plan = dry_run_plan(gate, original_handlers=original_handlers)
        log.emit({'event': 'DRY_RUN', **plan})
        after = repository_manifest(ROOT)
        assert_unchanged(gate, before, after)
        files = runtime_inventory(gate, BASE)
        gate.require(not any(optional_calls.values()), 'UNAUTHORIZED_STEP')
        monitor.raise_if_stopped()
        result = {
            'PHASE_B_STATUS': 'OFFLINE_COMPLETE', 'RUNTIME_INITIALIZATION_STATUS': 'PENDING_AUTHORIZATION',
            'STEPS': {'015-01': first, '015-02': second, '015-03': third}, 'DRY_RUN': plan,
            'OPTIONAL_HANDLER_CALLS': optional_calls,
            'REAL_DB_WRITE': 'NOT_STARTED', 'APPLY_REAL_DB': 'NOT_AUTHORIZED', 'REAL_DB_WRITE_SCOPE': [],
            'DATABASE_CONNECTIONS': 0, 'MONITOR_STOP_EVENT': monitor.stop_event,
            'MONITOR_SCOPE': 'PYTHON_AUDITED_OPERATIONS_AND_EXACT_READ_ONLY_GIT_CHILD',
            'REPOSITORY_BEFORE_SHA256': canonical_hash(before),
            'REPOSITORY_AFTER_SHA256': canonical_hash(after),
            'REPOSITORY_ENTRY_COUNT': len(before), 'IGNORED_FILES_INCLUDED': True,
            'REPOSITORY_UNCHANGED': True, 'CREATED_DIRECTORIES': first['CREATED_DIRECTORIES'],
            'CREATED_FILES': sorted([*files, 'reports/retry-result.json']),
            'INPUT_FILE_HASHES': {name: file_hash(BASE / name) for name in FILE_NAMES[:2]},
            'USAGE_LIMIT_HIT': False,
        }
        write_exclusive(BASE / 'reports/retry-result.json', result)
        runtime_inventory(gate, BASE)
        log.emit({'event': 'COMPLETE', 'PHASE_B_STATUS': 'OFFLINE_COMPLETE',
                  'RUNTIME_INITIALIZATION_STATUS': 'PENDING_AUTHORIZATION',
                  'REAL_DB_WRITE': 'NOT_STARTED', 'USAGE_LIMIT_HIT': False})
        return 0
    except Exception as exc:
        trigger = (monitor.stop_event['trigger'] if monitor and monitor.stop_event else
                   str(exc) if isinstance(exc, PhaseBStop) or gate and isinstance(exc, gate.GateError)
                   else 'RETRY_EXECUTION_FAILED')
        log.emit({'event': 'STOP', 'trigger': trigger, 'PHASE_B_STATUS': 'BLOCKED',
                  'REAL_DB_WRITE': 'NOT_STARTED', 'USAGE_LIMIT_HIT': False})
        return 1
    finally:
        log.close()


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--expected-head', required=True)
    args = parser.parse_args(argv)
    return execute(args.expected_head)


if __name__ == '__main__':
    sys.exit(main())
