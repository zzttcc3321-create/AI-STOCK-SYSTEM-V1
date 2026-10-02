"""Pure wire rules shared by the local collector and the ingress.

Snapshot freshness measures collection age. It never supplies a missing
provider clock or a missing unit declaration to the frozen normalizer.
"""

from __future__ import annotations

import base64
import hashlib
import json
import math
import re
from datetime import datetime, timezone
from typing import Any, Mapping

SOURCE = "TDX_OFFICIAL_LOCAL_HTTP"
RUNTIME_NODE = "QI_WINDOWS_LOCAL"
TRANSPORT = "WINDOWS_TO_AWS_PUSH"
MAX_PACKET_BYTES = 1024 * 1024
SCALARS = {
    "now": "Now", "last_close": "LastClose", "open": "Open", "high": "Max",
    "low": "Min", "volume": "Volume", "amount": "Amount", "average": "Average",
}
BOOK = {
    "bid_prices": "Buyp", "bid_volumes": "Buyv",
    "ask_prices": "Sellp", "ask_volumes": "Sellv",
}
FIELDS = frozenset({
    "stock_id", "source", "runtime_node", "transport", "request_timestamp",
    "provider_timestamp", "snapshot_timestamp", "raw_response_hash", "request_id",
    "system_run_id", "collection_task_id", "freshness", "collection_mode",
    "realtime_quote_status", "provider_status", "raw_response_b64", "snapshot", "units",
})
SENSITIVE_KEYS = frozenset({"token", "password", "cookie", "session", "apikey", "authorization"})


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def timestamp(value: Any) -> datetime:
    if not isinstance(value, str):
        raise ValueError("timestamp must be a timezone-aware ISO string")
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("timestamp requires a timezone")
    return parsed.astimezone(timezone.utc)


def positive(value: Any, name: str, *, minimum: float = 0) -> float:
    number = float(value)
    if not math.isfinite(number) or number <= 0 or number < minimum:
        raise ValueError(f"invalid {name}")
    return number


def identifier(value: Any) -> str:
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", value):
        raise ValueError("invalid identifier")
    return value


def stock_identifier(value: Any) -> str:
    if not isinstance(value, str) or not re.fullmatch(r"[0-9]{6}\.(SH|SZ|BJ)", value):
        raise ValueError("stock_id requires six digits and SH/SZ/BJ suffix")
    return value


def json_bytes(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")


def packet_hash(packet: Mapping[str, Any]) -> str:
    return hashlib.sha256(json_bytes(packet)).hexdigest()


def check_sensitive(value: Any, credential: str | None = None) -> None:
    """Reject unexpected secret material instead of changing raw evidence."""
    if isinstance(value, Mapping):
        for key, item in value.items():
            normalized = "".join(c for c in str(key).lower() if c.isalnum())
            if any(normalized.endswith(name) for name in SENSITIVE_KEYS):
                raise ValueError("sensitive material is forbidden")
            check_sensitive(str(key), credential)
            check_sensitive(item, credential)
    elif isinstance(value, (list, tuple)):
        for item in value:
            check_sensitive(item, credential)
    elif isinstance(value, str) and credential and credential in value:
        raise ValueError("sensitive material is forbidden")


def map_snapshot(raw: Any) -> dict[str, Any]:
    if not isinstance(raw, dict) or raw.get("error") is not None:
        raise ValueError("invalid RPC response")
    result = raw.get("result")
    if not isinstance(result, dict) or result.get("ErrorId") != "0":
        raise ValueError("TDX did not return success")

    def numeric(value: Any) -> Any:
        try:
            valid = (not isinstance(value, bool) and isinstance(value, (int, float))
                     and math.isfinite(value) and value >= 0)
        except OverflowError:
            valid = False
        if not valid:
            raise ValueError("snapshot field must be a finite nonnegative JSON number")
        return value

    snapshot = {name: numeric(result.get(original)) for name, original in SCALARS.items()}
    for name, original in BOOK.items():
        values = result.get(original)
        if not isinstance(values, list) or len(values) != 5:
            raise ValueError("order book requires exactly five levels")
        snapshot[name] = [numeric(value) for value in values]
    return snapshot


def validate_units(units: Any) -> dict[str, str]:
    allowed = {
        "price": {"元", "yuan", "cny"},
        "volume": {"股", "share", "shares", "手", "lot", "lots"},
        "amount": {"元", "yuan", "cny", "万元", "万人民币", "10k yuan"},
    }
    if not isinstance(units, dict) or set(units) - set(allowed):
        raise ValueError("invalid unit declaration")
    if any(not isinstance(v, str) or v not in allowed[k] for k, v in units.items()):
        raise ValueError("unsupported unit declaration")
    return dict(units)


def freshness(snapshot_timestamp: str | None, *, now: datetime, stale_after: float) -> str:
    positive(stale_after, "stale_after")
    if snapshot_timestamp is None:
        return "MISSING"
    age = (now - timestamp(snapshot_timestamp)).total_seconds()
    if age < 0:
        raise ValueError("future snapshot is forbidden")
    return "STALE" if age > stale_after else "REALTIME"


def raw_bytes(packet: Mapping[str, Any]) -> bytes | None:
    encoded = packet["raw_response_b64"]
    if encoded is None:
        return None
    if not isinstance(encoded, str):
        raise ValueError("raw response must use base64")
    data = base64.b64decode(encoded, validate=True)
    if hashlib.sha256(data).hexdigest() != packet["raw_response_hash"]:
        raise ValueError("raw hash mismatch")
    return data


def validate_packet(packet: Any, *, now: datetime, credential: str | None = None) -> None:
    if not isinstance(packet, dict) or set(packet) != FIELDS:
        raise ValueError("unexpected or missing envelope fields")
    if len(json_bytes(packet)) > MAX_PACKET_BYTES:
        raise ValueError("packet exceeds size limit")
    check_sensitive(packet, credential)
    stock_identifier(packet["stock_id"])
    for field in ("request_id", "system_run_id", "collection_task_id"):
        identifier(packet[field])
    if (packet["source"], packet["runtime_node"], packet["transport"]) != (SOURCE, RUNTIME_NODE, TRANSPORT):
        raise ValueError("identity mismatch")
    if packet["collection_mode"] not in {"AUTO", "MANUAL"}:
        raise ValueError("invalid collection mode")
    requested = timestamp(packet["request_timestamp"])
    if requested > now:
        raise ValueError("future request is forbidden")
    # The verified method has no documented provider clock. Do not invent one.
    if packet["provider_timestamp"] is not None:
        raise ValueError("verified protocol does not supply a provider timestamp")
    validate_units(packet["units"])
    data = raw_bytes(packet)
    raw = None
    rpc_id_mismatch = False
    if data is not None:
        try:
            raw = json.loads(data)
        except (ValueError, UnicodeError):
            if credential and credential.encode("utf-8") in data:
                raise ValueError("sensitive material is forbidden") from None
        check_sensitive(raw, credential)
        rpc_id_mismatch = isinstance(raw, dict) and "id" in raw and raw["id"] != packet["request_id"]
    elif packet["raw_response_hash"] is not None:
        raise ValueError("hash without raw response")
    if packet["realtime_quote_status"] == "MISSING":
        if packet["snapshot"] is not None or packet["snapshot_timestamp"] is not None or packet["freshness"] != "MISSING":
            raise ValueError("missing event must not carry a snapshot")
        if packet["provider_status"] not in {"UNAVAILABLE", "TIMEOUT", "UNKNOWN", "VALIDATION_FAILED"}:
            raise ValueError("invalid missing provider status")
        try:
            map_snapshot(raw)
        except ValueError:
            pass
        else:
            if not rpc_id_mismatch or packet["provider_status"] != "VALIDATION_FAILED":
                raise ValueError("successful raw response cannot claim MISSING")
    else:
        if packet["realtime_quote_status"] != "REALTIME" or packet["provider_status"] != "AVAILABLE":
            raise ValueError("invalid snapshot status")
        if packet["freshness"] not in {"REALTIME", "STALE"}:
            raise ValueError("invalid collection freshness")
        if rpc_id_mismatch:
            raise ValueError("RPC identity mismatch")
        if not isinstance(packet["snapshot"], dict) or packet["snapshot"] != map_snapshot(raw):
            raise ValueError("snapshot does not match raw response")
        collected = timestamp(packet["snapshot_timestamp"])
        if not requested <= collected <= now:
            raise ValueError("invalid collection clock ordering")
