"""Offline tests of the canonical business boundary and traceability."""

from copy import deepcopy
from dataclasses import FrozenInstanceError, replace
import unittest

from analysis import AnalysisInputError, AnalysisPackageBuilder
from normalizer import DataNormalizer
from providers import ProviderEnvelope, ProviderStatus
from evidence import EvidenceBuilder
from evidence import EvidenceSource, EvidenceValidationResult, ValidationStatus
from test_information_normalizer import gate_information


STOCK = "601518.SH"
REQUEST_TIME = "2026-09-30T17:00:00Z"


def metadata(identifier="EV-DAILY", source="TUSHARE_MCP", **changes):
    value = {"evidence_id": identifier, "source": source,
             "request_timestamp": REQUEST_TIME, "provider_timestamp": None,
             "validation_result": {"status": "PASS", "failures": []},
             "human_confirm": "PENDING"}
    value.update(changes)
    return value


def canonical_daily(**changes):
    value = {"stock_id": STOCK, "trade_date": "2026-09-30", "open": 10.0,
             "high": 11.0, "low": 9.0, "close": 10.5, "volume": 100000.0,
             "amount": 20000.0, "adjust_type": "UNADJUSTED", "adjust_status": "VERIFIED",
             "provider": "TUSHARE_MCP", "provider_timestamp": None,
             "data_quality_status": "PASS"}
    value.update(changes)
    return value


def canonical_quote(**changes):
    value = {"stock_id": STOCK, "quote_timestamp": "2026-09-30T15:00:00+08:00",
             "price": 10.5, "bid": None, "ask": None, "volume": None, "amount": None,
             "freshness": "REALTIME", "provider": "TDX_OFFICIAL_LOCAL_HTTP",
             "data_quality_status": "PASS"}
    value.update(changes)
    return value


def package_arguments(*, quote=False, holding=False):
    value = {
        "stock_identity": {"stock_id": STOCK, "name": "示意证券", "market": "SH"},
        "canonical_data": {"daily_kline": [canonical_daily()]},
        "evidence_metadata": [metadata()],
        "evidence_links": {"stock_identity": ["EV-DAILY"], "daily_kline.0": ["EV-DAILY"]},
        "source_status": {"TUSHARE_MCP": "PASS", "TDX_OFFICIAL_LOCAL_HTTP": "MISSING",
                          "WORKBUDDY_TDX_MCP": "MISSING"},
    }
    if quote:
        value["canonical_data"]["market_quotes"] = [canonical_quote()]
        value["evidence_metadata"].append(metadata("EV-QUOTE", "TDX_OFFICIAL_LOCAL_HTTP"))
        value["evidence_links"]["market_quotes.0"] = ["EV-QUOTE"]
        value["source_status"]["TDX_OFFICIAL_LOCAL_HTTP"] = "PASS"
    if holding:
        value["holding_snapshot"] = {"stock_id": STOCK, "quantity": 100.0,
                                     "cost_price": 10.0, "snapshot_time": REQUEST_TIME,
                                     "profit_loss": None, "profit_loss_pct": None}
    return value


def information_package_arguments():
    arguments = package_arguments()
    arguments["canonical_information"] = []
    arguments["source_status"]["WORKBUDDY_TDX_MCP"] = "UNVERIFIED"
    number = 0
    for tool, view in [("wenda_notice_query", None), ("wenda_news_query", None),
                       ("wenda_report_query", None), ("tdx_api_data", "profit"),
                       ("tdx_api_data", "balance"), ("tdx_api_data", "cashflow")]:
        result, evidence = gate_information(tool, view=view)
        arguments["canonical_information"].append(result)
        arguments["evidence_metadata"].append(evidence)
        index = number if view else 0
        arguments["evidence_links"][f"{result.category}.{index}"] = [evidence.evidence_id]
        if view:
            number += 1
    return arguments


class CanonicalInformationPackageTests(unittest.TestCase):
    def test_all_six_tool_views_enter_four_supported_blocks(self):
        package = AnalysisPackageBuilder().build(**information_package_arguments())
        for field, count in [("recent_announcements", 1), ("recent_news", 1),
                             ("research_summary", 1), ("financial_summary", 3)]:
            block = getattr(package, field)
            self.assertEqual(len(block["records"]), count)
            self.assertEqual(block["status"], "UNVERIFIED")
            self.assertTrue(all(row["evidence_ids"] for row in block["records"]))
        for field in ("valuation_summary", "sector_context"):
            self.assertEqual(getattr(package, field)["status"], "MISSING")
            self.assertIn(field, package.data_quality["missing_fields"])
        self.assertEqual(package.data_quality["package_status"], "INCOMPLETE")
        self.assertEqual(package.data_quality["data_quality_status"], "UNVERIFIED")

    def test_raw_mapping_envelope_and_plain_canonical_rows_rejected(self):
        result, _ = gate_information()
        for value in ({"data": []}, [dict(result.canonical_data[0])],
                      [{"recent_news": result.canonical_data}], [replace(result)]):
            arguments = package_arguments()
            arguments["canonical_information"] = value
            with self.subTest(value=type(value)), self.assertRaises(AnalysisInputError):
                AnalysisPackageBuilder().build(**arguments)

    def test_missing_fail_or_not_validated_info_evidence_is_excluded(self):
        for mode in ("missing", "FAIL", "NOT_VALIDATED"):
            arguments = information_package_arguments()
            evidence = arguments["evidence_metadata"][2]
            if mode == "missing":
                arguments["evidence_metadata"].pop(2)
            else:
                arguments["evidence_metadata"][2] = replace(evidence,
                    validation_result=EvidenceValidationResult(ValidationStatus(mode)))
            package = AnalysisPackageBuilder().build(**arguments)
            self.assertEqual(package.recent_news["records"], ())
            self.assertEqual(package.data_quality["package_status"], "INCOMPLETE")
            self.assertIn("recent_news.0.evidence", package.data_quality["missing_fields"])
            self.assertNotIn(evidence.evidence_id, [item["evidence_id"] for item in package.evidence_index])

    def test_six_field_pass_metadata_cannot_replace_typed_information_evidence(self):
        arguments = information_package_arguments()
        evidence = arguments["evidence_metadata"][2]
        arguments["evidence_metadata"][2] = metadata(evidence.evidence_id, "WORKBUDDY_TDX_MCP")
        with self.assertRaises(AnalysisInputError):
            AnalysisPackageBuilder().build(**arguments)

    def test_wrong_information_evidence_source_rejected(self):
        arguments = information_package_arguments()
        arguments["evidence_metadata"][2] = replace(arguments["evidence_metadata"][2], source=EvidenceSource.TUSHARE_MCP)
        with self.assertRaises(AnalysisInputError):
            AnalysisPackageBuilder().build(**arguments)

    def test_information_evidence_identity_tool_and_hashes_bound(self):
        for mode in ("stock", "tool", "hash", "raw_hash", "original", "clock", "path"):
            arguments = information_package_arguments()
            evidence = arguments["evidence_metadata"][2]
            changes = {}
            if mode == "stock": changes["request_params"] = {**evidence.request_params, "stock_id": "000001.SZ"}
            if mode == "tool": changes["endpoint_or_tool"] = "wenda_notice_query"
            if mode == "hash": changes["normalized_data_hash"] = "0" * 64
            if mode == "raw_hash": changes["raw_response_hash"] = "0" * 64
            if mode == "original": changes["supersedes_evidence_id"] = "unrelated"
            if mode == "clock": changes["request_timestamp"] = "2026-10-03T12:00:00+08:00"
            if mode == "path": changes["raw_response_path"] = "run008/task008/other/raw.json"
            arguments["evidence_metadata"][2] = replace(evidence, **changes)
            with self.subTest(mode=mode), self.assertRaises(AnalysisInputError):
                AnalysisPackageBuilder().build(**arguments)

    def test_news_evidence_cannot_support_notice(self):
        arguments = information_package_arguments()
        arguments["evidence_links"]["recent_announcements.0"] = arguments["evidence_links"]["recent_news.0"]
        with self.assertRaises(AnalysisInputError):
            AnalysisPackageBuilder().build(**arguments)

    def test_canonical_validation_cannot_reuse_original_evidence_id(self):
        arguments = information_package_arguments()
        evidence = arguments["evidence_metadata"][2]
        identifier = evidence.supersedes_evidence_id
        arguments["evidence_metadata"][2] = replace(evidence, evidence_id=identifier)
        arguments["evidence_links"]["recent_news.0"] = [identifier]
        with self.assertRaises(AnalysisInputError):
            AnalysisPackageBuilder().build(**arguments)

    def test_each_information_record_requires_its_batch_evidence(self):
        arguments = information_package_arguments()
        arguments["evidence_links"].pop("financial_summary.1")
        package = AnalysisPackageBuilder().build(**arguments)
        self.assertEqual(len(package.financial_summary["records"]), 2)
        self.assertIn("financial_summary.1.evidence", package.data_quality["missing_fields"])

    def test_populated_information_and_missing_declaration_conflict(self):
        for field in ("recent_announcements", "recent_news", "research_summary", "financial_summary"):
            for status in ("MISSING", "NOT_AVAILABLE"):
                arguments = information_package_arguments()
                arguments["information_availability"] = {field: {"status": status, "reason": "unavailable"}}
                with self.subTest(field=field, status=status), self.assertRaises(AnalysisInputError):
                    AnalysisPackageBuilder().build(**arguments)

    def test_information_source_missing_or_fourth_source_rejected(self):
        for mode in ("missing", "fourth"):
            arguments = information_package_arguments()
            if mode == "missing": arguments["source_status"]["WORKBUDDY_TDX_MCP"] = "MISSING"
            else: arguments["source_status"]["FOURTH_SOURCE"] = "PASS"
            with self.assertRaises(AnalysisInputError):
                AnalysisPackageBuilder().build(**arguments)

    def test_stock_identity_must_match_information(self):
        arguments = information_package_arguments()
        arguments["stock_identity"]["stock_id"] = "000001.SZ"
        arguments["canonical_data"] = {}
        arguments["evidence_links"].pop("daily_kline.0")
        with self.assertRaises(AnalysisInputError):
            AnalysisPackageBuilder().build(**arguments)

    def test_empty_gate_keeps_category_missing(self):
        arguments = package_arguments()
        result, _ = gate_information(data=[])
        arguments["canonical_information"] = [result]
        package = AnalysisPackageBuilder().build(**arguments)
        self.assertEqual(package.recent_news["status"], "MISSING")

    def test_duplicate_canonical_info_is_rejected(self):
        arguments = information_package_arguments()
        arguments["canonical_information"].append(arguments["canonical_information"][1])
        with self.assertRaises(AnalysisInputError):
            AnalysisPackageBuilder().build(**arguments)


class AnalysisPackageTests(unittest.TestCase):
    def build(self, arguments=None):
        return AnalysisPackageBuilder().build(**(arguments if arguments is not None else package_arguments()))

    def test_canonical_market_and_all_section_9_fields(self):
        package = self.build()
        required = {"stock_identity", "holding_snapshot", "latest_quote", "daily_kline_summary",
                    "recent_announcements", "recent_news", "financial_summary", "valuation_summary",
                    "sector_context", "research_summary", "data_quality", "evidence_index"}
        self.assertTrue(required <= set(package.to_dict()))
        self.assertEqual(package.daily_kline_summary["records"][0]["data"], canonical_daily())
        self.assertEqual(package.stock_id, STOCK)
        self.assertEqual(package.data_quality["REALTIME_QUOTE_STATUS"], "MISSING")

    def test_real_normalizer_output_and_typed_evidence_without_provider_fetch(self):
        envelope = ProviderEnvelope(
            "TUSHARE_MCP", "historical_daily", ProviderStatus.AVAILABLE, REQUEST_TIME, None,
            "run/task/request/raw.json", {"bars": [{"stock_id": STOCK, "trade_date": "20260930",
                "open": 10, "high": 11, "low": 9, "close": 10.5, "volume": 1000,
                "amount": 2, "adjust_type": "UNADJUSTED"}]}, None,
        )
        result = DataNormalizer().normalize(envelope, units={"price": "元", "volume": "手", "amount": "万元"})
        evidence = EvidenceBuilder().build(fetch_result=envelope, endpoint_or_tool="offline_daily",
            request_params={}, collection_task_id="task", system_run_id="run",
            normalization_result=result, evidence_id="EV-DAILY")
        arguments = package_arguments()
        arguments["canonical_data"] = {"daily_kline": result.canonical_data}
        arguments["evidence_metadata"] = [evidence]
        package = self.build(arguments)
        self.assertEqual(package.daily_kline_summary["records"][0]["data"]["volume"], 100000)
        self.assertEqual(set(package.evidence_index[0]), set(metadata()))
        self.assertNotIn("raw_response_path", package.to_json())

    def test_canonical_persistence_metadata_preserved(self):
        arguments = package_arguments()
        arguments["canonical_data"]["daily_kline"][0].update(id=7, created_at=REQUEST_TIME)
        package = self.build(arguments)
        self.assertEqual(package.daily_kline_summary["records"][0]["data"]["id"], 7)

    def test_raw_envelopes_and_full_normalization_objects_rejected(self):
        envelope = ProviderEnvelope("TUSHARE_MCP", "historical_daily", ProviderStatus.AVAILABLE,
                                    REQUEST_TIME, None, None, {}, None)
        normalization = DataNormalizer().normalize(envelope)
        for value in (envelope, normalization, b"raw bytes", {"payload": {"data": []}},
                      {"raw_response": "text"}, {"raw_response_b64": "dGVzdA=="},
                      {"raw_bytes": b"bytes"}, {"raw_data": {}}):
            with self.subTest(kind=type(value).__name__):
                arguments = package_arguments()
                arguments["canonical_data"] = value
                with self.assertRaises(AnalysisInputError):
                    self.build(arguments)

    def test_information_payload_data_cannot_be_relabelled_canonical(self):
        for kind, data in (("financial_summary", {"eps": 1}),
                           ("daily_kline", {"title": "F10"}),
                           ("market_quotes", {"data": []})):
            arguments = package_arguments()
            arguments["canonical_data"] = {kind: [data]}
            with self.subTest(kind=kind), self.assertRaises(AnalysisInputError):
                self.build(arguments)

    def test_raw_fields_rejected_at_every_boundary(self):
        for field in ("stock_identity", "holding_snapshot", "evidence_links", "source_status",
                      "information_availability"):
            arguments = package_arguments()
            arguments[field] = {"raw_response": "forbidden"}
            with self.subTest(field=field), self.assertRaises(AnalysisInputError):
                self.build(arguments)

    def test_nested_raw_and_opaque_values_rejected(self):
        for value in ({"raw_response": "forbidden"}, b"bytes", object()):
            arguments = package_arguments()
            arguments["canonical_data"]["daily_kline"][0]["amount"] = value
            with self.subTest(kind=type(value).__name__), self.assertRaises(AnalysisInputError):
                self.build(arguments)

    def test_failed_daily_and_quote_rejected(self):
        for kind in ("daily_kline", "market_quotes"):
            arguments = package_arguments(quote=True)
            arguments["canonical_data"][kind][0]["data_quality_status"] = "FAILED"
            with self.subTest(kind=kind), self.assertRaises(AnalysisInputError):
                self.build(arguments)

    def test_failed_source_retains_status_but_cannot_supply_data(self):
        arguments = package_arguments()
        arguments["source_status"]["TUSHARE_MCP"] = "FAILED"
        with self.assertRaises(AnalysisInputError):
            self.build(arguments)
        arguments["canonical_data"] = {}
        arguments["evidence_links"].pop("daily_kline.0")
        package = self.build(arguments)
        self.assertEqual(package.data_quality["source_status"]["TUSHARE_MCP"], "FAILED")
        self.assertEqual(package.data_quality["package_status"], "INCOMPLETE")

    def test_missing_or_unavailable_source_cannot_supply_data(self):
        for status in ("MISSING", "NOT_AVAILABLE"):
            arguments = package_arguments()
            arguments["source_status"]["TUSHARE_MCP"] = status
            with self.subTest(status=status), self.assertRaises(AnalysisInputError):
                self.build(arguments)

    def test_not_available_quote_status_preserved_without_realtime_claim(self):
        arguments = package_arguments()
        arguments["source_status"]["TDX_OFFICIAL_LOCAL_HTTP"] = "NOT_AVAILABLE"
        package = self.build(arguments)
        self.assertEqual(package.latest_quote["status"], "NOT_AVAILABLE")
        self.assertEqual(package.data_quality["REALTIME_QUOTE_STATUS"], "MISSING")

    def test_invalid_metadata_types_cannot_smuggle_data(self):
        for changes in ({"id": {"data": "noncanonical"}}, {"created_at": {"data": []}},
                        {"provider": ["TUSHARE_MCP"]}, {"data_quality_status": ["PASS"]},
                        {"adjust_type": ["UNADJUSTED"]}, {"adjust_status": "UNKNOWN"}):
            arguments = package_arguments()
            arguments["canonical_data"]["daily_kline"][0].update(changes)
            with self.subTest(changes=changes), self.assertRaises(AnalysisInputError):
                self.build(arguments)

    def test_missing_realtime_does_not_remove_history(self):
        package = self.build()
        self.assertEqual(len(package.daily_kline_summary["records"]), 1)
        self.assertEqual(package.latest_quote["status"], "MISSING")
        self.assertEqual(package.data_quality["REALTIME_QUOTE_STATUS"], "MISSING")

    def test_present_realtime_and_nullable_fields_preserved(self):
        package = self.build(package_arguments(quote=True))
        row = package.latest_quote["records"][0]["data"]
        self.assertIsNone(row["bid"])
        self.assertIsNone(row["volume"])
        self.assertEqual(package.data_quality["REALTIME_QUOTE_STATUS"], "PASS")

    def test_stale_or_unknown_quote_never_claims_realtime(self):
        for freshness, clock in (("STALE", REQUEST_TIME), ("UNKNOWN", None)):
            arguments = package_arguments(quote=True)
            arguments["canonical_data"]["market_quotes"][0].update(
                freshness=freshness, quote_timestamp=clock, data_quality_status="WARNING")
            with self.subTest(freshness=freshness):
                package = self.build(arguments)
                self.assertEqual(package.data_quality["REALTIME_QUOTE_STATUS"], "MISSING")
                self.assertIn("market_quotes.0", package.data_quality["stale_fields"])

    def test_reported_stale_overrides_quote_realtime(self):
        arguments = package_arguments(quote=True)
        arguments["stale_fields"] = ["market_quotes.0"]
        self.assertEqual(self.build(arguments).data_quality["REALTIME_QUOTE_STATUS"], "MISSING")

    def test_no_timestamp_with_realtime_is_rejected(self):
        arguments = package_arguments(quote=True)
        arguments["canonical_data"]["market_quotes"][0]["quote_timestamp"] = None
        with self.assertRaises(AnalysisInputError):
            self.build(arguments)

    def test_watch_has_null_holding_and_correct_mode(self):
        package = self.build()
        self.assertIsNone(package.holding_snapshot)
        self.assertEqual(package.position_mode, "WATCH_ONLY")
        self.assertEqual(package.analysis_mode, "WATCH_ANALYSIS")

    def test_holding_preserved_without_derived_pnl(self):
        package = self.build(package_arguments(holding=True))
        self.assertEqual(package.position_mode, "HOLDING_ANALYSIS")
        row = package.holding_snapshot["records"][0]["data"]
        self.assertEqual(row["quantity"], 100)
        self.assertIsNone(row["profit_loss"])
        self.assertIsNone(row["profit_loss_pct"])

    def test_missing_holding_cost_fail_closed_without_backfilling(self):
        arguments = package_arguments(holding=True)
        arguments["holding_snapshot"]["cost_price"] = None
        package = self.build(arguments)
        self.assertIsNone(package.holding_snapshot["records"][0]["data"]["cost_price"])
        self.assertIn("holding_snapshot.cost_price", package.data_quality["missing_fields"])
        self.assertFalse(package.data_quality["complete_analysis_input"])

    def test_absent_holding_evidence_preserves_snapshot_without_switching_to_watch(self):
        arguments = package_arguments(holding=True)
        package = self.build(arguments)
        self.assertEqual(package.holding_snapshot["records"][0]["data"], arguments["holding_snapshot"])
        self.assertEqual(package.position_mode, "HOLDING_ANALYSIS")

    def test_three_source_evidence_cannot_claim_holding_quantity_or_cost(self):
        for source in ("TUSHARE_MCP", "TDX_OFFICIAL_LOCAL_HTTP", "WORKBUDDY_TDX_MCP"):
            arguments = package_arguments(holding=True)
            arguments["evidence_metadata"].append(metadata("EV-HOLDING", source))
            arguments["evidence_links"]["holding_snapshot"] = ["EV-HOLDING"]
            with self.subTest(source=source), self.assertRaisesRegex(
                    AnalysisInputError, "holding_snapshot cannot reference Provider Evidence"):
                self.build(arguments)
        for field, value in (("provider", "WORKBUDDY_TDX_MCP"),
                             ("source", "TUSHARE_MCP"), ("evidence_ids", ["EV-DAILY"])):
            arguments = package_arguments(holding=True)
            arguments["holding_snapshot"][field] = value
            with self.subTest(field=field), self.assertRaises(AnalysisInputError):
                self.build(arguments)

    def test_information_absence_and_not_available_are_explicit(self):
        arguments = package_arguments()
        arguments["information_availability"] = {
            "research_summary": {"status": "NOT_AVAILABLE", "reason": "Source returned no reports"}}
        package = self.build(arguments)
        self.assertEqual(package.financial_summary["status"], "MISSING")
        self.assertEqual(package.research_summary["status"], "NOT_AVAILABLE")
        self.assertEqual(package.research_summary["records"], ())
        self.assertEqual(package.research_summary["reason"], "Source returned no reports")
        self.assertFalse(package.data_quality["complete_analysis_input"])

    def test_information_content_rejected_even_with_pass_label(self):
        arguments = package_arguments()
        arguments["information_availability"] = {"financial_summary": {
            "status": "PASS", "reason": "claimed canonical", "records": [{"eps": 1}]}}
        with self.assertRaises(AnalysisInputError):
            self.build(arguments)

    def test_each_market_record_has_own_evidence_references(self):
        arguments = package_arguments(quote=True)
        arguments["canonical_data"]["daily_kline"].append(canonical_daily(trade_date="2026-09-29"))
        arguments["evidence_metadata"].append(metadata("EV-SECOND"))
        arguments["evidence_links"]["daily_kline.1"] = ["EV-SECOND"]
        package = self.build(arguments)
        index = {item["evidence_id"]: item for item in package.evidence_index}
        for name in ("stock_identity", "daily_kline_summary", "latest_quote"):
            for entry in getattr(package, name)["records"]:
                self.assertTrue(entry["evidence_ids"])
                for identifier in entry["evidence_ids"]:
                    self.assertIn(identifier, index)
        self.assertEqual(package.daily_kline_summary["records"][1]["evidence_ids"], ("EV-SECOND",))

    def test_missing_evidence_excludes_only_unsupported_data(self):
        arguments = package_arguments()
        arguments["canonical_data"]["daily_kline"].append(canonical_daily(trade_date="2026-09-29"))
        package = self.build(arguments)
        self.assertEqual(len(package.daily_kline_summary["records"]), 1)
        self.assertIn("daily_kline.1.evidence", package.data_quality["missing_fields"])
        self.assertEqual(package.data_quality["package_status"], "INCOMPLETE")

    def test_missing_all_evidence_removes_business_facts(self):
        arguments = package_arguments()
        arguments["evidence_metadata"] = []
        package = self.build(arguments)
        self.assertEqual(package.stock_identity["records"], ())
        self.assertEqual(package.daily_kline_summary["records"], ())
        self.assertEqual(package.evidence_index, ())
        self.assertNotIn("示意证券", package.to_json())
        self.assertFalse(package.data_quality["complete_analysis_input"])

    def test_failed_evidence_rejected_and_unvalidated_excluded(self):
        arguments = package_arguments()
        arguments["evidence_metadata"][0]["validation_result"]["status"] = "FAIL"
        with self.assertRaises(AnalysisInputError):
            self.build(arguments)
        arguments["evidence_metadata"][0]["validation_result"]["status"] = "NOT_VALIDATED"
        self.assertEqual(self.build(arguments).daily_kline_summary["records"], ())

    def test_evidence_source_must_match_canonical_source(self):
        arguments = package_arguments()
        arguments["evidence_metadata"][0]["source"] = "WORKBUDDY_TDX_MCP"
        with self.assertRaises(AnalysisInputError):
            self.build(arguments)

    def test_unused_evidence_never_enters_index(self):
        arguments = package_arguments()
        arguments["evidence_metadata"].append(metadata("EV-UNUSED"))
        self.assertEqual(len(self.build(arguments).evidence_index), 1)

    def test_duplicate_or_invalid_evidence_rejected(self):
        changes = ({"evidence_id": ""}, {"request_timestamp": "2026-09-30T17:00:00"},
                   {"human_confirm": "APPROVED"}, {"provider_timestamp": "bad-time"},
                   {"validation_result": {"status": "PASS", "failures": ["inconsistent"]}})
        for change in changes:
            arguments = package_arguments()
            arguments["evidence_metadata"][0].update(change)
            with self.subTest(change=change), self.assertRaises(AnalysisInputError):
                self.build(arguments)
        arguments = package_arguments()
        arguments["evidence_metadata"].append(metadata())
        with self.assertRaises(AnalysisInputError):
            self.build(arguments)

    def test_three_source_identity_and_fourth_source_rejection_everywhere(self):
        self.assertEqual(set(self.build().data_quality["source_status"]),
                         {"TUSHARE_MCP", "TDX_OFFICIAL_LOCAL_HTTP", "WORKBUDDY_TDX_MCP"})
        for location in ("row", "status", "evidence"):
            arguments = package_arguments()
            if location == "row":
                arguments["canonical_data"]["daily_kline"][0]["provider"] = "FOURTH_SOURCE"
            elif location == "status":
                arguments["source_status"]["FOURTH_SOURCE"] = "PASS"
            else:
                arguments["evidence_metadata"].append(metadata("EV-UNUSED", "FOURTH_SOURCE"))
            with self.subTest(location=location), self.assertRaises(AnalysisInputError):
                self.build(arguments)

    def test_implicit_fallback_and_wrong_roles_rejected(self):
        for provider in ("TDX_OFFICIAL_LOCAL_HTTP", "WORKBUDDY_TDX_MCP"):
            arguments = package_arguments()
            arguments["canonical_data"]["daily_kline"][0]["provider"] = provider
            arguments["source_status"][provider] = "PASS"
            with self.subTest(provider=provider), self.assertRaises(AnalysisInputError):
                self.build(arguments)
        arguments = package_arguments(quote=True)
        arguments["canonical_data"]["market_quotes"][0]["provider"] = "TUSHARE_MCP"
        with self.assertRaises(AnalysisInputError):
            self.build(arguments)

    def test_explicit_validated_official_fallback(self):
        arguments = package_arguments()
        arguments["canonical_data"]["daily_kline"][0]["provider"] = "TDX_OFFICIAL_LOCAL_HTTP"
        arguments["source_status"].update(TUSHARE_MCP="MISSING", TDX_OFFICIAL_LOCAL_HTTP="PASS")
        arguments["evidence_metadata"][0]["source"] = "TDX_OFFICIAL_LOCAL_HTTP"
        arguments["fallback_used"] = {"from_source": "TUSHARE_MCP", "to_source": "TDX_OFFICIAL_LOCAL_HTTP",
                                      "reason": "Primary missing; official fallback explicitly selected", "validated": True}
        self.assertEqual(self.build(arguments).data_quality["fallback_used"], arguments["fallback_used"])
        for change in ({"validated": False}, {"reason": ""}, {"to_source": "WORKBUDDY_TDX_MCP"},
                       {"to_source": "FOURTH_SOURCE"}):
            bad = deepcopy(arguments)
            bad["fallback_used"].update(change)
            with self.subTest(change=change), self.assertRaises(AnalysisInputError):
                self.build(bad)

    def test_fallback_declaration_without_fallback_data_rejected(self):
        arguments = package_arguments()
        arguments["fallback_used"] = {"from_source": "TUSHARE_MCP", "to_source": "TDX_OFFICIAL_LOCAL_HTTP",
                                      "reason": "Explicit", "validated": True}
        with self.assertRaises(AnalysisInputError):
            self.build(arguments)

    def test_unverified_adjustment_and_warning_remain_explicit(self):
        arguments = package_arguments()
        arguments["canonical_data"]["daily_kline"][0].update(adjust_type=None, adjust_status="UNVERIFIED",
                                                              data_quality_status="UNVERIFIED")
        package = self.build(arguments)
        self.assertEqual(package.daily_kline_summary["status"], "UNVERIFIED")
        self.assertIn("daily_kline.0: UNVERIFIED", package.data_quality["validation_warnings"])
        arguments["canonical_data"]["daily_kline"][0]["data_quality_status"] = "PASS"
        with self.assertRaises(AnalysisInputError):
            self.build(arguments)

    def test_optional_missing_amount_is_not_derived(self):
        arguments = package_arguments()
        arguments["canonical_data"]["daily_kline"][0].update(amount=None, data_quality_status="WARNING")
        package = self.build(arguments)
        self.assertIsNone(package.daily_kline_summary["records"][0]["data"]["amount"])
        self.assertIn("daily_kline.0.amount", package.data_quality["missing_fields"])

    def test_historical_gaps_and_quality_metadata_preserved(self):
        arguments = package_arguments()
        arguments.update(missing_fields=["daily_kline.2026-09-29"], stale_fields=["daily_kline.0"],
                         validation_warnings=["Partial collection; manual review"])
        package = self.build(arguments)
        self.assertIn("daily_kline.2026-09-29", package.data_quality["missing_fields"])
        self.assertIn("daily_kline.0", package.data_quality["stale_fields"])
        self.assertIn("Partial collection; manual review", package.data_quality["validation_warnings"])
        self.assertEqual(len(package.daily_kline_summary["records"]), 1)

    def test_hard_contract_failures_do_not_enter_business_input(self):
        for changes in ({"high": 8}, {"volume": -1}, {"close": None}, {"close": True},
                        {"amount": float("nan")}, {"amount": float("inf")},
                        {"trade_date": "20260930"}, {"trade_date": "2026-02-30"},
                        {"stock_id": "OTHER.SH"}, {"adjust_type": "QFQ", "adjust_status": "UNVERIFIED"},
                        {"data_quality_status": "NOT_VALIDATED"}):
            arguments = package_arguments()
            arguments["canonical_data"]["daily_kline"][0].update(changes)
            with self.subTest(changes=changes), self.assertRaises(AnalysisInputError):
                self.build(arguments)

    def test_duplicate_daily_records_and_ambiguous_quotes_rejected(self):
        for quote in (False, True):
            arguments = package_arguments(quote=quote)
            kind = "market_quotes" if quote else "daily_kline"
            arguments["canonical_data"][kind] *= 2
            with self.subTest(kind=kind), self.assertRaises(AnalysisInputError):
                self.build(arguments)

    def test_dangling_evidence_link_rejected(self):
        arguments = package_arguments()
        arguments["evidence_links"]["daily_kline.5"] = ["EV-DAILY"]
        with self.assertRaises(AnalysisInputError):
            self.build(arguments)

    def test_credential_keys_and_text_never_enter_package(self):
        for key in ("api_key", "Cookie", "Session", "access_token", "credential", "Password"):
            arguments = package_arguments()
            arguments["stock_identity"][key] = "offline-test-value"
            with self.subTest(key=key), self.assertRaises(AnalysisInputError):
                self.build(arguments)
        for text in ("Cookie: offline-test", "Session: offline-test", "API Key: offline-test",
                     "raw_response: offline-test", "Bearer offline-test", "credential offline-test"):
            arguments = package_arguments()
            arguments["validation_warnings"] = [text]
            with self.subTest(text=text), self.assertRaises(AnalysisInputError):
                self.build(arguments)

    def test_serialization_deterministic_and_input_not_mutated(self):
        arguments = package_arguments(quote=True, holding=True)
        original = deepcopy(arguments)
        first, second = self.build(arguments), self.build(arguments)
        self.assertEqual(first.to_json(), second.to_json())
        arguments["evidence_metadata"].reverse()
        arguments["source_status"] = dict(reversed(list(arguments["source_status"].items())))
        self.assertEqual(first.to_json(), self.build(arguments).to_json())
        self.assertEqual(second.to_dict()["daily_kline_summary"]["records"][0]["data"],
                         original["canonical_data"]["daily_kline"][0])
        self.assertEqual(original["evidence_metadata"][0]["human_confirm"], "PENDING")

    def test_recursive_immutability_and_detached_serialization(self):
        arguments = package_arguments()
        package = self.build(arguments)
        before = package.to_json()
        arguments["canonical_data"]["daily_kline"][0]["close"] = 999
        package.to_dict()["daily_kline_summary"]["records"][0]["data"]["close"] = 999
        self.assertEqual(package.to_json(), before)
        with self.assertRaises(TypeError):
            package.daily_kline_summary["records"][0]["data"]["close"] = 999
        with self.assertRaises(FrozenInstanceError):
            package.stock_id = "OTHER"
        self.assertNotIn("_gate_digest", package.to_dict())


if __name__ == "__main__":
    unittest.main()
