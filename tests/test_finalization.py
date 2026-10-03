"""Final report review gates, seals, retained history and report revisions."""

from dataclasses import FrozenInstanceError, fields, replace
import unittest

from analysis import (
    ConfirmationError, finalize_analysis, validate_final_analysis_evidence,
    validate_final_snapshot, validate_finalization_result, validate_prepared_snapshot,
)
from analysis.contracts import jsonable
from analysis.finalization import final_snapshot_content_hash
from analysis.output_contracts import ANALYSIS_FIELDS, digest
from evidence import HumanConfirm
from test_analysis_package import information_package_arguments, package_arguments
from test_human_confirmation import ACTOR, STAMP, changed_field, confirm_remaining, make_flow, source_records


def reviewed_flow(**kwargs):
    flow = make_flow(**kwargs)
    confirm_remaining(flow)
    candidate = flow.prepare_final_snapshot()
    return flow, candidate


def final_result(flow=None, candidate=None):
    if flow is None:
        flow, candidate = reviewed_flow(quote=True)
    flow.confirm_final_report(candidate, actor_id=ACTOR, created_at=STAMP)
    result = finalize_analysis(flow, candidate, evidence_records=source_records(flow.run_evidence))
    return flow, candidate, result


class PreparationTests(unittest.TestCase):
    def test_required_pending_blocks_candidate(self):
        flow = make_flow()
        with self.assertRaisesRegex(ConfirmationError, "REQUIRED_CONFIRMATIONS_PENDING"):
            flow.prepare_final_snapshot()
        self.assertIsNone(flow._prepared)

    def test_each_required_item_independently_blocks_candidate(self):
        initial = make_flow()
        for pending in (item.key for item in initial.items if item.target != "FINAL_REPORT"):
            flow = make_flow()
            for item in flow.items:
                if item.target != "FINAL_REPORT" and item.key != pending:
                    flow.confirm(*item.key, actor_id=ACTOR)
            with self.subTest(pending=pending), self.assertRaises(ConfirmationError): flow.prepare_final_snapshot()

    def test_candidate_is_pending_and_not_final(self):
        flow, candidate = reviewed_flow()
        self.assertIs(candidate.human_confirm, HumanConfirm.PENDING)
        self.assertEqual(candidate.final_state, "PREPARED")
        self.assertEqual(set(candidate.seven_fields), set(ANALYSIS_FIELDS))
        validate_prepared_snapshot(candidate)
        self.assertEqual(candidate.confirmation_log_hash, flow.confirmation_log_hash)

    def test_confirmed_fields_preserve_ai_values(self):
        flow, candidate = reviewed_flow()
        for name, value in candidate.seven_fields.items():
            self.assertEqual(value["DECISION"], "CONFIRMED")
            self.assertEqual(value["EFFECTIVE_VALUE"], flow.draft.output[name])
            self.assertEqual(value["AI_ORIGINAL"], flow.draft.output[name])

    def test_rejected_field_null_effective_with_reason(self):
        flow = make_flow()
        flow.reject("operation_plan", "REVIEW_ADVICE", actor_id=ACTOR, reason="不采纳建议")
        confirm_remaining(flow)
        value = flow.prepare_final_snapshot().seven_fields["operation_plan"]
        self.assertEqual(value["DECISION"], "REJECTED")
        self.assertIsNone(value["EFFECTIVE_VALUE"])
        self.assertTrue(any(item["decision_reason"] == "不采纳建议" for item in value["decision_reasons"]))

    def test_modified_field_shows_original_override_effective_origin(self):
        flow = make_flow()
        override = flow.modify("risk_level", "REVIEW_FIELD", changed_field(flow), actor_id=ACTOR, reason="调高风险")
        confirm_remaining(flow)
        value = flow.prepare_final_snapshot().seven_fields["risk_level"]
        self.assertEqual(value["DECISION"], "MODIFIED")
        self.assertEqual(value["AI_ORIGINAL"], flow.draft.output["risk_level"])
        self.assertEqual(value["EFFECTIVE_VALUE"], override.replacement)
        self.assertEqual(value["HUMAN_OVERRIDE"]["replacement"], override.replacement)
        self.assertEqual((value["HUMAN_DECISION"], value["ORIGIN"]), ("MODIFY", "HUMAN"))
        self.assertEqual(value["AI_OUTPUT_HASH"], flow.run_evidence.model_output_hash)

    def test_other_rejected_reason_wins_over_override(self):
        flow = make_flow()
        value = jsonable(flow.draft.output["reasoning"])
        value["content"]["steps"][0]["statement"] = "依据所引用事实与限制，结论须人工核查"
        flow.modify("reasoning", "REVIEW_FIELD", value, actor_id=ACTOR, reason="修改推理")
        flow.reject("reasoning", "REVIEW_ASSUMPTION", actor_id=ACTOR, reason="假设不成立")
        confirm_remaining(flow)
        field = flow.prepare_final_snapshot().seven_fields["reasoning"]
        self.assertEqual(field["DECISION"], "REJECTED")
        self.assertIsNone(field["EFFECTIVE_VALUE"])

    def test_rejected_override_suppresses_effective_value(self):
        flow = make_flow()
        override = flow.modify("risk_level", "REVIEW_FIELD", changed_field(flow), actor_id=ACTOR, reason="调高风险")
        flow.reject("HUMAN_OVERRIDE:" + override.override_id, "REVIEW_OVERRIDE", actor_id=ACTOR, reason="修改有误")
        confirm_remaining(flow)
        candidate = flow.prepare_final_snapshot()
        self.assertEqual(candidate.seven_fields["risk_level"]["DECISION"], "REJECTED")
        self.assertIsNone(candidate.seven_fields["risk_level"]["EFFECTIVE_VALUE"])
        self.assertTrue(any(item["decision_reason"] == "修改有误" for item in candidate.watch_items))

    def test_rejected_data_quality_suppresses_affected_fields(self):
        flow = make_flow()
        target = "canonical_market_data.market_quotes"
        flow.reject(target, "REVIEW_DATA_QUALITY", actor_id=ACTOR, reason="数据不足需回到上游")
        confirm_remaining(flow)
        candidate = flow.prepare_final_snapshot()
        self.assertTrue(all(item["DECISION"] == "REJECTED" and item["EFFECTIVE_VALUE"] is None
                            for item in candidate.seven_fields.values()))
        self.assertTrue(any(item["target"] == target and item["state"] == "REJECTED"
                            for item in candidate.watch_items))

    def test_warning_items_all_have_explicit_results(self):
        arguments = package_arguments()
        arguments["canonical_data"]["daily_kline"][0]["data_quality_status"] = "WARNING"
        arguments["source_status"]["TUSHARE_MCP"] = "WARNING"
        flow, candidate = reviewed_flow(arguments=arguments)
        quality = [item for item in candidate.confirmation_decisions if item["reason_code"] == "REVIEW_DATA_QUALITY"]
        self.assertTrue(quality)
        self.assertTrue(all(item["state"] == "CONFIRMED" for item in quality))
        self.assertTrue(all(flow.current(*item.key) is not HumanConfirm.PENDING
                            for item in flow.items if item.target != "FINAL_REPORT"))

    def test_unverified_missing_assumption_decisions_visible(self):
        flow, candidate = reviewed_flow(arguments=information_package_arguments())
        decisions = candidate.confirmation_decisions
        self.assertTrue(any(item["target"] == "intelligence_data.recent_news" for item in decisions))
        self.assertTrue(any(item["target"] == "intelligence_data.valuation_summary" for item in decisions))
        self.assertTrue(any(item["reason_code"] == "REVIEW_ASSUMPTION" and item["state"] == "CONFIRMED"
                            for item in decisions))
        self.assertTrue(candidate.watch_items)

    def test_prepare_repeated_for_same_revision_rejected(self):
        flow, candidate = reviewed_flow()
        with self.assertRaises(ConfirmationError): flow.prepare_final_snapshot()
        self.assertIs(flow._prepared, candidate)

    def test_prepared_candidate_immutable_and_replace_loses_seal(self):
        flow, candidate = reviewed_flow()
        with self.assertRaises(FrozenInstanceError): candidate.model_version = "OTHER"
        with self.assertRaises(TypeError): candidate.seven_fields["risk_level"]["DECISION"] = "REJECTED"
        with self.assertRaises(ConfirmationError): validate_prepared_snapshot(replace(candidate))

    def test_forced_prepared_mutation_rejected_before_confirmation(self):
        flow, candidate = reviewed_flow()
        object.__setattr__(candidate, "stock_id", "other")
        with self.assertRaises(ConfirmationError): flow.confirm_final_report(candidate, actor_id=ACTOR)

    def test_modification_after_preparation_forbidden(self):
        flow, candidate = reviewed_flow()
        with self.assertRaises(ConfirmationError):
            flow.modify("risk_level", "REVIEW_FIELD", changed_field(flow), actor_id=ACTOR, reason="改动")

    def test_correction_after_prepare_invalidates_reviewed_candidate(self):
        flow, candidate = reviewed_flow()
        flow.reject("risk_level", "REVIEW_FIELD", actor_id=ACTOR, reason="事后发现错误")
        with self.assertRaisesRegex(ConfirmationError, "PREPARED_SNAPSHOT_STALE"):
            flow.confirm_final_report(candidate, actor_id=ACTOR)


class FinalReportGateTests(unittest.TestCase):
    def test_pending_final_report_blocks_conclusion(self):
        flow, candidate = reviewed_flow()
        with self.assertRaisesRegex(ConfirmationError, "FINAL_REPORT_NOT_CONFIRMED"):
            finalize_analysis(flow, candidate, evidence_records=source_records(flow.run_evidence))
        self.assertIsNone(flow._final_evidence_id)

    def test_rejected_final_report_blocks_conclusion(self):
        flow, candidate = reviewed_flow()
        flow.reject_final_report(candidate, actor_id=ACTOR, reason="不通过最终报告")
        with self.assertRaisesRegex(ConfirmationError, "FINAL_REPORT_NOT_CONFIRMED"):
            finalize_analysis(flow, candidate, evidence_records=source_records(flow.run_evidence))
        with self.assertRaises(ConfirmationError): flow.confirm_final_report(candidate, actor_id=ACTOR)

    def test_cross_revision_candidate_rejected(self):
        flow, candidate = reviewed_flow()
        other, other_candidate = reviewed_flow()
        with self.assertRaises(ConfirmationError): flow.confirm_final_report(other_candidate, actor_id=ACTOR)
        with self.assertRaises(ConfirmationError): finalize_analysis(other, candidate, evidence_records=())

    def test_final_report_confirmation_event_binds_candidate_hash(self):
        flow, candidate = reviewed_flow()
        event = flow.confirm_final_report(candidate, actor_id=ACTOR, created_at=STAMP)
        self.assertEqual(event.reviewed_snapshot_hash, candidate.prepared_snapshot_hash)
        self.assertEqual(event.actor_id, ACTOR)
        self.assertEqual(event.target, "FINAL_REPORT")

    def test_final_report_actor_time_required(self):
        flow, candidate = reviewed_flow()
        for kwargs in ({"actor_id": ""}, {"actor_id": ACTOR, "created_at": "2026-10-03"}):
            with self.assertRaises(ConfirmationError): flow.confirm_final_report(candidate, **kwargs)

    def test_duplicate_report_confirmation_no_duplicate_event(self):
        flow, candidate = reviewed_flow()
        flow.confirm_final_report(candidate, actor_id=ACTOR)
        events = flow.events
        with self.assertRaises(ConfirmationError): flow.confirm_final_report(candidate, actor_id=ACTOR)
        self.assertEqual(events, flow.events)

    def test_duplicate_finalization_rejected(self):
        flow, candidate, result = final_result()
        with self.assertRaisesRegex(ConfirmationError, "FINAL_ANALYSIS_ALREADY_CREATED"):
            finalize_analysis(flow, candidate, evidence_records=source_records(flow.run_evidence))

    def test_correction_after_final_confirm_before_factory_blocks_finalization(self):
        flow, candidate = reviewed_flow()
        flow.confirm_final_report(candidate, actor_id=ACTOR)
        flow.reject("operation_plan", "REVIEW_FIELD", actor_id=ACTOR, reason="建议有误")
        with self.assertRaisesRegex(ConfirmationError, "PREPARED_SNAPSHOT_STALE"):
            finalize_analysis(flow, candidate, evidence_records=source_records(flow.run_evidence))


class FinalSealTests(unittest.TestCase):
    def test_final_objects_sealed_and_seven_fields_exact(self):
        flow, candidate, result = final_result()
        validate_final_snapshot(result.snapshot)
        validate_final_analysis_evidence(result.evidence)
        validate_finalization_result(result, flow.draft, flow.run_evidence)
        self.assertEqual(set(result.snapshot.seven_fields), set(ANALYSIS_FIELDS))
        self.assertEqual(result.snapshot.final_state, "FINAL")
        self.assertIs(result.snapshot.human_confirm, HumanConfirm.CONFIRMED)
        self.assertIs(result.evidence.human_confirm, HumanConfirm.CONFIRMED)
        self.assertFalse(hasattr(result.evidence, "source"))

    def test_replaced_final_evidence_even_no_change_fails(self):
        flow, candidate, result = final_result()
        with self.assertRaises(ConfirmationError): validate_final_analysis_evidence(replace(result.evidence))

    def test_replaced_final_snapshot_even_no_change_fails(self):
        flow, candidate, result = final_result()
        with self.assertRaises(ConfirmationError): validate_final_snapshot(replace(result.snapshot))

    def test_every_final_evidence_field_forced_mutation_rejected(self):
        for item in fields(final_result()[2].evidence):
            flow, candidate, result = final_result()
            evidence = result.evidence
            old = getattr(evidence, item.name)
            value = ("other",) if isinstance(old, tuple) else None if item.name == "_gate_digest" else "tampered"
            object.__setattr__(evidence, item.name, value)
            with self.subTest(field=item.name), self.assertRaises(ConfirmationError): validate_final_analysis_evidence(evidence)

    def test_every_final_snapshot_field_forced_mutation_rejected(self):
        for item in fields(final_result()[2].snapshot):
            flow, candidate, result = final_result()
            snapshot = result.snapshot
            object.__setattr__(snapshot, item.name, None if item.name == "_gate_digest" else "tampered")
            with self.subTest(field=item.name), self.assertRaises(ConfirmationError): validate_final_snapshot(snapshot)

    def test_final_contracts_and_conclusion_deeply_immutable(self):
        flow, candidate, result = final_result()
        with self.assertRaises(FrozenInstanceError): result.evidence.confirmed_by = "other"
        with self.assertRaises(TypeError): result.conclusion.structured_fields["risk_level"]["DECISION"] = "REJECTED"
        with self.assertRaises(TypeError): result.conclusion.calculation["input_hash"] = "other"
        with self.assertRaises(TypeError): result.snapshot.confirmation_events[0]["actor_id"] = "other"

    def test_all_calculation_hash_bindings_and_recomputable_log_snapshot(self):
        flow, candidate, result = final_result()
        calculation = result.conclusion.calculation
        run = flow.run_evidence
        for name in ("analysis_run_id", "input_hash", "model_output_hash", "validated_output_hash"):
            self.assertEqual(calculation[name], getattr(run, name))
        self.assertEqual(calculation["validated_output_hash"], digest(flow.draft.output))
        self.assertEqual(calculation["final_snapshot_hash"], final_snapshot_content_hash(result.snapshot))
        self.assertEqual(calculation["confirmation_log_hash"], digest(calculation["confirmation_events"]))
        self.assertEqual(calculation["final_evidence_id"], result.evidence.final_evidence_id)
        self.assertEqual(result.snapshot.confirmed_by, ACTOR)
        self.assertEqual(result.snapshot.confirmed_at, STAMP)
        self.assertEqual(result.snapshot.confirmation_events[-1]["reviewed_snapshot_hash"], candidate.prepared_snapshot_hash)

    def test_altered_conclusion_binding_fails(self):
        for name, value in (("evidence_ids", ("EV-QUOTE",)), ("model_version", "OTHER"),
                            ("analysis_report_id", "OTHER"), ("calculation", {}), ("structured_fields", {})):
            flow, candidate, result = final_result()
            altered = replace(result, conclusion=replace(result.conclusion, **{name: value}))
            with self.subTest(field=name), self.assertRaises(ConfirmationError):
                validate_finalization_result(altered, flow.draft, flow.run_evidence)

    def test_final_evidence_to_dict_detached_and_seal_hidden(self):
        result = final_result()[2]
        copied = result.evidence.to_dict()
        self.assertNotIn("_gate_digest", copied)
        copied["input_evidence_ids"].clear()
        self.assertTrue(result.evidence.input_evidence_ids)


class CorrectionRevisionTests(unittest.TestCase):
    def test_report_correction_keeps_old_objects_and_append_log(self):
        flow, candidate, result = final_result()
        before = result.snapshot.to_dict(), result.evidence.to_dict(), jsonable(result.conclusion.calculation)
        old_events = flow.events
        event = flow.reject_final_report(candidate, actor_id=ACTOR, reason="事后发现分析有误")
        self.assertIs(event.previous_state, HumanConfirm.CONFIRMED)
        self.assertEqual(flow.events[:-1], old_events)
        self.assertEqual(before, (result.snapshot.to_dict(), result.evidence.to_dict(), jsonable(result.conclusion.calculation)))
        validate_finalization_result(result, flow.draft, flow.run_evidence)

    def test_report_correction_reason_required(self):
        flow, candidate, result = final_result()
        with self.assertRaises(ConfirmationError): flow.reject_final_report(candidate, actor_id=ACTOR, reason="")

    def test_new_revision_has_new_ids_and_supersedes_both_final_artifacts(self):
        flow, candidate, old = final_result()
        flow.reject_final_report(candidate, actor_id=ACTOR, reason="需要新修订")
        revision = flow.new_revision()
        self.assertNotEqual(revision.analysis_report_id, flow.analysis_report_id)
        self.assertNotEqual(revision.revision_id, flow.revision_id)
        self.assertEqual(revision.events, ())
        self.assertTrue(all(revision.current(*item.key) is HumanConfirm.PENDING for item in revision.items))
        confirm_remaining(revision)
        new_candidate = revision.prepare_final_snapshot()
        _, _, new = final_result(revision, new_candidate)
        self.assertEqual(new.snapshot.supersedes_analysis_report_id, old.snapshot.analysis_report_id)
        self.assertEqual(new.evidence.supersedes_final_evidence_id, old.evidence.final_evidence_id)
        self.assertNotEqual(new.evidence.final_evidence_id, old.evidence.final_evidence_id)
        self.assertIs(revision.draft, flow.draft)
        self.assertIs(revision.run_evidence, flow.run_evidence)

    def test_same_report_id_for_new_revision_rejected(self):
        flow = make_flow()
        flow.reject("risk_level", "REVIEW_FIELD", actor_id=ACTOR, reason="驳回")
        with self.assertRaises(ConfirmationError): flow.new_revision(analysis_report_id=flow.analysis_report_id)

    def test_rejected_item_can_only_be_reconfirmed_in_new_revision(self):
        flow = make_flow()
        flow.reject("risk_level", "REVIEW_FIELD", actor_id=ACTOR, reason="驳回")
        with self.assertRaises(ConfirmationError): flow.confirm("risk_level", "REVIEW_FIELD", actor_id=ACTOR)
        revision = flow.new_revision()
        revision.confirm("risk_level", "REVIEW_FIELD", actor_id=ACTOR)
        self.assertIs(flow.current("risk_level", "REVIEW_FIELD"), HumanConfirm.REJECTED)
        self.assertIs(revision.current("risk_level", "REVIEW_FIELD"), HumanConfirm.CONFIRMED)

    def test_new_revision_without_any_rejection_rejected(self):
        flow = make_flow()
        with self.assertRaises(ConfirmationError): flow.new_revision()

    def test_prior_modify_is_not_post_finalization_correction(self):
        flow = make_flow()
        flow.modify("risk_level", "REVIEW_FIELD", changed_field(flow), actor_id=ACTOR, reason="风险调高")
        confirm_remaining(flow)
        candidate = flow.prepare_final_snapshot()
        flow.confirm_final_report(candidate, actor_id=ACTOR)
        with self.assertRaisesRegex(ConfirmationError, "FINAL_REPORT_REVISION_REQUIRES_CORRECTION"):
            flow.new_revision()

    def test_item_correction_after_finalization_allows_fresh_revision(self):
        flow, candidate, old = final_result()
        flow.reject("risk_level", "REVIEW_FIELD", actor_id=ACTOR, reason="事后发现风险判断错误")
        revision = flow.new_revision()
        self.assertEqual(revision._supersedes_analysis_report_id, old.snapshot.analysis_report_id)

    def test_new_final_evidence_cannot_reuse_predecessor_id(self):
        flow, candidate, old = final_result()
        flow.reject_final_report(candidate, actor_id=ACTOR, reason="修订")
        revision = flow.new_revision()
        confirm_remaining(revision)
        candidate = revision.prepare_final_snapshot()
        revision.confirm_final_report(candidate, actor_id=ACTOR)
        with self.assertRaises(ConfirmationError):
            finalize_analysis(revision, candidate, evidence_records=source_records(revision.run_evidence),
                              final_evidence_id=old.evidence.final_evidence_id)


if __name__ == "__main__":
    unittest.main()
