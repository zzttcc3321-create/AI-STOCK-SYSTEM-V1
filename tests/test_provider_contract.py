from dataclasses import asdict
import unittest

from providers import (
    ContractErrorCode,
    ProviderEnvelope,
    ProviderStatus,
    validate_contract,
)


def valid_envelope() -> ProviderEnvelope:
    return ProviderEnvelope(
        provider_name="TUSHARE_MCP",
        data_kind="daily_kline",
        status=ProviderStatus.AVAILABLE,
        request_timestamp="2026-10-01T09:00:00+08:00",
        provider_timestamp=None,
        raw_reference="raw/request-1.json",
        payload={"bars": []},
        error=None,
    )


class ProviderContractValidationTests(unittest.TestCase):
    def test_valid_envelope_passes(self) -> None:
        result = validate_contract(valid_envelope())
        self.assertTrue(result.valid)
        self.assertEqual(result.status, ProviderStatus.AVAILABLE)
        self.assertIsNone(result.error)

    def test_missing_field_fails_without_raising(self) -> None:
        envelope = asdict(valid_envelope())
        del envelope["request_timestamp"]

        result = validate_contract(envelope)

        self.assertFalse(result.valid)
        self.assertEqual(result.status, ProviderStatus.VALIDATION_FAILED)
        self.assertEqual(result.error.code, ContractErrorCode.MISSING_REQUIRED_FIELD)

    def test_illegal_status_fails(self) -> None:
        envelope = asdict(valid_envelope())
        envelope["status"] = "OK_BUT_NOT_FROZEN"

        result = validate_contract(envelope)

        self.assertFalse(result.valid)
        self.assertEqual(result.status, ProviderStatus.VALIDATION_FAILED)
        self.assertEqual(result.error.code, ContractErrorCode.INVALID_STATUS)

    def test_cross_responsibility_request_is_rejected(self) -> None:
        envelope = asdict(valid_envelope())
        envelope["provider_name"] = "TUSHARE_PROVIDER"
        envelope["data_kind"] = "realtime_quote"

        result = validate_contract(envelope)

        self.assertFalse(result.valid)
        self.assertEqual(result.status, ProviderStatus.VALIDATION_FAILED)
        self.assertEqual(result.error.code, ContractErrorCode.INVALID_DATA_KIND_SCOPE)


if __name__ == "__main__":
    unittest.main()
