"""#014-R1 dev-box verification; temporary synthetic DB/archive only.

This harness does not validate Windows E1/E2/E7, or call real providers/models.
"""
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / 'tests'))

from analysis.output_validator import prompt_inputs
from evidence import ArchiveWriter
from test_information_archive_roundtrip import archived_information
from test_dashboard_runtime import runtime_fixture, confirm_items


# #014-R2: commander-approved durability contract revision only. Every other
# frozen file, migration and schema remains protected.
APPROVED_014_R2_FROZEN_FILES = frozenset({
    'docs/frozen/EVIDENCE_ARCHIVE_SIDECAR_V1.0.md',
})


def git(*args):
    return subprocess.check_output(['git', '-C', str(ROOT), *args], text=True).strip()


def verify_protected_paths(baseline):
    changed = set(git('diff', '--name-only', baseline, 'HEAD', '--', 'migration',
                      'persistence/migration.py', 'persistence/schema_v1_1.py',
                      'docs/frozen').splitlines())
    protected = changed - APPROVED_014_R2_FROZEN_FILES
    assert not protected, 'Protected paths changed: ' + ', '.join(sorted(protected))
    return sorted(changed & APPROVED_014_R2_FROZEN_FILES)


def verify():
    result = {'TASK_ID': 'AI-STOCK-V1-E2E-R1-014', 'scope': 'DEV_SYNTHETIC_ONLY',
              'real_database_access': 0, 'live_model_calls': 0, 'live_provider_calls': 0,
              'E1': 'NOT_RUN_WINDOWS_DEFERRED', 'E2': 'NOT_RUN_WINDOWS_DEFERRED',
              'E7': 'NOT_RUN_WINDOWS_QI_DEFERRED'}
    baseline = git('rev-parse', '5a86655')
    subprocess.run(['git', '-C', str(ROOT), 'merge-base', '--is-ancestor', baseline, 'HEAD'], check=True)
    approved_changes = verify_protected_paths(baseline)
    status = git('status', '--short')
    result['E0'] = {'status': 'PASS_DEV_SCOPE' if not status else 'FAIL_WORKTREE_DIRTY',
        'baseline': baseline, 'head': git('rev-parse', 'HEAD'), 'worktree_clean': not bool(status),
        'protected_paths_unchanged': True, 'approved_014_r2_frozen_changes': approved_changes,
        'real_schema_B_old32_digest': 'NOT_RUN_WINDOWS_DEFERRED'}
    with tempfile.TemporaryDirectory(prefix='e2e014r1-') as temporary:
        writer = ArchiveWriter(Path(temporary) / 'information')
        checked = []
        cases = [('wenda_notice_query', None), ('wenda_news_query', None), ('wenda_report_query', None),
                 *[('tdx_api_data', view) for view in ('profit', 'balance', 'cashflow')]]
        for n, (tool, view) in enumerate(cases):
            _, _, record, raw = archived_information(writer, tool=tool, view=view,
                raw_id='abc123' if n == 0 else 'raw' + str(n), canonical_id='EV-00001' if n == 0 else 'EV-' + str(n))
            writer.reader.verify_record(record)
            recovered = writer.reader.evidence(record.raw_response_path, record.evidence_id)
            assert recovered['canonical_id'] == record.evidence_id
            assert recovered['raw_reference_id'] != recovered['canonical_id']
            assert writer.reader.read(record.raw_response_path)['raw_parts']['response'] == raw
            checked.append({'tool': tool, 'view': view, 'canonical_id': recovered['canonical_id'],
                            'raw_reference_id': recovered['raw_reference_id'],
                            'supersedes_evidence_id': recovered['supersedes_evidence_id'],
                            'raw_hash': record.raw_response_hash, 'normalized_hash': record.normalized_data_hash})
        result['E3'] = {'status': 'PASS_DEV_SCOPE', 'cases': checked}
        db, runtime, record = runtime_fixture(Path(temporary) / 'runtime')
        try:
            runtime.prepare(record.evidence_id)
            assert runtime.indicators['ma5'] == 157 and runtime.indicators['ma20'] == 149.5
            assert runtime.indicators['ma60'] == 129.5 and runtime.indicators['volume_change'] == .15
            assert runtime.indicators['trend'] == 'UP'
            result['E4'] = {'status': 'PASS_DEV_SCOPE', 'indicators': runtime.indicators}
            fields = prompt_inputs(runtime.prompt)
            assert set(fields) == {'stock_id', 'holding_position', 'canonical_market_data', 'financial_data',
                                   'intelligence_data', 'evidence_index', 'data_quality_report'}
            assert runtime.prompt == __import__('analysis').PromptBuilder().build(runtime.package)
            result['E5'] = {'status': 'PASS_CODE_CONTRACT', 'keys': sorted(fields),
                'input_hash': runtime.prompt.input_hash, 'evidence_count': len(runtime.prompt.evidence_ids),
                'DATA_STATUS': runtime.data_status, 'holding_position': None}
            assert not runtime.executor.calls
            runtime.generate(actor_id='DEV_TEST_REVIEWER')
            confirm_items(runtime)
            runtime.prepare_archive()
            assert db.execute('SELECT count(*) FROM analysis_reports').fetchone()[0] == 0
            final = runtime.confirm_and_archive(actor_id='DEV_TEST_REVIEWER')
            assert db.execute('SELECT status FROM analysis_tasks').fetchone()[0] == 'ARCHIVED'
            result['E6_runtime'] = {'status': 'PASS_DEV_SIMULATION', 'history': runtime.history,
                'DATA_STATUS': runtime.data_status, 'final_snapshot_hash': final.snapshot.final_snapshot_hash,
                'confirmation_log_hash': final.snapshot.confirmation_log_hash,
                'human_actor': 'DEV_TEST_REVIEWER', 'qi_confirmation': 'NOT_RUN'}
            result['synthetic_executor_calls'] = len(runtime.executor.calls)
        finally:
            db.close()
    if importlib.util.find_spec('streamlit') is None:
        result['E6_UI'] = {'status': 'BLOCKED_WITH_ROOT_CAUSE', 'reason': 'STREAMLIT_NOT_INSTALLED'}
    else:
        execution = subprocess.run([sys.executable, '-m', 'unittest', 'discover', '-s', 'tests',
                                    '-p', 'test_dashboard_streamlit.py', '-v'], cwd=ROOT, capture_output=True, text=True)
        result['E6_UI'] = {'status': 'PASS_DEV_APPTEST' if execution.returncode == 0 else 'FAIL',
                           'returncode': execution.returncode}
    result['CONCLUSION'] = ('READY_FOR_REVIEW' if result['E0']['status'] == 'PASS_DEV_SCOPE' and
        result['E6_UI']['status'] == 'PASS_DEV_APPTEST' else 'BLOCKED_WITH_ROOT_CAUSE')
    return result


if __name__ == '__main__':
    print(json.dumps(verify(), ensure_ascii=False, indent=2, allow_nan=False))
