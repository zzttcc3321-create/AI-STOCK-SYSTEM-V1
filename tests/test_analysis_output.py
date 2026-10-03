"""Output boundary failures, using synthetic already-gated input packages."""

from copy import deepcopy
from dataclasses import FrozenInstanceError, replace
from datetime import datetime, timezone
import hashlib
import json
import unittest

from analysis import (AnalysisOutputError, AnalysisPackageBuilder, OutputParser,
                      PromptBuilder, SevenFieldValidator, output_schema)
from analysis.contracts import stable_json
from analysis.output_contracts import (ANALYSIS_FIELDS, LIMITATION_IMPACT, LIMITATION_REASONS,
                                       REVIEW_ASSUMPTION, digest, validate_draft)
from test_analysis_package import information_package_arguments, package_arguments


def deterministic_response(envelope):
    """Test-only conservative fake: exact facts, LEVEL_3, pending review.

    Works for synthetic packages and the separately supplied real #008 replay.
    It measures orchestration, not model quality or evidence sufficiency.
    """
    inputs = json.loads(envelope.user_prompt.split("\n", 2)[1])
    market = inputs["canonical_market_data"]
    blocks = {f"canonical_market_data.{key}": block for key, block in market.items()}
    blocks["financial_data"] = inputs["financial_data"]
    blocks.update({f"intelligence_data.{key}": block for key, block in inputs["intelligence_data"].items()})
    quality = inputs["data_quality_report"]
    limitations = [{"input_path": key, "status": block["status"]}
                   for key, block in blocks.items() if block["status"] != "PASS"]
    if quality["data_quality_status"] != "PASS":
        limitations.append({"input_path": "data_quality_report", "status": quality["data_quality_status"]})
    limitations.extend({"input_path": f"data_quality_report.source_status.{source}", "status": status}
                       for source, status in quality["source_status"].items() if status in {"WARNING", "UNVERIFIED"})
    for item in limitations:
        block = blocks.get(item["input_path"])
        item["reason"] = block["reason"] if block and block["reason"] else LIMITATION_REASONS[item["status"]]
        item["impact"] = LIMITATION_IMPACT

    def first_fact(paths):
        for path in paths:
            for index, entry in enumerate(blocks[path]["records"]):
                for name in ("close", "price", "report_date", "title"):
                    if entry["data"].get(name) is not None:
                        return {"input_path": f"{path}.records.{index}.data.{name}",
                                "value": entry["data"][name], "evidence_ids": entry["evidence_ids"]}
        return None
    market_fact = first_fact(["canonical_market_data.daily_kline", "canonical_market_data.market_quotes"])
    fact = first_fact(["financial_data", "intelligence_data.recent_news", "intelligence_data.recent_announcements"]) or market_fact

    def times(identifiers, holding=False):
        values = set()
        for block in blocks.values():
            for row in block["records"]:
                if set(identifiers) & set(row["evidence_ids"]):
                    values.update(row["data"][key] for key in ("trade_date", "quote_timestamp", "report_date", "publish_date", "publish_time")
                                  if row["data"].get(key) is not None)
        if holding:
            values.add(inputs["holding_position"]["records"][0]["data"]["snapshot_time"])
        if not values:
            return {"start": None, "end": None}
        def clock(value):
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
            return parsed.replace(tzinfo=timezone.utc) if parsed.tzinfo is None else parsed.astimezone(timezone.utc)
        ordered = sorted(values, key=lambda value: (clock(value), value))
        return {"start": ordered[0], "end": ordered[-1]}

    statements = {
        "market_view": "历史行情分析" if market_fact else "证据不足，趋势不明",
        "position_status": "无持仓（观察模式）" if envelope.position_mode == "WATCH_ONLY" else "持仓事实来自 portfolio_positions，行情判断引用 Evidence",
        "risk_level": "依据证据评估风险" if fact else "证据不足，风险不明",
        "operation_plan": "证据不足，暂不给出操作建议", "t_strategy": "证据不足，暂不给出操作建议",
        "reasoning": "推理仅依据所列输入与证据", "confidence": "数据缺失或口径未验证导致置信度降低",
    }
    output = {}
    for name in ANALYSIS_FIELDS:
        selected = market_fact if name in {"market_view", "position_status"} else fact
        if name == "position_status" and envelope.position_mode == "WATCH_ONLY":
            selected = None
        facts = [deepcopy(selected)] if selected else []
        ids = list(selected["evidence_ids"]) if selected else []
        if name == "position_status" and envelope.position_mode == "HOLDING_ANALYSIS":
            for key in ("quantity", "cost_price"):
                value = inputs["holding_position"]["records"][0]["data"][key]
                if value is not None:
                    facts.append({"input_path": f"holding_position.records.0.data.{key}", "value": value, "evidence_ids": []})
        content = {"statement": statements[name], "facts": facts, "limitations": deepcopy(limitations)}
        if name == "market_view": content["assessment"] = "UNCLEAR"
        if name == "risk_level": content["assessment"] = "UNKNOWN"
        if name == "confidence": content["assessment"] = "LOW"
        if name in {"operation_plan", "t_strategy"}: content["advice"] = {"level": "LEVEL_3"}
        statuses = {item["status"] for item in limitations}
        status = ("MISSING" if not ids or "MISSING" in statuses else "NOT_AVAILABLE" if "NOT_AVAILABLE" in statuses
                  else "UNVERIFIED" if "UNVERIFIED" in statuses else "WARNING" if "WARNING" in statuses else "PASS")
        output[name] = {"content": content, "evidence_ids": ids,
                        "time_range": times(ids, holding=name == "position_status" and envelope.position_mode == "HOLDING_ANALYSIS"),
                        "data_quality_status": status}
        if name == "position_status":
            output[name]["input_refs"] = [] if envelope.position_mode == "WATCH_ONLY" else ["holding_position"]
    review = [{"target": name, "reason": "REVIEW_ADVICE"} for name in ("operation_plan", "t_strategy")]
    review.extend({"target": item["input_path"], "reason": "REVIEW_DATA_QUALITY"} for item in limitations)
    review.append({"target": "reasoning", "reason": "REVIEW_ASSUMPTION", "assumption": REVIEW_ASSUMPTION})
    return {"analysis": output, "human_confirmation_items": review, "final_state": "UNCONFIRMED"}


class AnalysisOutputTests(unittest.TestCase):
    def setUp(self):
        self.package = AnalysisPackageBuilder().build(**package_arguments())
        self.envelope = PromptBuilder().build(self.package)
        self.response = deterministic_response(self.envelope)
        self.validator = SevenFieldValidator()

    def validate(self, response=None, envelope=None):
        return self.validator.validate(json.dumps(response if response is not None else self.response, ensure_ascii=False), envelope or self.envelope)

    def reject(self, response=None, envelope=None):
        with self.assertRaises(AnalysisOutputError):
            self.validate(response, envelope)

    def test_exact_seven_fields_and_three_sections(self):
        draft = self.validate()
        self.assertEqual(set(draft.output), set(ANALYSIS_FIELDS))
        self.assertEqual(set(self.response), {"analysis", "human_confirmation_items", "final_state"})
        self.assertEqual(draft.analysis_status, "UNCONFIRMED")
        self.assertEqual(draft.human_confirm, "PENDING")

    def test_every_missing_analysis_field_rejected(self):
        for name in ANALYSIS_FIELDS:
            output = deepcopy(self.response)
            del output["analysis"][name]
            with self.subTest(name=name): self.reject(output)

    def test_extra_analysis_field_rejected(self):
        self.response["analysis"]["extra"] = self.response["analysis"]["reasoning"]
        self.reject()

    def test_extra_response_section_rejected(self):
        self.response["final_conclusion"] = "approved"
        self.reject()

    def test_every_missing_evidence_time_or_quality_rejected(self):
        for name in ANALYSIS_FIELDS:
            for key in ("content", "evidence_ids", "time_range", "data_quality_status"):
                output = deepcopy(self.response)
                del output["analysis"][name][key]
                with self.subTest(name=name, key=key): self.reject(output)

    def test_unknown_evidence_id_rejected(self):
        self.response["analysis"]["market_view"]["evidence_ids"] = ["EV-NONEXISTENT"]
        self.reject()

    def test_existing_but_outside_prompt_evidence_rejected(self):
        other = PromptBuilder().build(AnalysisPackageBuilder().build(**package_arguments(quote=True)))
        self.assertIn("EV-QUOTE", other.evidence_ids)
        self.response["analysis"]["reasoning"]["evidence_ids"].append("EV-QUOTE")
        self.reject()

    def test_duplicate_field_evidence_rejected(self):
        self.response["analysis"]["market_view"]["evidence_ids"] *= 2
        self.reject()

    def test_forged_company_fact_with_real_evidence_rejected(self):
        self.response["analysis"]["reasoning"]["content"]["facts"][0]["value"] = 999.0
        self.reject()

    def test_fact_from_absent_company_record_rejected(self):
        self.response["analysis"]["reasoning"]["content"]["facts"][0]["input_path"] = "financial_data.records.0.data.revenue"
        self.reject()

    def test_fact_evidence_must_support_its_record(self):
        env = PromptBuilder().build(AnalysisPackageBuilder().build(**package_arguments(quote=True)))
        output = deterministic_response(env)
        field = output["analysis"]["reasoning"]
        field["evidence_ids"].append("EV-QUOTE")
        field["content"]["facts"][0]["evidence_ids"] = ["EV-QUOTE"]
        self.reject(output, env)

    def test_fact_evidence_must_be_in_its_field(self):
        self.response["analysis"]["reasoning"]["evidence_ids"] = []
        self.reject()

    def test_null_financial_metric_cannot_be_claimed_obtained(self):
        env = PromptBuilder().build(AnalysisPackageBuilder().build(**information_package_arguments()))
        output = deterministic_response(env)
        fact = output["analysis"]["reasoning"]["content"]["facts"][0]
        fact.update(input_path="financial_data.records.0.data.revenue", value=521392556.03)
        self.reject(output, env)

    def test_watch_only_rejects_holding_quantity_cost_and_profit(self):
        for key in ("quantity", "cost_price", "profit_loss"):
            output = deepcopy(self.response)
            output["analysis"]["position_status"]["content"]["facts"].append(
                {"input_path": f"holding_position.records.0.data.{key}", "value": 100.0, "evidence_ids": []})
            with self.subTest(key=key): self.reject(output)

    def test_watch_only_rejects_holding_semantics_even_without_numbers(self):
        self.response["analysis"]["position_status"]["content"]["statement"] = "持仓事实来自 portfolio_positions，行情判断引用 Evidence"
        self.reject()

    def test_watch_only_rejects_holding_input_ref(self):
        self.response["analysis"]["position_status"]["input_refs"] = ["holding_position"]
        self.reject()

    def test_holding_branch_binds_portfolio_and_market(self):
        env = PromptBuilder().build(AnalysisPackageBuilder().build(**package_arguments(holding=True)))
        draft = self.validate(deterministic_response(env), env)
        position = draft.output["position_status"]
        self.assertEqual(position["input_refs"], ("holding_position",))
        self.assertIn("EV-DAILY", position["evidence_ids"])
        self.assertTrue(all(not fact["evidence_ids"] for fact in position["content"]["facts"] if fact["input_path"].startswith("holding_position.")))

    def test_holding_without_input_ref_rejected(self):
        env = PromptBuilder().build(AnalysisPackageBuilder().build(**package_arguments(holding=True)))
        output = deterministic_response(env)
        output["analysis"]["position_status"]["input_refs"] = []
        self.reject(output, env)

    def test_holding_time_range_binds_snapshot_as_well_as_market_clock(self):
        env = PromptBuilder().build(AnalysisPackageBuilder().build(**package_arguments(holding=True)))
        output = deterministic_response(env)
        self.validate(output, env)
        self.assertEqual(output["analysis"]["position_status"]["time_range"]["end"], "2026-09-30T17:00:00Z")
        output["analysis"]["position_status"]["time_range"]["end"] = "2026-09-30"
        self.reject(output, env)

    def test_holding_provider_evidence_for_quantity_rejected(self):
        env = PromptBuilder().build(AnalysisPackageBuilder().build(**package_arguments(holding=True)))
        output = deterministic_response(env)
        output["analysis"]["position_status"]["content"]["facts"][-1]["evidence_ids"] = ["EV-DAILY"]
        self.reject(output, env)

    def test_holding_without_actual_market_evidence_rejected(self):
        args = package_arguments(holding=True)
        args["evidence_links"].pop("daily_kline.0")
        env = PromptBuilder().build(AnalysisPackageBuilder().build(**args))
        self.reject(deterministic_response(env), env)

    def test_realtime_missing_allows_dated_historical_analysis(self):
        self.assertEqual(self.package.data_quality["REALTIME_QUOTE_STATUS"], "MISSING")
        draft = self.validate()
        self.assertEqual(draft.output["market_view"]["time_range"]["start"], "2026-09-30")

    def test_realtime_missing_current_price_claim_rejected(self):
        for text in ("当前实时价格为10.5元", "当前价10.5", "current realtime price is 10.5"):
            output = deepcopy(self.response)
            output["analysis"]["market_view"]["content"]["statement"] = text
            with self.subTest(text=text): self.reject(output)

    def test_realtime_quote_branch_valid(self):
        env = PromptBuilder().build(AnalysisPackageBuilder().build(**package_arguments(quote=True)))
        self.validate(deterministic_response(env), env)

    def test_unverified_upgrade_to_pass_rejected(self):
        env = PromptBuilder().build(AnalysisPackageBuilder().build(**information_package_arguments()))
        output = deterministic_response(env)
        output["analysis"]["reasoning"]["data_quality_status"] = "PASS"
        self.reject(output, env)

    def test_unverified_upgrade_to_warning_rejected(self):
        env = PromptBuilder().build(AnalysisPackageBuilder().build(**information_package_arguments()))
        output = deterministic_response(env)
        output["analysis"]["reasoning"]["data_quality_status"] = "WARNING"
        self.reject(output, env)

    def test_warning_upgrade_to_pass_rejected(self):
        args = package_arguments()
        args["source_status"]["TUSHARE_MCP"] = "WARNING"
        env = PromptBuilder().build(AnalysisPackageBuilder().build(**args))
        output = deterministic_response(env)
        output["analysis"]["market_view"]["data_quality_status"] = "PASS"
        self.reject(output, env)

    def test_evidenced_historical_field_can_remain_pass_when_other_blocks_missing(self):
        field = self.response["analysis"]["market_view"]
        field["content"]["limitations"] = []
        field["data_quality_status"] = "PASS"
        draft = self.validate()
        self.assertEqual(draft.output["market_view"]["data_quality_status"], "PASS")

    def test_warning_field_preserved_without_quality_upgrade(self):
        args = package_arguments()
        args["source_status"]["TUSHARE_MCP"] = "WARNING"
        env = PromptBuilder().build(AnalysisPackageBuilder().build(**args))
        output = deterministic_response(env)
        output["analysis"]["market_view"]["content"]["limitations"] = []
        output["analysis"]["market_view"]["data_quality_status"] = "WARNING"
        self.validate(output, env)

    def test_unverified_information_field_preserved(self):
        env = PromptBuilder().build(AnalysisPackageBuilder().build(**information_package_arguments()))
        output = deterministic_response(env)
        field = output["analysis"]["reasoning"]
        field["content"]["limitations"] = []
        field["data_quality_status"] = "UNVERIFIED"
        draft = self.validate(output, env)
        self.assertEqual(draft.output["reasoning"]["data_quality_status"], "UNVERIFIED")

    def test_info_unverified_valuation_and_sector_missing_continue(self):
        env = PromptBuilder().build(AnalysisPackageBuilder().build(**information_package_arguments()))
        draft = self.validate(deterministic_response(env), env)
        self.assertIn("CANONICAL-profit", draft.used_evidence_ids)
        reviews = {item["target"] for item in draft.human_confirmation_items}
        self.assertTrue({"financial_data", "intelligence_data.valuation_summary", "intelligence_data.sector_context"} <= reviews)

    def test_not_available_declaration_preserved(self):
        args = package_arguments()
        args["information_availability"] = {"research_summary": {"status": "NOT_AVAILABLE", "reason": "Not supplied"}}
        env = PromptBuilder().build(AnalysisPackageBuilder().build(**args))
        self.validate(deterministic_response(env), env)

    def test_false_availability_limitation_rejected(self):
        self.response["analysis"]["reasoning"]["content"]["limitations"][0]["status"] = "UNVERIFIED"
        self.reject()

    def test_fabricated_time_range_rejected(self):
        self.response["analysis"]["market_view"]["time_range"]["end"] = "2026-10-03"
        self.reject()

    def test_collection_clock_cannot_replace_publication_clock(self):
        env = PromptBuilder().build(AnalysisPackageBuilder().build(**information_package_arguments()))
        output = deterministic_response(env)
        output["analysis"]["reasoning"]["time_range"] = {"start": "2026-10-03", "end": "2026-10-03"}
        # Main fact is a historical report; request times are never data times.
        self.reject(output, env)

    def test_level1_without_evidence_rejected(self):
        field = self.response["analysis"]["operation_plan"]
        field["evidence_ids"] = []
        field["content"]["facts"] = []
        field["content"].update(statement="明确操作建议", advice={"level": "LEVEL_1", "action": "BUY"})
        self.reject()

    def test_level1_with_real_evidence_but_incomplete_input_rejected(self):
        self.response["analysis"]["operation_plan"]["content"].update(
            statement="明确操作建议", advice={"level": "LEVEL_1", "action": "HOLD"})
        self.response["analysis"]["confidence"]["content"]["assessment"] = "HIGH"
        self.reject()

    def level2(self):
        field = self.response["analysis"]["operation_plan"]
        fact = field["content"]["facts"][0]
        field["content"].update(statement="条件式建议，证据有限", advice={
            "level": "LEVEL_2", "action": "HOLD",
            "condition": {"input_path": fact["input_path"], "operator": "GT", "value": fact["value"]},
            "observation_path": fact["input_path"], "verification": "MANUAL_RECHECK"})
        return field

    def test_level2_condition_low_confidence_explicit_limitations_valid(self):
        self.level2()
        self.validate()

    def test_level2_high_confidence_rejected(self):
        self.level2()
        self.response["analysis"]["confidence"]["content"]["assessment"] = "HIGH"
        self.reject()

    def test_level2_without_limitations_rejected(self):
        self.level2()["content"]["limitations"] = []
        self.reject()

    def test_level2_without_missing_reason_or_impact_rejected(self):
        self.level2()
        for name in ("reason", "impact"):
            output = deepcopy(self.response)
            del output["analysis"]["operation_plan"]["content"]["limitations"][0][name]
            with self.subTest(name=name): self.reject(output)

    def test_limitation_cannot_invent_company_fact_in_reason(self):
        self.response["analysis"]["reasoning"]["content"]["limitations"][0]["reason"] = "公司拥有重大新项目"
        self.reject()

    def test_level2_without_trigger_or_verification_rejected(self):
        self.level2()
        for name in ("condition", "observation_path", "verification"):
            output = deepcopy(self.response)
            del output["analysis"]["operation_plan"]["content"]["advice"][name]
            with self.subTest(name=name): self.reject(output)

    def test_level2_invented_trigger_price_rejected(self):
        self.level2()["content"]["advice"]["condition"]["value"] = 999.0
        self.reject()

    def test_level2_stale_quote_cannot_be_used_as_realtime_trigger(self):
        args = package_arguments(quote=True)
        args["canonical_data"]["daily_kline"] = []
        args["evidence_links"].pop("daily_kline.0")
        args["canonical_data"]["market_quotes"][0]["freshness"] = "STALE"
        env = PromptBuilder().build(AnalysisPackageBuilder().build(**args))
        output = deterministic_response(env)
        fact = output["analysis"]["operation_plan"]["content"]["facts"][0]
        self.assertIn("market_quotes", fact["input_path"])
        output["analysis"]["operation_plan"]["content"].update(statement="条件式建议，证据有限", advice={
            "level": "LEVEL_2", "action": "HOLD", "condition": {"input_path": fact["input_path"], "operator": "GT", "value": fact["value"]},
            "observation_path": fact["input_path"], "verification": "MANUAL_RECHECK"})
        self.reject(output, env)

    def test_level3_without_any_evidence_valid(self):
        args = package_arguments()
        args["evidence_metadata"] = []
        env = PromptBuilder().build(AnalysisPackageBuilder().build(**args))
        draft = self.validate(deterministic_response(env), env)
        self.assertEqual(draft.used_evidence_ids, ())
        self.assertEqual(draft.output["operation_plan"]["content"]["statement"], "证据不足，暂不给出操作建议")

    def test_level3_cannot_smuggle_action(self):
        self.response["analysis"]["operation_plan"]["content"]["advice"]["action"] = "BUY"
        self.reject()

    def test_auto_trading_and_execution_claims_rejected(self):
        for text in ("自动买入", "自动卖出", "已下单", "已委托", "已成交", "已执行", "券商委托指令", "submit_order"):
            output = deepcopy(self.response)
            output["analysis"]["operation_plan"]["content"]["statement"] = text
            with self.subTest(text=text): self.reject(output)

    def test_fourth_source_fact_rejected(self):
        fact = self.response["analysis"]["reasoning"]["content"]["facts"][0]
        fact.update(input_path="canonical_market_data.daily_kline.records.0.data.provider", value="FOURTH_SOURCE")
        self.reject()

    def test_model_memory_fact_prose_rejected(self):
        self.response["analysis"]["reasoning"]["content"]["statement"] = "公司拥有尚未提供的重大新项目"
        self.reject()

    def test_sensitive_raw_and_payload_text_rejected_everywhere(self):
        for text in ("credential", "raw_response", "provider payload", "Authorization: Bearer abc", "sk-" + "a" * 24):
            output = deepcopy(self.response)
            output["analysis"]["reasoning"]["content"]["facts"][0]["value"] = text
            with self.subTest(text=text): self.reject(output)

    def test_sensitive_raw_keys_rejected(self):
        for key in ("raw_response", "provider_payload", "api_key", "password"):
            output = deepcopy(self.response)
            output["analysis"]["reasoning"][key] = "hidden"
            with self.subTest(key=key): self.reject(output)

    def test_missing_confirmation_advice_abnormal_or_assumption_rejected(self):
        for reason in ("REVIEW_ADVICE", "REVIEW_DATA_QUALITY", "REVIEW_ASSUMPTION"):
            output = deepcopy(self.response)
            output["human_confirmation_items"] = [item for item in output["human_confirmation_items"] if item["reason"] != reason]
            with self.subTest(reason=reason): self.reject(output)

    def test_confirmation_state_upgrade_rejected(self):
        for state in ("CONFIRMED", "APPROVED", "FINAL", "已采纳", "已执行"):
            output = deepcopy(self.response)
            output["final_state"] = state
            with self.subTest(state=state): self.reject(output)

    def test_parser_invalid_json_fences_and_wrong_root_rejected(self):
        for raw in ("{", "```json\n{}\n```", "[]", "null", "{} {}", {"analysis": {}}):
            with self.subTest(kind=type(raw).__name__), self.assertRaises(AnalysisOutputError): OutputParser().parse(raw)

    def test_parser_duplicate_keys_at_all_levels_rejected(self):
        for raw in ('{"analysis":{},"analysis":{}}', '{"analysis":{"market_view":{"content":1,"content":2}}}'):
            with self.assertRaises(AnalysisOutputError): OutputParser().parse(raw)

    def test_parser_nan_infinity_and_overflow_rejected(self):
        for raw in ('{"a":NaN}', '{"a":Infinity}', '{"a":-Infinity}', '{"a":1e9999}'):
            with self.assertRaises(AnalysisOutputError): OutputParser().parse(raw)

    def test_no_rewriting_and_hash_definitions(self):
        raw = json.dumps(self.response, ensure_ascii=False, indent=4)
        draft = self.validator.validate(raw, self.envelope)
        self.assertEqual(draft.to_dict()["output"], self.response["analysis"])
        self.assertEqual(draft.model_output_hash, hashlib.sha256(raw.encode()).hexdigest())
        self.assertEqual(draft.validated_output_hash, digest(self.response["analysis"]))
        other = self.validator.validate(stable_json(self.response), self.envelope)
        self.assertEqual(draft.validated_output_hash, other.validated_output_hash)
        self.assertNotEqual(draft.model_output_hash, other.model_output_hash)
        self.assertEqual(draft.human_confirmation_items, other.human_confirmation_items)

    def test_draft_is_deeply_immutable_and_detached(self):
        draft = self.validate()
        self.response["analysis"].clear()
        self.assertEqual(len(draft.output), 7)
        with self.assertRaises(TypeError): draft.output["market_view"]["content"]["statement"] = "changed"
        with self.assertRaises(FrozenInstanceError): draft.analysis_status = "CONFIRMED"

    def test_changed_prompt_hash_and_template_rejected(self):
        for env in (replace(self.envelope, input_hash="0" * 64), replace(self.envelope, template_hash="0" * 64)):
            self.reject(envelope=env)

    def test_changed_draft_seal_rejected(self):
        draft = self.validate()
        object.__setattr__(draft, "used_evidence_ids", ())
        with self.assertRaises(AnalysisOutputError): validate_draft(draft)

    def test_schema_objects_are_independent(self):
        schema = output_schema()
        schema["properties"]["analysis"]["required"].clear()
        self.assertEqual(len(output_schema()["properties"]["analysis"]["required"]), 7)


if __name__ == "__main__":
    unittest.main()
