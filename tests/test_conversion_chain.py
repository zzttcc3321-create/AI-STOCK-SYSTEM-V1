"""R1-FINAL-02: offline field/source/unit/rule consistency at the business gate."""

from copy import deepcopy
from dataclasses import replace
import unittest

from analysis import AnalysisInputError, AnalysisPackageBuilder, PromptBuilder
from analysis.contracts import safe_json
from evidence import EvidenceBuilder, sha256_digest
from normalizer import DataNormalizer
from normalizer.semantics import validate_metadata
from test_analysis_package import package_arguments
from test_evidence_whitelist import CUTOFF, normalized_arguments
from test_normalizer import daily_bar, envelope, UNITS


class ConversionChainTests(unittest.TestCase):
    def arguments(self, *, units=None, bar=None):
        raw = envelope(payload={"bars": [bar or daily_bar()]})
        result = DataNormalizer().normalize(raw, units=UNITS if units is None else units)
        args = package_arguments()
        args["canonical_data"]["daily_kline"] = deepcopy(list(result.canonical_data))
        args["evidence_metadata"] = [EvidenceBuilder().build(
            fetch_result=replace(raw, raw_reference="run/task/request/daily.json"),
            endpoint_or_tool="offline_daily", request_params={},
            collection_task_id="task", system_run_id="run",
            normalization_result=result, evidence_id="EV-DAILY")]
        return args, result

    def step(self, args, field):
        return next(step for step in args["canonical_data"]["daily_kline"][0]
                    ["conversion_chain"] if step["field"] == field)

    def assert_rejected(self, args, field):
        before = deepcopy(args["canonical_data"])
        evidence_before = [replace(item) for item in args["evidence_metadata"]]
        try:
            with self.assertRaisesRegex(AnalysisInputError, "conversion chain.*" + field):
                AnalysisPackageBuilder().build(**args)
        finally:
            # Rejection adds a reason; it must never repair or replace sealed facts.
            self.assertEqual(args["canonical_data"], before)
            self.assertEqual(args["evidence_metadata"], evidence_before)

    def test_amount_rule_on_traded_shares_is_rejected(self):
        args, result = self.arguments()
        original = deepcopy(result)
        self.step(args, "volume").update(
            source="TUSHARE_MCP.row.amount", rule="TEN_THOUSAND_YUAN_TO_YUAN",
            input_unit="万元", target_unit="元")
        self.assert_rejected(args, "volume")
        self.assertEqual(result, original)
        self.assertEqual(args["evidence_metadata"][0].normalized_data_hash,
                         sha256_digest(result.canonical_data))

    def test_amount_rule_on_price_is_rejected(self):
        args, _ = self.arguments()
        self.step(args, "close").update(rule="TEN_THOUSAND_YUAN_TO_YUAN", input_unit="万元")
        self.assert_rejected(args, "close")

    def test_correct_rule_with_wrong_source_field_is_rejected(self):
        args, _ = self.arguments()
        self.step(args, "volume")["source"] = "TUSHARE_MCP.row.amount"
        self.assert_rejected(args, "volume")

    def test_identity_with_conflicting_units_is_rejected(self):
        args, _ = self.arguments()
        self.step(args, "volume").update(rule="IDENTITY", input_unit="元", target_unit="股")
        self.assert_rejected(args, "volume")

    def test_conversion_with_wrong_input_unit_is_rejected(self):
        args, _ = self.arguments()
        self.step(args, "amount")["input_unit"] = "元"
        self.assert_rejected(args, "amount")

    def test_conversion_with_wrong_target_unit_is_rejected(self):
        args, _ = self.arguments()
        self.step(args, "volume")["target_unit"] = "元"
        self.assert_rejected(args, "volume")

    def test_conversion_without_unit_declarations_is_rejected(self):
        args, _ = self.arguments()
        step = self.step(args, "volume")
        del step["input_unit"]
        del step["target_unit"]
        self.assert_rejected(args, "volume")

    def test_conflicting_duplicate_steps_are_rejected(self):
        args, _ = self.arguments()
        row = args["canonical_data"]["daily_kline"][0]
        duplicate = dict(self.step(args, "volume"), rule="IDENTITY", input_unit="股")
        row["conversion_chain"] = [*row["conversion_chain"], duplicate]
        self.assert_rejected(args, "volume")

    def test_mapping_rule_cannot_bypass_numeric_units(self):
        args, _ = self.arguments()
        self.step(args, "volume")["rule"] = "FIELD_MAPPING"
        self.assert_rejected(args, "volume")

    def test_unverified_rule_cannot_be_wrapped_as_verified(self):
        args, _ = self.arguments()
        self.step(args, "volume")["rule"] = "UNVERIFIED"
        self.assert_rejected(args, "volume")

    def test_date_rule_cannot_be_used_for_stock_identity(self):
        args, _ = self.arguments()
        self.step(args, "stock_id")["rule"] = "TRADE_DATE_ISO"
        self.assert_rejected(args, "stock_id")

    def test_period_source_cannot_be_an_amount_field(self):
        args, _ = self.arguments()
        self.step(args, "period")["source"] = "TUSHARE_MCP.row.amount"
        self.assert_rejected(args, "period")

    def test_semantics_validator_rejects_conflict_without_mutation(self):
        args, _ = self.arguments()
        self.step(args, "volume").update(rule="TEN_THOUSAND_YUAN_TO_YUAN",
                                          input_unit="万元", target_unit="元")
        row = args["canonical_data"]["daily_kline"][0]
        before = deepcopy(row)
        with self.assertRaisesRegex(ValueError, "conversion chain.*volume.*TRADED_SHARES"):
            validate_metadata(row, "daily_kline")
        self.assertEqual(row, before)

    def test_quote_volume_cannot_use_amount_conversion(self):
        args, _ = normalized_arguments("UNADJUSTED")
        row = args["canonical_data"]["market_quotes"][0]
        step = next(step for step in row["conversion_chain"] if step["field"] == "volume")
        step.update(source="TDX_OFFICIAL_LOCAL_HTTP.row.amount",
                    rule="TEN_THOUSAND_YUAN_TO_YUAN", input_unit="万元", target_unit="元")
        self.assert_rejected(args, "volume")

    def test_partial_identity_unit_declaration_is_rejected(self):
        args, _ = self.arguments()
        del self.step(args, "close")["target_unit"]
        self.assert_rejected(args, "close")

    def test_undeclared_field_in_chain_is_rejected(self):
        args, _ = self.arguments()
        self.step(args, "pre_close")["field"] = "invented"
        self.assert_rejected(args, "invented")

    def test_missing_optional_identity_values_remain_missing(self):
        # The normalizer records IDENTITY for an absent amount, even if its
        # declared raw unit is 万元. No conversion was applied to missing data.
        args, result = self.arguments(bar=daily_bar(amount=None))
        self.assertEqual(self.step(args, "amount")["rule"], "IDENTITY")
        package = AnalysisPackageBuilder().build(**args)
        self.assertEqual(package.daily_kline_summary["status"], "WARNING")
        self.assertIsNone(package.daily_kline_summary["records"][0]["data"]["amount"])
        self.assertEqual(package.to_dict()["daily_kline_summary"]["records"][0]["data"]
                         ["conversion_chain"], safe_json(result.canonical_data[0]["conversion_chain"]))

    def test_normal_traded_shares_chain_remains_pass(self):
        args, result = self.arguments()
        package = AnalysisPackageBuilder().build(**args)
        self.assertEqual(package.daily_kline_summary["status"], "PASS")
        self.assertEqual(package.data_quality["data_quality_status"], "PASS")
        self.assertEqual(package.to_dict()["daily_kline_summary"]["records"][0]["data"],
                         safe_json(result.canonical_data[0]))

    def test_amount_conversion_and_all_existing_unit_aliases_remain_pass(self):
        for lot, amount, price in (("手", "万元", "元"), ("lot", "万人民币", "yuan"),
                                   (" LOTS ", " 10K YUAN ", " CNY ")):
            with self.subTest(lot=lot, amount=amount, price=price):
                args, _ = self.arguments(units={"vol": lot, "amount": amount, "price": price})
                package = AnalysisPackageBuilder().build(**args)
                self.assertEqual(self.step(args, "amount")["rule"], "TEN_THOUSAND_YUAN_TO_YUAN")
                self.assertEqual(package.daily_kline_summary["status"], "PASS")
                self.assertEqual(package.data_quality["data_quality_status"], "PASS")

    def test_normalized_identity_chain_remains_pass(self):
        args, _ = self.arguments(units={"volume": "shares", "amount": "cny", "price": "yuan"},
                                 bar=daily_bar(volume=100000, amount=20000))
        self.assertEqual(self.step(args, "volume")["source"], "TUSHARE_MCP.row.volume")
        self.assertTrue(all(self.step(args, field)["rule"] == "IDENTITY"
                            for field in ("open", "high", "low", "close", "volume", "amount")))
        self.assertEqual(AnalysisPackageBuilder().build(**args).daily_kline_summary["status"], "PASS")

    def test_existing_identity_metadata_without_units_remains_pass(self):
        args = package_arguments(quote=True)
        package = AnalysisPackageBuilder().build(**args)
        self.assertEqual(package.daily_kline_summary["status"], "PASS")
        self.assertEqual(package.latest_quote["status"], "PASS")
        self.assertEqual(package.data_quality["data_quality_status"], "PASS")

    def test_final_01_cutoff_recheck_preserves_conversion_and_evidence(self):
        args, results = normalized_arguments("UNADJUSTED", quote_time="2026-09-30T16:55:50Z")
        args["judgment_cutoff"] = CUTOFF
        before = deepcopy(args["canonical_data"])
        evidence_before = [replace(item) for item in args["evidence_metadata"]]
        package = AnalysisPackageBuilder().build(**args)
        entry = package.latest_quote["records"][0]
        self.assertEqual(entry["data"]["freshness"], "REALTIME")
        self.assertEqual(entry["freshness_at_cutoff"]["freshness"], "STALE")
        self.assertEqual(entry["freshness_at_cutoff"]["freshness_check"]["age_seconds"], 310)
        self.assertEqual(package.latest_quote["status"], "WARNING")
        self.assertEqual(package.data_quality["REALTIME_QUOTE_STATUS"], "MISSING")
        retained = package.to_dict()["latest_quote"]["records"][0]["data"]
        for field in ("conversion_chain", "freshness_check"):
            self.assertEqual(retained[field], safe_json(results[1].canonical_data[0][field]))
        self.assertEqual(args["canonical_data"], before)
        self.assertEqual(args["evidence_metadata"], evidence_before)

    def test_final_03_whitelist_only_includes_retained_evidence(self):
        args, _ = normalized_arguments("QFQ")
        evidence_before = [replace(item) for item in args["evidence_metadata"]]
        package = AnalysisPackageBuilder().build(**args)
        prompt = PromptBuilder().build(package)
        self.assertEqual(package.daily_kline_summary["status"], "PASS")
        self.assertEqual(package.latest_quote["records"], ())
        self.assertEqual(set(prompt.evidence_ids), {"EV-DAILY"})
        self.assertEqual({item["evidence_id"] for item in package.evidence_index}, {"EV-DAILY"})
        self.assertEqual(args["evidence_metadata"], evidence_before)
