import unittest

from normalizer import ConversionRule, DataNormalizer, DataQualityStatus, UnitStatus
from providers import ProviderEnvelope, ProviderStatus


def daily_bar(**changes):
    value = {
        "ts_code": "601518.SH", "trade_date": "20260930", "open": 10,
        "high": 11, "low": 9, "close": 10.5, "pre_close": 10,
        "vol": 1_000, "amount": 2, "adjust_type": "UNADJUSTED",
    }
    value.update(changes)
    return value


def envelope(*, kind="historical_daily", provider="TUSHARE_MCP", payload=None):
    return ProviderEnvelope(
        provider_name=provider, data_kind=kind, status=ProviderStatus.AVAILABLE,
        request_timestamp="2026-09-30T17:00:00Z", provider_timestamp=None,
        raw_reference="mock://normalizer", payload=payload or {}, error=None,
    )


UNITS = {"price": "元", "vol": "手", "amount": "万元"}


class DailyNormalizerTests(unittest.TestCase):
    def setUp(self):
        self.normalizer = DataNormalizer()

    def normalize(self, bar=None, **kwargs):
        return self.normalizer.normalize(
            envelope(payload={"bars": [bar or daily_bar()]}), units=kwargs.get("units", UNITS)
        )

    def test_five_tuple_conversion_and_pass_canonical(self):
        result = self.normalize()
        fields = result.normalized_data[0].numeric_fields
        self.assertEqual(fields["volume"].raw_value, 1_000)
        self.assertEqual(fields["volume"].normalized_value, 100_000)
        self.assertEqual(fields["volume"].conversion_rule, ConversionRule.LOT100_TO_SHARE)
        self.assertEqual(fields["amount"].normalized_value, 20_000)
        self.assertEqual(fields["amount"].conversion_rule, ConversionRule.TEN_THOUSAND_YUAN_TO_YUAN)
        self.assertEqual(result.canonical_data[0]["data_quality_status"], "PASS")

    def test_unknown_unit_stays_normalized_and_is_not_canonical(self):
        result = self.normalize(units={**UNITS, "price": None})
        price = result.normalized_data[0].numeric_fields["close"]
        self.assertIsNone(price.normalized_value)
        self.assertEqual(price.unit_status, UnitStatus.UNVERIFIED)
        self.assertEqual(result.normalized_data[0].validation_result.status, DataQualityStatus.FAILED)
        self.assertEqual(result.canonical_data, ())

    def test_missing_amount_is_never_derived(self):
        result = self.normalize(daily_bar(amount=None))
        self.assertIsNone(result.normalized_data[0].numeric_fields["amount"].normalized_value)
        self.assertIsNone(result.canonical_data[0]["amount"])
        self.assertNotEqual(result.canonical_data[0]["amount"], 10.5 * 100_000)

    def test_hard_price_invariant_failure_stops_at_normalized(self):
        result = self.normalize(daily_bar(high=8))
        self.assertEqual(result.canonical_data, ())
        validation = result.normalized_data[0].validation_result
        self.assertFalse(validation.passed)
        self.assertEqual(validation.status.value, "FAILED")
        self.assertTrue(any("high" in item for item in validation.failures))

    def test_large_price_change_is_warning_not_failure(self):
        result = self.normalize(daily_bar(close=14, high=14))
        self.assertEqual(result.canonical_data[0]["data_quality_status"], "WARNING")
        self.assertTrue(result.normalized_data[0].validation_result.passed)

    def test_unknown_adjustment_is_unverified_but_canonical(self):
        result = self.normalize(daily_bar(adjust_type=None))
        row = result.canonical_data[0]
        self.assertIsNone(row["adjust_type"])
        self.assertEqual(row["adjust_status"], "UNVERIFIED")
        self.assertEqual(row["data_quality_status"], "UNVERIFIED")

    def test_missing_trading_date_is_recorded_without_interpolation(self):
        payload = {"bars": [daily_bar()], "expected_trade_dates": ["20260929", "20260930"]}
        result = self.normalizer.normalize(envelope(payload=payload), units=UNITS)
        self.assertEqual([(gap.trade_date, gap.status) for gap in result.missing_data], [("2026-09-29", "MISSING")])
        self.assertEqual(len(result.canonical_data), 1)

    def test_illegal_fourth_provider_fails(self):
        result = self.normalizer.normalize(
            envelope(provider="UNAPPROVED_SOURCE", payload={"bars": [daily_bar()]}), units=UNITS
        )
        self.assertEqual(result.canonical_data, ())
        self.assertIn("three frozen sources", result.normalized_data[0].validation_result.failures[0])

    def test_evidence_records_reference_five_tuple_and_validation(self):
        result = self.normalize()
        record = result.normalized_data[0]
        volume_rule = next(item for item in record.normalization_rule if item.field == "volume")
        self.assertIs(volume_rule.input_value, record.numeric_fields["volume"])
        self.assertEqual(volume_rule.rule, ConversionRule.LOT100_TO_SHARE)
        self.assertTrue(record.validation_result.passed)

    def test_both_daily_kind_names_are_accepted(self):
        for kind in ("historical_daily", "daily_kline"):
            with self.subTest(kind=kind):
                result = self.normalizer.normalize(
                    envelope(kind=kind, payload={"bars": [daily_bar()]}), units=UNITS
                )
                self.assertEqual(len(result.canonical_data), 1)


class QuoteNormalizerTests(unittest.TestCase):
    def test_quote_has_independent_contract_and_nullable_book(self):
        payload = {
            "quotes": [{"stock_id": "601518.SH", "quote_timestamp": None, "price": 12,
                        "bid": None, "ask": None, "volume": 5, "amount": None}],
        }
        result = DataNormalizer().normalize(
            envelope(kind="market_quotes", provider="TDX_OFFICIAL_LOCAL_HTTP", payload=payload),
            units={"price": "元", "volume": "手", "amount": "元"},
        )
        quote = result.canonical_data[0]
        self.assertEqual(quote["freshness"], "UNKNOWN")
        self.assertIsNone(quote["bid"])
        self.assertIsNone(quote["ask"])
        self.assertEqual(quote["volume"], 500)
        self.assertEqual(quote["data_quality_status"], "WARNING")


if __name__ == "__main__":
    unittest.main()
