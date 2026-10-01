import unittest

from providers import ContractErrorCode, ProviderStatus, TushareMCPProvider


def bar(**changes):
    value = {
        "trade_date": "20260930", "open": 10, "high": 11, "low": 9, "close": 10.5,
        "pre_close": 10, "change": 0.5, "pct_chg": 5, "vol": 1000, "amount": 10500,
        "adj_factor": 1.25,
    }
    value.update(changes)
    return value


class FakeTransport:
    def __init__(self, response=None):
        self.response = response or {"bars": [bar()], "expected_trade_dates": ["20260930"], "raw_reference": "mock://1"}
        self.fetch_calls = []
        self.health_calls = 0

    def fetch_daily(self, token, stock_id, date_from, date_to, **timeouts):
        self.fetch_calls.append((token, stock_id, date_from, date_to, timeouts))
        return self.response

    def health_check(self, token, **timeouts):
        self.health_calls += 1
        return True


class TushareProviderTests(unittest.TestCase):
    request = {"data_kind": "historical_daily", "stock_id": "601518.SH", "date_from": "20260930", "date_to": "20260930"}

    def provider(self, response=None, environ=None):
        transport = FakeTransport(response)
        # An opaque object is sufficient for the mock and avoids embedding any
        # token-shaped credential value in the repository.
        return TushareMCPProvider(
            transport, environ={"TUSHARE_TOKEN": object()} if environ is None else environ
        ), transport

    def test_valid_daily_bars_pass_contract(self):
        provider, transport = self.provider()
        result = provider.fetch(self.request)
        self.assertEqual(result.status, ProviderStatus.AVAILABLE)
        self.assertTrue(provider.validate_contract(result, expected_data_kind="historical_daily").valid)
        self.assertEqual(result.payload["validation"]["errors"], [])
        self.assertEqual(transport.fetch_calls[0][-1], {"connect_timeout": 10, "read_timeout": 30})

    def test_missing_field_fails_and_preserves_raw_reference(self):
        response = {"bars": [bar(amount=None)], "raw_reference": "mock://raw"}
        provider, _ = self.provider(response)
        result = provider.fetch(self.request)
        self.assertEqual(result.status, ProviderStatus.VALIDATION_FAILED)
        self.assertEqual(result.raw_reference, "mock://raw")
        self.assertIn("amount", result.error.message)

    def test_invalid_date_range_is_rejected_without_transport_call(self):
        provider, transport = self.provider()
        result = provider.fetch({**self.request, "date_from": "20261001", "date_to": "20260930"})
        self.assertEqual(result.status, ProviderStatus.VALIDATION_FAILED)
        self.assertEqual(transport.fetch_calls, [])

    def test_anomalies_are_marked_but_not_deleted(self):
        provider, _ = self.provider({"bars": [bar(pct_chg=25, vol=-1)], "raw_reference": "mock://anomaly"})
        result = provider.fetch(self.request)
        self.assertEqual(result.status, ProviderStatus.AVAILABLE)
        self.assertEqual(len(result.payload["bars"]), 1)
        self.assertEqual({item["field"] for item in result.payload["validation"]["anomalies"]}, {"pct_chg", "vol"})

    def test_missing_trade_date_fails_explicitly(self):
        response = {"bars": [bar()], "expected_trade_dates": ["20260929", "20260930"], "raw_reference": "mock://gap"}
        provider, _ = self.provider(response)
        result = provider.fetch(self.request)
        self.assertEqual(result.status, ProviderStatus.VALIDATION_FAILED)
        self.assertEqual(result.payload["validation"]["missing_trade_dates"], ["20260929"])

    def test_cross_responsibility_request_is_rejected(self):
        provider, transport = self.provider()
        result = provider.fetch({**self.request, "data_kind": "realtime_quote"})
        self.assertEqual(result.status, ProviderStatus.VALIDATION_FAILED)
        self.assertEqual(result.error.code, ContractErrorCode.INVALID_DATA_KIND_SCOPE)
        self.assertEqual(transport.fetch_calls, [])

    def test_missing_token_fails_without_exposing_a_secret(self):
        provider, transport = self.provider(environ={})
        with self.assertLogs(level="DEBUG") as logs:
            # assertLogs needs a record; it is deliberately unrelated to provider credentials.
            import logging
            logging.debug("provider test marker")
            result = provider.fetch(self.request)
        self.assertEqual(result.status, ProviderStatus.AUTH_FAILED)
        self.assertEqual(transport.fetch_calls, [])
        self.assertNotIn("token=", " ".join(logs.output).lower() + repr(result).lower())

    def test_health_check_never_fetches_market_data(self):
        provider, transport = self.provider()
        self.assertEqual(provider.health_check(), ProviderStatus.AVAILABLE)
        self.assertEqual(transport.health_calls, 1)
        self.assertEqual(transport.fetch_calls, [])


if __name__ == "__main__":
    unittest.main()
