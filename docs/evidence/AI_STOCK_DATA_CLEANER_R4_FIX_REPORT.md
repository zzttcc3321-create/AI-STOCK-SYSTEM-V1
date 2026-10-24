# AI-STOCK-DATA-CLEANER-R4 复现核验与停工报告

日期：2026-10-24。结论：**未复现指定缺陷，按任务停点停工；未修改清洗器或测试。** 本报告不是修复完成证明。

## 固定基线

- 工作树分支 `codex/cleaner-r4-fix`，HEAD `7f798c0605c049ea2dc758a76eb82d783cb75d59`；本地 `feat/report-cleaning` 引用同一提交。没有跟随任何新上游 HEAD。
- 开工时 `git status --short` 只有预置的未跟踪根因报告 `docs/evidence/AI_STOCK_DATA_CLEANER_R4_ROOT_CAUSE.md`。未读取或改写生产部署副本。
- 从 `report-cleaning/` 运行 `python3 -B -m unittest test_report_clean`：**108 tests，OK，退出码 0**；四条预期 fallback 日志。未运行调度器、SSH 或市场数据任务。
- 已阅读首轮根因报告、`dual_channel.py`、`CLEANING.md`、`FIX_NOTES.md`、`FIX2_NOTES.md`、`FIX3_NOTES.md`、三份 `REAUDIT*VERDICT.md` 与现有测试。

## 解析依据与脱敏最小构造

`test_report_clean.py` 的 `STREAM_PREFIX` 给出可核实的流形状：stdin notice、完整 Codex banner、两条横线包围的元数据（含 `model` 和 `session id`）、`user` 提示、一个 `codex` 中间输出段、末尾另一个 `codex` 报告段。角色行是独占一行的裸 `codex\n`；`STREAM_REPORT` 是虚构持仓的四节报告；`STREAM_USAGE` 和第二份完全相同的报告是已审计的使用量分隔形状。现有 `test_stream_line_endings_and_optional_stdin_notice` 验证 LF、CRLF、CR。

在两个原有 Codex 段之间插入一个 Codex 段，不使用任何生产内容：

```python
from test_report_clean import STREAM_PREFIX, STREAM_REPORT, STREAM_USAGE
from dual_channel import clean_report

prefix = STREAM_PREFIX[:-len("codex\n")] + "codex\n" + "codex\n"
raw = prefix + STREAM_REPORT + STREAM_USAGE + STREAM_REPORT
expected = STREAM_REPORT
assert sum(line.strip() == "codex" for line in raw.splitlines()) == 3
assert clean_report(raw).encode("utf-8") == expected.encode("utf-8")
```

第一段仍是 fixture 内已有的中间 chatter；第二段从其 `codex` 角色行到紧接着的下一条 `codex` 角色行，内容长度为零；第三段是完整报告。空白变体在第二个角色行后插入 `" \t\n"`，不触及正文。所有构造均基于已核实的本地序列化形状，但**生产异常的原始字节不存在**；生产是否真的采用这种相邻裸角色行、第三段是否确实为最终报告、是否还有额外用户或工具段，均无法核实。这些构造只是脱敏假设，不能代表生产实样。

## 复现结果与完整输出字节比较

在基线上，空段/仅空白段 × 单份报告/用量分隔加精确第二份 × LF/CRLF/CR，共 **12/12** 个构造：均识别到三个裸 `codex` 行，`clean_report(raw).encode("utf-8") == STREAM_REPORT` 对应行尾版本的完整 UTF-8 字节，输出是原输入中的连续字节片段，无 warning，未出现 `ambiguous_transcript_roles` 或等价整份放行。

| 行尾 | 中间段 | 单份原文/输出字节 | 重复原文/输出字节 | 输出 SHA-256（两种报告形状相同） |
| --- | --- | ---: | ---: | --- |
| LF | 空 | 1828 / 729 | 2575 / 729 | `67836dc8bef915401e0e717a2b99fedc108a41f7216906b59bf11ec65b909482` |
| LF | `" \t"` | 1831 / 729 | 2578 / 729 | 同上 |
| CRLF | 空 | 1880 / 748 | 2648 / 748 | `237e0ee2f63b26ced54a768298d775da8b42e472a8de8f925f32731a7f213b56` |
| CRLF | `" \t"` | 1884 / 748 | 2652 / 748 | 同上 |
| CR | 空 | 1828 / 729 | 2575 / 729 | `d05ed0d7bbfd70d9d03c216f78e5759a32da973a352a960a224811410363c006` |
| CR | `" \t"` | 1831 / 729 | 2578 / 729 | 同上 |

这些长度和哈希只涉及虚构 fixture。没有生产输出可作字节比较。

## 分支级定位及异常路径

`_prefix()` 第 143–160 行把完整 header 后、代码保护范围之外、`.strip()` 后恰为 `user` 或 `codex` 的行列为角色边界。第 150–159 行逐段寻找紧跟 `codex` 的完整四节报告；第 160 行仅当合格报告段不唯一，或它不是最后一个角色段时抛 `ValueError("ambiguous_transcript_roles")`。本次构造中，中间空段没有候选报告，最后一段恰好是唯一合格段，所以不会走该异常分支。随后 `_clean()` 第 303–326 行的边界检查和精确重复比较均通过。

一个报告后确实再有新角色或正文中出现独占一行的裸 `codex`，会被当前解析逻辑当作另一边界，可能整份放行；但这与已核实的“中间空段、最后报告”构造不同。现有 `test_multiple_complete_codex_reports_are_ambiguous`、`test_complete_report_before_later_chatter_is_ambiguous` 和第三轮复审要求该路径保守处理。仅凭纯文本 `codex` 一行，不能安全断言它是协议角色还是有效报告正文；任意放宽会有丢失其他段有效内容的风险。这里没有证据可把该路径认定为 2026-10-23 07:38 的根因。

`clean_report()` 第 335–339 行会捕获上述异常，记录 `[REPORT_CLEAN_FALLBACK]` 并返回**原始输入对象**；本轮十二个构造均未进入该异常路径。首轮“空行/显式空角色行”构造与本轮“三个可识别 Codex 角色段”不同，不拿它代替复现。

## 回归、改动和下一步证据

要求先得到稳定红灯再修复。指定形状的第 ①、② 项在基线即为绿灯，故**没有失败回归、没有红灯→绿灯证据，也没有新增测试或代码修复**。③–⑥ 项不得在根因未确认时驱动猜测性改动。前三轮 108 项既有测试仍全通过；新旧测试差异为零。异常路径与字节保真未因本轮改动改变。

需要生产流的脱敏原始字节片段：完整 header 后的三个 `codex` 角色行及各自前后若干行（保留空白、缩进、行尾）、最终报告的起止与使用量分隔、是否存在后续 `user`/工具段，以及期望输出的字节边界。若生产仅有结构化事件而合并文本丢失了段边界，则需说明该输入契约；在此之前无法确定安全的最小修复。取得证据后再做失败回归和函数/分支级修复评估。

当前无已跟踪文件改动，`git diff --stat` 为空；`git status --short` 包含预置根因报告与本新增停工报告两个未跟踪文件。未提交、推送、建 PR 或合并 PR #53。
