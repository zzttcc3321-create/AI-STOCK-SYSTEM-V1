"""R1-FINAL-03: retained business records, not audit links, allow citations."""

from copy import deepcopy
from dataclasses import replace
import json
import unittest

from analysis import (AnalysisOutputError, AnalysisPackageBuilder, PromptBuilder,
                      SevenFieldValidator)
from analysis.contracts import safe_json, stable_json
from analysis.output_contracts import digest
from evidence import EvidenceBuilder, sha256_digest
from normalizer import DataNormalizer
from test_analysis_output import deterministic_response
from test_analysis_package import STOCK
from test_freshness import quote_input
from test_normalizer import daily_bar, envelope, UNITS


ACQUIRED = "2026-09-30T17:00:01Z"
CUTOFF = "2026-09-30T17:01:00Z"


def normalized_arguments(basis="QFQ", *, quote_time="2026-09-30T16:59:59Z"):
    """Synthetic provider envelopes pass through the production normalizer."""
    daily_raw = envelope(payload={"bars": [daily_bar(
        adjust_type=basis, adjust_status="VERIFIED",
        disclosure_time="2026-09-30T15:00:00+08:00")]})
    quote_raw = quote_input(quote_timestamp=quote_time, disclosure_time=quote_time)
    normalizer = DataNormalizer()
    daily = normalizer.normalize(daily_raw, units=UNITS, acquired_at=ACQUIRED)
    quote = normalizer.normalize(quote_raw, units={"price": "元"}, acquired_at=ACQUIRED)
    evidence = [EvidenceBuilder().build(
        fetch_result=raw, endpoint_or_tool=tool, request_params={"stock_id": STOCK},
        collection_task_id="task", system_run_id="run", normalization_result=result,
        evidence_id=identifier)
        for raw, result, tool, identifier in (
            (replace(daily_raw, raw_reference="run/task/request/daily.json"),
             daily, "offline_daily", "EV-DAILY"),
            (quote_raw, quote, "offline_quote", "EV-QUOTE"))]
    return {
        "stock_identity": {"stock_id": STOCK},
        "canonical_data": {"daily_kline": list(daily.canonical_data),
                           "market_quotes": list(quote.canonical_data)},
        "evidence_metadata": evidence,
        "evidence_links": {"stock_identity": ["EV-DAILY"],
                           "daily_kline.0": ["EV-DAILY"],
                           "market_quotes.0": ["EV-QUOTE"]},
        "source_status": {"TUSHARE_MCP": "PASS", "TDX_OFFICIAL_LOCAL_HTTP": "PASS",
                          "WORKBUDDY_TDX_MCP": "MISSING"},
    }, (daily, quote)


class EvidenceWhitelistTests(unittest.TestCase):
    def build(self, arguments):
        package = AnalysisPackageBuilder().build(**arguments)
        return package, PromptBuilder().build(package)

    def response(self, prompt, identifiers):
        response = deterministic_response(prompt)
        confidence = response["analysis"]["confidence"]
        confidence["evidence_ids"] = identifiers
        inputs = json.loads(prompt.user_prompt.split("\n", 2)[1])
        quotes = inputs["canonical_market_data"]["market_quotes"]["records"]
        if quotes and set(identifiers).intersection(quotes[0]["evidence_ids"]):
            confidence["time_range"]["end"] = quotes[0]["data"]["quote_timestamp"]
        return json.dumps(response, ensure_ascii=False)

    def assert_rejected_quote(self, prompt):
        with self.assertRaises(AnalysisOutputError) as caught:
            SevenFieldValidator().validate(self.response(prompt, ["EV-DAILY", "EV-QUOTE"]), prompt)
        self.assertRegex(str(caught.exception), "unknown or out-of-prompt evidence_id")
        self.assertIn("EV-QUOTE", str(caught.exception))
        self.assertIn("no retained record support", str(caught.exception))

    def test_qfq_excluded_quote_is_absent_from_prompt_whitelist(self):
        arguments, _ = normalized_arguments()
        package, prompt = self.build(arguments)
        self.assertEqual(package.latest_quote["records"], ())
        self.assertEqual(len(package.daily_kline_summary["records"]), 1)
        self.assertEqual(set(prompt.evidence_ids), {"EV-DAILY"})
        inputs = json.loads(prompt.user_prompt.split("\n", 2)[1])
        self.assertEqual({item["evidence_id"] for item in inputs["evidence_index"]}, {"EV-DAILY"})

    def test_qfq_excluded_quote_citation_is_rejected(self):
        arguments, _ = normalized_arguments()
        package, prompt = self.build(arguments)
        self.assertEqual(package.latest_quote["records"], ())
        self.assert_rejected_quote(prompt)

    def test_exclusion_audit_and_original_evidence_are_preserved(self):
        arguments, results = normalized_arguments()
        arguments["validation_warnings"] = ["Existing audit warning"]
        arguments["missing_fields"] = ["existing.audit.gap"]
        before = deepcopy(arguments, {id(item): replace(item) for item in arguments["evidence_metadata"]})
        evidence_before = [replace(item) for item in arguments["evidence_metadata"]]
        raw_hashes = [sha256_digest(result.raw_data.payload) for result in results]
        package, prompt = self.build(arguments)
        quality = package.to_dict()["data_quality"]
        self.assertEqual(quality["excluded_record_quality"], [{
            "path": "market_quotes", "status": "UNVERIFIED",
            "reason": "cross-series adjustment mismatch; snapshot excluded",
            "evidence_ids": ["EV-QUOTE"]}])
        self.assertIn("CROSS_SERIES_ADJUSTMENT_MISMATCH",
                      quality["price_comparability"][1]["reason_codes"])
        self.assertIn("market_quotes: cross-series adjustment mismatch; snapshot excluded",
                      quality["incomplete_reasons"])
        self.assertIn("Existing audit warning", quality["validation_warnings"])
        self.assertIn("existing.audit.gap", quality["missing_fields"])
        draft = SevenFieldValidator().validate(self.response(prompt, ["EV-DAILY"]), prompt)
        self.assertEqual(draft.to_dict()["limitation_context"]["data_quality_report"], quality)
        self.assertEqual(arguments, before)
        self.assertEqual(arguments["evidence_metadata"], evidence_before)
        self.assertEqual([sha256_digest(result.raw_data.payload) for result in results], raw_hashes)
        for evidence, result in zip(evidence_before, results):
            self.assertEqual(evidence.normalized_data_hash, sha256_digest(result.canonical_data))

    def test_unadjusted_control_keeps_both_citations(self):
        arguments, _ = normalized_arguments("UNADJUSTED")
        package, prompt = self.build(arguments)
        self.assertEqual(len(package.latest_quote["records"]), 1)
        self.assertEqual(set(prompt.evidence_ids), {"EV-DAILY", "EV-QUOTE"})
        draft = SevenFieldValidator().validate(self.response(prompt, ["EV-DAILY", "EV-QUOTE"]), prompt)
        self.assertEqual(set(draft.used_evidence_ids), {"EV-DAILY", "EV-QUOTE"})

    def test_shared_evidence_survives_quote_exclusion(self):
        arguments, _ = normalized_arguments()
        # Identity has no provider role restriction; share the quote Evidence
        # with this retained record without crossing the frozen market roles.
        arguments["evidence_links"]["stock_identity"] = ["EV-QUOTE"]
        package, prompt = self.build(arguments)
        self.assertEqual(package.latest_quote["records"], ())
        self.assertEqual(package.stock_identity["records"][0]["evidence_ids"], ("EV-QUOTE",))
        self.assertEqual(set(prompt.evidence_ids), {"EV-DAILY", "EV-QUOTE"})
        draft = SevenFieldValidator().validate(self.response(prompt, ["EV-DAILY", "EV-QUOTE"]), prompt)
        self.assertIn("EV-QUOTE", draft.used_evidence_ids)

    def test_only_retained_daily_evidence_is_accepted(self):
        arguments, _ = normalized_arguments()
        _, prompt = self.build(arguments)
        draft = SevenFieldValidator().validate(self.response(prompt, ["EV-DAILY"]), prompt)
        self.assertEqual(draft.used_evidence_ids, ("EV-DAILY",))

    def test_without_cutoff_preserves_collection_freshness(self):
        arguments, results = normalized_arguments("UNADJUSTED")
        package, prompt = self.build(arguments)
        entry = package.latest_quote["records"][0]
        self.assertNotIn("freshness_at_cutoff", entry)
        self.assertEqual(entry["data"]["freshness"], "REALTIME")
        self.assertEqual(package.to_dict()["latest_quote"]["records"][0]["data"],
                         safe_json(results[1].canonical_data[0]))
        self.assertEqual(package.data_quality["REALTIME_QUOTE_STATUS"], "PASS")
        self.assertEqual(set(prompt.evidence_ids), {"EV-DAILY", "EV-QUOTE"})

    def test_cutoff_freshness_recheck_keeps_retained_stale_evidence(self):
        arguments, _ = normalized_arguments("UNADJUSTED", quote_time="2026-09-30T16:55:50Z")
        arguments["judgment_cutoff"] = CUTOFF
        before = deepcopy(arguments, {id(item): replace(item) for item in arguments["evidence_metadata"]})
        package, prompt = self.build(arguments)
        entry = package.latest_quote["records"][0]
        self.assertEqual(entry["data"]["freshness"], "REALTIME")
        self.assertEqual(entry["freshness_at_cutoff"]["freshness"], "STALE")
        self.assertEqual(entry["freshness_at_cutoff"]["freshness_check"]["age_seconds"], 310)
        self.assertEqual(package.data_quality["REALTIME_QUOTE_STATUS"], "MISSING")
        self.assertEqual(set(prompt.evidence_ids), {"EV-DAILY", "EV-QUOTE"})
        SevenFieldValidator().validate(self.response(prompt, ["EV-DAILY", "EV-QUOTE"]), prompt)
        self.assertEqual(arguments, before)

    def test_no_daily_series_does_not_trigger_cross_series_exclusion(self):
        arguments, _ = normalized_arguments()
        arguments["canonical_data"].pop("daily_kline")
        arguments["evidence_links"].pop("daily_kline.0")
        arguments["evidence_links"].pop("stock_identity")
        package, prompt = self.build(arguments)
        self.assertEqual(len(package.latest_quote["records"]), 1)
        self.assertEqual(set(prompt.evidence_ids), {"EV-QUOTE"})
        draft = SevenFieldValidator().validate(self.response(prompt, ["EV-QUOTE"]), prompt)
        self.assertEqual(draft.used_evidence_ids, ("EV-QUOTE",))

    def test_mixed_series_exclusion_drops_only_exclusive_evidence(self):
        arguments, _ = normalized_arguments("UNADJUSTED")
        second, _ = normalized_arguments("QFQ")
        row = deepcopy(second["canonical_data"]["daily_kline"][0])
        row["trade_date"] = "2026-09-29"
        row["knowability"]["data_period"]["value"] = row["trade_date"]
        arguments["canonical_data"]["daily_kline"].append(row)
        arguments["evidence_links"]["daily_kline.1"] = ["EV-DAILY"]
        arguments["evidence_links"].pop("stock_identity")
        package, prompt = self.build(arguments)
        self.assertEqual(package.daily_kline_summary["records"], ())
        self.assertEqual(package.latest_quote["records"], ())
        self.assertEqual(prompt.evidence_ids, ())
        self.assertEqual({identifier for item in package.data_quality["excluded_record_quality"]
                          for identifier in item["evidence_ids"]}, {"EV-DAILY", "EV-QUOTE"})

    def test_validator_rejects_orphan_even_if_prompt_index_contains_it(self):
        arguments, _ = normalized_arguments()
        package, prompt = self.build(arguments)
        inputs = PromptBuilder().inputs(package)
        # Simulate the old bug's intact envelope with an orphan index entry.
        evidence = arguments["evidence_metadata"][1]
        inputs["evidence_index"] = [item for item in inputs["evidence_index"]
                                    if item["evidence_id"] != "EV-QUOTE"] + [{
            "evidence_id": evidence.evidence_id, "source": evidence.source,
            "request_timestamp": evidence.request_timestamp,
            "provider_timestamp": evidence.provider_timestamp,
            "validation_result": {"status": "PASS", "failures": []},
            "human_confirm": evidence.human_confirm}]
        metadata, _, instruction = prompt.user_prompt.split("\n", 2)
        prompt = replace(prompt, user_prompt=metadata + "\n" + stable_json(inputs) + "\n" + instruction,
                         evidence_ids=("EV-DAILY", "EV-QUOTE"))
        rendered = prompt.to_dict()
        for name in ("input_hash", "NETWORK_CALL_COUNT", "MODEL_CALL_COUNT", "DATABASE_WRITE"):
            rendered.pop(name)
        prompt = replace(prompt, input_hash=digest(rendered))
        self.assert_rejected_quote(prompt)


if __name__ == "__main__":
    unittest.main()
