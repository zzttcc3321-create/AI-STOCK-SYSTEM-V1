"""Synthetic holding snapshots crossing the historical judgment boundary."""

from copy import deepcopy
import json
import unittest
from unittest.mock import patch

from analysis import (AnalysisOutputError, AnalysisPackageBuilder, AnalysisRunner,
                      PromptBuilder, SevenFieldValidator)
from evidence.contracts import TimeFact
from evidence.knowability import time_facts
from normalizer.semantics import metadata as semantics_metadata
from test_analysis_output import deterministic_response
from test_analysis_runner import FakeModelExecutor


CUTOFF = "2030-01-02T12:00:00Z"


def holding_arguments(snapshot_time):
    stock_id = "SYNTHETIC.SH"
    acquired = "2030-01-01T10:00:00Z"
    daily = {
        "stock_id": stock_id, "trade_date": "2030-01-01", "open": 12.0,
        "high": 13.0, "low": 11.0, "close": 12.5, "volume": 1000.0,
        "amount": 12500.0, "adjust_type": "UNADJUSTED", "adjust_status": "VERIFIED",
        "provider": "TUSHARE_MCP", "provider_timestamp": None,
        "data_quality_status": "PASS",
        "knowability": time_facts(period="2030-01-01", period_basis="canonical.trade_date",
            disclosure=TimeFact("2030-01-01T09:00:00Z", "synthetic.disclosure", "VERIFIED"),
            acquired_at=acquired, judgment_cutoff=CUTOFF),
    }
    daily.update(semantics_metadata(daily, "TUSHARE_MCP", "daily_kline"))
    return {
        "stock_identity": {"stock_id": stock_id},
        "canonical_data": {"daily_kline": [daily]},
        "evidence_metadata": [{"evidence_id": "EV-SYNTHETIC-DAILY", "source": "TUSHARE_MCP",
            "request_timestamp": acquired, "provider_timestamp": None,
            "validation_result": {"status": "PASS", "failures": []}, "human_confirm": "PENDING"}],
        "evidence_links": {"stock_identity": ["EV-SYNTHETIC-DAILY"],
                           "daily_kline.0": ["EV-SYNTHETIC-DAILY"]},
        "source_status": {"TUSHARE_MCP": "PASS"},
        "holding_snapshot": {"stock_id": stock_id, "quantity": 7.0,
                             "cost_price": 12.0, "snapshot_time": snapshot_time},
        "judgment_cutoff": CUTOFF,
    }


class HoldingCutoffTests(unittest.TestCase):
    def test_excluded_snapshot_is_an_explicit_output_failure(self):
        arguments = holding_arguments("2030-01-02T12:00:01Z")
        package = AnalysisPackageBuilder().build(**arguments)
        self.assertEqual(package.position_mode, "HOLDING_ANALYSIS")
        self.assertEqual(package.holding_snapshot["records"], ())
        self.assertEqual(package.holding_snapshot["status"], "UNVERIFIED")
        self.assertEqual(package.holding_snapshot["reason"], "Snapshot after judgment cutoff")
        self.assertEqual(package.data_quality["package_status"], "INCOMPLETE")
        self.assertIn("holding_snapshot", package.data_quality["missing_fields"])

        # Model output references the holding input without inventing a holding
        # fact. This reaches the clock check instead of failing fact resolution.
        watch_arguments = deepcopy(arguments)
        watch_arguments.pop("holding_snapshot")
        watch = AnalysisPackageBuilder().build(**watch_arguments)
        response = deterministic_response(PromptBuilder().build(watch))
        position = response["analysis"]["position_status"]
        market = response["analysis"]["market_view"]
        position["input_refs"] = ["holding_position"]
        position["evidence_ids"] = deepcopy(market["evidence_ids"])
        position["time_range"] = deepcopy(market["time_range"])
        position["content"]["facts"] = deepcopy(market["content"]["facts"])
        position["content"]["statement"] = "持仓事实来自 portfolio_positions，行情判断引用 Evidence"

        envelope = PromptBuilder().build(package)
        with self.assertRaisesRegex(AnalysisOutputError, "holding reference has no retained snapshot"):
            SevenFieldValidator().validate(json.dumps(response, ensure_ascii=False), envelope)

        executor = FakeModelExecutor(response)
        with patch("analysis.runner.AnalysisRunEvidence.from_draft") as build:
            result = AnalysisRunner(executor).run(package)
        self.assertEqual((result.status, result.error_code), ("OUTPUT_INVALID", "OUTPUT_INVALID"))
        self.assertEqual(result.MODEL_CALL_COUNT, 1)
        self.assertEqual(len(executor.calls), 1)
        self.assertEqual(result.prompt_envelope, envelope)
        self.assertIsNone(result.draft)
        self.assertIsNone(result.evidence)
        build.assert_not_called()

    def test_snapshot_before_or_at_cutoff_remains_usable(self):
        for snapshot in ("2030-01-02T11:59:59Z", CUTOFF, "2030-01-02T20:00:00+08:00"):
            with self.subTest(snapshot=snapshot):
                package = AnalysisPackageBuilder().build(**holding_arguments(snapshot))
                self.assertEqual(len(package.holding_snapshot["records"]), 1)
                result = AnalysisRunner(FakeModelExecutor()).run(package)
                self.assertEqual(result.status, "UNCONFIRMED")
                position = result.draft.output["position_status"]
                self.assertEqual(position["time_range"]["end"], snapshot)
                self.assertEqual(position["input_refs"], ("holding_position",))
