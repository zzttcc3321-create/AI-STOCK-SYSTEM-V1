"""Offline financial trace and fail-closed binding tests with real archives."""

from copy import deepcopy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from evidence import ArchiveWriter
from normalizer import InformationNormalizer
from providers import ProviderStatus
from providers.tdx_information import F10_VIEWS, MCPResponse, TdxInformationProvider
from test_information_normalizer import information_input
from test_tdx_information import Clock, MockTransport, TEST_ENV, query
import tools.e2_windows_verify as harness


STOCK = "601518.SH"
WIRE_ARGUMENTS = {
    "profit": {"code": "601518", "entry": "TdxShareCW.ph_agf10_cw_lyb", "fixedTag": "00101"},
    "balance": {"code": "601518", "entry": "TdxShareCW.ph_agf10_cw_zcfzb"},
    "cashflow": {"code": "601518", "entry": "TdxShareCW.ph_agf10_cw_xjllb", "fixedTag": "00101"},
}


class FinancialRequestTraceTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.run_dir = Path(temporary.name) / "run"
        self.writer = ArchiveWriter(self.run_dir / "phase_c")
        self.transport = MockTransport()
        self.provider = TdxInformationProvider(
            self.transport, evidence_root=self.run_dir / "phase_c",
            archive_writer=self.writer, environ=TEST_ENV, clock=Clock())
        # These tests permit only the mock transport and temporary archives.
        for target in ("socket.socket", "socket.create_connection"):
            guard = patch(target, side_effect=AssertionError("real network forbidden"))
            guard.start()
            self.addCleanup(guard.stop)

    def _assert_trace(self, view):
        request = query("tdx_api_data", arguments={"view": view})
        before = deepcopy(request)
        result = self.provider.fetch(request)
        self.assertEqual(result.status, ProviderStatus.AVAILABLE)
        self.assertEqual(request, before)
        back = self.writer.reader.read(result.raw_reference)
        self.assertEqual(back["integrity_status"], "VERIFIED")
        params = back["metadata"]["record"]["request_params"]
        self.assertEqual(params["semantic_request"], {
            "method": "tools/call", "stock_id": STOCK,
            "params": {"name": "tdx_api_data", "arguments": {"view": view}},
        })
        self.assertEqual(params["provider_request"], {
            "method": "tools/call", "stock_id": STOCK,
            "params": {"name": "tdx_api_data", "arguments": WIRE_ARGUMENTS[view]},
        })
        self.assertEqual(params["params"], params["provider_request"]["params"])
        self.assertEqual(params["request_day"], result.payload["request_day"])
        self.assertEqual(params, result.payload["evidence"][-1]["record"]["request_params"])
        wire = self.transport.calls[-1][0]
        self.assertEqual(wire["params"], params["provider_request"]["params"])
        self.assertNotIn("semantic_request", wire)
        self.assertEqual(back["raw_parts"]["response"], self.transport.raw_bodies[-1])
        return result, back

    def test_profit_translation_is_traceable_in_sealed_archive(self):
        self._assert_trace("profit")

    def test_balance_translation_is_traceable_without_fixed_tag(self):
        self._assert_trace("balance")

    def test_cashflow_translation_is_traceable_in_sealed_archive(self):
        self._assert_trace("cashflow")

    def test_financial_normalizer_accepts_trace_and_preserves_data_semantics(self):
        _, _, raw = information_input("tdx_api_data", view="profit")
        self.transport.result = json.loads(raw)["result"]
        captured = []
        write = self.writer.write

        def capture(**kwargs):
            receipt = write(**kwargs)
            captured.append(receipt["record"])
            return receipt

        with patch.object(self.writer, "write", side_effect=capture):
            result, back = self._assert_trace("profit")
        # Normalize with the actual frozen EvidenceRecord returned by Writer.
        # No persistence or database is involved.
        source = captured[-1]
        self.writer.reader.verify_record(source)
        normalized = InformationNormalizer().normalize(
            result, source_evidence=source, raw_bytes=back["raw_parts"]["response"])
        self.assertEqual(normalized.canonical_data[0]["statement_view"], "profit")
        self.assertEqual(normalized.provenance["request_params"], source.request_params)
        self.assertEqual(result.payload["data"], self.transport.result["structuredContent"]["data"])

    def _phase_c(self, mutation=None):
        # Each invocation collects fresh evidence, including subtest fixtures.
        provider = TdxInformationProvider(
            self.transport, evidence_root=self.run_dir / "phase_c",
            archive_writer=self.writer, environ=TEST_ENV, clock=Clock())
        write = self.writer.write

        def write_fixture(**kwargs):
            params = kwargs["request_params"]
            if (mutation is not None and params.get("method") == "tools/call"
                    and params.get("params", {}).get("name") == "tdx_api_data"):
                # Alter the fixture before sealing so integrity still verifies.
                params = deepcopy(params)
                mutation(params)
                kwargs["request_params"] = params
            return write(**kwargs)

        with patch.dict("os.environ", TEST_ENV), \
                patch.object(harness, "TdxInformationProvider", return_value=provider), \
                patch.object(self.writer, "write", side_effect=write_fixture):
            return harness.phase_c_real_info(self.run_dir, STOCK, "20260911", "20261011")

    def _assert_rejected(self, mutation):
        summary = self._phase_c(mutation)
        self.assertEqual(summary["result"], "FAIL")
        for view in WIRE_ARGUMENTS:
            call = summary["calls"]["financial_" + view]
            self.assertEqual(call["archive_integrity"], "VERIFIED")
            self.assertFalse(call["binding_verified"])
            self.assertFalse(call["archive_verified"])
        self.assertTrue(summary["calls"]["announcement"]["binding_verified"])
        return summary

    def test_phase_c_accepts_all_three_verified_financial_mappings(self):
        summary = self._phase_c()
        self.assertEqual(summary["result"], "PASS")
        for view in WIRE_ARGUMENTS:
            call = summary["calls"]["financial_" + view]
            self.assertTrue(call["binding_verified"])
            self.assertTrue(call["binding_semantic_ok"])
            self.assertTrue(call["binding_provider_ok"])
            self.assertTrue(call["binding_mapping_ok"])

    def test_profit_semantic_with_balance_wire_mapping_is_rejected(self):
        # Simulate a provider translation defect, not a corrupt archive.
        with patch.dict(F10_VIEWS, {"profit": ("TdxShareCW.ph_agf10_cw_zcfzb", None)}):
            summary = self._phase_c()
        call = summary["calls"]["financial_profit"]
        self.assertEqual(call["archive_integrity"], "VERIFIED")
        self.assertTrue(call["binding_semantic_ok"])
        self.assertFalse(call["binding_mapping_ok"])
        self.assertFalse(call["binding_verified"])
        self.assertEqual(summary["result"], "FAIL")

    def test_missing_semantic_trace_is_rejected(self):
        self._assert_rejected(lambda p: p.pop("semantic_request", None))

    def test_missing_provider_trace_is_rejected(self):
        self._assert_rejected(lambda p: p.pop("provider_request", None))

    def test_wrong_semantic_view_is_rejected(self):
        self._assert_rejected(lambda p: p["semantic_request"]["params"]["arguments"].update(view="company"))

    def test_wrong_semantic_stock_is_rejected(self):
        self._assert_rejected(lambda p: p["semantic_request"].update(stock_id="000001.SZ"))

    def test_wrong_semantic_method_is_rejected(self):
        self._assert_rejected(lambda p: p["semantic_request"].update(method="tools/list"))

    def test_wrong_semantic_tool_is_rejected(self):
        self._assert_rejected(lambda p: p["semantic_request"]["params"].update(name="wenda_news_query"))

    def test_wrong_provider_stock_is_rejected(self):
        self._assert_rejected(lambda p: p["provider_request"].update(stock_id="000001.SZ"))

    def test_conflicting_provider_copy_is_rejected(self):
        def mutation(params):
            params["provider_request"] = deepcopy(params["provider_request"])
            params["provider_request"]["params"]["arguments"]["entry"] = "OTHER_ENTRY"
        self._assert_rejected(mutation)

    def test_wrong_wire_stock_code_is_rejected(self):
        self._assert_rejected(lambda p: p["params"]["arguments"].update(code="000001"))

    def test_wrong_fixed_tag_and_balance_extra_fixed_tag_are_rejected(self):
        self._assert_rejected(lambda p: p["params"]["arguments"].update(fixedTag="WRONG_TAG"))

    def test_missing_fixed_tag_is_rejected_for_profit_and_cashflow(self):
        summary = self._phase_c(lambda p: p["params"]["arguments"].pop("fixedTag", None))
        self.assertEqual(summary["result"], "FAIL")
        self.assertFalse(summary["calls"]["financial_profit"]["binding_verified"])
        self.assertFalse(summary["calls"]["financial_cashflow"]["binding_verified"])
        self.assertTrue(summary["calls"]["financial_balance"]["binding_verified"])

    def test_extra_wire_argument_is_rejected(self):
        self._assert_rejected(lambda p: p["params"]["arguments"].update(view="profit"))

    def test_malformed_trace_is_rejected(self):
        for malformed in (None, [], "profit", {"params": []}):
            with self.subTest(malformed=malformed):
                self._assert_rejected(lambda p: p.update(semantic_request=malformed))

    def test_cached_financial_result_preserves_trace(self):
        result, back = self._assert_trace("profit")
        count = len(self.transport.calls)
        again = self.provider.fetch(query("tdx_api_data"))
        self.assertEqual(len(self.transport.calls), count)
        self.assertEqual(again.raw_reference, result.raw_reference)
        self.assertEqual(again.payload["evidence"][-1]["record"]["request_params"],
                         back["metadata"]["record"]["request_params"])

    def test_session_retry_preserves_financial_semantic_trace(self):
        def expire_once(rpc, headers):
            if rpc["method"] == "tools/call" and self.transport.initializations == 1:
                return MCPResponse(404, {}, b"session expired")
        self.transport.hook = expire_once
        result, _ = self._assert_trace("profit")
        calls = [e for e in result.payload["evidence"]
                 if e["record"]["request_params"]["method"] == "tools/call"]
        self.assertEqual(len(calls), 2)
        for entry in calls:
            params = entry["record"]["request_params"]
            self.assertEqual(params["semantic_request"]["params"]["arguments"], {"view": "profit"})
            self.assertEqual(params["provider_request"]["params"]["arguments"], WIRE_ARGUMENTS["profit"])

    def test_empty_financial_response_still_has_verifiable_trace(self):
        self.transport.result = {"content": [{"type": "text", "text": '{"data": []}'}]}
        summary = self._phase_c()
        self.assertEqual(summary["result"], "PASS")
        for view in WIRE_ARGUMENTS:
            call = summary["calls"]["financial_" + view]
            self.assertEqual(call["status"], "NOT_AVAILABLE")
            self.assertTrue(call["binding_verified"])
