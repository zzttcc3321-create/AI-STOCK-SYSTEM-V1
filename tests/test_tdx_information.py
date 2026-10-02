"""Mock transport only, including urllib wire tests. Real sockets are forbidden."""

from datetime import datetime, timedelta, timezone
import hashlib
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import MagicMock, patch
from urllib.error import HTTPError, URLError

from providers import ProviderStatus, TdxInformationProvider
from providers.tdx_information import (
    ACTUAL_PROVIDER_SERVICE, ENDPOINT, LOGICAL_SOURCE, MAX_BODY_BYTES,
    MCPHTTPTransport, MCPResponse, NoRedirect, TOOL_KINDS,
)

# TEST_KEY_ONLY: deliberately synthetic; never read a real key in this suite.
TEST_ENV = {"TDX_MCP_API_KEY": "TEST_KEY_ONLY"}
NOW = datetime(2026, 10, 2, 3, 30, tzinfo=timezone.utc)
ROWS = [{"title": "MOCK 研报", "time": "2026-10-01", "url": "https://example.invalid/report",
         "source": "MOCK", "summary": "TEST DATA ONLY"}]


def setUpModule():
    global GUARDS
    GUARDS = [patch("socket.socket", side_effect=AssertionError("real network forbidden")),
              patch("socket.create_connection", side_effect=AssertionError("real network forbidden"))]
    for guard in GUARDS:
        guard.start()


def tearDownModule():
    for guard in reversed(GUARDS):
        guard.stop()


def encode(value):
    return json.dumps(value, ensure_ascii=False).encode()


def query(tool="wenda_report_query", **changes):
    arguments = {"stock_code": "601518.SH", "bdate": "20261001", "edate": "20261002"}
    if tool == "tdx_api_data":
        arguments = {"view": "profit"}
    elif tool == "tdx_ai_listening":
        arguments = {"date": "2026-10-02"}
    elif tool == "wenda_macro_query":
        arguments = {"query": "GDP|20240101|20241231||"}
    value = {"stock_id": "601518.SH", "tool": tool, "data_kind": TOOL_KINDS.get(tool, "news"),
             "arguments": arguments, "system_run_id": "run_006", "collection_task_id": "task_006",
             "request_id": "request_006"}
    if tool == "wenda_macro_query":
        value["stock_id"] = ""
    value.update(changes)
    return value


class Clock:
    def __init__(self):
        self.now = NOW

    def __call__(self):
        return self.now


class MockTransport:
    def __init__(self):
        self.calls = []
        self.hook = None
        self.result = {"content": [{"type": "text", "text": json.dumps({"code": 0, "data": ROWS})}]}
        self.raw_bodies = []
        self.initializations = 0

    def post(self, body, *, headers, timeout):
        rpc = json.loads(body)
        self.calls.append((rpc, dict(headers), timeout))
        if self.hook:
            overridden = self.hook(rpc, headers)
            if overridden is not None:
                self.raw_bodies.append(overridden.body)
                return overridden
        method = rpc["method"]
        response_headers = {"Content-Type": "application/json"}
        if method == "initialize":
            self.initializations += 1
            result = {"protocolVersion": "2025-03-26", "capabilities": {"tools": {}},
                      "serverInfo": {"name": "MOCK", "version": "1"}}
            response_headers["mcp-session-id"] = f"mock-session-{self.initializations}"
        elif method == "notifications/initialized":
            return MCPResponse(202, {}, b"")
        elif method == "tools/list":
            result = {"tools": [{"name": name, "inputSchema": {"type": "object"}} for name in TOOL_KINDS]}
        elif method == "tools/call":
            result = self.result
        else:
            raise AssertionError("unexpected RPC")
        data = encode({"jsonrpc": "2.0", "id": rpc["id"], "result": result})
        self.raw_bodies.append(data)
        return MCPResponse(200, response_headers, data)


class ProviderTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.transport = MockTransport()
        self.clock = Clock()
        self.provider = TdxInformationProvider(self.transport, evidence_root=self.root,
                                               environ=TEST_ENV, clock=self.clock)

    def calls(self, method):
        return [c for c in self.transport.calls if c[0]["method"] == method]

    def test_handshake_notification_session_tools_and_bearer(self):
        result = self.provider.fetch(query())
        self.assertEqual(result.status, ProviderStatus.AVAILABLE)
        self.assertEqual([c[0]["method"] for c in self.transport.calls],
                         ["initialize", "notifications/initialized", "tools/list", "tools/call"])
        for _, headers, timeout in self.transport.calls:
            self.assertEqual(headers["Authorization"], "Bearer TEST_KEY_ONLY")
            self.assertEqual(headers["Accept"], "application/json, text/event-stream")
            self.assertEqual(timeout, 30)
        self.assertNotIn("Mcp-Session-Id", self.transport.calls[0][1])
        for _, headers, _ in self.transport.calls[1:]:
            self.assertEqual(headers["Mcp-Session-Id"], "mock-session-1")
            self.assertEqual(headers["MCP-Protocol-Version"], "2025-03-26")
        self.assertNotIn("id", self.calls("notifications/initialized")[0][0])

    def test_missing_key_fails_at_start_without_transport(self):
        for env in ({}, {"TDX_MCP_API_KEY": ""}, {"TDX_MCP_API_KEY": "bad\nkey"}):
            with self.assertRaisesRegex(ValueError, "TDX_MCP_API_KEY"):
                TdxInformationProvider(self.transport, evidence_root=self.root, environ=env)
        self.assertEqual(self.transport.calls, [])

    def test_default_key_source_is_environment_only(self):
        with patch.dict("os.environ", TEST_ENV, clear=True):
            provider = TdxInformationProvider(self.transport, evidence_root=self.root, clock=self.clock)
            self.assertEqual(provider.fetch(query()).status, ProviderStatus.AVAILABLE)

    def test_announcement(self):
        self.assert_success("wenda_notice_query")

    def test_research(self):
        self.assert_success("wenda_report_query")

    def test_news(self):
        self.assert_success("wenda_news_query")

    def test_macro_maps_to_frozen_news_kind(self):
        self.assert_success("wenda_macro_query")

    def test_f10_maps_to_frozen_financial_kind(self):
        self.assert_success("tdx_api_data")

    def test_listening_requires_explicit_time_and_is_news_only(self):
        self.assert_success("tdx_ai_listening")

    def assert_success(self, tool):
        result = self.provider.fetch(query(tool))
        self.assertEqual(result.status, ProviderStatus.AVAILABLE)
        self.assertEqual(result.payload["data"], ROWS)
        self.assertEqual(result.payload["canonical_data"], [])
        self.assertTrue(self.provider.validate_contract(result).valid)
        self.assertIsNone(result.provider_timestamp)
        expected = {"symbol": "601518", "bdate": "20261001", "edate": "20261002"}
        if tool == "tdx_api_data":
            expected = {"entry": "TdxShareCW.ph_agf10_cw_lyb", "code": "601518", "fixedTag": "00101"}
        elif tool == "tdx_ai_listening":
            expected = {"setcode_code": "1_601518", "date": "2026-10-02"}
        elif tool == "wenda_macro_query":
            expected = {"query": "GDP|20240101|20241231||"}
        self.assertEqual(self.calls("tools/call")[0][0]["params"],
                         {"name": tool, "arguments": expected})
        self.assertEqual(result.payload["arguments"], expected)
        self.assertEqual(result.payload["evidence"][-1]["record"]["request_params"]["params"]["arguments"], expected)

    def assert_rejected_without_effect(self, request):
        call_count = len(self.transport.calls)
        files = set(self.root.rglob("*.json"))
        result = self.provider.fetch(request)
        self.assertEqual(result.status, ProviderStatus.VALIDATION_FAILED)
        self.assertEqual(result.payload["data"], [])
        self.assertEqual(result.payload["evidence"], [])
        self.assertIsNone(result.raw_reference)
        self.assertEqual(len(self.transport.calls), call_count)
        self.assertEqual(set(self.root.rglob("*.json")), files)

    def test_stock_id_generates_symbol_for_all_stock_wenda_tools_and_markets(self):
        for tool in ("wenda_notice_query", "wenda_report_query", "wenda_news_query"):
            for stock in ("601518.SH", "000001.SZ", "920001.BJ"):
                with self.subTest(tool=tool, stock=stock):
                    args = {"bdate": "20261001", "edate": "20261002"}
                    result = self.provider.fetch(query(tool, stock_id=stock, arguments=args))
                    self.assertEqual(result.status, ProviderStatus.AVAILABLE)
                    self.assertEqual(self.calls("tools/call")[-1][0]["params"]["arguments"],
                                     {**args, "symbol": stock[:6]})

    def test_matching_symbol_and_legacy_stock_code_unify_to_one_effect(self):
        first = self.provider.fetch(query(arguments={"bdate": "20261001", "edate": "20261002"}))
        for identity in ({"symbol": "601518"}, {"stock_code": "601518"},
                         {"stock_code": "601518.SH"},
                         {"symbol": "601518", "stock_code": "601518.SH"},
                         {"symbol": "601518", "stock_code": "601518"}):
            with self.subTest(identity=identity):
                request = query(arguments={"bdate": "20261001", "edate": "20261002", **identity})
                self.assertEqual(self.provider.fetch(request), first)
        self.assertEqual(first.status, ProviderStatus.AVAILABLE)
        self.assertEqual(len(self.calls("tools/call")), 1)
        self.assertNotIn("stock_code", first.payload["arguments"])

    def test_conflicting_stock_aliases_rejected_before_any_http(self):
        for tool in ("wenda_notice_query", "wenda_report_query", "wenda_news_query"):
            for identity in ({"symbol": "000001"}, {"stock_code": "000001.SZ"},
                             {"stock_code": "601518.SZ"},
                             {"symbol": "601518", "stock_code": "000001"},
                             {"symbol": "000001", "stock_code": "601518.SH"}):
                with self.subTest(tool=tool, identity=identity):
                    self.assert_rejected_without_effect(query(
                        tool, arguments={"bdate": "20261001", "edate": "20261002", **identity}))

    def test_stock_aliases_require_exact_string_identity(self):
        for name in ("symbol", "stock_code"):
            for value in (None, 601518, True, "", " 601518", "601518 ", ["601518"],
                          {"code": "601518"}, "６０１５１８"):
                with self.subTest(name=name, value=value):
                    self.assert_rejected_without_effect(query(arguments={
                        "bdate": "20261001", "edate": "20261002", name: value}))

    def test_stock_conflict_cannot_return_cached_data_or_trigger_http(self):
        self.assertEqual(self.provider.fetch(query()).status, ProviderStatus.AVAILABLE)
        for identity in ({"symbol": "000001"}, {"stock_code": "601518.SZ"}):
            self.assert_rejected_without_effect(query(arguments={
                "bdate": "20261001", "edate": "20261002", **identity}))

    def test_translated_symbol_in_wire_payload_and_evidence_without_mutating_request(self):
        request = query()
        before = json.loads(json.dumps(request))
        result = self.provider.fetch(request)
        self.assertEqual(result.status, ProviderStatus.AVAILABLE)
        self.assertEqual(request, before)
        wire_args = self.calls("tools/call")[0][0]["params"]["arguments"]
        self.assertEqual(wire_args["symbol"], "601518")
        self.assertNotIn("stock_code", wire_args)
        self.assertNotIn("stock_code", result.payload["arguments"])
        for path in self.root.rglob("*.evidence.json"):
            self.assertNotIn("stock_code", path.read_text())
        expected_hash = hashlib.sha256(json.dumps(
            wire_args, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
        self.assertEqual(result.payload["parameters_hash"], expected_hash)

    def test_listening_setcode_code_derived_from_all_three_markets(self):
        for stock, expected in (("601518.SH", "1_601518"), ("000001.SZ", "0_000001"),
                                ("920001.BJ", "2_920001")):
            with self.subTest(stock=stock):
                result = self.provider.fetch(query("tdx_ai_listening", stock_id=stock))
                self.assertEqual(result.status, ProviderStatus.AVAILABLE)
                self.assertEqual(self.calls("tools/call")[-1][0]["params"]["arguments"],
                                 {"setcode_code": expected, "date": "2026-10-02"})

    def test_listening_matching_setcode_code_unifies_to_same_cache_key(self):
        first = self.provider.fetch(query("tdx_ai_listening"))
        second = self.provider.fetch(query("tdx_ai_listening", arguments={
            "setcode_code": "1_601518", "date": "2026-10-02"}))
        self.assertEqual(first.status, ProviderStatus.AVAILABLE)
        self.assertEqual(second, first)
        self.assertEqual(len(self.calls("tools/call")), 1)

    def test_listening_setcode_conflicts_rejected_before_http(self):
        for value in ("0_601518", "1_000001", "2_601518", "1_601518 ", "", None, 601518,
                      ["1_601518"]):
            with self.subTest(value=value):
                self.assert_rejected_without_effect(query("tdx_ai_listening", arguments={
                    "setcode_code": value, "date": "2026-10-02"}))

    def test_listening_conflict_rejected_after_cached_success(self):
        self.provider.fetch(query("tdx_ai_listening"))
        self.assert_rejected_without_effect(query("tdx_ai_listening", arguments={
            "date": "2026-10-02", "setcode_code": "0_601518"}))

    def test_listening_stock_id_required_and_cannot_be_supplied_by_setcode(self):
        for stock in ("", None, "601518", "601518.XX", "６０１５１８.SH"):
            with self.subTest(stock=stock):
                self.assert_rejected_without_effect(query("tdx_ai_listening", stock_id=stock, arguments={
                    "date": "2026-10-02", "setcode_code": "1_601518"}))

    def test_listening_date_is_required_strict_and_calendar_valid(self):
        for day in (None, "", "20261002", "2026-1-02", "2026-10-2", "2026/10/02",
                    "2026-10-02T00:00:00", "2026-10-02\n", " 2026-10-02", "２０２６-１０-０２",
                    "2026-02-29", "2026-02-30", "2026-13-01", "0000-01-01", 20261002):
            with self.subTest(day=day):
                self.assert_rejected_without_effect(query("tdx_ai_listening", arguments={"date": day}))
        self.assert_rejected_without_effect(query("tdx_ai_listening", arguments={}))

    def test_listening_valid_leap_day_and_empty_result(self):
        self.transport.result = {"structuredContent": {"code": 0, "data": []}}
        result = self.provider.fetch(query("tdx_ai_listening", arguments={"date": "2024-02-29"}))
        self.assertEqual(result.status, ProviderStatus.NOT_AVAILABLE)
        self.assertTrue(result.payload["evidence"])
        self.assertEqual(self.calls("tools/call")[-1][0]["params"]["arguments"],
                         {"setcode_code": "1_601518", "date": "2024-02-29"})

    def test_listening_time_ranges_never_substitute_for_date(self):
        for args in ({"bdate": "20261001", "edate": "20261002"},
                     {"time_range": {"start": "20261001", "end": "20261002"}},
                     {"date": "2026-10-02", "bdate": "20261001", "edate": "20261002"}):
            with self.subTest(args=args):
                self.assert_rejected_without_effect(query("tdx_ai_listening", arguments=args))

    def test_macro_query_required_string_and_five_pipe_fields(self):
        for args in ({}, {"bdate": "20240101", "edate": "20241231"},
                     {"query": None}, {"query": 123}, {"query": []}, {"query": "GDP"},
                     {"query": "GDP|20240101|20241231|"},
                     {"query": "GDP|20240101|20241231|||extra"}):
            with self.subTest(args=args):
                self.assert_rejected_without_effect(query("wenda_macro_query", arguments=args))

    def test_macro_query_passthrough_without_date_semantics_or_stock_requirement(self):
        text = "宏观主题|自定义起始|自定义截止|关键词|描述"
        request = query("wenda_macro_query", arguments={"query": text})
        request.pop("stock_id")
        result = self.provider.fetch(request)
        self.assertEqual(result.status, ProviderStatus.AVAILABLE)
        self.assertEqual(self.calls("tools/call")[0][0]["params"]["arguments"], {"query": text})

    def test_macro_stock_aliases_still_require_outer_identity_and_remove_stock_code(self):
        args = {"query": "GDP|20240101|20241231||", "stock_code": "601518.SH"}
        self.assert_rejected_without_effect(query("wenda_macro_query", arguments=args))
        self.assert_rejected_without_effect(query("wenda_macro_query", stock_id="000001.SZ", arguments=args))
        result = self.provider.fetch(query("wenda_macro_query", stock_id="601518.SH", arguments=args))
        self.assertEqual(result.status, ProviderStatus.AVAILABLE)
        self.assertEqual(self.calls("tools/call")[-1][0]["params"]["arguments"],
                         {"query": args["query"], "symbol": "601518"})

    def test_f10_all_four_view_mappings_exact_wire_parameters(self):
        for view, expected in (
            ("profit", {"entry": "TdxShareCW.ph_agf10_cw_lyb", "fixedTag": "00101", "code": "601518"}),
            ("balance", {"entry": "TdxShareCW.ph_agf10_cw_zcfzb", "code": "601518"}),
            ("cashflow", {"entry": "TdxShareCW.ph_agf10_cw_xjllb", "fixedTag": "00101", "code": "601518"}),
            ("company", {"entry": "TdxSharePCCW.tdxf10_gg_gsgk", "fixedTag": "0", "code": "601518"}),
        ):
            with self.subTest(view=view):
                request = query("tdx_api_data", arguments={"view": view})
                result = self.provider.fetch(request)
                self.assertEqual(result.status, ProviderStatus.AVAILABLE)
                self.assertEqual(self.calls("tools/call")[-1][0]["params"]["arguments"], expected)
                self.assertEqual(result.payload["arguments"], expected)
                self.assertEqual(result.payload["evidence"][-1]["record"]["request_params"]["params"]["arguments"], expected)
                self.assertEqual(request["arguments"], {"view": view})
        self.assertEqual(len(self.calls("tools/call")), 4)

    def test_f10_code_is_derived_from_stock_id_including_leading_zero(self):
        for stock in ("000001.SZ", "920001.BJ"):
            with self.subTest(stock=stock):
                result = self.provider.fetch(query("tdx_api_data", stock_id=stock, arguments={"view": "balance"}))
                self.assertEqual(result.status, ProviderStatus.AVAILABLE)
                self.assertEqual(self.calls("tools/call")[-1][0]["params"]["arguments"],
                                 {"entry": "TdxShareCW.ph_agf10_cw_zcfzb", "code": stock[:6]})

    def test_f10_missing_or_non_whitelisted_views_rejected(self):
        self.assert_rejected_without_effect(query("tdx_api_data", arguments={}))
        for view in (None, "", "f10", "financial", "raw", "quote", "Profit", "profit ",
                     "TdxShareCW.ph_agf10_cw_lyb", 1, ["profit"], {"view": "profit"}):
            with self.subTest(view=view):
                self.assert_rejected_without_effect(query("tdx_api_data", arguments={"view": view}))

    def test_f10_entry_fixedtag_and_code_cannot_be_caller_supplied_even_if_matching(self):
        for update in ({"entry": "TdxShareCW.ph_agf10_cw_lyb"}, {"entry": "tdx_quotes"},
                       {"fixedTag": "00101"}, {"fixedTag": "99999"},
                       {"code": "601518"}, {"code": "000001"}):
            with self.subTest(update=update):
                self.assert_rejected_without_effect(query("tdx_api_data", arguments={"view": "profit", **update}))
        self.assert_rejected_without_effect(query("tdx_api_data", arguments={"view": "balance", "fixedTag": "00101"}))

    def test_f10_raw_and_generic_passthrough_forbidden(self):
        for update in ({"mode": "raw"}, {"mode": "view"}, {"api_name": "f10"},
                       {"api_name": "financial"}, {"arguments": {"entry": "tdx_quotes"}},
                       {"params": {"fixedTag": "99999"}}, {"symbol": "601518"},
                       {"stock_code": "601518.SH"}, {"bdate": "20261001", "edate": "20261002"}):
            with self.subTest(update=update):
                self.assert_rejected_without_effect(query("tdx_api_data", arguments={"view": "profit", **update}))

    def test_f10_overrides_rejected_even_after_cached_success(self):
        self.provider.fetch(query("tdx_api_data"))
        for update in ({"fixedTag": "00101"}, {"entry": "TdxShareCW.ph_agf10_cw_lyb"}, {"mode": "raw"}):
            self.assert_rejected_without_effect(query("tdx_api_data", arguments={"view": "profit", **update}))

    def test_f10_all_four_views_accept_legal_empty_results(self):
        self.transport.result = {"structuredContent": {"code": 0, "data": []}}
        for view in ("profit", "balance", "cashflow", "company"):
            with self.subTest(view=view):
                result = self.provider.fetch(query("tdx_api_data", arguments={"view": view}))
                self.assertEqual(result.status, ProviderStatus.NOT_AVAILABLE)
                self.assertTrue(result.payload["evidence"])
                self.assertEqual(result.payload["data"], [])
        self.assertEqual(len(self.calls("tools/call")), 4)

    def test_timeout_and_wrapped_timeout(self):
        for failure in (TimeoutError("TEST_KEY_ONLY"), URLError(TimeoutError())):
            self.transport.hook = lambda rpc, headers, failure=failure: (_ for _ in ()).throw(failure)
            result = self.provider.fetch(query())
            self.assertEqual(result.status, ProviderStatus.TIMEOUT)
            self.assertEqual(result.payload["data"], [])
            self.assertNotIn("TEST_KEY_ONLY", str(result))

    def test_401_and_403_are_auth_failed_raw_retained(self):
        for status in (401, 403):
            self.transport.hook = lambda rpc, headers, status=status: MCPResponse(status, {}, b'{"error":"auth rejected"}')
            result = self.provider.fetch(query())
            self.assertEqual(result.status, ProviderStatus.AUTH_FAILED)
            self.assertEqual(result.payload["evidence"][-1]["raw_bytes"], b'{"error":"auth rejected"}')
            self.assertNotIn("TEST_KEY_ONLY", str(result))

    def test_connection_refused_and_dns_not_available_do_not_raise(self):
        for failure in (ConnectionRefusedError(), URLError("DNS unavailable")):
            self.transport.hook = lambda rpc, headers, failure=failure: (_ for _ in ()).throw(failure)
            result = self.provider.fetch(query())
            self.assertEqual(result.status, ProviderStatus.NOT_AVAILABLE)
            self.assertIsNone(result.raw_reference)

    def test_nonzero_business_code_only_raw_no_canonical(self):
        self.transport.result = {"structuredContent": {"code": 7, "data": ROWS}}
        result = self.provider.fetch(query())
        self.assertEqual(result.status, ProviderStatus.VALIDATION_FAILED)
        self.assertEqual(result.payload["data"], [])
        self.assertEqual(result.payload["canonical_data"], [])
        self.assertIn(b'"code": 7', result.payload["evidence"][-1]["raw_bytes"])

    def test_raw_bytes_disk_hash_evidence_endpoint_tool_identity(self):
        result = self.provider.fetch(query())
        entries = result.payload["evidence"]
        self.assertEqual(len(entries), 3)
        for entry, body in zip(entries, self.transport.raw_bodies):
            record = entry["record"]
            self.assertEqual(entry["raw_bytes"], body)
            self.assertEqual(Path(record["raw_response_path"]).read_bytes(), body)
            self.assertEqual(record["raw_response_hash"], hashlib.sha256(body).hexdigest())
            self.assertEqual(record["source"], LOGICAL_SOURCE)
            self.assertEqual(entry["actual_provider_service"], ACTUAL_PROVIDER_SERVICE)
            self.assertIn(ENDPOINT, record["endpoint_or_tool"])
        self.assertEqual(entries[-1]["record"]["endpoint_or_tool"], ENDPOINT + "#wenda_report_query")
        self.assertEqual(entries[-1]["record"]["request_params"]["params"]["arguments"]["bdate"], "20261001")
        self.assertEqual(result.raw_reference, entries[-1]["record"]["raw_response_path"])
        sidecar = Path(result.raw_reference).with_suffix(".evidence.json")
        self.assertEqual(json.loads(sidecar.read_bytes())["record"], entries[-1]["record"])
        self.assertNotIn("TEST_KEY_ONLY", str(result))
        self.assertTrue(all(b"TEST_KEY_ONLY" not in p.read_bytes() for p in self.root.rglob("*.json")))

    def test_quotes_kline_trading_fourth_tools_rejected_before_transport(self):
        for tool in ("tdx_quotes", "tdx_kline", "place_order", "account_query", "unknown"):
            self.assertEqual(self.provider.fetch(query(tool)).status, ProviderStatus.VALIDATION_FAILED)
        self.assertEqual(self.transport.calls, [])

    def test_generic_f10_cannot_escape_to_quote_or_arbitrary_fields(self):
        for args in ({"api_name": "tdx_quotes"}, {"api_name": "f10", "method": "tdx_quotes"},
                     {"api_name": "f10", "fields": "price"}, {"stock_code": "000001.SZ"}):
            request = query("tdx_api_data")
            request["arguments"].update(args)
            self.assertEqual(self.provider.fetch(request).status, ProviderStatus.VALIDATION_FAILED)
        self.assertEqual(self.transport.calls, [])

    def test_kind_scope_and_stock_validation(self):
        for changes in ({"data_kind": "realtime_quote"}, {"data_kind": "macro"},
                        {"stock_id": "bad"}, {"stock_id": ""}):
            self.assertEqual(self.provider.fetch(query(**changes)).status, ProviderStatus.VALIDATION_FAILED)
        self.assertEqual(self.provider.fetch(None).status, ProviderStatus.VALIDATION_FAILED)
        self.assertEqual(self.transport.calls, [])

    def test_same_query_has_one_effect_reordering_params_and_new_request_id(self):
        first = self.provider.fetch(query())
        file_count = len(list(self.root.rglob("*.json")))
        request = query(request_id="different_request")
        request["arguments"] = dict(reversed(list(request["arguments"].items())))
        second = self.provider.fetch(request)
        self.assertEqual(second, first)
        self.assertEqual(len(self.calls("tools/call")), 1)
        self.assertEqual(len(list(self.root.rglob("*.json"))), file_count)

    def test_cache_bound_to_tool_parameters_stock_and_business_day(self):
        first = self.provider.fetch(query())
        other_stock = query(stock_id="000001.SZ")
        other_stock["arguments"]["stock_code"] = "000001.SZ"
        requests = [query("wenda_news_query"), other_stock]
        changed = query()
        changed["arguments"]["bdate"] = "20260930"
        requests.append(changed)
        for request in requests:
            result = self.provider.fetch(request)
            self.assertNotEqual(result.payload["cache_key"], first.payload["cache_key"])
        self.clock.now += timedelta(days=1)
        next_day = self.provider.fetch(query())
        self.assertNotEqual(next_day.payload["cache_key"], first.payload["cache_key"])
        self.assertEqual(len(self.calls("tools/call")), 5)

    def test_caller_mutation_cannot_poison_cache(self):
        result = self.provider.fetch(query())
        result.payload["data"][0]["title"] = "changed"
        result.payload["evidence"].clear()
        second = self.provider.fetch(query())
        self.assertEqual(second.payload["data"], ROWS)
        self.assertEqual(len(second.payload["evidence"]), 3)

    def test_failures_not_cached_and_no_stale_fallback(self):
        self.provider.fetch(query())
        self.clock.now += timedelta(days=1)
        self.transport.hook = lambda rpc, headers: (_ for _ in ()).throw(TimeoutError())
        for _ in range(2):
            result = self.provider.fetch(query())
            self.assertEqual(result.status, ProviderStatus.TIMEOUT)
            self.assertEqual(result.payload["data"], [])
        self.assertEqual(len(self.calls("tools/call")), 2)

    def test_empty_data_has_evidence_no_fabrication_and_is_deduplicated(self):
        for data in ([], {}, None):
            self.transport.result = {"structuredContent": {"code": 0, "data": data}}
            request = query(request_id="empty")
            request["arguments"]["scenario"] = str(data)
            result = self.provider.fetch(request)
            self.assertEqual(result.status, ProviderStatus.NOT_AVAILABLE)
            self.assertFalse(result.payload["data"])
            self.assertTrue(result.payload["evidence"])
            count = len(self.calls("tools/call"))
            self.provider.fetch(request)
            self.assertEqual(len(self.calls("tools/call")), count)

    def test_malformed_json_rpc_preserves_exact_raw(self):
        for body in (b"broken JSON", b"[]", b'{}', b'{"jsonrpc":"2.0","id":"wrong","result":{}}'):
            self.transport.hook = lambda rpc, headers, body=body: MCPResponse(200, {}, body)
            result = self.provider.fetch(query())
            self.assertEqual(result.status, ProviderStatus.VALIDATION_FAILED)
            self.assertEqual(result.payload["evidence"][-1]["raw_bytes"], body)

    def test_rpc_error_and_mcp_iserror_are_not_data(self):
        def error(rpc, headers):
            if rpc["method"] == "tools/call":
                return MCPResponse(200, {}, encode({"jsonrpc": "2.0", "id": rpc["id"],
                                                    "error": {"code": -32603, "message": "mock"}}))
        self.transport.hook = error
        self.assertEqual(self.provider.fetch(query()).status, ProviderStatus.VALIDATION_FAILED)
        self.transport.hook = None
        self.transport.result = {"isError": True, "structuredContent": {"code": 0, "data": ROWS}}
        self.assertEqual(self.provider.fetch(query()).status, ProviderStatus.VALIDATION_FAILED)

    def test_session_expiration_reinitializes_exactly_once(self):
        expired = [False]
        def hook(rpc, headers):
            if rpc["method"] == "tools/call" and not expired[0]:
                expired[0] = True
                return MCPResponse(404, {}, b'{"error":"expired session"}')
        self.transport.hook = hook
        result = self.provider.fetch(query())
        self.assertEqual(result.status, ProviderStatus.AVAILABLE)
        self.assertEqual(self.transport.initializations, 2)
        self.assertEqual(len(self.calls("tools/call")), 2)
        self.assertEqual(len(result.payload["evidence"]), 6)
        self.assertEqual(self.calls("tools/call")[-1][1]["Mcp-Session-Id"], "mock-session-2")

    def test_repeated_session_expiration_times_out_without_loop(self):
        self.transport.hook = lambda rpc, headers: MCPResponse(404, {}, b"expired") if rpc["method"] == "tools/call" else None
        result = self.provider.fetch(query())
        self.assertEqual(result.status, ProviderStatus.TIMEOUT)
        self.assertEqual(self.transport.initializations, 2)
        self.assertEqual(len(self.calls("tools/call")), 2)

    def test_rehandshake_failure_retains_failure_status(self):
        initialized = [False]
        def hook(rpc, headers):
            if rpc["method"] == "tools/call":
                initialized[0] = True
                return MCPResponse(404, {}, b"expired")
            if initialized[0] and rpc["method"] == "initialize":
                return MCPResponse(401, {}, b"auth failed")
        self.transport.hook = hook
        self.assertEqual(self.provider.fetch(query()).status, ProviderStatus.AUTH_FAILED)
        self.assertEqual(len(self.calls("initialize")), 2)

    def test_business_dates_required_and_passthrough(self):
        for args in ({}, {"time_range": "latest"}, {"bdate": "20261002"},
                     {"bdate": "20261002", "edate": "20261001"},
                     {"bdate": "20260230", "edate": "20261002"}):
            self.assertEqual(self.provider.fetch(query(arguments=args)).status, ProviderStatus.VALIDATION_FAILED)
        self.assertEqual(self.transport.calls, [])
        args = {"stock_code": "601518.SH", "time_range": {"start": "2026-10-01", "end": "2026-10-02"}}
        result = self.provider.fetch(query(arguments=args))
        self.assertEqual(result.status, ProviderStatus.AVAILABLE)
        self.assertEqual(self.calls("tools/call")[0][0]["params"]["arguments"],
                         {"symbol": "601518", "time_range": args["time_range"]})

    def test_health_check_only_control_plane(self):
        self.assertEqual(self.provider.health_check(), ProviderStatus.AVAILABLE)
        self.assertEqual(self.calls("tools/call"), [])
        self.assertEqual(self.transport.initializations, 1)

    def test_metadata_preserves_three_sources(self):
        description = self.provider.describe()
        self.assertEqual(description["provider_name"], LOGICAL_SOURCE)
        self.assertEqual(description["actual_provider_service"], ACTUAL_PROVIDER_SERVICE)
        self.assertEqual(description["data_source_count"], 3)
        self.assertNotIn("realtime_quote", description["data_kinds"])
        self.assertTrue(description["read_only"])
        self.assertFalse(description["architecture_change"])

    def test_sensitive_input_and_response_echo_never_reaches_disk(self):
        for args in ({"api_key": "fake"}, {"nested": {"Authorization": "fake"}},
                     {"q": "TEST_KEY_ONLY"}):
            request = query()
            request["arguments"].update(args)
            self.assertEqual(self.provider.fetch(request).status, ProviderStatus.VALIDATION_FAILED)
        self.assertEqual(self.transport.calls, [])
        for body in (b'{"echo":"TEST_KEY_ONLY"}', b'{"echo":"\\u0054EST_KEY_ONLY"}'):
            self.transport.hook = lambda rpc, headers, body=body: MCPResponse(200, {}, body)
            result = self.provider.fetch(query())
            self.assertEqual(result.status, ProviderStatus.VALIDATION_FAILED)
            self.assertEqual(list(self.root.rglob("*.json")), [])
            self.assertNotIn("TEST_KEY_ONLY", str(result))

    def test_evidence_identifiers_cannot_escape_directory(self):
        for name in ("system_run_id", "collection_task_id", "request_id"):
            self.assertEqual(self.provider.fetch(query(**{name: "../escape"})).status, ProviderStatus.VALIDATION_FAILED)
        self.assertEqual(self.transport.calls, [])

    def test_discovered_missing_tool_not_available_without_call(self):
        def hook(rpc, headers):
            if rpc["method"] == "tools/list":
                return MCPResponse(200, {}, encode({"jsonrpc": "2.0", "id": rpc["id"], "result": {"tools": []}}))
        self.transport.hook = hook
        self.assertEqual(self.provider.fetch(query()).status, ProviderStatus.NOT_AVAILABLE)
        self.assertEqual(self.calls("tools/call"), [])

    def test_invalid_handshake_version_or_missing_session(self):
        for version, headers in (("invalid", {"Mcp-Session-Id": "mock"}), ("2025-03-26", {})):
            self.transport.hook = lambda rpc, h, version=version, headers=headers: MCPResponse(
                200, headers, encode({"jsonrpc": "2.0", "id": rpc["id"], "result": {"protocolVersion": version}}))
            self.assertEqual(self.provider.fetch(query()).status, ProviderStatus.VALIDATION_FAILED)

    def test_tools_list_pagination(self):
        def hook(rpc, headers):
            if rpc["method"] == "tools/list":
                page = ({"tools": [], "nextCursor": "page2"} if not rpc["params"]
                        else {"tools": [{"name": "wenda_report_query"}]})
                return MCPResponse(200, {}, encode({"jsonrpc": "2.0", "id": rpc["id"], "result": page}))
        self.transport.hook = hook
        self.assertEqual(self.provider.fetch(query()).status, ProviderStatus.AVAILABLE)
        self.assertEqual(len(self.calls("tools/list")), 2)
        self.assertEqual(self.calls("tools/list")[-1][0]["params"], {"cursor": "page2"})

    def test_sse_matching_response_and_raw_wire_retained(self):
        def hook(rpc, headers):
            if rpc["method"] == "tools/call":
                data = b': mock keepalive\r\n\r\ndata: {"jsonrpc":"2.0","method":"notifications/mock"}\r\n\r\n'
                data += b'data: ' + encode({"jsonrpc": "2.0", "id": rpc["id"], "result": self.transport.result}) + b'\r\n\r\n'
                return MCPResponse(200, {"Content-Type": "text/event-stream"}, data)
        self.transport.hook = hook
        result = self.provider.fetch(query())
        self.assertEqual(result.status, ProviderStatus.AVAILABLE)
        self.assertTrue(result.payload["evidence"][-1]["raw_bytes"].startswith(b": mock"))

    def test_response_size_limit_retains_bounded_raw_no_data(self):
        self.transport.hook = lambda rpc, headers: MCPResponse(200, {}, b"x" * (MAX_BODY_BYTES + 1))
        result = self.provider.fetch(query())
        self.assertEqual(result.status, ProviderStatus.VALIDATION_FAILED)
        self.assertEqual(len(result.payload["evidence"][-1]["raw_bytes"]), MAX_BODY_BYTES + 1)
        self.assertFalse(result.payload["evidence"][-1]["raw_response_complete"])

    def test_storage_failure_never_reports_success(self):
        with patch("pathlib.Path.open", side_effect=OSError("TEST_KEY_ONLY")):
            result = self.provider.fetch(query())
        self.assertEqual(result.status, ProviderStatus.NOT_AVAILABLE)
        self.assertEqual(result.payload["data"], [])
        self.assertNotIn("TEST_KEY_ONLY", str(result))

    def test_bounded_cache_evicts_and_next_day_clears(self):
        with patch("providers.tdx_information.MAX_CACHE_ENTRIES", 2):
            for value in range(3):
                request = query()
                request["arguments"]["q"] = str(value)
                self.provider.fetch(request)
            self.assertEqual(len(self.provider._cache), 2)
            request["arguments"]["q"] = "0"
            self.provider.fetch(request)
            self.assertEqual(len(self.calls("tools/call")), 4)
        self.clock.now += timedelta(days=1)
        self.provider.fetch(query())
        self.assertEqual(len(self.provider._cache), 1)

    def test_ambiguous_time_ranges_and_stock_mismatch_are_rejected(self):
        for update in ({"time_range": {"start": "20261001", "end": "20261002"}},
                       {"stock_code": "000001.SZ"}):
            request = query()
            request["arguments"].update(update)
            self.assertEqual(self.provider.fetch(request).status, ProviderStatus.VALIDATION_FAILED)
        self.assertEqual(self.transport.calls, [])

    def test_duplicate_json_fields_and_nonfinite_numbers_are_raw_only(self):
        for body in (b'{"jsonrpc":"2.0","id":1,"id":2,"result":{}}',
                     b'{"jsonrpc":"2.0","result":NaN}'):
            self.transport.hook = lambda rpc, headers, body=body: MCPResponse(200, {}, body)
            result = self.provider.fetch(query())
            self.assertEqual(result.status, ProviderStatus.VALIDATION_FAILED)
            self.assertEqual(result.payload["evidence"][-1]["raw_bytes"], body)

    def test_health_failure_statuses_and_control_plane_evidence_persisted(self):
        for code, status in ((401, ProviderStatus.AUTH_FAILED), (503, ProviderStatus.NOT_AVAILABLE),
                             (504, ProviderStatus.TIMEOUT)):
            self.transport.hook = lambda rpc, headers, code=code: MCPResponse(code, {}, b"mock failure")
            self.assertEqual(self.provider.health_check(), status)
        self.assertEqual(self.calls("tools/call"), [])
        self.assertEqual(len(list(self.root.rglob("*.evidence.json"))), 3)

    def test_byte_budget_skips_cache_for_large_queries(self):
        with patch("providers.tdx_information.MAX_CACHE_BYTES", 1):
            for _ in range(2):
                self.assertEqual(self.provider.fetch(query()).status, ProviderStatus.AVAILABLE)
        self.assertEqual(len(self.calls("tools/call")), 2)
        self.assertEqual(len(self.provider._cache), 0)

    def test_pagination_cycle_has_finite_limit(self):
        def hook(rpc, headers):
            if rpc["method"] == "tools/list":
                return MCPResponse(200, {}, encode({"jsonrpc": "2.0", "id": rpc["id"],
                                                   "result": {"tools": [], "nextCursor": "same"}}))
        self.transport.hook = hook
        self.assertEqual(self.provider.fetch(query()).status, ProviderStatus.VALIDATION_FAILED)
        self.assertEqual(len(self.calls("tools/list")), 8)

    def test_rpc_result_and_error_cannot_coexist(self):
        def hook(rpc, headers):
            return MCPResponse(200, {}, encode({"jsonrpc": "2.0", "id": rpc["id"],
                                               "result": {}, "error": {"code": -1}}))
        self.transport.hook = hook
        self.assertEqual(self.provider.fetch(query()).status, ProviderStatus.VALIDATION_FAILED)

    def test_outer_business_error_cannot_hide_in_success_structured_content(self):
        self.transport.result = {"code": 1, "structuredContent": {"code": 0, "data": ROWS}}
        result = self.provider.fetch(query())
        self.assertEqual(result.status, ProviderStatus.VALIDATION_FAILED)
        self.assertEqual(result.payload["data"], [])

    def test_empty_http_body_still_has_raw_hash_and_failed_validation(self):
        self.transport.hook = lambda rpc, headers: MCPResponse(200, {}, b"")
        result = self.provider.fetch(query())
        self.assertEqual(result.status, ProviderStatus.VALIDATION_FAILED)
        record = result.payload["evidence"][-1]["record"]
        self.assertEqual(Path(record["raw_response_path"]).read_bytes(), b"")
        self.assertEqual(record["raw_response_hash"], hashlib.sha256(b"").hexdigest())

    def test_timeout_configuration_rejects_invalid_values(self):
        for value in (0, -1, True, float("nan"), float("inf"), "30"):
            with self.assertRaisesRegex(ValueError, "timeout"):
                TdxInformationProvider(self.transport, evidence_root=self.root,
                                       environ=TEST_ENV, timeout=value)


class TransportTests(unittest.TestCase):
    def test_urllib_request_endpoint_auth_deadline_and_bound_mocked(self):
        with patch("providers.tdx_information.build_opener") as factory:
            response = MagicMock()
            response.code = 200
            response.headers.items.return_value = [("Content-Type", "application/json")]
            response.read.return_value = b"{}"
            response.__enter__.return_value = response
            factory.return_value.open.return_value = response
            transport = MCPHTTPTransport()
            result = transport.post(b"{}", headers={"Authorization": "Bearer TEST_KEY_ONLY"}, timeout=3)
            request = factory.return_value.open.call_args.args[0]
            self.assertEqual(request.full_url, ENDPOINT)
            self.assertEqual(request.get_method(), "POST")
            self.assertEqual(request.get_header("Authorization"), "Bearer TEST_KEY_ONLY")
            self.assertEqual(factory.return_value.open.call_args.kwargs, {"timeout": 3})
            response.read.assert_called_once_with(MAX_BODY_BYTES + 1)
            self.assertEqual(result.body, b"{}")
            self.assertIsInstance(factory.call_args.args[0], NoRedirect)

    def test_urllib_http_error_retains_status_headers_and_body(self):
        with patch("providers.tdx_information.build_opener") as factory:
            factory.return_value.open.side_effect = HTTPError(ENDPOINT, 403, "mock", {}, io.BytesIO(b"mock auth error"))
            result = MCPHTTPTransport().post(b"{}", headers={}, timeout=3)
            self.assertEqual(result.status, 403)
            self.assertEqual(result.body, b"mock auth error")

    def test_redirects_are_refused_before_bearer_forwarding(self):
        self.assertIsNone(NoRedirect().redirect_request(None, None, 302, "", {}, "https://example.invalid"))


if __name__ == "__main__":
    unittest.main()
