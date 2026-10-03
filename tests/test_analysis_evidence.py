"""Draft run provenance stays immutable, pending, and separate from Evidence."""

from dataclasses import FrozenInstanceError, replace
import hashlib
import json
import unittest
from unittest.mock import patch

from analysis import (AnalysisOutputError, AnalysisPackageBuilder, AnalysisRunEvidence,
                      PromptBuilder, SevenFieldValidator, ValidatedAnalysisDraft)
from analysis.output_contracts import digest
from analysis.analysis_evidence import validate_analysis_run_evidence
from evidence import EvidenceRecord
from test_analysis_package import package_arguments
from test_analysis_output import deterministic_response


class AnalysisRunEvidenceTests(unittest.TestCase):
    def setUp(self):
        self.envelope = PromptBuilder().build(AnalysisPackageBuilder().build(**package_arguments()))
        self.response = json.dumps(deterministic_response(self.envelope), ensure_ascii=False, indent=2)
        self.draft = SevenFieldValidator().validate(self.response, self.envelope)

    def build(self, **kwargs):
        return AnalysisRunEvidence.from_draft(self.envelope, self.draft, model_version="TEST-MODEL-V1", **kwargs)

    def test_all_required_provenance_bound(self):
        evidence = self.build(analysis_run_id="RUN-009-TEST", created_at="2026-10-03T12:00:00Z")
        self.assertEqual(evidence.analysis_run_id, "RUN-009-TEST")
        self.assertEqual(evidence.stock_id, self.envelope.stock_id)
        self.assertEqual(evidence.prompt_version, self.envelope.prompt_version)
        self.assertEqual(evidence.template_hash, self.envelope.template_hash)
        self.assertEqual(evidence.input_hash, self.envelope.input_hash)
        self.assertEqual(evidence.output_schema_version, self.draft.output_schema_version)
        self.assertEqual(evidence.model_output_hash, hashlib.sha256(self.response.encode()).hexdigest())
        self.assertEqual(evidence.validated_output_hash, digest(self.draft.output))
        self.assertEqual(evidence.input_evidence_ids, self.envelope.evidence_ids)

    def test_factory_seals_all_serialized_provenance(self):
        evidence = self.build()
        validate_analysis_run_evidence(evidence)
        self.assertEqual(evidence._gate_digest, digest(evidence.to_dict()))
        self.assertNotIn("_gate_digest", evidence.to_dict())
        self.assertNotIn("_gate_digest", repr(evidence))

    def test_replace_even_without_changes_loses_seal(self):
        evidence = self.build()
        for copied in (replace(evidence), replace(evidence, model_version="OTHER-MODEL")):
            self.assertIsNone(copied._gate_digest)
            with self.assertRaises(AnalysisOutputError): validate_analysis_run_evidence(copied)
        validate_analysis_run_evidence(evidence)

    def test_hand_constructed_run_evidence_has_no_seal(self):
        value = self.build().to_dict()
        for key in ("analysis_status", "human_confirm", "final_conclusion"): value.pop(key)
        evidence = AnalysisRunEvidence(**value)
        self.assertIsNone(evidence._gate_digest)
        with self.assertRaises(AnalysisOutputError): validate_analysis_run_evidence(evidence)

    def test_seal_cannot_be_supplied_at_construction(self):
        value = self.build().to_dict()
        for key in ("analysis_status", "human_confirm", "final_conclusion"): value.pop(key)
        with self.assertRaises(TypeError): AnalysisRunEvidence(**value, _gate_digest="0" * 64)

    def test_every_provenance_and_state_mutation_fails_validation(self):
        changes = {
            "analysis_run_id": "OTHER-RUN", "stock_id": "OTHER.SH",
            "prompt_version": "OTHER-PROMPT", "template_hash": "0" * 64,
            "input_hash": "0" * 64, "output_schema_version": "OTHER-SCHEMA",
            "model_version": "OTHER-MODEL", "model_output_hash": "0" * 64,
            "validated_output_hash": "0" * 64,
            "input_evidence_ids": ("EV-DAILY", "EV-OTHER"), "used_evidence_ids": (),
            "created_at": "2026-10-04T12:00:00Z", "analysis_status": "CONFIRMED",
            "human_confirm": "CONFIRMED", "final_conclusion": True,
        }
        self.assertEqual(set(changes), set(self.build().to_dict()))
        for name, value in changes.items():
            evidence = self.build()
            object.__setattr__(evidence, name, value)
            with self.subTest(name=name), self.assertRaises(AnalysisOutputError):
                validate_analysis_run_evidence(evidence)

    def test_replaced_provenance_fields_fail_validation(self):
        for changes in ({"used_evidence_ids": ()}, {"input_hash": "0" * 64},
                        {"analysis_run_id": "OTHER-RUN"}, {"created_at": "2026-10-04T12:00:00Z"}):
            with self.subTest(field=next(iter(changes))), self.assertRaises(AnalysisOutputError):
                validate_analysis_run_evidence(replace(self.build(), **changes))

    def test_wrong_type_missing_or_changed_seal_fails_validation(self):
        for value in (None, {}, self.build().to_dict(), self.draft):
            with self.subTest(kind=type(value).__name__), self.assertRaises(AnalysisOutputError):
                validate_analysis_run_evidence(value)
        for seal in (None, "0" * 64):
            evidence = self.build()
            object.__setattr__(evidence, "_gate_digest", seal)
            with self.subTest(seal=seal), self.assertRaises(AnalysisOutputError):
                validate_analysis_run_evidence(evidence)

    def test_used_evidence_is_exact_seven_field_union(self):
        evidence = self.build()
        used = {identifier for field in self.draft.output.values() for identifier in field["evidence_ids"]}
        self.assertEqual(evidence.used_evidence_ids, tuple(sorted(used)))

    def test_input_evidence_can_exceed_used_evidence(self):
        env = PromptBuilder().build(AnalysisPackageBuilder().build(**package_arguments(quote=True)))
        draft = SevenFieldValidator().validate(json.dumps(deterministic_response(env)), env)
        evidence = AnalysisRunEvidence.from_draft(env, draft, model_version="TEST-MODEL")
        self.assertIn("EV-QUOTE", evidence.input_evidence_ids)
        self.assertNotIn("EV-QUOTE", evidence.used_evidence_ids)

    def test_no_final_evidence_or_confirmation_transition(self):
        with patch("evidence.EvidenceBuilder.build", side_effect=AssertionError("final evidence path")) as build:
            evidence = self.build()
        self.assertNotIsInstance(evidence, EvidenceRecord)
        self.assertEqual((evidence.analysis_status, evidence.human_confirm, evidence.final_conclusion), ("UNCONFIRMED", "PENDING", False))
        build.assert_not_called()

    def test_status_cannot_be_supplied_or_changed(self):
        evidence = self.build()
        with self.assertRaises(ValueError): replace(evidence, human_confirm="CONFIRMED")
        with self.assertRaises(FrozenInstanceError): evidence.human_confirm = "CONFIRMED"

    def test_json_snapshot_detached_from_record(self):
        evidence = self.build()
        copied = evidence.to_dict()
        copied["input_evidence_ids"].clear()
        self.assertEqual(evidence.input_evidence_ids, ("EV-DAILY",))
        self.assertEqual(json.loads(json.dumps(evidence.to_dict()))["analysis_status"], "UNCONFIRMED")

    def test_replaced_unsealed_draft_rejected(self):
        self.draft = replace(self.draft)
        with self.assertRaises(AnalysisOutputError): self.build()

    def test_hand_constructed_draft_rejected(self):
        value = self.draft.to_dict()
        for key in ("analysis_status", "human_confirm", "final_state"): value.pop(key)
        self.draft = ValidatedAnalysisDraft(**value)
        with self.assertRaises(AnalysisOutputError): self.build()

    def test_forced_draft_output_or_hash_mutation_rejected(self):
        for name, value in (("validated_output_hash", "0" * 64), ("model_output_hash", "0" * 64), ("analysis_status", "CONFIRMED")):
            draft = SevenFieldValidator().validate(self.response, self.envelope)
            object.__setattr__(draft, name, value)
            with self.subTest(name=name), self.assertRaises(AnalysisOutputError):
                AnalysisRunEvidence.from_draft(self.envelope, draft, model_version="TEST-MODEL")

    def test_different_prompt_draft_pair_rejected(self):
        other = PromptBuilder().build(AnalysisPackageBuilder().build(**package_arguments(quote=True)))
        with self.assertRaises(AnalysisOutputError): AnalysisRunEvidence.from_draft(other, self.draft, model_version="TEST-MODEL")

    def test_created_at_must_include_valid_timezone(self):
        for value in ("not-a-time", "2026-10-03T12:00:00", "2026-99-99T00:00:00Z"):
            with self.subTest(value=value), self.assertRaises(AnalysisOutputError): self.build(created_at=value)

    def test_explicit_model_identity_required(self):
        with self.assertRaises(TypeError): AnalysisRunEvidence.from_draft(self.envelope, self.draft)
        with self.assertRaises(AnalysisOutputError): AnalysisRunEvidence.from_draft(self.envelope, self.draft, model_version="")

    def test_empty_run_id_does_not_silently_generate_replacement(self):
        with self.assertRaises(AnalysisOutputError): self.build(analysis_run_id="")

    def test_default_run_id_is_unique_and_created_at_is_aware(self):
        from datetime import datetime
        first, second = self.build(), self.build()
        self.assertNotEqual(first.analysis_run_id, second.analysis_run_id)
        self.assertIsNotNone(datetime.fromisoformat(first.created_at).tzinfo)


if __name__ == "__main__":
    unittest.main()
