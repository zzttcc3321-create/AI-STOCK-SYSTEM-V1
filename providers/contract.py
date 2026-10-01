"""Data types shared by every provider implementation."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any


class ProviderStatus(str, Enum):
    """Frozen provider availability and result statuses."""

    AVAILABLE = "AVAILABLE"
    UNAVAILABLE = "UNAVAILABLE"
    UNKNOWN = "UNKNOWN"
    DISABLED = "DISABLED"
    TIMEOUT = "TIMEOUT"
    AUTH_FAILED = "AUTH_FAILED"
    VALIDATION_FAILED = "VALIDATION_FAILED"
    NOT_AVAILABLE = "NOT_AVAILABLE"
    PARTIAL = "PARTIAL"


class ContractErrorCode(str, Enum):
    """Machine-readable contract rejection reasons (not provider statuses)."""

    MISSING_REQUIRED_FIELD = "MISSING_REQUIRED_FIELD"
    INVALID_PROVIDER_IDENTITY = "INVALID_PROVIDER_IDENTITY"
    DATA_KIND_MISMATCH = "DATA_KIND_MISMATCH"
    INVALID_STATUS = "INVALID_STATUS"
    INVALID_DATA_KIND_SCOPE = "INVALID_DATA_KIND_SCOPE"


@dataclass(frozen=True, slots=True)
class ContractError:
    code: ContractErrorCode
    message: str
    retryable: bool = False


@dataclass(frozen=True, slots=True)
class ProviderEnvelope:
    """Source-neutral envelope returned by ``Provider.fetch``.

    ``provider_timestamp`` may be ``None`` when the source supplies no clock,
    while the field itself remains part of every envelope.
    """

    provider_name: str
    data_kind: str
    status: ProviderStatus
    request_timestamp: str
    provider_timestamp: str | None
    raw_reference: str | None
    payload: Any
    error: ContractError | None


@dataclass(frozen=True, slots=True)
class ContractValidationResult:
    """Non-throwing result produced by contract validation."""

    valid: bool
    status: ProviderStatus
    error: ContractError | None = None
