"""Read-only TDX cloud MCP adapter; no desktop runtime or source fallback."""

from __future__ import annotations

from collections import OrderedDict
from copy import deepcopy
from dataclasses import dataclass, fields, is_dataclass
from datetime import date, datetime, timedelta, timezone
from enum import Enum
import hashlib
import json
import math
import os
from pathlib import Path
import re
from typing import Any, Mapping
from urllib.error import HTTPError, URLError
from urllib.request import HTTPRedirectHandler, Request, build_opener
from uuid import uuid4

from evidence import EvidenceBuilder
from .contract import ContractError, ContractErrorCode, ProviderEnvelope, ProviderStatus
from .interface import Provider

LOGICAL_SOURCE = "WORKBUDDY_TDX_MCP"
ACTUAL_PROVIDER_SERVICE = "TDX_OFFICIAL_CLOUD_MCP"
ENDPOINT = "https://txmcp.tdx.com.cn:3001/txmcp"
PROTOCOL_VERSIONS = ("2025-03-26", "2025-06-18", "2024-11-05")
TOOL_KINDS = {
    "wenda_notice_query": "announcement", "wenda_report_query": "research",
    "wenda_news_query": "news", "wenda_macro_query": "news",
    "tdx_api_data": "financial", "tdx_ai_listening": "news",
}
F10_VIEWS = {
    "profit": ("TdxShareCW.ph_agf10_cw_lyb", "00101"),
    "balance": ("TdxShareCW.ph_agf10_cw_zcfzb", None),
    "cashflow": ("TdxShareCW.ph_agf10_cw_xjllb", "00101"),
    "company": ("TdxSharePCCW.tdxf10_gg_gsgk", "0"),
}
MARKET_SETCODES = {"SH": "1", "SZ": "0", "BJ": "2"}
MAX_BODY_BYTES = 1024 * 1024
MAX_CACHE_BYTES = 8 * 1024 * 1024
MAX_CACHE_ENTRIES = 64
CHINA_TZ = timezone(timedelta(hours=8))


@dataclass(frozen=True)
class MCPResponse:
    status: int
    headers: Mapping[str, str]
    body: bytes


class NoRedirect(HTTPRedirectHandler):
    """Never forward the Bearer header to a redirect destination."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class MCPHTTPTransport:
    """Bounded HTTPS POSTs using stdlib urllib and the inherited TLS trust."""

    def __init__(self) -> None:
        self._opener = build_opener(NoRedirect())

    def post(self, body: bytes, *, headers: Mapping[str, str], timeout: float) -> MCPResponse:
        request = Request(ENDPOINT, data=body, headers=dict(headers), method="POST")
        try:
            response = self._opener.open(request, timeout=timeout)
        except HTTPError as exc:
            response = exc
        with response:
            data = response.read(MAX_BODY_BYTES + 1)
            return MCPResponse(response.code, dict(response.headers.items()), data)


class _Failure(Exception):
    def __init__(self, status: ProviderStatus, message: str | None = None):
        self.status = status
        self.message = message


class _SessionExpired(Exception):
    pass


def _json(value: bytes | str) -> Any:
    def reject_constant(_):
        raise ValueError("non-finite JSON number")

    def unique_object(pairs):
        result = {}
        for key, item in pairs:
            if key in result:
                raise ValueError("duplicate JSON key")
            result[key] = item
        return result

    return json.loads(value, parse_constant=reject_constant, object_pairs_hook=unique_object)


def _encode(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
                      allow_nan=False).encode("utf-8")


def _no_data(text: str) -> bool:
    """Recognize explicit empty-result notices, never an incidental substring."""
    return bool(re.fullmatch(
        r"(?:暂无(?:相关)?数据|暂无相关内容|没有(?:相关)?数据|未查询到(?:相关)?数据|"
        r"未找到(?:相关)?数据|无数据|no data(?: available| found)?|no results(?: found)?)"
        r"[。.!！]?", text.strip(), re.IGNORECASE))


def _business_error(value: dict) -> None:
    for flag in ("ok", "isError"):
        if flag in value and type(value[flag]) is not bool:
            raise _Failure(ProviderStatus.VALIDATION_FAILED)
    if "code" in value and type(value["code"]) is not int:
        raise _Failure(ProviderStatus.VALIDATION_FAILED)
    messages = [value.get(name) for name in ("message", "msg", "error")]
    if isinstance(value.get("error"), dict):
        messages.append(value["error"].get("message"))
    no_data = any(isinstance(item, str) and _no_data(item) for item in messages)
    if value.get("ok") is False or value.get("code", 0) != 0:
        if no_data:
            raise _Failure(ProviderStatus.NOT_AVAILABLE, "TDX business result: no data")
        # The frozen status enum has no PROVIDER_ERROR. Keep remote business
        # failures distinct from local parameters and malformed responses.
        raise _Failure(ProviderStatus.UNAVAILABLE, "TDX business error: server rejected or failed the query")
    if value.get("isError") is True and no_data:
        raise _Failure(ProviderStatus.NOT_AVAILABLE, "TDX business result: no data")


def _business_data(value):
    if isinstance(value, dict):
        _business_error(value)
        if value.get("isError") is True:
            raise _Failure(ProviderStatus.UNAVAILABLE, "TDX tool reported a server error")
        if "data" in value and "response" in value and value["data"] != value["response"]:
            raise _Failure(ProviderStatus.VALIDATION_FAILED)
        if "data" not in value and "response" not in value:
            raise _Failure(ProviderStatus.VALIDATION_FAILED)
        value = value["data"] if "data" in value else value["response"]
    if value is None:
        return []
    if not isinstance(value, (list, dict)):
        raise _Failure(ProviderStatus.VALIDATION_FAILED)
    return value


def _plain(value: Any) -> Any:
    """Serialize frozen evidence without mutating it or its MappingProxyType."""
    if is_dataclass(value):
        return {field.name: _plain(getattr(value, field.name)) for field in fields(value)}
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, Mapping):
        return {name: _plain(item) for name, item in value.items()}
    if isinstance(value, (tuple, list)):
        return [_plain(item) for item in value]
    return value


def _date(value: Any) -> date:
    if not isinstance(value, str):
        raise ValueError("date required")
    return datetime.strptime(value, "%Y%m%d" if re.fullmatch(r"\d{8}", value) else "%Y-%m-%d").date()


def _time_range(arguments: Mapping[str, Any]) -> None:
    if "bdate" in arguments or "edate" in arguments:
        if "time_range" in arguments:
            raise ValueError("ambiguous time ranges")
        start, end = _date(arguments.get("bdate")), _date(arguments.get("edate"))
    else:
        span = arguments.get("time_range")
        if not isinstance(span, Mapping) or set(span) != {"start", "end"}:
            raise ValueError("explicit time range required")
        start, end = _date(span["start"]), _date(span["end"])
    if start > end:
        raise ValueError("reversed time range")


def _arguments(tool: str, stock: str, arguments: Mapping[str, Any]) -> dict:
    """Validate and translate before any HTTP, cache lookup, or evidence effect."""
    values = dict(arguments)
    code = stock[:6]
    if tool == "tdx_api_data":
        # Only the view selector is public; every gateway parameter is derived.
        view = values.get("view")
        if set(values) != {"view"} or not isinstance(view, str) or view not in F10_VIEWS:
            raise ValueError("F10 view outside information scope")
        entry, fixed_tag = F10_VIEWS[view]
        values = {"entry": entry, "code": code}
        if fixed_tag is not None:
            values["fixedTag"] = fixed_tag
    elif tool == "tdx_ai_listening":
        if set(values) - {"date", "setcode_code"}:
            raise ValueError("listening requires date, not a time range")
        setcode_code = MARKET_SETCODES[stock[7:]] + "_" + code
        if "setcode_code" in values and values["setcode_code"] != setcode_code:
            raise ValueError("stock identity mismatch")
        day = values.get("date")
        if not isinstance(day, str) or not re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}", day):
            raise ValueError("listening date must be YYYY-MM-DD")
        date.fromisoformat(day)  # Also reject impossible calendar dates.
        values["setcode_code"] = setcode_code
    else:
        # Legacy stock_code may be a canonical id or its six-digit code. symbol
        # is the actual wire field and must equal the derived six-digit code.
        has_identity = "symbol" in values or "stock_code" in values
        if has_identity and not stock:
            raise ValueError("stock id required for stock identity")
        if "symbol" in values and values["symbol"] != code:
            raise ValueError("stock identity mismatch")
        if "stock_code" in values and values["stock_code"] not in (stock, code):
            raise ValueError("stock identity mismatch")
        values.pop("stock_code", None)
        if tool == "wenda_macro_query":
            query = values.get("query")
            if not isinstance(query, str) or len(query.split("|")) != 5:
                raise ValueError("macro query must have five pipe-separated fields")
            if has_identity:
                values["symbol"] = code
        else:
            _time_range(values)
            values["symbol"] = code
    return values


class TdxInformationProvider(Provider):
    """Serial cron adapter with append-only raw evidence and bounded daily cache.

    ``evidence_root`` is an explicit local output directory, never a production
    database. ``environ`` is the test seam; production uses only os.environ.
    """

    def __init__(self, transport=None, *, evidence_root: str | Path,
                 environ: Mapping[str, str] | None = None,
                 clock=lambda: datetime.now(timezone.utc), timeout: float = 30) -> None:
        env = os.environ if environ is None else environ
        key = env.get("TDX_MCP_API_KEY")
        if not isinstance(key, str) or not key.strip() or any(c.isspace() for c in key):
            raise ValueError("TDX_MCP_API_KEY is required and must be a single token")
        if not isinstance(timeout, (int, float)) or isinstance(timeout, bool) or not math.isfinite(timeout) or timeout <= 0:
            raise ValueError("timeout must be positive and finite")
        self._key = key
        self._transport = transport if transport is not None else MCPHTTPTransport()
        self._root = Path(evidence_root)
        self._clock, self._timeout = clock, timeout
        self._session = None
        self._version = PROTOCOL_VERSIONS[0]
        self._tools: set[str] = set()
        self._cache: OrderedDict[str, tuple[ProviderEnvelope, int]] = OrderedDict()
        self._cache_bytes = 0
        self._cache_day = None

    def describe(self) -> Mapping[str, Any]:
        return {"provider_name": LOGICAL_SOURCE, "actual_provider_service": ACTUAL_PROVIDER_SERVICE,
                "endpoint": ENDPOINT, "responsibility": "information_fundamental", "read_only": True,
                "tools": tuple(TOOL_KINDS), "data_kinds": tuple(sorted(set(TOOL_KINDS.values()))),
                "data_source_count": 3, "architecture_change": False,
                "source_access_path_change": True, "fourth_source_forbidden": True,
                "public_official_doc_verified": "PARTIAL"}

    def _safe(self, value: Any) -> None:
        # Reject sensitive inputs before transport, hashing, or evidence storage.
        if isinstance(value, Mapping):
            for name, item in value.items():
                clean = re.sub(r"[^a-z0-9]", "", str(name).lower())
                if any(word in clean for word in ("token", "apikey", "authorization", "password", "cookie", "session", "secret")):
                    raise ValueError("sensitive field forbidden")
                self._safe(item)
        elif isinstance(value, (list, tuple)):
            for item in value:
                self._safe(item)
        elif isinstance(value, str) and self._key in value:
            raise ValueError("sensitive value forbidden")

    @staticmethod
    def _header(headers: Mapping[str, str], name: str) -> str | None:
        return next((v for k, v in headers.items() if k.lower() == name.lower()), None)

    def _raw(self, response: MCPResponse, *, method: str, params: Mapping[str, Any],
             context: Mapping[str, str], at: str, entries: list) -> None:
        data = response.body
        if not isinstance(data, bytes):
            raise _Failure(ProviderStatus.VALIDATION_FAILED)
        # R6 takes precedence over raw retention if a hostile response echoes a key.
        if self._key.encode() in data or _encode(self._key)[1:-1] in data:
            raise _Failure(ProviderStatus.VALIDATION_FAILED)
        # Decode JSON string escapes too, without rejecting malformed raw bodies.
        text = data.decode("utf-8", errors="replace")
        unescaped = re.sub(r"\\u([0-9a-fA-F]{4})", lambda m: chr(int(m[1], 16)), text)
        if self._key in unescaped:
            raise _Failure(ProviderStatus.VALIDATION_FAILED)
        if not data and method == "notifications/initialized" and response.status == 202:
            return
        raw_hash = hashlib.sha256(data).hexdigest()
        path = self._root / context["system_run_id"] / context["collection_task_id"] / context["request_id"] / (uuid4().hex + ".json")
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("xb") as file:
            file.write(data)
        stub = ProviderEnvelope(LOGICAL_SOURCE, context["data_kind"], ProviderStatus.VALIDATION_FAILED,
                                at, None, str(path), None, None)
        tool = params.get("name") if method == "tools/call" else method
        record = EvidenceBuilder().build(
            fetch_result=stub, endpoint_or_tool=f"{ENDPOINT}#{tool}",
            request_params={"method": method, "params": params,
                            "stock_id": context.get("stock_id"), "request_day": at[:10]},
            collection_task_id=context["collection_task_id"], system_run_id=context["system_run_id"],
            raw_response_hash=raw_hash,
        )
        metadata = {"record": _plain(record), "http_status": response.status,
                    "raw_response_complete": len(data) <= MAX_BODY_BYTES,
                    "actual_provider_service": ACTUAL_PROVIDER_SERVICE}
        with path.with_suffix(".evidence.json").open("xb") as file:
            file.write(_encode(metadata))
        entries.append({**metadata, "raw_bytes": data})

    def _rpc(self, method: str, params: Mapping[str, Any], *, context, at, entries,
             notification: bool = False):
        rpc_id = uuid4().hex
        body = {"jsonrpc": "2.0", "method": method, "params": params}
        if not notification:
            body["id"] = rpc_id
        headers = {"Authorization": f"Bearer {self._key}", "Content-Type": "application/json",
                   "Accept": "application/json, text/event-stream"}
        if self._session:
            headers.update({"Mcp-Session-Id": self._session, "MCP-Protocol-Version": self._version})
        try:
            response = self._transport.post(_encode(body), headers=headers, timeout=self._timeout)
        except TimeoutError:
            raise _Failure(ProviderStatus.TIMEOUT) from None
        except URLError as exc:
            status = ProviderStatus.TIMEOUT if isinstance(exc.reason, TimeoutError) else ProviderStatus.NOT_AVAILABLE
            raise _Failure(status) from None
        except PermissionError:
            raise _Failure(ProviderStatus.AUTH_FAILED) from None
        except OSError:
            raise _Failure(ProviderStatus.NOT_AVAILABLE) from None
        self._raw(response, method=method, params=params, context=context, at=at, entries=entries)
        if response.status in (401, 403):
            raise _Failure(ProviderStatus.AUTH_FAILED)
        if response.status == 404 and self._session:
            raise _SessionExpired()
        if response.status in (408, 504):
            raise _Failure(ProviderStatus.TIMEOUT)
        if not 200 <= response.status < 300:
            raise _Failure(ProviderStatus.NOT_AVAILABLE)
        if len(response.body) > MAX_BODY_BYTES:
            raise _Failure(ProviderStatus.VALIDATION_FAILED)
        if notification and response.status == 202 and not response.body:
            return None, response.headers
        raw = response.body
        content_type = self._header(response.headers, "Content-Type") or "application/json"
        if content_type.split(";", 1)[0].strip() == "text/event-stream":
            # Select the response by RPC id; server notifications are not results.
            messages = []
            event_data = []
            for line in raw.decode("utf-8").replace("\r\n", "\n").split("\n") + [""]:
                if line.startswith("data:"):
                    event_data.append(line[5:].lstrip(" "))
                elif not line and event_data:
                    messages.append(_json("\n".join(event_data)))
                    event_data = []
            matches = [m for m in messages if isinstance(m, dict) and m.get("id") == rpc_id]
            if len(matches) != 1:
                raise _Failure(ProviderStatus.VALIDATION_FAILED)
            value = matches[0]
        else:
            value = _json(raw)
        if (not isinstance(value, dict) or value.get("jsonrpc") != "2.0" or value.get("id") != rpc_id
                or ("result" in value) == ("error" in value)):
            raise _Failure(ProviderStatus.VALIDATION_FAILED)
        if "error" in value:
            error = value["error"]
            if (not isinstance(error, dict) or type(error.get("code")) is not int
                    or not isinstance(error.get("message"), str)):
                raise _Failure(ProviderStatus.VALIDATION_FAILED)
            if error["code"] in (-32700, -32600, -32601, -32602):
                raise _Failure(ProviderStatus.VALIDATION_FAILED, "TDX RPC rejected the request or parameters")
            raise _Failure(ProviderStatus.UNAVAILABLE, "TDX RPC reported a server error")
        return value["result"], response.headers

    def _handshake(self, *, context, at, entries) -> None:
        self._session, self._tools = None, set()
        result, headers = self._rpc("initialize", {
            "protocolVersion": PROTOCOL_VERSIONS[0], "capabilities": {},
            "clientInfo": {"name": "tdx-information-provider", "version": "1.0"},
        }, context=context, at=at, entries=entries)
        session = self._header(headers, "Mcp-Session-Id")
        if (not isinstance(result, dict) or result.get("protocolVersion") not in PROTOCOL_VERSIONS
                or not isinstance(session, str) or not session or len(session) > 256
                or any(c.isspace() for c in session) or self._key in session):
            raise _Failure(ProviderStatus.VALIDATION_FAILED)
        self._session, self._version = session, result["protocolVersion"]
        self._rpc("notifications/initialized", {}, context=context, at=at, entries=entries, notification=True)
        params = {}
        for _ in range(8):
            result, _ = self._rpc("tools/list", params, context=context, at=at, entries=entries)
            if not isinstance(result, dict) or not isinstance(result.get("tools"), list):
                raise _Failure(ProviderStatus.VALIDATION_FAILED)
            for tool in result["tools"]:
                if not isinstance(tool, dict) or not isinstance(tool.get("name"), str):
                    raise _Failure(ProviderStatus.VALIDATION_FAILED)
                self._tools.add(tool["name"])
            cursor = result.get("nextCursor")
            if cursor is None:
                return
            if not isinstance(cursor, str) or not cursor:
                raise _Failure(ProviderStatus.VALIDATION_FAILED)
            self._safe(cursor)
            params = {"cursor": cursor}
        raise _Failure(ProviderStatus.VALIDATION_FAILED)

    def _context(self, request, kind):
        context = {"data_kind": kind, "stock_id": request.get("stock_id", "")}
        for name in ("system_run_id", "collection_task_id", "request_id"):
            value = request.get(name, uuid4().hex)
            if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,100}", value):
                raise ValueError("invalid evidence identifier")
            context[name] = value
        return context

    def _execute(self, operation, *, context, at, entries):
        for attempt in range(2):
            try:
                if not self._session:
                    self._handshake(context=context, at=at, entries=entries)
                return operation()
            except _SessionExpired:
                self._session, self._tools = None, set()
                if attempt:
                    raise _Failure(ProviderStatus.TIMEOUT) from None
            except Exception:
                self._session, self._tools = None, set()
                raise

    @staticmethod
    def _data(result, *, tool: str):
        if not isinstance(result, dict):
            raise _Failure(ProviderStatus.VALIDATION_FAILED)
        _business_error(result)
        empty_notice = False
        if "structuredContent" in result:
            data = _business_data(result["structuredContent"])
        elif "data" in result or "response" in result:
            data = _business_data(result)
        else:
            content = result.get("content")
            if not isinstance(content, list):
                raise _Failure(ProviderStatus.VALIDATION_FAILED)
            values = []
            for item in content:
                if not isinstance(item, dict) or not isinstance(item.get("type"), str):
                    raise _Failure(ProviderStatus.VALIDATION_FAILED)
                if item["type"] != "text":
                    continue  # Unknown items remain verbatim in raw Evidence.
                text = item.get("text")
                if not isinstance(text, str):
                    raise _Failure(ProviderStatus.VALIDATION_FAILED)
                try:
                    value = _json(text)
                except json.JSONDecodeError:
                    if text.lstrip().startswith(("{", "[")):
                        raise _Failure(ProviderStatus.VALIDATION_FAILED) from None
                    if tool != "tdx_ai_listening":
                        continue
                    # App metadata is evidence, not a business article.
                    if "_meta" in item:
                        continue
                    lines = [line.strip().strip("*>_ ") for line in text.splitlines()
                             if line.strip() and not line.lstrip().startswith("#")]
                    if lines and all(_no_data(line) for line in lines):
                        value = []
                        empty_notice = True
                    elif text.strip():
                        value = [deepcopy(item)]  # Preserve source markdown as supplied.
                    else:
                        value = []
                if isinstance(value, dict):
                    _business_error(value)
                    if not any(name in value for name in ("data", "response", "isError", "ok", "code")):
                        continue  # Unrecognized JSON/App metadata is raw-only.
                values.append(_business_data(value))
            if content and not values:
                raise _Failure(ProviderStatus.VALIDATION_FAILED)
            nonempty = [value for value in values if value]
            data = nonempty[0] if len(nonempty) == 1 else [
                row for value in nonempty for row in (value if isinstance(value, list) else [value])]
        if result.get("isError") is True:
            if empty_notice and not data:
                raise _Failure(ProviderStatus.NOT_AVAILABLE, "TDX business result: no data")
            raise _Failure(ProviderStatus.UNAVAILABLE, "TDX tool reported a server error")
        return data

    def fetch(self, request: Mapping[str, Any]) -> ProviderEnvelope:
        at = self._clock().astimezone(CHINA_TZ).isoformat()
        entries = []
        kind = request.get("data_kind", "news") if isinstance(request, Mapping) else "news"
        payload = {"data": [], "canonical_data": [], "evidence": entries}

        def envelope(status, message=None):
            reference = entries[-1]["record"]["raw_response_path"] if entries else None
            error = None if status == ProviderStatus.AVAILABLE else ContractError(
                ContractErrorCode.INVALID_DATA_KIND_SCOPE if status == ProviderStatus.VALIDATION_FAILED
                else ContractErrorCode.MISSING_REQUIRED_FIELD,
                message or "TDX information query did not produce verified data")
            return ProviderEnvelope(LOGICAL_SOURCE, kind, status, at, None, reference, payload, error)

        try:
            self._safe(request)
            tool = request["tool"]
            if not isinstance(tool, str) or tool not in TOOL_KINDS or TOOL_KINDS[tool] != kind:
                raise ValueError("out-of-scope tool or data kind")
            stock = request.get("stock_id", "")
            if not isinstance(stock, str) or (stock and not re.fullmatch(r"[0-9]{6}\.(SH|SZ|BJ)", stock)):
                raise ValueError("invalid stock id")
            if not stock and tool != "wenda_macro_query":
                raise ValueError("stock id required")
            arguments = request["arguments"]
            if not isinstance(arguments, Mapping):
                raise ValueError("arguments required")
            arguments = _arguments(tool, stock, arguments)
            encoded = _encode(arguments)
            if len(encoded) > 65536:
                raise ValueError("arguments too large")
            arguments = json.loads(encoded)
            context = self._context(request, kind)
            day = at[:10]
            if self._cache_day != day:
                self._cache.clear()
                self._cache_bytes, self._cache_day = 0, day
            params_hash = hashlib.sha256(encoded).hexdigest()
            cache_key = hashlib.sha256(_encode([stock, tool, params_hash, day])).hexdigest()
            payload.update(stock_id=stock, tool=tool, arguments=arguments,
                           request_day=day, parameters_hash=params_hash, cache_key=cache_key)
            if cache_key in self._cache:
                self._cache.move_to_end(cache_key)
                return deepcopy(self._cache[cache_key][0])

            def call():
                if tool not in self._tools:
                    raise _Failure(ProviderStatus.NOT_AVAILABLE)
                result, _ = self._rpc("tools/call", {"name": tool, "arguments": arguments},
                                      context=context, at=at, entries=entries)
                return self._data(result, tool=tool)

            payload["data"] = self._execute(call, context=context, at=at, entries=entries)
            status = ProviderStatus.AVAILABLE if payload["data"] else ProviderStatus.NOT_AVAILABLE
            result = envelope(status)
            # Cache successful/empty results only; failures may be retried explicitly.
            size = sum(len(item["raw_bytes"]) for item in entries) * 8 + len(encoded) * 8 + 4096
            if size <= MAX_CACHE_BYTES:
                while self._cache and (len(self._cache) >= MAX_CACHE_ENTRIES or self._cache_bytes + size > MAX_CACHE_BYTES):
                    _, (_, removed) = self._cache.popitem(last=False)
                    self._cache_bytes -= removed
                self._cache[cache_key] = (deepcopy(result), size)
                self._cache_bytes += size
            return result
        except _Failure as exc:
            return envelope(exc.status, exc.message)
        except OSError:
            return envelope(ProviderStatus.NOT_AVAILABLE)
        except (AttributeError, KeyError, TypeError, ValueError, UnicodeError, RecursionError):
            return envelope(ProviderStatus.VALIDATION_FAILED)

    def health_check(self) -> ProviderStatus:
        # Control plane only; no tools/call and no business query.
        at = self._clock().astimezone(CHINA_TZ).isoformat()
        context = self._context({}, "news")
        try:
            self._session = None
            self._execute(lambda: None, context=context, at=at, entries=[])
            return ProviderStatus.AVAILABLE
        except _Failure as exc:
            return exc.status
        except OSError:
            return ProviderStatus.NOT_AVAILABLE
        except (AttributeError, KeyError, TypeError, ValueError, UnicodeError, RecursionError):
            return ProviderStatus.VALIDATION_FAILED
