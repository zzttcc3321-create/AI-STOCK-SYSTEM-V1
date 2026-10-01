"""Provider contract foundation.

This package contains contracts only.  It deliberately contains no data-source
client or transport implementation.
"""

from .contract import (
    ContractError,
    ContractErrorCode,
    ContractValidationResult,
    ProviderEnvelope,
    ProviderStatus,
)
from .interface import Provider
from .validation import validate_contract

__all__ = [
    "ContractError",
    "ContractErrorCode",
    "ContractValidationResult",
    "Provider",
    "ProviderEnvelope",
    "ProviderStatus",
    "validate_contract",
]
