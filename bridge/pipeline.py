"""File sidecars consuming the frozen normalizer and Evidence Builder APIs."""

from __future__ import annotations

import os
import tempfile
from dataclasses import fields, is_dataclass
from enum import Enum
from pathlib import Path
from typing import Any, Mapping

from evidence import EvidenceBuilder
from normalizer import DataNormalizer
from providers.contract import ProviderEnvelope, ProviderStatus
from .contracts import SOURCE, json_bytes, packet_hash, raw_bytes


def jsonable(value: Any) -> Any:
    if is_dataclass(value):
        return {field.name: jsonable(getattr(value, field.name)) for field in fields(value)}
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, Mapping):
        return {str(k): jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [jsonable(item) for item in value]
    return value


def atomic_write(path: Path, data: bytes, *, immutable: bool = False) -> None:
    """Single-process commit: fsync data, then atomically replace its name.

    Immutable files are only ever written once; an interrupted first write may
    leave a harmless temporary file, never a partial committed JSON record.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    if immutable and path.exists():
        if path.read_bytes() != data:
            raise ValueError("immutable file conflict")
        return
    descriptor, name = tempfile.mkstemp(prefix=".pending-", dir=path.parent)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(name, path)
        # Directory fsync is unavailable on Windows; file fsync + replace is
        # still used there. On POSIX it also persists the rename across reboot.
        if os.name == "posix":
            descriptor = os.open(path.parent, os.O_RDONLY)
            try:
                os.fsync(descriptor)
            finally:
                os.close(descriptor)
    finally:
        if os.path.exists(name):
            os.unlink(name)


class ProcessLock:
    """OS file lock: released on process death, with no stale lockfile race."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self.handle = None

    def __enter__(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.handle = self.path.open("a+b")
        try:
            if os.name == "nt":
                import msvcrt
                if self.path.stat().st_size == 0:
                    self.handle.write(b"0")
                    self.handle.flush()
                self.handle.seek(0)
                msvcrt.locking(self.handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(self.handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            self.handle.close()
            self.handle = None
            raise OSError("another ingestion process owns the state directory") from None
        return self

    def __exit__(self, *args):
        if self.handle is not None:
            self.handle.close()


def materialize(packet: dict[str, Any], root: Path) -> dict[str, Any]:
    """Persist byte-exact raw response and append-only normalization/evidence."""
    folder = root / packet["system_run_id"] / packet["collection_task_id"] / packet["request_id"]
    data = raw_bytes(packet)
    raw_path = folder / "raw.json" if data is not None else None
    if raw_path is not None:
        atomic_write(raw_path, data, immutable=True)
    snapshot = packet["snapshot"]
    payload = None
    if snapshot is not None:
        payload = {"stock_id": packet["stock_id"], "price": snapshot["now"],
                   "bid": snapshot["bid_prices"][0], "ask": snapshot["ask_prices"][0],
                   "volume": snapshot["volume"], "amount": snapshot["amount"],
                   "quote_timestamp": packet["provider_timestamp"], "freshness": "UNKNOWN",
                   "units": packet["units"]}
    envelope = ProviderEnvelope(SOURCE, "realtime_quote", ProviderStatus(packet["provider_status"]),
                                packet["request_timestamp"], packet["provider_timestamp"],
                                str(raw_path) if raw_path else None, payload, None)
    normalization = DataNormalizer().normalize(envelope) if snapshot is not None else None
    evidence = EvidenceBuilder().build(
        fetch_result=envelope, endpoint_or_tool="get_market_snapshot",
        request_params={"stock_code": packet["stock_id"], "jsonrpc": "2.0", "request_id": packet["request_id"]},
        collection_task_id=packet["collection_task_id"], system_run_id=packet["system_run_id"],
        normalization_result=normalization, raw_response_hash=packet["raw_response_hash"],
        evidence_id=packet_hash({"request_id": packet["request_id"]}),
    )
    result = {"evidence": jsonable(evidence), "normalization": jsonable(normalization),
              "canonical_data": jsonable(normalization.canonical_data) if normalization else []}
    atomic_write(folder / "evidence.json", json_bytes(result), immutable=True)
    return result
