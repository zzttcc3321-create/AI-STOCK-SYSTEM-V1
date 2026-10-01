"""Read-only Tushare provider for historical daily bars."""

from __future__ import annotations

from datetime import date, datetime, timezone
import http.client
import json
import os
import socket
from typing import Any, Mapping, Protocol, Sequence
from uuid import uuid4

from .contract import ContractError, ContractErrorCode, ProviderEnvelope, ProviderStatus
from .interface import Provider

PROVIDER_NAME = "TUSHARE_MCP"
DATA_KIND = "historical_daily"
CONNECT_TIMEOUT_SECONDS = 10
READ_TIMEOUT_SECONDS = 30
TUSHARE_API_URL = "https://api.tushare.pro"
REQUIRED_BAR_FIELDS = frozenset(
    {"open", "high", "low", "close", "pre_close", "change", "pct_chg", "vol", "amount"}
)


class TushareTransport(Protocol):
    """Narrow seam used to keep provider tests fully offline."""

    def fetch_daily(
        self, token: str, stock_id: str, date_from: str, date_to: str, *, connect_timeout: int, read_timeout: int
    ) -> Mapping[str, Any]: ...

    def health_check(self, token: str, *, connect_timeout: int, read_timeout: int) -> bool: ...


class HTTPTransport:
    """Minimal Tushare HTTP transport; secrets are never retained or logged."""

    def _post(
        self, body: Mapping[str, Any], connect_timeout: int, read_timeout: int
    ) -> Mapping[str, Any]:
        encoded = json.dumps(body).encode("utf-8")
        connection = http.client.HTTPSConnection("api.tushare.pro", timeout=connect_timeout)
        try:
            connection.connect()
            if connection.sock is not None:
                connection.sock.settimeout(read_timeout)
            connection.request("POST", "/", body=encoded, headers={"Content-Type": "application/json"})
            response = connection.getresponse()
            if response.status >= 400:
                raise OSError(f"Tushare HTTP status {response.status}")
            return json.loads(response.read().decode("utf-8"))
        finally:
            connection.close()

    def _query(
        self, token: str, api_name: str, params: Mapping[str, Any], fields: str,
        connect_timeout: int, read_timeout: int,
    ) -> Mapping[str, Any]:
        result = self._post({
            "api_name": api_name, "token": token, "params": dict(params), "fields": fields
        }, connect_timeout, read_timeout)
        if result.get("code") != 0:
            raise PermissionError("Tushare rejected the request")
        return result

    @staticmethod
    def _rows(result: Mapping[str, Any]) -> list[dict[str, Any]]:
        data = result.get("data") or {}
        fields = data.get("fields") or []
        return [dict(zip(fields, item)) for item in data.get("items") or []]

    def fetch_daily(
        self, token: str, stock_id: str, date_from: str, date_to: str, *, connect_timeout: int, read_timeout: int
    ) -> Mapping[str, Any]:
        params = {"ts_code": stock_id, "start_date": date_from, "end_date": date_to}
        daily = self._query(token, "daily", params, "", connect_timeout, read_timeout)
        factors = self._query(
            token, "adj_factor", params, "ts_code,trade_date,adj_factor", connect_timeout, read_timeout
        )
        calendar = self._query(
            token, "trade_cal", {"start_date": date_from, "end_date": date_to, "is_open": "1"},
            "cal_date", connect_timeout, read_timeout,
        )
        factor_by_date = {row["trade_date"]: row.get("adj_factor") for row in self._rows(factors)}
        bars = self._rows(daily)
        for bar in bars:
            bar["adj_factor"] = factor_by_date.get(bar.get("trade_date"))
        return {
            "bars": bars,
            "expected_trade_dates": [row["cal_date"] for row in self._rows(calendar)],
            "raw_reference": f"tushare://daily/{stock_id}/{date_from}/{date_to}",
        }

    def health_check(self, token: str, *, connect_timeout: int, read_timeout: int) -> bool:
        # The user endpoint checks transport and authorization without requesting market data.
        self._query(token, "user", {}, "", connect_timeout, read_timeout)
        return True


def _timestamp() -> str:
    return datetime.now(timezone.utc).isoformat()


def _parse_date(value: Any) -> date:
    if not isinstance(value, str):
        raise ValueError("date must be a string")
    for fmt in ("%Y%m%d", "%Y-%m-%d"):
        try:
            return datetime.strptime(value, fmt).date()
        except ValueError:
            pass
    raise ValueError("date must use YYYYMMDD or YYYY-MM-DD")


class TushareMCPProvider(Provider):
    """Historical-daily-only adapter implementing the frozen provider contract."""

    def __init__(self, transport: TushareTransport | None = None, *, environ: Mapping[str, str] | None = None) -> None:
        self._transport = transport or HTTPTransport()
        self._environ = os.environ if environ is None else environ

    def describe(self) -> Mapping[str, Any]:
        return {
            "provider_name": PROVIDER_NAME,
            "data_kinds": (DATA_KIND,),
            "connect_timeout_seconds": CONNECT_TIMEOUT_SECONDS,
            "read_timeout_seconds": READ_TIMEOUT_SECONDS,
            "read_only": True,
        }

    def _envelope(
        self, *, data_kind: str, status: ProviderStatus, requested_at: str, raw_reference: str | None = None,
        payload: Any = None, error: ContractError | None = None
    ) -> ProviderEnvelope:
        return ProviderEnvelope(
            provider_name=PROVIDER_NAME, data_kind=data_kind, status=status,
            request_timestamp=requested_at, provider_timestamp=None, raw_reference=raw_reference,
            payload=payload, error=error,
        )

    def fetch(self, request: Mapping[str, Any]) -> ProviderEnvelope:
        requested_at = _timestamp()
        data_kind = request.get("data_kind")
        if data_kind != DATA_KIND:
            return self._envelope(
                data_kind=str(data_kind), status=ProviderStatus.VALIDATION_FAILED, requested_at=requested_at,
                error=ContractError(
                    ContractErrorCode.INVALID_DATA_KIND_SCOPE,
                    f"{PROVIDER_NAME} only supports data_kind {DATA_KIND!r}",
                ),
            )

        token = self._environ.get("TUSHARE_TOKEN")
        if not token:
            return self._envelope(
                data_kind=DATA_KIND, status=ProviderStatus.AUTH_FAILED, requested_at=requested_at,
                error=ContractError(ContractErrorCode.MISSING_REQUIRED_FIELD, "TUSHARE_TOKEN is not configured"),
            )

        stock_id, date_from, date_to = (request.get(key) for key in ("stock_id", "date_from", "date_to"))
        try:
            if not isinstance(stock_id, str) or not stock_id.strip():
                raise ValueError("stock_id is required")
            start, end = _parse_date(date_from), _parse_date(date_to)
            if start > end:
                raise ValueError("date_from must not be after date_to")
        except ValueError as exc:
            return self._envelope(
                data_kind=DATA_KIND, status=ProviderStatus.VALIDATION_FAILED, requested_at=requested_at,
                error=ContractError(ContractErrorCode.MISSING_REQUIRED_FIELD, str(exc)),
            )

        normalized_from, normalized_to = start.strftime("%Y%m%d"), end.strftime("%Y%m%d")
        try:
            raw = self._transport.fetch_daily(
                token, stock_id, normalized_from, normalized_to,
                connect_timeout=CONNECT_TIMEOUT_SECONDS, read_timeout=READ_TIMEOUT_SECONDS,
            )
        except PermissionError:
            return self._envelope(
                data_kind=DATA_KIND, status=ProviderStatus.AUTH_FAILED, requested_at=requested_at,
                error=ContractError(ContractErrorCode.INVALID_STATUS, "Tushare authorization failed"),
            )
        except (TimeoutError, socket.timeout):
            return self._envelope(data_kind=DATA_KIND, status=ProviderStatus.TIMEOUT, requested_at=requested_at)
        except (OSError, ValueError, TypeError):
            return self._envelope(data_kind=DATA_KIND, status=ProviderStatus.UNAVAILABLE, requested_at=requested_at)

        raw_reference = raw.get("raw_reference") or f"tushare://response/{uuid4()}"
        bars = raw.get("bars")
        report = self._validate_bars(bars, raw.get("expected_trade_dates"))
        payload = {"bars": bars, "validation": report, "adjustment_basis": "adj_factor_per_trade_date"}
        if report["errors"]:
            return self._envelope(
                data_kind=DATA_KIND, status=ProviderStatus.VALIDATION_FAILED, requested_at=requested_at,
                raw_reference=str(raw_reference), payload=payload,
                error=ContractError(ContractErrorCode.MISSING_REQUIRED_FIELD, "; ".join(report["errors"])),
            )
        return self._envelope(
            data_kind=DATA_KIND, status=ProviderStatus.AVAILABLE, requested_at=requested_at,
            raw_reference=str(raw_reference), payload=payload,
        )

    @staticmethod
    def _validate_bars(bars: Any, expected_trade_dates: Any) -> dict[str, list[Any]]:
        errors: list[str] = []
        anomalies: list[dict[str, Any]] = []
        if not isinstance(bars, Sequence) or isinstance(bars, (str, bytes)):
            return {"errors": ["bars must be a sequence"], "missing_trade_dates": [], "anomalies": []}
        if not bars:
            errors.append("bars must not be empty")
        seen_dates: set[str] = set()
        for index, bar in enumerate(bars):
            if not isinstance(bar, Mapping):
                errors.append(f"bar[{index}] must be a mapping")
                continue
            missing = sorted(field for field in REQUIRED_BAR_FIELDS if bar.get(field) is None)
            if missing:
                errors.append(f"bar[{index}] missing fields: {', '.join(missing)}")
            trade_date = bar.get("trade_date")
            try:
                parsed = _parse_date(trade_date)
                canonical = parsed.strftime("%Y%m%d")
                if canonical in seen_dates:
                    errors.append(f"duplicate trade_date: {canonical}")
                seen_dates.add(canonical)
            except ValueError:
                errors.append(f"bar[{index}] has invalid trade_date")
            factor = bar.get("adj_factor")
            if factor is None:
                errors.append(f"bar[{index}] missing adj_factor")
            try:
                pct_chg, volume = float(bar.get("pct_chg")), float(bar.get("vol"))
                if abs(pct_chg) > 20:
                    anomalies.append({"index": index, "field": "pct_chg", "value": pct_chg, "threshold": 20})
                if volume < 0:
                    anomalies.append({"index": index, "field": "vol", "value": volume, "threshold": 0})
            except (TypeError, ValueError):
                errors.append(f"bar[{index}] has non-numeric pct_chg or vol")
        expected = set(expected_trade_dates or [])
        missing_dates = sorted(expected.difference(seen_dates))
        if missing_dates:
            errors.append(f"missing trade dates: {', '.join(missing_dates)}")
        return {"errors": errors, "missing_trade_dates": missing_dates, "anomalies": anomalies}

    def health_check(self) -> ProviderStatus:
        token = self._environ.get("TUSHARE_TOKEN")
        if not token:
            return ProviderStatus.AUTH_FAILED
        try:
            return ProviderStatus.AVAILABLE if self._transport.health_check(
                token, connect_timeout=CONNECT_TIMEOUT_SECONDS, read_timeout=READ_TIMEOUT_SECONDS
            ) else ProviderStatus.UNAVAILABLE
        except PermissionError:
            return ProviderStatus.AUTH_FAILED
        except (TimeoutError, socket.timeout):
            return ProviderStatus.TIMEOUT
        except (OSError, ValueError, TypeError):
            return ProviderStatus.UNAVAILABLE
