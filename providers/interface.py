"""Abstract interface implemented by provider adapters."""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any, Mapping

from .contract import ContractValidationResult, ProviderEnvelope, ProviderStatus
from .validation import validate_contract


class Provider(ABC):
    """Transport-independent provider contract."""

    @abstractmethod
    def fetch(self, request: Mapping[str, Any]) -> ProviderEnvelope:
        """Perform one read-only fetch and return a standard envelope."""

    @abstractmethod
    def health_check(self) -> ProviderStatus:
        """Check connectivity/auth only; implementations must not fetch data."""

    @abstractmethod
    def describe(self) -> Mapping[str, Any]:
        """Return the provider's immutable identity and capability metadata."""

    def validate_contract(
        self,
        envelope: ProviderEnvelope | Mapping[str, Any],
        *,
        expected_data_kind: str | None = None,
    ) -> ContractValidationResult:
        """Validate a fetch result before it can reach normalization."""

        return validate_contract(envelope, expected_data_kind=expected_data_kind)
