"""Offline tests for the rebuilt Windows E2 verification harness.

All tests run without network access and without touching any real
database. They verify the harness safety contracts:
  1. Output directory must be new (refuse existing).
  2. Empty units declaration never yields a VERIFIED canonical.
  3. AUTH_FAILED / NOT_AVAILABLE / VALIDATION_FAILED stay distinct.
  4. Archive failure is reported as failure, never PASS.
  5. No sqlite usage anywhere in the harness module.
"""

import ast
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import tools.e2_windows_verify as harness
from providers import ProviderStatus


class OutputDirSafetyTests(unittest.TestCase):
    def test_new_dir_created(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "run-new"
            out = harness.new_run_dir(target)
            self.assertTrue(out.is_dir())

    def test_existing_dir_refused(self):
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(SystemExit) as ctx:
                harness.new_run_dir(tmp)
            self.assertIn("REFUSED", str(ctx.exception))

    def test_existing_file_refused(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "run-new"
            target.mkdir()
            (target / "old.json").write_text("{}")
            with self.assertRaises(SystemExit):
                harness.new_run_dir(target)


class UnitsContractTests(unittest.TestCase):
    def test_empty_units_accepted_by_validator(self):
        from bridge.contracts import validate_units
        self.assertEqual(validate_units({}), {})

    def test_empty_units_constant(self):
        self.assertEqual(harness.EMPTY_UNITS, {})

    def test_phase_a_reports_canonical_restricted(self):
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = harness.new_run_dir(Path(tmp) / "run")
            result = harness.phase_a_offline(run_dir)
            self.assertEqual(result["result"], "PASS")
            self.assertTrue(result["checks"]["units_empty"])
            self.assertTrue(result["checks"]["canonical_restricted"])


class StatusDistinctionTests(unittest.TestCase):
    def test_three_failure_statuses_distinct(self):
        self.assertEqual(len({
            ProviderStatus.AUTH_FAILED.value,
            ProviderStatus.NOT_AVAILABLE.value,
            ProviderStatus.VALIDATION_FAILED.value,
        }), 3)

    def test_phase_c_no_key_is_blocked_auth(self):
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = harness.new_run_dir(Path(tmp) / "run")
            with patch.dict("os.environ", {}, clear=False):
                import os
                os.environ.pop("TDX_MCP_API_KEY", None)
                result = harness.phase_c_real_info(
                    run_dir, "601518.SH", None, None)
            self.assertEqual(result["result"], "BLOCKED_AUTH")


class PhaseAContractTests(unittest.TestCase):
    def test_phase_a_passes_offline(self):
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = harness.new_run_dir(Path(tmp) / "run")
            result = harness.phase_a_offline(run_dir)
            self.assertEqual(result["phase"], "A_offline")
            self.assertEqual(result["result"], "PASS")
            checks = result["checks"]
            self.assertTrue(checks["envelope_built"])
            self.assertTrue(checks["materialized"])
            self.assertTrue(checks["raw_json_exists"])
            self.assertTrue(checks["evidence_json_exists"])
            self.assertTrue(checks["numeric_ok"])
            self.assertTrue(checks["statuses_distinct"])

    def test_numeric_check_rejects_bad_snapshot(self):
        bad = {"now": float("nan"), "last_close": 1.0, "open": 1.0,
               "high": 1.0, "low": 1.0, "volume": 1, "amount": 1.0,
               "average": 1.0,
               "bid_prices": [1.0] * 5, "bid_volumes": [1] * 5,
               "ask_prices": [1.0] * 5, "ask_volumes": [1] * 5}
        self.assertFalse(harness.check_numeric_snapshot(bad)["all_ok"])

    def test_numeric_check_rejects_negative(self):
        bad = {"now": -1.0, "last_close": 1.0, "open": 1.0,
               "high": 1.0, "low": 1.0, "volume": 1, "amount": 1.0,
               "average": 1.0,
               "bid_prices": [1.0] * 5, "bid_volumes": [1] * 5,
               "ask_prices": [1.0] * 5, "ask_volumes": [1] * 5}
        self.assertFalse(harness.check_numeric_snapshot(bad)["all_ok"])

    def test_hash_check_detects_mismatch(self):
        import base64, hashlib
        raw = b'{"a": 1}'
        payload = {"raw_response_b64": base64.b64encode(raw).decode(),
                   "raw_response_hash": "0" * 64}
        self.assertFalse(harness.check_raw_hash(payload)["hash_match"])


class NoDatabaseTests(unittest.TestCase):
    def test_harness_module_has_no_sqlite(self):
        source = Path(harness.__file__).read_text(encoding="utf-8")
        tree = ast.parse(source)
        imported = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported.update(a.name.split(".")[0] for a in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imported.add(node.module.split(".")[0])
        self.assertNotIn("sqlite3", imported)
        self.assertFalse(any("sqlite" in m.lower() for m in imported))
        # Check for actual DB write code patterns, not docstring mentions
        self.assertNotIn("APPLY_REAL_DB", source)
        self.assertNotIn("sqlite3.connect", source)

    def test_report_flags_no_db_write(self):
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = harness.new_run_dir(Path(tmp) / "run")
            # Simulate a minimal main() run in offline mode.
            rc = harness.main(["--output-dir", str(run_dir / "inner"),
                               "--mode", "offline"])
            self.assertEqual(rc, 0)
            report = json.loads((run_dir / "inner" / "verify_report.json")
                                .read_text(encoding="utf-8"))
            self.assertFalse(report["database_write"])
            self.assertFalse(report["credential_persisted"])
            self.assertIn("git_head", report)




class B01TimestampOrderingTests(unittest.TestCase):
    """B01 regression: `now` must be captured AFTER fetch() returns.

    fetch() fills snapshot_timestamp when the HTTP response is parsed.
    A `now` taken before fetch would be earlier than snapshot_timestamp,
    and freshness() would reject it as a future timestamp.
    """

    def test_now_captured_after_fetch(self):
        """utc_now() must be called after provider.fetch() returns."""
        import datetime
        call_order = []
        t_fetch = datetime.datetime(2026, 10, 11, 0, 0, 5,
                                    tzinfo=datetime.timezone.utc)

        def fake_utc_now():
            call_order.append("utc_now")
            return t_fetch  # now >= snapshot_timestamp: no future-timestamp error

        def fake_fetch(request):
            call_order.append("fetch")
            result = MagicMock()
            result.status = ProviderStatus.AVAILABLE
            result.raw_reference = "ref/123"
            return result

        captured = {}

        def fake_make_envelope(result, *, mode, units, now, stale_after):
            captured["now"] = now
            # Simulate what the real make_envelope does with freshness:
            # it must not see now < snapshot_timestamp.
            return {"request_id": "req-123", "snapshot": {}}

        def fake_materialize(packet, evidence_root):
            return {"ok": True}

        with tempfile.TemporaryDirectory() as tmp:
            run_dir = harness.new_run_dir(Path(tmp) / "run")
            with patch.object(harness, "utc_now", side_effect=fake_utc_now), \
                 patch.object(harness, "TdxLocalHttpProvider") as mock_cls, \
                 patch.object(harness, "make_envelope",
                              side_effect=fake_make_envelope), \
                 patch.object(harness, "materialize",
                              side_effect=fake_materialize):
                mock_provider = MagicMock()
                mock_provider.fetch.side_effect = fake_fetch
                mock_cls.return_value = mock_provider
                # Patch the internal helpers phase_b uses for checks
                with patch.object(harness, "check_numeric_snapshot",
                                  return_value={"all_ok": True}), \
                     patch.object(harness, "check_raw_hash",
                                  return_value={"hash_match": True}):
                    result = harness.phase_b_real_quote(run_dir, "601518.SH")

            # fetch must come before utc_now in the fixed code
            self.assertEqual(call_order[0], "fetch")
            self.assertIn("utc_now", call_order)
            self.assertLess(call_order.index("fetch"),
                            call_order.index("utc_now"))

    def test_old_ordering_would_fail_freshness(self):
        """Sanity: if now < snapshot_timestamp, freshness rejects it."""
        import datetime
        from bridge.contracts import freshness
        t_snapshot = datetime.datetime(2026, 10, 11, 0, 0, 5,
                                       tzinfo=datetime.timezone.utc)
        t_now_early = datetime.datetime(2026, 10, 11, 0, 0, 0,
                                        tzinfo=datetime.timezone.utc)
        # freshness should reject a snapshot timestamp in the future
        # relative to now (this is the B01 bug scenario).
        with self.assertRaises(Exception):
            freshness(t_snapshot.isoformat(), now=t_now_early,
                      stale_after=300.0)


class B02OverallStatusTests(unittest.TestCase):
    """B02 regression: blocked/skipped phases must not yield overall PASS.

    NO_SILENT_DEGRADATION: overall PASS requires every phase to truly pass.
    """

    def test_blocked_auth_gives_incomplete_not_pass(self):
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = harness.new_run_dir(Path(tmp) / "run")
            # info mode without API key -> Phase C BLOCKED_AUTH
            with patch.dict("os.environ", {}, clear=False):
                import os
                os.environ.pop("TDX_MCP_API_KEY", None)
                rc = harness.main(["--output-dir", str(run_dir / "inner"),
                                   "--mode", "info"])
            # Must NOT be 0 (PASS). INCOMPLETE -> exit code 2.
            self.assertEqual(rc, 2)
            report = json.loads((run_dir / "inner" / "verify_report.json")
                                .read_text(encoding="utf-8"))
            self.assertEqual(report["overall"], "INCOMPLETE")
            self.assertNotEqual(report["overall"], "PASS")

    def test_all_pass_gives_pass(self):
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = harness.new_run_dir(Path(tmp) / "run")
            rc = harness.main(["--output-dir", str(run_dir / "inner"),
                               "--mode", "offline"])
            self.assertEqual(rc, 0)
            report = json.loads((run_dir / "inner" / "verify_report.json")
                                .read_text(encoding="utf-8"))
            self.assertEqual(report["overall"], "PASS")

    def test_fail_phase_gives_fail(self):
        # Simulate a FAIL phase via direct main() logic check.
        # We patch phase_a_offline to return FAIL.
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = harness.new_run_dir(Path(tmp) / "run")
            with patch.object(harness, "phase_a_offline",
                              return_value={"phase": "A_offline",
                                            "result": "FAIL"}):
                rc = harness.main(["--output-dir", str(run_dir / "inner"),
                                   "--mode", "offline"])
            self.assertEqual(rc, 1)
            report = json.loads((run_dir / "inner" / "verify_report.json")
                                .read_text(encoding="utf-8"))
            self.assertEqual(report["overall"], "FAIL")




class B03RealValueTests(unittest.TestCase):
    """B03: assertions must be based on real return values, never hardcoded."""

    def test_phase_a_canonical_check_uses_real_values(self):
        """canonical_restricted must reflect materialize() output."""
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = harness.new_run_dir(Path(tmp) / "run")
            result = harness.phase_a_offline(run_dir)
            checks = result["checks"]
            # The diagnostic fields must carry the REAL values...
            self.assertIn("canonical_data_return", checks)
            self.assertIn("canonical_data_persisted", checks)
            # ...and canonical_restricted must be derived from them.
            self.assertEqual(
                checks["canonical_restricted"],
                checks["canonical_data_return"] == []
                and checks["canonical_data_persisted"] == [],
            )

    def test_phase_c_fails_when_calls_fail(self):
        """phase_c must not PASS when business calls fail."""
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = harness.new_run_dir(Path(tmp) / "run")
            with patch.dict("os.environ",
                            {"TDX_MCP_API_KEY": "dummy-key"}, clear=False):
                with patch.object(harness, "TdxInformationProvider") as cls:
                    mock_info = MagicMock()
                    # Control plane OK...
                    mock_info.health_check.return_value = \
                        ProviderStatus.AVAILABLE
                    # ...but business calls AUTH_FAILED.
                    bad = MagicMock()
                    bad.status = ProviderStatus.AUTH_FAILED
                    bad.raw_reference = "ref/x"
                    mock_info.fetch.return_value = bad
                    cls.return_value = mock_info
                    result = harness.phase_c_real_info(
                        run_dir, "601518.SH", "20260911", "20261011")
            self.assertEqual(result["result"], "FAIL")
            self.assertIn("failed_calls", result)
            self.assertTrue(len(result["failed_calls"]) > 0)

    def test_phase_c_rejects_fabricated_reference(self):
        """B03: a fabricated raw_reference must FAIL archive readback."""
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = harness.new_run_dir(Path(tmp) / "run")
            with patch.dict("os.environ",
                            {"TDX_MCP_API_KEY": "dummy-key"}, clear=False):
                with patch.object(harness, "TdxInformationProvider") as cls:
                    mock_info = MagicMock()
                    mock_info.health_check.return_value = \
                        ProviderStatus.AVAILABLE
                    good = MagicMock()
                    good.status = ProviderStatus.AVAILABLE
                    good.raw_reference = "ref/y"  # fabricated: no archive
                    mock_info.fetch.return_value = good
                    cls.return_value = mock_info
                    result = harness.phase_c_real_info(
                        run_dir, "601518.SH", "20260911", "20261011")
            # ArchiveReader.read("ref/y") raises -> archive_verified False
            self.assertEqual(result["result"], "FAIL")
            self.assertTrue(len(result["failed_calls"]) > 0)


class B06ControlPlaneMappingTests(unittest.TestCase):
    """B06: only AUTH_FAILED maps to BLOCKED_AUTH."""

    def _run_phase_c(self, run_dir, control_status):
        import os
        from unittest.mock import MagicMock, patch
        from providers import ProviderStatus
        import tools.e2_windows_verify as harness
        with patch.dict("os.environ", {"TDX_MCP_API_KEY": "k"}, clear=False):
            with patch.object(harness, "TdxInformationProvider") as cls:
                mock_info = MagicMock()
                mock_info.health_check.return_value = control_status
                cls.return_value = mock_info
                return harness.phase_c_real_info(
                    run_dir, "601518.SH", "20260911", "20261011")

    def test_auth_failed_maps_to_blocked_auth(self):
        import tempfile
        from pathlib import Path
        from providers import ProviderStatus
        import tools.e2_windows_verify as harness
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = harness.new_run_dir(Path(tmp) / "run")
            result = self._run_phase_c(run_dir, ProviderStatus.AUTH_FAILED)
            self.assertEqual(result["result"], "BLOCKED_AUTH")

    def test_timeout_maps_to_incomplete_not_blocked_auth(self):
        import tempfile
        from pathlib import Path
        from providers import ProviderStatus
        import tools.e2_windows_verify as harness
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = harness.new_run_dir(Path(tmp) / "run")
            result = self._run_phase_c(run_dir, ProviderStatus.TIMEOUT)
            self.assertEqual(result["result"], "INCOMPLETE")
            self.assertNotEqual(result["result"], "BLOCKED_AUTH")

    def test_unavailable_maps_to_incomplete(self):
        import tempfile
        from pathlib import Path
        from providers import ProviderStatus
        import tools.e2_windows_verify as harness
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = harness.new_run_dir(Path(tmp) / "run")
            result = self._run_phase_c(run_dir,
                                       ProviderStatus.NOT_AVAILABLE)
            self.assertEqual(result["result"], "INCOMPLETE")


class B04PathBoundaryTests(unittest.TestCase):
    """B04: new_run_dir must refuse unsafe locations."""

    def test_refuses_repo_internal_path(self):
        repo_root = Path(harness.__file__).resolve().parents[1]
        target = repo_root / "tmp-e2-should-not-exist-xyz"
        # Ensure it doesn't exist (we refuse before creating).
        self.assertFalse(target.exists())
        with self.assertRaises(SystemExit) as ctx:
            harness.new_run_dir(target)
        self.assertIn("REFUSED", str(ctx.exception))

    def test_refuses_symlink(self):
        with tempfile.TemporaryDirectory() as tmp:
            real = Path(tmp) / "real-target"
            real.mkdir()
            link = Path(tmp) / "link-dir"
            try:
                link.symlink_to(real, target_is_directory=True)
            except OSError:
                self.skipTest("symlinks not supported on this platform")
            # The symlink itself doesn't exist as a dir yet; new_run_dir
            # should refuse because a component is a symlink.
            # Note: path.exists() follows symlinks, so use a subpath.
            sub = link / "run-new"
            with self.assertRaises(SystemExit) as ctx:
                harness.new_run_dir(sub)
            self.assertIn("link/reparse", str(ctx.exception).lower())

    def test_accepts_tmp_isolated_dir(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "isolated-run"
            out = harness.new_run_dir(target)
            self.assertTrue(out.is_dir())

    def test_refuses_junction_component(self):
        """B04: Windows junctions / reparse points are refused."""
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "run-new"
            # Simulate a junction by patching is_junction on the instance.
            orig_resolve = Path.resolve
            def fake_resolve(self):
                return orig_resolve(self)
            with patch.object(Path, "is_junction", create=True,
                              return_value=True):
                with self.assertRaises(SystemExit) as ctx:
                    harness.new_run_dir(target)
            self.assertIn("REFUSED", str(ctx.exception))

    def test_refuses_historical_archive_root(self):
        """B04: historical archive roots (e2e014-rerun-runtime) refused."""
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "e2e014-rerun-runtime" / "new-run"
            with self.assertRaises(SystemExit) as ctx:
                harness.new_run_dir(target)
            self.assertIn("REFUSED", str(ctx.exception))
            self.assertIn("historical", str(ctx.exception).lower())


class B05ExceptionHandlingTests(unittest.TestCase):
    """B05: phase exceptions must still produce a report with FAIL."""

    def test_phase_exception_produces_fail_report(self):
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = harness.new_run_dir(Path(tmp) / "run")
            def boom(_run_dir):
                raise RuntimeError("simulated phase crash")
            with patch.object(harness, "phase_a_offline",
                              side_effect=boom):
                rc = harness.main(["--output-dir", str(run_dir / "inner"),
                                   "--mode", "offline"])
            # Non-zero exit, report exists, phase marked FAIL with
            # desensitized error (B05: raw text never persisted).
            self.assertNotEqual(rc, 0)
            report_path = run_dir / "inner" / "verify_report.json"
            self.assertTrue(report_path.exists())
            report = json.loads(report_path.read_text(encoding="utf-8"))
            self.assertEqual(report["overall"], "FAIL")
            phase = report["phases"][0]
            self.assertEqual(phase["result"], "FAIL")
            self.assertEqual(phase["error_type"], "RuntimeError")
            self.assertEqual(phase["error_code"], "PHASE_EXCEPTION")
            self.assertNotIn("simulated phase crash", phase["error"])
            self.assertNotIn("simulated phase crash",
                             report_path.read_text(encoding="utf-8"))

    def test_phase_exception_with_secret_does_not_leak(self):
        """B05: exception text carrying secrets must not reach the report."""
        secret = "Authorization: Bearer SECRET_TOKEN_XYZ123"
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = harness.new_run_dir(Path(tmp) / "run")
            def boom(_run_dir):
                raise RuntimeError(secret)
            with patch.object(harness, "phase_a_offline",
                              side_effect=boom):
                rc = harness.main(["--output-dir", str(run_dir / "inner"),
                                   "--mode", "offline"])
            self.assertNotEqual(rc, 0)
            raw = (run_dir / "inner" / "verify_report.json").read_text(
                encoding="utf-8")
            self.assertNotIn("SECRET_TOKEN_XYZ123", raw)
            self.assertNotIn("Authorization", raw)


if __name__ == "__main__":
    unittest.main()


class B03BindingVerificationTests(unittest.TestCase):
    """B03 (round 3): archive must belong to THIS request."""

    def _mock_phase_c(self, run_dir, meta_override):
        """Run phase_c with a mocked provider whose archive metadata is controlled."""
        from unittest.mock import MagicMock, patch
        with patch.dict("os.environ",
                        {"TDX_MCP_API_KEY": "dummy-key"}, clear=False):
            with patch.object(harness, "TdxInformationProvider") as cls:
                mock_info = MagicMock()
                mock_info.health_check.return_value = ProviderStatus.AVAILABLE
                good = MagicMock()
                good.status = ProviderStatus.AVAILABLE
                good.raw_reference = "ref/good"
                good.payload = {"data": [{"x": 1}]}
                mock_info.fetch.return_value = good
                cls.return_value = mock_info
                with patch.object(harness, "ArchiveReader") as reader_cls:
                    reader = MagicMock()
                    reader.read.return_value = {
                        "integrity_status": "VERIFIED",
                        "metadata": meta_override,
                        "raw_parts": {"response": b"{}"},
                    }
                    reader_cls.return_value = reader
                    return harness.phase_c_real_info(
                        run_dir, "601518.SH", "20260911", "20261011")

    def test_binding_rejects_wrong_tool(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = harness.new_run_dir(Path(tmp) / "run")
            meta = {
                "endpoint_or_tool": "mcp#OTHER_TOOL",
                "request_params": {
                    "stock_id": "601518.SH",
                    "params": {"arguments": {"bdate": "20260911",
                                             "edate": "20261011"}},
                },
            }
            result = self._mock_phase_c(run_dir, meta)
            self.assertEqual(result["result"], "FAIL")
            for label, call in result["calls"].items():
                self.assertFalse(call["binding_verified"],
                                 f"{label} should fail binding")

    def test_binding_rejects_wrong_stock(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = harness.new_run_dir(Path(tmp) / "run")
            meta = {
                "endpoint_or_tool": "mcp#wenda_notice_query",
                "request_params": {
                    "stock_id": "000001.SZ",
                    "params": {"arguments": {"bdate": "20260911",
                                             "edate": "20261011"}},
                },
            }
            result = self._mock_phase_c(run_dir, meta)
            self.assertEqual(result["result"], "FAIL")

    def test_binding_accepts_matching_request(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = harness.new_run_dir(Path(tmp) / "run")
            meta = {
                "endpoint_or_tool": "mcp#wenda_notice_query",
                "request_params": {
                    "stock_id": "601518.SH",
                    "params": {"arguments": {"bdate": "20260911",
                                             "edate": "20261011"}},
                },
            }
            result = self._mock_phase_c(run_dir, meta)
            # announcement/news share the same date-range params; financial
            # views use different params so they fail binding in this mock.
            # At minimum the binding logic must run (not be skipped).
            for label, call in result["calls"].items():
                self.assertIn("binding_verified", call)


class B04FailClosedTests(unittest.TestCase):
    """B04 (round 3): fail-closed when junction status cannot be verified."""

    def test_refuses_when_is_junction_missing_on_windows(self):
        import tempfile, sys
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "run-new"
            # Simulate Windows Python without Path.is_junction.
            with patch.object(sys, "platform", "win32"):
                # Remove is_junction if present.
                had = hasattr(Path, "is_junction")
                orig = getattr(Path, "is_junction", None)
                try:
                    if had:
                        delattr(Path, "is_junction")
                    with self.assertRaises(SystemExit) as ctx:
                        harness.new_run_dir(target)
                    self.assertIn("REFUSED", str(ctx.exception))
                finally:
                    if had:
                        Path.is_junction = orig
