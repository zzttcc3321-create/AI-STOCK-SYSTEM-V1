"""#014-E2 Windows verification harness (rebuilt).

LEGACY_DRIVER_SOURCE_LOST: the original windows_collect.py is unrecoverable.
This is an independently named replacement; it does not impersonate the
legacy driver and must not reuse legacy run records as its own evidence.

Scope:
  Phase A (offline): contract checks with mock transports. No network.
  Phase B (real quote): TdxLocalHttpProvider -> make_envelope -> materialize,
      using the frozen Bridge/Normalizer/Evidence implementations.
  Phase C (real info): TdxInformationProvider health check + business calls.
      If MCP auth fails, Phase C is marked BLOCKED_AUTH and skipped.

Safety:
  - Read-only against frozen data sources.
  - All output goes to a single caller-specified directory that MUST NOT
    exist beforehand. The harness refuses to run into an existing directory.
  - No SQLite connections, no migrations, no network push, no credential
    persistence. Reports contain no credentials, raw quote text, or session
    identifiers.
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import math
import subprocess
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from bridge.pipeline import materialize
from bridge.windows_tdx_push import make_envelope
from evidence import ArchiveWriter
from providers import ProviderStatus
from providers.tdx_information import TdxInformationProvider
from providers.tdx_realtime import TdxLocalHttpProvider
from evidence.archive_reader import ArchiveReader

TASK_ID = "AI-STOCK-V1-014-E2-WINDOWS-VERIFY-HARNESS-R1"
# Empty verified-units declaration: unverified units must never produce
# a VERIFIED canonical. This is intentional, not a placeholder.
EMPTY_UNITS: dict = {}

QUOTE_SCALARS = ("now", "last_close", "open", "high", "low",
                 "volume", "amount", "average")
QUOTE_BOOKS = ("bid_prices", "bid_volumes", "ask_prices", "ask_volumes")


def git_head() -> str:
    return subprocess.check_output(
        ["git", "-C", str(ROOT), "rev-parse", "HEAD"], text=True).strip()


def git_status() -> str:
    return subprocess.check_output(
        ["git", "-C", str(ROOT), "status", "--porcelain"], text=True).strip()


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _repo_root() -> Path:
    """Repository root (parent of tools/)."""
    return Path(__file__).resolve().parents[1]


def new_run_dir(output_dir: str | Path) -> Path:
    """Create the isolated run directory. Refuse unsafe locations."""
    path = Path(output_dir)
    if path.exists():
        raise SystemExit(
            f"REFUSED: output directory already exists: {path}\n"
            "Specify a new, non-existent directory to avoid overwriting "
            "historical evidence.")
    # B04: refuse symlinks AND Windows junctions / reparse points
    # anywhere in the path. is_symlink() alone misses junctions.
    def _is_junction(p: Path) -> bool:
        is_junc = getattr(p, "is_junction", None)
        if callable(is_junc):
            try:
                return bool(is_junc())
            except OSError:
                return False
        return False

    probe = path
    while True:
        if probe.is_symlink() or _is_junction(probe):
            raise SystemExit(
                f"REFUSED: path component is a link/reparse point: {probe}\n"
                "Links could redirect evidence outside the isolated dir.")
        parent = probe.parent
        if parent == probe:
            break
        probe = parent
    resolved = path.resolve()
    # B04: refuse locations inside the repository tree.
    repo = _repo_root()
    try:
        resolved.relative_to(repo)
        raise SystemExit(
            f"REFUSED: output directory is inside the repository: {resolved}\n"
            "Evidence must go to an isolated directory outside the repo.")
    except ValueError:
        pass  # not inside repo: acceptable
    # B04: refuse historical archive roots (e.g. D:\e2e014-rerun-runtime).
    # Re-running into them would overwrite prior evidence.
    _HISTORICAL_ARCHIVE_MARKERS = ("e2e014-rerun-runtime", "e2e014-rerun")
    lowered = str(resolved).lower()
    for marker in _HISTORICAL_ARCHIVE_MARKERS:
        if marker in lowered:
            raise SystemExit(
                f"REFUSED: output directory is inside a historical archive "
                f"root ({marker}): {resolved}\n"
                "Use a fresh isolated directory instead.")
    path.mkdir(parents=True)
    return path.resolve()


def check_numeric_snapshot(snapshot: dict) -> dict:
    """Validate the H-R2 numeric adaptation on a real snapshot."""
    scalars_ok = all(
        type(snapshot.get(k)) in (int, float)
        and math.isfinite(snapshot[k])
        and snapshot[k] >= 0
        for k in QUOTE_SCALARS
    )
    books_ok = all(
        isinstance(snapshot.get(k), list)
        and len(snapshot[k]) == 5
        and all(type(v) in (int, float) and math.isfinite(v) and v >= 0
                for v in snapshot[k])
        for k in QUOTE_BOOKS
    )
    return {"scalars_ok": scalars_ok, "books_ok": books_ok,
            "all_ok": scalars_ok and books_ok}


def check_raw_hash(payload: dict) -> dict:
    """Recompute SHA-256 of the raw response and compare."""
    raw = base64.b64decode(payload["raw_response_b64"])
    digest = hashlib.sha256(raw).hexdigest()
    return {"hash_match": digest == payload["raw_response_hash"],
            "raw_bytes": len(raw)}


def phase_a_offline(run_dir: Path) -> dict:
    """Phase A: offline contract checks with synthetic data.

    Exercises the envelope/materialize path with a synthetic provider
    result shaped like the frozen contract. No network access.
    """
    from types import SimpleNamespace

    now = utc_now()
    # TDX-format field names (what map_snapshot expects from raw)
    tdx_raw = {
        "Now": 4.2, "LastClose": 4.1, "Open": 4.12, "Max": 4.3, "Min": 4.0,
        "Volume": 12345, "Amount": 5184900.0, "Average": 4.2,
        "Buyp": [4.19, 0, 0, 0, 0], "Buyv": [200, 0, 0, 0, 0],
        "Sellp": [4.21, 0, 0, 0, 0], "Sellv": [300, 0, 0, 0, 0],
    }
    # Normalized snapshot (what make_envelope expects in payload)
    snapshot = {
        "now": 4.2, "last_close": 4.1, "open": 4.12, "high": 4.3,
        "low": 4.0, "volume": 12345, "amount": 5184900.0, "average": 4.2,
        "bid_prices": [4.19, 0, 0, 0, 0], "bid_volumes": [200, 0, 0, 0, 0],
        "ask_prices": [4.21, 0, 0, 0, 0], "ask_volumes": [300, 0, 0, 0, 0],
    }
    raw = json.dumps({"result": {"ErrorId": "0", **tdx_raw}}).encode()
    now_iso = now.isoformat()
    result = SimpleNamespace(
        payload={
            "stock_id": "000001.SZ",
            "request_id": uuid.uuid4().hex,
            "system_run_id": "offline",
            "collection_task_id": "phase_a",
            "snapshot": snapshot,
            "snapshot_timestamp": now_iso,
            "raw_response_b64": base64.b64encode(raw).decode(),
            "raw_response_hash": hashlib.sha256(raw).hexdigest(),
            "realtime_quote_status": "REALTIME",
        },
        status=ProviderStatus.AVAILABLE,
        request_timestamp=now_iso,
        provider_timestamp=None,
        raw_reference=None,
    )
    packet = make_envelope(result, mode="MANUAL", units=EMPTY_UNITS,
                           now=now, stale_after=300.0)
    out = materialize(packet, run_dir / "phase_a")
    evidence_dir = run_dir / "phase_a" / "offline" / "phase_a" / packet["request_id"]
    # B03: verify canonical restriction from the real materialize() return
    # value and the written evidence.json - never hardcode True.
    canonical_data = out.get("canonical_data")
    evidence_json_path = evidence_dir / "evidence.json"
    evidence_canonical = None
    if evidence_json_path.exists():
        try:
            evidence_canonical = json.loads(
                evidence_json_path.read_text(encoding="utf-8")
            ).get("canonical_data")
        except (json.JSONDecodeError, OSError):
            evidence_canonical = "UNREADABLE"
    checks = {
        "envelope_built": True,
        "materialized": isinstance(out, dict),
        "raw_json_exists": (evidence_dir / "raw.json").exists(),
        "evidence_json_exists": evidence_json_path.exists(),
        "numeric_ok": check_numeric_snapshot(snapshot)["all_ok"],
        "units_empty": packet["units"] == {},
        # Empty units must yield no VERIFIED canonical: the real return
        # value and the persisted evidence.json must both show empty list.
        "canonical_restricted": (
            canonical_data == [] and evidence_canonical == []
        ),
        "canonical_data_return": canonical_data,
        "canonical_data_persisted": evidence_canonical,
    }
    # Status distinction contract: the three failure statuses must remain
    # distinct values and must never be conflated.
    checks["statuses_distinct"] = len({
        ProviderStatus.AUTH_FAILED.value,
        ProviderStatus.NOT_AVAILABLE.value,
        ProviderStatus.VALIDATION_FAILED.value,
    }) == 3
    # B03: gate only on boolean contract checks; diagnostic fields
    # (canonical_data_return/_persisted) carry real values for the report
    # but must not affect the PASS/FAIL computation via falsy [].
    boolean_checks = {k: v for k, v in checks.items()
                      if k not in ("canonical_data_return",
                                   "canonical_data_persisted")}
    return {"phase": "A_offline",
            "result": "PASS" if all(boolean_checks.values()) else "FAIL",
            "checks": checks}


def phase_b_real_quote(run_dir: Path, stock_id: str) -> dict:
    """Phase B: real TDX quote through the frozen pipeline."""
    run_id = "e2win" + uuid.uuid4().hex
    provider = TdxLocalHttpProvider(
        archive_writer=ArchiveWriter(run_dir / "phase_b" / "realtime"))
    request = {
        "system_run_id": run_id,
        "collection_task_id": "real_quote",
        "request_id": uuid.uuid4().hex,
        "stock_id": stock_id,
        "data_kind": "realtime_quote",
        "evidence_id": uuid.uuid4().hex,
    }
    result = provider.fetch(request)
    # B01 fix: capture `now` AFTER fetch() returns. fetch() fills
    # snapshot_timestamp when the HTTP response is parsed, so a `now`
    # taken before fetch would be earlier than snapshot_timestamp and
    # freshness() would reject it as a future timestamp.
    now = utc_now()
    summary: dict = {
        "phase": "B_real_quote",
        "provider_status": result.status.value,
        "archive_present": bool(result.raw_reference),
    }
    if result.status != ProviderStatus.AVAILABLE:
        summary["result"] = "SKIPPED_NOT_AVAILABLE"
        summary["note"] = ("Provider did not return AVAILABLE; "
                           "no silent substitution performed.")
        return summary
    payload = result.payload or {}
    snapshot = payload.get("snapshot") or {}
    summary["numeric"] = check_numeric_snapshot(snapshot)
    summary["raw_hash"] = check_raw_hash(payload)
    # Envelope + materialize through the frozen implementations.
    packet = make_envelope(result, mode="MANUAL", units=EMPTY_UNITS,
                           now=now, stale_after=300.0)
    out = materialize(packet, run_dir / "phase_b" / "evidence")
    evidence_dir = (run_dir / "phase_b" / "evidence" / run_id
                    / "real_quote" / packet["request_id"])
    summary["envelope_built"] = True
    summary["raw_json_exists"] = (evidence_dir / "raw.json").exists()
    summary["evidence_json_exists"] = (evidence_dir / "evidence.json").exists()
    # Raw readback: re-read the archived bytes and re-verify the hash.
    raw_path = evidence_dir / "raw.json"
    if raw_path.exists():
        reread = raw_path.read_bytes()
        summary["raw_readback_bytes"] = len(reread)
        summary["raw_readback_hash_match"] = (
            hashlib.sha256(reread).hexdigest() == payload["raw_response_hash"])
    else:
        summary["raw_readback_hash_match"] = False
    # B03: verify evidence.json canonical content from the real file,
    # not just its existence. Empty units => canonical_data must be [].
    evidence_json_path = evidence_dir / "evidence.json"
    summary["evidence_canonical_data"] = None
    summary["evidence_canonical_restricted"] = False
    if evidence_json_path.exists():
        try:
            persisted = json.loads(
                evidence_json_path.read_text(encoding="utf-8"))
            summary["evidence_canonical_data"] = persisted.get("canonical_data")
            summary["evidence_canonical_restricted"] = (
                summary["evidence_canonical_data"] == [])
        except (json.JSONDecodeError, OSError) as exc:
            summary["evidence_read_error"] = type(exc).__name__
    # Also check the in-memory materialize() return value.
    summary["materialize_canonical_data"] = out.get("canonical_data")
    summary["materialize_canonical_restricted"] = (
        summary["materialize_canonical_data"] == [])
    ok = (summary["numeric"]["all_ok"]
          and summary["raw_hash"]["hash_match"]
          and summary["raw_readback_hash_match"]
          and summary["evidence_json_exists"]
          and summary["evidence_canonical_restricted"]
          and summary["materialize_canonical_restricted"])
    summary["result"] = "PASS" if ok else "FAIL"
    return summary


def phase_c_real_info(run_dir: Path, stock_id: str,
                      date_from: str | None, date_to: str | None) -> dict:
    """Phase C: real information chain. BLOCKED_AUTH when key fails."""
    import os
    summary: dict = {"phase": "C_real_info"}
    if not os.environ.get("TDX_MCP_API_KEY"):
        summary["result"] = "BLOCKED_AUTH"
        summary["note"] = "TDX_MCP_API_KEY not configured; no business calls made."
        return summary
    info = TdxInformationProvider(evidence_root=run_dir / "phase_c")
    control = info.health_check()
    summary["control_plane"] = control.value
    if control != ProviderStatus.AVAILABLE:
        # B06: only AUTH_FAILED maps to BLOCKED_AUTH. TIMEOUT,
        # NOT_AVAILABLE, VALIDATION_FAILED, etc. keep their real cause;
        # the run is INCOMPLETE, never misreported as an auth problem.
        if control == ProviderStatus.AUTH_FAILED:
            summary["result"] = "BLOCKED_AUTH"
        else:
            summary["result"] = "INCOMPLETE"
        summary["note"] = ("Control plane not AVAILABLE "
                           f"({control.value}); business calls skipped.")
        return summary
    if not (date_from and date_to):
        summary["result"] = "SKIPPED_MISSING_DATE_RANGE"
        return summary
    # B03: verify each call's archive by actually reading it back.
    # A fabricated raw_reference (e.g. "ref/y") fails the read and can
    # never yield PASS.
    reader = ArchiveReader(run_dir / "phase_c")

    def _verify_call(label, r):
        info = {"status": r.status.value,
                "archive_present": bool(r.raw_reference),
                "archive_verified": False,
                "archive_integrity": None}
        if r.raw_reference and r.status in (ProviderStatus.AVAILABLE,
                                            ProviderStatus.NOT_AVAILABLE):
            try:
                back = reader.read(r.raw_reference)
                info["archive_integrity"] = back.get("integrity_status")
                info["archive_verified"] = (
                    back.get("integrity_status") == "VERIFIED")
            except Exception as exc:  # noqa: BLE001 - record, don't leak
                info["archive_read_error"] = type(exc).__name__
        return info

    results = {}
    for label, tool, kind in (
            ("announcement", "wenda_notice_query", "announcement"),
            ("news", "wenda_news_query", "news")):
        r = info.fetch({
            "system_run_id": "e2win" + uuid.uuid4().hex,
            "collection_task_id": label,
            "request_id": uuid.uuid4().hex,
            "stock_id": stock_id, "tool": tool, "data_kind": kind,
            "arguments": {"bdate": date_from, "edate": date_to},
        })
        results[label] = _verify_call(label, r)
    for view in ("profit", "balance", "cashflow"):
        r = info.fetch({
            "system_run_id": "e2win" + uuid.uuid4().hex,
            "collection_task_id": view,
            "request_id": uuid.uuid4().hex,
            "stock_id": stock_id, "tool": "tdx_api_data",
            "data_kind": "financial", "arguments": {"view": view},
        })
        results["financial_" + view] = _verify_call(view, r)
    summary["calls"] = results
    # B03: PASS only if every business call genuinely succeeded AND its
    # archive reads back with VERIFIED integrity. AVAILABLE or
    # NOT_AVAILABLE (explicit nodata classification) are acceptable
    # statuses. AUTH_FAILED / VALIDATION_FAILED / unreadable archive are
    # real failures, never silently passed.
    acceptable = {"AVAILABLE", "NOT_AVAILABLE"}
    failed_calls = {
        label: info for label, info in results.items()
        if info["status"] not in acceptable or not info["archive_verified"]
    }
    summary["failed_calls"] = failed_calls
    if failed_calls:
        summary["result"] = "FAIL"
        summary["note"] = (
            f"{len(failed_calls)} business call(s) did not succeed: "
            + ", ".join(sorted(failed_calls)))
    else:
        summary["result"] = "PASS"
    return summary


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Minimal Windows E2 verification harness (rebuilt).")
    parser.add_argument("--output-dir", required=True,
                        help="New isolated run directory (must not exist).")
    parser.add_argument("--mode", default="offline",
                        choices=["offline", "quote", "info", "all"],
                        help="offline=Phase A only; quote=+Phase B; "
                             "info=+Phase C; all=A+B+C.")
    parser.add_argument("--stock-id", default="601518.SH")
    parser.add_argument("--date-from", default=None)
    parser.add_argument("--date-to", default=None)
    args = parser.parse_args(argv)

    run_dir = new_run_dir(args.output_dir)
    report: dict = {
        "task_id": TASK_ID,
        "git_head": git_head(),
        "git_status": git_status(),
        "mode": args.mode,
        "stock_id": args.stock_id,
        "database_write": False,
        "credential_persisted": False,
    }
    phases = []
    # B05: a phase raising an exception must still produce a complete
    # failure report (never a missing verify_report.json) with a non-zero
    # exit code, without masking the real error.
    # B05: desensitized error records. The raw exception text is never
    # persisted to the report; it may contain credentials, tokens, or
    # session identifiers. Only the stable type name and a fixed code go
    # into verify_report.json.
    def _desensitized_error(exc: BaseException) -> dict:
        return {
            "error_type": type(exc).__name__,
            "error_code": "PHASE_EXCEPTION",
            "error": "phase raised; details withheld from report",
        }

    def _run_phase(name, fn, *a, **k):
        try:
            return fn(*a, **k)
        except SystemExit:
            raise
        except Exception as exc:  # noqa: BLE001 - report, don't mask
            rec = _desensitized_error(exc)
            rec.update({"phase": name, "result": "FAIL"})
            return rec
    if args.mode in ("offline", "all"):
        phases.append(_run_phase("A_offline", phase_a_offline, run_dir))
    if args.mode in ("quote", "all"):
        phases.append(_run_phase("B_real_quote", phase_b_real_quote,
                                 run_dir, args.stock_id))
    if args.mode in ("info", "all"):
        phases.append(_run_phase("C_real_info", phase_c_real_info, run_dir,
                                 args.stock_id, args.date_from, args.date_to))
    report["phases"] = phases
    # B02 fix (NO_SILENT_DEGRADATION): a blocked or skipped phase must never
    # be reported as an overall PASS. Only a run where every executed phase
    # truly passed may return PASS / exit 0.
    results = [p.get("result") for p in phases]
    if all(r == "PASS" for r in results):
        overall_status, exit_code = "PASS", 0
    elif any(r == "FAIL" for r in results):
        overall_status, exit_code = "FAIL", 1
    else:
        # At least one phase was blocked/skipped (e.g. BLOCKED_AUTH,
        # SKIPPED_NOT_AVAILABLE, SKIPPED_MISSING_DATE_RANGE) and none failed.
        # The verification is incomplete: report it honestly, never claim PASS.
        overall_status, exit_code = "INCOMPLETE", 2
    report["overall"] = overall_status
    (run_dir / "verify_report.json").write_text(
        json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    print(json.dumps({"overall": report["overall"],
                      "phases": [(p["phase"], p["result"]) for p in phases],
                      "report": str(run_dir / "verify_report.json")}))
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
