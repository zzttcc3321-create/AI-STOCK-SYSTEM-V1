"""Offline tests: no SSH, SCP, Codex, market-data fetch, or disk writes."""
import contextlib
import io
import json
import subprocess
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, mock_open, patch

import dual_channel
from dual_channel import clean_report

# Invented portfolio, date, prices, quantities, and prose throughout.
REPORT = (
    "# 持仓日报（2051-03-12）\n\n"
    "**一、持仓盈亏表**\n"
    "| 代码 | 名称 | 现价 | 涨跌幅 | 盈亏 |\n"
    "| 900003 | 示例股份丙 | 71.6800 | +1.73% | -86.52 |\n\n"
    "**二、技术面一句话**\n"
    "- 示例股份丙：虚构信号灯呈紫色，按样本节拍记录。\n\n"
    "**三、今日动作指令**\n"
    "- 示例股份丙：仅在模拟阈值74.3900触发样本标记时处理6137股，其余情况保留虚构仓位。\n\n"
    "**四、风险提示**\n"
    "- 本组虚构仓位仅测试文字保真，样本标记没有交易含义。\n"
)
HEADER = (
    "OpenAI Codex v0.0.0 (test fixture)\n"
    "--------\n"
    "workdir: /tmp/example\n"
    "model: gpt-6.1-sol\n"
    "provider: openai\n"
    "approval: never\n"
    "sandbox: read-only\n"
    "reasoning effort: high\n"
    "reasoning summaries: auto\n"
    "session id: fixture-session\n"
    "--------\n"
)
USAGE = "tokens used\n1,234\n"
AUDIT_REPORT = "# 股票日报\nKeep this report wording exactly as written.\n"

# Independently invented data: never populate this fixture from a capture.
STREAM_REPORT = (
    "**2047年6月19日｜持仓日报（08:37）**\n\n"
    "**日期**：2047-06-19\n\n"
    "**一、持仓盈亏表**\n"
    "| 代码 | 名称 | 现价 | 数量 | 盈亏 |\n"
    "| SYNTH-A | 示例股份甲 | 47.8300 | 137 | +18.7300 |\n"
    "| SYNTH-B | 示例股份乙 | 63.2900 | 259 | -24.6100 |\n\n"
    "**二、技术面一句话**（每只一行）\n"
    "- 示例股份甲：模拟信号保持平稳，按既定条件观察。\n"
    "- 示例股份乙：模拟量能偏弱，等待条件确认。\n\n"
    "**三、今日动作指令**\n"
    "- 示例股份甲：观察48.9700；满足模拟条件时处理137股。  \n"
    "- 示例股份乙：保留259股，等待64.5300的模拟条件。\n\n"
    "**四、风险提示**\n"
    "- 本例全部为虚构数据；重复段落、数字与末尾空格均须原样保留。\n"
)
STREAM_PREFIX = (
    "Reading additional input from stdin...\n"
    "OpenAI Codex v0.0.0 (synthetic stream fixture)\n"
    "--------\n"
    "workdir: /tmp/synthetic-report-fixture\n"
    "model: synthetic-model\n"
    "provider: synthetic-provider\n"
    "approval: never\n"
    "sandbox: read-only\n"
    "reasoning effort: high\n"
    "reasoning summaries: auto\n"
    "session id: synthetic-session-fix-two\n"
    "--------\n"
    "user\n"
    "请按以下模拟模板输出报告。\n"
    "**日期**：YYYY-MM-DD\n"
    "**一、持仓盈亏表**\n"
    "**二、技术面一句话**\n"
    "**三、今日动作指令**\n"
    "**四、风险提示**\n"
    "## 本次任务\n"
    "生成持仓日报（2047-06-19 08:37）。\n"
    "## 持仓数据\n"
    '{"date":"2047-06-19","holdings":['
    '{"code":"SYNTH-A","name":"示例股份甲","price":47.83,"quantity":137},'
    '{"code":"SYNTH-B","name":"示例股份乙","price":63.29,"quantity":259}]}\n'
    "warning: synthetic optional service unavailable\n"
    "codex\n"
    "我会先核对模拟输入，再生成示例日报。\n"
    "web search: 示例股份甲 模拟资料\n"
    "exec\n"
    "/bin/bash -lc 'synthetic-probe --version' in /tmp/synthetic-report-fixture exited 127:\n"
    "/bin/bash: line 1: synthetic-probe: command not found\n"
    "web search: 示例股份乙 模拟资料\n"
    "codex\n"
)
STREAM_USAGE = "tokens used\n8,765\n"
STRUCTURAL_STREAM = STREAM_PREFIX + STREAM_REPORT + STREAM_USAGE + STREAM_REPORT


class ReportCleanTests(unittest.TestCase):
    def fallback(self, raw):
        with self.assertLogs("dual_channel", level="WARNING") as messages:
            result = clean_report(raw)
        self.assertIs(result, raw)
        self.assertTrue(any("[REPORT_CLEAN_FALLBACK]" in line for line in messages.output))
        return result

    def test_cli_header(self):
        self.assertEqual(clean_report(HEADER + "codex\n" + REPORT), REPORT)

    def test_exact_duplicate(self):
        self.assertEqual(clean_report(REPORT + REPORT), REPORT)

    def test_usage_between_duplicates(self):
        raw = HEADER + "codex\n" + REPORT + USAGE + REPORT
        self.assertEqual(clean_report(raw), REPORT)

    def test_clean_report_unchanged(self):
        self.assertIs(clean_report(REPORT), REPORT)

    def test_idempotence(self):
        cases = [REPORT, REPORT * 2, HEADER + REPORT + USAGE + REPORT,
                 HEADER + "codex\n" + REPORT + USAGE, "\n" + REPORT + "\n\n"]
        for raw in cases:
            with self.subTest(raw=raw[:40]):
                once = clean_report(raw)
                self.assertEqual(clean_report(once), once)

    def test_unknown_falls_back(self):
        self.fallback("session id: fixture\nmodel: example\n完全无法识别的输出。\n")

    def test_header_and_footer(self):
        self.fallback(HEADER + REPORT + USAGE)

    def test_inline_usage_footer(self):
        self.fallback(HEADER + REPORT + "tokens used: 1,234\n")

    def test_loose_header(self):
        self.fallback("session id: fixture\nmodel: gpt-6.1-sol\n" + REPORT)

    def test_bare_codex_is_real_prelude(self):
        self.fallback("codex\n" + AUDIT_REPORT)

    def test_unwrapped_real_metadata(self):
        self.fallback("model: valuation\nsession id: research\n" + AUDIT_REPORT)

    def test_four_space_code_metadata(self):
        self.fallback("    model: valuation\n    session id: research\n\n" + AUDIT_REPORT)

    def test_indented_banner_is_protected(self):
        for indent in ("    ", "\t", " \t"):
            with self.subTest(indent=repr(indent)):
                self.fallback("".join(indent + line for line in HEADER.splitlines(keepends=True))
                              + AUDIT_REPORT)

    def test_indented_header_field_is_protected(self):
        self.fallback(HEADER.replace("model:", "    model:") + AUDIT_REPORT)

    def test_indented_leading_roles_are_protected(self):
        for role in ("codex", "user"):
            for indent in ("    ", "\t"):
                with self.subTest(role=role, indent=repr(indent)):
                    self.fallback(HEADER + indent + role + "\n" + AUDIT_REPORT)

    def test_indented_echo_role_cannot_authorize_prefix_deletion(self):
        self.fallback(HEADER + "user\nPrompt example:\n    codex\n" + AUDIT_REPORT)

    def test_fenced_echo_role_cannot_authorize_prefix_deletion(self):
        self.fallback(HEADER + "user\n```text\ncodex\n" + AUDIT_REPORT + "```\n")

    def test_echoed_prompt_and_reasoning(self):
        raw = HEADER + "user\n请按模板输出。\nmcp startup: no servers\nthinking\n分析中。\ncodex\n" + REPORT
        self.assertEqual(clean_report(raw), REPORT)

    def test_date_anchor_from_template(self):
        dated = REPORT.replace("# 持仓日报（2051-03-12）", "**2051-03-12**")
        self.assertEqual(clean_report(HEADER + dated * 2), dated)
        self.fallback(HEADER + dated + USAGE + dated)

    def test_section_as_first_paragraph(self):
        body = REPORT.split("\n\n", 1)[1]
        self.assertEqual(clean_report(body + body), body)

    def test_triple_duplicate(self):
        self.assertEqual(clean_report(HEADER + REPORT * 3), REPORT)
        self.fallback(HEADER + REPORT * 3 + USAGE)

    def test_different_number_falls_back(self):
        self.fallback(HEADER + REPORT + REPORT.replace("71.6800", "71.6900"))

    def test_truncated_copy_falls_back(self):
        self.fallback(HEADER + REPORT + REPORT[:len(REPORT) // 2])

    def test_repeated_legal_paragraph_preserved(self):
        paragraph = "- 虚构标记各自记录，不合并示例状态。\n"
        report = REPORT.replace("**四、风险提示**\n", "**四、风险提示**\n" + paragraph * 2)
        self.assertEqual(clean_report(HEADER + report), report)

    def test_similar_paragraphs_preserved(self):
        body = REPORT + "- 示例股份丙：观察模拟阈值74.3900。\n- 示例股份丁：观察模拟阈值74.3900。\n"
        self.assertEqual(clean_report(HEADER + body), body)

    def test_metadata_looking_body_preserved(self):
        body = REPORT.replace("**二、技术面一句话**", "model: 保留正文\nsession id: 保留正文\n"
                              "tokens used\n123\n**二、技术面一句话**")
        self.assertEqual(clean_report(HEADER + body), body)

    def test_fenced_anchor_and_metadata_preserved(self):
        body = REPORT + "\n\x60\x60\x60text\n# 持仓日报（2051-03-12）\nmodel: example\ntokens used\n123\n\x60\x60\x60\n"
        self.assertEqual(clean_report(HEADER + body), body)

    def test_tilde_fence_preserved(self):
        body = REPORT + "\n~~~text\n" + REPORT + "~~~\n"
        self.assertEqual(clean_report(HEADER + body), body)

    def test_numbers_wording_spaces_exact(self):
        body = REPORT.replace("71.6800", "+71.680000").replace("-86.52", "-000086.5200")
        body = body.replace("保留", "保留  ").replace("6137股", "6,137股  ")
        self.assertEqual(clean_report(HEADER + body * 2), body)
        self.fallback(HEADER + body * 2 + USAGE)

    def test_crlf_exact(self):
        header, report, usage = (s.replace("\n", "\r\n") for s in (HEADER, REPORT, USAGE))
        self.assertEqual(clean_report(header + report * 2), report)
        self.fallback(header + report + usage + report)

    def test_no_final_newline(self):
        body = REPORT.rstrip("\n")
        self.assertEqual(clean_report(HEADER + body), body)

    def test_clean_outer_whitespace_preserved(self):
        raw = "\n\n" + REPORT + " \n\n"
        self.assertIs(clean_report(raw), raw)

    def test_unknown_prelude_preserves_entire_original(self):
        self.fallback("unrecognized prelude\n" + REPORT)

    def test_incomplete_header_falls_back(self):
        self.fallback("OpenAI Codex v0.0.0\n--------\nmodel: fixture\n" + REPORT)

    def test_ambiguous_role_boundaries_fall_back(self):
        self.fallback(HEADER + "user\ncodex\n" + REPORT + "codex\n" + REPORT)

    def test_empty_falls_back(self):
        self.fallback("")

    def test_internal_failure_falls_back(self):
        with patch.object(dual_channel, "_prefix", side_effect=RuntimeError("fixture failure")):
            self.fallback(HEADER + REPORT)

    def test_body_only_usage_pair_preserved(self):
        body = REPORT + "tokens used\n123\n"
        self.assertEqual(clean_report(body * 2), body)

    def test_same_title_inside_body_is_not_deduped(self):
        self.fallback(HEADER + REPORT + "# 持仓日报（2051-03-12）\n仅为后记，不能删除。\n")

    def test_template_heading_suffixes(self):
        body = REPORT.replace("# 持仓日报（2051-03-12）", "**2051-03-12**")
        body = body.replace("**二、技术面一句话**", "**二、技术面一句话**（每只一行）")
        body = body.replace("**三、今日动作指令**", "**三、今日动作指令**（每只一行，加粗）")
        body = body.replace("**四、风险提示**", "**四、风险提示**（1-2条）")
        self.assertEqual(clean_report(HEADER + body * 2), body)

    def test_short_title_report(self):
        body = "# 股票日报\n本组虚构样本按紫色信号灯记录，示例阈值与文字均需完整保留。\n"
        self.assertLess(len(body), 100)
        self.assertEqual(clean_report(HEADER + body + body), body)

    def test_different_wording_falls_back(self):
        self.fallback(HEADER + REPORT + REPORT.replace("保留虚构仓位", "变更模拟状态"))

    def test_different_internal_spaces_fall_back(self):
        self.fallback(HEADER + REPORT + REPORT.replace("阈值74.3900", "阈值 74.3900"))

    def test_incomplete_date_report_falls_back(self):
        self.fallback(HEADER + "**2051-03-12**\n**一、持仓盈亏表**\n未完成的正文。\n")

    def test_mixed_line_endings_are_not_normalized(self):
        self.fallback(HEADER + REPORT + REPORT.replace("\n", "\r\n"))

    def test_nonidentical_final_eol(self):
        self.fallback(AUDIT_REPORT + AUDIT_REPORT[:-1] + "\r\n")

    def test_nonidentical_missing_final_eol(self):
        self.fallback(AUDIT_REPORT + AUDIT_REPORT[:-1])

    def test_nonidentical_boundary_whitespace(self):
        for suffix in ("\n", " \n", "\t\r\n"):
            with self.subTest(suffix=repr(suffix)):
                self.fallback(AUDIT_REPORT + suffix + AUDIT_REPORT)
                self.fallback(AUDIT_REPORT * 2 + suffix)

    def test_identical_boundary_whitespace_is_preserved(self):
        body = AUDIT_REPORT + " \n\t\n"
        self.assertEqual(clean_report(HEADER + body * 2), body)

    def test_dedup_without_envelope_retains_leading_whitespace(self):
        prefix = "\r\n \n"
        self.assertEqual(clean_report(prefix + AUDIT_REPORT * 2), prefix + AUDIT_REPORT)

    def test_footer_confirmation_does_not_ignore_final_eol(self):
        self.fallback(HEADER + AUDIT_REPORT + USAGE + AUDIT_REPORT[:-1] + "\r\n")
        self.fallback(HEADER + AUDIT_REPORT + USAGE + AUDIT_REPORT[:-1])

    def test_indented_title_in_body_preserved(self):
        body = REPORT + "\n    # 持仓日报（2051-03-12）\n    此处是正文中的代码示例。\n"
        self.assertEqual(clean_report(HEADER + body), body)

    def test_fenced_usage_at_end_preserved(self):
        body = REPORT + "\n```text\ntokens used\n123\n```\n"
        self.assertEqual(clean_report(HEADER + body), body)

    def test_usage_without_envelope_preserved(self):
        body = REPORT + USAGE
        self.assertIs(clean_report(body), body)

    def test_tab_indented_body_code(self):
        body = AUDIT_REPORT + "\n\ttokens used\n\t123\n"
        self.assertEqual(clean_report(HEADER + body), body)

    def test_indented_usage_code_preserved(self):
        for indent in ("    ", "\t", " \t", "  \t"):
            for usage in ("tokens used\n123\n", "tokens used: 123\n"):
                with self.subTest(indent=repr(indent), usage=usage):
                    code = "".join(indent + line for line in usage.splitlines(keepends=True))
                    body = AUDIT_REPORT + "\n" + code
                    self.assertEqual(clean_report(HEADER + body), body)

    def test_legitimate_body_usage_data(self):
        body = AUDIT_REPORT + "Report telemetry:\ntokens used\n123\n"
        self.fallback(HEADER + body)

    def test_identical_body_telemetry_preserved_with_envelope(self):
        body = AUDIT_REPORT + "Report telemetry:\n" + USAGE
        self.assertEqual(clean_report(HEADER + body * 2), body)

    def test_unconfirmed_usage_on_different_copies_falls_back(self):
        self.fallback(HEADER + AUDIT_REPORT + USAGE + AUDIT_REPORT + "tokens used\n999\n")

    def test_usage_difference_is_not_a_duplicate(self):
        body = AUDIT_REPORT + "Report telemetry:\n"
        self.fallback(HEADER + body + USAGE + body)

    def test_inline_usage_difference_is_not_a_duplicate(self):
        self.fallback(HEADER + AUDIT_REPORT + "tokens used: 1,234\n" + AUDIT_REPORT)

    def test_ambiguous_usage_with_trailing_whitespace_falls_back(self):
        for usage in (USAGE, "tokens used: 123\n"):
            with self.subTest(usage=usage):
                self.fallback(HEADER + AUDIT_REPORT + usage + " \n\t\r\n")

    def test_short_threshold_ignores_internal_whitespace(self):
        body = "# 股票日报\nx" + " " * 18 + "y\n"
        lines = body.splitlines(keepends=True)
        outside = dual_channel._outside_fences(lines)
        self.assertEqual(sum(not c.isspace() for c in lines[1]), 2)
        self.assertFalse(dual_channel._valid_report(lines, outside, 0, len(lines)))
        self.fallback(body * 2)

    def test_short_threshold_exact_boundary(self):
        for count in (19, 20):
            body = "# 股票日报\n" + " \t ".join("x" * count) + "\n"
            with self.subTest(nonwhitespace=count):
                if count < 20:
                    self.fallback(body * 2)
                else:
                    self.assertEqual(clean_report(HEADER + body * 2), body)

    def test_too_many_anchors_fall_back(self):
        self.fallback(HEADER + REPORT * 65)

    def test_deletion_only_and_idempotence_across_formats(self):
        for anchor in ("# 持仓晨报（2051-03-12）", "# 午盘简报", "**日期：2051年3月12日**"):
            for eol in ("\n", "\r\n"):
                body = REPORT.replace("# 持仓日报（2051-03-12）", anchor).replace("\n", eol)
                header = HEADER.replace("\n", eol)
                usage = USAGE.replace("\n", eol)
                for raw in (body, body * 2, header + body * 2, header + body):
                    with self.subTest(anchor=anchor, eol=repr(eol), length=len(raw)):
                        cleaned = clean_report(raw)
                        self.assertEqual(cleaned, body)
                        self.assertIn(cleaned, raw)
                        self.assertEqual(clean_report(cleaned), cleaned)
                self.fallback(header + body + usage + body)
                self.fallback(header + body + usage)

    def test_synthetic_real_stream_shape(self):
        with self.assertNoLogs("dual_channel", level="WARNING"):
            cleaned = clean_report(STRUCTURAL_STREAM)
        self.assertEqual(cleaned.encode("utf-8"), STREAM_REPORT.encode("utf-8"))
        self.assertIn(cleaned.encode("utf-8"), STRUCTURAL_STREAM.encode("utf-8"))
        self.assertEqual(clean_report(cleaned), cleaned)

    def test_stream_line_endings_and_optional_stdin_notice(self):
        for eol in ("\n", "\r\n", "\r"):
            for notice in (True, False):
                raw = STRUCTURAL_STREAM if notice else STRUCTURAL_STREAM.split("\n", 1)[1]
                with self.subTest(eol=repr(eol), notice=notice):
                    cleaned = clean_report(raw.replace("\n", eol))
                    self.assertEqual(cleaned, STREAM_REPORT.replace("\n", eol))
                    self.assertEqual(clean_report(cleaned), cleaned)

    def test_prompt_report_anchors_and_complete_example_are_ignored(self):
        raw = STRUCTURAL_STREAM.replace("## 本次任务\n", STREAM_REPORT + "## 本次任务\n")
        self.assertEqual(clean_report(raw), STREAM_REPORT)

    def test_intermediate_title_and_protected_sections_are_skipped(self):
        for fence in ("```", "~~~"):
            chatter = ("# 股票日报\n这是超过二十个非空白字符的模拟中间说明，尚未输出报告。\n"
                       + fence + "text\n" + STREAM_REPORT + fence + "\n")
            raw = STRUCTURAL_STREAM.replace("我会先核对模拟输入，再生成示例日报。\n", chatter)
            with self.subTest(fence=fence):
                self.assertEqual(clean_report(raw), STREAM_REPORT)

    def test_stream_title_requires_immediate_codex_boundary(self):
        self.fallback(STRUCTURAL_STREAM.replace(
            "codex\n" + STREAM_REPORT, "codex\n先补充一句模拟说明。\n" + STREAM_REPORT, 1))

    def test_stream_requires_complete_banner_and_roles(self):
        for raw in (
                STRUCTURAL_STREAM.replace("model: synthetic-model\n", ""),
                STRUCTURAL_STREAM.replace("session id: synthetic-session-fix-two\n", ""),
                STRUCTURAL_STREAM.replace("OpenAI Codex v0.0.0 (synthetic stream fixture)\n", ""),
                STRUCTURAL_STREAM.replace("codex\n", "assistant\n"),
                STRUCTURAL_STREAM.replace("Reading additional input from stdin...", "Unknown prelude"),
                "    " + STRUCTURAL_STREAM,
                STRUCTURAL_STREAM.replace("codex\n", "\tcodex\n")):
            with self.subTest(length=len(raw)):
                self.fallback(raw)

    def test_stream_requires_title_and_full_ordered_template(self):
        for body in (
                STREAM_REPORT.replace(STREAM_REPORT.splitlines()[0], "**2047-06-19**"),
                STREAM_REPORT[STREAM_REPORT.index("**一、持仓盈亏表**"):],
                STREAM_REPORT.replace("**二、技术面一句话**（每只一行）\n", ""),
                STREAM_REPORT.replace("**三、今日动作指令**", "**二、技术面一句话**"),
                STREAM_REPORT.replace("**二、技术面一句话**", "**三、今日动作指令**")
                             .replace("**四、风险提示**", "**二、技术面一句话**"),
                STREAM_REPORT + "**四、风险提示**\n重复标题使模板不确定。\n"):
            with self.subTest(length=len(body)):
                self.fallback(STREAM_PREFIX + body + STREAM_USAGE + body)

    def test_stream_template_cannot_span_codex_segments(self):
        first, second = STREAM_REPORT.split("**三、今日动作指令**", 1)
        self.fallback(STREAM_PREFIX + first + "codex\n**三、今日动作指令**" + second)

    def test_stream_web_search_tail_falls_back(self):
        self.fallback(STREAM_PREFIX + STREAM_REPORT + "web search: imaginary late trace\n")

    def test_stream_exec_tail_falls_back(self):
        self.fallback(STREAM_PREFIX + STREAM_REPORT + "exec\nsynthetic-probe exited 127\n")

    def test_stream_user_role_tail_falls_back(self):
        self.fallback(STREAM_PREFIX + STREAM_REPORT + "user\nChange the imaginary report.\n")

    def test_stream_template_cannot_span_user_segments(self):
        first, second = STREAM_REPORT.split("**三、今日动作指令**", 1)
        self.fallback(STREAM_PREFIX + first + "user\n**三、今日动作指令**" + second)

    def test_stream_plain_text_tail_falls_back(self):
        self.fallback(STREAM_PREFIX + STREAM_REPORT + "Unstructured imaginary continuation.\n")

    def test_stream_tails_across_envelopes_eols_and_copies(self):
        tails = ("web search: invented late trace\n", "exec\nfake-tool exited 127\n",
                 "user\nInvented follow-up.\n", "codex\nInvented follow-up.\n",
                 "Unstructured invented continuation.\n")
        for prefix in (STREAM_PREFIX, STREAM_PREFIX.split("\n", 1)[1], HEADER + "codex\n"):
            for eol in ("\n", "\r\n", "\r"):
                for body in (STREAM_REPORT, STREAM_REPORT * 2,
                             STREAM_REPORT + STREAM_USAGE + STREAM_REPORT):
                    for tail in tails:
                        with self.subTest(prefix=prefix[:20], eol=repr(eol),
                                          copies=body.count(STREAM_REPORT), tail=tail):
                            raw = (prefix + body + tail).replace("\n", eol)
                            self.assertIs(self.fallback(self.fallback(raw)), raw)

    def test_stream_numbered_risk_list_can_end_at_eof(self):
        body = STREAM_REPORT.replace("- 本例全部", "1. 本例全部")
        with self.assertNoLogs("dual_channel", level="WARNING"):
            self.assertEqual(clean_report(STREAM_PREFIX + body), body)

    def test_stream_unstructured_risk_section_is_ambiguous_without_copy(self):
        body = STREAM_REPORT.replace("- 本例全部", "本例全部")
        self.fallback(STREAM_PREFIX + body)
        self.assertEqual(clean_report(STREAM_PREFIX + body + STREAM_USAGE + body), body)

    def test_stream_protected_transcript_examples_are_preserved(self):
        for example in ("```text\nuser\nexec\nweb search: invented example\n```\n",
                        "    user\n    exec\n    web search: invented example\n"):
            body = STREAM_REPORT + example
            with self.subTest(example=example):
                self.assertEqual(clean_report(STREAM_PREFIX + body), body)
                self.assertEqual(clean_report(STREAM_PREFIX + body + STREAM_USAGE + body), body)

    def test_multiple_complete_codex_reports_are_ambiguous(self):
        self.fallback(STREAM_PREFIX + STREAM_REPORT + "codex\n" + STREAM_REPORT)

    def test_complete_report_before_later_chatter_is_ambiguous(self):
        self.fallback(STREAM_PREFIX + STREAM_REPORT + "codex\n后续模拟说明。\n")

    def test_stream_copy_differences_preserve_full_original(self):
        for copy in (STREAM_REPORT.replace("47.8300", "47.8400"),
                     STREAM_REPORT.replace("既定条件", "其他条件"),
                     STREAM_REPORT.replace("  \n", " \n"),
                     STREAM_REPORT[:-1], STREAM_REPORT[:-1] + "\r\n",
                     STREAM_REPORT + "\n", STREAM_REPORT[:len(STREAM_REPORT) // 2]):
            with self.subTest(length=len(copy)):
                self.fallback(STREAM_PREFIX + STREAM_REPORT + STREAM_USAGE + copy)

    def test_stream_usage_requires_exact_unprotected_separator(self):
        for usage in ("tokens used\n", "tokens used\nnot-a-count\n",
                      "\ttokens used\n\t8765\n", "    tokens used: 8765\n",
                      "```text\n" + STREAM_USAGE + "```\n",
                      STREAM_USAGE + "\n", "\n" + STREAM_USAGE,
                      STREAM_USAGE + " \n", "Report telemetry:\n" + STREAM_USAGE):
            with self.subTest(usage=repr(usage)):
                self.fallback(STREAM_PREFIX + STREAM_REPORT + usage + STREAM_REPORT)

    def test_stream_inline_usage_can_be_confirmed(self):
        raw = STREAM_PREFIX + STREAM_REPORT + "tokens used: 8,765\n" + STREAM_REPORT
        self.assertEqual(clean_report(raw), STREAM_REPORT)

    def test_stream_single_report_usage_remains_ambiguous(self):
        self.fallback(STREAM_PREFIX + STREAM_REPORT + STREAM_USAGE)

    def test_stream_full_report_without_usage(self):
        for copies in (1, 2, 3):
            with self.subTest(copies=copies):
                self.assertEqual(clean_report(STREAM_PREFIX + STREAM_REPORT * copies), STREAM_REPORT)

    def test_stream_extra_copy_with_usage_is_ambiguous(self):
        self.fallback(STREAM_PREFIX + STREAM_REPORT + STREAM_USAGE + STREAM_REPORT * 2)

    def test_stream_identical_telemetry_is_preserved(self):
        body = STREAM_REPORT + STREAM_USAGE
        self.assertEqual(clean_report(STREAM_PREFIX + body * 2), body)

    def test_usage_confirmation_requires_full_template_and_role(self):
        for prefix, body in ((HEADER, STREAM_REPORT),
                             (HEADER + "codex\n", AUDIT_REPORT),
                             ("", STREAM_REPORT)):
            with self.subTest(prefix=prefix[:20]):
                self.fallback(prefix + body + STREAM_USAGE + body)

    def test_stream_confirmed_copy_preserves_boundary_whitespace(self):
        body = STREAM_REPORT + " \n\t\n"
        self.assertEqual(clean_report(STREAM_PREFIX + body + STREAM_USAGE + body), body)


class RecoveryIntegrationTests(unittest.TestCase):
    @contextlib.contextmanager
    def recovered(self, raw):
        with contextlib.ExitStack() as stack:
            handles = []
            for name in ("prompt", "script"):
                handle = MagicMock()
                handle.name = "/tmp/clean_out/virtual_" + name
                handle.__enter__.return_value = handle
                handles.append(handle)
            stack.enter_context(patch.object(tempfile, "NamedTemporaryFile", side_effect=handles))
            disk_read = stack.enter_context(patch.object(dual_channel, "open", mock_open(read_data=raw), create=True))
            run = stack.enter_context(patch.object(subprocess, "run"))
            remote = stack.enter_context(patch.object(
                dual_channel, "ssh_run",
                side_effect=[SimpleNamespace(stdout="started", stderr=""),
                             SimpleNamespace(stdout="yes", stderr=""),
                             SimpleNamespace(stdout="", stderr="")]))
            stack.enter_context(patch.object(dual_channel.time, "sleep"))
            stack.enter_context(patch.object(dual_channel.os, "unlink"))
            stack.enter_context(patch.object(dual_channel.os.path, "exists", return_value=False))
            yield disk_read, run, remote, handles

    def test_recovery_cleans_before_return(self):
        raw = HEADER + "codex\n" + REPORT * 2
        with self.recovered(raw) as (disk, run, remote, _):
            with patch.object(dual_channel, "clean_report", wraps=clean_report) as cleaner:
                result = dual_channel.try_codex_analysis("offline fixture")
            self.assertEqual(result, {"success": True, "result": REPORT})
            cleaner.assert_called_once_with(raw)
            self.assertEqual(run.call_count, 3)
            self.assertEqual(remote.call_count, 3)
            self.assertEqual(disk.call_args.kwargs, {"encoding": "utf-8", "newline": ""})

    def test_recovery_preserves_ambiguous_usage(self):
        raw = HEADER + "codex\n" + REPORT + USAGE + REPORT.replace("71.6800", "71.6900")
        with self.recovered(raw), self.assertLogs("dual_channel", level="WARNING"):
            self.assertEqual(dual_channel.try_codex_analysis("fixture"),
                             {"success": True, "result": raw})

    def test_recovery_cleans_synthetic_real_stream(self):
        with self.recovered(STRUCTURAL_STREAM):
            self.assertEqual(dual_channel.try_codex_analysis("synthetic fixture"),
                             {"success": True, "result": STREAM_REPORT})

    def test_unknown_recovery_returns_raw(self):
        raw = "未知结构的短报告：仍需保留。\n"
        with self.recovered(raw), self.assertLogs("dual_channel", level="WARNING"):
            self.assertEqual(dual_channel.try_codex_analysis("fixture"), {"success": True, "result": raw})

    def test_short_clean_report_not_lost(self):
        body = "# 股票日报\n本组虚构样本按紫色信号灯记录，示例阈值与文字均需完整保留。\n"
        with self.recovered(HEADER + body):
            self.assertEqual(dual_channel.try_codex_analysis("fixture"), {"success": True, "result": body})

    def test_short_error_recovery_fails(self):
        raw = "Error: authentication failed.\n"
        with self.recovered(raw), self.assertLogs("dual_channel", level="WARNING"):
            self.assertEqual(dual_channel.try_codex_analysis("fixture"),
                             {"success": False, "error": raw.strip()})

    def test_cli_error_detection_is_content_based(self):
        for raw in ("Error: " + "authentication failed. " * 20,
                    "fatal: unable to connect\n", "codex: error: authentication failed\n",
                    HEADER + "codex\nError: authentication failed.\n"):
            with self.subTest(raw=raw[:60]), self.recovered(raw), \
                 self.assertLogs("dual_channel", level="WARNING"):
                result = dual_channel.try_codex_analysis("fixture")
                self.assertFalse(result["success"])
                self.assertIn("error", result)

    def test_report_discussing_cli_errors_is_accepted(self):
        body = AUDIT_REPORT + "The CLI said Error: authentication failed.\n"
        with self.recovered(HEADER + body):
            self.assertEqual(dual_channel.try_codex_analysis("fixture"),
                             {"success": True, "result": body})

    def test_short_error_recovery_engages_fallback_channel(self):
        raw = "Error: authentication failed.\n"
        stdout = io.StringIO()
        with self.recovered(raw), self.assertLogs("dual_channel", level="WARNING"):
            with patch.object(dual_channel, "fetch_market_data", return_value={"fixture": True}), \
                 patch.object(dual_channel, "build_prompt", return_value="fixture"), \
                 patch.object(dual_channel.sys, "argv", ["dual_channel.py", "evening"]), \
                 contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(io.StringIO()):
                dual_channel.main()
        output = json.loads(stdout.getvalue())
        self.assertEqual(output["channel"], "aqi_fallback")
        self.assertIsNone(output["report"])
        self.assertEqual(output["fallback_reason"], raw.strip())
        self.assertEqual(output["market_data"], {"fixture": True})

    def test_empty_recovery_still_fails(self):
        with self.recovered(""), self.assertLogs("dual_channel", level="WARNING"):
            self.assertEqual(dual_channel.try_codex_analysis("fixture"),
                             {"success": False, "error": "Codex输出为空"})

    def test_recovery_retains_whitespace(self):
        raw = "\n" + REPORT + "\n \n"
        with self.recovered(raw):
            self.assertEqual(dual_channel.try_codex_analysis("fixture")["result"], raw)

    def test_whitespace_only_recovery_still_fails(self):
        with self.recovered(" \r\n\t\n"), self.assertLogs("dual_channel", level="WARNING"):
            self.assertEqual(dual_channel.try_codex_analysis("fixture"),
                             {"success": False, "error": "Codex输出为空"})

    def test_unknown_report_delivered_in_json_with_fallback_log(self):
        raw = "\n未知格式，但这是需要保留的完整短报告：模拟价格71.6800，保留紫色标记。  \r\n"
        stdout = io.StringIO()
        with self.recovered(raw), self.assertLogs("dual_channel", level="WARNING") as messages:
            with patch.object(dual_channel, "fetch_market_data", return_value={}), \
                 patch.object(dual_channel, "build_prompt", return_value="fixture"), \
                 patch.object(dual_channel.sys, "argv", ["dual_channel.py", "evening"]), \
                 contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(io.StringIO()):
                dual_channel.main()
        self.assertEqual(json.loads(stdout.getvalue())["report"], raw)
        self.assertNotIn("[REPORT_CLEAN_FALLBACK]", stdout.getvalue())
        self.assertTrue(any("[REPORT_CLEAN_FALLBACK]" in line for line in messages.output))

    def test_model_reasoning_and_json_delivery(self):
        stdout = io.StringIO()
        with self.recovered(HEADER + REPORT * 2) as (_, _, _, handles):
            with patch.object(dual_channel, "fetch_market_data", return_value={}), \
                 patch.object(dual_channel, "build_prompt", return_value="fixture"), \
                 patch.object(dual_channel.sys, "argv", ["dual_channel.py", "evening"]), \
                 contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(io.StringIO()):
                dual_channel.main()
            script = handles[1].write.call_args.args[0]
        output = json.loads(stdout.getvalue())
        self.assertEqual(output["report"], REPORT)
        self.assertEqual(output["channel"], "codex")
        self.assertEqual(output["model"], "gpt-6.1-sol")
        self.assertIn('--config \'model_reasoning_effort="high"\'', script)
        self.assertEqual(output["report_type"], "evening")


if __name__ == "__main__":
    unittest.main(verbosity=2)
