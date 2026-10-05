"""Versioned, JSON-only provenance; never apply new rules to canonical input."""
from copy import deepcopy

RULE_VERSION = "CANONICAL_SEMANTICS_V1.0"
SEMANTIC_FIELDS = frozenset({"period", "field_semantics", "conversion_source", "rule_version", "conversion_chain", "adjustment_basis"})
DAILY_SEMANTICS = {"open": "OPENING_PRICE", "high": "PERIOD_HIGH_PRICE", "low": "PERIOD_LOW_PRICE",
                   "close": "CLOSING_PRICE", "volume": "TRADED_SHARES", "amount": "TURNOVER_CNY"}
QUOTE_SEMANTICS = {"price": "LAST_TRADED_PRICE", "bid": "BEST_BID_PRICE", "ask": "BEST_ASK_PRICE",
                   "volume": "TRADED_SHARES", "amount": "TURNOVER_CNY"}


def metadata(source, provider, kind, numeric=None):
    """Retain declarations, including unsupported semantics, without guessing conversions."""
    if "rule_version" in source and numeric is not None:
        raise ValueError("versioned canonical input cannot be normalized under new rules")
    daily = kind == "daily_kline"
    semantics = DAILY_SEMANTICS if daily else QUOTE_SEMANTICS
    result = {"period": deepcopy(source.get("period", "DAILY" if daily else "SNAPSHOT")),
              "field_semantics": deepcopy(source.get("field_semantics", semantics)),
              "conversion_source": provider, "rule_version": RULE_VERSION,
              "adjustment_basis": source.get("adjust_type") if daily else source.get("adjust_type", "UNADJUSTED")}
    if result["adjustment_basis"] not in {"UNADJUSTED", "QFQ", "HFQ"}:
        result["adjustment_basis"] = "UNKNOWN"
    chain = []
    for field, item in (numeric or {}).items():
        raw_field = "ts_code" if field == "stock_id" else "vol" if field == "volume" and "volume" not in source else field
        chain.append({"field": field, "source": f"{provider}.row.{raw_field}",
                      "rule": item.conversion_rule.value if item.conversion_rule else "UNVERIFIED",
                      "rule_version": RULE_VERSION, "input_unit": item.raw_unit, "target_unit": item.normalized_unit})
    if numeric is None:
        chain.extend({"field": field, "source": f"{provider}.row.{field}",
                      "rule": "IDENTITY", "rule_version": RULE_VERSION} for field in semantics)
    chain.extend({"field": field, "source": f"{provider}.row.{raw_field}",
                  "rule": rule, "rule_version": RULE_VERSION}
                 for field, raw_field, rule in (("stock_id", "stock_id" if "stock_id" in source else "ts_code", "FIELD_MAPPING"),
                     ("period", "period", "DECLARED_PERIOD_OR_DATA_KIND"),
                     ("adjustment_basis", "adjust_type" if daily else "price", "PROVIDER_BASIS_NO_CONVERSION")))
    for step in chain:
        if step["field"] == "period" and "period" not in source:
            step["source"] = f"{provider}.envelope.data_kind"
    chain.append({"field": "field_semantics",
                  "source": f"{provider}.row.field_semantics" if "field_semantics" in source else f"{provider}.contract.{kind}",
                  "rule": "FIELD_MAPPING", "rule_version": RULE_VERSION})
    if daily:
        chain.append({"field": "trade_date", "source": f"{provider}.row.trade_date",
                      "rule": "TRADE_DATE_ISO", "rule_version": RULE_VERSION})
    result["conversion_chain"] = tuple(chain)
    return result


def validate_metadata(row, kind):
    if not SEMANTIC_FIELDS <= row.keys():
        raise ValueError("canonical semantics metadata missing; re-normalize raw into new Evidence")
    if row["rule_version"] != RULE_VERSION:
        raise ValueError("unsupported rule version; canonical data must not be reinterpreted")
    if row["conversion_source"] != row["provider"]:
        raise ValueError("conversion source differs from canonical provider")
    expected = DAILY_SEMANTICS if kind == "daily_kline" else QUOTE_SEMANTICS
    if row["period"] != ("DAILY" if kind == "daily_kline" else "SNAPSHOT") or row["field_semantics"] != expected:
        raise ValueError("unsupported period or field semantics; conversion requires an audited rule")
    basis = row["adjustment_basis"]
    expected_basis = (row.get("adjust_type") or "UNKNOWN") if kind == "daily_kline" else "UNADJUSTED"
    if basis != expected_basis:
        raise ValueError("adjustment basis conflicts with canonical adjustment")
    chain = row["conversion_chain"]
    if not isinstance(chain, (list, tuple)) or not chain:
        raise ValueError("conversion chain missing")
    for step in chain:
        if (not isinstance(step, dict) or not isinstance(step.get("field"), str) or step.get("rule_version") != row["rule_version"]
                or not isinstance(step.get("source"), str) or not step["source"].startswith(row["provider"] + ".")
                or step.get("rule") not in {"IDENTITY", "LOT100_TO_SHARE", "TEN_THOUSAND_YUAN_TO_YUAN", "UNVERIFIED", "FIELD_MAPPING", "DECLARED_PERIOD_OR_DATA_KIND", "PROVIDER_BASIS_NO_CONVERSION", "TRADE_DATE_ISO"}):
            raise ValueError("invalid or reinterpreted conversion chain")

    if not (set(expected) | {"stock_id", "period", "adjustment_basis"}) <= {step["field"] for step in chain}:
        raise ValueError("conversion chain does not cover canonical fields")
    _validate_conversion_chain(row, kind, expected)


def _validate_conversion_chain(row, kind, expected):
    """Check the frozen V1 mapping, without editing or replaying any step."""
    daily = kind == "daily_kline"
    numeric = set(expected) | ({"pre_close"} if daily else set())
    declarations = {
        "stock_id": ("FIELD_MAPPING", {"row.stock_id", "row.ts_code"} if daily else {"row.stock_id"}),
        "period": ("DECLARED_PERIOD_OR_DATA_KIND", {"row.period", "payload.period", "envelope.data_kind"}),
        "adjustment_basis": ("PROVIDER_BASIS_NO_CONVERSION",
                             {"row.adjust_type"} if daily else {"row.price", "row.adjust_type", "payload.adjust_type"}),
        "field_semantics": ("FIELD_MAPPING", {"row.field_semantics", "payload.field_semantics", f"contract.{kind}"}),
    }
    if daily:
        declarations["trade_date"] = ("TRADE_DATE_ISO", {"row.trade_date"})
    seen = set()
    for step in row["conversion_chain"]:
        field, rule = step["field"], step["rule"]
        semantic = expected.get(field, "PREVIOUS_CLOSING_PRICE" if field == "pre_close" else field)

        def reject(reason):
            raise ValueError(
                f"conversion chain inconsistent: {field}={semantic}; source={step['source']}; "
                f"rule={rule}; {step.get('input_unit')}->{step.get('target_unit')}; {reason}")

        # V1 emits one direct transformation per field; it has no audited
        # composition rule that could justify duplicate or competing steps.
        if field in seen:
            reject("duplicate or conflicting steps for canonical field")
        seen.add(field)
        source = step["source"][len(row["provider"]) + 1:]
        if field not in numeric:
            if field not in declarations:
                reject("field is not part of the canonical semantics contract")
            required_rule, sources = declarations[field]
            if rule != required_rule or source not in sources:
                reject("field, source and declaration rule conflict")
            if "input_unit" in step or "target_unit" in step:
                reject("non-numeric declaration cannot carry numeric units")
            continue
        sources = {f"row.{field}"} | ({"row.vol"} if field == "volume" else set())
        target = "股" if field == "volume" else "元"
        rules = {"IDENTITY"} | ({"LOT100_TO_SHARE"} if field == "volume" else
                                {"TEN_THOUSAND_YUAN_TO_YUAN"} if field == "amount" else set())
        if source not in sources or rule not in rules:
            reject("field semantics, source and conversion rule conflict")
        has_input, has_target = "input_unit" in step, "target_unit" in step
        # Existing canonical IDENTITY metadata may omit both units. An
        # explicit conversion must carry both of its original declarations.
        if not has_input and not has_target and rule == "IDENTITY":
            continue
        if not has_input or not has_target or step["target_unit"] != target:
            reject("missing or conflicting target unit for field semantics")
        # Missing data is never converted. The normalizer preserves its raw
        # unit declaration even when it emits IDENTITY for a null value.
        if rule == "IDENTITY" and row.get(field) is None:
            continue
        unit = step["input_unit"]
        unit = unit.strip().lower() if isinstance(unit, str) else None
        allowed_units = ({"股", "share", "shares"} if target == "股" else {"元", "yuan", "cny"})
        if rule == "LOT100_TO_SHARE":
            allowed_units = {"手", "lot", "lots"}
        elif rule == "TEN_THOUSAND_YUAN_TO_YUAN":
            allowed_units = {"万元", "万人民币", "10k yuan"}
        if unit not in allowed_units:
            reject("input unit conflicts with conversion rule and field semantics")
