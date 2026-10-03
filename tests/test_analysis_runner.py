"""One-shot injected execution and observed zero external side effects."""

import ast
from contextlib import ExitStack
from copy import deepcopy
from dataclasses import replace
import hashlib
import json
from pathlib import Path
import unittest
from unittest.mock import patch

from analysis import AnalysisPackage, AnalysisPackageBuilder, AnalysisRunner, PromptBuilder
from analysis.contracts import stable_json
from analysis.analysis_evidence import validate_analysis_run_evidence
from analysis.runner import (ANALYSIS_RUNNER_CAPABILITY, LIVE_MODEL_BACKEND_CONFIGURED,
                             LIVE_MODEL_RUNNING, REAL_MODEL_OUTPUT_VERIFIED, RUNTIME_ANALYSIS_MODEL)
from analysis.prompt_builder import TEMPLATE_PATH
from test_analysis_package import information_package_arguments, package_arguments
from test_analysis_output import deterministic_response


class FakeModelExecutor:
    """Only test executor. No production runtime model is selected."""
    def __init__(self, response=None, *, model_version="TEST-ONLY-DETERMINISTIC-V1", mutate=None):
        self.model_version = model_version
        self.response = response
        self.mutate = mutate
        self.calls = []

    def generate(self, prompt_envelope, output_schema):
        self.calls.append((prompt_envelope, deepcopy(output_schema)))
        if isinstance(self.response, Exception):
            raise self.response
        response = deterministic_response(prompt_envelope) if self.response is None else self.response
        if self.mutate:
            self.mutate(response)
        return json.dumps(response, ensure_ascii=False) if isinstance(response, dict) else response


class AnalysisRunnerTests(unittest.TestCase):
    def setUp(self):
        self.package = AnalysisPackageBuilder().build(**package_arguments())
        self.fake = FakeModelExecutor()
        self.runner = AnalysisRunner(self.fake)

    def assert_failed(self, result, status):
        self.assertEqual(result.status, status)
        self.assertEqual(result.error_code, status)
        self.assertIsNone(result.draft)
        self.assertIsNone(result.evidence)

    def test_watch_run_returns_only_unconfirmed_draft_and_pending_evidence(self):
        result = self.runner.run(self.package)
        self.assertEqual(result.status, "UNCONFIRMED")
        self.assertEqual(result.MODEL_CALL_COUNT, 1)
        self.assertEqual(result.draft.analysis_status, "UNCONFIRMED")
        self.assertEqual(result.evidence.human_confirm, "PENDING")
        self.assertFalse(result.evidence.final_conclusion)
        self.assertEqual(result.evidence.model_version, self.fake.model_version)
        validate_analysis_run_evidence(result.evidence)

    def test_medium_confidence_and_reasoning_steps_run(self):
        fake = FakeModelExecutor(mutate=lambda response: response["analysis"]["confidence"]["content"].update(
            assessment="MEDIUM"))
        result = AnalysisRunner(fake).run(self.package)
        self.assertEqual(result.status, "UNCONFIRMED")
        self.assertEqual(result.draft.output["confidence"]["content"]["assessment"], "MEDIUM")
        self.assertEqual(len(result.draft.output["reasoning"]["content"]["steps"]), 2)
        validate_analysis_run_evidence(result.evidence)

    def test_missing_steps_never_builds_run_evidence(self):
        fake = FakeModelExecutor(mutate=lambda response: response["analysis"]["reasoning"]["content"].pop("steps"))
        with patch("analysis.runner.AnalysisRunEvidence.from_draft", side_effect=AssertionError("must not build")) as build:
            self.assert_failed(AnalysisRunner(fake).run(self.package), "OUTPUT_INVALID")
            build.assert_not_called()

    def test_out_of_prompt_step_evidence_never_builds_run_evidence(self):
        fake = FakeModelExecutor(mutate=lambda response: response["analysis"]["reasoning"]["content"]["steps"][0].update(
            evidence_ids=["EV-OUTSIDE-PROMPT"]))
        with patch("analysis.runner.AnalysisRunEvidence.from_draft", side_effect=AssertionError("must not build")) as build:
            self.assert_failed(AnalysisRunner(fake).run(self.package), "OUTPUT_INVALID")
            build.assert_not_called()

    def test_one_generate_call_receives_frozen_envelope_and_schema(self):
        result = self.runner.run(self.package)
        self.assertEqual(len(self.fake.calls), 1)
        envelope, schema = self.fake.calls[0]
        self.assertEqual(envelope, PromptBuilder().build(self.package))
        self.assertEqual(envelope, result.prompt_envelope)
        self.assertEqual(set(schema["properties"]["analysis"]["properties"]), set(result.draft.output))

    def test_holding_run(self):
        package = AnalysisPackageBuilder().build(**package_arguments(holding=True, quote=True))
        result = self.runner.run(package)
        self.assertEqual(result.status, "UNCONFIRMED")
        self.assertEqual(result.draft.output["position_status"]["input_refs"], ("holding_position",))

    def test_info_unverified_and_missing_blocks_run(self):
        package = AnalysisPackageBuilder().build(**information_package_arguments())
        result = self.runner.run(package)
        self.assertEqual(result.status, "UNCONFIRMED")
        self.assertIn("CANONICAL-profit", result.evidence.used_evidence_ids)

    def test_model_exception_reports_model_failed_without_error_payload(self):
        fake = FakeModelExecutor(RuntimeError("secret provider payload"))
        result = AnalysisRunner(fake).run(self.package)
        self.assert_failed(result, "MODEL_FAILED")
        self.assertEqual(len(fake.calls), 1)
        self.assertEqual(result.MODEL_CALL_COUNT, 1)
        self.assertNotIn("secret", repr(result))

    def test_invalid_json_reports_output_invalid(self):
        fake = FakeModelExecutor("{not-json")
        self.assert_failed(AnalysisRunner(fake).run(self.package), "OUTPUT_INVALID")
        self.assertEqual(len(fake.calls), 1)

    def test_duplicate_key_nan_and_infinity_report_output_invalid(self):
        for raw in ('{"analysis":{},"analysis":{}}', '{"x":NaN}', '{"x":Infinity}', '{"x":1e999}'):
            fake = FakeModelExecutor(raw)
            with self.subTest(raw=raw):
                self.assert_failed(AnalysisRunner(fake).run(self.package), "OUTPUT_INVALID")
                self.assertEqual(len(fake.calls), 1)

    def test_invalid_seven_fields_never_builds_run_evidence(self):
        fake = FakeModelExecutor(mutate=lambda response: response["analysis"].pop("risk_level"))
        with patch("analysis.runner.AnalysisRunEvidence.from_draft", side_effect=AssertionError("must not build")) as build:
            self.assert_failed(AnalysisRunner(fake).run(self.package), "OUTPUT_INVALID")
            build.assert_not_called()

    def test_invalid_input_types_never_call_model(self):
        for value in (None, {}, self.package.to_dict(), "601518.SH"):
            with self.subTest(kind=type(value).__name__): self.assert_failed(self.runner.run(value), "INPUT_INVALID")
        self.assertEqual(self.fake.calls, [])

    def test_hand_constructed_replaced_or_modified_package_rejected(self):
        for package in (AnalysisPackage(**self.package.to_dict()), replace(self.package, stock_id="OTHER.SH")):
            self.assert_failed(self.runner.run(package), "INPUT_INVALID")
        object.__setattr__(self.package, "position_mode", "HOLDING_ANALYSIS")
        self.assert_failed(self.runner.run(self.package), "INPUT_INVALID")
        self.assertEqual(self.fake.calls, [])

    def test_no_runtime_model_default_or_live_capability_claim(self):
        self.assertEqual(ANALYSIS_RUNNER_CAPABILITY, "DEVELOPED")
        self.assertEqual(RUNTIME_ANALYSIS_MODEL, "NOT_FROZEN")
        self.assertEqual((LIVE_MODEL_BACKEND_CONFIGURED, LIVE_MODEL_RUNNING, REAL_MODEL_OUTPUT_VERIFIED), ("NO", "NO", "NO"))
        with self.assertRaises(TypeError): AnalysisRunner()

    def test_missing_empty_or_sensitive_model_version_rejected_before_generate(self):
        for version in (None, "", "  ", "credential", "sk-" + "a" * 24):
            fake = FakeModelExecutor(model_version=version)
            with self.subTest(kind=type(version).__name__):
                self.assert_failed(AnalysisRunner(fake).run(self.package), "MODEL_FAILED")
                self.assertEqual(fake.calls, [])

    def test_model_version_property_failure_is_model_failed(self):
        class BrokenExecutor:
            @property
            def model_version(self): raise RuntimeError("unavailable")
            def generate(self, *_): raise AssertionError("must not generate")
        self.assert_failed(AnalysisRunner(BrokenExecutor()).run(self.package), "MODEL_FAILED")

    def test_model_version_cannot_change_during_run(self):
        fake = FakeModelExecutor()
        original = fake.generate
        def generate(*args):
            response = original(*args)
            fake.model_version = "OTHER-TEST-VERSION"
            return response
        fake.generate = generate
        self.assert_failed(AnalysisRunner(fake).run(self.package), "MODEL_FAILED")
        self.assertEqual(len(fake.calls), 1)

    def test_executor_schema_mutation_cannot_relax_validator(self):
        class MutatingExecutor(FakeModelExecutor):
            def generate(self, envelope, schema):
                schema["properties"]["analysis"]["required"].clear()
                return super().generate(envelope, schema)
        fake = MutatingExecutor(mutate=lambda response: response["analysis"].pop("market_view"))
        self.assert_failed(AnalysisRunner(fake).run(self.package), "OUTPUT_INVALID")

    def test_prompt_input_and_template_hashes_recompute(self):
        result = self.runner.run(self.package)
        envelope = result.prompt_envelope
        value = envelope.to_dict()
        for name in ("input_hash", "NETWORK_CALL_COUNT", "MODEL_CALL_COUNT", "DATABASE_WRITE"): value.pop(name)
        self.assertEqual(envelope.input_hash, hashlib.sha256(stable_json(value).encode()).hexdigest())
        self.assertEqual(envelope.template_hash, hashlib.sha256(TEMPLATE_PATH.read_bytes()).hexdigest())
        self.assertEqual(result.evidence.input_hash, envelope.input_hash)

    def test_each_run_is_one_call_and_unique_evidence_with_stable_output(self):
        first, second = self.runner.run(self.package), self.runner.run(self.package)
        self.assertEqual(len(self.fake.calls), 2)
        self.assertNotEqual(first.evidence.analysis_run_id, second.evidence.analysis_run_id)
        self.assertEqual(first.evidence.validated_output_hash, second.evidence.validated_output_hash)
        self.assertEqual(first.evidence.input_hash, second.evidence.input_hash)

    def test_zero_provider_normalizer_db_network_raw_read_or_writes(self):
        with ExitStack() as stack:
            mocks = [stack.enter_context(patch(name, side_effect=AssertionError("forbidden side effect"))) for name in (
                "socket.create_connection", "socket.socket.connect", "urllib.request.urlopen", "sqlite3.connect",
                "providers.tushare.TushareMCPProvider.fetch", "providers.tdx_realtime.TdxLocalHttpProvider.fetch",
                "providers.tdx_information.TdxInformationProvider.fetch", "normalizer.DataNormalizer.normalize",
                "normalizer.InformationNormalizer.normalize", "evidence.EvidenceBuilder.build",
                "pathlib.Path.write_bytes", "pathlib.Path.write_text", "pathlib.Path.read_text",
            )]
            original_read = Path.read_bytes
            reads = []
            def template_only(path):
                self.assertEqual(path, TEMPLATE_PATH)
                reads.append(path)
                return original_read(path)
            stack.enter_context(patch("pathlib.Path.read_bytes", template_only))
            result = self.runner.run(self.package)
            self.assertEqual(result.status, "UNCONFIRMED")
            self.assertEqual(reads, [TEMPLATE_PATH])
            for mocked in mocks: mocked.assert_not_called()

    def test_no_file_reads_after_executor_begins(self):
        with ExitStack() as stack:
            fake = FakeModelExecutor()
            original = fake.generate
            def generate(*args):
                for name in ("pathlib.Path.read_bytes", "pathlib.Path.read_text", "builtins.open"):
                    stack.enter_context(patch(name, side_effect=AssertionError("late file read")))
                return original(*args)
            fake.generate = generate
            self.assertEqual(AnalysisRunner(fake).run(self.package).status, "UNCONFIRMED")

    def test_new_modules_have_no_external_clients_or_store_imports(self):
        allowed = {"__future__", "typing", "hashlib", "json", "dataclasses", "datetime", "uuid", "re"}
        for filename in ("output_contracts.py", "output_validator.py", "analysis_evidence.py", "model_executor.py", "runner.py"):
            tree = ast.parse((TEMPLATE_PATH.parents[2] / "analysis" / filename).read_text())
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    for alias in node.names: self.assertIn(alias.name.split(".")[0], allowed)
                elif isinstance(node, ast.ImportFrom) and node.level == 0:
                    self.assertIn(node.module.split(".")[0], allowed)


if __name__ == "__main__":
    unittest.main()
