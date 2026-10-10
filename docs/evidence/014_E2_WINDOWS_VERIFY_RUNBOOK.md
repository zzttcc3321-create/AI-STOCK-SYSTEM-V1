# #014-E2 Windows 验证运行手册

TASK_ID: AI-STOCK-V1-014-E2-WINDOWS-VERIFY-HARNESS-R1
BASELINE: b7fb0e166a7186397db559698d9a573be4774b20

> LEGACY_DRIVER_SOURCE_LOST：原始 `windows_collect.py` 已无法取得。
> 本脚本为独立重建，不冒充旧版本，不复用旧运行记录。

## 1. 前置条件

- Windows 实机，仓库位于 `D:\AI-STOCK-SYSTEM-V1`，HEAD 为 `b7fb0e1`
- Python 可用，`pip` 依赖已按仓库要求安装
- 通达信本地 TQ HTTP 服务监听 `127.0.0.1:17709`（Phase B 需要）
- `TDX_MCP_API_KEY` 已配置为进程环境变量（Phase C 需要；缺失时标记
`BLOCKED_AUTH`，不执行真实业务请求）

## 2. 运行命令

```powershell
# 每次运行必须指定一个的新目录
python tools/e2_windows_verify.py --output-dir "D:\e2-verify-<run-id>" --mode all
```

`--mode` 取值：

| mode | 执行阶段 |
|---------|-----------------------|
| offline | 仅 Phase A（离线合同检查，无网络） |
| quote | Phase B（真实 TDX 报价链路） |
| info | Phase C（真实资讯链路） |
| all | A + B + C |

可选参数：`--stock-id`（默认 `601518.SH`），
`--date-from` / `--date-to`（`YYYYMMDD`，Phase C 公告/新闻区间）。

## 3. 安全边界

- 只读调用冻结数据源；禁止真实 SQLite 连接/写入、Migration、
`APPLY_REAL_DB=YES`、网络 Push、自动交易、云端部署。
- 所有输出写入 `--output-dir` 指定的新目录；目录已存在时脚本拒绝执行。
- 报告（`verify_report.json`）仅含状态、证据引用、SHA-256、受限原因
和脱敏错误码；不含凭据、原始行情正文、会话标识。

## 4. 阶段说明与结果定义

### Phase A — 离线合同检查
使用合成数据走 `make_envelope` → `materialize` 全链路，验证：
信封构建、归档写入、Raw 回读、数值字段检查、Hash 校验、
空单位声明（`{}`）不产生 VERIFIED Canonical、
`AUTH_FAILED` / `NOT_AVAILABLE` / `VALIDATION_FAILED` 三者区分。

### Phase B — Windows TDX 真实链路
`TdxLocalHttpProvider.fetch()` → `make_envelope(units={})` →
`materialize()`。验证真实快照数值适配（H-R2）、Raw SHA-256、
归档回读。单位声明为空，已知未验证的价格/盘口量/金额
不得进入 VERIFIED Canonical（预期受限）。

### Phase C — H-I1 信息链路
先执行 `health_check()` 控制面探针。非 `AVAILABLE` 时标记
`BLOCKED_AUTH` 并跳过业务调用。恢复后测试公告/新闻/
财务三视图的明确空通知（`NOT_AVAILABLE`）与未知散文
（`VALIDATION_FAILED`）分类。

## 5. 证据清单

每次运行在输出目录生成：

- `verify_report.json` — 汇总报告（Git HEAD、工作区状态、各阶段结果）
- `phase_a/...` — 离线验证的 `raw.json` / `evidence.json`
- `phase_b/...` — 真实报价的 `raw.json` / `evidence.json`
- `phase_c/...` — 资讯链路证据（如执行）

## 6. 验收标准（摘要）

1. 受测 Git HEAD 与工作区状态明确记录
2. 原始响应可完整回读，Hash 重算一致
3. 未验证单位不得产生伪 VERIFIED Canonical
4. 三种失败状态必须区分，不得 conflation
5. 归档失败时明确报告 FAIL
6. 所有写入限定在全新的隔离目录
7. 无真实数据库写入、无凭据落盘、无外部 Push
