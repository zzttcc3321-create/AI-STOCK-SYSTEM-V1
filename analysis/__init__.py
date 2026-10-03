"""Canonical-only analysis packages and frozen P3 prompt assembly."""

from .contracts import AnalysisInputError, AnalysisPackage, PromptEnvelope
from .package_builder import AnalysisPackageBuilder
from .prompt_builder import PromptBuilder
from .analysis_evidence import AnalysisRunEvidence
from .model_executor import ModelExecutor
from .output_contracts import (
    ANALYSIS_FIELDS, OUTPUT_SCHEMA_VERSION, AnalysisOutputError, OutputParser,
    ValidatedAnalysisDraft, output_schema,
)
from .output_validator import SevenFieldValidator
from .runner import AnalysisRunner, AnalysisRunResult
from .confirmation_contracts import (
    ConfirmationError, ConfirmationEvent, ConfirmationItem, HumanOverride, PreparedFinalSnapshot,
    validate_confirmation_input, validate_prepared_snapshot,
)
from .confirmation_flow import ConfirmationFlow
from .finalization import (
    FinalAnalysisSnapshot, FinalAnalysisEvidence, FinalizationResult, prepare_final_snapshot,
    finalize_analysis, trace_final_analysis, validate_final_snapshot,
    validate_final_analysis_evidence, validate_finalization_result,
)

__all__ = ["AnalysisInputError", "AnalysisPackage", "AnalysisPackageBuilder",
           "PromptBuilder", "PromptEnvelope", "AnalysisRunEvidence", "ModelExecutor",
           "ANALYSIS_FIELDS", "OUTPUT_SCHEMA_VERSION", "AnalysisOutputError", "OutputParser",
           "ValidatedAnalysisDraft", "output_schema", "SevenFieldValidator", "AnalysisRunner",
           "AnalysisRunResult", "ConfirmationError", "ConfirmationEvent", "ConfirmationItem",
           "HumanOverride", "PreparedFinalSnapshot", "ConfirmationFlow", "FinalAnalysisSnapshot",
           "FinalAnalysisEvidence", "FinalizationResult", "validate_confirmation_input",
           "validate_prepared_snapshot", "prepare_final_snapshot", "finalize_analysis",
           "trace_final_analysis", "validate_final_snapshot", "validate_final_analysis_evidence",
           "validate_finalization_result"]
