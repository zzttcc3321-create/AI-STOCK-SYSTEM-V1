"""Fail-closed validation for provider envelopes."""

from __future__ import annotations

from dataclasses import asdict
from enum import Enum
from typing import Any, Mapping

from .contract import (
    ContractError,
    ContractErrorCode,
    ContractValidationResult,
    ProviderEnvelope,
    ProviderStatus,
)

REQUIRED_ENVELOPE_FIELDS = frozenset(
    {
        "provider_name",
        "data_kind",
        "status",
        "request_timestamp",
        "provider_timestamp",
        "raw_reference",
        "payload",
        "error",
    }
)

# The three identities and their responsibilities are locked by Provider Design
# V1.0.  TUSHARE_PROVIDER is accepted as the interface-level name used by the
# task card, but it has exactly the same (historical-only) scope.
PROVIDER_DATA_KIND_SCOPES: Mapping[str, frozenset[str]] = {
    # ``historical_daily`` is the capability name used by the frozen provider
    # responsibility matrix.  ``daily_kline`` remains accepted for backwards
    # compatibility with the request vocabulary established in P4.1.
    "TUSHARE_MCP": frozenset({"daily_kline", "historical_daily"}),
    "TUSHARE_PROVIDER": frozenset({"daily_kline"}),
    "TDX_OFFICIAL_LOCAL_HTTP": frozenset({"realtime_quote"}),
    "WORKBUDDY_TDX_MCP": frozenset(
        {"announcement", "financial", "news", "research", "valuation"}
    ),
}


def _failure(code: ContractErrorCode, message: str) -> ContractValidationResult:
    return ContractValidationResult(
        valid=False,
        status=ProviderStatus.VALIDATION_FAILED,
        error=ContractError(code=code, message=message),
    )


def _as_mapping(envelope: ProviderEnvelope | Mapping[str, Any]) -> Mapping[str, Any]:
    if isinstance(envelope, ProviderEnvelope):
        return asdict(envelope)
    return envelope


def validate_contract(
    envelope: ProviderEnvelope | Mapping[str, Any],
    *,
    expected_data_kind: str | None = None,
) -> ContractValidationResult:
    """Validate an envelope without propagating malformed-input exceptions."""

    try:
        values = _as_mapping(envelope)
        missing = sorted(REQUIRED_ENVELOPE_FIELDS.difference(values.keys()))
        if missing:
            return _failure(
                ContractErrorCode.MISSING_REQUIRED_FIELD,
                f"Missing required envelope field(s): {', '.join(missing)}",
            )

        provider_name = values["provider_name"]
        if not isinstance(provider_name, str) or provider_name not in PROVIDER_DATA_KIND_SCOPES:
            return _failure(
                ContractErrorCode.INVALID_PROVIDER_IDENTITY,
                "provider_name must identify a registered provider",
            )

        data_kind = values["data_kind"]
        if expected_data_kind is not None and data_kind != expected_data_kind:
            return _failure(
                ContractErrorCode.DATA_KIND_MISMATCH,
                f"Expected data_kind {expected_data_kind!r}, received {data_kind!r}",
            )

        if data_kind not in PROVIDER_DATA_KIND_SCOPES[provider_name]:
            return _failure(
                ContractErrorCode.INVALID_DATA_KIND_SCOPE,
                f"{provider_name} cannot provide data_kind {data_kind!r}",
            )

        status = values["status"]
        raw_status = status.value if isinstance(status, Enum) else status
        try:
            ProviderStatus(raw_status)
        except (TypeError, ValueError):
            return _failure(
                ContractErrorCode.INVALID_STATUS,
                f"Unknown provider status: {raw_status!r}",
            )

        return ContractValidationResult(valid=True, status=ProviderStatus.AVAILABLE)
    except (AttributeError, KeyError, TypeError, ValueError) as exc:
        return _failure(
            ContractErrorCode.MISSING_REQUIRED_FIELD,
            f"Malformed provider envelope: {exc}",
        )
