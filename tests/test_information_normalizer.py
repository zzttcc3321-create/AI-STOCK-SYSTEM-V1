"""G0-shaped offline tests of explicit mappings and original Evidence binding."""

from copy import deepcopy
from dataclasses import FrozenInstanceError, replace
import hashlib
import json
import unittest

from evidence import EvidenceBuilder, EvidenceValidationResult, ValidationStatus, sha256_digest
from normalizer import InformationNormalizer, InformationNormalizationError
from normalizer.information import validate_information_result
from normalizer.information_contracts import (
    FINANCIAL_METRICS, STATEMENT_ENTRIES, TOOL_KINDS, InformationRuleId,
)
from providers import ProviderEnvelope, ProviderStatus
from providers.tdx_information import TdxInformationProvider


STOCK = "601518.SH"
TIME = "2026-10-03T11:27:00+08:00"


def information_input(tool="wenda_news_query", *, view=None, data=None, wire="json"):
    """Synthetic boundary tests; G0 mapping is documented from separate real raw."""
    arguments = {"symbol": STOCK[:6]}
    if view:
        arguments = {"code": STOCK[:6], "entry": next(key for key, value in STATEMENT_ENTRIES.items() if value == view)}
        if view != "balance":
            arguments["fixedTag"] = "00101"
    if data is None:
        if view:
            data = {"transformed": {"parser": "statements", "summary": "源提供财报概况",
                    "tables": [{"name": "statements", "layout": "table", "rows": [
                        {"日期": "2026-06-30", "营业收入": 521392556.03,
                         "净利润": 240082679.71, "基本每股收益": .12,
                         "资产合计": 7035659203.01, "负债合计": 848495676.23,
                         "经营活动现金流量净额": 288115215.24}]}]}}
        else:
            data = [["标题", "时间", "链接", "来源", "摘要"],
                    ["真实结构示意标题", "2025-11-28", "https://example.invalid/article",
                     "示意来源", "源提供摘要"]]
    response = {"structuredContent": {"ok": True, "tool_name": tool, "data": data}}
    body = {"jsonrpc": "2.0", "id": "offline-id", "result": response}
    raw = json.dumps(body, ensure_ascii=False).encode()
    if wire == "sse":
        raw = b"event: message\ndata: " + raw + b"\n\n"
    elif wire == "archived-sse":
        raw = b"event: message\\ndata: " + raw + b"\\n\\n"
    status = ProviderStatus.NOT_AVAILABLE if data == [] else ProviderStatus.AVAILABLE
    envelope = ProviderEnvelope("WORKBUDDY_TDX_MCP", TOOL_KINDS[tool], status, TIME, None,
                                "run008/task008/request/raw.json",
                                {"stock_id": STOCK, "tool": tool, "arguments": arguments, "data": data}, None)
    original = EvidenceBuilder().build(fetch_result=envelope, endpoint_or_tool=tool,
        request_params={"method": "tools/call", "stock_id": STOCK,
                        "params": {"name": tool, "arguments": arguments}},
        system_run_id="run008", collection_task_id="task008", evidence_id="ORIGINAL-" + (view or tool),
        raw_response_hash=hashlib.sha256(raw).hexdigest())
    return envelope, original, raw


def gate_information(tool="wenda_news_query", *, view=None, data=None, wire="json"):
    envelope, original, raw = information_input(tool, view=view, data=data, wire=wire)
    result = InformationNormalizer().normalize(envelope, source_evidence=original, raw_bytes=raw)
    verified = EvidenceBuilder().build(fetch_result=envelope, endpoint_or_tool=original.endpoint_or_tool,
        request_params=original.request_params, system_run_id=original.system_run_id,
        collection_task_id=original.collection_task_id, normalization_result=result,
        supersedes_evidence_id=original.evidence_id, raw_response_hash=original.raw_response_hash,
        evidence_id="CANONICAL-" + (view or tool))
    return result, verified


class InformationNormalizerTests(unittest.TestCase):
    def test_final_evidence_carries_exact_information_rule_ids(self):
        summary = "source_supplied_summary:INFO_SOURCE_SUMMARY_EXACT_COPY"
        information = {summary, "title:INFO_TITLE_EXACT_COPY", "knowability:KNOWABILITY_FOUR_CLOCKS"}
        financial = {summary, "report_date:FIN_REPORT_DATE_EXACT_ISO_DATE",
                     "publish_time:FIN_DISCLOSURE_TIME_EXPLICIT_FIELD", "knowability:KNOWABILITY_FOUR_CLOCKS",
                     "statement_view:FIN_STATEMENT_VIEW_EXACT_ENTRY",
                     *(f"{metric}:FIN_METRIC_UNIT_UNVERIFIED_NULL" for metric in FINANCIAL_METRICS)}
        samples = [(tool, None) for tool in ("wenda_notice_query", "wenda_news_query", "wenda_report_query")]
        samples += [("tdx_api_data", view) for view in ("profit", "balance", "cashflow")]
        observed_ids = set()
        for tool, view in samples:
            with self.subTest(tool=tool, view=view):
                result, evidence = gate_information(tool, view=view)
                expected = set(financial if view else information)
                if not view:
                    publication = "publish_date" if tool == "wenda_report_query" else "publish_time"
                    expected.add(f"{publication}:INFO_PUBLICATION_TIME_UNVERIFIED_NULL")
                    if tool == "wenda_report_query":
                        expected.add("institution:INFO_INSTITUTION_UNVERIFIED_NULL")
                self.assertIsNotNone(evidence.normalization_rule)
                self.assertEqual({rule for rule in evidence.normalization_rule if not rule.startswith("CANONICAL_SEMANTICS:")}, expected)
                self.assertTrue(any(rule.startswith("CANONICAL_SEMANTICS:") for rule in evidence.normalization_rule))
                self.assertIsNotNone(evidence.normalized_data_hash)
                self.assertEqual(evidence.normalized_data_hash, sha256_digest(result.canonical_data))
                self.assertEqual(evidence.validation_result.status, ValidationStatus.PASS)
                observed_ids.update(rule.split(":", 1)[1] for rule in evidence.normalization_rule if not rule.startswith("CANONICAL_SEMANTICS:"))
        self.assertEqual(observed_ids, {rule.value for rule in InformationRuleId})

    def test_normalized_data_forced_mutation_breaks_gate(self):
        for mode in ("remove", "values", "numeric_fields", "normalization_rule", "validation_result"):
            with self.subTest(mode=mode):
                result, _ = gate_information()
                validate_information_result(result)
                record = result.normalized_data[0]
                mutations = {
                    "values": {**record.values, "title": "forced mutation"},
                    "numeric_fields": {"unexpected": None},
                    "normalization_rule": (),
                    "validation_result": replace(record.validation_result, passed=False),
                }
                changed = () if mode == "remove" else (replace(record, **{mode: mutations[mode]}),)
                object.__setattr__(result, "normalized_data", changed)
                with self.assertRaisesRegex(InformationNormalizationError, "unchanged InformationNormalizer"):
                    validate_information_result(result)

    def test_financial_arguments_require_exact_keys_and_fixed_tag(self):
        for view in ("profit", "balance", "cashflow"):
            envelope, original, raw = information_input("tdx_api_data", view=view)
            good = envelope.payload["arguments"]
            InformationNormalizer().normalize(envelope, source_evidence=original, raw_bytes=raw)
            bad = [{**good, "extra": "unrecognized"},
                   {key: value for key, value in good.items() if key != "entry"},
                   {key: value for key, value in good.items() if key != "code"},
                   {**good, "code": "000001"},
                   {**good, "entry": good["entry"] + "_alias"}]
            if view == "balance":
                bad += [{**good, "fixedTag": value} for value in ("00101", None)]
            else:
                bad += [{key: value for key, value in good.items() if key != "fixedTag"}]
                bad += [{**good, "fixedTag": value} for value in ("00102", 101, None)]
            for arguments in bad:
                with self.subTest(view=view, arguments=arguments):
                    params = {**original.request_params,
                              "params": {"name": "tdx_api_data", "arguments": arguments}}
                    changed = replace(envelope, payload={**envelope.payload, "arguments": arguments})
                    with self.assertRaises(InformationNormalizationError):
                        InformationNormalizer().normalize(changed,
                            source_evidence=replace(original, request_params=params), raw_bytes=raw)

    def test_notice_news_report_are_separate_explicit_categories(self):
        for tool, category in [("wenda_notice_query", "recent_announcements"),
                               ("wenda_news_query", "recent_news"),
                               ("wenda_report_query", "research_summary")]:
            with self.subTest(tool=tool):
                result, evidence = gate_information(tool)
                row = result.canonical_data[0]
                self.assertEqual(result.category, category)
                self.assertEqual(row["stock_id"], STOCK)
                self.assertEqual(row["source"], "WORKBUDDY_TDX_MCP")
                self.assertEqual(row["title"], "真实结构示意标题")
                self.assertEqual(row["data_quality_status"], "UNVERIFIED")
                self.assertIsNone(row["publish_date" if tool == "wenda_report_query" else "publish_time"])
                self.assertEqual(evidence.validation_result.status, ValidationStatus.PASS)

    def test_summary_is_verbatim_and_never_content_or_generated(self):
        result, _ = gate_information()
        row = result.canonical_data[0]
        self.assertEqual(row["source_supplied_summary"], "源提供摘要")
        self.assertEqual(row["SOURCE_SUPPLIED_SUMMARY"], "YES")
        self.assertEqual(row["MODEL_GENERATED_SUMMARY"], "NO")
        self.assertIsNone(row["content"])

    def test_generic_source_is_not_institution_and_no_rating_inference(self):
        result, _ = gate_information("wenda_report_query")
        self.assertIsNone(result.canonical_data[0]["institution"])
        self.assertIsNone(result.canonical_data[0]["rating"])

    def test_missing_summary_is_null(self):
        result, _ = gate_information(data=[["标题"], ["标题示意"]])
        row = result.canonical_data[0]
        self.assertIsNone(row["source_supplied_summary"])
        self.assertEqual(row["SOURCE_SUPPLIED_SUMMARY"], "NO")

    def test_unknown_columns_are_raw_only_not_aliases(self):
        result, _ = gate_information(data=[["标题", "发布时间", "正文", "industry"],
                                          ["标题示意", TIME, "不能变为正文", "不能成为行业"]])
        row = result.canonical_data[0]
        self.assertIsNone(row["publish_time"])
        self.assertIsNone(row["content"])
        self.assertNotIn("industry", row)

    def test_fuzzy_title_and_bad_rows_are_rejected(self):
        for data in ([["title"], ["标题"]], [["标题", "标题"], ["一", "二"]],
                     [["标题"], []], [["标题"], [None]], [["标题"], [""]],
                     {"标题": "不是审计结构"}):
            with self.subTest(data=data), self.assertRaises(InformationNormalizationError):
                envelope, original, raw = information_input(data=data)
                InformationNormalizer().normalize(envelope, source_evidence=original, raw_bytes=raw)

    def test_financial_three_views_keep_report_period_and_null_metrics(self):
        for view in ("profit", "balance", "cashflow"):
            with self.subTest(view=view):
                result, _ = gate_information("tdx_api_data", view=view)
                row = result.canonical_data[0]
                self.assertEqual(row["report_date"], "2026-06-30")
                self.assertEqual(row["statement_view"], view)
                self.assertEqual(row["provider"], "WORKBUDDY_TDX_MCP")
                self.assertTrue(all(row[name] is None for name in FINANCIAL_METRICS))
                self.assertEqual(row["data_quality_status"], "UNVERIFIED")

    def test_financial_date_not_derived_from_text_or_request(self):
        envelope, original, _ = information_input("tdx_api_data", view="profit")
        data = deepcopy(envelope.payload["data"])
        data["transformed"]["tables"][0]["rows"][0] = {"截止日期": "2026中报"}
        envelope, original, raw = information_input("tdx_api_data", view="profit", data=data)
        with self.assertRaises(InformationNormalizationError):
            InformationNormalizer().normalize(envelope, source_evidence=original, raw_bytes=raw)

    def test_bad_date_duplicate_date_and_unknown_parser_rejected(self):
        for mode in ("bad-date", "duplicate", "parser", "table"):
            envelope, _, _ = information_input("tdx_api_data", view="balance")
            data = deepcopy(envelope.payload["data"])
            tr = data["transformed"]
            if mode == "bad-date": tr["tables"][0]["rows"][0]["日期"] = "2026-02-30"
            if mode == "duplicate": tr["tables"][0]["rows"] *= 2
            if mode == "parser": tr["parser"] = "company"
            if mode == "table": tr["tables"][0]["name"] = "unknown"
            envelope, original, raw = information_input("tdx_api_data", view="balance", data=data)
            with self.subTest(mode=mode), self.assertRaises(InformationNormalizationError):
                InformationNormalizer().normalize(envelope, source_evidence=original, raw_bytes=raw)

    def test_raw_hash_and_provider_payload_are_bound(self):
        envelope, original, raw = information_input()
        for changed_raw, changed_envelope in ((raw + b" ", envelope),
                (raw, replace(envelope, payload={**envelope.payload, "data": [["标题"], ["篡改"]]}))):
            with self.assertRaises(InformationNormalizationError):
                InformationNormalizer().normalize(changed_envelope, source_evidence=original, raw_bytes=changed_raw)

    def test_identity_source_tool_category_and_clocks_must_match(self):
        envelope, original, raw = information_input()
        changes = [replace(envelope, provider_name="FOURTH_SOURCE"),
                   replace(envelope, provider_name="TUSHARE_MCP"),
                   replace(envelope, data_kind="research"),
                   replace(envelope, request_timestamp="2026-10-03T11:28:00+08:00"),
                   replace(envelope, provider_timestamp=TIME),
                   replace(envelope, raw_reference="other/raw.json"),
                   replace(envelope, payload={**envelope.payload, "stock_id": "000001.SZ"}),
                   replace(envelope, payload={**envelope.payload, "tool": "wenda_report_query"})]
        for changed in changes:
            with self.assertRaises(InformationNormalizationError):
                InformationNormalizer().normalize(changed, source_evidence=original, raw_bytes=raw)

    def test_excluded_tools_and_company_never_canonicalized(self):
        envelope, original, raw = information_input()
        for tool in ("wenda_macro_query", "tdx_ai_listening", "unknown_tool"):
            with self.subTest(tool=tool), self.assertRaises(InformationNormalizationError):
                InformationNormalizer().normalize(replace(envelope, payload={**envelope.payload, "tool": tool}),
                                                source_evidence=original, raw_bytes=raw)
        envelope, original, raw = information_input("tdx_api_data", view="profit")
        params = {
            "method": "tools/call", "stock_id": STOCK, "params": {"name": "tdx_api_data",
            "arguments": {"entry": "TdxSharePCCW.tdxf10_gg_gsgk", "code": "601518"}}}
        envelope = replace(envelope, payload={**envelope.payload, "arguments": params["params"]["arguments"]})
        with self.assertRaises(InformationNormalizationError):
            InformationNormalizer().normalize(envelope, source_evidence=replace(original, request_params=params), raw_bytes=raw)

    def test_empty_response_has_no_records_and_no_pass_evidence(self):
        result, evidence = gate_information(data=[])
        self.assertEqual(result.canonical_data, ())
        self.assertEqual(evidence.validation_result.status, ValidationStatus.FAIL)

    def test_failed_original_evidence_is_rejected(self):
        envelope, original, raw = information_input()
        original = replace(original, validation_result=EvidenceValidationResult(ValidationStatus.FAIL))
        with self.assertRaises(InformationNormalizationError):
            InformationNormalizer().normalize(envelope, source_evidence=original, raw_bytes=raw)

    def test_json_and_both_archived_sse_formats_match(self):
        expected, _ = gate_information()
        for wire in ("sse", "archived-sse"):
            result, _ = gate_information(wire=wire)
            self.assertEqual(result.canonical_data, expected.canonical_data)

    def test_provider_timestamp_and_collection_time_do_not_become_publication(self):
        envelope, original, raw = information_input()
        provider_time = "2026-10-03T10:00:00+08:00"
        envelope = replace(envelope, provider_timestamp=provider_time)
        original = replace(original, provider_timestamp=provider_time)
        result = InformationNormalizer().normalize(envelope, source_evidence=original, raw_bytes=raw)
        self.assertEqual(result.provenance["request_timestamp"], TIME)
        self.assertEqual(result.provenance["provider_timestamp"], provider_time)
        self.assertIsNone(result.canonical_data[0]["publish_time"])

    def test_ambiguous_sse_and_duplicate_json_keys_rejected(self):
        envelope, original, raw = information_input()
        for changed_raw in (b"event: message\ndata: " + raw + b"\n\ndata: " + raw + b"\n\n",
                            b'{"data": [], "data": []}', b'{"data": NaN}'):
            original_changed = replace(original, raw_response_hash=hashlib.sha256(changed_raw).hexdigest())
            with self.assertRaises(InformationNormalizationError):
                InformationNormalizer().normalize(envelope, source_evidence=original_changed, raw_bytes=changed_raw)

    def test_append_only_hash_chain_original_unchanged(self):
        envelope, original, raw = information_input()
        result = InformationNormalizer().normalize(envelope, source_evidence=original, raw_bytes=raw)
        _, evidence = gate_information()
        self.assertEqual(original.validation_result.status, ValidationStatus.NOT_VALIDATED)
        self.assertIsNone(original.normalized_data_hash)
        self.assertEqual(evidence.supersedes_evidence_id, original.evidence_id)
        self.assertEqual(evidence.raw_response_hash, original.raw_response_hash)
        self.assertEqual(evidence.normalized_data_hash, sha256_digest(result.canonical_data))
        self.assertIsNone(evidence.analysis_output_hash)

    def test_result_recursively_immutable_and_replace_loses_gate(self):
        result, _ = gate_information()
        with self.assertRaises(TypeError): result.canonical_data[0]["title"] = "changed"
        with self.assertRaises(TypeError): result.provenance["request_params"]["stock_id"] = "changed"
        with self.assertRaises(FrozenInstanceError): result.category = "financial_summary"
        with self.assertRaises(InformationNormalizationError): validate_information_result(replace(result))

    def test_extraction_uses_existing_provider_parser(self):
        envelope, _, raw = information_input()
        self.assertEqual(TdxInformationProvider._data(json.loads(raw)["result"], tool=envelope.payload["tool"]),
                         envelope.payload["data"])
