"""Serial canonical DB→indicators→Prompt→review→archive orchestration."""
import copy
from datetime import datetime, timezone
from functools import wraps
from threading import RLock

from analysis import AnalysisPackageBuilder, PromptBuilder, AnalysisRunner, ConfirmationFlow, finalize_analysis
from analysis.indicator_calculator import IndicatorCalculator
from analysis.output_validator import prompt_inputs
from evidence import EvidenceRecord, EvidenceSource, EvidenceValidationResult, ValidationStatus, HumanConfirm
from evidence.builder import sanitize_request_params
from persistence.read_models import EvidenceReadRepository
from persistence.repository import SQLitePersistenceRepository


class RuntimeErrorV1(ValueError):
    pass


def serialized_operation(method):
    @wraps(method)
    def serialized(self, *args, **kwargs):
        # Hold through reads, write authorization, commit/rollback and state updates.
        with self._lock:
            return method(self, *args, **kwargs)
    return serialized


def record_from_archive(value):
    value = dict(value)
    value['source'] = EvidenceSource(value['source'])
    value['human_confirm'] = HumanConfirm(value['human_confirm'])
    value['validation_result'] = EvidenceValidationResult(ValidationStatus(value['validation_result']['status']),
                                                         tuple(value['validation_result']['failures']))
    value['normalization_rule'] = tuple(value['normalization_rule']) if value['normalization_rule'] else None
    value['request_params'] = sanitize_request_params(value['request_params'])
    return EvidenceRecord(**value)


class RecordingExecutor:
    def __init__(self, executor):
        self.executor = executor
        self.raw = None

    @property
    def model_version(self):
        return self.executor.model_version

    def generate(self, envelope, schema):
        self.raw = self.executor.generate(envelope, schema)
        return self.raw


class DashboardRuntime:
    def __init__(self, db, *, archive_writer, executor=None):
        self._lock = RLock()
        self.db = db
        self.writer = archive_writer
        self.executor = executor
        self.repo = SQLitePersistenceRepository(db, archive_reader=archive_writer.reader,
                                                enable_real_provider_evidence=True)
        self.status = 'COLLECTING'
        self.history = [self.status]
        self.package = self.prompt = self.indicators = self.record = None
        self.flow = self.prepared = self.final = None
        self._pending_final = None
        self.evidence_created_at = None

    def _stage(self, state):
        self.status = state
        self.history.append(state)

    @serialized_operation
    def prepare(self, evidence_id):
        if self.flow is not None:
            raise RuntimeErrorV1('ACTIVE_REVIEW_EXISTS')
        self._stage('NORMALIZING')
        # Clear stale input before any failure; never display previous results as current.
        self.package = self.prompt = self.indicators = self.record = None
        self.indicators = IndicatorCalculator(self.db, archive_reader=self.writer.reader).calculate(evidence_id)
        projection = EvidenceReadRepository(self.db, archive_reader=self.writer.reader).evidence(evidence_id)
        if projection.get('canonical_id') is None:
            raise RuntimeErrorV1('V1_1_CANONICAL_ID_REQUIRED')
        self.record = record_from_archive(self.writer.reader.evidence(projection['raw_data_path'], evidence_id))
        data = self.writer.reader.normalized(self.record.raw_response_path,
                                             self.record.normalized_data_hash)['normalized_data']
        links = {'stock_identity': [evidence_id], **{'daily_kline.' + str(n): [evidence_id] for n in range(len(data))}}
        self.package = AnalysisPackageBuilder().build(stock_identity={'stock_id': self.indicators['stock_id']},
            canonical_data={'daily_kline': data}, holding_snapshot=None,
            evidence_metadata=[self.record], evidence_links=links,
            source_status={self.record.source.value: 'PASS'},
            validation_warnings=self.indicators['limitations'])
        self.prompt = PromptBuilder().build(self.package)
        self.evidence_created_at = projection['created_at']
        self._stage('READY_FOR_REVIEW')

    @property
    @serialized_operation
    def data_status(self):
        if self.prompt is None:
            return 'PARTIAL'
        quality = prompt_inputs(self.prompt)['data_quality_report']
        return ('PASS' if quality['data_quality_status'] == 'PASS' and quality['complete_analysis_input'] and
                self.indicators['DATA_STATUS'] == 'PASS' else 'PARTIAL')

    @serialized_operation
    def generate(self, *, actor_id):
        if self.prompt is None or self.executor is None or self.flow is not None:
            raise RuntimeErrorV1('MODEL_EXECUTOR_OR_INPUT_UNAVAILABLE')
        recording = RecordingExecutor(self.executor)
        execution = AnalysisRunner(recording).run(self.package)
        if execution.status != 'UNCONFIRMED':
            raise RuntimeErrorV1(execution.error_code)
        flow = ConfirmationFlow(execution.draft, execution.evidence)
        self.repo.persist_analysis_run(execution.draft, execution.evidence, execution.prompt_envelope,
            task_date=datetime.now(timezone.utc).date().isoformat(), created_by=actor_id,
            raw_model_output=recording.raw, evidence_created_at=self.evidence_created_at,
            evidence_records=(self.record,))
        self.flow = flow
        self.repo.update_task_status(flow.run_evidence.analysis_run_id, 'READY_FOR_REVIEW')

    @serialized_operation
    def decide(self, target, reason_code, *, actor_id, reject=False, reason=''):
        if self.flow is None or self.prepared is not None or self.final is not None:
            raise RuntimeErrorV1('REVIEW_UNAVAILABLE')
        candidate_flow = copy.copy(self.flow)
        method = candidate_flow.reject if reject else candidate_flow.confirm
        method(target, reason_code, actor_id=actor_id, reason=reason)
        self.repo.persist_confirmation_progress(candidate_flow)
        self.flow = candidate_flow

    @serialized_operation
    def prepare_archive(self):
        if self.flow is None or self.prepared is not None:
            raise RuntimeErrorV1('REVIEW_UNAVAILABLE')
        candidate_flow = copy.copy(self.flow)
        candidate = candidate_flow.prepare_final_snapshot()
        self.repo.persist_confirmation_progress(candidate_flow, prepared=candidate)
        self.flow, self.prepared = candidate_flow, candidate

    @serialized_operation
    def confirm_and_archive(self, *, actor_id):
        if self.final is not None:
            return self.final
        if self.prepared is None:
            raise RuntimeErrorV1('FINAL_SNAPSHOT_REVIEW_REQUIRED')
        if self._pending_final is None:
            candidate_flow = copy.copy(self.flow)
            candidate_flow.confirm_final_report(self.prepared, actor_id=actor_id)
            final = finalize_analysis(candidate_flow, self.prepared, evidence_records=(self.record,))
            self._pending_final = candidate_flow, final
        candidate_flow, final = self._pending_final
        if actor_id != final.snapshot.confirmed_by:
            raise RuntimeErrorV1("ARCHIVE_RETRY_ACTOR_MISMATCH")
        # Append-only bindings may precede DB commit; a retry uses the same final IDs.
        self.writer.append_analysis_binding(self.record.raw_response_path, binding=final.evidence.to_dict())
        self.repo.persist_finalization(candidate_flow, self.prepared, final, evidence_records=(self.record,),
                                       evidence_created_at=self.evidence_created_at)
        self.repo.update_task_status(candidate_flow.run_evidence.analysis_run_id, 'ARCHIVED')
        self.flow, self.final = candidate_flow, final
        self._stage('ARCHIVED')
        return final
