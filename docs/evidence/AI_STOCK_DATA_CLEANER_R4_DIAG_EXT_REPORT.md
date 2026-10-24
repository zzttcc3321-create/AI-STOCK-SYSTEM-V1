# AI-STOCK-DATA-CLEANER-R4 扩大诊断报告（R4-DIAG-EXT-01）

日期：2026-10-24。性质：只读、脱敏合成数据诊断。阶段结论：**R4_ROOT_CAUSE=NOT_CONFIRMED；R4_FIX=NOT_AUTHORIZED**。

## 基线与执行边界

- 工作树 `codex/cleaner-r4-fix`，开工 HEAD `f2ce88eda5b00b29ad61425ca8ed2b983a201c42`，工作树干净。固定基线 `feat/report-cleaning@7f798c0605c049ea2dc758a76eb82d783cb75d59` 未移动。`7f798c0..HEAD` 只新增首轮根因报告和复现核验报告；`report-cleaning/dual_channel.py`、`CLEANING.md` 和 `test_report_clean.py` 与固定基线相同。没有跟随上游新 HEAD。
- 开工复跑：在 `report-cleaning/` 执行 `python3 -B -m unittest test_report_clean`，**108 tests，OK，退出码 0**；四条既有、预期的 fallback 日志。没有运行线上调度器、SSH、市场数据任务。
- 已读两份前期报告、`dual_channel.py`、`CLEANING.md` 和 `test_report_clean.py`。本轮没有修改清洗器、测试或生产部署副本，没有新增测试文件。

## 合成矩阵的共同定义

所有文本从测试文件的 `STREAM_PREFIX`、`STREAM_REPORT`、`STREAM_USAGE` 构造，不含生产内容。`P` 表示完整 `STREAM_PREFIX`：stdin notice、完整 CLI header、`user` 提示、已有 `codex` chatter/工具轨迹、最后一个裸 `codex` 角色行。`R` 表示四节完整报告；`U` 表示 `STREAM_USAGE`。**单份**模式为 `R`，**重复**模式为 `R+U+R`，两份报告字节完全一致。整段输入统一将 `\n` 换为 LF、CRLF 或 CR；预期输出也使用相同行尾。第 4 类因定义上有两个合格角色段，单份模式是各段一份 `R`，重复模式是在第二个角色段内用 `R+U+R`。第 6 类的 `R*` 在第四节前插入围栏代码块 ` ```text / codex / synthetic example / ``` `（各占一行）；重复模式使用完全相同的两份 `R*`。

形状及其**预期契约**：

1. `P+R[+U+R]+user+后续虚构请求`：完整报告之后有新用户段，保留整份原文。
2. `P+R[+U+R]+codex+非报告说明`：最后角色段不含报告，保留整份原文。
3. `P+R[+U+R]+exec+虚构工具输出`：工具尾段没有已证实的安全报告终点，保留整份原文。
4. `P+R+codex+R[+U+R]`：两个 `codex` 角色段各有合格报告，保守拒绝并保留整份原文。
5. 将 `P` 的最后 `codex` 改成相邻两个 `codex`（中间空段），再接 `R[+U+R]+user+后续虚构请求`：中间空段与报告后角色段共存，保留整份原文。
6. `P+R*[+U+R*]`：报告正文的围栏代码中有独占一行的 `codex`，安全清洗为完整 `R*`；代码块和原始行尾必须保真。

表中“原文”指返回**同一输入对象**，其完整 UTF-8 字节未变；“R* 字节相等”指输出与该行尾版本的 `R*` 完整 UTF-8 字节相等，且是原输入的连续片段。异常类别是 `clean_report()` 捕获后写入 warning 的原因；没有异常向调用方抛出。“目标异常”仅指 warning 原因为 `ValueError:ambiguous_transcript_roles`。

| 输入结构 | 行尾 | 模式 | 逐项预期 | 实际输出 | 异常类别 | 触发目标异常 |
| --- | --- | --- | --- | --- | --- | --- |
| 1 报告→user | LF | 单份 | 原文 | 原文 | `ValueError:ambiguous_transcript_roles` | 是 |
| 1 报告→user | LF | 重复 | 原文 | 原文 | `ValueError:ambiguous_transcript_roles` | 是 |
| 1 报告→user | CRLF | 单份 | 原文 | 原文 | `ValueError:ambiguous_transcript_roles` | 是 |
| 1 报告→user | CRLF | 重复 | 原文 | 原文 | `ValueError:ambiguous_transcript_roles` | 是 |
| 1 报告→user | CR | 单份 | 原文 | 原文 | `ValueError:ambiguous_transcript_roles` | 是 |
| 1 报告→user | CR | 重复 | 原文 | 原文 | `ValueError:ambiguous_transcript_roles` | 是 |
| 2 报告→codex 非报告 | LF | 单份 | 原文 | 原文 | `ValueError:ambiguous_transcript_roles` | 是 |
| 2 报告→codex 非报告 | LF | 重复 | 原文 | 原文 | `ValueError:ambiguous_transcript_roles` | 是 |
| 2 报告→codex 非报告 | CRLF | 单份 | 原文 | 原文 | `ValueError:ambiguous_transcript_roles` | 是 |
| 2 报告→codex 非报告 | CRLF | 重复 | 原文 | 原文 | `ValueError:ambiguous_transcript_roles` | 是 |
| 2 报告→codex 非报告 | CR | 单份 | 原文 | 原文 | `ValueError:ambiguous_transcript_roles` | 是 |
| 2 报告→codex 非报告 | CR | 重复 | 原文 | 原文 | `ValueError:ambiguous_transcript_roles` | 是 |
| 3 报告→exec 工具输出 | LF | 单份 | 原文 | 原文 | `ValueError:uncertain_report_end` | 否 |
| 3 报告→exec 工具输出 | LF | 重复 | 原文 | 原文 | `ValueError:uncertain_report_end` | 否 |
| 3 报告→exec 工具输出 | CRLF | 单份 | 原文 | 原文 | `ValueError:uncertain_report_end` | 否 |
| 3 报告→exec 工具输出 | CRLF | 重复 | 原文 | 原文 | `ValueError:uncertain_report_end` | 否 |
| 3 报告→exec 工具输出 | CR | 单份 | 原文 | 原文 | `ValueError:uncertain_report_end` | 否 |
| 3 报告→exec 工具输出 | CR | 重复 | 原文 | 原文 | `ValueError:uncertain_report_end` | 否 |
| 4 两个合格 codex 段 | LF | 单份 | 原文 | 原文 | `ValueError:ambiguous_transcript_roles` | 是 |
| 4 两个合格 codex 段 | LF | 重复 | 原文 | 原文 | `ValueError:ambiguous_transcript_roles` | 是 |
| 4 两个合格 codex 段 | CRLF | 单份 | 原文 | 原文 | `ValueError:ambiguous_transcript_roles` | 是 |
| 4 两个合格 codex 段 | CRLF | 重复 | 原文 | 原文 | `ValueError:ambiguous_transcript_roles` | 是 |
| 4 两个合格 codex 段 | CR | 单份 | 原文 | 原文 | `ValueError:ambiguous_transcript_roles` | 是 |
| 4 两个合格 codex 段 | CR | 重复 | 原文 | 原文 | `ValueError:ambiguous_transcript_roles` | 是 |
| 5 中间空 codex 段＋报告后 user | LF | 单份 | 原文 | 原文 | `ValueError:ambiguous_transcript_roles` | 是 |
| 5 中间空 codex 段＋报告后 user | LF | 重复 | 原文 | 原文 | `ValueError:ambiguous_transcript_roles` | 是 |
| 5 中间空 codex 段＋报告后 user | CRLF | 单份 | 原文 | 原文 | `ValueError:ambiguous_transcript_roles` | 是 |
| 5 中间空 codex 段＋报告后 user | CRLF | 重复 | 原文 | 原文 | `ValueError:ambiguous_transcript_roles` | 是 |
| 5 中间空 codex 段＋报告后 user | CR | 单份 | 原文 | 原文 | `ValueError:ambiguous_transcript_roles` | 是 |
| 5 中间空 codex 段＋报告后 user | CR | 重复 | 原文 | 原文 | `ValueError:ambiguous_transcript_roles` | 是 |
| 6 正文围栏内 codex | LF | 单份 | R* 字节相等 | R* 字节相等 | 无 | 否 |
| 6 正文围栏内 codex | LF | 重复 | R* 字节相等 | R* 字节相等 | 无 | 否 |
| 6 正文围栏内 codex | CRLF | 单份 | R* 字节相等 | R* 字节相等 | 无 | 否 |
| 6 正文围栏内 codex | CRLF | 重复 | R* 字节相等 | R* 字节相等 | 无 | 否 |
| 6 正文围栏内 codex | CR | 单份 | R* 字节相等 | R* 字节相等 | 无 | 否 |
| 6 正文围栏内 codex | CR | 重复 | R* 字节相等 | R* 字节相等 | 无 | 否 |

**矩阵合计 36/36 项符合上述预期**：24 项触发目标异常（形状 1、2、4、5，各 6 项）；6 项 `uncertain_report_end`（形状 3）；6 项无 warning 且保真清洗（形状 6）。本轮没有已证实的“应安全清洗却未清洗”输入，因此没有修复意义上的 FAIL 红灯。目标异常出现仅说明异常路径被触发，不能把现行 fail-open 设计误报成缺陷。

## 函数与分支级判断

`_prefix()` 在完整 banner/header 后，用 `_outside_fences()` 掩码排除代码上下文，仅把未受保护且整行 `.strip()` 为 `user` 或 `codex` 的行作为角色边界（第 141–145 行）。含 stdin notice 或多个 `codex` 段时，逐段检查紧随 `codex` 的标题和四节模板，形成合格候选（第 146–159 行）。**仅当候选数不等于 1，或唯一候选不属于最后一个角色段时**，第 160–161 行抛 `ambiguous_transcript_roles`。

- 形状 1、2、5：报告段只有一个合格候选，但后面还有 `user` 或非报告 `codex` 角色段；命中“候选不在末角色段”。形状 5 的中间空段本身不产生候选；去掉报告后的 `user`，最后一段恰为唯一合格报告，按前轮 12 项核验会正常清洗。**空段本身不是异常根因。**
- 形状 4：两个角色段各自产生合格候选；命中“候选数量不等于 1”。即使第二段内部有完全重复报告，仍有两个不同角色段的合格候选。
- 形状 3：`exec` 不在 `_prefix()` 的 `user`/`codex` 角色边界集合中；唯一报告仍位于最后被识别的角色段。随后 `_clean()` 调用 `_stream_boundary()`，其第 212–213 行发现未受保护的 `exec`，抛 `uncertain_report_end`，保留工具输出及全部原文。
- 形状 6：围栏内的 `codex` 被 `_outside_fences()` 标为受保护，不增加角色边界，也不触发 `_stream_boundary()` 的尾段检查。`_clean()` 对单份报告保留完整正文；重复模式通过 `_confirmed_usage_copy()` 的精确相等检查，返回第一份完整 `R*`。

`clean_report()` 第 335–339 行捕获这些异常、记录原因并返回原输入对象。该 fail-open 属于已批准的保守契约。首轮报告中的“无显式角色标记的空行”只是边界编码假设；它与本轮三个可识别裸 `codex` 角色段不同，不能用来确认本次生产根因。**根因判断：未确认。** 当前只确认了哪些合成结构进入既有保护分支。

## 与 07:38 生产日志的匹配程度

已知日志描述为 07:38 fail-open、三个 `codex` 角色段、中间一段为空。前轮直接构造此三段形状，在最后一段为唯一合格报告且无后续角色时，12/12 正常清洗；这符合 `_prefix()` 条件。新矩阵的形状 5 同时具有三个 `codex` 段和中间空段，并触发目标异常，**但额外引入了报告后的 `user` 角色段**。是否存在这个尾段，日志摘要未证明；形状 1、2、4 虽触发同类异常，也未同时匹配“三个 `codex` 段、中间为空”的已知结构。形状 3 的异常类别不同，形状 6 无异常。

因此匹配程度仅为**条件性、部分匹配**：目标异常可由额外尾角色或多合格候选解释，但没有生产原始字节证明哪一种结构实际出现。也缺少空段在原始事件/合并文本中的表示、报告的起止字节、代码围栏状态，以及期望保留/删除的明确段边界契约。不能从 warning 名称或合成矩阵反推生产报告应该被清洗。

## 下一步：转入证据采集设计

选择**转入证据采集设计**，暂不申请修复授权。采集 07:38 事件的脱敏原始字节或可重放的结构化事件：完整 header 后三个 `codex` 角色行及其前后字节、空段的编码和行尾、所有后续 `user`/`codex`/工具段、报告标题和四节边界、用量分隔与重复块；同时给出经批准的预期输出字节范围。若上游结构化事件有角色/空段元数据而合并文本丢失该信息，应明确传递契约。取得这些证据后，才能判断是否存在真正的“应安全清洗却 fail-open”红灯，并决定是否申请最小修复。

尽管本轮已在合成形状中重现 `ambiguous_transcript_roles`，生产原始字节与段边界契约仍缺失；**不得据此放宽 `_prefix()`**。本报告不构成修复授权或修复完成证明。
