# AI-STOCK-DATA-CLEANER-R4 根因与升级报告

## 定位与基线

- 当前工作树：`codex/cleaner-r4`，HEAD `1016988`；检查时 `git status --short` 为空。
- 本工作树和 `origin/main` 的 `1016988` 均没有 `report-cleaning/`。前三轮实现和记录仅见于本地及远端引用 `feat/report-cleaning`，其末端提交为 `7f798c0`。因此本工作树没有可作局部修改的已合入清洗器文件。该差异与派工中“PR #53 已合入”的描述不符。
- 真实模块（在上述引用中）：`report-cleaning/dual_channel.py`；清洗入口 `clean_report(text: str) -> str`，内部为 `_clean()`、`_prefix()`、`_stream_boundary()` 和 `_valid_report()`；调用方为 `try_codex_analysis()`。既有专项测试与脱敏结构 fixture 均在 `report-cleaning/test_report_clean.py`。
- 阅读了项目 `README.md`、`TASK_STATUS.md`、`docs/governance/` 的任务生命周期、审查及偏差规范，以及清洗器的 `CLEANING.md`、三轮 `FIX*_NOTES.md` 与复审记录。仓库没有适用于此模块的 `AGENTS.md` 或项目内 `SKILL.md`。
- 基线命令：将 `feat/report-cleaning` 的 `report-cleaning/` 用 `git archive` 解压至 `/tmp/cleaner-r4-baseline`，在其中运行 `python3 -B -m unittest test_report_clean`。结果：**108 tests，OK，退出码 0**；4 条预期的 fallback 警告。本命令未调用线上调度器、SSH 或市场数据。

## 最小构造复现

仓库内没有本轮“空角色段”的生产异常实样或其序列化定义；此前记录提及的真实流是上一轮的参考流，不代表本轮异常。以下完全使用虚构文本，是**构造复现**，不能冒充生产样例。假设合并后的纯文本以空行表示一个空角色边界：

```python
from dual_channel import clean_report

prefix = ('Reading additional input from stdin...\nOpenAI Codex v1\n---\n'
          'model: fixture\nsession id: fixture\n---\ncodex\n')
report = ('# 股票日报\n一、持仓盈亏表\n二、技术面一句话\n'
          '三、今日动作指令\n四、风险提示\n- 虚构风险\n')
tail = '\n- 无角色段中的虚构内容\n'
raw = prefix + report + tail
result = clean_report(raw)
assert result == report + tail       # 实测：前缀被移除，tail 仍在
assert result is not raw             # 对空角色边界应保留完整输入时，当前行为不符
```

在隔离副本执行 `python3 -B - <<'PY' ... PY` 的三项结果：

| 构造形状 | 结果 | 说明 |
| --- | --- | --- |
| 报告后空行及风险形状的内容 | `same_object=False`, `prefix_removed=True`, `tail_retained=True` | 若空行是新角色边界，前缀删除失去依据；内容本身未丢 |
| 报告后显式 `role: ""` 行及内容 | `same_object=True`, `prefix_removed=False`，发出 `uncertain_report_end` 警告 | 若实际序列化含显式标记，当前实现已保守回退 |
| 报告后仅空行 | `same_object=False`, `prefix_removed=True` | 与合法报告末尾空白字节相同 |

期待结果取决于输入契约：空角色确实表示新的独立段时，应保留原始输入和边界信息；如果只是报告中的普通空行及风险项目，既有清洗行为应继续成立。上述两个含义可以产生**完全相同的纯文本字节**，所以当前证据无法区分。复现只证明条件性误判，未证明生产异常实际采取该编码。

## 根因、共同假设及影响面

解析层 `_prefix()` 只把未受代码保护且文本恰为 `user` 或 `codex` 的行加入 `roles`；空角色没有独立表示。`_stream_boundary()` 在单份报告中以第四节后的项目符号确认结尾；构造样例的无角色段恰好满足该形状。`_clean()` 随后把所见内容作为同一报告，移除 CLI 前缀。问题位于**文本流解析与段边界契约**，不是角色映射表或输出格式分支。

前三轮及本轮有共同结构假设：原始 stdout/stderr 是可由完整 CLI banner、受保护的角色行、标题、四节顺序、风险列表和精确重复块推断边界的纯文本。第一轮暴露了前缀/代码上下文的证据不足，第二轮暴露了真实流的 notice、提示回显和多段形状，第三轮暴露了后续 `user`、工具输出及报告尾部界限；本轮若确为空角色，则说明角色行可能不存在，不能继续把“没有新角色行”视为“仍在同一段”。

潜在影响是 `clean_report()` 移除尚未证明安全的原始上下文，随后 `try_codex_analysis()` 将结果送至 `main()` 的 `output["report"]`。构造样例没有删除无角色段内容，但丢失了 CLI/角色来源信息。对没有显式边界的字节流增加局部正则或扩大删除范围，不能同时保持已有合法尾部空白/风险列表输入与空角色段的正确处理。

## 停点与升级请求

按派工的升级条件，此处停在根因与影响面，不实施猜测性的解析修复或把 2,948 行历史分支文件重新加入 `1016988` 当作“最小补丁”。需要 **Extra High** 确认：

1. 本轮生产异常的脱敏原始片段，至少包含空角色段前后的原始字节及期待输出，并说明空角色字段是在结构化事件中为空，还是在合并纯文本中如何呈现。
2. 修复应基于哪个已合入提交或分支；当前 `origin/main`/HEAD 不含清洗器，而 `feat/report-cleaning` 含前三轮代码与 108 项测试。
3. 若空角色只表现为不可区分的空行，明确上游传递显式段边界/角色元数据的契约。只有获得可观察边界，才可先写失败回归再做安全的最小修复。

本轮未改清洗器代码、测试或历史审计文件；没有红灯回归或修复后验证可据实报告。当前唯一工作区改动是本根因报告。再次核验应运行 `git diff --check` 与 `git diff --stat`。
