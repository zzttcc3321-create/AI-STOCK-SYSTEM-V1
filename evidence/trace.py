"""Read-only trace, replay review, and data-package evidence indexing."""

from __future__ import annotations

from typing import Any, Callable, Iterable, Mapping

from .builder import sha256_digest
from .contracts import (
    AnalysisConclusion, EvidenceRecord, ReviewDifferenceEvent, ReviewRecord, TraceResult,
)


class EvidenceTrace:
    def __init__(self, evidence: Iterable[EvidenceRecord], conclusions: Iterable[AnalysisConclusion]) -> None:
        self._evidence = {item.evidence_id: item for item in evidence}
        self._conclusions = {item.analysis_report_id: item for item in conclusions}

    def query(self, analysis_report_id: str) -> TraceResult:
        conclusion = self._conclusions.get(analysis_report_id)
        if conclusion is None:
            return TraceResult(analysis_report_id, False, (), ("analysis_report",))
        missing: list[str] = []
        if not conclusion.structured_fields:
            missing.append("structured_fields")
        if not conclusion.model_version:
            missing.append("model_version")
        if not conclusion.calculation:
            missing.append("calculation")
        chain = []
        for evidence_id in conclusion.evidence_ids:
            item = self._evidence.get(evidence_id)
            if item is None:
                missing.append(f"evidence:{evidence_id}")
                continue
            for field in ("raw_response_path", "source", "request_timestamp"):
                if not getattr(item, field):
                    missing.append(f"{evidence_id}.{field}")
            if item.human_confirm.value == "REJECTED":
                missing.append(f"{evidence_id}.human_confirm_rejected")
            chain.append({
                "evidence_id": evidence_id, "structured_fields": conclusion.structured_fields,
                "model_version": conclusion.model_version, "calculation": conclusion.calculation,
                "raw_response_path": item.raw_response_path, "source": item.source.value,
                "request_timestamp": item.request_timestamp, "provider_timestamp": item.provider_timestamp,
            })
        return TraceResult(analysis_report_id, not missing, tuple(chain), tuple(missing))


def replay_review(
    conclusion: AnalysisConclusion, evidence: Iterable[EvidenceRecord], *,
    raw_reader: Callable[[str], Any], normalize: Callable[[Any, tuple[str, ...] | None], Any],
    calculate: Callable[[tuple[Any, ...], str], Any],
) -> ReviewRecord:
    """Replay without mutating either evidence or conclusion."""
    evidence = tuple(evidence)
    rows, differences, ids = [], [], []
    for item in evidence:
        ids.append(item.evidence_id)
        if not item.raw_response_path:
            differences.append(ReviewDifferenceEvent(item.evidence_id, "raw_response_path is missing"))
            continue
        raw = raw_reader(item.raw_response_path)
        if sha256_digest(raw) != item.raw_response_hash:
            differences.append(ReviewDifferenceEvent(item.evidence_id, "raw_response_hash mismatch"))
            continue
        normalized = normalize(raw, item.normalization_rule)
        if sha256_digest(normalized) != item.normalized_data_hash:
            differences.append(ReviewDifferenceEvent(item.evidence_id, "normalized_data_hash mismatch"))
            continue
        rows.append(normalized)
    if not differences and conclusion.model_version:
        output = calculate(tuple(rows), conclusion.model_version)
        expected = next((item.analysis_output_hash for item in evidence if item.analysis_output_hash), None)
        if expected is None or sha256_digest(output) != expected:
            differences.append(ReviewDifferenceEvent(ids[0] if ids else "", "analysis_output_hash mismatch"))
    elif not conclusion.model_version:
        differences.append(ReviewDifferenceEvent(ids[0] if ids else "", "model_version is missing"))
    return ReviewRecord(conclusion.analysis_report_id, not differences, tuple(ids), tuple(differences))


def evidence_index(records: Iterable[EvidenceRecord]) -> tuple[Mapping[str, Any], ...]:
    return tuple({
        "source": item.source.value, "request_timestamp": item.request_timestamp,
        "provider_timestamp": item.provider_timestamp,
        "validation_result": item.validation_result.status.value,
        "human_confirm": item.human_confirm.value,
    } for item in records)
