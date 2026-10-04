"""Bounded, read-only verification. An uncommitted directory is never evidence."""
import gzip
import hashlib
import os
import re

from .archive_contracts import (ArchiveError, ArchiveIdentity, CHUNK_BYTES, MAX_METADATA_BYTES,
    SIDECAR_NAME, json_hash, plain, reject_sensitive, require, sha256, unseal, validate_raw_metadata, validate_normalized_metadata)
from .archive_resolver import ArchiveResolver


class ArchiveReader:
    def __init__(self, root):
        self.resolver = root if isinstance(root, ArchiveResolver) else ArchiveResolver(root)

    def _metadata(self, directory, name):
        with self.resolver.open_read(self.resolver.file(directory, name)) as f:
            data = f.read(MAX_METADATA_BYTES + 1)
        require(len(data) <= MAX_METADATA_BYTES, "METADATA_SIZE_LIMIT")
        value = unseal(data)
        reject_sensitive(value)
        return value

    def read(self, uri, *, expected=None, include_raw=True):
        identity = ArchiveIdentity.parse(uri)
        directory = self.resolver.resolve(uri, expected=expected)
        try:
            if not self.resolver.file(directory, SIDECAR_NAME).exists():
                raise ArchiveError("ORPHAN_INCOMPLETE" if directory.exists() else "ARCHIVE_MISSING")
            meta = self._metadata(directory, SIDECAR_NAME)
            validate_raw_metadata(meta, identity)
            allowed = {SIDECAR_NAME} | {p["file"] for p in meta["parts"]}
            raw = {}
            with os.scandir(directory) as entries:
                for entry in entries:
                    require(not entry.is_symlink(), "SYMLINK_ESCAPE_REJECTED")
                    require(entry.is_file(follow_symlinks=False) and (entry.name in allowed or
                            re.fullmatch(r"(?:normalized|binding)-[a-f0-9]{64}\.json", entry.name)),
                            "DUPLICATE_OR_UNKNOWN_FILE")
            for p in meta["parts"]:
                path = self.resolver.file(directory, p["file"])
                with self.resolver.open_read(path) as f:
                    stored_hash = hashlib.sha256()
                    stored_size = 0
                    while chunk := f.read(CHUNK_BYTES):
                        stored_size += len(chunk)
                        require(stored_size <= p["stored_size"], "STORED_SIZE_MISMATCH")
                        stored_hash.update(chunk)
                    require(stored_size == p["stored_size"] and stored_hash.hexdigest() == p["stored_sha256"],
                            "STORED_HASH_MISMATCH")
                    f.seek(0)
                    raw_hash = hashlib.sha256()
                    size = 0
                    output = bytearray() if include_raw else None
                    with gzip.GzipFile(fileobj=f, mode="rb") as g:
                        while chunk := g.read(min(CHUNK_BYTES, p["raw_size"] - size + 1)):
                            size += len(chunk)
                            require(size <= p["raw_size"], "RAW_SIZE_MISMATCH")
                            raw_hash.update(chunk)
                            if output is not None:
                                output.extend(chunk)
                    require(size == p["raw_size"] and raw_hash.hexdigest() == p["raw_sha256"], "RAW_HASH_MISMATCH")
                    if output is not None:
                        raw[p["name"]] = bytes(output)
            return {"metadata": meta, "raw_parts": raw, "integrity_status": "VERIFIED"}
        except ArchiveError:
            raise
        except (OSError, ValueError, KeyError, TypeError, EOFError) as exc:
            raise ArchiveError("CORRUPT_ARCHIVE") from None

    def verify_record(self, record):
        record = plain(record)
        identity = ArchiveIdentity.parse(record["raw_response_path"])
        require(all(record[k] == getattr(identity, k) for k in
                    ("evidence_id", "system_run_id", "collection_task_id")), "ARCHIVE_CONTEXT_MISMATCH")
        meta = self.read(identity.uri, expected=identity, include_raw=False)["metadata"]
        raw_record = meta["record"]
        dynamic = {"normalization_rule", "validation_result", "normalized_data_hash", "analysis_output_hash",
                   "model_version", "human_confirm"}
        require({k: v for k, v in record.items() if k not in dynamic} ==
                {k: v for k, v in raw_record.items() if k not in dynamic}, "ARCHIVE_RECORD_MISMATCH")
        if record["normalized_data_hash"] is not None:
            normalized = self.normalized(identity.uri, record["normalized_data_hash"])
            require(normalized["normalization_rule"] == record["normalization_rule"] and
                    normalized["validation_result"] == record["validation_result"], "NORMALIZED_RECORD_MISMATCH")
        else:
            require(all(record[k] == raw_record[k] for k in ("normalization_rule", "validation_result")),
                    "NORMALIZED_RECORD_MISMATCH")
        require(record["analysis_output_hash"] is None, "ANALYSIS_OUTPUT_REQUIRES_BINDING")
        return meta

    def normalized(self, uri, content_hash):
        require(type(content_hash) is str and re.fullmatch(r"[a-f0-9]{64}", content_hash), "NORMALIZED_HASH_INVALID")
        self.read(uri, include_raw=False)
        directory = self.resolver.resolve(uri)
        try:
            value = self._metadata(directory, "normalized-" + content_hash + ".json")
            validate_normalized_metadata(value, uri, content_hash)
            return value
        except (OSError, KeyError, TypeError):
            raise ArchiveError("NORMALIZED_METADATA_MISSING_OR_CORRUPT") from None

    def binding(self, uri, analysis_report_id, expected):
        self.read(uri, include_raw=False)
        try:
            value = self._metadata(self.resolver.resolve(uri), "binding-" + json_hash(analysis_report_id) + ".json")
            require(value == {"kind": "ANALYSIS_BINDING", "uri": uri, "binding": plain(expected),
                              "analysis_output_hash": plain(expected).get("model_output_hash")},
                    "ANALYSIS_BINDING_MISMATCH")
            return value
        except (OSError, KeyError, TypeError):
            raise ArchiveError("ANALYSIS_BINDING_MISSING_OR_CORRUPT") from None
