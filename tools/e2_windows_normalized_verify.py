"""Verify real Windows information Normalizer -> Canonical Evidence readback.

Uses the existing provider without changing or rerunning Phase A/B/C checks.
Writes only to a fresh isolated archive; never opens a database. Reports keep
identities, hashes and explicit per-stage durability, never response content,
credentials, session identifiers or exception messages.
"""
from __future__ import annotations

import argparse
import os
import sys
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from evidence import ArchiveIdentity, ArchiveReader, ArchiveWriter, EvidenceBuilder
from evidence.archive_contracts import (
    MAX_METADATA_BYTES, canonical_json, json_hash, plain, require, sha256, unseal,
)
from evidence.archive_reader import durable_payload
from evidence.contracts import (
    EvidenceRecord, EvidenceSource, EvidenceValidationResult, HumanConfirm, ValidationStatus,
)
from normalizer import InformationNormalizer
from normalizer.information import validate_information_evidence
from providers import ProviderStatus
from providers.tdx_information import TdxInformationProvider
from tools.e2_windows_verify import git_head, git_status, new_run_dir

TASK_ID = "AI-STOCK-V1-014-E2-NORMALIZED-CANONICAL-WINDOWS"


def _record(value: dict) -> EvidenceRecord:
    return EvidenceRecord(**{
        **value,
        "source": EvidenceSource(value["source"]),
        "human_confirm": HumanConfirm(value["human_confirm"]),
        "normalization_rule": (tuple(value["normalization_rule"])
                               if value["normalization_rule"] is not None else None),
        "validation_result": EvidenceValidationResult(
            ValidationStatus(value["validation_result"]["status"]),
            tuple(value["validation_result"]["failures"])),
    })


def _sidecar(reader: ArchiveReader, uri: str, name: str) -> dict:
    """Bounded read of a sealed event, including its own durability level."""
    path = reader.resolver.file(reader.resolver.resolve(uri), name)
    with reader.resolver.open_read(path) as stream:
        data = stream.read(MAX_METADATA_BYTES + 1)
    require(len(data) <= MAX_METADATA_BYTES, "METADATA_SIZE_LIMIT")
    payload = unseal(data)
    durable_payload(payload)
    return payload


def verify_information(envelope, writer: ArchiveWriter, *, canonical_id=None) -> dict:
    """Verify one live provider envelope; no synthetic substitution on failure."""
    stages = {name: {"verified": False} for name in ("raw", "normalized", "canonical_evidence")}
    summary = {"result": "FAIL", "provider_status": envelope.status.value, "stages": stages}
    try:
        require(envelope.status in (ProviderStatus.AVAILABLE, ProviderStatus.NOT_AVAILABLE),
                "PROVIDER_NOT_AVAILABLE")
        uri = envelope.raw_reference
        identity = ArchiveIdentity.parse(uri)
        # A new Reader, independent of the Writer's readback instance.
        reader = ArchiveReader(writer.resolver.root)
        back = reader.read(uri, expected=identity)
        raw = back["raw_parts"]["response"]
        meta = back["metadata"]
        original = _record(meta["record"])
        reader.verify_record(original)
        # Match the exact business response returned by fetch(), excluding
        # initialization/discovery or earlier session-retry responses.
        entries = [entry for entry in envelope.payload["evidence"]
                   if entry["record"]["raw_response_path"] == uri]
        require(len(entries) == 1 and entries[0]["record"] == plain(original)
                and entries[0]["raw_bytes"] == raw, "PROVIDER_RAW_MISMATCH")
        require(sha256(raw) == original.raw_response_hash, "RAW_HASH_MISMATCH")
        raw_sidecar = _sidecar(reader, uri, "raw.evidence.json")
        stages["raw"] = {
            "verified": True, "uri": uri, "raw_reference_id": identity.evidence_id,
            "evidence_id": original.evidence_id, "bytes": len(raw),
            "raw_response_hash": sha256(raw), "sidecar_hash": json_hash(raw_sidecar),
            "durability_level": back["durability_level"],
        }
        normalizer = InformationNormalizer()
        result = normalizer.normalize(envelope, source_evidence=original, raw_bytes=raw)
        repeated = normalizer.normalize(envelope, source_evidence=original, raw_bytes=raw)
        require(plain(result) == plain(repeated), "NORMALIZATION_NONDETERMINISTIC")
        summary["normalization_deterministic"] = True
        summary["record_count"] = len(result.canonical_data)
        if not result.canonical_data:
            summary["result"] = "INCOMPLETE"
            summary["error_code"] = "NO_CANONICAL_CONTENT"
            return summary
        canonical = EvidenceBuilder().build(
            fetch_result=envelope, endpoint_or_tool=original.endpoint_or_tool,
            request_params=original.request_params, system_run_id=original.system_run_id,
            collection_task_id=original.collection_task_id, normalization_result=result,
            raw_response_hash=original.raw_response_hash,
            evidence_id=canonical_id or "CANONICAL-" + uuid.uuid4().hex,
        )
        validate_information_evidence(result, canonical)
        require(canonical.validation_result.status == ValidationStatus.PASS,
                "CANONICAL_VALIDATION_FAILED")
        writer.append_normalized(uri, normalized_data=result.canonical_data,
            normalization_rule=canonical.normalization_rule, validation_result=canonical.validation_result)
        normalized = reader.normalized(uri, canonical.normalized_data_hash)
        require(normalized["normalized_data"] == plain(result.canonical_data)
                and normalized["normalized_data_hash"] == json_hash(result.canonical_data)
                and normalized["normalization_rule"] == plain(canonical.normalization_rule)
                and normalized["validation_result"] == plain(canonical.validation_result),
                "NORMALIZED_READBACK_MISMATCH")
        stages["normalized"] = {
            "verified": True, "uri": normalized["uri"],
            "normalized_data_hash": normalized["normalized_data_hash"],
            "sidecar_hash": json_hash(normalized), "record_count": len(normalized["normalized_data"]),
            "durability_level": normalized["durability_level"],
        }
        writer.append_evidence(canonical)
        recovered = reader.evidence(uri, canonical.evidence_id)
        reader.verify_record(canonical)
        require(recovered == plain(canonical), "CANONICAL_READBACK_MISMATCH")
        event = _sidecar(reader, uri, "evidence-" + json_hash(canonical.evidence_id) + ".json")
        require(durable_payload(event) == {
            "kind": "CANONICAL_EVIDENCE", "uri": uri, "record": recovered,
        }, "CANONICAL_SIDECAR_MISMATCH")
        # Reverify raw bytes/metadata after both append-only commits.
        after = reader.read(uri, expected=identity)
        require(after == back and _sidecar(reader, uri, "raw.evidence.json") == raw_sidecar,
                "RAW_CHANGED_DURING_NORMALIZATION")
        stages["canonical_evidence"] = {
            "verified": True, "uri": uri, "canonical_id": recovered["canonical_id"],
            "raw_reference_id": recovered["raw_reference_id"],
            "raw_response_hash": recovered["raw_response_hash"],
            "normalized_data_hash": recovered["normalized_data_hash"],
            "record_hash": json_hash(recovered), "sidecar_hash": json_hash(event),
            "durability_level": event["durability_level"],
        }
        summary["result"] = "PASS"
    except Exception as exc:
        # Never persist exception text: it can contain echoed credentials.
        summary.update(error_type=type(exc).__name__, error_code="CHAIN_VERIFICATION_FAILED")
    return summary


def real_information(run_dir: Path, stock_id: str, date_from: str, date_to: str) -> dict:
    if not os.environ.get("TDX_MCP_API_KEY"):
        return {"result": "BLOCKED_AUTH", "error_code": "API_KEY_NOT_CONFIGURED", "calls": {}}
    writer = ArchiveWriter(run_dir / "archive")
    provider = TdxInformationProvider(evidence_root=run_dir / "archive", archive_writer=writer)
    cases = [
        ("announcement", "wenda_notice_query", "announcement", {"bdate": date_from, "edate": date_to}),
        ("news", "wenda_news_query", "news", {"bdate": date_from, "edate": date_to}),
        ("research", "wenda_report_query", "research", {"bdate": date_from, "edate": date_to}),
        *[("financial_" + view, "tdx_api_data", "financial", {"view": view})
          for view in ("profit", "balance", "cashflow")],
    ]
    calls = {}
    run_id = "e2norm" + uuid.uuid4().hex
    for label, tool, kind, arguments in cases:
        try:
            envelope = provider.fetch({
                "system_run_id": run_id, "collection_task_id": label, "request_id": uuid.uuid4().hex,
                "stock_id": stock_id, "tool": tool, "data_kind": kind, "arguments": arguments,
            })
            calls[label] = verify_information(envelope, writer)
        except Exception as exc:
            calls[label] = {"result": "FAIL", "error_type": type(exc).__name__,
                            "error_code": "CHAIN_VERIFICATION_FAILED"}
    statuses = [call["result"] for call in calls.values()]
    overall = "FAIL" if "FAIL" in statuses else "PASS" if all(s == "PASS" for s in statuses) else "INCOMPLETE"
    return {"result": overall, "calls": calls}


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", required=True, help="Fresh isolated directory outside the repository.")
    parser.add_argument("--stock-id", default="601518.SH")
    parser.add_argument("--date-from", required=True)
    parser.add_argument("--date-to", required=True)
    args = parser.parse_args(argv)
    run_dir = new_run_dir(args.output_dir)
    report = {"task_id": TASK_ID, "git_head": git_head(), "git_status": git_status(),
              "platform": sys.platform, "REAL_DB_WRITE": "NO", "credential_persisted": False}
    try:
        report.update(real_information(run_dir, args.stock_id, args.date_from, args.date_to))
    except Exception as exc:
        report.update(result="FAIL", error_type=type(exc).__name__, error_code="CHAIN_VERIFICATION_FAILED")
    # A Linux execution may verify content but cannot certify Windows execution.
    report["windows_real_chain_verified"] = sys.platform == "win32" and report["result"] == "PASS"
    path = run_dir / "normalized_verify_report.json"
    path.write_bytes(canonical_json(report))
    print(canonical_json({"result": report["result"],
                          "windows_real_chain_verified": report["windows_real_chain_verified"]}).decode())
    return 0 if report["result"] == "PASS" else 1 if report["result"] == "FAIL" else 2


if __name__ == "__main__":
    raise SystemExit(main())
