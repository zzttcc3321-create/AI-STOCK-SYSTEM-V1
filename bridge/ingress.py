"""Minimal single-process HTTP ingestion receiver, never a data provider.

The receiver has no outgoing HTTP client and never constructs a TDX provider.
One durable receipt is the commit point for auth/idempotency/freshness/storage.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from typing import Any

from .contracts import (
    MAX_PACKET_BYTES, freshness, json_bytes, packet_hash, positive,
    stock_identifier, timestamp, utc_now, validate_packet,
)
from .pipeline import ProcessLock, atomic_write, materialize


class IngressError(Exception):
    def __init__(self, status: int, code: str) -> None:
        self.status, self.code = status, code
        super().__init__(code)


class IngressStore:
    """Caller must own ProcessLock; HTTPServer serializes all writes."""

    def __init__(self, root: Path | str, *, environ=None, clock=utc_now) -> None:
        env = os.environ if environ is None else environ
        self._credential = env.get("TDX_INGRESS_TOKEN")
        if (not isinstance(self._credential, str) or not self._credential or not self._credential.isascii()
                or any(c.isspace() for c in self._credential)):
            raise ValueError("TDX_INGRESS_TOKEN must be configured")
        self.root, self.clock = Path(root).resolve(), clock
        self.stale_after = positive(env.get("REALTIME_STALE_AFTER_SECONDS", "300"), "stale threshold")
        poll = positive(env.get("REALTIME_POLL_SECONDS", "60"), "poll interval", minimum=30)
        self.missing_after = positive(env.get("REALTIME_MISSING_AFTER_SECONDS", str(2 * poll)), "missing threshold")
        (self.root / "receipts").mkdir(parents=True, exist_ok=True)

    def receive(self, authorization: str | None, packet: Any) -> dict[str, Any]:
        expected = "Bearer " + self._credential
        if not isinstance(authorization, str) or not hmac.compare_digest(authorization.encode(), expected.encode()):
            raise IngressError(401, "UNAUTHORIZED")
        now = self.clock()
        try:
            validate_packet(packet, now=now, credential=self._credential)
            digest = packet_hash(packet)
        except (TypeError, ValueError, OverflowError, RecursionError):
            raise IngressError(422, "INVALID_ENVELOPE") from None
        key = hashlib.sha256(packet["request_id"].encode()).hexdigest()
        receipt_path = self.root / "receipts" / f"{key}.json"
        if receipt_path.exists():
            existing = json.loads(receipt_path.read_bytes())
            if existing["packet_hash"] != digest:
                raise IngressError(409, "IDEMPOTENCY_CONFLICT")
            return existing["ack"]
        state = freshness(packet["snapshot_timestamp"], now=now, stale_after=self.stale_after)
        result = materialize(packet, self.root / "evidence")
        ack = {"stored": True, "request_id": packet["request_id"], "packet_hash": digest,
               "raw_response_hash": packet["raw_response_hash"], "freshness": state,
               "evidence_id": result["evidence"]["evidence_id"]}
        receipt = {"packet_hash": digest, "received_at": now.isoformat(), "envelope": packet,
                   "result": result, "ack": ack}
        atomic_write(receipt_path, json_bytes(receipt), immutable=True)
        return ack

    def current(self, stock_id: str) -> dict[str, Any]:
        """Current projection is derived, never overwritten by late arrivals.

        No snapshot => MISSING. Receipt silence => MISSING even when a previous
        price remains on disk. An old newly received snapshot => STALE.
        """
        stock_identifier(stock_id)
        latest = None
        order = None
        with os.scandir(self.root / "receipts") as entries:
            for entry in entries:
                if not entry.name.endswith(".json") or not entry.is_file():
                    continue
                record = json.loads(Path(entry.path).read_bytes())
                packet = record["envelope"]
                if packet["stock_id"] != stock_id:
                    continue
                candidate = (timestamp(packet["snapshot_timestamp"] or packet["request_timestamp"]),
                             timestamp(packet["request_timestamp"]), packet["request_id"])
                if order is None or candidate > order:
                    latest, order = record, candidate
        state = "MISSING"
        if latest:
            packet = latest["envelope"]
            now = self.clock()
            if (now - timestamp(latest["received_at"])).total_seconds() <= self.missing_after:
                state = freshness(packet["snapshot_timestamp"], now=now, stale_after=self.stale_after)
        canonical = latest["result"]["canonical_data"] if latest and state == "REALTIME" else []
        return {"stock_id": stock_id, "realtime_quote_status": state,
                "freshness": state, "canonical_data": canonical,
                # A collection clock cannot prove the time of a market quote.
                "usable_for_realtime": bool(canonical) and all(q["freshness"] == "REALTIME" for q in canonical),
                "request_id": latest["envelope"]["request_id"] if latest else None}


def handler_for(store: IngressStore):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            # Never echo headers, untrusted paths or bodies (including tokens).
            return

        def do_POST(self):
            status, response = 200, None
            try:
                if self.path != "/realtime":
                    raise IngressError(404, "NOT_FOUND")
                # Authenticate before reading/parsing an untrusted body.
                expected = "Bearer " + store._credential
                supplied = self.headers.get("Authorization", "")
                if not hmac.compare_digest(supplied.encode(), expected.encode()):
                    raise IngressError(401, "UNAUTHORIZED")
                if self.headers.get("Transfer-Encoding"):
                    raise IngressError(400, "UNSUPPORTED_ENCODING")
                try:
                    size = int(self.headers.get("Content-Length", "0"))
                except ValueError:
                    raise IngressError(400, "INVALID_LENGTH") from None
                if size <= 0 or size > MAX_PACKET_BYTES:
                    raise IngressError(413, "INVALID_SIZE")
                if self.headers.get_content_type() != "application/json":
                    raise IngressError(415, "JSON_REQUIRED")
                self.connection.settimeout(10)
                data = self.rfile.read(size)
                if len(data) != size:
                    raise IngressError(400, "INCOMPLETE_BODY")
                response = store.receive(supplied, json.loads(data))
            except IngressError as exc:
                status, response = exc.status, {"error": exc.code}
            except (ValueError, UnicodeError, RecursionError):
                status, response = 422, {"error": "INVALID_ENVELOPE"}
            except (OSError, TypeError):
                status, response = 503, {"error": "STORAGE_UNAVAILABLE"}
            encoded = json_bytes(response)
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(encoded)))
            self.end_headers()
            self.wfile.write(encoded)

    return Handler


class IngestionHTTPServer(HTTPServer):
    def get_request(self):
        connection, address = super().get_request()
        # Bound the header phase too: one silent client must not stall the
        # single-process reference receiver indefinitely.
        connection.settimeout(10)
        return connection, address


def main() -> None:
    root = Path(os.environ.get("TDX_INGRESS_STATE_DIR", str(Path.home() / "tdx-ingress-state")))
    with ProcessLock(root / ".receiver.lock"):
        store = IngressStore(root)
        # Deployment supplies an HTTPS ingress/reverse proxy; no production
        # listener or TLS infrastructure is installed by this reference module.
        server = IngestionHTTPServer((os.environ.get("TDX_INGRESS_BIND", "127.0.0.1"),
                             int(os.environ.get("TDX_INGRESS_PORT", "8080"))), handler_for(store))
        server.timeout = 10
        try:
            server.serve_forever()
        finally:
            server.server_close()


if __name__ == "__main__":
    main()
