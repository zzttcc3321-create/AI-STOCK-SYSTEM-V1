"""Offline quote clocks: normalizer -> evidenced package -> caller prompt."""

from copy import deepcopy
from dataclasses import replace
import json
import unittest

from analysis import AnalysisInputError, AnalysisPackageBuilder
from analysis.prompt_builder import PromptBuilder
from evidence import EvidenceBuilder, sha256_digest
from normalizer import DataNormalizer
from providers import ProviderEnvelope, ProviderStatus
from test_analysis_package import package_arguments, STOCK


REQUEST = "2026-09-30T17:00:00Z"
FRESH = "2026-09-30T16:59:59Z"


def quote_input(**changes):
    quote = {"stock_id": STOCK, "price": 10.5, "quote_timestamp": FRESH,
             "freshness": "REALTIME"}
    quote.update(changes)
    return ProviderEnvelope("TDX_OFFICIAL_LOCAL_HTTP", "realtime_quote",
        ProviderStatus.AVAILABLE, REQUEST, None, "run/task/request/raw.json", quote, None)


def quote_evidence(envelope, result):
    return EvidenceBuilder().build(fetch_result=envelope, endpoint_or_tool="offline_quote",
        request_params={"stock_id": STOCK}, collection_task_id="task", system_run_id="run",
        normalization_result=result, evidence_id="EV-QUOTE")


def package_for(envelope, result, *, evidence=None, **context):
    evidence = evidence or quote_evidence(envelope, result)
    arguments = package_arguments(quote=True)
    arguments["canonical_data"]["market_quotes"] = result.canonical_data
    arguments["evidence_metadata"][-1] = evidence
    return AnalysisPackageBuilder().build(**arguments, **context), evidence


class QuoteFreshnessTests(unittest.TestCase):
    def normalize(self, envelope=None, **context):
        return DataNormalizer().normalize(envelope or quote_input(), units={"price": "元"}, **context)

    def assert_state(self, envelope, state, reason=None, **context):
        result = self.normalize(envelope, **context)
        row = result.canonical_data[0]
        self.assertEqual(row["freshness"], state)
        check = row["freshness_check"]
        self.assertEqual(check["contract_version"], "QUOTE_FRESHNESS_V1.0")
        self.assertEqual(check["stale_after_seconds"], 300)
        self.assertEqual(check["future_tolerance_seconds"], 0)
        self.assertEqual(check["request_context_max_gap_seconds"], 300)
        self.assertEqual(check["request_timestamp"], envelope.request_timestamp)
        self.assertEqual(check["quote_timestamp"], row["quote_timestamp"])
        if reason:
            self.assertIn(reason, check["reason_codes"])
        package, evidence = package_for(envelope, result)
        realtime = "PASS" if state == "REALTIME" else "MISSING"
        self.assertEqual(package.data_quality["REALTIME_QUOTE_STATUS"], realtime)
        inputs = PromptBuilder().inputs(package)
        self.assertEqual(inputs["data_quality_report"]["REALTIME_QUOTE_STATUS"], realtime)
        records = inputs["canonical_market_data"]["market_quotes"]["records"]
        if records:
            self.assertEqual(records[0]["data"]["freshness_check"], json.loads(json.dumps(check)))
        else:
            # R1-D03 still excludes records without historical knowability.
            self.assertNotEqual(state, "REALTIME")
            self.assertIsNotNone(context.get("judgment_cutoff"))
        self.assertEqual(evidence.normalized_data_hash, sha256_digest(result.canonical_data))
        if state != "REALTIME":
            self.assertEqual(row["data_quality_status"], "WARNING")
            self.assertIn("market_quotes.0", package.data_quality["stale_fields"])
            if reason:
                self.assertTrue(any(reason in warning for warning in
                    inputs["data_quality_report"]["validation_warnings"]))
        return row

    def test_missing_timestamp_and_present_control(self):
        self.assert_state(quote_input(), "REALTIME")
        for declared in (None, "REALTIME", "STALE", "UNKNOWN"):
            with self.subTest(declared=declared):
                self.assert_state(quote_input(quote_timestamp=None, freshness=declared),
                                  "UNKNOWN", "QUOTE_TIMESTAMP_MISSING")

    def test_expired_timestamp_and_threshold_control(self):
        self.assert_state(quote_input(quote_timestamp="2026-09-30T16:55:00Z"), "REALTIME")
        for declared in (None, "REALTIME", "STALE", "UNKNOWN"):
            with self.subTest(declared=declared):
                self.assert_state(quote_input(quote_timestamp="2026-09-30T16:54:59Z", freshness=declared),
                                  "STALE", "STALE_QUOTE_TIMESTAMP")
        self.assert_state(quote_input(quote_timestamp="2020-01-01T00:00:00Z", freshness=None),
                          "STALE", "STALE_QUOTE_TIMESTAMP")

    def test_future_timestamp_and_equal_control(self):
        self.assert_state(quote_input(quote_timestamp=REQUEST), "REALTIME")
        for declared in (None, "REALTIME"):
            with self.subTest(declared=declared):
                self.assert_state(quote_input(quote_timestamp="2026-09-30T17:00:00.000001Z", freshness=declared),
                                  "UNKNOWN", "FUTURE_QUOTE_TIMESTAMP")
        # Quotes collected after request start are valid at acquisition completion.
        self.assert_state(quote_input(quote_timestamp="2026-09-30T17:00:01Z"), "REALTIME",
                          acquired_at="2026-09-30T17:00:02Z")
        # A later decision clock cannot hide a quote from after acquisition.
        self.assert_state(quote_input(quote_timestamp="2026-09-30T17:00:03Z"),
                          "UNKNOWN", "QUOTE_AFTER_ACQUISITION",
                          acquired_at="2026-09-30T17:00:02Z",
                          judgment_cutoff="2026-09-30T17:00:04Z")

    def test_request_context_mismatch_and_boundary_control(self):
        self.assert_state(quote_input(quote_timestamp="2026-09-30T17:05:00Z"), "REALTIME",
                          acquired_at="2026-09-30T17:05:00Z")
        for declared in (None, "REALTIME"):
            for request in ("2026-09-30T16:54:59Z", "2026-09-30T17:00:01Z"):
                with self.subTest(declared=declared, request=request):
                    self.assert_state(replace(quote_input(freshness=declared), request_timestamp=request),
                                      "UNKNOWN", "REQUEST_TIME_MISMATCH", judgment_cutoff=REQUEST)
        self.assert_state(quote_input(quote_timestamp="2026-09-30T17:05:01Z"),
                          "UNKNOWN", "REQUEST_TIME_MISMATCH",
                          acquired_at="2026-09-30T17:05:01Z")

    def test_timestamp_presence_does_not_supply_a_declaration(self):
        self.assert_state(quote_input(), "REALTIME")
        envelope = quote_input()
        del envelope.payload["freshness"]
        self.assert_state(envelope, "UNKNOWN", "FRESHNESS_UNDECLARED")
        row = self.assert_state(quote_input(freshness=None), "UNKNOWN", "FRESHNESS_UNDECLARED")
        self.assertEqual(row["freshness_check"]["age_seconds"], 1)

    def test_clock_source_and_explicit_null_preserved(self):
        envelope = quote_input()
        del envelope.payload["quote_timestamp"]
        envelope = replace(envelope, provider_timestamp=FRESH)
        row = self.assert_state(envelope, "REALTIME")
        self.assertEqual(row["freshness_check"]["timestamp_source"], "envelope.provider_timestamp")
        row = self.assert_state(replace(quote_input(quote_timestamp=None), provider_timestamp=FRESH),
                                "UNKNOWN", "QUOTE_TIMESTAMP_MISSING")
        self.assertEqual(row["freshness_check"]["timestamp_source"], "provider.row.quote_timestamp")
        self.assert_state(replace(envelope, payload={**envelope.payload, "freshness": None}),
                          "UNKNOWN", "FRESHNESS_UNDECLARED")

    def test_reference_priority_timezone_and_knowability_independence(self):
        envelope = quote_input(disclosure_time=FRESH)
        before = deepcopy(envelope)
        row = self.assert_state(envelope, "REALTIME", acquired_at="2026-09-30T17:00:01Z",
                                judgment_cutoff="2026-10-01T01:00:02+08:00")
        self.assertEqual(row["freshness_check"]["reference_source"], "caller.judgment_cutoff")
        self.assertEqual(row["freshness_check"]["age_seconds"], 3)
        self.assertEqual(row["knowability"]["disclosed_at"]["value"], FRESH)
        self.assertEqual(row["knowability"]["acquired_at"]["value"], "2026-09-30T17:00:01Z")
        self.assertEqual(row["knowability"]["judgment_cutoff"]["value"], "2026-10-01T01:00:02+08:00")
        self.assertEqual(envelope, before)
        row = self.assert_state(envelope, "REALTIME", acquired_at="2026-09-30T17:00:01Z")
        self.assertEqual(row["freshness_check"]["reference_source"], "caller.acquired_at")
        row = self.assert_state(envelope, "REALTIME")
        self.assertEqual(row["freshness_check"]["reference_source"], "envelope.request_timestamp")
        self.assertNotIn("knowability", row)
        self.assert_state(quote_input(quote_timestamp="2026-10-01T01:00:00+08:00"), "REALTIME")
        # The chosen decision, not the envelope clock, determines quote age.
        self.assert_state(quote_input(quote_timestamp="2026-09-30T16:55:01Z"),
                          "STALE", "STALE_QUOTE_TIMESTAMP", judgment_cutoff="2026-09-30T17:00:02Z")

    def test_invalid_clocks_and_declarations_fail_closed(self):
        self.assert_state(quote_input(), "REALTIME")
        for timestamp in ("bad", "2026-09-30", "2026-09-30T17:00:00", 123):
            with self.subTest(timestamp=timestamp):
                row = self.normalize(quote_input(quote_timestamp=timestamp)).canonical_data[0]
                self.assertEqual(row["freshness"], "UNKNOWN")
                self.assertIn("QUOTE_TIMESTAMP_INVALID", row["freshness_check"]["reason_codes"])
                self.assertEqual(row["freshness_check"]["quote_timestamp"], timestamp)
        for request in (None, "bad", "2026-09-30T17:00:00"):
            with self.subTest(request=request):
                row = self.normalize(replace(quote_input(), request_timestamp=request)).canonical_data[0]
                self.assertEqual(row["freshness"], "UNKNOWN")
        for declared in ("INVALID", [], {}):
            with self.subTest(declared=declared):
                self.assert_state(quote_input(freshness=declared), "UNKNOWN", "INVALID_FRESHNESS_DECLARATION")

    def test_restricted_declarations_are_never_upgraded(self):
        self.assert_state(quote_input(), "REALTIME")
        for state in ("UNKNOWN", "STALE"):
            with self.subTest(state=state):
                self.assert_state(quote_input(freshness=state), state, "DECLARED_" + state)

    def test_builder_rechecks_acquisition_freshness_at_analysis_cutoff(self):
        envelope = quote_input(quote_timestamp="2026-09-30T16:55:50Z",
                               disclosure_time="2026-09-30T16:55:50Z")
        result = self.normalize(envelope, acquired_at="2026-09-30T17:00:01Z")
        before = deepcopy(result)
        row = result.canonical_data[0]
        self.assertEqual(row["freshness"], "REALTIME")
        self.assertEqual(row["freshness_check"]["age_seconds"], 251)
        cutoff = "2026-09-30T17:01:00Z"
        evidence = quote_evidence(envelope, result)
        evidence_before = replace(evidence)
        package, _ = package_for(envelope, result, evidence=evidence, judgment_cutoff=cutoff)
        self.assertEqual(package.data_quality["REALTIME_QUOTE_STATUS"], "MISSING")
        assessment = package.latest_quote["records"][0]["freshness_at_cutoff"]
        control = self.normalize(envelope, acquired_at="2026-09-30T17:00:01Z",
                                 judgment_cutoff=cutoff).canonical_data[0]
        self.assertEqual(assessment["freshness"], control["freshness"])
        self.assertEqual(assessment["freshness"], "STALE")
        self.assertEqual(assessment["freshness_check"]["age_seconds"], 310)
        self.assertEqual(assessment["freshness_check"]["reference_time"], cutoff)
        self.assertIn("STALE_QUOTE_TIMESTAMP", assessment["freshness_check"]["reason_codes"])
        self.assertEqual(package.latest_quote["status"], "WARNING")
        inputs = PromptBuilder().inputs(package)
        self.assertEqual(inputs["data_quality_report"]["REALTIME_QUOTE_STATUS"], "MISSING")
        self.assertIn("market_quotes.0", inputs["data_quality_report"]["stale_fields"])
        self.assertTrue(any("STALE_QUOTE_TIMESTAMP" in warning for warning in
                            inputs["data_quality_report"]["validation_warnings"]))
        # Collection facts remain exactly as sealed; analysis assessment is separate.
        self.assertEqual(inputs["canonical_market_data"]["market_quotes"]["records"][0]
                         ["data"]["freshness_check"], json.loads(json.dumps(row["freshness_check"])))
        self.assertEqual(result, before)
        self.assertEqual(evidence, evidence_before)
        self.assertEqual(evidence.normalized_data_hash, sha256_digest(result.canonical_data))
        current, same_evidence = package_for(envelope, result)
        self.assertEqual(current.data_quality["REALTIME_QUOTE_STATUS"], "PASS")
        self.assertEqual(same_evidence, evidence)

    def test_builder_cutoff_freshness_boundary_and_timezone_controls(self):
        envelope = quote_input(quote_timestamp="2026-09-30T16:55:50Z",
                               disclosure_time="2026-09-30T16:55:50Z")
        result = self.normalize(envelope, acquired_at="2026-09-30T17:00:01Z")
        for cutoff, age, state in (
                ("2026-09-30T17:00:49Z", 299, "REALTIME"),
                ("2026-09-30T17:00:50Z", 300, "REALTIME"),
                ("2026-10-01T01:00:50+08:00", 300, "REALTIME"),
                ("2026-09-30T17:00:50.000001Z", 300.000001, "STALE")):
            with self.subTest(cutoff=cutoff):
                package, _ = package_for(envelope, result, judgment_cutoff=cutoff)
                assessment = package.latest_quote["records"][0]["freshness_at_cutoff"]
                self.assertEqual(assessment["freshness"], state)
                self.assertEqual(assessment["freshness_check"]["age_seconds"], age)
                self.assertEqual(package.data_quality["REALTIME_QUOTE_STATUS"],
                                 "PASS" if state == "REALTIME" else "MISSING")

    def test_builder_without_cutoff_keeps_collection_freshness(self):
        result = self.normalize(acquired_at="2026-09-30T17:00:01Z")
        package, _ = package_for(quote_input(), result)
        entry = package.latest_quote["records"][0]
        self.assertNotIn("freshness_at_cutoff", entry)
        self.assertEqual(entry["data"]["freshness_check"]["reference_time"],
                         "2026-09-30T17:00:01Z")
        self.assertEqual(package.data_quality["REALTIME_QUOTE_STATUS"], "PASS")

    def test_builder_rechecks_with_cutoff_inferred_from_other_record(self):
        envelope = quote_input(quote_timestamp="2026-09-30T16:55:50Z",
                               disclosure_time="2026-09-30T16:55:50Z")
        result = self.normalize(envelope, acquired_at="2026-09-30T17:00:01Z")
        arguments = package_arguments(quote=True)
        arguments["canonical_data"]["market_quotes"] = result.canonical_data
        times = deepcopy(result.canonical_data[0]["knowability"])
        times["data_period"]["value"] = "2026-09-30"
        times["judgment_cutoff"].update(value="2026-09-30T17:01:00Z", status="VERIFIED")
        arguments["canonical_data"]["daily_kline"][0]["knowability"] = times
        package = AnalysisPackageBuilder().build(**arguments)
        self.assertEqual(package.data_quality["REALTIME_QUOTE_STATUS"], "MISSING")
        self.assertEqual(package.latest_quote["records"][0]["freshness_at_cutoff"]
                         ["freshness_check"]["age_seconds"], 310)

    def test_builder_rejects_missing_or_conflicting_freshness_basis_at_cutoff(self):
        envelope = quote_input(disclosure_time=FRESH)
        result = self.normalize(envelope, acquired_at="2026-09-30T17:00:01Z")
        for field, value in ((None, None), ("freshness_check", None), ("freshness_check", []),
                             ("freshness_check", {}), ("quote_timestamp", REQUEST),
                             ("request_timestamp", "2026-09-30T16:59:59Z"),
                             ("acquired_at", "2026-09-30T17:00:02Z"),
                             ("judgment_cutoff", "2026-09-30T17:00:02Z"),
                             ("age_seconds", 0), ("stale_after_seconds", 600),
                             ("contract_version", "UNSUPPORTED")):
            with self.subTest(field=field):
                altered = deepcopy(result)
                row = altered.canonical_data[0]
                if field is None:
                    del row["freshness_check"]
                elif field == "freshness_check":
                    row[field] = value
                else:
                    row["freshness_check"][field] = value
                with self.assertRaises(AnalysisInputError):
                    package_for(envelope, altered, judgment_cutoff="2026-09-30T17:01:00Z")

    def test_builder_rejects_evidence_request_clock_conflict_at_cutoff(self):
        envelope = quote_input(disclosure_time=FRESH)
        result = self.normalize(envelope, acquired_at="2026-09-30T17:00:01Z")
        evidence = replace(quote_evidence(envelope, result),
                           request_timestamp="2026-09-30T16:59:59Z")
        with self.assertRaises(AnalysisInputError):
            package_for(envelope, result, evidence=evidence, judgment_cutoff="2026-09-30T17:01:00Z")

    def test_builder_request_context_restriction_is_recomputed(self):
        envelope = quote_input(disclosure_time=FRESH)
        result = self.normalize(envelope, acquired_at="2026-09-30T17:00:01Z")
        package, _ = package_for(envelope, result, judgment_cutoff="2026-09-30T17:05:01Z")
        assessment = package.latest_quote["records"][0]["freshness_at_cutoff"]
        self.assertEqual(assessment["freshness"], "UNKNOWN")
        self.assertIn("REQUEST_TIME_MISMATCH", assessment["freshness_check"]["reason_codes"])
        self.assertEqual(package.data_quality["REALTIME_QUOTE_STATUS"], "MISSING")

    def test_builder_does_not_upgrade_collection_restrictions(self):
        for changes, acquired in (({"freshness": "STALE"}, "2026-09-30T17:00:01Z"),
                                  ({"freshness": "UNKNOWN"}, "2026-09-30T17:00:01Z"),
                                  ({"quote_timestamp": "2026-09-30T17:00:01Z"}, REQUEST)):
            with self.subTest(changes=changes):
                envelope = quote_input(disclosure_time=FRESH, **changes)
                result = self.normalize(envelope, acquired_at=acquired)
                package, _ = package_for(envelope, result, judgment_cutoff="2026-09-30T17:01:00Z")
                self.assertEqual(package.data_quality["REALTIME_QUOTE_STATUS"], "MISSING")
                self.assertNotEqual(package.latest_quote["records"][0]
                                    ["freshness_at_cutoff"]["freshness"], "REALTIME")


if __name__ == "__main__":
    unittest.main()
