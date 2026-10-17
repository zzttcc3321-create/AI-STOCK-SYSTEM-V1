"""Pure synthetic reference/planning tests; no database, optional step or runner."""
import copy
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

SPEC = importlib.util.spec_from_file_location('phase_b_retry',
    Path(__file__).resolve().parents[1] / 'tools/015_phase_b_retry.py')
retry = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(retry)
gate = retry.load_gate()


class PhaseBRetryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.reference = self.root / 'synthetic-reference.json'

    def validate(self, value):
        self.reference.write_text(json.dumps(value), encoding='utf-8')
        with patch.object(gate, 'open_database', side_effect=AssertionError('database forbidden')):
            return retry.validate_offline_reference(gate, self.reference)

    def test_approved_reference_is_explicitly_not_database_measurement(self):
        report = self.validate(copy.deepcopy(retry.APPROVED))
        self.assertEqual(report['result'], 'PASS')
        self.assertFalse(report['DATABASE_CONNECTED'])
        self.assertEqual(report['CURRENT_DATABASE_STATE'], 'NOT_MEASURED')
        self.assertEqual(report['CURRENT_TOTAL_TABLE_COUNT'], 'NOT_MEASURED')
        self.assertEqual(report['APPROVED_TABLE_COUNT'], 32)
        self.assertNotIn('TOTAL_TABLE_COUNT', report)
        self.assertNotEqual(report['EXPECTED_SOURCE'], report['OBSERVED_SOURCE'])
        self.assertEqual(report['APPROVED_LEGACY_DIGEST'], {
            'schema': retry.APPROVED['legacy_digest']['schema_digest'],
            'row_count_digest': retry.APPROVED['legacy_digest']['row_count_digest']})

    def test_schema_and_table_mismatch_stop(self):
        for field, value in [('schema_fingerprint', '0' * 64), ('schema_fingerprint', 1),
                             ('table_count', 33), ('table_count', True), ('table_count', 32.0),
                             ('table_count', '32')]:
            with self.subTest(field=field, value=value):
                reference = copy.deepcopy(retry.APPROVED)
                reference[field] = value
                with self.assertRaisesRegex(gate.GateError, '^SCHEMA_CHANGED$'):
                    self.validate(reference)

    def test_both_legacy_fields_and_exact_schema_are_required(self):
        for legacy in [{}, {'schema': '0' * 64, 'row_count_digest': '0' * 64},
                       {**retry.APPROVED['legacy_digest'], 'schema_digest': '0' * 64},
                       {**retry.APPROVED['legacy_digest'], 'row_count_digest': '0' * 64},
                       {**retry.APPROVED['legacy_digest'], 'extra': 'unused'}]:
            with self.subTest(legacy=legacy):
                reference = copy.deepcopy(retry.APPROVED)
                reference['legacy_digest'] = legacy
                with self.assertRaisesRegex(gate.GateError, '^LEGACY_DIGEST_CHANGED$'):
                    self.validate(reference)

    def test_duplicate_json_key_is_rejected(self):
        self.reference.write_text('{"table_count":32,"table_count":32}', encoding='utf-8')
        with self.assertRaisesRegex(gate.GateError, '^DUPLICATE_JSON_KEY$'):
            retry.validate_offline_reference(gate, self.reference)

    def test_extra_or_missing_reference_fields_are_rejected(self):
        for reference in [[], {}, {**retry.APPROVED, 'extra': 'unexpected'}]:
            with self.assertRaisesRegex(gate.GateError, '^OFFLINE_REFERENCE_INVALID$'):
                self.validate(reference)

    def test_dry_run_never_calls_handlers_or_authorizes(self):
        calls = []

        def forbidden(args, report):
            calls.append(args.command)
            raise AssertionError('optional handler forbidden')

        with patch.dict(gate.HANDLERS, {step: forbidden for step in gate.STEPS[3:]}), \
                patch.object(gate, 'open_database', side_effect=AssertionError('database forbidden')):
            plan = retry.dry_run_plan(gate)
        self.assertEqual(calls, [])
        self.assertEqual(plan['result'], 'PASS')
        self.assertEqual([row['STEP'] for row in plan['PLAN']], list(gate.STEPS[3:]))
        self.assertTrue(all(row['AUTHORIZATION_SUPPLIED'] is False and row['HANDLER_EXECUTED'] is False
                            for row in plan['PLAN']))

    def test_missing_authorization_route_blocks_dry_run(self):
        with patch.dict(gate.FLAGS, {'015-04': 'nonexistent'}):
            with self.assertRaisesRegex(gate.GateError, '^DRY_RUN_AUTHORIZATION_GATE_INVALID$'):
                retry.dry_run_plan(gate)

    def test_guard_wrapper_does_not_hide_invalid_original_handler(self):
        local_gate = retry.load_gate()
        local_gate.HANDLERS['015-04'] = lambda wrong_signature: None
        originals = dict(local_gate.HANDLERS)
        calls = retry.guard_optional_handlers(local_gate)
        with self.assertRaisesRegex(gate.GateError, '^DRY_RUN_HANDLER_INVALID$'):
            retry.dry_run_plan(local_gate, original_handlers=originals)
        self.assertFalse(any(calls.values()))

    def test_fresh_parser_uses_verified_tempdir_without_probe_write(self):
        with patch.object(gate.tempfile, 'tempdir', None), \
                patch.object(gate.tempfile, '_get_default_tempdir',
                             side_effect=AssertionError('tempdir probe forbidden')):
            retry.prepare_parser_tempdir(gate)
            args = gate.parser().parse_args(['015-01', '--base-dir', str(retry.BASE)])
            self.assertEqual(args.base_dir, retry.BASE)
            self.assertEqual(gate.tempfile.tempdir, '/tmp')

    def test_manifest_includes_ignored_files_and_detects_changes(self):
        (self.root / '.gitignore').write_text('ignored.txt\n', encoding='utf-8')
        ignored = self.root / 'ignored.txt'
        ignored.write_text('synthetic first', encoding='utf-8')
        (self.root / '.git').mkdir()
        (self.root / '.git' / 'HEAD').write_text('synthetic metadata', encoding='utf-8')
        before = retry.repository_manifest(self.root)
        self.assertIn('ignored.txt', before)
        self.assertFalse(any(name.startswith('.git/') for name in before))
        ignored.write_text('synthetic second', encoding='utf-8')
        with self.assertRaisesRegex(gate.GateError, '^UNEXPECTED_WRITE$'):
            retry.assert_unchanged(gate, before, retry.repository_manifest(self.root))
        (self.root / 'unexpected.txt').write_text('synthetic new', encoding='utf-8')
        with self.assertRaisesRegex(gate.GateError, '^UNKNOWN_FILE_CREATED$'):
            retry.assert_unchanged(gate, before, retry.repository_manifest(self.root))

    def test_exclusive_output_does_not_overwrite(self):
        path = self.root / 'output.json'
        retry.write_exclusive(path, {'synthetic': 1})
        before = path.read_bytes()
        with self.assertRaises(FileExistsError):
            retry.write_exclusive(path, {'synthetic': 2})
        self.assertEqual(path.read_bytes(), before)


if __name__ == '__main__':
    unittest.main()
