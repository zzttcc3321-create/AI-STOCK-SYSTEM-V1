"""Deterministic P3 rendering; all business instructions come from the freeze."""

from __future__ import annotations

import hashlib
import re
from pathlib import Path
from typing import Any

from .contracts import AnalysisInputError, AnalysisPackage, PromptEnvelope, stable_json
from .package_builder import validate_package


PROMPT_VERSION = "PROMPT_TEMPLATE_V1.0"
TEMPLATE_PATH = Path(__file__).resolve().parents[1] / "docs/frozen/P3_PROMPT_TEMPLATE_V1.0.md"
TEMPLATE_SHA256 = "de2b358bbf72c6d1764ad6eb91567618595d9a70bf3fadaf2592988b0a2ab226"


def _section(text: str, heading: str, end: str) -> str:
    try:
        return text.split(heading + "\n", 1)[1].split(end + "\n", 1)[0].strip()
    except IndexError:
        raise AnalysisInputError("frozen prompt section is missing") from None


def _template() -> tuple[str, str]:
    # One read-only local file access. No network, model, store or write API.
    data = TEMPLATE_PATH.read_bytes()
    if hashlib.sha256(data).hexdigest() != TEMPLATE_SHA256:
        raise AnalysisInputError("frozen P3 template changed; version review required")
    text = data.decode("utf-8")
    system_section = _section(text, "### 6.1 System Prompt（系统提示词）", "### 6.2 User Prompt（用户提示词结构）")
    matched = re.search(r"```text\n(.*?)\n```", system_section, re.DOTALL)
    if matched is None:
        raise AnalysisInputError("frozen system prompt is missing")
    # Include the frozen mode, tiered advice, human-confirmation and missing-data
    # rules verbatim. No example holdings, dates, or pretend model version.
    sections = [
        matched.group(1),
        _section(text, "### 2.3 分析模式分支", "## 3. 分析输出契约"),
        _section(text, "### 3.2 \"建议位\"的强制要求（三级输出模式）", "### 3.3 每个字段的三要素"),
        _section(text, "## 4. Human-in-the-loop 设计", "## 5. 缺失数据处理模板"),
        _section(text, "## 5. 缺失数据处理模板", "## 6. 完整 Prompt 模板示例"),
    ]
    user_section = _section(text, "### 6.2 User Prompt（用户提示词结构）", "### 6.3 期望输出结构（示意框架，不填真实结论）")
    instruction = "按 system prompt 的 7 字段输出，并附\"人工确认项\"清单。"
    if instruction not in user_section:
        raise AnalysisInputError("frozen user instruction is missing")
    return "\n\n".join(sections), instruction


class PromptBuilder:
    """Map a single AnalysisPackage to precisely the frozen seven inputs."""

    def inputs(self, package: AnalysisPackage) -> dict[str, Any]:
        validate_package(package)
        value = package.to_dict()
        quality = value["data_quality"]
        # Identity is carried in market metadata so every supplied business
        # block remains visible and traceable despite the fixed seven keys.
        return {
            "stock_id": value["stock_id"],
            "holding_position": value["holding_snapshot"],
            "canonical_market_data": {
                "stock_identity": value["stock_identity"],
                "daily_kline": value["daily_kline_summary"],
                "market_quotes": value["latest_quote"],
            },
            "financial_data": value["financial_summary"],
            "intelligence_data": {name: value[name] for name in (
                "recent_announcements", "recent_news", "valuation_summary",
                "sector_context", "research_summary",
            )},
            "evidence_index": value["evidence_index"],
            "data_quality_report": quality,
        }

    def build(self, package: AnalysisPackage) -> PromptEnvelope:
        inputs = self.inputs(package)
        system, instruction = _template()
        metadata = {"prompt_version": PROMPT_VERSION, "position_mode": package.position_mode,
                    "analysis_mode": package.analysis_mode}
        user = stable_json(metadata) + "\n" + stable_json(inputs) + "\n\n" + instruction
        identifiers = tuple(item["evidence_id"] for item in package.evidence_index)
        rendered = {
            "prompt_version": PROMPT_VERSION, "stock_id": package.stock_id,
            "position_mode": package.position_mode, "analysis_mode": package.analysis_mode,
            "system_prompt": system, "user_prompt": user,
            "evidence_ids": identifiers, "template_hash": TEMPLATE_SHA256,
        }
        input_hash = hashlib.sha256(stable_json(rendered).encode("utf-8")).hexdigest()
        return PromptEnvelope(**rendered, input_hash=input_hash)
