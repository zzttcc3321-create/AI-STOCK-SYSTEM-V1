"""V1 physical archive contracts. No legacy reference interpretation."""
from dataclasses import dataclass, fields, is_dataclass
from datetime import datetime
from pathlib import Path
from enum import Enum
import hashlib
import json
import math
import re
from collections.abc import Mapping

from .contracts import EvidenceSource, EvidenceRecord

MAX_RAW_BYTES = 8 * 1024 * 1024
MAX_METADATA_BYTES = 2 * 1024 * 1024
MAX_PARTS = 8
CHUNK_BYTES = 64 * 1024
SIDECAR_NAME = "raw.evidence.json"
SENSITIVE = ("token", "apikey", "authorization", "password", "cookie", "session", "secret")


class ArchiveError(ValueError):
    pass


def require(condition, code):
    if not condition:
        raise ArchiveError(code)


def identifier(value):
    require(type(value) is str and re.fullmatch(r"[A-Za-z0-9_-]{1,100}", value) is not None,
            "ARCHIVE_IDENTIFIER_INVALID")
    return value


def plain(value):
    if is_dataclass(value):
        return {f.name: plain(getattr(value, f.name)) for f in fields(value)}
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, Mapping):
        require(all(type(k) is str for k in value), "JSON_KEYS_MUST_BE_TEXT")
        return {k: plain(v) for k, v in value.items()}
    if type(value) in (list, tuple):
        return [plain(v) for v in value]
    if value is None or type(value) in (str, bool, int):
        return value
    if type(value) is float and math.isfinite(value):
        return value
    raise ArchiveError("FINITE_JSON_VALUES_REQUIRED")


def canonical_json(value):
    return json.dumps(plain(value), ensure_ascii=False, sort_keys=True,
                      separators=(",", ":"), allow_nan=False).encode("utf-8")


def strict_json(data):
    def unique(pairs):
        result = {}
        for k, v in pairs:
            require(k not in result, "DUPLICATE_JSON_KEY")
            result[k] = v
        return result
    def nonfinite(_):
        raise ArchiveError("FINITE_JSON_VALUES_REQUIRED")
    try:
        return plain(json.loads(data, object_pairs_hook=unique, parse_constant=nonfinite))
    except (ValueError, TypeError, UnicodeError, RecursionError) as exc:
        if isinstance(exc, ArchiveError):
            raise
        raise ArchiveError("INVALID_JSON") from None


def sha256(data):
    return hashlib.sha256(data).hexdigest()


def json_hash(value):
    return sha256(canonical_json(value))


def sanitized(value):
    value = plain(value)
    if isinstance(value, dict):
        return {k: sanitized(v) for k, v in value.items()
                if not any(word in re.sub(r"[^a-z0-9]", "", k.lower()) for word in SENSITIVE)}
    if isinstance(value, list):
        return [sanitized(v) for v in value]
    return value


def reject_sensitive(value):
    require(plain(value) == sanitized(value), "SENSITIVE_METADATA_FORBIDDEN")


def reject_secret_echo(data, secrets):
    text = data.decode("utf-8", errors="replace")
    unescaped = re.sub(r"\\u([0-9a-fA-F]{4})", lambda m: chr(int(m[1], 16)), text)
    for secret in secrets:
        require(type(secret) is str and bool(secret), "SECRET_GUARD_INVALID")
        require(secret.encode() not in data and canonical_json(secret)[1:-1] not in data
                and secret not in unescaped, "SENSITIVE_RESPONSE_ECHO")


@dataclass(frozen=True)
class ArchiveIdentity:
    system_run_id: str
    collection_task_id: str
    request_id: str
    evidence_id: str

    def __post_init__(self):
        for value in self.components:
            identifier(value)

    @property
    def components(self):
        return (self.system_run_id, self.collection_task_id, self.request_id, self.evidence_id)

    @property
    def uri(self):
        return "archive://v1/" + "/".join(self.components)

    @classmethod
    def parse(cls, uri):
        require(type(uri) is str and uri.startswith("archive://v1/"), "ARCHIVE_URI_REQUIRED")
        parts = uri[len("archive://v1/"):].split("/")
        require(len(parts) == 4, "ARCHIVE_URI_INVALID")
        result = cls(*parts)
        require(result.uri == uri, "ARCHIVE_URI_NONCANONICAL")
        return result


def bundle_hash(parts):
    # Ordered, named, length-delimited manifest avoids concatenation ambiguity.
    if len(parts) == 1:
        return parts[0]["raw_sha256"]
    return json_hash([{k: p[k] for k in ("name", "raw_size", "raw_sha256")} for p in parts])


def sealed(payload):
    return canonical_json({"payload": payload, "sha256": json_hash(payload)})


def unseal(data):
    value = strict_json(data)
    require(canonical_json(value) == data, "SIDECAR_NONCANONICAL")
    require(type(value) is dict and set(value) == {"payload", "sha256"}, "SIDECAR_SCHEMA_INVALID")
    require(value["sha256"] == json_hash(value["payload"]), "SIDECAR_HASH_MISMATCH")
    return value["payload"]


def validate_raw_metadata(metadata, identity):
    require(type(metadata) is dict and set(metadata) == {
        "version", "kind", "identity", "uri", "source", "source_reference", "record", "parts",
        "raw_response_hash", "attributes"}, "SIDECAR_SCHEMA_INVALID")
    require(metadata["version"] == "V1.0" and metadata["kind"] == "RAW" and
            metadata["identity"] == plain(identity) and metadata["uri"] == identity.uri,
            "ARCHIVE_CONTEXT_MISMATCH")
    require(metadata["source"] in {s.value for s in EvidenceSource}, "ARCHIVE_SOURCE_INVALID")
    parts = metadata["parts"]
    require(type(parts) is list and 1 <= len(parts) <= MAX_PARTS, "RAW_PARTS_INVALID")
    names = set()
    total = 0
    for p in parts:
        require(type(p) is dict and set(p) == {"name", "file", "raw_size", "raw_sha256",
                "stored_size", "stored_sha256"}, "RAW_PART_SCHEMA_INVALID")
        identifier(p["name"])
        require(p["name"] not in names and p["file"] == p["name"] + ".raw.gz", "DUPLICATE_RAW_PART")
        names.add(p["name"])
        for key in ("raw_size", "stored_size"):
            require(type(p[key]) is int and 0 <= p[key] <= MAX_RAW_BYTES + 65536, "RAW_SIZE_INVALID")
        for key in ("raw_sha256", "stored_sha256"):
            require(type(p[key]) is str and re.fullmatch(r"[a-f0-9]{64}", p[key]), "RAW_HASH_INVALID")
        total += p["raw_size"]
    require(total <= MAX_RAW_BYTES and metadata["raw_response_hash"] == bundle_hash(parts), "BUNDLE_HASH_MISMATCH")
    record = metadata["record"]
    schema = {f.name for f in fields(EvidenceRecord)}
    require(type(record) is dict and set(record) in (schema, schema - {"canonical_id", "raw_reference_id"}),
            "ARCHIVE_RECORD_SCHEMA_INVALID")
    require(type(record) is dict and record["source"] == metadata["source"] and
            record["raw_response_hash"] == metadata["raw_response_hash"] and
            record["raw_response_path"] == identity.uri and
            (record.get("raw_reference_id") or record["evidence_id"]) == identity.evidence_id and
            record.get("canonical_id") is None and
            all(record[k] == getattr(identity, k) for k in ("system_run_id", "collection_task_id")),
            "ARCHIVE_RECORD_MISMATCH")
    require(type(record["request_params"]) is dict and type(record["endpoint_or_tool"]) is str,
            "ARCHIVE_RECORD_SCHEMA_INVALID")
    require(datetime.fromisoformat(record["request_timestamp"]).tzinfo is not None, "ARCHIVE_TIMESTAMP_INVALID")
    validate_source_reference(metadata["source"], metadata["source_reference"], identity, record["request_params"])
    reject_sensitive(metadata)


def validate_source_reference(source, reference, identity, params):
    if reference is None:
        return
    require(type(reference) is str and not Path(reference).is_absolute() and "\\" not in reference,
            "SOURCE_REFERENCE_INVALID")
    if source == "TUSHARE_MCP":
        require(reference == f"tushare://daily/{params.get('stock_id')}/{params.get('date_from')}/{params.get('date_to')}",
                "TUSHARE_SOURCE_CONTEXT_MISMATCH")
    elif source == "TDX_OFFICIAL_LOCAL_HTTP":
        require(reference == f"tdx-local/{identity.system_run_id}/{identity.collection_task_id}/{identity.request_id}/raw.json",
                "TDX_SOURCE_CONTEXT_MISMATCH")
    else:
        require(reference.startswith("https://txmcp.tdx.com.cn:3001/txmcp#"), "SOURCE_REFERENCE_INVALID")


def validate_normalized_metadata(value, uri, content_hash):
    require(type(value) is dict and set(value) == {"kind", "uri", "normalized_data", "normalized_data_hash",
                "normalization_rule", "validation_result"} and value["kind"] == "NORMALIZED" and
            value["uri"] == uri and json_hash(value["normalized_data"]) == value["normalized_data_hash"] == content_hash,
            "NORMALIZED_HASH_MISMATCH")
    rules = value["normalization_rule"]
    require(rules is None or type(rules) is list and all(type(v) is str for v in rules), "NORMALIZATION_RULE_INVALID")
    validation = value["validation_result"]
    require(type(validation) is dict and set(validation) == {"status", "failures"} and
            validation["status"] in {"PASS", "FAIL", "NOT_VALIDATED"} and type(validation["failures"]) is list and
            all(type(v) is str for v in validation["failures"]), "VALIDATION_RESULT_INVALID")
    reject_sensitive(value)
