"""Pure validation functions used before canonical data is emitted."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Mapping

from .contracts import DataQualityStatus, NumericField, UnitStatus, ValidationResult

ALLOWED_PROVIDERS = frozenset({"TUSHARE_MCP", "TDX_OFFICIAL_LOCAL_HTTP", "WORKBUDDY_TDX_MCP"})
REALTIME_PROVIDER = "TDX_OFFICIAL_LOCAL_HTTP"
ALLOWED_ADJUST_TYPES = frozenset({"UNADJUSTED", "QFQ", "HFQ"})


def parse_trade_date(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    for fmt in ("%Y-%m-%d", "%Y%m%d"):
        try:
            return datetime.strptime(value, fmt).strftime("%Y-%m-%d")
        except ValueError:
            continue
    return None


def validate_daily(
    values: Mapping[str, Any], fields: Mapping[str, NumericField], *, warning_threshold: float
) -> ValidationResult:
    failures: list[str] = []
    warnings: list[str] = []
    if not values.get("stock_id"):
        failures.append("stock_id is required")
    if parse_trade_date(values.get("trade_date")) is None:
        failures.append("trade_date must be a valid YYYY-MM-DD calendar date")
    if values.get("provider") not in ALLOWED_PROVIDERS:
        failures.append("provider is not one of the three frozen sources")
    for name in ("open", "high", "low", "close", "volume"):
        item = fields[name]
        if item.unit_status is UnitStatus.UNVERIFIED:
            failures.append(f"{name} unit is unverified")
        elif item.normalized_value is None:
            failures.append(f"{name} is required and numeric")
        elif item.normalized_value < 0:
            failures.append(f"{name} must not be negative")
    amount = fields["amount"]
    if amount.raw_value is None:
        warnings.append("amount is missing; it was not derived")
    elif amount.unit_status is UnitStatus.UNVERIFIED:
        failures.append("amount unit is unverified")

    nums = {name: fields[name].normalized_value for name in ("open", "high", "low", "close")}
    if all(value is not None for value in nums.values()):
        if nums["high"] < nums["low"]:
            failures.append("high must be greater than or equal to low")
        if nums["high"] < nums["open"] or nums["high"] < nums["close"]:
            failures.append("high must be greater than or equal to open and close")
        if nums["low"] > nums["open"] or nums["low"] > nums["close"]:
            failures.append("low must be less than or equal to open and close")

    pre_close = fields.get("pre_close")
    close = fields["close"].normalized_value
    if pre_close and pre_close.normalized_value not in (None, 0) and close is not None:
        change = close / pre_close.normalized_value - 1
        if abs(change) > warning_threshold:
            warnings.append(f"price change {change:.2%} exceeds warning threshold {warning_threshold:.2%}")

    adjust_type = values.get("adjust_type")
    adjust_status = values.get("adjust_status")
    if adjust_type is not None and adjust_type not in ALLOWED_ADJUST_TYPES:
        failures.append("adjust_type is invalid")
    if adjust_type is None and adjust_status != "UNVERIFIED":
        failures.append("unknown adjust_type must carry adjust_status=UNVERIFIED")

    if failures:
        return ValidationResult(False, DataQualityStatus.FAILED, tuple(failures), tuple(warnings))
    if adjust_status == "UNVERIFIED" or any(f.unit_status is UnitStatus.UNVERIFIED for f in fields.values()):
        status = DataQualityStatus.UNVERIFIED
    elif warnings:
        status = DataQualityStatus.WARNING
    else:
        status = DataQualityStatus.PASS
    return ValidationResult(True, status, warnings=tuple(warnings))


def validate_quote(values: Mapping[str, Any], fields: Mapping[str, NumericField]) -> ValidationResult:
    failures: list[str] = []
    warnings: list[str] = []
    if not values.get("stock_id"):
        failures.append("stock_id is required")
    if values.get("provider") != REALTIME_PROVIDER:
        failures.append("market quote provider must be TDX_OFFICIAL_LOCAL_HTTP")
    price = fields["price"]
    if price.normalized_value is None or price.unit_status is UnitStatus.UNVERIFIED:
        failures.append("price is required in a verified unit")
    for name, item in fields.items():
        if item.normalized_value is not None and item.normalized_value < 0:
            failures.append(f"{name} must not be negative")
        if item.raw_value is not None and item.unit_status is UnitStatus.UNVERIFIED:
            failures.append(f"{name} unit is unverified")
    if values.get("quote_timestamp") is None:
        warnings.append("quote_timestamp is missing; freshness is UNKNOWN")
    for reason in values.get("freshness_check", {}).get("reason_codes", ()):
        warnings.append("quote freshness restriction: " + reason)
    if failures:
        return ValidationResult(False, DataQualityStatus.FAILED, tuple(failures), tuple(warnings))
    return ValidationResult(
        True, DataQualityStatus.WARNING if warnings else DataQualityStatus.PASS, warnings=tuple(warnings)
    )
