# 015 Runtime Initialization — Phase A / Windows Runbook

Phase A 只进行工程准备和离线合成测试，不连接 qi Windows 真实库，不产生真实库写入。
015 可产生有限运行写入，但不是无限制真实库写权限；015 内任何真实库写入必须逐项授权。
本工具的 REAL_DB_WRITE=false、APPLY_REAL_DB="NOT_AUTHORIZED" 固定；不执行数据库写入。
015-04 / 015-05 / 015-06 **需 qi 单独授权，默认不执行**。授权标志只是调用门禁，不能替代 qi 的逐项批准。

## 能力映射与复用

| 步骤 | 现有能力 | Phase A 最小补充 |
| --- | --- | --- |
| 015-01 | 部分缺口：E7 临时目录/独占文件模式 | 外部运行目录，已有文件保留，HEAD 检查 |
| 015-02 | 已有可复用：persistence.connection.open_database(readonly=True)、migration.schema_state/schema_fingerprint/legacy_digest/schema_inventory/checks | 与显式 014-E7 基线比较，不硬编码真实表数 |
| 015-03 | 完全缺失：没有这三个运行开关的统一门禁 | JSON 三项严格为 boolean false |
| 015-04 | 部分缺口：persistence.backup.file_sha256 | 授权后检查显式文件和元数据，不导入、不下载 |
| 015-05 | 部分缺口：已有分析持仓契约，不对应本任务四字段 | 显式参数校验，不保存或回显持仓 |
| 015-06 | 部分缺口：dashboard.app/runtime/factory；factory 使用可写连接，不能直接复用 | 复用只读数据库检查，仅生成无脚本、无外链的静态展示 |

已检查 tools/e7_pre_apply_gate.py、tests/test_e7_pre_apply_gate.py、dashboard、providers.interface 和 evidence.archive_reader。
不实例化 provider、DashboardRuntime、ArchiveWriter 或模型执行器；不运行动态 runtime factory。
.github/workflows/runtime-014-r1.yml 的 pull_request.branches 过滤目标分支，main 已被包含，不改工作流。

## 运行前

协调员在 Step 2 派工时填写 **合并后的 main HEAD**；本 PR 不提供或猜测该 HEAD。
以下命令的路径和元数据由协调员在 Git 外填写。不要将真实库路径、持仓、凭据或这些本地输入提交到 Git。
PowerShell 下使用已安装的 Python；每条命令非零退出或 result=FAIL 即 BLOCKED，禁止继续。

```powershell
$ExpectedHead = Read-Host '协调员填写合并后的 main HEAD（必填）'
if ($ExpectedHead -notmatch '^[a-f0-9]{40}$') { throw 'BLOCKED: invalid HEAD' }
$Base = Read-Host '仓库外运行目录（不得为仓库目录）'
$Db = Read-Host 'qi 批准的只读数据库选择器（Phase A 不填写真实库）'
$Baseline = Read-Host 'Git 外的 014-E7 核对基线 JSON'
$Config = Read-Host 'Git 外 Runtime 配置 JSON'
function Invoke-015 {
    & python tools/015_runtime_init.py @args
    if ($LASTEXITCODE -ne 0) { throw 'BLOCKED: 保留现场，不自动修复' }
}
```

基线 JSON 必须包含从实际 014-E7 证据人工核对取得的 SCHEMA_AFTER、LEGACY_DIGEST_AFTER（schema 与 row_count_digest 对象）、TABLE_COUNT_AFTER。
E7 输出的 SCHEMA_AFTER、LEGACY_DIGEST 和 e7-02 TOTAL_TABLE_COUNT 分别对应以上字段；不得生成一个当前快照冒充获批基线。
Runtime 配置 JSON：`{"AUTO_TRADING":false,"SCHEDULER":false,"AUTO_REFRESH":false}`。

## 默认步骤

```powershell
Invoke-015 015-01 --base-dir $Base --expected-head $ExpectedHead
Invoke-015 015-02 --db-path $Db --baseline $Baseline
Invoke-015 015-03 --config $Config
```

015-01 预期 result=PASS，CREATED_DIRECTORIES 只列新建目录；runtime/cache/logs/reports 存在，已有重要文件不覆盖。
默认 base-dir 为 OS 临时目录下 ai-stock-015-runtime，禁止写仓库内目录。
015-02 预期 SCHEMA_STATE=B，SCHEMA_FINGERPRINT/LEGACY_DIGEST/TOTAL_TABLE_COUNT 分别等于基线三项；CHECKS 全通过；mode=ro。
015-03 预期 PASS，DISABLED_FEATURES 包含 AUTO_TRADING、SCHEDULER、AUTO_REFRESH。

也可顺序执行：

```powershell
Invoke-015 all --base-dir $Base --expected-head $ExpectedHead --db-path $Db --baseline $Baseline --config $Config
```

预期 015-01～03 PASS，015-04～06 各为 SKIPPED_PENDING_AUTHORIZATION（不是 PASS 或 FAIL）；汇总 PENDING_AUTHORIZATION。
任一失败立即停止，后续步骤不执行。

## 单独授权步骤（默认不执行）

### 015-04 — 需 qi 单独授权，默认不执行

以下只验证文件；不导入数据库，不自动生成证券主数据，不下载任何数据。
metadata JSON 必须含 sha256/source/updated_at/version，与 qi 独立确认的参数完全一致；updated_at 为带时区 ISO 时间且不在未来。

```powershell
$Master = Read-Host '获批证券主数据文件'
$Meta = Read-Host '对应元数据 JSON'
$Hash = Read-Host 'qi 核对的 SHA256'
$Source = Read-Host 'qi 核对的来源'
$Updated = Read-Host 'qi 核对的更新时间'
$Version = Read-Host 'qi 核对的版本'
Invoke-015 015-04 --security-master-import=yes --security-file $Master --security-metadata $Meta --security-sha256 $Hash --security-source $Source --security-updated-at $Updated --security-version $Version
```

预期 PASS、SECURITY_MASTER_STATUS=VALIDATED_ONLY。无授权返回 SECURITY_MASTER_IMPORT_NOT_AUTHORIZED；任何不匹配 FAIL。

### 015-05 — 需 qi 单独授权，默认不执行

只验证显式 stock_code/shares/cost/date；人工逐项核对，股数为正整数、成本有限非负、日期合法且不在未来。
禁止从旧文件猜测、行情反推、同步券商。本工具不保存、不输出持仓；不要把值写入脚本、日志或文档。
真实持仓检查由 qi 在批准的本地环境操作；Phase A 只使用合成测试。PowerShell 不开启 transcript。

```powershell
$Code = Read-Host '人工核对 stock_code'
$Shares = Read-Host '人工核对 shares'
$Cost = Read-Host '人工核对 cost'
$Date = Read-Host '人工核对 date，YYYY-MM-DD'
Invoke-015 015-05 --holding-config-write=yes --stock-code $Code --shares $Shares --cost $Cost --date $Date
Remove-Variable Code,Shares,Cost,Date
```

预期 PASS、HOLDING_CONFIG_STATUS=VALIDATED_ONLY。无授权返回 HOLDING_CONFIG_WRITE_NOT_AUTHORIZED。
本步骤不写配置文件，更不写真实库；任何后续持仓落盘另行逐项授权。

### 015-06 — 需 qi 单独授权，默认不执行

无标志只读检查数据库/配置后返回 DASHBOARD_DATA_WRITE_NOT_AUTHORIZED（检查失败则返回该检查错误）；不写文件、不启动服务。
授权写入仅 reports/dashboard.html；文件存在则 FAIL，不覆盖。

```powershell
Invoke-015 015-06 --base-dir $Base --db-path $Db --baseline $Baseline --config $Config --dashboard-data-write=yes
Start-Process (Join-Path $Base 'reports/dashboard.html')
```

预期 PASS、PAGE_OPENABLE=true、DATA_READ=PASS、SCHEDULER_STARTED=false、NETWORK_CALLS=0。
人工确认页面可打开、显示 Data read: PASS 和实际读出的表数、无后台任务启动，才完成 Dashboard 验收。
CLI 的 PAGE_OPENABLE 表示本地 HTML 已写入且读取核对成功，不能替代人工浏览器验收。
不启动 Streamlit/调度，不联网、不调 LLM、不发通知、不刷新缓存。

## 失败停止与验收

以下任一发生即 **BLOCKED、保留现场、不自动修复**：

1. SCHEMA_CHANGED
2. LEGACY_DIGEST_CHANGED
3. UNEXPECTED_WRITE
4. NETWORK_CALL_DETECTED
5. SCHEDULER_STARTED
6. LLM_CALL_DETECTED
7. UNKNOWN_FILE_CREATED

前两项由 CLI 的基线门禁检查；其余由协调员对执行环境/文件清单/运行状态独立复核，不能将 CLI 的固定安全声明当作全系统监测器。
任何其他 FAIL 同样 BLOCKED。授权不是自动修复权限。

执行前后记录以下验收项（Git 外保存脱敏证据）：

- 数据库：SCHEMA_AFTER=SCHEMA_FINGERPRINT、LEGACY_DIGEST_AFTER=LEGACY_DIGEST、TABLE_COUNT_AFTER=TOTAL_TABLE_COUNT，三项必须等于 014-E7。
- 文件系统：CREATED_FILES、CREATED_DIRECTORIES、FILE_HASHES；只允许四个运行目录和单独授权的静态展示。对执行前已有重要文件做 hash 比较；发现未知文件或变更即 BLOCKED。
- Runtime：RUNTIME_INITIALIZATION_STATUS、ENABLED_FEATURES、DISABLED_FEATURES；默认 all 为 PENDING_AUTHORIZATION；跳过项保持待授权，不能宣称整体初始化完成。
- 安全：REAL_DB_WRITE_SCOPE=<approved items only>；本工具 scope=[]，NETWORK_CALLS=0、SCHEDULER_STARTED=false、LLM_CALLS=0。Phase A 无真实库写入。

## 红线自查

只新增 CLI、测试、本文档；migration/schema/核心业务逻辑文件变更数为 0。
开发机仅合成临时库离线测试，无真实库连接/写入，无真实持仓进入 Git 或文件。
写库开关固定 REAL_DB_WRITE=false、APPLY_REAL_DB="NOT_AUTHORIZED"；015 真实库写入必须逐项授权。
PR 不合并，等待 Review B 与 qi 批准。
