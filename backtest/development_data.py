"""Read only the bounded development candles from the private one-line export.

The source has an ascending ``candles`` array. Read one character at a time so
the first post-boundary row is stopped after its date, before any OHLCV field.
The unrelated top-level holdings and the trailing fields array are never parsed.
"""

from datetime import date
from decimal import Decimal
import json
from pathlib import Path

from .config import STAGE1
from .data import Candle, REQUIRED, dec, verify_daily


# The two Stage 1 announced cash events inside the development window only.
# Keep this local so importing the research loader does not touch later events.
DEV_DIVIDENDS = {
    date(2024, 7, 4): Decimal("0.09"),
    date(2025, 7, 3): Decimal("0.086"),
}


def _through(stream, marker: str) -> None:
    matched = 0
    while True:
        char = stream.read(1)
        if not char:
            raise ValueError("candles array missing")
        matched = matched + 1 if char == marker[matched] else (1 if char == marker[0] else 0)
        if matched == len(marker):
            return


def _next_nonspace(stream) -> str:
    while True:
        char = stream.read(1)
        if not char or not char.isspace():
            return char


def _json_string(stream) -> str:
    if _next_nonspace(stream) != '"':
        raise ValueError("expected JSON string")
    content = ['"']
    escaped = False
    while True:
        char = stream.read(1)
        if not char:
            raise ValueError("truncated JSON string")
        content.append(char)
        if char == '"' and not escaped:
            return json.loads(''.join(content))
        escaped = char == '\\' and not escaped
        if char != '\\':
            escaped = False


def load_development(path: str | Path) -> tuple[Candle, ...]:
    rows = []
    previous: date | None = None
    with Path(path).open(encoding="utf-8") as stream:
        _through(stream, '"candles"')
        if _next_nonspace(stream) != ':' or _next_nonspace(stream) != '[':
            raise ValueError("malformed candles array")
        while True:
            opening = _next_nonspace(stream)
            if opening == ']':
                break
            if opening == ',':
                opening = _next_nonspace(stream)
            if opening != '[':
                raise ValueError("expected candle row")
            symbol = _json_string(stream)
            if _next_nonspace(stream) != ',':
                raise ValueError("malformed candle symbol")
            raw_date = _json_string(stream)
            if len(raw_date) != 8 or not raw_date.isdigit():
                raise ValueError("invalid candle date")
            day = date.fromisoformat(f"{raw_date[:4]}-{raw_date[4:6]}-{raw_date[6:]}")
            if previous is not None and day <= previous:
                raise ValueError("source candle dates must ascend strictly")
            if day > STAGE1.development_end:
                break  # Critically, do not read this row's OHLCV or any later row.
            if day < STAGE1.development_start:
                raise ValueError("unexpected pre-development candle")
            previous = day
            values = []
            separator = _next_nonspace(stream)
            for _ in REQUIRED[2:]:
                if separator != ',':
                    raise ValueError("malformed candle fields")
                token = []
                while True:
                    char = stream.read(1)
                    if char in (',', ']'):
                        break
                    if not char:
                        raise ValueError("truncated candle")
                    token.append(char)
                values.append(dec(json.loads(''.join(token))))
                if char == ']' and len(values) != len(REQUIRED) - 2:
                    raise ValueError("short candle row")
                separator = char
            if separator != ']':
                raise ValueError("long candle row")
            rows.append(Candle(symbol, day, *values))
    if not rows or rows[-1].trade_date != STAGE1.development_end:
        raise ValueError("development window incomplete")
    checked = verify_daily(tuple(rows), DEV_DIVIDENDS)
    if not checked.valid or checked.first_date != STAGE1.development_start:
        raise ValueError("development candle gap or invalid price history")
    return tuple(rows)
