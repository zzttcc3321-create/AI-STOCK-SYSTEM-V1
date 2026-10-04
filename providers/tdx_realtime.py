"""Windows-only, read-only adapter for the verified TDX JSON-RPC method."""

from __future__ import annotations

import base64
import hashlib
import http.client
import json
import os
import sys
from typing import Any, Mapping
from urllib.parse import urlsplit
from uuid import uuid4

from bridge.contracts import (
    MAX_PACKET_BYTES, SOURCE, check_sensitive, identifier, map_snapshot, positive,
    stock_identifier, utc_now,
)
from evidence import ArchiveIdentity, ArchiveWriter
from evidence.archive_contracts import plain
from .contract import ContractError, ContractErrorCode, ProviderEnvelope, ProviderStatus
from .interface import Provider


class LocalHTTPTransport:
    """Direct loopback HTTP, with separate connect/read deadlines and no proxy."""

    def __init__(self, endpoint: str) -> None:
        url = urlsplit(endpoint)
        if (url.scheme != "http" or url.hostname not in {"127.0.0.1", "::1"}
                or url.username or url.password or url.query or url.fragment
                or url.path not in {"", "/"}):
            raise ValueError("TDX endpoint must be plain HTTP on numeric loopback")
        self.host, self.port = url.hostname, url.port or 17709

    def _request(self, method: str, body: bytes | None, connect_timeout: float, read_timeout: float):
        connection = http.client.HTTPConnection(self.host, self.port, timeout=connect_timeout)
        try:
            connection.connect()
            connection.sock.settimeout(read_timeout)
            connection.request(method, "/", body=body, headers={"Content-Type": "application/json; charset=utf-8"})
            response = connection.getresponse()
            data = response.read(MAX_PACKET_BYTES + 1)
            if len(data) > MAX_PACKET_BYTES:
                raise ValueError("TDX response exceeds size limit")
            if response.status >= 400:
                raise OSError("TDX HTTP rejection")
            return data
        finally:
            connection.close()

    def post(self, body: bytes, *, connect_timeout: float, read_timeout: float) -> bytes:
        return self._request("POST", body, connect_timeout, read_timeout)

    def health_check(self, *, timeout: float) -> bool:
        self._request("HEAD", None, timeout / 2, timeout / 2)
        return True


class TdxLocalHttpProvider(Provider):
    """No fallback, no automatic retries, no investment or unit inference."""

    def __init__(self, transport=None, *, environ: Mapping[str, str] | None = None, clock=utc_now,
                 archive_writer: ArchiveWriter | None = None) -> None:
        env = os.environ if environ is None else environ
        self.endpoint = env.get("TDX_LOCAL_ENDPOINT", "http://127.0.0.1:17709/")
        default_transport = LocalHTTPTransport(self.endpoint)
        self._transport = transport if transport is not None else default_transport
        self._local_runtime = transport is not None or sys.platform == "win32"
        self.connect_timeout = positive(env.get("TDX_CONNECT_TIMEOUT", "3"), "connect timeout")
        self.read_timeout = positive(env.get("TDX_READ_TIMEOUT", "10"), "read timeout")
        self._clock = clock
        self._archive = archive_writer

    def describe(self) -> Mapping[str, Any]:
        return {"provider_name": SOURCE, "data_kinds": ("realtime_quote",), "read_only": True,
                "runtime_node": "QI_WINDOWS_LOCAL", "methods": ("get_market_snapshot",),
                "connect_timeout_seconds": self.connect_timeout, "read_timeout_seconds": self.read_timeout}

    def health_check(self) -> ProviderStatus:
        if not self._local_runtime:
            return ProviderStatus.UNKNOWN
        try:
            return ProviderStatus.AVAILABLE if self._transport.health_check(timeout=5) else ProviderStatus.UNAVAILABLE
        except TimeoutError:
            return ProviderStatus.TIMEOUT
        except (OSError, ValueError, http.client.HTTPException):
            return ProviderStatus.UNAVAILABLE

    def fetch(self, request: Mapping[str, Any] | str) -> ProviderEnvelope:
        if isinstance(request, str):
            request = {"stock_id": request, "data_kind": "realtime_quote"}
        at = self._clock().isoformat()
        payload = {"realtime_quote_status": "MISSING", "freshness": "MISSING", "snapshot": None,
                   "snapshot_timestamp": None, "raw_response_b64": None, "raw_response_hash": None}
        raw_reference = None

        def result(status, code=None, message=None):
            return ProviderEnvelope(SOURCE, "realtime_quote", status, at, None, raw_reference, payload,
                                    ContractError(code, message) if code else None)

        try:
            if request.get("data_kind") != "realtime_quote":
                return result(ProviderStatus.VALIDATION_FAILED, ContractErrorCode.INVALID_DATA_KIND_SCOPE,
                              "TDX only supports realtime_quote")
            stock_id = stock_identifier(request.get("stock_id"))
            if self._archive is not None and "evidence_id" in request:
                identifier(request["evidence_id"])
            payload.update({"stock_id": stock_id, **{
                field: identifier(request.get(field, uuid4().hex))
                for field in ("request_id", "system_run_id", "collection_task_id")
            }})
        except (AttributeError, TypeError, ValueError):
            return result(ProviderStatus.VALIDATION_FAILED, ContractErrorCode.MISSING_REQUIRED_FIELD,
                          "invalid realtime request")
        if not self._local_runtime:
            return result(ProviderStatus.UNKNOWN)
        body = json.dumps({"jsonrpc": "2.0", "id": payload["request_id"], "method": "get_market_snapshot",
                           "params": {"stock_code": stock_id}}).encode("utf-8")
        try:
            data = self._transport.post(body, connect_timeout=self.connect_timeout, read_timeout=self.read_timeout)
            if not isinstance(data, bytes) or len(data) > MAX_PACKET_BYTES:
                raise ValueError("invalid transport response")
            try:
                raw = json.loads(data)
            except (ValueError, UnicodeError):
                raw = None
            if raw is not None:
                check_sensitive(raw)
            payload["raw_response_b64"] = base64.b64encode(data).decode("ascii")
            payload["raw_response_hash"] = hashlib.sha256(data).hexdigest()
            source_reference = (f"tdx-local/{payload['system_run_id']}/{payload['collection_task_id']}/"
                             f"{payload['request_id']}/raw.json")
            payload["source_reference"] = source_reference
            if self._archive is not None:
                identity = ArchiveIdentity(payload["system_run_id"], payload["collection_task_id"],
                                           payload["request_id"], request.get("evidence_id", uuid4().hex))
                receipt = self._archive.write(
                    identity=identity, source=SOURCE, raw_parts={"response": data},
                    endpoint_or_tool="get_market_snapshot", request_params={"stock_id": stock_id},
                    request_timestamp=at, source_reference=source_reference)
                raw_reference = receipt["uri"]
                payload["archive_record"] = plain(receipt["record"])
            if isinstance(raw, dict) and "id" in raw and raw["id"] != payload["request_id"]:
                raise ValueError("RPC identity mismatch")
            snapshot = map_snapshot(raw)
        except TimeoutError:
            return result(ProviderStatus.TIMEOUT)
        except (OSError, http.client.HTTPException):
            return result(ProviderStatus.UNAVAILABLE)
        except (TypeError, ValueError, UnicodeError, OverflowError, RecursionError):
            return result(ProviderStatus.VALIDATION_FAILED, ContractErrorCode.MISSING_REQUIRED_FIELD,
                          "TDX response failed snapshot validation")
        payload.update(snapshot=snapshot, snapshot_timestamp=self._clock().isoformat(),
                       realtime_quote_status="REALTIME", freshness="REALTIME")
        return result(ProviderStatus.AVAILABLE)
