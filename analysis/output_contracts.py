"""P3 structured output V1: seven analysis fields and two review sections.

Facts are exact input-path/value assertions. Commentary uses a closed vocabulary
so unsupported company facts cannot hide inside otherwise valid prose. This is
an output representation, not a new evidence sufficiency score.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from typing import Any, Mapping

from .contracts import AnalysisInputError, freeze, jsonable, safe_json, stable_json

OUTPUT_SCHEMA_VERSION = "P3_SEVEN_FIELD_OUTPUT_V1.0"
ANALYSIS_FIELDS = (
    "market_view", "position_status", "risk_level", "operation_plan",
    "t_strategy", "reasoning", "confidence",
)
ADVICE_FIELDS = ("operation_plan", "t_strategy")
STATEMENTS = {
    "market_view": ("历史行情分析", "证据不足，趋势不明"),
    "position_status": ("无持仓（观察模式）", "持仓事实来自 portfolio_positions，行情判断引用 Evidence"),
    "risk_level": ("依据证据评估风险", "证据不足，风险不明"),
    "operation_plan": ("明确操作建议", "条件式建议，证据有限", "证据不足，暂不给出操作建议"),
    "t_strategy": ("明确操作建议", "条件式建议，证据有限", "证据不足，暂不给出操作建议"),
    "reasoning": ("推理仅依据所列输入与证据",),
    "confidence": ("数据缺失或口径未验证导致置信度降低", "依据完整已验证证据"),
}
ASSESSMENTS = {
    "market_view": ("UPWARD", "SIDEWAYS", "DOWNWARD", "UNCLEAR"),
    "risk_level": ("HIGH", "MEDIUM", "LOW", "UNKNOWN"),
    "confidence": ("HIGH", "MEDIUM", "LOW"),
}
REASONING_STEP_STATEMENTS = (
    "核对所引用输入事实",
    "依据所引用事实与限制，结论须人工核查",
    "输入缺失，无法形成有证据支撑的结论",
)
ACTIONS = ("BUY", "SELL", "HOLD", "ADD", "REDUCE", "STOP_LOSS", "TAKE_PROFIT")
REVIEW_ASSUMPTION = "分析仅依据所提供的证据，缺失数据不补足"
LIMITATION_REASONS = {
    "WARNING": "输入含 WARNING，尚需人工核查",
    "UNVERIFIED": "输入口径未验证",
    "MISSING": "输入未提供可用数据",
    "NOT_AVAILABLE": "输入声明不可用",
}
LIMITATION_IMPACT = "限制本字段判断，降低置信度"


class AnalysisOutputError(ValueError):
    """An output fails a hard gate; messages never include model content."""


def digest(value: Any) -> str:
    return hashlib.sha256(stable_json(value).encode("utf-8")).hexdigest()


def _object(properties: dict, required: tuple | None = None) -> dict:
    return {"type": "object", "properties": properties,
            "required": list(properties if required is None else required),
            "additionalProperties": False}


def output_schema() -> dict[str, Any]:
    """Return a fresh JSON Schema; executor mutation cannot change our gates."""
    text = {"type": "string", "minLength": 1}
    ids = {"type": "array", "items": text, "uniqueItems": True}
    fact = _object({"input_path": text, "value": {"type": ["string", "number", "boolean"]},
                    "evidence_ids": ids})
    step = _object({"statement": {"enum": list(REASONING_STEP_STATEMENTS)},
                    "evidence_ids": ids,
                    "fact_refs": {"type": "array", "items": text, "uniqueItems": True}},
                   required=("statement", "evidence_ids"))
    limitation = _object({"input_path": text, "status": {"enum": list(LIMITATION_REASONS)},
                          "reason": text, "impact": {"const": LIMITATION_IMPACT}})
    condition = _object({"input_path": text, "operator": {"enum": ["GT", "LT", "EQ"]},
                         "value": {"type": "number"}})
    advice = {"oneOf": [
        _object({"level": {"const": "LEVEL_1"}, "action": {"enum": list(ACTIONS)}}),
        _object({"level": {"const": "LEVEL_2"}, "action": {"enum": list(ACTIONS)},
                 "condition": condition, "observation_path": text,
                 "verification": {"const": "MANUAL_RECHECK"}}),
        _object({"level": {"const": "LEVEL_3"}}),
    ]}
    fields = {}
    for name in ANALYSIS_FIELDS:
        content = {"statement": {"enum": list(STATEMENTS[name])},
                   "facts": {"type": "array", "items": fact},
                   "limitations": {"type": "array", "items": limitation}}
        if name in ASSESSMENTS:
            content["assessment"] = {"enum": list(ASSESSMENTS[name])}
        if name in ADVICE_FIELDS:
            content["advice"] = advice
        if name == "reasoning":
            content["steps"] = {"type": "array", "items": step, "minItems": 1}
        properties = {
            "content": _object(content), "evidence_ids": ids,
            "time_range": _object({"start": {"type": ["string", "null"]},
                                   "end": {"type": ["string", "null"]}}),
            "data_quality_status": {"enum": ["PASS", "WARNING", "UNVERIFIED", "MISSING", "NOT_AVAILABLE"]},
        }
        if name == "position_status":
            properties["input_refs"] = {"type": "array", "items": {"const": "holding_position"}, "uniqueItems": True}
        fields[name] = _object(properties)
    confirmation = {"oneOf": [
        _object({"target": {"enum": list(ADVICE_FIELDS)}, "reason": {"const": "REVIEW_ADVICE"}}),
        _object({"target": text, "reason": {"const": "REVIEW_DATA_QUALITY"}}),
        _object({"target": {"const": "reasoning"}, "reason": {"const": "REVIEW_ASSUMPTION"},
                 "assumption": {"const": REVIEW_ASSUMPTION}}),
    ]}
    return {"$schema": "https://json-schema.org/draft/2020-12/schema",
            "$id": OUTPUT_SCHEMA_VERSION, **_object({
                "analysis": _object(fields),
                "human_confirmation_items": {"type": "array", "items": confirmation, "minItems": 3},
                "final_state": {"const": "UNCONFIRMED"},
            })}


def _unique_object(pairs: list[tuple[str, Any]]) -> dict:
    result = {}
    for key, value in pairs:
        if key in result:
            raise AnalysisOutputError("duplicate JSON key")
        result[key] = value
    return result


def _nonfinite(_: str) -> None:
    raise AnalysisOutputError("nonfinite JSON number")


class OutputParser:
    """Accept one JSON text response, without fences, repair or coercion."""

    def parse(self, response: str) -> dict[str, Any]:
        if type(response) is not str:
            raise AnalysisOutputError("executor response must be JSON text")
        try:
            value = json.loads(response, object_pairs_hook=_unique_object,
                               parse_constant=_nonfinite)
            value = safe_json(value)
        except (ValueError, TypeError, RecursionError, AnalysisInputError):
            raise AnalysisOutputError("invalid, sensitive or nonfinite JSON output") from None
        if not isinstance(value, dict):
            raise AnalysisOutputError("output must be a JSON object")
        return value


@dataclass(frozen=True, slots=True)
class ValidatedAnalysisDraft:
    output: Mapping[str, Any]
    human_confirmation_items: tuple[Mapping[str, Any], ...]
    input_hash: str
    template_hash: str
    model_output_hash: str
    validated_output_hash: str
    input_evidence_ids: tuple[str, ...]
    used_evidence_ids: tuple[str, ...]
    output_schema_version: str = OUTPUT_SCHEMA_VERSION
    analysis_status: str = field(default="UNCONFIRMED", init=False)
    human_confirm: str = field(default="PENDING", init=False)
    final_state: str = field(default="UNCONFIRMED", init=False)
    _gate_digest: str | None = field(default=None, init=False, repr=False, compare=False)

    def __post_init__(self) -> None:
        object.__setattr__(self, "output", freeze(safe_json(self.output)))
        object.__setattr__(self, "human_confirmation_items", freeze(safe_json(self.human_confirmation_items)))
        object.__setattr__(self, "input_evidence_ids", tuple(self.input_evidence_ids))
        object.__setattr__(self, "used_evidence_ids", tuple(self.used_evidence_ids))

    def to_dict(self) -> dict[str, Any]:
        from dataclasses import fields
        return {f.name: jsonable(getattr(self, f.name)) for f in fields(self) if not f.name.startswith("_")}


def validate_draft(draft: ValidatedAnalysisDraft) -> None:
    if type(draft) is not ValidatedAnalysisDraft or draft._gate_digest != digest(draft.to_dict()):
        raise AnalysisOutputError("draft has not passed validation or changed after validation")
    if digest(draft.output) != draft.validated_output_hash:
        raise AnalysisOutputError("draft output hash mismatch")
