"""Deterministic, fail-closed gates over the PromptEnvelope alone.

No fact retrieval, scoring, model calls, prose rewriting or missing-field repair.
"""

from __future__ import annotations

import hashlib
import re
from datetime import datetime, timezone
from typing import Any, Mapping

from .contracts import PromptEnvelope, SOURCES, stable_json
from .output_contracts import (
    ADVICE_FIELDS, LIMITATION_REASONS, AnalysisOutputError, OutputParser, ValidatedAnalysisDraft,
    digest, output_schema,
)
from .prompt_builder import PROMPT_VERSION, TEMPLATE_SHA256

_FORBIDDEN_TEXT = re.compile(
    r"payload|raw[_ -]?(?:response|data|bytes)|provider[_ -]?(?:payload|envelope)|"
    r"自动(?:买入|卖出|交易|下单)|已(?:下单|委托|成交|执行|采纳)|券商(?:指令|委托)|"
    r"\b(?:CONFIRMED|APPROVED|FINAL|EXECUTED)\b|"
    r"\b(?:place_order|submit_order|broker_order|auto[_ -]?(?:trade|buy|sell))\b",
    re.IGNORECASE,
)


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise AnalysisOutputError(message)


def _schema_check(value: Any, schema: Mapping[str, Any]) -> None:
    """Validate the small closed JSON Schema vocabulary emitted by V1."""
    if "oneOf" in schema:
        matches = 0
        for alternative in schema["oneOf"]:
            try:
                _schema_check(value, alternative)
                matches += 1
            except AnalysisOutputError:
                pass
        _require(matches == 1, "output does not match exactly one schema branch")
        return
    if "const" in schema:
        _require(type(value) is type(schema["const"]) and value == schema["const"], "invalid fixed output value")
    if "enum" in schema:
        _require(value in schema["enum"], "unknown output value")
    expected = schema.get("type")
    if expected is not None:
        kinds = {"object": type(value) is dict, "array": type(value) is list,
                 "string": type(value) is str, "number": type(value) in (int, float),
                 "boolean": type(value) is bool, "null": value is None}
        _require(any(kinds[k] for k in (expected if isinstance(expected, list) else [expected])), "invalid output type")
    if type(value) is dict and expected == "object":
        properties = schema["properties"]
        _require(set(schema["required"]) <= set(value), "missing output field")
        _require(set(value) <= set(properties), "unknown output field")
        for name, item in value.items():
            _schema_check(item, properties[name])
    if type(value) is list and expected == "array":
        _require(len(value) >= schema.get("minItems", 0), "output array too short")
        if schema.get("uniqueItems"):
            _require(len({stable_json(item) for item in value}) == len(value), "duplicate output reference")
        for item in value:
            _schema_check(item, schema["items"])
    if type(value) is str:
        _require(len(value) >= schema.get("minLength", 0), "empty output text")


def _scan(value: Any) -> None:
    if isinstance(value, dict):
        for item in value.values():
            _scan(item)
    elif isinstance(value, list):
        for item in value:
            _scan(item)
    elif isinstance(value, str):
        _require(_FORBIDDEN_TEXT.search(value) is None, "forbidden output text or state")


def prompt_inputs(envelope: PromptEnvelope) -> dict[str, Any]:
    """Check envelope integrity and decode its already canonical input JSON."""
    _require(type(envelope) is PromptEnvelope, "PromptEnvelope required")
    rendered = envelope.to_dict()
    for key in ("input_hash", "NETWORK_CALL_COUNT", "MODEL_CALL_COUNT", "DATABASE_WRITE"):
        rendered.pop(key)
    _require(digest(rendered) == envelope.input_hash, "prompt input hash mismatch")
    _require(envelope.prompt_version == PROMPT_VERSION and envelope.template_hash == TEMPLATE_SHA256,
             "unknown frozen prompt version or template hash")
    _require(envelope.NETWORK_CALL_COUNT == envelope.MODEL_CALL_COUNT == envelope.DATABASE_WRITE == 0,
             "unexpected prompt side effect counters")
    try:
        metadata, encoded, _ = envelope.user_prompt.split("\n", 2)
        parser = OutputParser()
        meta, inputs = parser.parse(metadata), parser.parse(encoded)
    except (ValueError, AnalysisOutputError):
        raise AnalysisOutputError("invalid prompt input encoding") from None
    _require(meta == {"prompt_version": envelope.prompt_version, "position_mode": envelope.position_mode,
                      "analysis_mode": envelope.analysis_mode}, "prompt mode metadata mismatch")
    _require(inputs.get("stock_id") == envelope.stock_id, "prompt stock mismatch")
    ids = tuple(item["evidence_id"] for item in inputs["evidence_index"])
    _require(ids == envelope.evidence_ids and len(set(ids)) == len(ids), "prompt evidence index mismatch")
    _require(all(item["source"] in SOURCES and item["validation_result"]["status"] == "PASS"
                 for item in inputs["evidence_index"]), "invalid prompt evidence")
    return inputs


class _Context:
    def __init__(self, envelope: PromptEnvelope):
        self.envelope = envelope
        self.inputs = prompt_inputs(envelope)
        self.ids = set(envelope.evidence_ids)
        self.evidence = {item["evidence_id"]: item for item in self.inputs["evidence_index"]}
        market = self.inputs["canonical_market_data"]
        self.blocks = {
            "canonical_market_data.stock_identity": market["stock_identity"],
            "canonical_market_data.daily_kline": market["daily_kline"],
            "canonical_market_data.market_quotes": market["market_quotes"],
            "financial_data": self.inputs["financial_data"],
            **{f"intelligence_data.{key}": value for key, value in self.inputs["intelligence_data"].items()},
        }
        retained_ids = {identifier for block in self.blocks.values()
                        for entry in block["records"] for identifier in entry["evidence_ids"]}
        self.ids.intersection_update(retained_ids)
        self.market_ids = {identifier for key in ("canonical_market_data.daily_kline", "canonical_market_data.market_quotes")
                           for entry in self.blocks[key]["records"] for identifier in entry["evidence_ids"]}

    def resolve(self, path: str) -> Any:
        _require(isinstance(path, str) and bool(path), "input path required")
        current = self.inputs
        try:
            for part in path.split("."):
                if type(current) is list:
                    _require(part.isdigit() and str(int(part)) == part, "invalid record index")
                    current = current[int(part)]
                else:
                    current = current[part]
        except (KeyError, IndexError, TypeError):
            raise AnalysisOutputError("output references an absent input path") from None
        return current

    def status(self, path: str) -> str:
        value = self.resolve(path)
        if value is None:
            return "MISSING"
        if path == "data_quality_report":
            return value["data_quality_status"]
        if path in self.blocks:
            return value["status"]
        if path.startswith("data_quality_report.source_status."):
            return value
        raise AnalysisOutputError("limitation must identify an actual status or null input")

    def review_targets(self) -> set[str]:
        targets = {path for path, block in self.blocks.items() if block["status"] != "PASS"}
        quality = self.inputs["data_quality_report"]
        if quality["data_quality_status"] != "PASS":
            targets.add("data_quality_report")
        targets.update(f"data_quality_report.source_status.{source}"
                       for source, status in quality["source_status"].items() if status in {"WARNING", "UNVERIFIED"})
        return targets

    def limitation_reason(self, path: str) -> str:
        if path in self.blocks and self.blocks[path]["reason"]:
            return self.blocks[path]["reason"]
        return LIMITATION_REASONS[self.status(path)]

    def fact(self, fact: dict, field_ids: set[str], input_refs: list[str]) -> None:
        path = fact["input_path"]
        # Only canonical record leaves can be presented as facts, never index
        # metadata, quality reports, reasons, unavailable values or invented data.
        matched = re.fullmatch(r"(.+)\.records\.(0|[1-9][0-9]*)\.data\.([a-z_]+)", path)
        _require(matched is not None, "facts must address a canonical record scalar")
        block_path, number, _ = matched.groups()
        _require(block_path in self.blocks or block_path == "holding_position", "unknown fact block")
        actual = self.resolve(path)
        _require(actual is not None and type(actual) in (str, int, float, bool), "missing value cannot be a fact")
        _require(type(actual) is type(fact["value"]) and actual == fact["value"], "fact is absent or changed from prompt")
        refs = set(fact["evidence_ids"])
        _require(refs <= field_ids, "fact evidence outside its field")
        entry = self.resolve(f"{block_path}.records.{number}")
        if block_path == "holding_position":
            _require("holding_position" in input_refs and not refs and entry["input_source"] == "portfolio_positions",
                     "holding facts require portfolio input, never Provider Evidence")
        else:
            _require(bool(refs) and refs <= set(entry["evidence_ids"]), "fact evidence does not support the referenced record")

    def time_range(self, identifiers: set[str], input_refs: list[str]) -> dict:
        times = set()
        for block in self.blocks.values():
            for entry in block["records"]:
                if identifiers.intersection(entry["evidence_ids"]):
                    for key in ("trade_date", "quote_timestamp", "report_date", "publish_time", "publish_date"):
                        value = entry["data"].get(key)
                        if value is not None:
                            times.add(value)
        if "holding_position" in input_refs:
            holding = self.inputs["holding_position"]
            _require(holding is not None, "holding reference is absent from prompt")
            # The historical gate preserves the holding block but excludes a
            # snapshot after cutoff. Such a reference is an output violation,
            # handled by the runner's existing OUTPUT_INVALID boundary.
            _require(bool(holding["records"]), "holding reference has no retained snapshot")
            times.add(holding["records"][0]["data"]["snapshot_time"])
        if not times:
            return {"start": None, "end": None}
        def clock(value: str) -> datetime:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
            return parsed.replace(tzinfo=timezone.utc) if parsed.tzinfo is None else parsed.astimezone(timezone.utc)
        ordered = sorted(times, key=lambda value: (clock(value), value))
        return {"start": ordered[0], "end": ordered[-1]}

    def complete(self) -> bool:
        quality = self.inputs["data_quality_report"]
        # Use only the existing gate's completeness and explicit hard statuses.
        # No inference of 'sufficient' support, thresholds or scoring is allowed.
        return (quality["complete_analysis_input"] is True and quality["package_status"] == "COMPLETE"
                and not quality["missing_fields"] and not quality["stale_fields"]
                and not quality["validation_warnings"] and quality["data_quality_status"] == "PASS"
                and all(block["status"] == "PASS" for block in self.blocks.values())
                and all(status == "PASS" for status in quality["source_status"].values()))


class SevenFieldValidator:
    def validate(self, raw_response: str, envelope: PromptEnvelope) -> ValidatedAnalysisDraft:
        parsed = OutputParser().parse(raw_response)
        _schema_check(parsed, output_schema())
        _scan(parsed)
        context = _Context(envelope)
        output = parsed["analysis"]
        used = set()
        review_targets = context.review_targets()
        confidence = output["confidence"]["content"]["assessment"]
        for name, field in output.items():
            identifiers = set(field["evidence_ids"])
            _require(identifiers <= context.ids,
                     "unknown or out-of-prompt evidence_id (no retained record support): "
                     + ", ".join(sorted(identifiers - context.ids)))
            used.update(identifiers)
            content = field["content"]
            refs = field.get("input_refs", [])
            for fact in content["facts"]:
                context.fact(fact, identifiers, refs)
            limitations = content["limitations"]
            _require(len({item["input_path"] for item in limitations}) == len(limitations), "duplicate limitation")
            for item in limitations:
                _require(context.status(item["input_path"]) == item["status"], "limitation contradicts prompt availability")
                _require(context.limitation_reason(item["input_path"]) == item["reason"], "limitation reason contradicts input")
                review_targets.add(item["input_path"])
            if name == "reasoning":
                self._reasoning(content, identifiers)
            _require(field["time_range"] == context.time_range(identifiers, refs), "time range does not match cited input clocks")
            quality = context.inputs["data_quality_report"]
            statuses = {quality["data_quality_status"]}
            statuses.update(item["status"] for item in limitations)
            statuses.update(quality["source_status"][context.evidence[i]["source"]] for i in identifiers)
            statuses.update(block["status"] for block in context.blocks.values()
                            if any(identifiers.intersection(entry["evidence_ids"]) for entry in block["records"]))
            status = field["data_quality_status"]
            _require(status != "PASS" or (statuses == {"PASS"} and bool(identifiers)), "quality upgrade to PASS or unevidenced PASS")
            _require(status != "WARNING" or "UNVERIFIED" not in statuses, "UNVERIFIED cannot be downgraded to WARNING")
            _require(status not in {"MISSING", "NOT_AVAILABLE"} or not identifiers or status in statuses,
                     "invented missing/unavailable status")
            if not identifiers:
                _require(status in {"MISSING", "NOT_AVAILABLE"}, "unevidenced field must declare unavailable data")
            if name == "position_status":
                if envelope.position_mode == "WATCH_ONLY":
                    _require(content["statement"] == "无持仓（观察模式）" and not refs,
                             "WATCH_ONLY must retain no-position semantics")
                    _require(all(not f["input_path"].startswith("holding_position.") for f in content["facts"]),
                             "WATCH_ONLY cannot contain holding facts")
                else:
                    _require(content["statement"] == "持仓事实来自 portfolio_positions，行情判断引用 Evidence"
                             and refs == ["holding_position"] and bool(identifiers & context.market_ids),
                             "HOLDING_ANALYSIS requires holding input and actual market Evidence")
            if name == "market_view":
                assessment = content["assessment"]
                _require(assessment == "UNCLEAR" or bool(identifiers & context.market_ids), "trend requires actual market Evidence")
                _require(content["statement"] != "历史行情分析" or bool(identifiers & context.market_ids), "historical view requires market Evidence")
            if name == "risk_level":
                _require(content["assessment"] == "UNKNOWN" or bool(content["facts"]), "risk assessment requires supported facts")
            if name in ADVICE_FIELDS:
                self._advice(field, confidence, context)
        _require(confidence != "HIGH" or context.complete(), "HIGH confidence requires complete verified inputs")
        _require(output["confidence"]["content"]["statement"] != "依据完整已验证证据" or context.complete(),
                 "cannot claim incomplete/unverified inputs are complete")
        self._review(parsed["human_confirmation_items"], review_targets, context)
        draft = ValidatedAnalysisDraft(
            output=output, human_confirmation_items=tuple(parsed["human_confirmation_items"]),
            limitation_context={
                "aggregation_rule": "FIELD_STATUS_MAY_SUMMARIZE_AVAILABILITY; INPUT_LIMITATIONS_RETAINED_SEPARATELY",
                "data_quality_report": context.inputs["data_quality_report"],
                "limitations": [{"input_path": path, "status": context.status(path),
                                 "reason": context.limitation_reason(path)}
                                for path in sorted(review_targets)],
            },
            input_hash=envelope.input_hash, template_hash=envelope.template_hash,
            model_output_hash=hashlib.sha256(raw_response.encode("utf-8")).hexdigest(),
            validated_output_hash=digest(output), input_evidence_ids=envelope.evidence_ids,
            used_evidence_ids=tuple(sorted(used)),
        )
        object.__setattr__(draft, "_gate_digest", digest(draft.to_dict()))
        return draft

    def _reasoning(self, content: dict, field_ids: set[str]) -> None:
        for step in content["steps"]:
            identifiers = set(step["evidence_ids"])
            _require(identifiers <= field_ids, "step evidence outside its field or prompt")
            # Optional fact_refs are exact canonical input paths in reasoning.facts.
            # Without them, select only facts supported by this step's Evidence.
            facts = [fact for fact in content["facts"]
                     if identifiers.intersection(fact["evidence_ids"])]
            if "fact_refs" in step:
                paths = set(step["fact_refs"])
                facts = [fact for fact in facts if fact["input_path"] in paths]
                _require(paths == {fact["input_path"] for fact in facts},
                         "step references an absent or unsupported fact")
            supported = {identifier for fact in facts for identifier in fact["evidence_ids"]}
            _require(identifiers <= supported, "step evidence does not support its referenced inputs")
            if step["statement"] == "核对所引用输入事实":
                _require(bool(facts), "fact reasoning step requires supported inputs")
            elif step["statement"] == "输入缺失，无法形成有证据支撑的结论":
                _require(not identifiers and not facts and bool(content["limitations"]),
                         "missing-input step requires explicit limitations and no facts")
            else:
                _require(bool(facts) or bool(content["limitations"]),
                         "reasoning conclusion requires facts or explicit limitations")

    def _advice(self, field: dict, confidence: str, context: _Context) -> None:
        content = field["content"]
        advice = content["advice"]
        level = advice["level"]
        statements = {"LEVEL_1": "明确操作建议", "LEVEL_2": "条件式建议，证据有限",
                      "LEVEL_3": "证据不足，暂不给出操作建议"}
        _require(content["statement"] == statements[level], "advice level contradicts content")
        if level == "LEVEL_3":
            return
        _require(bool(field["evidence_ids"]) and bool(content["facts"]), "advice requires supported Evidence facts")
        _require(any(not fact["input_path"].startswith(("holding_position.", "canonical_market_data.stock_identity."))
                     for fact in content["facts"]), "identity or holdings alone cannot support advice")
        if level == "LEVEL_1":
            _require(confidence == "HIGH" and context.complete() and field["data_quality_status"] == "PASS"
                     and not content["limitations"], "LEVEL_1 requires complete verified evidence and HIGH confidence")
            return
        _require(confidence == "LOW" and bool(content["limitations"]), "LEVEL_2 requires LOW confidence and explicit limitations")
        condition = advice["condition"]
        actual = context.resolve(condition["input_path"])
        _require(type(actual) in (int, float), "condition must observe an available numeric input")
        _require(advice["observation_path"] == condition["input_path"], "condition must specify its observable input")
        _require(any(fact["input_path"] == condition["input_path"] and fact["evidence_ids"]
                     for fact in content["facts"]), "condition requires supporting fact Evidence")
        _require(actual == condition["value"], "condition anchor must be supplied input, never an invented threshold")
        if context.inputs["data_quality_report"]["REALTIME_QUOTE_STATUS"] == "MISSING":
            _require(not condition["input_path"].startswith("canonical_market_data.market_quotes."),
                     "missing realtime data cannot trigger a current-price condition")

    def _review(self, items: list[dict], targets: set[str], context: _Context) -> None:
        _require(len({stable_json(item) for item in items}) == len(items), "duplicate human confirmation item")
        advice_targets, quality_targets, assumption = set(), set(), False
        for item in items:
            if item["reason"] == "REVIEW_ADVICE":
                advice_targets.add(item["target"])
            elif item["reason"] == "REVIEW_ASSUMPTION":
                assumption = True
            else:
                _require(context.status(item["target"]) in {"WARNING", "UNVERIFIED", "MISSING", "NOT_AVAILABLE"},
                         "review item must identify actual abnormal data")
                quality_targets.add(item["target"])
        _require(advice_targets == set(ADVICE_FIELDS) and assumption and targets <= quality_targets,
                 "human confirmation must cover both advice fields, abnormal data and assumptions")
