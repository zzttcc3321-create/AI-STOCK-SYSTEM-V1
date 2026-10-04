"""Offline projection regressions: human acceptance never verifies input data."""
import json
import sqlite3
import unittest

from analysis import (AnalysisPackageBuilder, AnalysisRunEvidence, ConfirmationFlow,
                      PromptBuilder, SevenFieldValidator, finalize_analysis)
from analysis.contracts import jsonable
from analysis.output_contracts import validate_draft
from persistence.connection import configure
from persistence.migration import apply_migration
from persistence.read_models import AnalysisReadRepository, ConfirmationReadRepository, EvidenceReadRepository
from persistence.repository import SQLitePersistenceRepository
from test_analysis_output import deterministic_response
from test_analysis_package import package_arguments
from test_knowability import financial, CUTOFF
from test_freshness import quote_input, package_for
from normalizer import DataNormalizer
from test_human_confirmation import ACTOR, STAMP, changed_field, confirm_remaining, source_records


class LimitationFidelityTests(unittest.TestCase):
    def flow(self, status):
        args = package_arguments()
        args['source_status']['TUSHARE_MCP'] = status
        args['canonical_data']['daily_kline'][0]['data_quality_status'] = status
        args['validation_warnings'] = ['specific upstream limitation']
        package = AnalysisPackageBuilder().build(**args)
        envelope = PromptBuilder().build(package)
        response = deterministic_response(envelope)
        # Missing availability may summarize the field, but must not erase
        # WARNING/UNVERIFIED or its reasons from its separately sealed context.
        response['analysis']['risk_level']['content']['limitations'] = []
        response['analysis']['risk_level']['data_quality_status'] = status
        draft = SevenFieldValidator().validate(json.dumps(response), envelope)
        run = AnalysisRunEvidence.from_draft(envelope, draft, model_version='OFFLINE-D05')
        return package, envelope, response, ConfirmationFlow(draft, run)

    def test_prompt_draft_plan_override_final_and_read_projections(self):
        for status in ('WARNING', 'UNVERIFIED'):
            with self.subTest(status=status):
                package, envelope, response, flow = self.flow(status)
                context = jsonable(flow.draft.limitation_context)
                self.assertEqual(context['data_quality_report'], package.to_dict()['data_quality'])
                self.assertIn('specific upstream limitation', context['data_quality_report']['validation_warnings'])
                self.assertEqual(context['data_quality_report']['data_quality_status'], status)
                self.assertIn('aggregation_rule', package.daily_kline_summary)
                self.assertEqual(json.loads(envelope.user_prompt.split('\n')[1])['data_quality_report'], context['data_quality_report'])
                self.assertTrue(any(x['status'] == status for x in context['limitations']))
                with self.assertRaises(TypeError):
                    flow.draft.limitation_context['data_quality_report']['data_quality_status'] = 'PASS'
                validate_draft(flow.draft)
                override = flow.modify('risk_level', 'REVIEW_FIELD', changed_field(flow), actor_id=ACTOR, reason='review judgment', created_at=STAMP)
                self.assertEqual(override.replacement['data_quality_status'], flow.draft.output['risk_level']['data_quality_status'])
                flow.reject('operation_plan', 'REVIEW_ADVICE', actor_id=ACTOR,
                            reason='retain limitations on rejection', created_at=STAMP)
                confirm_remaining(flow)
                prepared = flow.prepare_final_snapshot()
                flow.confirm_final_report(prepared, actor_id=ACTOR, created_at=STAMP)
                final = finalize_analysis(flow, prepared, evidence_records=source_records(flow.run_evidence))
                for snapshot in (prepared, final.snapshot):
                    self.assertIsNone(snapshot.seven_fields['operation_plan']['EFFECTIVE_VALUE'])
                    for field in snapshot.seven_fields.values():
                        self.assertEqual(jsonable(field['input_limitation_context']), context)
                    self.assertTrue(snapshot.watch_items)
                db = configure(sqlite3.connect(':memory:'))
                try:
                    apply_migration(db)
                    repo = SQLitePersistenceRepository(db, allow_synthetic_evidence=True)
                    repo.persist_analysis_run(flow.draft, flow.run_evidence, envelope,
                        task_date='2026-10-03', created_by=ACTOR, raw_model_output=json.dumps(response),
                        evidence_records=source_records(flow.run_evidence), evidence_created_at=STAMP)
                    repo.persist_finalization(flow, prepared, final,
                        evidence_records=source_records(flow.run_evidence), evidence_created_at=STAMP)
                    before = db.total_changes
                    db.execute('PRAGMA query_only=ON')
                    reads = AnalysisReadRepository(db)
                    for report in (reads.analysis_history(flow.run_evidence.stock_id)[0],
                                   reads.latest_confirmed_analysis(flow.run_evidence.stock_id)['analysis']):
                        self.assertEqual(report['watch_items'], final.snapshot.to_dict()['watch_items'])
                        self.assertEqual(report['seven_fields']['risk_level']['input_limitation_context'], context)
                    state = ConfirmationReadRepository(db).current_confirmation_state(flow.analysis_report_id)
                    details = [x['details'] for x in state['event_limitation_details'] if x['reason_code'] == 'REVIEW_DATA_QUALITY']
                    self.assertTrue(details)
                    self.assertTrue(all(x['input_limitation_context'] == context for x in details))
                    self.assertTrue(all(x['details']['input_limitation_context'] == context
                                        for x in state['event_limitation_details']))
                    trace = EvidenceReadRepository(db).field_evidence_trace(flow.analysis_report_id, 'risk_level')
                    self.assertEqual(trace['field_value']['input_limitation_context'], context)
                    self.assertEqual(db.total_changes, before)
                finally:
                    db.close()

    def test_warning_prose_does_not_invent_unverified_status(self):
        args = package_arguments()
        args['validation_warnings'] = ['UNVERIFIED is an example label, not a typed status']
        package = AnalysisPackageBuilder().build(**args)
        self.assertEqual(package.data_quality['data_quality_status'], 'WARNING')

    def test_cutoff_and_freshness_reasons_survive_field_projection(self):
        _, _, args = financial(disclosure=None)
        cutoff_package = AnalysisPackageBuilder().build(**args, judgment_cutoff=CUTOFF)
        envelope = quote_input(quote_timestamp="2026-09-30T16:00:00Z")
        result = DataNormalizer().normalize(envelope, units={"price": "元"})
        stale_package, _ = package_for(envelope, result)
        for package in (cutoff_package, stale_package):
            with self.subTest(package=package.data_quality['data_quality_status']):
                prompt = PromptBuilder().build(package)
                response = deterministic_response(prompt)
                draft = SevenFieldValidator().validate(json.dumps(response), prompt)
                context = jsonable(draft.limitation_context)['data_quality_report']
                self.assertEqual(context, package.to_dict()['data_quality'])
                if package is cutoff_package:
                    self.assertTrue(context['knowability_restrictions'])
                    self.assertTrue(any('DISCLOSED_AT_UNVERIFIED' in item['reason_codes']
                                        for item in context['knowability_restrictions']))
                else:
                    self.assertIn('market_quotes.0', context['stale_fields'])
                    self.assertTrue(any('STALE_QUOTE_TIMESTAMP' in reason
                                        for reason in context['validation_warnings']))

    def test_prepared_absent_event_context_is_explicit(self):
        _, envelope, response, flow = self.flow('UNVERIFIED')
        flow.confirm('risk_level', 'REVIEW_FIELD', actor_id=ACTOR, created_at=STAMP)
        db = configure(sqlite3.connect(':memory:'))
        try:
            apply_migration(db)
            repo = SQLitePersistenceRepository(db, allow_synthetic_evidence=True)
            repo.persist_analysis_run(flow.draft, flow.run_evidence, envelope,
                task_date='2026-10-03', created_by=ACTOR, raw_model_output=json.dumps(response),
                evidence_records=source_records(flow.run_evidence), evidence_created_at=STAMP)
            repo.persist_confirmation_progress(flow)
            state = ConfirmationReadRepository(db).current_confirmation_state(flow.analysis_report_id)
            self.assertEqual(state['status'], 'CONFIRMATION_PLAN_UNAVAILABLE')
            self.assertEqual(state['event_limitation_details'][0]['details']['input_limitation_context'],
                             jsonable(flow.draft.limitation_context))
        finally:
            db.close()
