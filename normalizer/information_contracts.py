"""Read-only information gate output; no persistence or raw business input."""

from __future__ import annotations

from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Any, Mapping

from .contracts import NormalizedRecord


SOURCE = "WORKBUDDY_TDX_MCP"
TOOL_CATEGORIES = {
    "wenda_notice_query": "recent_announcements",
    "wenda_news_query": "recent_news",
    "wenda_report_query": "research_summary",
    "tdx_api_data": "financial_summary",
}
TOOL_KINDS = {
    "wenda_notice_query": "announcement", "wenda_news_query": "news",
    "wenda_report_query": "research", "tdx_api_data": "financial",
}
# Exact gateway entries observed in the G0 raw Evidence, not fuzzy aliases.
STATEMENT_ENTRIES = {
    "TdxShareCW.ph_agf10_cw_lyb": "profit",
    "TdxShareCW.ph_agf10_cw_zcfzb": "balance",
    "TdxShareCW.ph_agf10_cw_xjllb": "cashflow",
}
FINANCIAL_METRICS = (
    "revenue", "net_profit", "cash_flow", "total_asset", "total_liability", "roe", "eps",
)


class InformationNormalizationError(ValueError):
    """Raw evidence, identity or an explicitly audited shape failed validation."""


def freeze_information(value: Any) -> Any:
    if isinstance(value, Mapping):
        return MappingProxyType({key: freeze_information(item) for key, item in value.items()})
    if isinstance(value, (tuple, list)):
        return tuple(freeze_information(item) for item in value)
    return value


@dataclass(frozen=True, slots=True)
class InformationNormalizationResult:
    """Compatible with EvidenceBuilder, with a separate original-evidence binding.

    Only InformationNormalizer seals this object. Deserialized or replaced
    instances must go through the gate again. The seal is an in-process
    integrity check, not authentication of external callers.
    """

    category: str
    canonical_data: tuple[Mapping[str, Any], ...]
    normalized_data: tuple[NormalizedRecord, ...]
    provenance: Mapping[str, Any]
    _gate_digest: str | None = field(default=None, init=False, repr=False, compare=False)

    def __post_init__(self) -> None:
        object.__setattr__(self, "canonical_data", freeze_information(self.canonical_data))
        object.__setattr__(self, "provenance", freeze_information(self.provenance))
