"""Offline regression for R1-D01/D02; no providers or real database writes."""
from copy import deepcopy
from dataclasses import replace
import json
import unittest

from analysis import AnalysisInputError, AnalysisPackageBuilder, PromptBuilder
from evidence import EvidenceBuilder
from evidence.builder import sha256_digest
from normalizer import DataNormalizer
from normalizer.semantics import RULE_VERSION, SEMANTIC_FIELDS
from test_normalizer import daily_bar, envelope, UNITS
from test_analysis_package import canonical_daily, package_arguments
from test_information_normalizer import gate_information


class AdjustmentSemanticsTests(unittest.TestCase):
    def normalize(self, *bars):
        return DataNormalizer().normalize(envelope(payload={"bars": list(bars)}), units=UNITS)

    def arguments(self, bases, quote=False):
        args = package_arguments(quote=quote)
        args["canonical_data"]["daily_kline"] = [canonical_daily(
            trade_date=f"2026-09-{30-i:02d}", adjust_type=basis,
            adjust_status="VERIFIED" if basis else "UNVERIFIED",
            data_quality_status="PASS" if basis else "UNVERIFIED") for i, basis in enumerate(bases)]
        for i in range(len(bases)):
            args["evidence_links"][f"daily_kline.{i}"] = ["EV-DAILY"]
        return args

    def test_mixed_normalization_keeps_raw_and_declares_incomparability(self):
        bars = [daily_bar(), daily_bar(trade_date="20260929", adjust_type="QFQ", adjust_status="VERIFIED")]
        original = deepcopy(bars)
        result = self.normalize(*bars)
        self.assertEqual(bars, original)
        self.assertEqual(result.raw_data.payload["bars"], original)
        self.assertFalse(result.metadata["price_series"][0]["comparable"])
        self.assertEqual([row["adjustment_basis"] for row in result.canonical_data], ["UNADJUSTED", "QFQ"])

    def test_mixed_and_unknown_series_excluded_from_prompt(self):
        for bases in (["UNADJUSTED", "QFQ"], ["QFQ", "HFQ"], [None, None], [None, "UNADJUSTED"]):
            with self.subTest(bases=bases):
                package = AnalysisPackageBuilder().build(**self.arguments(bases))
                self.assertFalse(package.daily_kline_summary["records"])
                self.assertEqual(package.data_quality["data_quality_status"], "UNVERIFIED")
                self.assertFalse(package.data_quality["price_comparability"][0]["comparable"])
                inputs = PromptBuilder().inputs(package)
                self.assertFalse(inputs["canonical_market_data"]["daily_kline"]["records"])
                self.assertTrue(inputs["data_quality_report"]["excluded_record_quality"])

    def test_uniform_bases_and_unknown_single_record(self):
        for basis in ("UNADJUSTED", "QFQ", "HFQ", None):
            package = AnalysisPackageBuilder().build(**self.arguments([basis]))
            self.assertEqual(len(package.daily_kline_summary["records"]), 1)
            self.assertEqual(package.data_quality["price_comparability"][0]["comparable"], basis is not None)

    def test_cross_series_mismatch_declared_and_quote_excluded(self):
        for basis in ("QFQ", "HFQ", None):
            package = AnalysisPackageBuilder().build(**self.arguments([basis], quote=True))
            self.assertFalse(package.latest_quote["records"])
            self.assertEqual(package.data_quality["REALTIME_QUOTE_STATUS"], "MISSING")
            self.assertIn("CROSS_SERIES_ADJUSTMENT_MISMATCH", package.data_quality["price_comparability"][1]["reason_codes"])
        package = AnalysisPackageBuilder().build(**self.arguments(["UNADJUSTED"], quote=True))
        self.assertTrue(package.latest_quote["records"])

    def test_semantics_trace_unit_and_mapping_rules_without_raw_replacement(self):
        result = self.normalize(daily_bar())
        row = result.canonical_data[0]
        self.assertTrue(SEMANTIC_FIELDS <= row.keys())
        self.assertEqual(row["period"], "DAILY")
        self.assertEqual(row["field_semantics"]["close"], "CLOSING_PRICE")
        steps = {step["field"]: step for step in row["conversion_chain"]}
        self.assertEqual(steps["volume"]["source"], "TUSHARE_MCP.row.vol")
        self.assertEqual(steps["volume"]["rule"], "LOT100_TO_SHARE")
        self.assertEqual(steps["amount"]["rule"], "TEN_THOUSAND_YUAN_TO_YUAN")
        self.assertEqual(result.raw_data.payload["bars"][0]["vol"], 1000)

    def test_evidence_metadata_and_hash_cover_basis_and_chain(self):
        result = self.normalize(daily_bar())
        fetch = replace(result.raw_data, raw_reference="file:///tmp/RUN/TASK/REQUEST.json")
        ev = EvidenceBuilder().build(fetch_result=fetch, endpoint_or_tool="daily", request_params={},
            collection_task_id="TASK", system_run_id="RUN", normalization_result=result)
        self.assertEqual(ev.normalized_data_hash, sha256_digest(result.canonical_data))
        sidecars = [json.loads(rule.split(":", 1)[1]) for rule in ev.normalization_rule if rule.startswith("CANONICAL_SEMANTICS:")]
        self.assertEqual(sidecars[0]["adjustment_basis"], "UNADJUSTED")
        self.assertEqual(sidecars[0]["rule_version"], RULE_VERSION)
        changed = deepcopy(result.canonical_data)
        changed[0]["rule_version"] = "FUTURE"
        self.assertNotEqual(sha256_digest(changed), ev.normalized_data_hash)

    def test_gate_rejects_missing_conflicting_and_unsupported_metadata(self):
        for mode in ("missing", "version", "chain_version", "source", "basis", "weekly", "settlement", "chain_missing", "new_rule"):
            with self.subTest(mode=mode):
                args = self.arguments(["UNADJUSTED"])
                row = args["canonical_data"]["daily_kline"][0]
                if mode == "missing": del row["period"]
                elif mode == "version": row["rule_version"] = "CANONICAL_SEMANTICS_V0.9"
                elif mode == "chain_version": row["conversion_chain"][0]["rule_version"] = "V2"
                elif mode == "source": row["conversion_source"] = "WORKBUDDY_TDX_MCP"
                elif mode == "basis": row["adjustment_basis"] = "HFQ"
                elif mode == "weekly": row["period"] = "WEEKLY"
                elif mode == "settlement": row["field_semantics"]["close"] = "SETTLEMENT_PRICE"
                elif mode == "chain_missing": row["conversion_chain"] = []
                else: row["conversion_chain"][0]["rule"] = "NEW_RULE"
                with self.assertRaises(AnalysisInputError): AnalysisPackageBuilder().build(**args)

    def test_versioned_input_cannot_be_renormalized(self):
        with self.assertRaises(ValueError): self.normalize(daily_bar(rule_version="OLD"))

    def test_information_has_versioned_audited_chain_and_keeps_nullable_semantics(self):
        for tool, view in (("wenda_notice_query", None), ("tdx_api_data", "profit")):
            result, evidence = gate_information(tool, view=view)
            row = result.canonical_data[0]
            self.assertTrue((SEMANTIC_FIELDS - {"adjustment_basis"}) <= row.keys())
            self.assertEqual(row["conversion_source"], result.provenance["source_evidence_id"])
            self.assertEqual(row["rule_version"], RULE_VERSION)
            self.assertTrue(all(step["rule_version"] == RULE_VERSION for step in row["conversion_chain"]))
            self.assertEqual(evidence.normalized_data_hash, sha256_digest(result.canonical_data))
            nullable = "net_profit" if view else "publish_time"
            if nullable in row: self.assertEqual(row["field_semantics"][nullable], "UNVERIFIED")

    def test_payload_declarations_preserved_and_not_reinterpreted(self):
        raw = envelope(payload={"bars": [daily_bar()], "period": "WEEKLY"})
        result = DataNormalizer().normalize(raw, units=UNITS)
        row = result.canonical_data[0]
        self.assertEqual(row["period"], "WEEKLY")
        period_step = next(step for step in row["conversion_chain"] if step["field"] == "period")
        self.assertEqual(period_step["source"], "TUSHARE_MCP.payload.period")
        args = package_arguments()
        args["canonical_data"]["daily_kline"] = result.canonical_data
        with self.assertRaises(AnalysisInputError): AnalysisPackageBuilder().build(**args)
        self.assertEqual(raw.payload["bars"][0]["trade_date"], "20260930")

    def test_quote_carries_snapshot_semantics_and_conversion_provenance(self):
        raw = envelope(kind="market_quotes", provider="TDX_OFFICIAL_LOCAL_HTTP", payload={
            "stock_id": "601518.SH", "price": 10, "volume": 5})
        result = DataNormalizer().normalize(raw, units={"price": "元", "volume": "手"})
        row = result.canonical_data[0]
        self.assertTrue(SEMANTIC_FIELDS <= row.keys())
        self.assertEqual(row["period"], "SNAPSHOT")
        self.assertEqual(row["adjustment_basis"], "UNADJUSTED")
        self.assertEqual(row["field_semantics"]["price"], "LAST_TRADED_PRICE")
        step = next(step for step in row["conversion_chain"] if step["field"] == "volume")
        self.assertEqual(step["input_unit"], "手")
        self.assertEqual(step["unit_declaration_source"], "caller.units+payload.units")

    def test_restrictions_survive_draft_limitation_projection(self):
        from analysis import SevenFieldValidator
        from analysis.contracts import jsonable
        from test_analysis_output import deterministic_response
        package = AnalysisPackageBuilder().build(**self.arguments(["QFQ"], quote=True))
        prompt = PromptBuilder().build(package)
        draft = SevenFieldValidator().validate(json.dumps(deterministic_response(prompt)), prompt)
        report = jsonable(draft.limitation_context)["data_quality_report"]
        self.assertEqual(report, package.to_dict()["data_quality"])
        self.assertFalse(report["price_comparability"][1]["comparable"])
        self.assertEqual(report["data_quality_status"], "UNVERIFIED")

    def test_information_old_version_is_rejected_without_reinterpretation(self):
        from unittest.mock import patch
        from normalizer.information import validate_information_result
        from normalizer.information_contracts import InformationNormalizationError
        result, _ = gate_information()
        original = sha256_digest(result.canonical_data)
        with patch("normalizer.information.RULE_VERSION", "CANONICAL_SEMANTICS_V2.0"):
            with self.assertRaises(InformationNormalizationError): validate_information_result(result)
        self.assertEqual(sha256_digest(result.canonical_data), original)

    def test_explicit_adjusted_quote_cannot_be_treated_as_unadjusted(self):
        raw = envelope(kind="market_quotes", provider="TDX_OFFICIAL_LOCAL_HTTP", payload={
            "stock_id": "601518.SH", "price": 10, "adjust_type": "QFQ", "adjust_status": "VERIFIED"})
        result = DataNormalizer().normalize(raw, units={"price": "元"})
        self.assertEqual(result.canonical_data[0]["adjustment_basis"], "QFQ")
        args = package_arguments(quote=True)
        args["canonical_data"]["market_quotes"] = result.canonical_data
        with self.assertRaises(AnalysisInputError): AnalysisPackageBuilder().build(**args)

    def test_batch_quote_inherits_payload_adjustment_and_gate_rejects_it(self):
        for basis in ("QFQ", "HFQ", None, "INVALID"):
            with self.subTest(basis=basis):
                raw = envelope(kind="market_quotes", provider="TDX_OFFICIAL_LOCAL_HTTP", payload={
                    "adjust_type": basis, "adjust_status": "VERIFIED",
                    "quotes": [{"stock_id": "601518.SH", "price": 10},
                               {"stock_id": "601518.SH", "price": 11}]})
                original = deepcopy(raw)
                result = DataNormalizer().normalize(raw, units={"price": "元"})
                self.assertEqual(raw, original)
                self.assertEqual(result.raw_data, original)
                self.assertEqual(len(result.canonical_data), 2)
                for row in result.canonical_data:
                    self.assertEqual(row["adjustment_basis"], basis if basis in {"QFQ", "HFQ"} else "UNKNOWN")
                    step = next(step for step in row["conversion_chain"] if step["field"] == "adjustment_basis")
                    self.assertEqual(step["source"], "TDX_OFFICIAL_LOCAL_HTTP.payload.adjust_type")
                args = package_arguments(quote=True)
                args["canonical_data"]["market_quotes"] = result.canonical_data
                with self.assertRaises(AnalysisInputError): AnalysisPackageBuilder().build(**args)

    def test_batch_quote_conflicting_adjustment_declarations_fail_closed(self):
        for row_declaration in ({"adjust_type": "UNADJUSTED"}, {"adjust_type": "HFQ"},
                                {"adjust_type": None}, {"adjust_status": "UNVERIFIED"},
                                {"adjust_status": None}):
            with self.subTest(row_declaration=row_declaration):
                raw = envelope(kind="market_quotes", provider="TDX_OFFICIAL_LOCAL_HTTP", payload={
                    "adjust_type": "QFQ", "adjust_status": "VERIFIED",
                    "quotes": [{"stock_id": "601518.SH", "price": 10, **row_declaration}]})
                with self.assertRaisesRegex(ValueError, "conflicts with payload declaration"):
                    DataNormalizer().normalize(raw, units={"price": "元"})

    def test_batch_quote_matching_or_absent_adjustment_declarations(self):
        for declarations in ({}, {"adjust_type": "UNADJUSTED", "adjust_status": "VERIFIED"},
                             {"adjust_type": "QFQ", "adjust_status": "VERIFIED"}):
            with self.subTest(declarations=declarations):
                raw = envelope(kind="market_quotes", provider="TDX_OFFICIAL_LOCAL_HTTP", payload={**declarations,
                    "quotes": [{"stock_id": "601518.SH", "price": 10, **declarations}]})
                result = DataNormalizer().normalize(raw, units={"price": "元"})
                self.assertEqual(result.canonical_data[0]["adjustment_basis"],
                                 declarations.get("adjust_type", "UNADJUSTED"))
