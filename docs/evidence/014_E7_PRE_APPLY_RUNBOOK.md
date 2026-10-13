# #014-E7 Pre-apply 只读验收运行手册（Step 2 / Windows）

TASK_ID: AI-STOCK-V1-014-E7-PRE-APPLY-RUNTIME-GATE

`REAL_DB_WRITE=NO`；`APPLY_REAL_DB=NOT_AUTHORIZED`。
本手册只供 qi 在 Windows 本机执行。dev-box 不访问、不连接、不打开真实库。
任一命令 exit 非零、任一项 `FAIL` 或最终 `E7_STATUS=BLOCKED`：
**E7=BLOCKED，立即停止，不得继续下一步，不得进入真实写入。**
失败报告保留作检查；处理原因后使用新的输出目录从 E7-01 重新执行，不覆盖旧证据。

## 1. 前置条件与 PowerShell 公共设置

- checkout 协调员指定版本；安装仓库依赖；Python 3.10 或以上可用。
- 关闭可能持有数据库的程序，包括旧 dashboard、采集任务和数据库查看器。
  执行期间暂停对源库的外部写入，避免指纹基线变化。
- 确认真实库存在、路径正确，输出目录有足够空间存放备份与临时副本。
- 工作区必须 clean（包括未跟踪文件），不得残留 `probe_*`、`*probe*`、`e2_*_tmp*`。
- 报告和工作目录放在仓库外。输出目录由 qi 指定，建议同源库所在盘的新目录；
  不使用源库文件作为任何输出目标。工具不覆盖已有报告或备份。
- `--expected-head` 由协调员在 Step 2 派工时提供：E7 PR 合并后的 main 完整 HEAD。
  若尚未合并，基线为 `48ce73e`（完整值 `48ce73ef881035ea9f026d0d029ccf31cd8f85d0`）；
  该基线本身没有新工具，需要协调员明确安排工具可用的验收版本，不能在缺少 CLI 的
  checkout 上执行，也不能跳过版本锁定。比较使用完整 SHA；不要只传七位缩写。
  `--expected-branch` 同样按协调员指定（合并后通常为 `main`）。
  两项可选参数省略时只记录版本、不校验；正式派工应填写，不能自行用当前 HEAD 替代期望值。

以下使用 **PowerShell**，修改仓库位置、期望版本和输出目录后粘贴：

```powershell
$ErrorActionPreference = 'Stop'
Set-Location 'D:\AI-STOCK-SYSTEM-V1'
python --version
if ($LASTEXITCODE -ne 0) { throw 'Python unavailable; E7=BLOCKED' }

# 仅供 qi 本机粘贴使用；CLI 没有默认数据库路径。
$Db = 'D:\股票项目测试\DSA-ZhuLinsen-Phase2\data\stock_analysis.db'
$Out = 'D:\e7-pre-apply-20261013-01'  # qi 指定的全新目录，不能在仓库内
$Work = Join-Path $Out 'work'
$ExpectedHead = '<协调员提供的完整 HEAD SHA>'
$ExpectedBranch = 'main'              # 按协调员派工填写
if (-not (Test-Path -LiteralPath $Db -PathType Leaf)) { throw 'DB missing; E7=BLOCKED' }
if (Test-Path -LiteralPath $Out) { throw 'Use a new output directory; E7=BLOCKED' }

$Common = @('--db-path', $Db, '--output-dir', $Out)
function Invoke-E7 {
    param([string[]]$GateArgs)
    & python tools/e7_pre_apply_gate.py @GateArgs
    if ($LASTEXITCODE -ne 0) {
        throw 'E7=BLOCKED: stop now; do not run the next step or write to the real DB'
    }
}
```

每次工具成功 exit 0，打印一份 JSON 并保存报告。失败 exit 1，打印
`{"result":"FAIL","error_code":"明确错误码"}`；若目录可写且目标不存在也保存失败报告。
参数错误只打印安全 JSON。下列示例中的哈希占位符表示实际 64 位十六进制摘要。

## 2. E7-01 工作区与版本锁定

```powershell
Invoke-E7 -GateArgs (@('e7-01') + $Common + @('--expected-head', $ExpectedHead, '--expected-branch', $ExpectedBranch))
```

`e7-01.json` 预期：`result=PASS`、`GIT_HEAD` 等于指定完整 SHA、
`GIT_BRANCH` 等于指定分支、`GIT_STATUS=""`、`TEMP_PROBE_COUNT=0`、
`GIT_DIFF_CHECK=PASS`。显式扫描也检查被 gitignore 忽略的 probe 文件/目录。
版本不符、脏状态、probe 残留或 diff check 失败：立即停手，由协调员处理。

## 3. E7-02 真实库只读预检查

```powershell
Invoke-E7 -GateArgs (@('e7-02') + $Common)
```

只用 `open_database(..., readonly=True)` / SQLite `mode=ro` 打开 `$Db`。
`e7-02.json` 预期：`result=PASS`、`SCHEMA_STATE=B`、`TOTAL_TABLE_COUNT=39`、
`LEGACY_TABLE_COUNT=32`、`V1_1_TABLE_COUNT=7`、`SCHEMA_FINGERPRINT=<64位哈希>`、
`JOURNAL_MODE=wal` 或实际模式；`CHECKS` 包含 `quick_check=ok`、
`integrity_check=ok`、`foreign_key_check=PASS`。
A 态、C 态漂移、表数或完整性不符：立即停止，不尝试修复真实库或施加 migration。

## 4. E7-03 指纹确认与记录

```powershell
Invoke-E7 -GateArgs (@('e7-03') + $Common)
```

`e7-03.json` 预期：`result=PASS`、`SCHEMA_STATE=B`，记录
`SCHEMA_FINGERPRINT`、`LEGACY_SCHEMA_FINGERPRINT`、`LEGACY_ROW_COUNT_DIGEST`、
`LEGACY_DIGEST` 和 `JOURNAL_MODE`。只读执行 `preflight(require_legacy_32=True)`。
本步骤创建本次基线，不声称已与外部历史基线比对。
E7-04 使用同一输出目录的此报告；E7-07 使用该指纹与恢复后指纹比较。
缺少或损坏该报告、预检查失败：立即停止。

## 5. E7-04 WAL 安全备份演练

```powershell
Invoke-E7 -GateArgs (@('e7-04') + $Common)
$Bk = Get-Content -LiteralPath (Join-Path $Out 'e7-04.json') -Raw | ConvertFrom-Json
$BackupPath = [string]$Bk.BACKUP_PATH
$BackupSha256 = [string]$Bk.BACKUP_SHA256
```

复用 SQLite online backup（`wal_safe_backup`），不对源库做字节拷贝回退。
备份位于 `$Out`，名为 `stock_analysis_pre_apply_e7_<timestamp>.db`，
UTC timestamp 格式 `YYYYMMDDTHHMMSSZ`。与源路径相同或目标已存在即拒绝。
例如 `stock_analysis_pre_apply_e7_20261013T120000Z.db`。

`e7-04.json` 预期：`result=PASS`、`BACKUP_PATH` 指向新文件、
`BACKUP_SHA256=<64位哈希>`、`BACKUP_SIZE>0`、`BACKUP_FINGERPRINT_MATCH=true`，
`CHECKS` 全通过。备份前后核对源基线，备份 legacy 摘要也必须一致。
以上 PowerShell 变量直接从成功报告读取路径与 SHA256，供下一步使用。
哈希、指纹、完整性或有界超时失败：立即停止；失败产物不得视为有效备份。

## 6. E7-05 Migration rollback 演练（仅副本）

```powershell
Invoke-E7 -GateArgs (@('e7-05') + $Common + @('--backup-path', $BackupPath, '--backup-sha256', $BackupSha256, '--work-dir', $Work))
```

先验证 SHA256，再将备份字节拷贝到 `$Work` 内新建的独占临时目录。
apply/checks 只在该副本执行，**不打开、不修改真实源库**；备份只读。
回滚语义是删除工具自己的副本，再从备份恢复一份全新副本，
不执行 DROP TABLE、DROP INDEX 或反向 DDL。恢复后检查完整性、原状态、
schema 指纹及 legacy 摘要；验证备份前后 SHA256 不变。成功后清理临时副本目录。

实际 Step 2 的备份已经为 B 态，预期 `MIGRATION_RESULT=ALREADY_APPLIED`，
不重复施加 DDL。离线 A 态测试会走 `APPLIED`，恢复后的状态回到 A；
这仅用于测试，不允许拿真实库做该路径实验。

`e7-05.json` 预期：`result=PASS`、`ROLLBACK_RESULT=PASS`、
`MIGRATION_RESULT=ALREADY_APPLIED`、`SCHEMA_STATE=B`、
`LEGACY_DIGEST_UNCHANGED=true`、`BACKUP_HASH_UNCHANGED=true`，恢复后
`SCHEMA_FINGERPRINT` 等于 E7-03。哈希不符、apply 或恢复失败：立即停止。

## 7. E7-06 Runtime Smoke Test（离线只读）

```powershell
Invoke-E7 -GateArgs (@('e7-06') + $Common)
```

只导入 providers、dashboard.runtime、dashboard.factory、evidence.archive_reader，
确认 DashboardRuntime 类及 ProviderStatus 可达。
不调用 `create_runtime()`、不实例化 DashboardRuntime 或 Provider、不打开 `$Db`、
不启动 Streamlit、不调度任务、不联网、不调 LLM、不发送通知、不刷新缓存。
在 OS 临时目录创建合成 archive，再用 ArchiveReader 只读验证，比较读取前后文件哈希。
不会访问真实 archive；合成 archive 的内容与内部标识不会进入报告。

`e7-06.json` 预期：`result=PASS`，上述模块、类及 `TEMP_ARCHIVE_READ` 为 `PASS`；
`PROVIDER_CONNECTIONS=NOT_AVAILABLE`（此项有意不连接）；
`DATABASE_OPENED=false`、`NETWORK_CALLS=0`、`SERVICES_STARTED=0`。
任何检查 `FAIL`：立即停止。`NOT_AVAILABLE` 只表示本步骤明确跳过的 Provider 连接。

## 8. E7-07 Migration Audit

```powershell
$Audit = Join-Path $Out 'e7-audit.json'
Invoke-E7 -GateArgs (@('e7-07') + $Common + @('--input-dir', $Out, '--output', $Audit))
```

也可用六次 `--input <步骤报告路径>` 替代 `--input-dir`，顺序不限，
每步恰好一份，不允许同时传两种输入。`--output` 必须在 `$Out` 内且尚不存在。

最终 JSON 写入 `$Audit` 并打印。必需字段：`TASK_ID`、`GIT_HEAD`、
`DATABASE_PATH`、`BACKUP_PATH`、`BACKUP_SHA256`、`SCHEMA_BEFORE`、
`SCHEMA_AFTER`、`LEGACY_DIGEST`、`ROLLBACK_RESULT`、`REAL_DB_WRITE`、
`APPLY_REAL_DB`、`E7_STATUS`、`STEPS`（各步结果摘要）。

仅 01~06 全部 PASS、必需字段齐全、数据库路径/备份哈希/legacy 摘要一致、
烟测边界有效且 `SCHEMA_BEFORE`（E7-03）等于 `SCHEMA_AFTER`（E7-05 恢复后）
时才输出 `E7_STATUS=PASS` / exit 0。失败、缺失、损坏、重复或不一致的输入
输出 `result=FAIL`、`E7_STATUS=BLOCKED` / exit 1，不拼凑缺失证据。
审计只验证输入报告的一致性，不宣称已证明不存在外部程序的写入。

**审计中的 `REAL_DB_WRITE` 永远为 `false`，`APPLY_REAL_DB` 永远为
`"NOT_AUTHORIZED"`。E7 通过也不自动执行真实写入；必须 qi 亲口说
`APPLY_REAL_DB=YES` 才允许后续步骤，后续步骤不在本任务卡内。**

## 9. 可选顺序入口与交付证据

qi 也可在全新输出目录执行一次顺序入口（替代上面逐步执行，不能在已产生报告的目录重跑）：

```powershell
Invoke-E7 -GateArgs (@('all') + $Common + @('--expected-head', $ExpectedHead, '--expected-branch', $ExpectedBranch, '--work-dir', $Work))
```

`all` 严格 01→02→03→04→05→06→07，任一步失败立即停止。
自动把 E7-04 的备份路径和 SHA256 传给 E7-05；默认工作目录为 `$Out\work`；
最终审计默认保存为 `$Out\e7-07.json`。标准输出按顺序每步一行 JSON。

交付协调员：六份步骤报告、最终审计报告及备份 SHA256/大小摘要。
备份文件留在 qi 本机妥善保存，不上传真实库内容；不附数据库行数据、凭证或会话标识。
工具开发验收只使用 32 张合成 legacy 表及 tempfile 副本，不是重建真实 legacy schema。
