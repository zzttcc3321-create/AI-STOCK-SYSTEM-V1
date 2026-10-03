"""One-shot, persistence-neutral analysis orchestration over gated packages."""

from __future__ import annotations

from dataclasses import dataclass

from .analysis_evidence import AnalysisRunEvidence, validate_model_version
from .contracts import AnalysisInputError, AnalysisPackage, PromptEnvelope
from .model_executor import ModelExecutor
from .output_contracts import AnalysisOutputError, ValidatedAnalysisDraft, output_schema
from .output_validator import SevenFieldValidator
from .package_builder import validate_package
from .prompt_builder import PromptBuilder

ANALYSIS_RUNNER_CAPABILITY = "DEVELOPED"
RUNTIME_ANALYSIS_MODEL = "NOT_FROZEN"
LIVE_MODEL_BACKEND_CONFIGURED = "NO"
LIVE_MODEL_RUNNING = "NO"
REAL_MODEL_OUTPUT_VERIFIED = "NO"


@dataclass(frozen=True, slots=True)
class AnalysisRunResult:
    status: str
    prompt_envelope: PromptEnvelope | None = None
    draft: ValidatedAnalysisDraft | None = None
    evidence: AnalysisRunEvidence | None = None
    error_code: str | None = None
    MODEL_CALL_COUNT: int = 0


class AnalysisRunner:
    """No stores, Provider clients, file readers or default executor.

    PromptBuilder alone reads its frozen template. Validation thereafter uses
    the envelope in memory. Executor exceptions never expose provider content.
    A successful run is an unconfirmed draft, never a final conclusion.
    """

    def __init__(self, executor: ModelExecutor):
        self._executor = executor

    def run(self, package: AnalysisPackage) -> AnalysisRunResult:
        try:
            validate_package(package)
            envelope = PromptBuilder().build(package)
        except AnalysisInputError:
            return AnalysisRunResult(status="INPUT_INVALID", error_code="INPUT_INVALID")
        try:
            model_version = self._executor.model_version
            validate_model_version(model_version)
        except Exception:
            return AnalysisRunResult(status="MODEL_FAILED", prompt_envelope=envelope, error_code="MODEL_FAILED")
        try:
            raw = self._executor.generate(envelope, output_schema())
            if self._executor.model_version != model_version:
                raise AnalysisOutputError("model_version changed during generation")
        except Exception:
            return AnalysisRunResult(status="MODEL_FAILED", prompt_envelope=envelope,
                                     error_code="MODEL_FAILED", MODEL_CALL_COUNT=1)
        try:
            draft = SevenFieldValidator().validate(raw, envelope)
            evidence = AnalysisRunEvidence.from_draft(envelope, draft, model_version=model_version)
        except (AnalysisOutputError, AnalysisInputError, ValueError, TypeError, RecursionError, UnicodeError):
            return AnalysisRunResult(status="OUTPUT_INVALID", prompt_envelope=envelope,
                                     error_code="OUTPUT_INVALID", MODEL_CALL_COUNT=1)
        return AnalysisRunResult(status="UNCONFIRMED", prompt_envelope=envelope,
                                 draft=draft, evidence=evidence, MODEL_CALL_COUNT=1)
