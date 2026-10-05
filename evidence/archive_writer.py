"""Synchronous append-only writer; private root, atomic commits, no daemon."""
from contextlib import contextmanager
import gzip
import os
from pathlib import Path
import shutil
from types import SimpleNamespace
from uuid import uuid4

from .archive_contracts import (ArchiveError, ArchiveIdentity, MAX_RAW_BYTES, MAX_METADATA_BYTES,
    MAX_PARTS, SIDECAR_NAME, bundle_hash, canonical_json, identifier, json_hash, plain,
    reject_secret_echo, reject_sensitive, require, sanitized, sealed, sha256,
    validate_raw_metadata, validate_source_reference, validate_normalized_metadata)
from .archive_reader import ArchiveReader
from .archive_resolver import ArchiveResolver
from .builder import EvidenceBuilder


class ArchiveWriter:
    def __init__(self, root, *, quota_bytes=None, disk_usage=shutil.disk_usage):
        self.resolver = ArchiveResolver(root)
        self.reader = ArchiveReader(self.resolver)
        require(quota_bytes is None or type(quota_bytes) is int and quota_bytes >= 0, "QUOTA_INVALID")
        self.quota_bytes = quota_bytes  # Can only lower, never raise the 5% cap.
        self._disk_usage = disk_usage

    def _mkdir(self, path):
        self.resolver.safe(path)
        missing = []
        candidate = path
        while not candidate.exists():
            missing.append(candidate)
            candidate = candidate.parent
        for directory in reversed(missing):
            self.resolver.safe(directory.parent)
            directory.mkdir(mode=0o700, exist_ok=True)
            self._sync_dir(directory.parent)
        self.resolver.safe(path)

    @contextmanager
    def _lock(self):
        self._mkdir(self.resolver.root)
        lock = self.resolver.root / ".archive-write-lock"
        self.resolver.safe(lock)
        try:
            lock.mkdir(mode=0o700)
        except FileExistsError:
            raise ArchiveError("ARCHIVE_WRITER_BUSY") from None
        try:
            yield
        finally:
            lock.rmdir()

    def _quota(self, extra):
        limit = self._disk_usage(self.resolver.root).total // 20
        if self.quota_bytes is not None:
            limit = min(limit, self.quota_bytes)
        used = max(self.resolver.root.stat().st_size, getattr(self.resolver.root.stat(), "st_blocks", 0) * 512)
        def allocated(directory, depth=0):
            require(depth <= 8, "ARCHIVE_DIRECTORY_DEPTH_LIMIT")
            total = 0
            with os.scandir(directory) as entries:
                for entry in entries:
                    path = self.resolver.safe(Path(entry.path))
                    info = path.stat()
                    total += max(info.st_size, getattr(info, "st_blocks", 0) * 512)
                    if entry.is_dir(follow_symlinks=False):
                        total += allocated(path, depth + 1)
            return total
        # At most nine nested iterators, independent of archive volume. Include
        # every file/directory, including pending and orphaned material.
        used += allocated(self.resolver.root)
        require(used + extra <= limit, "ARCHIVE_DISK_QUOTA_EXCEEDED")
        require(extra <= self._disk_usage(self.resolver.root).free, "ARCHIVE_DISK_FULL")

    def _sync_dir(self, path):
        # Windows lacks a directory fsync in the stdlib. Fail closed instead of
        # claiming a durable commit on a platform without this primitive.
        fd = os.open(path, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0))
        try:
            os.fsync(fd)
        finally:
            os.close(fd)

    def _write_file(self, path, data):
        self.resolver.safe(path)
        with path.open("xb") as f:
            if hasattr(os, "fchmod"):
                os.fchmod(f.fileno(), 0o600)
            require(f.write(data) == len(data), "PARTIAL_WRITE")
            f.flush()
            os.fsync(f.fileno())

    def write(self, *, identity, source, raw_parts, endpoint_or_tool, request_params,
              request_timestamp, provider_timestamp=None, source_reference=None,
              attributes=None, secrets=()):
        require(type(identity) is ArchiveIdentity, "ARCHIVE_IDENTITY_REQUIRED")
        source = source.value if hasattr(source, "value") else source
        params = sanitized(request_params)
        validate_source_reference(source, source_reference, identity, params)
        require(isinstance(raw_parts, dict) and 1 <= len(raw_parts) <= MAX_PARTS, "RAW_PARTS_INVALID")
        total = 0
        parts, compressed = [], {}
        for name, data in raw_parts.items():
            identifier(name)
            require(type(data) is bytes, "EXACT_RAW_BYTES_REQUIRED")
            total += len(data)
            require(total <= MAX_RAW_BYTES, "RAW_SIZE_LIMIT")
            reject_secret_echo(data, secrets)
            stored = gzip.compress(data, mtime=0)
            compressed[name + ".raw.gz"] = stored
            parts.append(dict(name=name, file=name + ".raw.gz", raw_size=len(data), raw_sha256=sha256(data),
                              stored_size=len(stored), stored_sha256=sha256(stored)))
        record = EvidenceBuilder().build(
            fetch_result=SimpleNamespace(provider_name=source, status="AVAILABLE", raw_reference=identity.uri,
                                         request_timestamp=request_timestamp, provider_timestamp=provider_timestamp),
            endpoint_or_tool=endpoint_or_tool, request_params=params,
            collection_task_id=identity.collection_task_id, system_run_id=identity.system_run_id,
            evidence_id=identity.evidence_id, raw_response_hash=bundle_hash(parts))
        meta = dict(version="V1.0", kind="RAW", identity=plain(identity), uri=identity.uri, source=source,
                    source_reference=source_reference, record=plain(record), parts=parts,
                    raw_response_hash=bundle_hash(parts), attributes=plain(attributes or {}))
        validate_raw_metadata(meta, identity)
        encoded = sealed(meta)
        require(len(encoded) <= MAX_METADATA_BYTES, "METADATA_SIZE_LIMIT")
        # Credential guards cover metadata values too; never retain a credential
        # merely because it arrived under an innocent field name.
        reject_secret_echo(encoded, secrets)
        directory = self.resolver.resolve(identity.uri)
        with self._lock():
            if directory.exists():
                existing = self.reader.read(identity.uri, include_raw=False)["metadata"]
                require(existing == meta, "ARCHIVE_IDENTITY_CONFLICT")
                return {"status": "IDEMPOTENT_NOOP", "uri": identity.uri, "record": record, "metadata": meta}
            self._quota(sum(((len(v) + 4095) // 4096) * 4096 for v in compressed.values()) +
                        ((len(encoded) + 4095) // 4096) * 4096 + 5 * 4096)
            self._mkdir(directory.parent)
            staging = directory.parent / (".pending-" + uuid4().hex)
            staging.mkdir(mode=0o700)
            renamed = False
            try:
                for name, data in compressed.items():
                    self._write_file(staging / name, data)
                self._write_file(staging / SIDECAR_NAME, encoded)
                self._quota(0)
                self._sync_dir(staging)
                self.resolver.safe(directory)
                require(not directory.exists(), "ARCHIVE_IDENTITY_CONFLICT")
                os.rename(staging, directory)
                renamed = True
                self._sync_dir(directory.parent)
            except Exception:
                # A failure after rename is unacknowledged. Remove only the new
                # commit marker; retain raw as ORPHAN_INCOMPLETE for inspection.
                if renamed or directory.exists():
                    (directory / SIDECAR_NAME).unlink(missing_ok=True)
                if staging.exists():
                    shutil.rmtree(staging)
                raise
            self.reader.read(identity.uri, include_raw=False)
            return {"status": "ARCHIVED", "uri": identity.uri, "record": record, "metadata": meta}

    def _append(self, uri, filename, payload):
        reject_sensitive(payload)
        data = sealed(payload)
        require(len(data) <= MAX_METADATA_BYTES, "METADATA_SIZE_LIMIT")
        directory = self.resolver.resolve(uri)
        with self._lock():
            self.reader.read(uri, include_raw=False)
            target = self.resolver.file(directory, filename)
            if target.exists():
                require(self.reader._metadata(directory, filename) == payload, "ARCHIVE_IDENTITY_CONFLICT")
                return "IDEMPOTENT_NOOP"
            self._quota(((len(data) + 4095) // 4096) * 4096 + 4096)
            # Staging outside the committed directory prevents readers from
            # mistaking an in-progress append for a committed metadata event.
            staging = directory.parent / (".pending-" + uuid4().hex)
            linked = False
            try:
                self._write_file(staging, data)
                self._quota(0)
                # link() has atomic create-if-absent semantics; never replace.
                os.link(staging, target)
                linked = True
                self._sync_dir(directory)
            except Exception:
                if linked or target.exists():
                    target.unlink(missing_ok=True)
                raise
            finally:
                staging.unlink(missing_ok=True)
        return "APPENDED"

    def append_normalized(self, uri, *, normalized_data, normalization_rule, validation_result):
        payload = dict(kind="NORMALIZED", uri=uri, normalized_data=plain(normalized_data),
                       normalized_data_hash=json_hash(normalized_data), normalization_rule=plain(normalization_rule),
                       validation_result=plain(validation_result))
        validate_normalized_metadata(payload, uri, payload["normalized_data_hash"])
        self._append(uri, "normalized-" + payload["normalized_data_hash"] + ".json", payload)
        return payload

    def append_analysis_binding(self, uri, *, binding):
        binding = plain(binding)
        require(type(binding) is dict and type(binding.get("analysis_report_id")) is str,
                "ANALYSIS_BINDING_INVALID")
        return self._append(uri, "binding-" + json_hash(binding["analysis_report_id"]) + ".json",
                            dict(kind="ANALYSIS_BINDING", uri=uri, binding=binding,
                                 analysis_output_hash=binding.get("model_output_hash")))
