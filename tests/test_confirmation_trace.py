"""Final conclusion drill-down and a complete zero-I/O #010 demonstration."""

from contextlib import ExitStack
from dataclasses import replace
import hashlib
import json
import unittest
from unittest.mock import patch

from analysis import (
    AnalysisPackageBuilder, AnalysisRunEvidence, ConfirmationError, ConfirmationFlow, PromptBuilder,
    SevenFieldValidator, finalize_analysis, trace_final_analysis, validate_finalization_result,
)
from analysis.output_contracts import digest
from evidence import EvidenceSource, EvidenceTrace, EvidenceValidationResult, HumanConfirm, ValidationStatus
from test_analysis_output import deterministic_response
from test_analysis_package import package_arguments
from test_finalization import final_result, reviewed_flow
from test_human_confirmation import ACTOR, STAMP, changed_field, confirm_remaining, make_flow, source_records


def offline_demonstration():
    """Arrange synthetic sealed #009 inputs before guarding the entire #010 flow.

    No AnalysisRunner/executor invocation is necessary: #010 consumes Gate outputs.
    The JSON text here is a fixture, not a live or regenerated model response.
    """
    envelope = PromptBuilder().build(AnalysisPackageBuilder().build(**package_arguments(quote=True)))
    original_response = json.dumps(deterministic_response(envelope), ensure_ascii=False)
    draft = SevenFieldValidator().validate(original_response, envelope)
    run = AnalysisRunEvidence.from_draft(envelope, draft, model_version="OFFLINE-FIXTURE-010")
    records = source_records(run)
    originals = draft.to_dict(), run.to_dict(), tuple(records)
    with ExitStack() as stack:
        # Block I/O and every upstream generation/fetch/normalization entry point
        # after the already-gated fixture is assembled.
        targets = (
            "builtins.open", "pathlib.Path.open", "pathlib.Path.write_text", "pathlib.Path.write_bytes",
            "socket.socket", "socket.create_connection", "sqlite3.connect", "subprocess.Popen",
            "analysis.AnalysisRunner.run", "analysis.AnalysisPackageBuilder.build",
            "analysis.PromptBuilder.build", "analysis.SevenFieldValidator.validate",
            "evidence.EvidenceBuilder.build", "normalizer.DataNormalizer.normalize",
            "normalizer.InformationNormalizer.normalize", "providers.TushareMCPProvider.fetch",
            "providers.TdxLocalHttpProvider.fetch", "providers.TdxInformationProvider.fetch",
        )
        guards = [stack.enter_context(patch(target, side_effect=AssertionError("forbidden #010 side effect")))
                  for target in targets]
        flow = ConfirmationFlow(draft, run)
        assert all(flow.current(*item.key) is HumanConfirm.PENDING for item in flow.items)
        flow.confirm("operation_plan", "REVIEW_ADVICE", actor_id=ACTOR, created_at=STAMP)
        flow.reject("t_strategy", "REVIEW_ADVICE", actor_id=ACTOR, reason="暂不采纳 T 策略", created_at=STAMP)
        override = flow.modify("risk_level", "REVIEW_FIELD", changed_field(flow), actor_id=ACTOR,
                               reason="人工调高风险判断", referenced_evidence_ids=("EV-DAILY",), created_at=STAMP)
        confirm_remaining(flow)
        candidate = flow.prepare_final_snapshot()
        flow.confirm_final_report(candidate, actor_id=ACTOR, created_at=STAMP)
        result = finalize_analysis(flow, candidate, evidence_records=records)
        trace = trace_final_analysis(result, draft, run, evidence_records=records)
        validate_finalization_result(result, draft, run)
        assert originals == (draft.to_dict(), run.to_dict(), tuple(records))
        assert result.snapshot.seven_fields["t_strategy"]["EFFECTIVE_VALUE"] is None
        assert result.snapshot.seven_fields["risk_level"]["EFFECTIVE_VALUE"] == override.replacement
        assert result.conclusion.evidence_ids == run.used_evidence_ids
        assert trace.complete
        assert digest(draft.output) == result.evidence.validated_output_hash
        input_view = envelope.to_dict()
        for name in ("input_hash", "NETWORK_CALL_COUNT", "MODEL_CALL_COUNT", "DATABASE_WRITE"):
            input_view.pop(name)
        assert digest(input_view) == result.evidence.input_hash
        assert hashlib.sha256(original_response.encode("utf-8")).hexdigest() == result.evidence.model_output_hash
        assert digest(result.snapshot.confirmation_events) == result.evidence.confirmation_log_hash
        for guard in guards:
            guard.assert_not_called()
    return {
        "E2E_HUMAN_CONFIRMATION_FLOW": "PASS", "INPUT_EVIDENCE_COUNT": len(run.input_evidence_ids),
        "USED_EVIDENCE_COUNT": len(run.used_evidence_ids), "CONFIRMATION_EVENT_COUNT": len(flow.events),
        "FINAL_FIELDS": len(result.snapshot.seven_fields), "TRACE_COMPLETE": trace.complete,
        "AI_ORIGINAL_UNCHANGED": True, "ACTOR_ID_BOUND": "YES", "ACTOR_DB_VERIFIED": "NO",
        "DATABASE_WRITE": 0, "MODEL_CALL": 0, "PROVIDER_CALL": 0, "NORMALIZER_CALL": 0,
        "RAW_WRITE": 0, "TRADING_WRITE": 0, "BROKER_INTEGRATION": 0,
    }


class FinalTraceTests(unittest.TestCase):
    def test_existing_evidence_trace_drills_down_to_original_records(self):
        flow, candidate, result = final_result()
        records = source_records(flow.run_evidence)
        trace = EvidenceTrace(records, (result.conclusion,)).query(result.snapshot.analysis_report_id)
        self.assertTrue(trace.complete)
        self.assertEqual(tuple(item["evidence_id"] for item in trace.chain), flow.run_evidence.used_evidence_ids)
        self.assertTrue(all(item["raw_response_path"].startswith("fixtures/run/collection/") for item in trace.chain))
        self.assertEqual(trace.chain[0]["calculation"]["final_evidence_id"], result.evidence.final_evidence_id)

    def test_bridge_trace_checks_hash_and_conclusion_bindings(self):
        flow, candidate, result = final_result()
        altered = replace(result, conclusion=replace(result.conclusion, evidence_ids=flow.run_evidence.input_evidence_ids))
        with self.assertRaises(ConfirmationError):
            trace_final_analysis(altered, flow.draft, flow.run_evidence, evidence_records=source_records(flow.run_evidence))

    def test_used_only_not_unused_input_is_conclusion_support(self):
        flow, candidate, result = final_result()
        self.assertIn("EV-QUOTE", result.evidence.input_evidence_ids)
        self.assertNotIn("EV-QUOTE", result.conclusion.evidence_ids)
        self.assertEqual(result.conclusion.evidence_ids, flow.run_evidence.used_evidence_ids)
        self.assertEqual(result.conclusion.model_version, flow.run_evidence.model_version)

    def test_rejected_used_evidence_blocks_finalization(self):
        flow, candidate = reviewed_flow()
        flow.confirm_final_report(candidate, actor_id=ACTOR)
        records = tuple(replace(item, human_confirm=HumanConfirm.REJECTED) for item in source_records(flow.run_evidence))
        with self.assertRaisesRegex(ConfirmationError, "CONCLUSION_EVIDENCE_REJECTED_OR_INVALID"):
            finalize_analysis(flow, candidate, evidence_records=records)
        self.assertIsNone(flow._final_evidence_id)

    def test_rejected_unused_evidence_not_promoted_to_support(self):
        flow, candidate = reviewed_flow(quote=True)
        flow.confirm_final_report(candidate, actor_id=ACTOR)
        records = tuple(replace(item, human_confirm=HumanConfirm.REJECTED) if item.evidence_id == "EV-QUOTE" else item
                        for item in source_records(flow.run_evidence))
        result = finalize_analysis(flow, candidate, evidence_records=records)
        self.assertEqual(result.conclusion.evidence_ids, ("EV-DAILY",))

    def test_pending_collection_evidence_allowed_by_frozen_design(self):
        flow, candidate, result = final_result()
        trace = trace_final_analysis(result, flow.draft, flow.run_evidence,
                                    evidence_records=source_records(flow.run_evidence))
        self.assertTrue(trace.complete)
        self.assertTrue(all(record.human_confirm is HumanConfirm.PENDING for record in source_records(flow.run_evidence)))

    def test_missing_used_evidence_blocks_finalization(self):
        flow, candidate = reviewed_flow()
        flow.confirm_final_report(candidate, actor_id=ACTOR)
        with self.assertRaisesRegex(ConfirmationError, "CONCLUSION_EVIDENCE_MISSING"):
            finalize_analysis(flow, candidate, evidence_records=())

    def test_failed_unvalidated_used_evidence_blocks_finalization(self):
        for state in (ValidationStatus.FAIL, ValidationStatus.NOT_VALIDATED):
            flow, candidate = reviewed_flow()
            flow.confirm_final_report(candidate, actor_id=ACTOR)
            records = tuple(replace(item, validation_result=EvidenceValidationResult(state)) for item in source_records(flow.run_evidence))
            with self.subTest(state=state), self.assertRaises(ConfirmationError):
                finalize_analysis(flow, candidate, evidence_records=records)

    def test_missing_trace_metadata_and_sidecar_hashes_rejected(self):
        changes = [(name, None) for name in ("raw_response_path", "request_timestamp", "raw_response_hash", "normalized_data_hash")]
        changes += [("request_timestamp", "2026-10-03T12:00:00"), ("raw_response_path", " ")]
        for name, value in changes:
            flow, candidate = reviewed_flow()
            flow.confirm_final_report(candidate, actor_id=ACTOR)
            records = tuple(replace(item, **{name: value}) for item in source_records(flow.run_evidence))
            with self.subTest(field=name), self.assertRaises(ConfirmationError):
                finalize_analysis(flow, candidate, evidence_records=records)

    def test_duplicate_evidence_ids_rejected(self):
        flow, candidate = reviewed_flow()
        flow.confirm_final_report(candidate, actor_id=ACTOR)
        records = source_records(flow.run_evidence)
        with self.assertRaises(ConfirmationError): finalize_analysis(flow, candidate, evidence_records=records + records)

    def test_human_fourth_source_rejected(self):
        self.assertEqual(len(EvidenceSource), 3)
        with self.assertRaises(ValueError): EvidenceSource("HUMAN")
        flow, candidate = reviewed_flow()
        flow.confirm_final_report(candidate, actor_id=ACTOR)
        records = tuple(replace(item, source="HUMAN") for item in source_records(flow.run_evidence))
        with self.assertRaises(ConfirmationError): finalize_analysis(flow, candidate, evidence_records=records)

    def test_string_confirmation_state_cannot_bypass_typed_evidence_gate(self):
        flow, candidate = reviewed_flow()
        flow.confirm_final_report(candidate, actor_id=ACTOR)
        records = tuple(replace(item, human_confirm="PENDING") for item in source_records(flow.run_evidence))
        with self.assertRaises(ConfirmationError): finalize_analysis(flow, candidate, evidence_records=records)

    def test_current_rejected_record_does_not_support_historical_trace(self):
        flow, candidate, result = final_result()
        records = tuple(replace(item, human_confirm=HumanConfirm.REJECTED) for item in source_records(flow.run_evidence))
        trace = EvidenceTrace(records, (result.conclusion,)).query(result.snapshot.analysis_report_id)
        self.assertFalse(trace.complete)
        self.assertIn("EV-DAILY.human_confirm_rejected", trace.missing)
        with self.assertRaises(ConfirmationError):
            trace_final_analysis(result, flow.draft, flow.run_evidence, evidence_records=records)

    def test_post_finalization_report_correction_keeps_historical_trace(self):
        flow, candidate, result = final_result()
        flow.reject_final_report(candidate, actor_id=ACTOR, reason="报告事后纠错")
        trace = trace_final_analysis(result, flow.draft, flow.run_evidence,
                                    evidence_records=source_records(flow.run_evidence))
        self.assertTrue(trace.complete)
        self.assertIs(flow.current("FINAL_REPORT", "REVIEW_FINAL_REPORT"), HumanConfirm.REJECTED)
        self.assertIs(result.snapshot.human_confirm, HumanConfirm.CONFIRMED)

    def test_human_unused_input_reference_stays_override_context(self):
        flow = make_flow(quote=True)
        flow.modify("risk_level", "REVIEW_FIELD", changed_field(flow), actor_id=ACTOR,
                    reason="人工判断", referenced_evidence_ids=("EV-QUOTE",))
        confirm_remaining(flow)
        candidate = flow.prepare_final_snapshot()
        _, _, result = final_result(flow, candidate)
        self.assertEqual(result.snapshot.human_overrides[0]["referenced_evidence_ids"], ("EV-QUOTE",))
        self.assertNotIn("EV-QUOTE", result.conclusion.evidence_ids)

    def test_rejected_human_override_reference_cannot_support_effective_conclusion(self):
        flow = make_flow(quote=True)
        flow.modify("risk_level", "REVIEW_FIELD", changed_field(flow), actor_id=ACTOR,
                    reason="人工判断", referenced_evidence_ids=("EV-QUOTE",))
        confirm_remaining(flow)
        candidate = flow.prepare_final_snapshot()
        flow.confirm_final_report(candidate, actor_id=ACTOR)
        records = tuple(replace(item, human_confirm=HumanConfirm.REJECTED) if item.evidence_id == "EV-QUOTE" else item
                        for item in source_records(flow.run_evidence))
        with self.assertRaisesRegex(ConfirmationError, "CONCLUSION_EVIDENCE_REJECTED_OR_INVALID"):
            finalize_analysis(flow, candidate, evidence_records=records)
        self.assertIsNone(flow._final_evidence_id)

    def test_missing_human_override_reference_blocks_finalization(self):
        flow = make_flow(quote=True)
        flow.modify("risk_level", "REVIEW_FIELD", changed_field(flow), actor_id=ACTOR,
                    reason="人工判断", referenced_evidence_ids=("EV-QUOTE",))
        confirm_remaining(flow)
        candidate = flow.prepare_final_snapshot()
        flow.confirm_final_report(candidate, actor_id=ACTOR)
        records = tuple(item for item in source_records(flow.run_evidence) if item.evidence_id != "EV-QUOTE")
        with self.assertRaisesRegex(ConfirmationError, "CONCLUSION_EVIDENCE_MISSING"):
            finalize_analysis(flow, candidate, evidence_records=records)

    def test_current_rejected_override_context_blocks_historical_bridge_trace(self):
        flow = make_flow(quote=True)
        flow.modify("risk_level", "REVIEW_FIELD", changed_field(flow), actor_id=ACTOR,
                    reason="人工判断", referenced_evidence_ids=("EV-QUOTE",))
        confirm_remaining(flow)
        candidate = flow.prepare_final_snapshot()
        _, _, result = final_result(flow, candidate)
        records = tuple(replace(item, human_confirm=HumanConfirm.REJECTED) if item.evidence_id == "EV-QUOTE" else item
                        for item in source_records(flow.run_evidence))
        with self.assertRaises(ConfirmationError):
            trace_final_analysis(result, flow.draft, flow.run_evidence, evidence_records=records)

    def test_no_evidence_draft_preserves_review_but_cannot_finalize(self):
        arguments = package_arguments()
        arguments["canonical_data"] = {}
        arguments["evidence_metadata"] = []
        arguments["evidence_links"] = {}
        arguments["source_status"]["TUSHARE_MCP"] = "MISSING"
        flow, candidate = reviewed_flow(arguments=arguments)
        self.assertEqual(flow.run_evidence.used_evidence_ids, ())
        self.assertEqual(flow.draft.final_state, "UNCONFIRMED")
        self.assertTrue(candidate.watch_items)
        flow.confirm_final_report(candidate, actor_id=ACTOR)
        before = candidate.to_dict(), flow.events, flow.draft.to_dict(), flow.run_evidence.to_dict()
        with ExitStack() as stack:
            factories = [stack.enter_context(patch("analysis.finalization." + name))
                         for name in ("FinalAnalysisSnapshot", "FinalAnalysisEvidence", "AnalysisConclusion")]
            with self.assertRaisesRegex(ConfirmationError, "MODEL_OUTPUT_NEEDS_EVIDENCE"):
                finalize_analysis(flow, candidate, evidence_records=())
            for factory in factories:
                factory.assert_not_called()
        self.assertEqual(before, (candidate.to_dict(), flow.events, flow.draft.to_dict(), flow.run_evidence.to_dict()))
        self.assertIsNone(flow._final_evidence_id)
        self.assertIsNone(flow._final_snapshot_hash)
        self.assertEqual(candidate.final_state, "PREPARED")
        flow.validate()

    def test_unused_input_evidence_cannot_promote_unevidenced_output_to_final(self):
        envelope = PromptBuilder().build(AnalysisPackageBuilder().build(**package_arguments()))
        response = deterministic_response(envelope)
        for field in response["analysis"].values():
            field["evidence_ids"] = []
            field["content"]["facts"] = []
            field["data_quality_status"] = "MISSING"
            field["time_range"] = {"start": None, "end": None}
        response["analysis"]["market_view"]["content"]["statement"] = "证据不足，趋势不明"
        response["analysis"]["reasoning"]["content"]["steps"] = [
            {"statement": "输入缺失，无法形成有证据支撑的结论", "evidence_ids": []}]
        draft = SevenFieldValidator().validate(json.dumps(response, ensure_ascii=False), envelope)
        run = AnalysisRunEvidence.from_draft(envelope, draft, model_version="OFFLINE-FIXTURE-010")
        self.assertEqual(run.input_evidence_ids, ("EV-DAILY",))
        self.assertEqual(run.used_evidence_ids, ())
        flow = ConfirmationFlow(draft, run)
        confirm_remaining(flow)
        candidate = flow.prepare_final_snapshot()
        flow.confirm_final_report(candidate, actor_id=ACTOR)
        with self.assertRaisesRegex(ConfirmationError, "MODEL_OUTPUT_NEEDS_EVIDENCE"):
            finalize_analysis(flow, candidate, evidence_records=source_records(run))
        self.assertIsNone(flow._final_evidence_id)

    def test_complete_offline_flow_and_zero_side_effects(self):
        result = offline_demonstration()
        self.assertEqual(result["E2E_HUMAN_CONFIRMATION_FLOW"], "PASS")
        self.assertTrue(result["AI_ORIGINAL_UNCHANGED"])
        self.assertEqual((result["INPUT_EVIDENCE_COUNT"], result["USED_EVIDENCE_COUNT"]), (2, 1))
        for counter in ("DATABASE_WRITE", "MODEL_CALL", "PROVIDER_CALL", "NORMALIZER_CALL",
                        "RAW_WRITE", "TRADING_WRITE", "BROKER_INTEGRATION"):
            self.assertEqual(result[counter], 0)


if __name__ == "__main__":
    unittest.main()
