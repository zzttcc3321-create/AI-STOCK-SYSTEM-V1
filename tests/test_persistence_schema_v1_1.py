"""#011 schema contracts; SQLite guard is scoped to each schema test."""

import ast
from collections.abc import Mapping
from dataclasses import asdict
from enum import Enum
import math
from datetime import datetime
import hashlib
import importlib
import json
from pathlib import Path
import re
import sqlite3
import subprocess
import unittest
from unittest.mock import patch

REAL_CONNECT = sqlite3.connect

def memory_only(database, *args, **kwargs):
    if database != ':memory:':
        raise AssertionError('REAL_DB_CONNECTION=FORBIDDEN')
    return REAL_CONNECT(database, *args, **kwargs)

class SQLiteGuardTests(unittest.TestCase):
    def test_sqlite_connect_restored_after_memory_guard(self):
        original = sqlite3.connect
        with patch('sqlite3.connect', side_effect=memory_only):
            db = sqlite3.connect(':memory:')
            try:
                self.assertEqual(db.execute('SELECT 1').fetchone()[0], 1)
            finally:
                db.close()
            with self.assertRaisesRegex(AssertionError, 'FORBIDDEN'):
                sqlite3.connect('stock_analysis.db')
        self.assertIs(sqlite3.connect, original)
        with self.assertRaisesRegex(RuntimeError, 'guard failure'):
            with patch('sqlite3.connect', side_effect=memory_only):
                raise RuntimeError('guard failure')
        self.assertIs(sqlite3.connect, original)


from persistence import schema_v1_1 as schema
from analysis import AnalysisPackageBuilder, AnalysisRunner, ConfirmationFlow, finalize_analysis
from analysis.contracts import stable_json
from analysis.output_contracts import OutputParser, digest
from evidence import EvidenceSource, HumanConfirm
from test_analysis_runner import FakeModelExecutor
from test_analysis_package import package_arguments
from test_analysis_output import deterministic_response
from test_human_confirmation import ACTOR, STAMP, changed_field, confirm_remaining, source_records

EXPECTED = ('analysis_tasks', 'analysis_reports', 'evidence_records',
            'analysis_confirmation_events', 'analysis_human_overrides',
            'analysis_snapshots', 'analysis_final_evidence')
WHITELIST = {'docs/frozen/DB_SCHEMA_V1.1.md',
             'docs/decisions/DB_SCHEMA_V1_1_PERSISTENCE_UNFREEZE_20261003.md',
             'docs/DB_COMPATIBILITY_MAPPING_011.md',
             'docs/PERSISTENCE_READINESS_011.md', 'persistence/schema_v1_1.py',
             'tests/test_persistence_schema_v1_1.py'}
BASELINE = 'eaa928a2ad99121154563e8ecc660841beee5fcb'
IMPLEMENTATION_BASELINE = '91c19e524f5456f050813dc17418793163b8f1c4'
IMPLEMENTATION_WHITELIST = {
    'migration/003_db_schema_v1_1_additive.sql', 'persistence/__init__.py',
    'persistence/backup.py', 'persistence/connection.py', 'persistence/contracts.py',
    'persistence/serialization.py', 'persistence/migration.py', 'persistence/repository.py',
    'persistence/read_models.py', 'tests/test_persistence_schema_v1_1.py',
    'tests/test_persistence_backup.py', 'tests/test_persistence_migration.py',
    'tests/test_persistence_repository.py', 'tests/test_persistence_read_models.py',
    'docs/PERSISTENCE_IMPLEMENTATION_012.md', 'docs/PERSISTENCE_MIGRATION_RUNBOOK_012.md',
}

def insert(db, table, row):
    columns = ','.join(row)
    db.execute(f'INSERT INTO {table} ({columns}) VALUES ({",".join("?" for _ in row)})',
               tuple(row.values()))

def encode(value):
    # Persistence provenance contains raw_response_hash keys. Input-domain
    # privacy filters are not a persistence serializer; validate JSON types here.
    def validate(item):
        if isinstance(item, Enum):return validate(item.value)
        if isinstance(item, Mapping):
            for key,child in item.items():
                if type(key) is not str:raise ValueError('JSON keys must be text')
                validate(child)
        elif isinstance(item,(tuple,list)):
            for child in item:validate(child)
        elif item is None or type(item) in (str,bool,int):pass
        elif type(item) is float and math.isfinite(item):pass
        else:raise ValueError('only finite JSON values')
    validate(value)
    return stable_json(value)

def fixture():
    package = AnalysisPackageBuilder().build(**package_arguments(quote=True))
    execution = AnalysisRunner(FakeModelExecutor()).run(package)
    assert execution.status == 'UNCONFIRMED'
    flow = ConfirmationFlow(execution.draft, execution.evidence, analysis_report_id='REPORT-011-001')
    flow.modify('risk_level', 'REVIEW_FIELD', changed_field(flow), actor_id=ACTOR,
                reason='人工上调风险', referenced_evidence_ids=flow.run_evidence.input_evidence_ids,
                created_at=STAMP)
    flow.reject('operation_plan', 'REVIEW_ADVICE', actor_id=ACTOR, reason='暂不采纳', created_at=STAMP)
    confirm_remaining(flow)
    prepared = flow.prepare_final_snapshot()
    flow.confirm_final_report(prepared, actor_id=ACTOR, created_at=STAMP)
    final = finalize_analysis(flow, prepared, evidence_records=source_records(flow.run_evidence),
                              final_evidence_id='FINAL-EVIDENCE-011-001')
    envelope = execution.prompt_envelope.to_dict()
    raw = json.dumps(deterministic_response(execution.prompt_envelope), ensure_ascii=False)
    run = flow.run_evidence
    task = dict(id=run.analysis_run_id, stock_id=run.stock_id, task_date='2026-10-03',
                prompt_version=run.prompt_version, model_version=run.model_version,
                status=execution.status, created_by='fixture-creator-011', created_at=run.created_at,
                run_evidence_json=encode(run.to_dict()), validated_draft_json=encode(flow.draft.to_dict()),
                prompt_envelope_json=encode(envelope), raw_model_output=raw)
    rows = [('analysis_tasks', task)]
    for evidence in source_records(run):
        value = asdict(evidence)
        rows.append(('evidence_records', dict(id=evidence.evidence_id, analysis_report_id=None,
            source=evidence.source.value, timestamp=evidence.request_timestamp,
            raw_data_path=evidence.raw_response_path, calculation=encode({'normalization_rule':value['normalization_rule']}),
            model_version=evidence.model_version, human_confirm=evidence.human_confirm.value,
            collection_task_id=evidence.collection_task_id, system_run_id=evidence.system_run_id,
            created_at=STAMP, supersedes_evidence_id=evidence.supersedes_evidence_id)))
    rows.extend(revision_rows(flow, prepared, final))
    return execution, flow, prepared, final, rows

def revision_rows(flow, prepared, final):
    snapshot, evidence, conclusion = final.snapshot, final.evidence, final.conclusion
    report = dict(id=snapshot.analysis_report_id, task_id=snapshot.analysis_run_id,
        **{key:encode(value) for key,value in snapshot.to_dict()['seven_fields'].items()},
        human_confirm=snapshot.human_confirm.value, created_at=snapshot.confirmed_at,
        revision_id=snapshot.revision_id, model_version=conclusion.model_version,
        calculation=encode(conclusion.calculation), evidence_ids=encode(conclusion.evidence_ids),
        supersedes_analysis_report_id=snapshot.supersedes_analysis_report_id,
        final_snapshot_hash=snapshot.final_snapshot_hash, final_evidence_id=evidence.final_evidence_id)
    rows = [('analysis_reports', report)]  # Deferred forward references exercise COMMIT.
    for override in flow.human_overrides:
        row = override.to_dict()
        for key in ('replacement','referenced_evidence_ids'):row[key]=encode(row[key])
        rows.append(('analysis_human_overrides', row))
    for candidate,kind,hash_key in ((prepared,'PREPARED','prepared_snapshot_hash'),
                                   (snapshot,'FINAL','final_snapshot_hash')):
        rows.append(('analysis_snapshots', dict(snapshot_hash=getattr(candidate,hash_key),
            snapshot_kind=kind, analysis_run_id=candidate.analysis_run_id,
            analysis_report_id=candidate.analysis_report_id, revision_id=candidate.revision_id,
            payload_json=encode(candidate.to_dict()))))
    for ordinal,event in enumerate(flow.events):
        rows.append(('analysis_confirmation_events', dict(**event.to_dict(),event_sequence=ordinal)))
    row=evidence.to_dict()
    for key in ('input_evidence_ids','used_evidence_ids'):row[key]=encode(row[key])
    rows.append(('analysis_final_evidence',row))
    return rows

class ResumeSchemaTests(unittest.TestCase):
    def setUp(self):
        guard = patch('sqlite3.connect', side_effect=memory_only)
        guard.start()
        self.addCleanup(guard.stop)
        self.db=sqlite3.connect(':memory:')
        self.addCleanup(self.db.close)
        self.db.row_factory=sqlite3.Row
        self.db.executescript(schema.TEST_SCHEMA_DDL)
        self.execution,self.flow,self.prepared,self.final,self.rows=fixture()

    def commit_fixture(self):
        self.db.execute('BEGIN')
        for table,row in self.rows:insert(self.db,table,row)
        self.db.commit()

    def test_01_import_has_no_connection_or_io(self):
        with patch('sqlite3.connect',side_effect=AssertionError('no connection')):
            importlib.reload(schema)
        tree=ast.parse(Path('persistence/schema_v1_1.py').read_text())
        self.assertFalse(any(isinstance(n,(ast.Import,ast.ImportFrom)) for n in ast.walk(tree)))
        self.assertFalse(any(isinstance(n,ast.Call) and isinstance(n.func,ast.Name)
                             and n.func.id in ('open','exec','eval','__import__') for n in ast.walk(tree)))
        for database in ('stock_analysis.db', '', 'file::memory:?cache=shared',
                         'file:stock_analysis.db?mode=ro', b':memory:', Path(':memory:')):
            with self.subTest(database=database), self.assertRaisesRegex(AssertionError,'FORBIDDEN'):
                sqlite3.connect(database)

    def test_02_gate32_exactly_seven_new_tables(self):
        actual={r[0] for r in self.db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        self.assertEqual(schema.NEW_TABLES,EXPECTED)
        self.assertEqual(actual,set(EXPECTED))
        self.assertEqual(schema.INSERT_ONLY_TABLES, (
            'evidence_records', 'analysis_confirmation_events', 'analysis_human_overrides',
            'analysis_snapshots', 'analysis_final_evidence'))
        self.assertNotIn('analysis_conclusions',actual)
        self.assertEqual(self.db.execute('PRAGMA foreign_keys').fetchone()[0],1)
        self.assertEqual(self.db.execute('PRAGMA recursive_triggers').fetchone()[0],1)

    def test_03_roundtrip_run_draft_input_and_raw_hashes(self):
        self.commit_fixture()
        row=self.db.execute('SELECT * FROM analysis_tasks').fetchone()
        run=json.loads(row['run_evidence_json']); draft=json.loads(row['validated_draft_json'])
        self.assertEqual(run,self.flow.run_evidence.to_dict())
        self.assertEqual(draft,self.flow.draft.to_dict())
        self.assertEqual(row['id'],run['analysis_run_id'])
        self.assertEqual(digest(draft['output']),run['validated_output_hash'])
        self.assertEqual(hashlib.sha256(row['raw_model_output'].encode('utf-8')).hexdigest(),run['model_output_hash'])
        envelope=json.loads(row['prompt_envelope_json'])
        for key in ('input_hash','NETWORK_CALL_COUNT','MODEL_CALL_COUNT','DATABASE_WRITE'):envelope.pop(key)
        self.assertEqual(digest(envelope),run['input_hash'])
        self.assertNotIn('_gate_digest',run)

    def test_04_roundtrip_prepared_and_final_hashes(self):
        self.commit_fixture()
        for kind,candidate,hash_key in (('PREPARED',self.prepared,'prepared_snapshot_hash'),
                                        ('FINAL',self.final.snapshot,'final_snapshot_hash')):
            with self.subTest(kind=kind):
                row=self.db.execute('SELECT * FROM analysis_snapshots WHERE snapshot_kind=?',(kind,)).fetchone()
                payload=json.loads(row['payload_json'])
                self.assertEqual(payload,candidate.to_dict())
                self.assertEqual(payload['final_state'],kind)
                self.assertEqual(payload['confirmation_log_hash'],digest(payload['confirmation_events']))
                claimed=payload.pop(hash_key)
                self.assertEqual(claimed,digest(payload))
                self.assertEqual(row['snapshot_hash'],claimed)

    def test_05_seven_fields_original_modified_rejected(self):
        self.commit_fixture()
        row=self.db.execute('SELECT * FROM analysis_reports').fetchone()
        for key,value in self.final.snapshot.to_dict()['seven_fields'].items():
            self.assertEqual(json.loads(row[key]),value)
        self.assertEqual(json.loads(row['risk_level'])['DECISION'],'MODIFIED')
        self.assertEqual(json.loads(row['risk_level'])['ORIGIN'],'HUMAN')
        self.assertIsNone(json.loads(row['operation_plan'])['EFFECTIVE_VALUE'])
        self.assertEqual(json.loads(row['evidence_ids']),list(self.final.conclusion.evidence_ids))
        self.assertEqual(json.loads(row['calculation']),json.loads(encode(self.final.conclusion.calculation)))

    def test_06_event_order_enums_and_timezone(self):
        self.commit_fixture()
        rows=list(self.db.execute('SELECT * FROM analysis_confirmation_events ORDER BY event_sequence'))
        self.assertEqual([r['event_sequence'] for r in rows],list(range(len(self.flow.events))))
        for row,event in zip(rows,self.flow.events):
            restored=dict(row);restored.pop('event_sequence')
            self.assertEqual(restored,event.to_dict())
            self.assertEqual(row['created_at'],STAMP)
            self.assertIsNotNone(datetime.fromisoformat(row['created_at']).utcoffset())
            self.assertIsInstance(HumanConfirm(row['current_state']),HumanConfirm)
        self.assertEqual(digest([e.to_dict() for e in self.flow.events]),self.final.snapshot.confirmation_log_hash)

    def test_07_overrides_and_provider_columns_roundtrip(self):
        self.commit_fixture()
        stored=dict(self.db.execute('SELECT * FROM analysis_human_overrides').fetchone())
        for key in ('replacement','referenced_evidence_ids'):stored[key]=json.loads(stored[key])
        self.assertEqual(stored,self.flow.human_overrides[0].to_dict())
        for original in source_records(self.flow.run_evidence):
            row=self.db.execute('SELECT * FROM evidence_records WHERE id=?',(original.evidence_id,)).fetchone()
            self.assertNotIn('metadata_json', row.keys())
            self.assertEqual(row['source'],original.source.value)
            self.assertEqual(row['raw_data_path'],original.raw_response_path)
            self.assertEqual(row['timestamp'],original.request_timestamp)
            self.assertEqual(json.loads(row['calculation'])['normalization_rule'],list(original.normalization_rule))

    def test_08_final_evidence_is_distinct_and_roundtrips(self):
        self.commit_fixture()
        row=dict(self.db.execute('SELECT * FROM analysis_final_evidence').fetchone())
        for key in ('input_evidence_ids','used_evidence_ids'):row[key]=json.loads(row[key])
        self.assertEqual(row,self.final.evidence.to_dict())
        self.assertNotIn('source',row)
        self.assertEqual(len(EvidenceSource),3)
        invalid=next(dict(r) for t,r in self.rows if t=='evidence_records')
        invalid.update(id='HUMAN-EVIDENCE-FORBIDDEN',source='HUMAN')
        with self.assertRaises(sqlite3.IntegrityError):insert(self.db,'evidence_records',invalid)

    def test_09_only_task_status_update_allowed(self):
        self.commit_fixture()
        self.db.execute("UPDATE analysis_tasks SET status='ARCHIVED'")
        self.assertEqual(self.db.execute('SELECT status FROM analysis_tasks').fetchone()[0],'ARCHIVED')
        for table in EXPECTED:
            columns=[r[1] for r in self.db.execute(f'PRAGMA table_info({table})')]
            for column in columns:
                if table=='analysis_tasks' and column=='status':continue
                with self.subTest(table=table,column=column),self.assertRaisesRegex(sqlite3.IntegrityError,'UPDATE_FORBIDDEN'):
                    self.db.execute(f'UPDATE {table} SET {column}={column}')
            with self.subTest(table=table),self.assertRaisesRegex(sqlite3.IntegrityError,'DELETE_FORBIDDEN'):
                self.db.execute(f'DELETE FROM {table}')

    def test_10_replace_cannot_overwrite_immutable_identity(self):
        self.commit_fixture()
        for table,row in self.rows:
            for command in ('INSERT', 'INSERT OR REPLACE'):
                with self.subTest(table=table,command=command),self.assertRaisesRegex(sqlite3.IntegrityError,'IMMUTABLE_IDENTITY'):
                    self.db.execute(f'{command} INTO {table} ({",".join(row)}) VALUES ({",".join("?" for _ in row)})',tuple(row.values()))

    def test_11_rollback_failure_at_every_insert_step(self):
        for failure in range(len(self.rows)):
            with self.subTest(failure=failure):
                self.db.execute('BEGIN')
                try:
                    for step,(table,row) in enumerate(self.rows):
                        insert(self.db,table,row)
                        if step==failure:raise RuntimeError('injected transaction failure')
                    self.fail('injection was not reached')
                except RuntimeError:self.db.rollback()
                for table in EXPECTED:self.assertEqual(self.db.execute(f'SELECT count(*) FROM {table}').fetchone()[0],0)

    def test_12_deferred_commit_failure_rolls_back(self):
        self.db.execute('BEGIN')
        for table,row in self.rows:
            if table!='analysis_final_evidence':insert(self.db,table,row)
        with self.assertRaises(sqlite3.IntegrityError):self.db.commit()
        self.db.rollback()
        for table in EXPECTED:self.assertEqual(self.db.execute(f'SELECT count(*) FROM {table}').fetchone()[0],0)

    def test_13_pending_events_and_prepared_before_final_report(self):
        self.db.execute('BEGIN')
        for table,row in self.rows:
            if table in ('analysis_tasks','evidence_records','analysis_human_overrides') or (
                    table=='analysis_snapshots' and row['snapshot_kind']=='PREPARED') or (
                    table=='analysis_confirmation_events' and row['target']!='FINAL_REPORT'):
                insert(self.db,table,row)
        self.db.commit()
        self.assertEqual(self.db.execute('SELECT count(*) FROM analysis_reports').fetchone()[0],0)
        self.assertGreater(self.db.execute('SELECT count(*) FROM analysis_confirmation_events').fetchone()[0],0)

    def test_14_correction_revision_preserves_history_and_explicit_links(self):
        self.commit_fixture()
        old=self.db.execute('SELECT payload_json FROM analysis_snapshots WHERE snapshot_kind=\'FINAL\'').fetchone()[0]
        correction=self.flow.reject('market_view','REVIEW_FIELD',actor_id=ACTOR,
                                    reason='事后发现原判断需修订',created_at=STAMP)
        insert(self.db,'analysis_confirmation_events',dict(**correction.to_dict(),
               event_sequence=len(self.flow.events)-1))
        self.db.commit()
        flow=self.flow.new_revision(analysis_report_id='REPORT-011-002')
        confirm_remaining(flow)
        prepared=flow.prepare_final_snapshot()
        flow.confirm_final_report(prepared,actor_id=ACTOR,created_at=STAMP)
        final=finalize_analysis(flow,prepared,evidence_records=source_records(flow.run_evidence),
                                final_evidence_id='FINAL-EVIDENCE-011-002')
        self.db.execute('BEGIN')
        for table,row in revision_rows(flow,prepared,final):insert(self.db,table,row)
        self.db.commit()
        report=self.db.execute('SELECT * FROM analysis_reports WHERE id=?',(final.snapshot.analysis_report_id,)).fetchone()
        evidence=self.db.execute('SELECT * FROM analysis_final_evidence WHERE final_evidence_id=?',(final.evidence.final_evidence_id,)).fetchone()
        self.assertEqual(report['supersedes_analysis_report_id'],self.final.snapshot.analysis_report_id)
        self.assertEqual(evidence['supersedes_final_evidence_id'],self.final.evidence.final_evidence_id)
        self.assertNotEqual(report['revision_id'],self.final.snapshot.revision_id)
        self.assertEqual(self.db.execute('SELECT payload_json FROM analysis_snapshots WHERE snapshot_hash=?',
                        (self.final.snapshot.final_snapshot_hash,)).fetchone()[0],old)
        self.assertEqual(list(self.db.execute('PRAGMA foreign_key_check')),[])

    def test_15_missing_supersedes_target_is_rejected(self):
        self.commit_fixture()
        for table, key, predecessor in (
                ('analysis_reports', 'id', 'supersedes_analysis_report_id'),
                ('analysis_final_evidence', 'final_evidence_id', 'supersedes_final_evidence_id'),
                ('evidence_records', 'id', 'supersedes_evidence_id')):
            row=next(dict(r) for t,r in self.rows if t==table)
            row[key]='NEW-' + table
            if table=='analysis_reports':
                row.update(revision_id='NEW-REVISION', final_evidence_id='NEW-FINAL-EVIDENCE')
            if table=='analysis_final_evidence':
                row['analysis_report_id']='NEW-REPORT'
            if 'final_snapshot_hash' in row:
                row['final_snapshot_hash']='a' * 64
            row[predecessor]='MISSING-PREDECESSOR'
            with self.subTest(table=table), self.assertRaisesRegex(sqlite3.IntegrityError,'FOREIGN KEY'):
                insert(self.db,table,row)
            row[predecessor]=row[key]
            with self.subTest(table=table), self.assertRaisesRegex(sqlite3.IntegrityError,'CHECK constraint failed'):
                insert(self.db,table,row)

    def test_16_stable_json_and_invalid_json(self):
        value={'中文':{'array':[None,True,1,1.0],'time':STAMP},'first':'文本'}
        canonical=encode(value)
        self.assertEqual(canonical,encode(dict(reversed(list(value.items())))))
        decoded=OutputParser().parse(self.db.execute('SELECT CAST(? AS TEXT)',(canonical,)).fetchone()[0])
        self.assertEqual(encode(decoded),canonical)
        self.assertEqual(digest(decoded),digest(value))
        for invalid in ('{"a":1,"a":2}','{"a":1,"\\u0061":2}','{"x":NaN}',
                        '{"x":Infinity}','{"x":1e999}'):
            with self.subTest(invalid=invalid),self.assertRaises(ValueError):OutputParser().parse(invalid)
        for invalid in ({1:'value'},{'x':float('nan')},{'x':float('inf')}):
            with self.assertRaises(ValueError):encode(invalid)

    def test_17_real_inventory_projection_complete_without_business_counts(self):
        text=Path('docs/DB_COMPATIBILITY_MAPPING_011.md').read_text()
        block=text.split('<!-- G0_INVENTORY_BEGIN -->',1)[1].split('<!-- G0_INVENTORY_END -->',1)[0]
        data=json.loads(block.split('```json\n',1)[1].split('\n```',1)[0])
        self.assertEqual(len(data['tables']),data['table_count'])
        self.assertEqual(data['table_count'],32)
        self.assertTrue(set(EXPECTED).isdisjoint(t['name'] for t in data['tables']))
        self.assertEqual(sum(len(t['indexes']) for t in data['tables']),226)
        self.assertEqual(sum(len(t['foreign_keys']) for t in data['tables']),11)
        self.assertTrue(all('row_count' not in t for t in data['tables']))
        self.assertEqual(data['quick_check'],'ok')
        self.assertEqual(data['journal_mode'],'wal')
        canonical=json.dumps(data,ensure_ascii=False,sort_keys=True,separators=(',',':'))
        self.assertIn(hashlib.sha256(canonical.encode()).hexdigest(),text)
        local=Path('docs/freeze/DB_EXISTING_SCHEMA_INVENTORY_V1.json')
        if local.exists():  # PR checkout needs only the embedded projection.
            raw=local.read_bytes()
            self.assertEqual(hashlib.sha256(raw).hexdigest(),'c9eaf6d1bc8927c8c0dbdb6dd0404ddc52751b2bcefbbad1c5b66a406fbadbd2')
            original=json.loads(raw)
            self.assertEqual(data['tables'],[{k:t[k] for k in ('name','columns','primary_key','indexes','foreign_keys')}
                                            for t in original['tables']])

    def test_18_gates31_33_34_35_and_scope(self):
        frozen=Path('docs/frozen/DB_SCHEMA_V1.1.md').read_text()
        for marker in ('VERSION=V1.1','STATUS=FROZEN_FOR_IMPLEMENTATION','SUPERSEDES=DB_SCHEMA_V1.0',
                       'CHANGE_REASON=PERSISTENCE_G0_BLOCKED_UNFREEZE','MIGRATION_POLICY=ADDITIVE_ONLY'):
            self.assertIn(marker,frozen)
        for table in EXPECTED:
            self.assertIn('### '+table,frozen)
            for definition in schema.TABLE_DEFINITIONS[table]:
                if not definition.startswith(('CHECK(', 'UNIQUE(')):self.assertIn('`'+definition.split()[0]+'`',frozen)
        decision=Path('docs/decisions/DB_SCHEMA_V1_1_PERSISTENCE_UNFREEZE_20261003.md').read_text()
        for marker in ('#011_SCHEMA_UNFREEZE_DECISION=APPROVED','QI_DECISION=OPTION_A',
                       'DB_SCHEMA_TARGET=V1.1','UNFREEZE_SCOPE=DB_SCHEMA_ONLY'):self.assertIn(marker,decision)
        self.assertIn('只有 PERSISTENCE_READINESS=READY 才允许定义 #012 正式 migration',frozen)
        self.assertFalse(re.search(r'\b(DROP|ALTER)\s+(TABLE|INDEX)|\bRENAME\s+TO',schema.TEST_SCHEMA_DDL,re.I))
        original=subprocess.check_output(['git','show',BASELINE+':docs/frozen/DB_SCHEMA_V1.0.md'])
        self.assertEqual(Path('docs/frozen/DB_SCHEMA_V1.0.md').read_bytes(),original)
        # #011 historical scope remains frozen; #012 adds only its approved files.
        historical=subprocess.check_output(['git','diff',BASELINE,IMPLEMENTATION_BASELINE,'--name-only']).decode().splitlines()
        self.assertTrue(set(historical)<=WHITELIST,historical)
        changed=subprocess.check_output(['git','diff',IMPLEMENTATION_BASELINE,
            '00ecb1518daa9d7bf63b53712ace9cb2b076af7f','--name-only']).decode().splitlines()
        self.assertTrue(set(changed)<=IMPLEMENTATION_WHITELIST,changed)
        self.assertEqual({str(p) for p in Path('migration').glob('*.sql')},
                         {'migration/003_db_schema_v1_1_additive.sql'})
        for path in ('persistence/schema_v1_1.py','docs/frozen/DB_SCHEMA_V1.1.md'):
            self.assertEqual(Path(path).read_bytes(),subprocess.check_output(
                ['git','show',IMPLEMENTATION_BASELINE+':'+path]))

    def test_19_one_prepared_per_report_revision_and_one_final_per_report(self):
        self.commit_fixture()
        row=next(dict(r) for t,r in self.rows
                 if t=='analysis_snapshots' and r['snapshot_kind']=='PREPARED')
        row['snapshot_hash']='a' * 64
        with self.assertRaisesRegex(sqlite3.IntegrityError,
                r'UNIQUE constraint failed: analysis_snapshots.analysis_report_id, '
                r'analysis_snapshots.revision_id, analysis_snapshots.snapshot_kind'):
            insert(self.db,'analysis_snapshots',row)
        # The same report may have a PREPARED candidate for a different revision.
        row['revision_id']='ANOTHER-REVISION'
        insert(self.db,'analysis_snapshots',row)
        self.db.commit()
        row.update(snapshot_hash='b' * 64, snapshot_kind='FINAL')
        with self.assertRaisesRegex(sqlite3.IntegrityError,
                r'UNIQUE constraint failed: analysis_snapshots.analysis_report_id$'):
            insert(self.db,'analysis_snapshots',row)

    def test_20_multiple_successors_preserve_all_three_predecessor_links(self):
        self.commit_fixture()
        self.flow.reject('market_view','REVIEW_FIELD',actor_id=ACTOR,
                         reason='需要修订',created_at=STAMP)
        original=next(dict(r) for t,r in self.rows if t=='evidence_records')
        for ordinal in (1,2):
            flow=self.flow.new_revision(analysis_report_id=f'REPORT-SUCCESSOR-{ordinal}')
            confirm_remaining(flow)
            prepared=flow.prepare_final_snapshot()
            flow.confirm_final_report(prepared,actor_id=ACTOR,created_at=STAMP)
            final=finalize_analysis(flow,prepared,evidence_records=source_records(flow.run_evidence),
                                    final_evidence_id=f'FINAL-EVIDENCE-SUCCESSOR-{ordinal}')
            for table,row in revision_rows(flow,prepared,final):
                insert(self.db,table,row)
            evidence=dict(original, id=f'EVIDENCE-SUCCESSOR-{ordinal}',
                          supersedes_evidence_id=original['id'])
            insert(self.db,'evidence_records',evidence)
        self.db.commit()
        for table, predecessor, identity in (
                ('analysis_reports','supersedes_analysis_report_id',self.final.snapshot.analysis_report_id),
                ('analysis_final_evidence','supersedes_final_evidence_id',self.final.evidence.final_evidence_id),
                ('evidence_records','supersedes_evidence_id',original['id'])):
            with self.subTest(table=table):
                self.assertEqual(self.db.execute(
                    f'SELECT count(*) FROM {table} WHERE {predecessor}=?',(identity,)).fetchone()[0],2)
        self.assertEqual(list(self.db.execute('PRAGMA foreign_key_check')),[])

if __name__ == '__main__':
    unittest.main(verbosity=2)
