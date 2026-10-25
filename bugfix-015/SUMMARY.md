# 015 快照与币种缺陷修复

```text
ACTUAL_MODEL_NAME=UNAVAILABLE_IN_SESSION_METADATA
ACTUAL_REASONING_EFFORT=UNAVAILABLE_IN_SESSION_METADATA
MODEL_DISCLOSURE=会话仅标明基于 GPT-6 的 Codex，未暴露实际部署模型标识与推理档位；相关环境变量亦未设置，不以默认配置冒充实际值。
DATE=2026-10-22 UTC
WORKTREE=/home/ubuntu/wt/BUGFIX-015
BRANCH=fix/015-snapshot-currency
START_HEAD=207f7e1
STATUS=FIX_APPLIED_FULL_TESTS_PASS_RECOVERY_DELIVERY_CURRENT_WORKTREE_REF_BLOCKED
```

本次按用户授权实施诊断中的两项修复。快照修复针对的是“合成样本已复现的候选缺陷”，与原始审计文本的一致性尚待复核。

所有验证限定于合成数据；不连接真实事实库、不执行 APPLY_REAL_DB，不改 schema、不迁移旧表、不改生产配置。不 push、不创建 PR、不合并，发布等待 qi 批准。

## 根因与修复设计

1. **快照候选缺陷**：015-06 找到同 batch_id 后，仅检查历史快照内部的行数、成本与总额，自洽即返回 `IDEMPOTENT_NOOP`，未与当前事实投影和元数据业务日期比较。持仓或证券名称已变、旧批次日期被错误标注时，旧快照仍能被误判为当前有效输出。
   修复保留 `holding_config_meta` 的有效业务日期、受支持格式版本 `1.0`、来源 SHA256 作为批次定位证据，在已有批次判断前构造并验证当前事实投影，再核对已有 overview 的业务日期、行数、总额及全部投影字段。不一致明确失败，不覆盖历史批次。格式版本不是快照修订序号；不使用文件 mtime，也不把 hash 字典序解释为时间顺序。
2. **币种缺陷**：015-04 原先只检查 currency 为非空文本；015-05 不检查所引用主数据的币种，015-06 JOIN 不读取币种，却将总额固定标注为 CNY。异常值因此能污染事实表并进入人民币汇总。
   修复将当前支持集合明确为 `{CNY}`，在 CSV 准备、主数据事实写入，以及 05/06 引用入口检查。非法值整批拒绝；CSV 真正空字段保留既有默认 CNY 的约定，存量事实字段为空则拒绝。大小写和空白变体不自动归一化。

币种集合依据：`tools/phase_c_contracts.py` 限定 SSE/SZSE/BSE 市场，主数据默认 CNY，overview 写入与验证固定 CNY；`docs/evidence/015_PHASE_C_SCHEMA_BINDING.md` 的精度契约明确单位为 yuan，未定义换汇；现有正向 fixture 为 CNY 或空字段默认 CNY，USD 仅为冲突样本。USD/HKD 虽是其他业务场景的货币，也不属于本管线当前支持集合。

## 改动文件与逐处理由

| 文件/位置 | 改动与理由 |
| --- | --- |
| `tools/phase_c_contracts.py` 模块常量及 `_currency` | 显式定义 `{CNY}`；新增 `CurrencyValidationError`，固定错误码 `INVALID_CURRENCY`，另附无原始值的可追溯坐标。 |
| 同文件 `prepare` | CSV 逐条保留逻辑记录序号，默认处理后校验币种；非法第二行也拒绝整批，不过滤后继续。 |
| 同文件 `_security` | CREATE/INSERT 前再次校验全部 prepared 行，防止直接调用 `execute` 绕过 CSV 校验。 |
| 同文件 `_holding` | 同时读取引用证券的状态与币种，在创建/替换持仓前拒绝存量非法主数据，幂等路径同样检查。 |
| 同文件 `_dashboard` JOIN/投影 | 读取币种，先验证全部当前事实并形成投影，再创建目标表或判断已有批次；已有快照不能绕过当前币种检查。 |
| 同文件 `_dashboard` 已有批次路径 | 保留内部一致性验证，并与当前业务日期、行数、总额和逐行投影比较；分别报告 `DASHBOARD_EXISTING_METADATA_MISMATCH` / `DASHBOARD_EXISTING_PROJECTION_MISMATCH`，保持合法重复调用的 NOOP 和历史追加语义。 |
| `tools/phase_c_safety.py` `main` 失败报告 | 复用现有单行 JSON FAIL 输出，仅对币种错误增加 `error_context`，含来源 hash、作用域、记录序号和输入库 hash；不输出非法值、证券标识、持仓或来源路径，不输出成功交接 hash。 |
| `tests/test_015_snapshot_candidate_regression.py` | 8 项内存测试覆盖旧批次与当前事实/日期冲突、空批次日期冲突、合法幂等、按业务日期追加、同日来源追加及版本门禁；1 项真实 CLI 临时库测试覆盖 dry-run/常规调用均拒绝旧快照且文件不变。 |
| `tests/test_015_currency_entry_regression.py` | 9 项内存/模拟文件测试覆盖非法集合、prepared 绕过、05/06 存量非法币种、旧快照绕过、空字段边界、合法三步管线与脱敏报告；2 项真实 CLI 临时库测试覆盖 04/05/06 错误追溯、整批拒绝及文件不变。 |
| `bugfix-015/SUMMARY.md` | 保存本次环境披露、根因、逐处理由、诊断一致性、实际前后测试结果及遗留风险。 |

错误记录示例（占位 hash，无真实数据）：

```json
{"step":"015-04","result":"FAIL","error":"INVALID_CURRENCY","error_context":{"field":"currency","record_scope":"SECURITY_SOURCE","record_number":2,"source_sha256":"<64位来源SHA256>","database_sha256":"<64位输入库SHA256>"}}
```

记录序号从 1 开始。`SECURITY_SOURCE` 对应原始 CSV 逻辑记录（不含表头）；`SECURITY_PREPARED`、`HOLDING_PREPARED` 和 `DASHBOARD_PROJECTION` 对应按 market/symbol 排序后的记录。后两者的来源 hash 为持仓来源，结合输入库 hash 可定位被引用的主数据。错误交由原有事务边界处理，不静默丢弃。

## 与诊断的一致性

已读取 `/tmp/bugfix-015/DIAGNOSIS.md` 与 `/tmp/bugfix-015/SUMMARY.md`，将其中币种入口缺失与“已有批次仅内部自洽即 NOOP”的候选根因逐处对照本 worktree。上一轮 `~/work/taskchain/bugfix2.log` 的压缩稿只先解码到 `/tmp` 审阅；两个源文件 SHA256 与稿件基线一致，并逐项核对了 12 处拟修改内容。新增 CLI 磁盘测试补充了稿件的内存覆盖。

**快照修复针对的是“合成样本已复现的候选缺陷”，与原始审计文本的一致性尚待复核。** 本仓库未找到相应原始审计，未声称已完成该核验。本次依据用户最新授权实施，不沿用上轮缺少写权限的停止状态。

## 测试结果

| 验证 | 修改前 | 修改后 |
| --- | --- | --- |
| 全量测试 | **1,177 tests，582.264s，OK**，0 failures / 0 errors / 0 skipped | **1,197 tests，581.496s，OK**，0 failures / 0 errors / 0 skipped |
| 015（全量日志中的实际通过项） | **129 PASS** | **149 PASS**，含新增 20 项 |
| 新增回归测试 | **20 tests；14 项缺陷测试失败，24 处失败断言（含子用例）；6 项通过，0 errors** | **20 tests，0.761s，OK** |

全量基线在实现文件未修改时运行完毕后才应用修复；该次 discovery 发生在新增测试落盘前，日志确认不包含新测试。新增同一份测试的失败均为原逻辑放过非法币种、错误接受旧快照或缺少错误追溯信息，不是导入/权限等测试设施错误。8 项快照内存测试中 4 项缺陷测试失败，另有 1 项 CLI 快照测试失败；9 项币种内存/模拟文件测试中 7 项缺陷测试失败，另有 2 项 CLI 币种测试失败。

命令（从 worktree 根目录执行）：

```sh
# 修改前、修改后均执行全量；先完成前者才改实现。
env -u APPLY_REAL_DB -u REAL_DB_WRITE PYTHONDONTWRITEBYTECODE=1 TMPDIR=/tmp \
  python3 -B -m unittest discover -s tests -v
# 同一份新增测试分别验证修复前失败、修复后通过。
env -u APPLY_REAL_DB -u REAL_DB_WRITE PYTHONDONTWRITEBYTECODE=1 TMPDIR=/tmp \
  python3 -B -m unittest discover -s tests -p 'test_015_*regression.py' -v
```

日志保存在 `/tmp/bugfix-015/current-baseline-full.log`、`current-regression-before-final.log`、`current-regression-after.log`、`current-after-full.log`。015 前后数量取全量日志的实际通过测试项，不引用上轮诊断的历史数字。

新增测试仅使用 SQLite `:memory:`、`/tmp` 合成 SQLite/CSV，CLI 磁盘测试调用真实入口、文件门禁和预演；失败时断言库字节、schema、全部 fixture 文件不变，且无 `POST_WRITE_SHA256`。内存事务测试使用真实 `GuardedConnection`、`execute_checked` 和 schema/完整性检查。全量中的既有真实库授权门禁测试也只模拟授权条件并操作临时 fixture，不连接真实库、不执行真实 APPLY_REAL_DB。

静态校验：两个修改后源文件 SHA256 均与逐处审阅的候选稿一致；以 AST 对照 HEAD 验证 DDL、CREATE pin、READ/WRITE 白名单、AUTH 常量完全不变；`git diff --check` 通过。没有改动既有测试以迎合修复。

## 遗留风险与范围

- 候选快照问题与原始审计的对应关系仍待阿七复核；本次没有扩大到其他 Dashboard 模块。
- `version=1.0` 是格式版本，同日不同 source hash 没有修订顺序证据；维持追加，不据 hash 大小判新旧，其他模块的排序未改。
- 现有 batch_id 仅保存来源 hash 前 12 位，快照 schema 没有完整来源 hash。日期和内容比较不能额外区分“前缀相同且投影相同、完整 hash 不同”的来源；本次不改 schema。
- 若数据本身的日期、版本和来源标注错误且内容一致，管线无法凭现有证据恢复真实时间；不使用文件时间猜测。冲突批次明确拒绝，需运行方核对来源，不自动覆盖历史。
- 非法币种错误沿用 CLI stdout JSON 记录机制，运行方需保存报告。记录序号不是多行 CSV 的物理行号。
- 不清洗存量真实数据、不迁移旧表（包括三位小数旧表），不修改生产配置、冻结 DDL、CREATE hash pin 或 SQL 权限白名单。

## Git 交付

已尝试在当前分支暂存新增文档和测试，`git add` 返回：

```text
fatal: Unable to create '/home/ubuntu/work/AI-STOCK-SYSTEM-V1/.git/worktrees/BUGFIX-015/index.lock': Read-only file system
```

全量验证完成后再次尝试暂存全部五个文件，仍收到同一错误。worktree 内容可写，但 `.git` 文件引用的共享 Git 元数据目录在工作区外，仍处于只读权限范围。当前环境不能修改该目录或提升权限；**原 worktree 的分支仍停在 `207f7e1`，没有完成暂存或提交**，不能冒充已在此处提交。

为保存可导入的完整提交，使用 `/tmp/bugfix-015/commit-recovery` 独立副本的同名分支 `fix/015-snapshot-currency` 作为恢复交付。它以相同的 `207f7e1` 为父提交，仅包含与 worktree 逐字节一致的上述五个文件，不修改共享仓库的任何分支。交付产物：

- `/tmp/bugfix-015/fix-015-snapshot-currency.bundle`：从该基线起的单个提交，可导入已有基线的仓库。
- `/tmp/bugfix-015/fix-015-snapshot-currency.patch`：同一提交的文本补丁，便于审阅。
- `/tmp/bugfix-015/GIT_DELIVERY.json`：记录恢复提交完整 SHA、bundle SHA256、父提交、文件 hash，以及原 worktree 分支未更新的状态；不在本摘要内嵌本提交自身 SHA。
- `/tmp/bugfix-015/TEST_RESULTS.json`：实际测试数量、用时、前后结果及完整日志 hash。

最终交付前核对恢复提交的每个文件内容与本 worktree 相同，并验证 bundle 的基线前提。要更新原 worktree 的分支，需要运行环境允许写入整个共享 Git 元数据目录 `/home/ubuntu/work/AI-STOCK-SYSTEM-V1/.git`（含 worktree index、objects 和 refs），只放开工作目录不足以完成提交。

未 push、未创建 PR、未合并；发布仍等待 qi 批准。
