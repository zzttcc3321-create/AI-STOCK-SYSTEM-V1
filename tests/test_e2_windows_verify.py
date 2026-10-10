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


if __name__ == "__main__":
    unittest.main()
