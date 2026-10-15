"""B02/B03 regressions using synthetic data and an in-memory transport only."""

from dataclasses import asdict, replace
import unittest

from evidence import EvidenceBuilder
from normalizer import DataNormalizer, UnitStatus
from providers import ProviderStatus, TushareMCPProvider
from test_normalizer import daily_bar, envelope, UNITS
from test_tushare_provider import FakeTransport, bar


RAW_PATH = "archive/run-integrity/task-integrity/request/response.json"


def fetch_bars(bars):
    provider = TushareMCPProvider(
        FakeTransport({"bars": bars, "raw_reference": RAW_PATH}),
        environ={"TUSHARE_TOKEN": object()},
    )
    return provider.fetch({"data_kind": "historical_daily", "stock_id": "601518.SH",
                           "date_from": "20260930", "date_to": "20260930"})


def evidence(fetch, normalized):
    return EvidenceBuilder().build(
        fetch_result=fetch, normalization_result=normalized, endpoint_or_tool="daily",
        request_params={}, system_run_id="run-integrity", collection_task_id="task-integrity",
    )


class BooleanNumericRegressionTests(unittest.TestCase):
    def assert_boolean_volume_rejected(self, unit):
        fetch = replace(envelope(payload={"bars": [daily_bar(vol=True)]}), raw_reference=RAW_PATH)
        result = DataNormalizer().normalize(fetch, units={**UNITS, "vol": unit})
        field = result.normalized_data[0].numeric_fields["volume"]
        self.assertIs(field.raw_value, True)
        self.assertIsNone(field.normalized_value)
        self.assertEqual(field.unit_status, UnitStatus.UNVERIFIED)
        self.assertFalse(result.normalized_data[0].validation_result.passed)
        self.assertEqual(result.canonical_data, ())
        self.assertEqual(evidence(fetch, result).validation_result.status.value, "FAIL")

    def test_boolean_volume_in_shares_never_becomes_one(self):
        self.assert_boolean_volume_rejected("股")

    def test_boolean_volume_in_lots_never_becomes_one_hundred(self):
        self.assert_boolean_volume_rejected("手")

    def test_other_daily_numeric_fields_reject_both_booleans(self):
        for name in ("open", "high", "low", "close", "pre_close", "volume", "amount"):
            for value in (True, False):
                with self.subTest(field=name, value=value):
                    result = DataNormalizer().normalize(
                        envelope(payload={"bars": [daily_bar(**{name: value})]}),
                        units={**UNITS, "volume": "股"},
                    )
                    field = result.normalized_data[0].numeric_fields[name]
                    self.assertIsNone(field.normalized_value)
                    self.assertEqual(field.unit_status, UnitStatus.UNVERIFIED)
                    self.assertNotEqual(result.normalized_data[0].validation_result.status.value, "PASS")

    def test_quote_numeric_fields_reject_booleans(self):
        for name in ("price", "bid", "ask", "volume", "amount"):
            with self.subTest(field=name):
                row = {"stock_id": "601518.SH", "price": 10, "bid": 10, "ask": 10,
                       "volume": 100, "amount": 1000, name: True}
                result = DataNormalizer().normalize(
                    envelope(kind="market_quotes", provider="TDX_OFFICIAL_LOCAL_HTTP",
                             payload={"quotes": [row]}),
                    units={"price": "元", "volume": "股", "amount": "元"},
                )
                self.assertIsNone(result.normalized_data[0].numeric_fields[name].normalized_value)
                self.assertEqual(result.canonical_data, ())

    def test_provider_rejects_boolean_numeric_fields(self):
        for name in ("open", "high", "low", "close", "pre_close", "change",
                     "pct_chg", "vol", "amount", "adj_factor"):
            for value in (True, False):
                with self.subTest(field=name, value=value):
                    fetch = fetch_bars([bar(**{name: value})])
                    self.assertEqual(fetch.status, ProviderStatus.VALIDATION_FAILED)
                    self.assertTrue(any(name in error for error in fetch.payload["validation"]["errors"]))


class ProviderFailureRegressionTests(unittest.TestCase):
    def failed_fetch(self):
        missing_factor = bar(ts_code="601518.SH", adjust_type="UNADJUSTED")
        del missing_factor["adj_factor"]
        valid = bar(ts_code="601518.SH", trade_date="20260929", adjust_type="UNADJUSTED")
        return fetch_bars([missing_factor, valid])

    def test_missing_adjustment_factor_fails_entire_normalization_and_evidence(self):
        fetch = self.failed_fetch()
        self.assertEqual(fetch.status, ProviderStatus.VALIDATION_FAILED)
        self.assertIn("bar[0] missing adj_factor", fetch.payload["validation"]["errors"])
        for source in (fetch, {**asdict(fetch), "status": "VALIDATION_FAILED"}):
            with self.subTest(envelope_type=type(source).__name__):
                result = DataNormalizer().normalize(source, units=UNITS)
                self.assertEqual(result.canonical_data, ())
                self.assertEqual(len(result.normalized_data), 2)
                for row in result.normalized_data:
                    self.assertFalse(row.validation_result.passed)
                    self.assertEqual(row.validation_result.status.value, "FAILED")
                    self.assertIn("provider status VALIDATION_FAILED", row.validation_result.failures)
                self.assertEqual(evidence(source, result).validation_result.status.value, "FAIL")

    def test_evidence_consumes_failed_envelope_even_when_rows_pass_or_are_absent(self):
        fetch = self.failed_fetch()
        row_only_result = DataNormalizer().normalize(
            replace(fetch, status=ProviderStatus.AVAILABLE), units=UNITS,
        )
        self.assertTrue(all(row.validation_result.passed for row in row_only_result.normalized_data))
        for result in (row_only_result, None):
            with self.subTest(has_normalization=result is not None):
                validation = evidence(fetch, result).validation_result
                self.assertEqual(validation.status.value, "FAIL")
                self.assertIn("provider status VALIDATION_FAILED", validation.failures)


if __name__ == "__main__":
    unittest.main()
