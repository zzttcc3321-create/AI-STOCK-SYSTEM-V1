"""One Task Scheduler invocation: collect, journal, send a bounded batch, exit."""

from __future__ import annotations

import argparse
import heapq
import json
import os
import sys
from datetime import timezone, timedelta
from pathlib import Path
from urllib import error, request
from urllib.parse import urlsplit
from uuid import uuid4

from providers import TdxLocalHttpProvider
from .contracts import (
    MAX_PACKET_BYTES, RUNTIME_NODE, SOURCE, TRANSPORT, freshness,
    json_bytes, packet_hash, positive, timestamp, utc_now, validate_packet, validate_units,
)
from .pipeline import ProcessLock, atomic_write, materialize


class NoRedirect(request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


class PushHTTPTransport:
    """No redirect/proxy: the scoped credential goes only to the configured URL."""

    def __init__(self, url: str, credential: str) -> None:
        target = urlsplit(url)
        if (target.scheme not in {"https", "http"} or not target.hostname
                or (target.scheme == "http" and target.hostname not in {"127.0.0.1", "::1"})
                or target.username or target.password or target.query or target.fragment):
            raise ValueError("ingress requires HTTPS (HTTP allowed only on numeric loopback)")
        if not credential or not credential.isascii() or any(c.isspace() for c in credential):
            raise ValueError("ingestion credential is not configured correctly")
        self._url, self._credential = url, credential
        self._opener = request.build_opener(request.ProxyHandler({}), NoRedirect())

    def send(self, packet):
        body = json_bytes(packet)
        outgoing = request.Request(self._url, data=body, method="POST", headers={
            "Authorization": "Bearer " + self._credential,
            "Content-Type": "application/json; charset=utf-8",
        })
        with self._opener.open(outgoing, timeout=10) as response:
            if response.status != 200:
                raise OSError("ingress rejected envelope")
            data = response.read(MAX_PACKET_BYTES + 1)
            if len(data) > MAX_PACKET_BYTES:
                raise ValueError("oversized acknowledgment")
            return json.loads(data)


def make_envelope(result, *, mode: str, units: dict, now, stale_after: float) -> dict:
    payload = result.payload
    packet = {field: payload[field] for field in (
        "stock_id", "request_id", "system_run_id", "collection_task_id", "snapshot",
        "snapshot_timestamp", "raw_response_b64", "raw_response_hash", "realtime_quote_status",
    )}
    packet.update(source=SOURCE, runtime_node=RUNTIME_NODE, transport=TRANSPORT,
                  request_timestamp=result.request_timestamp, provider_timestamp=result.provider_timestamp,
                  freshness=freshness(payload["snapshot_timestamp"], now=now, stale_after=stale_after),
                  collection_mode=mode, provider_status=result.status.value, units=validate_units(units))
    validate_packet(packet, now=now)
    return packet


class Outbox:
    """Durable envelopes, deleted only after a matching committed acknowledgment."""

    def __init__(self, root: Path | str, *, clock=utc_now) -> None:
        self.root, self.clock = Path(root).resolve(), clock
        self.pending = self.root / "pending"
        self.pending.mkdir(parents=True, exist_ok=True)

    def log(self, event: str, **safe_fields) -> None:
        # Callers pass only fixed event codes, validated identifiers and counts.
        with (self.root / "collector.log").open("ab") as handle:
            handle.write(json_bytes({"at": self.clock().isoformat(), "event": event, **safe_fields}) + b"\n")

    def count(self) -> int:
        with os.scandir(self.pending) as entries:
            return sum(1 for entry in entries if entry.is_file() and entry.name.endswith(".json"))

    def pending_bytes(self) -> int:
        with os.scandir(self.pending) as entries:
            return sum(entry.stat().st_size for entry in entries if entry.is_file() and entry.name.endswith(".json"))

    def enqueue(self, packet: dict, credential: str | None = None) -> None:
        validate_packet(packet, now=self.clock(), credential=credential)
        prefix = timestamp(packet["request_timestamp"]).strftime("%Y%m%dT%H%M%S%fZ")
        name = f"{prefix}-{packet['request_id']}.json"
        atomic_write(self.pending / name, json_bytes(packet), immutable=True)

    def flush(self, sender, *, batch_size: int = 20, credential: str | None = None) -> bool:
        if not 1 <= batch_size <= 100:
            raise ValueError("batch size must be between 1 and 100")
        with os.scandir(self.pending) as entries:
            names = heapq.nsmallest(batch_size, (entry.name for entry in entries if entry.is_file() and entry.name.endswith(".json")))
        for name in names:
            path = self.pending / name
            try:
                packet = json.loads(path.read_bytes())
                validate_packet(packet, now=self.clock(), credential=credential)
                materialize(packet, self.root / "evidence")
                ack = sender.send(packet)
                expected = {"request_id": packet["request_id"], "packet_hash": packet_hash(packet),
                            "raw_response_hash": packet["raw_response_hash"], "stored": True}
                if not isinstance(ack, dict) or any(ack.get(k) != v for k, v in expected.items()):
                    raise ValueError("invalid acknowledgment")
                path.unlink()
                self.log("PUSH_CONFIRMED", request_id=packet["request_id"])
            except error.HTTPError as exc:
                self.log("IDEMPOTENCY_CONFLICT" if exc.code == 409 else "INGRESS_REJECTED",
                         http_status=exc.code, pending_count=self.count())
                return False
            except (OSError, ValueError, TypeError, RecursionError):
                self.log("PUSH_PENDING", pending_count=self.count())
                return False
        remaining = self.count()
        self.log("BATCH_COMPLETE", pending_count=remaining)
        return remaining == 0


def market_session(now, *, skip_closed: bool) -> bool:
    if not skip_closed:
        return True
    local = now.astimezone(timezone(timedelta(hours=8)))
    minute = local.hour * 60 + local.minute
    # Weekday clock window only; holidays are intentionally not guessed.
    return local.weekday() < 5 and (570 <= minute < 690 or 780 <= minute < 900)


def run_once(stock_id: str, *, state_dir: Path | str, mode="AUTO", environ=None,
             provider=None, sender=None, clock=utc_now, batch_size=20, push_only=False) -> int:
    env = os.environ if environ is None else environ
    poll = positive(env.get("REALTIME_POLL_SECONDS", "60"), "poll interval", minimum=30)
    stale = positive(env.get("REALTIME_STALE_AFTER_SECONDS", "300"), "stale threshold")
    units = validate_units(json.loads(env.get("TDX_VERIFIED_UNITS_JSON", "{}")))
    maximum_pending = positive(env.get("TDX_OUTBOX_MAX_BYTES", str(100 * MAX_PACKET_BYTES)),
                               "outbox size", minimum=MAX_PACKET_BYTES)
    if not 1 <= batch_size <= 100:
        raise ValueError("batch size must be between 1 and 100")
    if mode not in {"AUTO", "MANUAL"}:
        raise ValueError("collection mode requires AUTO or MANUAL")
    root = Path(state_dir).resolve()
    with ProcessLock(root / ".collector.lock"):
        outbox = Outbox(root, clock=clock)
        failed_local = False
        marker = root / "last-auto-collection.json"
        collect = not push_only and market_session(clock(), skip_closed=env.get("TDX_SKIP_CLOSED_SESSIONS", "0") == "1")
        capacity_blocked = collect and outbox.pending_bytes() > maximum_pending - MAX_PACKET_BYTES
        if capacity_blocked:
            collect = False
            outbox.log("OUTBOX_CAPACITY_COLLECTION_PAUSED", pending_count=outbox.count())
        if mode == "AUTO" and marker.exists() and collect:
            last = timestamp(json.loads(marker.read_bytes())["at"])
            collect = (clock() - last).total_seconds() >= poll
            if not collect:
                outbox.log("POLL_THROTTLED", pending_count=outbox.count())
        if collect:
            local = provider if provider is not None else TdxLocalHttpProvider(environ=env, clock=clock)
            result = local.fetch({"data_kind": "realtime_quote", "stock_id": stock_id,
                                  "request_id": uuid4().hex, "system_run_id": uuid4().hex,
                                  "collection_task_id": uuid4().hex})
            if not local.validate_contract(result, expected_data_kind="realtime_quote").valid:
                raise ValueError("provider contract validation failed")
            packet = make_envelope(result, mode=mode, units=units, now=clock(), stale_after=stale)
            outbox.enqueue(packet, env.get("TDX_INGRESS_TOKEN"))
            failed_local = packet["realtime_quote_status"] == "MISSING"
            outbox.log("TDX_MISSING" if failed_local else "COLLECTED", request_id=packet["request_id"], stock_id=stock_id)
            if mode == "AUTO":
                atomic_write(marker, json_bytes({"at": result.request_timestamp}))
        if not outbox.count():
            outbox.log("NOTHING_PENDING")
            return 0
        # Always materialize evidence before attempting the HTTP client, even
        # if its URL/credential configuration is unavailable this invocation.
        # flush() also materializes each recovered envelope before its send.
        if collect:
            materialize(packet, root / "evidence")
        try:
            transport = sender if sender is not None else PushHTTPTransport(
                env.get("TDX_INGRESS_URL", ""), env.get("TDX_INGRESS_TOKEN", ""))
            completed = outbox.flush(transport, batch_size=batch_size, credential=env.get("TDX_INGRESS_TOKEN"))
        except (OSError, ValueError):
            outbox.log("INGRESS_UNAVAILABLE", pending_count=outbox.count())
            completed = False
        # Local MISSING takes precedence if both endpoints are down; pending
        # count and PUSH_PENDING separately expose the cloud failure.
        return 1 if failed_local else (0 if completed and not capacity_blocked else 2)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("stock_id")
    parser.add_argument("--collection-mode", choices=("AUTO", "MANUAL"), default="AUTO")
    parser.add_argument("--state-dir", default=os.environ.get("TDX_PUSH_STATE_DIR", str(Path.home() / "tdx-push-state")))
    parser.add_argument("--batch-size", type=int, default=20)
    parser.add_argument("--push-only", action="store_true", help="recover pending envelopes without fetching TDX")
    args = parser.parse_args(argv)
    try:
        return run_once(args.stock_id, state_dir=args.state_dir, mode=args.collection_mode,
                        batch_size=args.batch_size, push_only=args.push_only)
    except (OSError, ValueError, TypeError, KeyError):
        # Never print an exception message: a URL/header/body may contain a credential.
        print("TDX_PUSH_CONFIGURATION_OR_STATE_ERROR", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
