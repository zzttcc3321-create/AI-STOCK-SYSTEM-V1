"""Historical evidence selection, synthetic clocks, no provider or DB writes."""

from copy import deepcopy
from dataclasses import replace
import json
import unittest

from analysis import AnalysisInputError, AnalysisPackageBuilder
from analysis.prompt_builder import PromptBuilder
from evidence import EvidenceBuilder, sha256_digest
from evidence.contracts import TimeFact
from evidence.knowability import cutoff_reasons, time_facts
from normalizer import DataNormalizer, InformationNormalizer, InformationNormalizationError
from providers import ProviderEnvelope, ProviderStatus
from test_analysis_package import package_arguments, STOCK
from test_information_normalizer import information_input


CUTOFF = "2026-04-15T16:00:00+08:00"
DISCLOSED = "2026-04-10T09:00:00+08:00"
ACQUIRED = "2026-04-10T10:00:00+08:00"
REQUESTED = "2026-04-10T09:59:00+08:00"


def financial(*, disclosure=DISCLOSED, acquired=ACQUIRED, cutoff=None, extra=None):
    envelope, _, _ = information_input("tdx_api_data", view="profit")
    data = deepcopy(envelope.payload["data"])
    row = data["transformed"]["tables"][0]["rows"][0]
    row["日期"] = "2026-03-31"
    if disclosure is not None:
        row["disclosure_time"] = disclosure
    row.update(extra or {})
    envelope, original, raw = information_input("tdx_api_data", view="profit", data=data)
    envelope = replace(envelope, request_timestamp=REQUESTED)
    original = replace(original, request_timestamp=REQUESTED)
    result = InformationNormalizer().normalize(envelope, source_evidence=original,
        raw_bytes=raw, acquired_at=acquired, judgment_cutoff=cutoff)
    evidence = EvidenceBuilder().build(fetch_result=envelope, endpoint_or_tool=original.endpoint_or_tool,
        request_params=original.request_params, system_run_id=original.system_run_id,
        collection_task_id=original.collection_task_id, normalization_result=result,
        supersedes_evidence_id=original.evidence_id, raw_response_hash=original.raw_response_hash,
        evidence_id="FIN-KNOWABILITY")
    arguments = package_arguments()
    arguments["canonical_information"] = [result]
    arguments["evidence_metadata"].append(evidence)
    arguments["evidence_links"]["financial_summary.0"] = [evidence.evidence_id]
    arguments["source_status"]["WORKBUDDY_TDX_MCP"] = "UNVERIFIED"
    return result, evidence, arguments


def restriction(package, path="financial_summary.0"):
    return next(item for item in package.data_quality["knowability_restrictions"] if item["path"] == path)


class KnowabilityTests(unittest.TestCase):
    def test_report_date_uses_china_calendar_across_midnight_and_offsets(self):
        _, _, arguments = financial(extra={"日期": "2026-04-15"})
        self.assert_period_calendar(arguments, "financial_summary", "financial_summary.0")

    def test_trade_date_uses_china_calendar_across_midnight_and_offsets(self):
        arguments = package_arguments()
        arguments["canonical_data"]["daily_kline"][0].update(
            trade_date="2026-04-15", knowability=time_facts(
                period="2026-04-15", period_basis="canonical.trade_date",
                disclosure=TimeFact(DISCLOSED, "row.disclosure_time", "VERIFIED"),
                acquired_at=ACQUIRED, judgment_cutoff=None))
        arguments["evidence_metadata"][0]["request_timestamp"] = REQUESTED
        self.assert_period_calendar(arguments, "daily_kline_summary", "daily_kline.0")

    def assert_period_calendar(self, arguments, block, path):
        original = deepcopy(arguments["stock_identity"])
        boundaries = [
            (("2026-04-14T23:59:59+08:00", "2026-04-14T15:59:59Z",
              "2026-04-14T11:59:59-04:00"), False),
            (("2026-04-15T00:00:00+08:00", "2026-04-14T16:00:00Z",
              "2026-04-14T12:00:00-04:00"), True),
            (("2026-04-15T01:00:00+08:00", "2026-04-14T17:00:00Z",
              "2026-04-14T13:00:00-04:00"), True),
        ]
        for equivalents, retained in boundaries:
            outcomes = []
            for boundary in equivalents:
                with self.subTest(block=block, cutoff=boundary):
                    package = AnalysisPackageBuilder().build(**arguments, judgment_cutoff=boundary)
                    self.assertEqual(bool(getattr(package, block)["records"]), retained)
                    codes = () if retained else restriction(package, path)["reason_codes"]
                    self.assertEqual(tuple(codes), () if retained else ("DATA_PERIOD_AFTER_CUTOFF",))
                    outcomes.append(tuple(codes))
                    self.assertIn("UTC+08:00", package.data_quality["data_period_calendar"])
            self.assertEqual(len(set(outcomes)), 1)
        self.assertEqual(arguments["stock_identity"], original)

    def test_date_without_calendar_contract_is_explicitly_unverified(self):
        times = time_facts(period="2026-04-15", period_basis="unknown.date",
            disclosure=TimeFact(DISCLOSED, "row.disclosure_time", "VERIFIED"),
            acquired_at=ACQUIRED, judgment_cutoff=CUTOFF)
        for boundary in ("2026-04-15T01:00:00+08:00", "2026-04-14T17:00:00Z"):
            self.assertEqual(cutoff_reasons(times, boundary), ("DATA_PERIOD_UNVERIFIED",))
        times["data_period"] = {"value": "2026-04-14T17:00:00Z",
                                "source_basis": "row.timestamp", "status": "VERIFIED"}
        self.assertEqual(cutoff_reasons(times, "2026-04-15T01:00:00+08:00"), ())
        self.assertEqual(cutoff_reasons(times, "2026-04-14T16:59:59Z"),
                         ("DATA_PERIOD_AFTER_CUTOFF",))

    def test_historical_identity_retains_evidenced_lookup_key_without_four_clocks(self):
        for embedded_cutoff in (None, CUTOFF):
            _, _, arguments = financial(cutoff=embedded_cutoff)
            arguments["stock_identity"] = {"stock_id": STOCK}
            original = deepcopy(arguments["stock_identity"])
            package = AnalysisPackageBuilder().build(**arguments, judgment_cutoff=CUTOFF)
            entry = package.stock_identity["records"][0]
            self.assertEqual(entry["data"], {"stock_id": STOCK})
            self.assertEqual(entry["evidence_ids"], ("EV-DAILY",))
            self.assertEqual(entry["identity_scope"], "LOOKUP_KEY_ONLY")
            self.assertIn("EV-DAILY", [item["evidence_id"] for item in package.evidence_index])
            self.assertFalse(any(item["path"].startswith("stock_identity")
                                 for item in package.data_quality["knowability_restrictions"]))
            self.assertFalse(any(item.startswith("stock_identity")
                                 for item in package.data_quality["incomplete_reasons"]))
            self.assertEqual(arguments["stock_identity"], original)

    def test_historical_identity_attributes_are_excluded_and_auditable(self):
        _, _, arguments = financial()
        arguments["stock_identity"].update(symbol="601518", industry="未来行业属性")
        original = deepcopy(arguments["stock_identity"])
        current = AnalysisPackageBuilder().build(**arguments)
        self.assertEqual(current.stock_identity["records"][0]["data"], original)
        package = AnalysisPackageBuilder().build(**arguments, judgment_cutoff=CUTOFF)
        self.assertEqual(package.stock_identity["records"][0]["data"], {"stock_id": STOCK})
        for field in ("symbol", "market", "name", "industry"):
            path = "stock_identity." + field
            limit = restriction(package, path)
            self.assertEqual(limit["reason_codes"], ("IDENTITY_ATTRIBUTE_TIME_UNVERIFIED",))
            self.assertFalse(limit["usable_at_cutoff"])
            self.assertEqual(limit["evidence_ids"], ("EV-DAILY",))
            self.assertIn(path, package.data_quality["missing_fields"])
        inputs = PromptBuilder().inputs(package)
        self.assertNotIn("未来行业属性", json.dumps(inputs, ensure_ascii=False))
        self.assertNotIn("示意证券", package.to_json())
        self.assertIn("IDENTITY_ATTRIBUTE_TIME_UNVERIFIED", json.dumps(inputs))
        self.assertEqual(package.stock_identity["status"], "PASS")
        self.assertEqual(package.data_quality["package_status"], "INCOMPLETE")
        self.assertEqual(arguments["stock_identity"], original)

    def test_historical_identity_still_requires_validated_evidence(self):
        for mode in ("missing", "NOT_VALIDATED", "FAIL"):
            arguments = package_arguments()
            arguments["stock_identity"] = {"stock_id": STOCK}
            arguments["canonical_data"] = {}
            arguments["evidence_links"].pop("daily_kline.0")
            if mode == "missing":
                arguments["evidence_metadata"] = []
            else:
                arguments["evidence_metadata"][0]["validation_result"]["status"] = mode
            with self.subTest(mode=mode):
                if mode == "FAIL":
                    with self.assertRaises(AnalysisInputError):
                        AnalysisPackageBuilder().build(**arguments, judgment_cutoff=CUTOFF)
                else:
                    package = AnalysisPackageBuilder().build(**arguments, judgment_cutoff=CUTOFF)
                    self.assertEqual(package.stock_identity["records"], ())
                    self.assertIn("stock_identity.evidence", package.data_quality["missing_fields"])
                    self.assertEqual(package.evidence_index, ())

    def test_four_independent_facts_and_disclosed_before_cutoff_usable(self):
        result, evidence, arguments = financial()
        row = result.canonical_data[0]
        self.assertEqual(row["report_date"], "2026-03-31")
        self.assertEqual(row["publish_time"], DISCLOSED)
        self.assertEqual(row["knowability"]["acquired_at"]["value"], ACQUIRED)
        self.assertEqual(row["knowability"]["disclosed_at"]["source_basis"], "statement.row.disclosure_time")
        self.assertIsNone(row["knowability"]["judgment_cutoff"]["value"])
        self.assertEqual(evidence.normalized_data_hash, sha256_digest(result.canonical_data))
        for boundary in (CUTOFF, ACQUIRED):
            package = AnalysisPackageBuilder().build(**arguments, judgment_cutoff=boundary)
            retained = package.financial_summary["records"][0]["data"]
            self.assertEqual(retained["knowability"]["judgment_cutoff"]["value"], boundary)
            self.assertIn(evidence.evidence_id, [item["evidence_id"] for item in package.evidence_index])
            inputs = PromptBuilder().inputs(package)
            self.assertEqual(inputs["financial_data"]["records"][0]["data"], retained)
        _, _, equal_inputs = financial(disclosure=ACQUIRED)
        self.assertEqual(len(AnalysisPackageBuilder().build(**equal_inputs, judgment_cutoff=ACQUIRED)
                             .financial_summary["records"]), 1)

    def test_q1_disclosed_after_april_15_is_excluded_and_limits_reach_prompt(self):
        _, evidence, arguments = financial(disclosure="2026-04-20T09:00:00+08:00",
                                           acquired="2026-04-20T10:00:00+08:00")
        package = AnalysisPackageBuilder().build(**arguments, judgment_cutoff=CUTOFF)
        self.assertEqual(package.financial_summary["records"], ())
        limits = restriction(package)
        self.assertIn("DISCLOSED_AFTER_CUTOFF", limits["reason_codes"])
        self.assertFalse(limits["usable_at_cutoff"])
        self.assertEqual(limits["knowability"]["data_period"]["value"], "2026-03-31")
        self.assertNotIn(evidence.evidence_id, [item["evidence_id"] for item in package.evidence_index])
        inputs = PromptBuilder().inputs(package)
        self.assertEqual(inputs["financial_data"]["records"], [])
        self.assertIn("DISCLOSED_AFTER_CUTOFF", json.dumps(inputs["data_quality_report"]))
        self.assertEqual(package.data_quality["package_status"], "INCOMPLETE")

    def test_missing_ambiguous_or_conflicting_disclosure_fails_closed(self):
        cases = [(None, {"late": "2026-04-01"}, "DISCLOSED_AT_UNVERIFIED"),
                 ("2026-04-10", {}, "DISCLOSED_AT_UNVERIFIED"),
                 ("not-a-clock", {}, "DISCLOSED_AT_UNVERIFIED"),
                 (DISCLOSED, {"publish_time": "2026-04-12T09:00:00+08:00"}, "SOURCE_TIME_CONFLICT"),
                 (DISCLOSED, {"publish_time": "2026-04-20"}, "SOURCE_TIME_CONFLICT")]
        for disclosed, extra, reason in cases:
            with self.subTest(disclosure=disclosed, extra=extra):
                result, _, arguments = financial(disclosure=disclosed, extra=extra)
                package = AnalysisPackageBuilder().build(**arguments, judgment_cutoff=CUTOFF)
                self.assertEqual(package.financial_summary["records"], ())
                self.assertIn(reason, restriction(package)["reason_codes"])
                self.assertEqual(result.canonical_data[0]["data_quality_status"], "UNVERIFIED")
        _, _, arguments = financial(extra={"publish_time": "2026-04-10T01:00:00Z"})
        self.assertEqual(len(AnalysisPackageBuilder().build(**arguments, judgment_cutoff=CUTOFF)
                             .financial_summary["records"]), 1)

    def test_acquisition_late_missing_or_inconsistent_is_restricted(self):
        for acquired, code in (("2026-04-16T10:00:00+08:00", "ACQUIRED_AFTER_CUTOFF"),
                               (None, "ACQUIRED_AT_UNVERIFIED")):
            with self.subTest(acquired=acquired):
                result, _, arguments = financial(acquired=acquired)
                package = AnalysisPackageBuilder().build(**arguments, judgment_cutoff=CUTOFF)
                self.assertEqual(package.financial_summary["records"], ())
                self.assertIn(code, restriction(package)["reason_codes"])
                if acquired is None:
                    self.assertIsNone(result.canonical_data[0]["knowability"]["acquired_at"]["value"])
        _, _, arguments = financial(disclosure="2026-04-11T09:00:00+08:00")
        self.assertIn("SOURCE_TIME_CONFLICT", restriction(
            AnalysisPackageBuilder().build(**arguments, judgment_cutoff=CUTOFF))["reason_codes"])
        with self.assertRaises(InformationNormalizationError):
            financial(acquired="2026-04-09T10:00:00+08:00")

    def test_sealed_cutoff_cannot_disappear_or_conflict_and_hashes_bind_time(self):
        result, evidence, arguments = financial(cutoff=CUTOFF)
        package = AnalysisPackageBuilder().build(**arguments)
        self.assertEqual(package.data_quality["judgment_cutoff"]["value"], CUTOFF)
        with self.assertRaisesRegex(AnalysisInputError, "conflicting judgment cutoffs"):
            AnalysisPackageBuilder().build(**arguments, judgment_cutoff="2026-04-16T16:00:00+08:00")
        for bad in ("2026-04-15", "2026-04-15T16:00:00", "2026-02-30T16:00:00Z"):
            with self.subTest(cutoff=bad), self.assertRaises(AnalysisInputError):
                AnalysisPackageBuilder().build(**arguments, judgment_cutoff=bad)
        changed, _, _ = financial(disclosure="2026-04-11T09:00:00+08:00", cutoff=CUTOFF)
        self.assertNotEqual(evidence.normalized_data_hash, sha256_digest(changed.canonical_data))
        with self.assertRaises(TypeError):
            result.canonical_data[0]["knowability"]["disclosed_at"]["value"] = CUTOFF
        envelope, original, raw = information_input(data=[])
        empty = InformationNormalizer().normalize(envelope, source_evidence=original,
            raw_bytes=raw, judgment_cutoff=CUTOFF)
        empty_inputs = package_arguments()
        empty_inputs["canonical_information"] = [empty]
        self.assertEqual(AnalysisPackageBuilder().build(**empty_inputs).data_quality
                         ["judgment_cutoff"]["value"], CUTOFF)
        for bad_information in (None, {"financial_summary": []}):
            bad_inputs = package_arguments()
            bad_inputs["canonical_information"] = bad_information
            with self.assertRaises(AnalysisInputError):
                AnalysisPackageBuilder().build(**bad_inputs)
        arguments["evidence_metadata"][-1] = replace(evidence, normalized_data_hash="0" * 64)
        with self.assertRaises(AnalysisInputError):
            AnalysisPackageBuilder().build(**arguments)

    def test_market_and_holding_future_inputs_cannot_bypass_cutoff(self):
        arguments = package_arguments(quote=True, holding=True)
        package = AnalysisPackageBuilder().build(**arguments, judgment_cutoff=CUTOFF)
        self.assertEqual(package.daily_kline_summary["records"], ())
        self.assertEqual(package.latest_quote["records"], ())
        self.assertEqual(package.holding_snapshot["records"], ())
        self.assertIn("DATA_PERIOD_AFTER_CUTOFF", restriction(package, "daily_kline.0")["reason_codes"])
        for kind, payload in (("historical_daily", {"bars": [{"stock_id": STOCK, "trade_date": "20260410",
                "open": 10, "high": 11, "low": 9, "close": 10.5, "volume": 100,
                "amount": 1000, "adjust_type": "UNADJUSTED", "disclosure_time": DISCLOSED}]}),
                ("realtime_quote", {"stock_id": STOCK, "price": 10.5,
                 "quote_timestamp": DISCLOSED, "disclosure_time": DISCLOSED})):
            source = "TUSHARE_MCP" if kind == "historical_daily" else "TDX_OFFICIAL_LOCAL_HTTP"
            envelope = ProviderEnvelope(source, kind, ProviderStatus.AVAILABLE, REQUESTED, None,
                                       "run/task/request/raw.json", payload, None)
            normalized = DataNormalizer().normalize(envelope, units={"price": "元", "volume": "股", "amount": "元"},
                                                     acquired_at=ACQUIRED, judgment_cutoff=CUTOFF)
            row = normalized.canonical_data[0]
            self.assertEqual(row["knowability"]["acquired_at"]["value"], ACQUIRED)
            inputs = package_arguments()
            field = "daily_kline" if kind == "historical_daily" else "market_quotes"
            inputs["canonical_data"] = {field: [row]}
            inputs["evidence_links"] = {"stock_identity": ["EV-DAILY"], field + ".0": ["EV-DAILY"]}
            inputs["evidence_metadata"][0].update(source=source, request_timestamp=REQUESTED)
            inputs["source_status"][source] = "PASS"
            block = "daily_kline_summary" if kind == "historical_daily" else "latest_quote"
            self.assertEqual(len(getattr(AnalysisPackageBuilder().build(**inputs), block)["records"]), 1)
            inputs["canonical_data"][field][0] = {**row, "knowability": time_facts(
                period=row.get("trade_date", row.get("quote_timestamp")), period_basis="canonical.period",
                disclosure=TimeFact(None, "canonical.disclosure_missing"), acquired_at=ACQUIRED,
                judgment_cutoff=CUTOFF)}
            self.assertEqual(getattr(AnalysisPackageBuilder().build(**inputs), block)["records"], ())
            inputs["canonical_data"][field][0] = {**row, "knowability": time_facts(
                period="2026-03-31", period_basis="canonical.period",
                disclosure=TimeFact(DISCLOSED, "provider.row.disclosure_time", "VERIFIED"),
                acquired_at=ACQUIRED, judgment_cutoff=CUTOFF)}
            self.assertIn("SOURCE_TIME_CONFLICT", restriction(
                AnalysisPackageBuilder().build(**inputs), field + ".0")["reason_codes"])
