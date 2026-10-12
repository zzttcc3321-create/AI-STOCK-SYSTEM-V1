"""Offline verification of the Windows runner using real filesystem sidecars.

All business inputs are synthetic; none certifies Windows REAL execution.
No network or database connections are used.
"""
from dataclasses import replace
from pathlib import Path
import json
import tempfile
import unittest
from unittest.mock import patch

from evidence import ArchiveError, ArchiveIdentity, ArchiveReader, ArchiveWriter, EvidenceBuilder
from evidence.archive_contracts import json_hash, plain, sealed, sha256, unseal
from normalizer import InformationNormalizer
from providers import ProviderStatus
from test_information_normalizer import information_input
from tools import e2_windows_normalized_verify as harness


class NormalizedWindowsVerificationTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        # Real file flush/fsync + link semantics, portable to Windows.
        self.writer = ArchiveWriter(self.root / "archive", durability_level="FILE_SYNC_ONLY")

    def envelope(self, tool="wenda_news_query", *, view=None, data=None, raw_id="raw001"):
        envelope, original, raw = information_input(tool, view=view, data=data)
        identity = ArchiveIdentity("run008", "task008", "request", raw_id)
        receipt = self.writer.write(identity=identity, source=original.source,
            raw_parts={"response": raw}, endpoint_or_tool=original.endpoint_or_tool,
            request_params=original.request_params, request_timestamp=original.request_timestamp)
        return replace(envelope, raw_reference=identity.uri, payload={**envelope.payload,
            "evidence": [{"record": plain(receipt["record"]), "raw_bytes": raw}]}), receipt["record"], raw

    def verify(self, envelope):
        return harness.verify_information(envelope, self.writer, canonical_id="CANONICAL-001")

    def test_normalize_and_canonical_record_are_deterministic(self):
        envelope, original, raw = self.envelope()
        first = InformationNormalizer().normalize(envelope, source_evidence=original, raw_bytes=raw)
        second = InformationNormalizer().normalize(envelope, source_evidence=original, raw_bytes=raw)
        self.assertEqual(plain(first), plain(second))
        records = [EvidenceBuilder().build(fetch_result=envelope,
            endpoint_or_tool=original.endpoint_or_tool, request_params=original.request_params,
            system_run_id=original.system_run_id, collection_task_id=original.collection_task_id,
            normalization_result=result, raw_response_hash=sha256(raw), evidence_id="CANONICAL-001")
            for result in (first, second)]
        self.assertEqual(records[0], records[1])
        self.assertEqual(records[0].normalized_data_hash, json_hash(first.canonical_data))
        self.assertNotEqual(records[0].canonical_id, records[0].raw_reference_id)

    def test_all_six_categories_verify_three_independent_stages(self):
        cases = [("wenda_notice_query", None), ("wenda_news_query", None), ("wenda_report_query", None),
                 *[("tdx_api_data", view) for view in ("profit", "balance", "cashflow")]]
        for n, (tool, view) in enumerate(cases):
            with self.subTest(tool=tool, view=view):
                envelope, original, raw = self.envelope(tool, view=view, raw_id="raw" + str(n))
                report = self.verify(envelope)
                self.assertEqual(report["result"], "PASS", report)
                self.assertTrue(report["normalization_deterministic"])
                for stage in report["stages"].values():
                    self.assertTrue(stage["verified"])
                    self.assertEqual(stage["durability_level"], "FILE_SYNC_ONLY")
                reader = ArchiveReader(self.writer.resolver.root)
                canonical = reader.evidence(envelope.raw_reference, "CANONICAL-001")
                self.assertEqual(canonical["canonical_id"], "CANONICAL-001")
                self.assertEqual(canonical["raw_reference_id"], "raw" + str(n))
                self.assertIsNone(canonical["supersedes_evidence_id"])
                self.assertEqual(canonical["raw_response_hash"], sha256(raw))
                normalized = reader.normalized(envelope.raw_reference, canonical["normalized_data_hash"])
                expected = InformationNormalizer().normalize(envelope, source_evidence=original, raw_bytes=raw)
                self.assertEqual(normalized["normalized_data"], plain(expected.canonical_data))
                self.assertEqual(json_hash(normalized["normalized_data"]), canonical["normalized_data_hash"])
                self.assertEqual(report["stages"]["canonical_evidence"]["record_hash"], json_hash(canonical))

    def test_retry_preserves_original_raw_and_all_committed_sidecars(self):
        envelope, _, _ = self.envelope()
        self.assertEqual(self.verify(envelope)["result"], "PASS")
        directory = self.writer.resolver.resolve(envelope.raw_reference)
        before = {p.name: (p.read_bytes(), p.stat().st_mtime_ns) for p in directory.iterdir()}
        report = self.verify(envelope)
        self.assertEqual(report["result"], "PASS", report)
        self.assertEqual(before, {p.name: (p.read_bytes(), p.stat().st_mtime_ns) for p in directory.iterdir()})

    def test_empty_data_is_incomplete_and_never_archives_a_pass_canonical(self):
        envelope, _, _ = self.envelope(data=[])
        report = self.verify(envelope)
        self.assertEqual(report["result"], "INCOMPLETE")
        self.assertEqual(report["error_code"], "NO_CANONICAL_CONTENT")
        self.assertTrue(report["stages"]["raw"]["verified"])
        self.assertFalse(report["stages"]["canonical_evidence"]["verified"])
        directory = self.writer.resolver.resolve(envelope.raw_reference)
        self.assertEqual(list(directory.glob("evidence-*.json")), [])

    def test_mismatched_provider_content_fails_after_raw_read_succeeds(self):
        envelope, _, _ = self.envelope()
        changed = replace(envelope, payload={**envelope.payload, "data": [["标题"], ["篡改"]]})
        report = self.verify(changed)
        self.assertEqual(report["result"], "FAIL")
        self.assertTrue(report["stages"]["raw"]["verified"])
        self.assertFalse(report["stages"]["normalized"]["verified"])

    def test_mismatched_envelope_identity_or_clocks_fail_closed(self):
        envelope, _, _ = self.envelope()
        for change in (
            {"raw_reference": "archive://v1/run008/task008/other/raw001"},
            {"request_timestamp": "2026-10-04T11:27:00+08:00"},
            {"payload": {**envelope.payload, "stock_id": "000001.SZ"}},
        ):
            with self.subTest(change=list(change)):
                self.assertEqual(self.verify(replace(envelope, **change))["result"], "FAIL")

    def test_provider_raw_bytes_mismatch_fails_closed(self):
        envelope, _, raw = self.envelope()
        entry = envelope.payload["evidence"][0]
        changed = replace(envelope, payload={**envelope.payload,
            "evidence": [{**entry, "raw_bytes": raw + b" "}]})
        self.assertEqual(self.verify(changed)["result"], "FAIL")

    def test_corrupt_raw_never_reaches_normalization(self):
        envelope, _, _ = self.envelope()
        directory = self.writer.resolver.resolve(envelope.raw_reference)
        (directory / "response.raw.gz").write_bytes(b"corrupt")
        with patch.object(harness, "InformationNormalizer") as normalizer:
            report = self.verify(envelope)
        self.assertEqual(report["result"], "FAIL")
        self.assertFalse(report["stages"]["raw"]["verified"])
        normalizer.assert_not_called()

    def test_tampered_or_mismatched_normalized_sidecar_does_not_pass_raw_gate(self):
        envelope, _, _ = self.envelope()
        self.assertEqual(self.verify(envelope)["result"], "PASS")
        directory = self.writer.resolver.resolve(envelope.raw_reference)
        path = next(directory.glob("normalized-*.json"))
        original = path.read_bytes()
        for mode in ("hash", "content", "rules", "validation", "durability"):
            with self.subTest(mode=mode):
                payload = unseal(original)
                if mode == "hash":
                    altered = json.loads(original)
                    altered["sha256"] = "0" * 64
                    from evidence.archive_contracts import canonical_json
                    path.write_bytes(canonical_json(altered))
                else:
                    if mode == "content": payload["normalized_data"][0]["title"] = "changed"
                    if mode == "rules": payload["normalization_rule"] = ["wrong"]
                    if mode == "validation": payload["validation_result"] = {"status": "FAIL", "failures": []}
                    if mode == "durability": payload.pop("durability_level")
                    path.write_bytes(sealed(payload))
                self.assertEqual(ArchiveReader(self.writer.resolver.root).read(envelope.raw_reference)["integrity_status"],
                                 "VERIFIED")
                self.assertEqual(self.verify(envelope)["result"], "FAIL")
                path.write_bytes(original)

    def test_canonical_tampering_and_missing_durability_fail_independently(self):
        envelope, _, _ = self.envelope()
        self.assertEqual(self.verify(envelope)["result"], "PASS")
        directory = self.writer.resolver.resolve(envelope.raw_reference)
        path = next(directory.glob("evidence-*.json"))
        original = path.read_bytes()
        for mode in ("identity", "hash", "durability"):
            with self.subTest(mode=mode):
                payload = unseal(original)
                if mode == "identity": payload["record"]["raw_reference_id"] = "other"
                if mode == "hash": payload["record"]["normalized_data_hash"] = "0" * 64
                if mode == "durability": payload.pop("durability_level")
                path.write_bytes(sealed(payload))
                reader = ArchiveReader(self.writer.resolver.root)
                self.assertEqual(reader.read(envelope.raw_reference)["integrity_status"], "VERIFIED")
                with self.assertRaises(ArchiveError):
                    reader.evidence(envelope.raw_reference, "CANONICAL-001")
                report = self.verify(envelope)
                self.assertEqual(report["result"], "FAIL")
                self.assertTrue(report["stages"]["normalized"]["verified"])
                self.assertFalse(report["stages"]["canonical_evidence"]["verified"])
                path.write_bytes(original)

    def test_canonical_missing_can_never_be_inferred_from_raw_success(self):
        envelope, _, _ = self.envelope()
        report = self.verify(envelope)
        self.assertEqual(report["result"], "PASS")
        next(self.writer.resolver.resolve(envelope.raw_reference).glob("evidence-*.json")).unlink()
        reader = ArchiveReader(self.writer.resolver.root)
        self.assertEqual(reader.read(envelope.raw_reference)["integrity_status"], "VERIFIED")
        with self.assertRaises(ArchiveError):
            reader.evidence(envelope.raw_reference, "CANONICAL-001")

    def test_append_file_sync_failure_fails_closed_without_secret_logging(self):
        envelope, _, _ = self.envelope()
        with patch("evidence.archive_writer.os.fsync", side_effect=OSError("private exception text")):
            report = self.verify(envelope)
        self.assertEqual(report["result"], "FAIL")
        self.assertTrue(report["stages"]["raw"]["verified"])
        self.assertFalse(report["stages"]["normalized"]["verified"])
        self.assertNotIn("private exception text", json.dumps(report))

    def test_runner_executes_only_information_chain_and_requires_all_calls(self):
        envelope, _, _ = self.envelope()
        with patch.dict("os.environ", {"TDX_MCP_API_KEY": "offline-test-key"}), \
             patch.object(harness, "TdxInformationProvider") as provider, \
             patch.object(harness, "verify_information", side_effect=[
                 {"result": "PASS"}] * 5 + [{"result": "INCOMPLETE"}]):
            provider.return_value.fetch.return_value = envelope
            report = harness.real_information(self.root, "601518.SH", "20260911", "20261011")
        self.assertEqual(provider.return_value.fetch.call_count, 6)
        self.assertEqual(set(report["calls"]), {"announcement", "news", "research",
            "financial_profit", "financial_balance", "financial_cashflow"})
        self.assertEqual(report["result"], "INCOMPLETE")

    def test_main_missing_auth_reports_incomplete_without_provider_or_db(self):
        target = self.root / "new-run"
        with patch.dict("os.environ", {}, clear=True), \
             patch.object(harness, "TdxInformationProvider") as provider, \
             patch("sqlite3.connect", side_effect=AssertionError("database forbidden")):
            code = harness.main(["--output-dir", str(target), "--date-from", "20260911", "--date-to", "20261011"])
        report = json.loads((target / "normalized_verify_report.json").read_bytes())
        self.assertEqual(code, 2)
        self.assertEqual(report["result"], "BLOCKED_AUTH")
        self.assertEqual(report["REAL_DB_WRITE"], "NO")
        self.assertFalse(report["windows_real_chain_verified"])
        provider.assert_not_called()

    def test_main_exception_produces_sanitized_failure_report(self):
        target = self.root / "new-run"
        with patch.object(harness, "real_information", side_effect=RuntimeError("private exception text")):
            code = harness.main(["--output-dir", str(target), "--date-from", "20260911", "--date-to", "20261011"])
        data = (target / "normalized_verify_report.json").read_text()
        self.assertEqual(code, 1)
        self.assertNotIn("private exception text", data)
        self.assertEqual(json.loads(data)["error_type"], "RuntimeError")

    def test_linux_pass_does_not_certify_windows_real_execution(self):
        target = self.root / "new-run"
        with patch.object(harness, "real_information", return_value={"result": "PASS", "calls": {}}), \
             patch.object(harness.sys, "platform", "linux"):
            self.assertEqual(harness.main(["--output-dir", str(target), "--date-from", "20260911",
                                          "--date-to", "20261011"]), 0)
        report = json.loads((target / "normalized_verify_report.json").read_bytes())
        self.assertFalse(report["windows_real_chain_verified"])


if __name__ == "__main__":
    unittest.main()
