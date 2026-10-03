"""Offline state machine, override boundary, and append-only revision tests."""

from copy import deepcopy
from dataclasses import FrozenInstanceError, replace
import json
import unittest
from unittest.mock import patch

from analysis import (
    AnalysisPackageBuilder, AnalysisRunEvidence, ConfirmationError, ConfirmationFlow,
    PromptBuilder, SevenFieldValidator,
)
from analysis.contracts import jsonable
from analysis.output_contracts import digest
from evidence import EvidenceRecord, EvidenceSource, EvidenceValidationResult, HumanConfirm, ValidationStatus
from test_analysis_output import deterministic_response
from test_analysis_package import canonical_daily, canonical_quote, package_arguments, REQUEST_TIME


ACTOR = "reviewer-010"
STAMP = "2026-10-03T12:00:00+08:00"


def make_inputs(*, quote=False, holding=False, arguments=None):
    arguments = package_arguments(quote=quote, holding=holding) if arguments is None else arguments
    envelope = PromptBuilder().build(AnalysisPackageBuilder().build(**arguments))
    response = json.dumps(deterministic_response(envelope), ensure_ascii=False)
    draft = SevenFieldValidator().validate(response, envelope)
    run = AnalysisRunEvidence.from_draft(envelope, draft, model_version="OFFLINE-FIXTURE-010")
    return draft, run


def make_flow(**kwargs):
    return ConfirmationFlow(*make_inputs(**kwargs))


def confirm_remaining(flow):
    for item in flow.items:
        if item.target != "FINAL_REPORT" and flow.current(*item.key) is HumanConfirm.PENDING:
            flow.confirm(*item.key, actor_id=ACTOR, created_at=STAMP)


def changed_field(flow, target="risk_level"):
    value = jsonable(flow.draft.output[target])
    value["content"]["assessment"] = "HIGH"
    return value


def source_records(run):
    """Synthetic upstream provenance; no provider, normalizer or raw file I/O."""
    return tuple(EvidenceRecord(
        source=EvidenceSource.TUSHARE_MCP if identifier == "EV-DAILY" else EvidenceSource.TDX_OFFICIAL_LOCAL_HTTP,
        endpoint_or_tool="daily" if identifier == "EV-DAILY" else "quote",
        request_params={"stock_id": run.stock_id}, request_timestamp=REQUEST_TIME,
        provider_timestamp=None, raw_response_path=f"fixtures/run/collection/{identifier}.json",
        normalization_rule=("close:NO_CONVERSION",),
        validation_result=EvidenceValidationResult(ValidationStatus.PASS),
        collection_task_id="collection", system_run_id="run", model_version=None,
        human_confirm=HumanConfirm.PENDING,
        raw_response_hash=digest({"close": 10.5} if identifier == "EV-DAILY" else {"price": 10.5}),
        normalized_data_hash=digest([canonical_daily()] if identifier == "EV-DAILY" else [canonical_quote()]),
        analysis_output_hash=None,
        evidence_id=identifier,
    ) for identifier in run.input_evidence_ids)


class ConfirmationInputTests(unittest.TestCase):
    def setUp(self):
        self.draft, self.run = make_inputs()

    def test_sealed_pair_accepted_and_all_items_pending(self):
        flow = ConfirmationFlow(self.draft, self.run)
        self.assertTrue(all(flow.current(*item.key) is HumanConfirm.PENDING for item in flow.items))

    def test_unsealed_draft_rejected(self):
        with self.assertRaisesRegex(ConfirmationError, "CONFIRMATION_INPUT_INVALID"):
            ConfirmationFlow(replace(self.draft), self.run)

    def test_unsealed_run_rejected(self):
        with self.assertRaisesRegex(ConfirmationError, "CONFIRMATION_INPUT_INVALID"):
            ConfirmationFlow(self.draft, replace(self.run))

    def test_separate_upstream_gates_both_called_on_failure(self):
        with patch("analysis.confirmation_contracts.validate_draft", side_effect=ValueError), patch(
            "analysis.confirmation_contracts.validate_analysis_run_evidence") as gate:
            with self.assertRaises(ConfirmationError): ConfirmationFlow(self.draft, self.run)
        gate.assert_called_once_with(self.run)

    def test_individually_sealed_but_mismatched_run_binding_rejected(self):
        other_draft, other_run = make_inputs(quote=True)
        with self.assertRaisesRegex(ConfirmationError, "CONFIRMATION_INPUT_INVALID"):
            ConfirmationFlow(self.draft, other_run)
        with self.assertRaises(ConfirmationError): ConfirmationFlow(other_draft, self.run)

    def test_all_pair_binding_fields_checked_after_independent_gates(self):
        # Gate stubs isolate pair matching, rather than merely testing seal failure.
        for name in ("input_hash", "validated_output_hash", "model_output_hash", "template_hash",
                     "output_schema_version", "input_evidence_ids", "used_evidence_ids"):
            value = ("OTHER",) if name.endswith("ids") else "0" * 64
            run = replace(self.run, **{name: value}) if name not in ("input_evidence_ids", "used_evidence_ids") else None
            if run is None:
                run = replace(self.run)
                object.__setattr__(run, name, value)
            with self.subTest(name=name), patch("analysis.confirmation_contracts.validate_draft"), patch(
                "analysis.confirmation_contracts.validate_analysis_run_evidence"):
                with self.assertRaisesRegex(ConfirmationError, "CONFIRMATION_INPUT_INVALID"):
                    ConfirmationFlow(self.draft, run)

    def test_upstream_forced_tampering_rejected(self):
        object.__setattr__(self.draft, "model_output_hash", "0" * 64)
        with self.assertRaises(ConfirmationError): ConfirmationFlow(self.draft, self.run)

    def test_seven_field_and_frozen_p3_reasons_in_plan(self):
        flow = ConfirmationFlow(self.draft, self.run)
        keys = {item.key for item in flow.items}
        self.assertTrue({(name, "REVIEW_FIELD") for name in self.draft.output} <= keys)
        self.assertIn(("operation_plan", "REVIEW_ADVICE"), keys)
        self.assertIn(("t_strategy", "REVIEW_ADVICE"), keys)
        self.assertIn(("reasoning", "REVIEW_ASSUMPTION"), keys)
        self.assertIn(("FINAL_REPORT", "REVIEW_FINAL_REPORT"), keys)
        self.assertEqual(len(keys), len(flow.items))

    def test_unknown_input_types_fail_with_stable_code(self):
        for draft, run in (({}, self.run), (self.draft, {}), (None, None)):
            with self.subTest(draft=type(draft)), self.assertRaises(ConfirmationError):
                ConfirmationFlow(draft, run)


class HumanStateTests(unittest.TestCase):
    def setUp(self):
        self.flow = make_flow()
        self.key = ("operation_plan", "REVIEW_ADVICE")

    def test_pending_to_confirmed(self):
        event = self.flow.confirm(*self.key, actor_id=ACTOR, created_at=STAMP)
        self.assertEqual((event.previous_state, event.current_state),
                         (HumanConfirm.PENDING, HumanConfirm.CONFIRMED))
        self.assertEqual(event.actor_id, ACTOR)
        self.assertEqual(event.created_at, STAMP)
        self.assertEqual(event.analysis_run_id, self.flow.run_evidence.analysis_run_id)

    def test_pending_to_rejected(self):
        self.flow.reject(*self.key, actor_id=ACTOR, reason="暂不采纳")
        self.assertIs(self.flow.current(*self.key), HumanConfirm.REJECTED)

    def test_confirmed_to_rejected_with_reason_appends(self):
        first = self.flow.confirm(*self.key, actor_id=ACTOR)
        second = self.flow.reject(*self.key, actor_id=ACTOR, reason="发现建议错误")
        self.assertEqual(len(self.flow.events), 2)
        self.assertIs(self.flow.events[0], first)
        self.assertIs(second.previous_state, HumanConfirm.CONFIRMED)

    def test_confirmed_to_rejected_without_reason_rejected_atomically(self):
        self.flow.confirm(*self.key, actor_id=ACTOR)
        for reason in ("", " ", None):
            with self.subTest(reason=reason), self.assertRaises(ConfirmationError):
                self.flow.reject(*self.key, actor_id=ACTOR, reason=reason)
        self.assertEqual(len(self.flow.events), 1)

    def test_rejected_to_confirmed_forbidden(self):
        self.flow.reject(*self.key, actor_id=ACTOR, reason="驳回")
        with self.assertRaises(ConfirmationError): self.flow.confirm(*self.key, actor_id=ACTOR)
        self.assertEqual(len(self.flow.events), 1)

    def test_confirmed_and_rejected_to_pending_forbidden(self):
        for state in (HumanConfirm.CONFIRMED, HumanConfirm.REJECTED):
            flow = make_flow()
            flow.transition(*self.key, state, actor_id=ACTOR, reason="人工审查")
            with self.assertRaises(ConfirmationError):
                flow.transition(*self.key, HumanConfirm.PENDING, actor_id=ACTOR)

    def test_duplicate_confirmation_rejected(self):
        self.flow.confirm(*self.key, actor_id=ACTOR)
        with self.assertRaises(ConfirmationError): self.flow.confirm(*self.key, actor_id=ACTOR)
        self.assertEqual(len(self.flow.events), 1)

    def test_duplicate_rejection_rejected(self):
        self.flow.reject(*self.key, actor_id=ACTOR, reason="驳回")
        with self.assertRaises(ConfirmationError): self.flow.reject(*self.key, actor_id=ACTOR, reason="驳回")

    def test_actor_required_and_never_inferred(self):
        with self.assertRaises(TypeError): self.flow.confirm(*self.key)
        for actor in ("", " ", None, 3):
            with self.subTest(actor=actor), self.assertRaises(ConfirmationError):
                self.flow.confirm(*self.key, actor_id=actor)
        self.assertEqual(self.flow.events, ())

    def test_invalid_and_naive_event_time_rejected(self):
        for stamp in ("2026-10-03T12:00:00", "bad", "2026-13-33T12:00:00Z", 3):
            with self.subTest(stamp=stamp), self.assertRaises(ConfirmationError):
                self.flow.confirm(*self.key, actor_id=ACTOR, created_at=stamp)
        self.assertEqual(self.flow.events, ())

    def test_default_event_time_aware(self):
        from datetime import datetime
        event = self.flow.confirm(*self.key, actor_id=ACTOR)
        self.assertIsNotNone(datetime.fromisoformat(event.created_at).utcoffset())

    def test_event_immutable_and_tuple_detached(self):
        old = self.flow.events
        event = self.flow.confirm(*self.key, actor_id=ACTOR)
        self.assertEqual(old, ())
        with self.assertRaises(FrozenInstanceError): event.actor_id = "someone"
        copied = event.to_dict()
        copied["actor_id"] = "someone"
        self.assertEqual(event.actor_id, ACTOR)

    def test_update_and_delete_forbidden(self):
        for method, code in ((self.flow.update_confirmation_event, "UPDATE"),
                             (self.flow.delete_confirmation_event, "DELETE")):
            with self.assertRaisesRegex(ConfirmationError, code + "_CONFIRMATION_EVENT=FORBIDDEN"):
                method("event")

    def test_forced_event_mutation_invalidates_flow(self):
        event = self.flow.confirm(*self.key, actor_id=ACTOR)
        object.__setattr__(event, "actor_id", "other")
        with self.assertRaisesRegex(ConfirmationError, "CONFIRMATION_LOG_INVALID"):
            self.flow.confirm("t_strategy", "REVIEW_ADVICE", actor_id=ACTOR)

    def test_unknown_target_or_reason_rejected(self):
        for key in (("cost_price", "REVIEW_FIELD"), ("operation_plan", "unknown")):
            with self.subTest(key=key), self.assertRaises(ConfirmationError):
                self.flow.confirm(*key, actor_id=ACTOR)

    def test_state_does_not_add_modified(self):
        self.assertEqual({state.value for state in HumanConfirm}, {"PENDING", "CONFIRMED", "REJECTED"})
        with self.assertRaises(ConfirmationError):
            self.flow.transition(*self.key, "MODIFIED", actor_id=ACTOR)

    def test_final_report_cannot_bypass_candidate(self):
        with self.assertRaisesRegex(ConfirmationError, "FINAL_REPORT_REQUIRES_SNAPSHOT_REVIEW"):
            self.flow.confirm("FINAL_REPORT", "REVIEW_FINAL_REPORT", actor_id=ACTOR)

    def test_target_reason_keys_independent(self):
        self.flow.confirm(*self.key, actor_id=ACTOR)
        self.assertIs(self.flow.current("operation_plan", "REVIEW_FIELD"), HumanConfirm.PENDING)


class HumanOverrideTests(unittest.TestCase):
    def setUp(self):
        self.flow = make_flow(holding=True)
        self.key = ("risk_level", "REVIEW_FIELD")

    def modify(self, value=None, **kwargs):
        params = dict(actor_id=ACTOR, reason="人工更保守的风险判断", referenced_evidence_ids=("EV-DAILY",), created_at=STAMP)
        params.update(kwargs)
        return self.flow.modify(*self.key, changed_field(self.flow) if value is None else value, **params)

    def test_modify_keeps_draft_run_and_both_hashes_exact(self):
        before = self.flow.draft.to_dict(), self.flow.run_evidence.to_dict()
        self.modify()
        self.assertEqual(before, (self.flow.draft.to_dict(), self.flow.run_evidence.to_dict()))

    def test_modify_rejects_original_and_confirms_override_as_two_events(self):
        override = self.modify()
        self.assertIs(self.flow.current(*self.key), HumanConfirm.REJECTED)
        self.assertEqual([event.human_decision for event in self.flow.events], ["MODIFY", "CONFIRM"])
        self.assertEqual(self.flow.events[0].override_id, override.override_id)
        self.assertIs(self.flow.current("HUMAN_OVERRIDE:" + override.override_id, "REVIEW_OVERRIDE"), HumanConfirm.CONFIRMED)

    def test_override_provenance_fields_and_original_hash(self):
        override = self.modify()
        self.assertEqual(override.origin, "HUMAN")
        self.assertEqual(override.actor_id, ACTOR)
        self.assertEqual(override.original_value_hash, digest(self.flow.draft.output["risk_level"]))
        self.assertEqual(override.created_at, STAMP)
        self.assertEqual(override.referenced_evidence_ids, ("EV-DAILY",))
        self.assertEqual(override.analysis_run_id, self.flow.run_evidence.analysis_run_id)

    def test_override_deeply_immutable_and_detached(self):
        value = changed_field(self.flow)
        override = self.modify(value)
        value["content"]["assessment"] = "LOW"
        with self.assertRaises(TypeError): override.replacement["content"]["assessment"] = "LOW"
        self.assertEqual(override.replacement["content"]["assessment"], "HIGH")

    def test_override_nonexistent_evidence_rejected_atomically(self):
        for refs in (("NONEXISTENT",), ("EV-DAILY", "EV-DAILY"), (3,)):
            with self.subTest(refs=refs), self.assertRaises(ConfirmationError):
                self.modify(referenced_evidence_ids=refs)
        self.assertEqual((self.flow.events, self.flow.human_overrides), ((), ()))

    def test_override_can_reference_unused_input_without_changing_used_set(self):
        self.flow = make_flow(quote=True)
        override = self.modify(referenced_evidence_ids=("EV-QUOTE",))
        self.assertIn("EV-QUOTE", override.referenced_evidence_ids)
        self.assertNotIn("EV-QUOTE", self.flow.run_evidence.used_evidence_ids)

    def test_empty_reason_or_actor_rejected_atomically(self):
        for kwargs in ({"reason": ""}, {"reason": " "}, {"actor_id": ""}):
            with self.subTest(kwargs=kwargs), self.assertRaises(ConfirmationError): self.modify(**kwargs)
        self.assertEqual(self.flow.events, ())

    def test_fact_value_modification_forbidden(self):
        value = changed_field(self.flow)
        value["content"]["facts"][0]["value"] = 100
        with self.assertRaises(ConfirmationError): self.modify(value)

    def test_fact_number_type_modification_forbidden(self):
        value = changed_field(self.flow)
        value["content"]["facts"][0]["value"] = 10
        original = jsonable(self.flow.draft.output["risk_level"])
        # A separate equality case checks int versus float preserving canonical bytes.
        from analysis.confirmation_flow import _validate_replacement
        original["content"]["facts"][0]["value"] = 10.0
        with self.assertRaises(ConfirmationError): _validate_replacement(original, value, "risk_level")

    def test_fact_refs_quality_limitations_and_times_modification_forbidden(self):
        variants = []
        value = changed_field(self.flow); value["data_quality_status"] = "PASS"; variants.append(value)
        value = changed_field(self.flow); value["evidence_ids"] = []; variants.append(value)
        value = changed_field(self.flow); value["time_range"]["start"] = "2026-01-01"; variants.append(value)
        value = changed_field(self.flow); value["content"]["limitations"] = []; variants.append(value)
        value = changed_field(self.flow); value["content"]["facts"] = []; variants.append(value)
        for value in variants:
            with self.subTest(value=value), self.assertRaises(ConfirmationError): self.modify(value)

    def test_holding_quantity_and_cost_cannot_be_modified(self):
        value = jsonable(self.flow.draft.output["position_status"])
        for index, fact in enumerate(value["content"]["facts"]):
            replacement = deepcopy(value)
            replacement["content"]["facts"][index]["value"] = 999
            with self.subTest(path=fact["input_path"]), self.assertRaises(ConfirmationError):
                self.flow.modify("position_status", "REVIEW_FIELD", replacement, actor_id=ACTOR, reason="事实有误")

    def test_financial_value_and_announcement_title_cannot_be_modified(self):
        from analysis.confirmation_flow import _validate_replacement
        for path, old, new in (("financial_data.records.0.data.net_profit", 100.0, 200.0),
                               ("intelligence_data.recent_announcements.records.0.data.title", "原始公告", "新公告")):
            original = jsonable(self.flow.draft.output["risk_level"])
            original["content"]["facts"][0].update(input_path=path, value=old)
            value = deepcopy(original)
            value["content"]["assessment"] = "HIGH"
            value["content"]["facts"][0]["value"] = new
            with self.subTest(path=path), self.assertRaises(ConfirmationError):
                _validate_replacement(original, value, "risk_level")

    def test_canonical_target_and_data_quality_item_cannot_be_modified(self):
        for target, reason in (("canonical_market_data.daily_kline", "REVIEW_DATA_QUALITY"),
                               ("data_quality_status", "REVIEW_FIELD")):
            with self.assertRaises(ConfirmationError):
                self.flow.modify(target, reason, {}, actor_id=ACTOR, reason="事实有误")

    def test_unstructured_prose_and_unknown_property_rejected(self):
        for replacement in ("收盘价改成 999", {"price": 999}, {**changed_field(self.flow), "extra": True}):
            with self.subTest(replacement=replacement), self.assertRaises(ConfirmationError): self.modify(replacement)

    def test_free_text_cannot_smuggle_fact_into_statement(self):
        value = changed_field(self.flow)
        value["content"]["statement"] = "公告标题改为新公告，价格 999"
        with self.assertRaises(ConfirmationError): self.modify(value)

    def test_advice_upgrade_from_insufficient_evidence_forbidden(self):
        value = jsonable(self.flow.draft.output["operation_plan"])
        value["content"]["advice"] = {"level": "LEVEL_1", "action": "BUY"}
        with self.assertRaises(ConfirmationError):
            self.flow.modify("operation_plan", "REVIEW_ADVICE", value, actor_id=ACTOR, reason="新增建议")

    def test_reasoning_fact_refs_modification_forbidden(self):
        value = jsonable(self.flow.draft.output["reasoning"])
        value["content"]["steps"][0]["fact_refs"] = []
        with self.assertRaises(ConfirmationError):
            self.flow.modify("reasoning", "REVIEW_ASSUMPTION", value, actor_id=ACTOR, reason="修改引用")

    def test_modify_already_rejected_original_forbidden(self):
        self.flow.reject(*self.key, actor_id=ACTOR, reason="驳回")
        with self.assertRaises(ConfirmationError): self.modify()

    def test_modify_confirmed_original_appends_correction(self):
        self.flow.confirm(*self.key, actor_id=ACTOR)
        self.modify()
        self.assertEqual(len(self.flow.events), 3)
        self.assertIs(self.flow.events[1].previous_state, HumanConfirm.CONFIRMED)

    def test_same_field_cannot_have_conflicting_overrides(self):
        self.flow = make_flow()
        value = jsonable(self.flow.draft.output["reasoning"])
        value["content"]["steps"][0]["statement"] = "依据所引用事实与限制，结论须人工核查"
        self.flow.modify("reasoning", "REVIEW_FIELD", value, actor_id=ACTOR, reason="人工修改")
        with self.assertRaisesRegex(ConfirmationError, "HUMAN_OVERRIDE_ALREADY_EXISTS"):
            self.flow.modify("reasoning", "REVIEW_ASSUMPTION", value, actor_id=ACTOR, reason="另一个修改")

    def test_override_can_be_rejected_without_rewriting_original_or_override(self):
        override = self.modify()
        before = override.to_dict()
        self.flow.reject("HUMAN_OVERRIDE:" + override.override_id, "REVIEW_OVERRIDE", actor_id=ACTOR, reason="撤销人工判断")
        self.assertEqual(before, override.to_dict())

    def test_forced_override_tampering_detected(self):
        override = self.modify()
        object.__setattr__(override, "origin", "AI")
        with self.assertRaises(ConfirmationError): self.flow.validate()

    def test_unchanged_override_rejected(self):
        with self.assertRaisesRegex(ConfirmationError, "HUMAN_OVERRIDE_UNCHANGED"):
            self.modify(jsonable(self.flow.draft.output["risk_level"]))


if __name__ == "__main__":
    unittest.main()
