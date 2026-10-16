# CODE_REVIEW_PACKAGE_A — M01/M02/C01

- TASK_ID: M01/M02/C01
- BASELINE: main `27d47b01`
- BRANCH: `fix/m01-m02-c01`
- SCOPE: 离线代码修复与合成数据回归；本记录提供审查事实，不替代独立审核或生产验证。

## CHANGE_SCOPE / CODE_DIFF_SUMMARY

| 文件 | 变更与行为 |
| --- | --- |
| `persistence/connection.py` | `open_database` 增加 `check_same_thread` 参数，默认保持 `True`；原写门禁和事务逻辑不变。 |
| `dashboard/factory.py`、`dashboard/runtime.py` | 工厂连接显式允许跨线程；Runtime 用同一 `RLock` 串行化完整读取、生成、确认和归档操作，覆盖授权检查、事务提交/回滚及内存状态更新。 |
| `tests/test_dashboard_runtime.py`、`tests/test_dashboard_streamlit.py` | 使用临时文件数据库与真实生产工厂；覆盖跨线程完整流程、写事务期间阻止交错读取、门禁拒绝后释放锁，以及 AppTest 经工厂创建会话再重跑。 |
| `analysis/output_validator.py` | 截止时间过滤后引用空持仓快照时显式抛出 `AnalysisOutputError`；Runner 原有边界返回 `OUTPUT_INVALID`，不生成 draft/evidence。 |
| `tests/test_analysis_snapshot_cutoff.py` | 合成快照覆盖实际 builder 过滤、校验器类型化错误、Runner 受控失败，以及截止前、截止相等、时区等价边界。 |
| `tools/015_runtime_init.py`、`tests/test_015_runtime_init.py` | 在路径解析前检查原始祖先，检查四个运行子目录与报告输出目录；通过 `lstat` 的目录类型和 reparse 属性拒绝 symlink/junction，检查异常或 Windows 属性缺失受控返回 `DIRECTORY_UNSAFE/BLOCKED`。 |

## C01 确认依据

原代码仅检查 `is_symlink()`，Windows junction 的目录类型仍可通过检查。`015-06` 随后在 `reports` 下创建文件，因此跳转目录能改变实际写入目标。使用既有 `marked_reparse_point` 合成测试工具，修复前复现初始化目录与报告写入未被拒绝；修复后在任何目录创建或报告文件写入前拒绝该路径。

检测方式与既有 archive resolver 的 `lstat` / `FILE_ATTRIBUTE_REPARSE_POINT` 模式一致，支持 Python 3.10，不依赖 `Path.is_junction()`。本次运行环境为 Linux，使用合成 Windows 属性覆盖；未执行原生 Windows junction 验证。

## STATIC_ANALYSIS_RESULT

- B01 的 `_require_real_db_write`、`is_temporary_database`、`transaction` 与基线 AST 一致；保留每次写入检查、未配置路径时 fail-closed，以及运行写入与迁移的独立授权。
- B06 所在 `tools/e2_windows_verify.py` 与 `tests/test_e2_windows_verify.py` 与基线逐字节一致。
- 无数据库 schema、迁移、冻结文档或 Provider 变更。

## TEST_RESULT

- `python3 -m pytest tests/test_dashboard_runtime.py tests/test_dashboard_streamlit.py tests/test_real_db_write_gate.py -q`：41 passed、129 subtests passed，无跳过，256.79 秒；包含要求的 B01 回归。
- M02 相关回归：174 passed、228 subtests passed。
- `python3 -m pytest tests/test_015_runtime_init.py -q`：21 passed、7 subtests passed。
- 最终全量测试结果在提交前填写。

## 既有 CI 限制

Runtime CI 另运行 `tools/e2e_014_r1.py`。其保护检查固定对比 `5a86655`；在本任务基线 `27d47b01` 上单独调用 `verify_protected_paths('5a86655')` 已返回 `AssertionError: Protected paths changed: persistence/migration.py`。差异是既有 B01 提交 `a143d56` 将迁移事务改为 `_migration=True`，用于授权分离。本任务未改该文件，也未放宽旧 E2E 保护检查；此基线问题独立于 pytest 测试结果。

## IMPACT_ANALYSIS / RISK_FOUND

- Runtime 锁作用域包含模型调用，重叠操作按同一会话顺序执行；生产 UI 的数据库访问均通过 Runtime 方法。直接使用裸连接的其他调用者仍须自行保证串行化，默认连接继续保留线程检查。
- M02 保留 builder 的 `INCOMPLETE` 和空快照的 `UNVERIFIED` 语义，不扩大 Runner 的异常捕获列表，不改变截止过滤规则。
- C01 加固初始化路径的检查，不提供防御并发文件系统替换的原子沙箱；原生 Windows 验证仍有待独立环境执行。
- 所有数据库与快照测试使用临时目录或内存中的合成数据，无真实数据库访问、外部数据源或真实模型调用。
