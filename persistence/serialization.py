"""Lossless canonical JSON with no repr, identity or timestamp coercion."""
from collections.abc import Mapping
from enum import Enum
import hashlib
import json
import math

from .contracts import PersistenceError


def json_value(value):
    if isinstance(value, Enum):
        return json_value(value.value)
    if isinstance(value, Mapping):
        if any(type(k) is not str for k in value):
            raise PersistenceError("JSON_KEYS_MUST_BE_TEXT")
        return {k: json_value(v) for k, v in value.items()}
    if type(value) in (list, tuple):
        return [json_value(v) for v in value]
    if value is None or type(value) in (str, bool, int):
        return value
    if type(value) is float and math.isfinite(value):
        return value
    raise PersistenceError("FINITE_JSON_VALUES_REQUIRED")


def dumps(value):
    return json.dumps(json_value(value), ensure_ascii=False, sort_keys=True,
                      separators=(",", ":"), allow_nan=False)


def _object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise PersistenceError("DUPLICATE_JSON_KEY")
        result[key] = value
    return result


def _nonfinite(value):
    raise PersistenceError("FINITE_JSON_VALUES_REQUIRED")


def loads(value):
    if type(value) is not str:
        raise PersistenceError("JSON_TEXT_REQUIRED")
    try:
        return json_value(json.loads(value, object_pairs_hook=_object, parse_constant=_nonfinite))
    except (ValueError, TypeError, RecursionError) as exc:
        raise PersistenceError("INVALID_JSON") from exc


def digest(value):
    return hashlib.sha256(dumps(value).encode("utf-8")).hexdigest()
