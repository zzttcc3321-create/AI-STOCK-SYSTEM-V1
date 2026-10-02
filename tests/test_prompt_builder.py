"""Frozen-template rendering, deterministic hashing and zero side effects."""

import ast
from contextlib import ExitStack
from dataclasses import replace
import hashlib
import json
from pathlib import Path
import re
import unittest
from unittest.mock import patch

from analysis import AnalysisInputError, AnalysisPackage, AnalysisPackageBuilder, PromptBuilder
from analysis.contracts import stable_json
from analysis.prompt_builder import PROMPT_VERSION, TEMPLATE_PATH, TEMPLATE_SHA256
from test_analysis_package import metadata, package_arguments


class PromptBuilderTests(unittest.TestCase):
    def setUp(self):
        self.builder = PromptBuilder()
        self.package = AnalysisPackageBuilder().build(**package_arguments())

    def test_exact_seven_inputs_from_one_package(self):
        inputs = self.builder.inputs(self.package)
        self.assertEqual(set(inputs), {"stock_id", "holding_position", "canonical_market_data",
                                     "financial_data", "intelligence_data", "evidence_index",
                                     "data_quality_report"})
        self.assertEqual(inputs["canonical_market_data"]["daily_kline"],
                         self.package.to_dict()["daily_kline_summary"])
        self.assertEqual(inputs["data_quality_report"], self.package.to_dict()["data_quality"])
        self.assertIsNone(inputs["holding_position"])

    def test_watch_only_without_fabricated_holding(self):
        envelope = self.builder.build(self.package)
        metadata, encoded, instruction = envelope.user_prompt.split("\n", 2)
        self.assertEqual(json.loads(metadata)["position_mode"], "WATCH_ONLY")
        self.assertEqual(json.loads(metadata)["analysis_mode"], "WATCH_ANALYSIS")
        self.assertIsNone(json.loads(encoded)["holding_position"])
        self.assertIn('position_status` 输出"无持仓（观察模式）"', envelope.system_prompt)
        self.assertEqual(envelope.analysis_mode, "WATCH_ANALYSIS")
        self.assertEqual(envelope.position_mode, "WATCH_ONLY")
        self.assertNotIn("持仓数量", envelope.user_prompt)
        self.assertNotIn("model_version", envelope.user_prompt)
        self.assertNotIn("task_date", envelope.user_prompt)

    def test_holding_branch_preserves_actual_snapshot(self):
        package = AnalysisPackageBuilder().build(**package_arguments(holding=True))
        envelope = self.builder.build(package)
        self.assertEqual(envelope.position_mode, "HOLDING_ANALYSIS")
        self.assertEqual(envelope.analysis_mode, "HOLDING_ANALYSIS")
        row = self.builder.inputs(package)["holding_position"]["records"][0]["data"]
        self.assertEqual(row["quantity"], 100)
        self.assertIsNone(row["profit_loss"])

    def test_missing_holding_evidence_does_not_become_watch(self):
        arguments = package_arguments(holding=True)
        package = AnalysisPackageBuilder().build(**arguments)
        envelope = self.builder.build(package)
        self.assertEqual(envelope.position_mode, "HOLDING_ANALYSIS")
        self.assertEqual(self.builder.inputs(package)["holding_position"]["records"][0]["data"],
                         arguments["holding_snapshot"])
        self.assertNotIn("holding_snapshot.evidence", envelope.user_prompt)

    def test_holding_enters_prompt_without_any_provider_evidence(self):
        arguments = package_arguments(holding=True)
        arguments["evidence_metadata"] = []
        package = AnalysisPackageBuilder().build(**arguments)
        envelope = self.builder.build(package)
        inputs = json.loads(envelope.user_prompt.split("\n", 2)[1])
        self.assertEqual(inputs["holding_position"]["records"][0]["data"], arguments["holding_snapshot"])
        self.assertEqual(envelope.analysis_mode, "HOLDING_ANALYSIS")
        self.assertEqual(inputs["evidence_index"], [])
        self.assertEqual(envelope.evidence_ids, ())
        self.assertEqual(inputs["canonical_market_data"]["daily_kline"]["records"], [])
        self.assertNotIn("holding_snapshot.evidence", inputs["data_quality_report"]["missing_fields"])

    def test_holding_without_provider_evidence_keeps_market_evidence_gate(self):
        arguments = package_arguments(holding=True, quote=True)
        for missing_market_evidence in (False, True):
            if missing_market_evidence:
                arguments["evidence_links"].pop("daily_kline.0")
                arguments["evidence_links"].pop("market_quotes.0")
            package = AnalysisPackageBuilder().build(**arguments)
            envelope = self.builder.build(package)
            inputs = self.builder.inputs(package)
            with self.subTest(missing_market_evidence=missing_market_evidence):
                self.assertEqual(envelope.position_mode, "HOLDING_ANALYSIS")
                self.assertEqual(envelope.analysis_mode, "HOLDING_ANALYSIS")
                self.assertEqual(inputs["holding_position"]["records"][0]["data"], arguments["holding_snapshot"])
                for kind, identifier in (("daily_kline", "EV-DAILY"), ("market_quotes", "EV-QUOTE")):
                    records = inputs["canonical_market_data"][kind]["records"]
                    if missing_market_evidence:
                        self.assertEqual(records, [])
                        self.assertIn(f"{kind}.0.evidence", inputs["data_quality_report"]["missing_fields"])
                    else:
                        self.assertEqual(records[0]["evidence_ids"], [identifier])
                        self.assertIn(identifier, envelope.evidence_ids)

    def test_prompt_distinguishes_portfolio_input_from_market_evidence_sources(self):
        arguments = package_arguments(holding=True, quote=True)
        arguments["evidence_metadata"].append(metadata("EV-UNUSED", "WORKBUDDY_TDX_MCP"))
        envelope = self.builder.build(AnalysisPackageBuilder().build(**arguments))
        inputs = json.loads(envelope.user_prompt.split("\n", 2)[1])
        holding = inputs["holding_position"]["records"][0]
        self.assertEqual(set(holding), {"data", "input_source"})
        self.assertEqual(holding["input_source"], "portfolio_positions")
        self.assertEqual(holding["data"], arguments["holding_snapshot"])
        self.assertEqual({item["source"] for item in inputs["evidence_index"]},
                         {"TUSHARE_MCP", "TDX_OFFICIAL_LOCAL_HTTP"})
        self.assertEqual(envelope.evidence_ids, ("EV-DAILY", "EV-QUOTE"))
        for source in ("TUSHARE_MCP", "TDX_OFFICIAL_LOCAL_HTTP", "WORKBUDDY_TDX_MCP"):
            self.assertNotIn(source, json.dumps(inputs["holding_position"]))

    def test_realtime_missing_with_explicit_frozen_prohibition(self):
        envelope = self.builder.build(self.package)
        self.assertIn('"REALTIME_QUOTE_STATUS":"MISSING"', envelope.user_prompt)
        self.assertIn("不得称为实时结论。", envelope.system_prompt)
        self.assertIn('禁止"根据历史推测当前价格"。', envelope.system_prompt)
        self.assertIn("所有涉及\"当前价\"的判断改用最近日K收盘价并标注日期", envelope.system_prompt)
        self.assertEqual(self.builder.inputs(self.package)["canonical_market_data"]["market_quotes"]["records"], [])

    def test_available_realtime_retains_evidenced_quote(self):
        package = AnalysisPackageBuilder().build(**package_arguments(quote=True))
        inputs = self.builder.inputs(package)
        self.assertEqual(inputs["data_quality_report"]["REALTIME_QUOTE_STATUS"], "PASS")
        self.assertEqual(inputs["canonical_market_data"]["market_quotes"]["records"][0]["evidence_ids"], ["EV-QUOTE"])

    def test_financial_intelligence_missing_without_raw_provider_completion(self):
        inputs = self.builder.inputs(self.package)
        self.assertEqual(inputs["financial_data"]["status"], "MISSING")
        self.assertEqual(inputs["financial_data"]["records"], [])
        for block in inputs["intelligence_data"].values():
            self.assertEqual(block["status"], "MISSING")
            self.assertEqual(block["records"], [])
        arguments = package_arguments()
        arguments["information_availability"] = {"research_summary": {
            "status": "NOT_AVAILABLE", "reason": "Official reports absent"}}
        package = AnalysisPackageBuilder().build(**arguments)
        self.assertEqual(self.builder.inputs(package)["intelligence_data"]["research_summary"]["status"], "NOT_AVAILABLE")

    def test_missing_evidence_fail_closed_in_prompt(self):
        arguments = package_arguments()
        arguments["evidence_metadata"] = []
        package = AnalysisPackageBuilder().build(**arguments)
        envelope = self.builder.build(package)
        self.assertEqual(envelope.evidence_ids, ())
        inputs = self.builder.inputs(package)
        self.assertEqual(inputs["data_quality_report"]["package_status"], "INCOMPLETE")
        self.assertFalse(inputs["data_quality_report"]["complete_analysis_input"])
        self.assertEqual(inputs["canonical_market_data"]["daily_kline"]["records"], [])
        self.assertNotIn("示意证券", envelope.user_prompt)

    def test_partial_evidence_preserves_retained_record_after_exclusion(self):
        arguments = package_arguments(quote=True)
        arguments["canonical_data"]["daily_kline"].insert(0, dict(arguments["canonical_data"]["daily_kline"][0], trade_date="2026-09-29"))
        arguments["evidence_links"]["daily_kline.1"] = arguments["evidence_links"].pop("daily_kline.0")
        package = AnalysisPackageBuilder().build(**arguments)
        envelope = self.builder.build(package)
        rows = self.builder.inputs(package)["canonical_market_data"]["daily_kline"]["records"]
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["data"]["trade_date"], "2026-09-30")
        self.assertIn("daily_kline.0.evidence", envelope.user_prompt)

    def test_unverified_and_warning_explicit_in_prompt(self):
        arguments = package_arguments()
        row = arguments["canonical_data"]["daily_kline"][0]
        row.update(adjust_type=None, adjust_status="UNVERIFIED", data_quality_status="UNVERIFIED")
        arguments["source_status"]["TUSHARE_MCP"] = "WARNING"
        envelope = self.builder.build(AnalysisPackageBuilder().build(**arguments))
        self.assertIn('"data_quality_status":"UNVERIFIED"', envelope.user_prompt)
        self.assertIn('"TUSHARE_MCP":"WARNING"', envelope.user_prompt)
        self.assertIn("当前数据口径未完成验证（UNVERIFIED：Provider 未声明或未经验证）", envelope.system_prompt)
        self.assertIn("跨口径比较已禁用", envelope.system_prompt)

    def test_evidence_six_fields_and_human_state_preserved(self):
        inputs = self.builder.inputs(self.package)
        self.assertEqual(set(inputs["evidence_index"][0]), {"evidence_id", "source", "request_timestamp",
                         "provider_timestamp", "validation_result", "human_confirm"})
        self.assertEqual(inputs["evidence_index"][0]["human_confirm"], "PENDING")
        self.assertEqual(self.builder.build(self.package).evidence_ids, ("EV-DAILY",))

    def test_business_rules_are_verbatim_from_frozen_template(self):
        frozen = TEMPLATE_PATH.read_text(encoding="utf-8")
        system_example = frozen.split("### 6.1 System Prompt（系统提示词）\n", 1)[1]
        system_example = re.search(r"```text\n(.*?)\n```", system_example, re.DOTALL).group(1)
        envelope = self.builder.build(self.package)
        self.assertTrue(envelope.system_prompt.startswith(system_example))
        remaining = envelope.system_prompt[len(system_example):]
        # Every appended rule line is read from the freeze, not rewritten.
        for line in remaining.splitlines():
            if line.strip():
                self.assertIn(line, frozen)
        instruction = envelope.user_prompt.split("\n\n", 1)[1]
        self.assertIn(instruction, frozen)
        for required in ("Level 1（证据充分）", "Level 2（证据有限）", "Level 3（证据不足）",
                         "第一段：AI 建议", "第二段：人工确认项", "第三段：最终状态",
                         "PARTIAL_REFETCH=MANUAL_TRIGGER_ONLY", "TRADING_WRITE=FORBIDDEN"):
            self.assertIn(required, envelope.system_prompt)
        self.assertNotIn("STOCK_EXAMPLE_A", envelope.user_prompt)

    def test_template_pin_matches_actual_frozen_bytes_and_version(self):
        envelope = self.builder.build(self.package)
        self.assertEqual(envelope.template_hash, hashlib.sha256(TEMPLATE_PATH.read_bytes()).hexdigest())
        self.assertEqual(envelope.template_hash, TEMPLATE_SHA256)
        self.assertEqual(envelope.prompt_version, PROMPT_VERSION)
        self.assertEqual(envelope.prompt_version, "PROMPT_TEMPLATE_V1.0")

    def test_changed_frozen_template_rejected_without_changing_file(self):
        with patch("analysis.prompt_builder.TEMPLATE_SHA256", "0" * 64):
            with self.assertRaises(AnalysisInputError):
                self.builder.build(self.package)

    def test_identical_inputs_give_identical_serialization_and_hash(self):
        repeated = AnalysisPackageBuilder().build(**package_arguments())
        first, second = self.builder.build(self.package), self.builder.build(repeated)
        self.assertEqual(first.to_json(), second.to_json())
        self.assertEqual(first.input_hash, second.input_hash)
        rendered = first.to_dict()
        for field in ("input_hash", "NETWORK_CALL_COUNT", "MODEL_CALL_COUNT", "DATABASE_WRITE"):
            rendered.pop(field)
        self.assertEqual(first.input_hash, hashlib.sha256(stable_json(rendered).encode("utf-8")).hexdigest())

    def test_hash_changes_for_data_evidence_quality_and_position(self):
        baseline = self.builder.build(self.package).input_hash
        for choice in ("data", "evidence", "quality", "holding", "quote"):
            arguments = package_arguments(holding=choice == "holding", quote=choice == "quote")
            if choice == "data":
                arguments["canonical_data"]["daily_kline"][0]["close"] = 10.6
            elif choice == "evidence":
                arguments["evidence_metadata"][0]["request_timestamp"] = "2026-09-30T17:01:00Z"
            elif choice == "quality":
                arguments["validation_warnings"] = ["Manual review requested"]
            package = AnalysisPackageBuilder().build(**arguments)
            with self.subTest(choice=choice):
                self.assertNotEqual(self.builder.build(package).input_hash, baseline)

    def test_mapping_and_evidence_order_do_not_change_hash(self):
        arguments = package_arguments(holding=True, quote=True)
        first = self.builder.build(AnalysisPackageBuilder().build(**arguments))
        arguments["evidence_metadata"].reverse()
        arguments["canonical_data"] = dict(reversed(list(arguments["canonical_data"].items())))
        arguments["validation_warnings"] = ["Review B", "Review A", "Review A"]
        second = self.builder.build(AnalysisPackageBuilder().build(**arguments))
        arguments["validation_warnings"] = ["Review A", "Review B"]
        third = self.builder.build(AnalysisPackageBuilder().build(**arguments))
        self.assertEqual(second.input_hash, third.input_hash)
        arguments.pop("validation_warnings")
        self.assertEqual(first.input_hash, self.builder.build(AnalysisPackageBuilder().build(**arguments)).input_hash)

    def test_prompt_has_no_raw_data_or_credentials(self):
        envelope = self.builder.build(self.package)
        rendered = envelope.system_prompt + envelope.user_prompt
        for forbidden in ("raw_response", "raw_bytes", "credential", "API Key", "Cookie", "Session"):
            with self.subTest(forbidden=forbidden):
                self.assertNotIn(forbidden.lower(), rendered.lower())

    def test_zero_network_model_database_or_file_writes_observed(self):
        with ExitStack() as stack:
            mocks = [stack.enter_context(patch(name, side_effect=AssertionError("forbidden side effect"))) for name in (
                "socket.create_connection", "socket.socket.connect", "urllib.request.urlopen",
                "sqlite3.connect", "pathlib.Path.write_bytes", "pathlib.Path.write_text",
                "providers.tushare.TushareMCPProvider.fetch",
                "providers.tdx_realtime.TdxLocalHttpProvider.fetch",
                "providers.tdx_information.TdxInformationProvider.fetch",
            )]
            package = AnalysisPackageBuilder().build(**package_arguments())
            envelope = self.builder.build(package)
            self.assertEqual(envelope.NETWORK_CALL_COUNT, 0)
            self.assertEqual(envelope.MODEL_CALL_COUNT, 0)
            self.assertEqual(envelope.DATABASE_WRITE, 0)
            for mocked in mocks:
                mocked.assert_not_called()
        # Closed import set establishes there is no model/client dependency.
        for filename in ("contracts.py", "package_builder.py", "prompt_builder.py"):
            tree = ast.parse((TEMPLATE_PATH.parents[2] / "analysis" / filename).read_text())
            permitted = {"__future__", "json", "math", "re", "dataclasses", "enum", "types",
                         "typing", "datetime", "hashlib", "pathlib", "evidence", "normalizer"}
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    for name in node.names:
                        self.assertIn(name.name.split(".")[0], permitted)
                elif isinstance(node, ast.ImportFrom) and node.level == 0:
                    self.assertIn(node.module.split(".")[0], permitted)

    def test_gate_required_and_modified_or_hand_constructed_package_rejected(self):
        changed = replace(self.package, position_mode="HOLDING_ANALYSIS")
        recreated = AnalysisPackage(**self.package.to_dict())
        for value in ({"canonical_data": []}, changed, recreated):
            with self.subTest(kind=type(value).__name__), self.assertRaises(AnalysisInputError):
                self.builder.build(value)

    def test_detects_forced_mutation_after_gate(self):
        package = AnalysisPackageBuilder().build(**package_arguments())
        object.__setattr__(package, "stock_id", "OTHER.SH")
        with self.assertRaises(AnalysisInputError):
            self.builder.build(package)

    def test_mapping_result_is_detached_from_validated_package(self):
        before = self.builder.build(self.package).input_hash
        inputs = self.builder.inputs(self.package)
        inputs["canonical_market_data"]["daily_kline"]["records"].clear()
        self.assertEqual(before, self.builder.build(self.package).input_hash)


if __name__ == "__main__":
    unittest.main()
