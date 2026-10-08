# #012B Evidence Archive 实施说明

实现依据：[子级冻结合同](frozen/EVIDENCE_ARCHIVE_SIDECAR_V1.0.md)，父级 `EVIDENCE_PIPELINE_DESIGN_V1.0` 未修改。

```
TASK_NUMBER=#012B
TASK_ID=AI-STOCK-V1-EVIDENCE-ARCHIVE-012B
BASELINE_MAIN=00ecb1518daa9d7bf63b53712ace9cb2b076af7f
BASELINE_TESTS=584/584
REAL_PROVIDER_EVIDENCE_WRITE_DEFAULT=DISABLED
SOURCE_COUNT=3
FOURTH_SOURCE=NO
DB_SCHEMA_CHANGE=NO
MIGRATION_CHANGE=NO
REAL_WINDOWS_DB_WRITE=0
AWS_DB_WRITE=0
MODEL_CALL=0
TRADING_WRITE=0
BACKGROUND_DAEMON=NO
OLD_ARCHIVE_INTERPRETATION=NO
OLD_REFERENCE_BACKFILL=NO
```

## 实现与调用顺序

`ArchiveIdentity` 定义 canonical URI；`ArchiveResolver(root)` 只解析该 URI；`ArchiveWriter(root)` 同步原子提交并回读验证；`ArchiveReader(root)` 只读复算所有相关 Hash。归档根目录由调用方显式提供，不自动选生产目录。

三源通过 `archive_writer=` 注入共享 Writer。Tushare transport 先保留真实 entity bytes，再解析 JSON，成功时三个 part 一起归档，解析/HTTP/中途错误时只保存实际收到的 response。TDX local 使用 transport 原始 data；旧 `tdx-local/...` 存于 `source_reference`。TDX information 的 `_raw()` 私有文件实现已移除，保留 R6 echo 检查后交共享 Writer。信息源的缓存/协议/业务处理规则不变。未配置 Writer 的既有 live/test 调用不建立可验证的 archive 提交，repository 默认拒绝真实证据 DB 写入。

未来新证据的接入顺序：

1. 给 Provider 配置 `ArchiveWriter(root)`，采集时提供 run/collection/request context；使用返回的 archive record/URI。
2. 现有 Normalizer 完成后调用 `append_normalized(uri, normalized_data=..., normalization_rule=..., validation_result=...)`，使用返回的 hash 构建新阶段的 immutable EvidenceRecord；raw record/sidecar 不变。
3. 既有 analysis/人工确认流程生成 FinalAnalysisEvidence 后，调用 `append_analysis_binding(uri, binding=final.evidence.to_dict())`，为每条 used Evidence 追加绑定。
4. `SQLitePersistenceRepository(db, archive_reader=ArchiveReader(root), enable_real_provider_evidence=True)` 在既有授权的测试/运行流程中验证归档再持久化。参数缺省为 False；本任务只验证 SQLite 内存库，不执行该真实运行配置。
5. 给 `EvidenceReadRepository` / `AnalysisReadRepository` 注入 `archive_reader=`，读取动态验证状态与 `REPORT_FULL_TRACE`。

Raw Hash 与 normalized Hash 分别重算；分析 Hash 从既有 DB 原始模型输出重算，不调用模型。DB calculation 补齐标准化规则和 validation result，并保存 normalized hash 对应具体不可变 sidecar；不改变 Normalizer 规则。

失败时可以存在未提交 orphan，不能把它当作可用证据，也不能在同身份下覆盖修复。现有损坏归档需要人工诊断并用新的证据身份重新采集。quota 没有自动清理；锁/失败目录没有后台回收。

## 验证

执行 `python3 -m unittest discover -s tests -q`。新增五个测试模块覆盖合同、Writer/Reader、三源、持久化集成，所有 raw 均为人工构造/mock bytes，SQLite 使用 `:memory:`，文件只在临时目录。

| 验收项 | 验证方式 |
| --- | --- |
| bit-exact / raw+stored+bundle SHA-256 | 二进制、非 UTF-8、空、单/多 part 往返；改 raw/stored bytes 后拒绝；独立复算 bundle |
| canonical JSON / duplicate / NaN | 规范 bytes、嵌套 duplicate keys、NaN/Infinity、非 canonical sidecar 拒绝 |
| 请求字段 / TDX key echo | 递归脱敏；普通、JSON、Unicode echo 拒绝且不创建 raw |
| fail-closed | raw/sidecar/partial write、rename、fsync、append sync、quota、transaction 中途归档损坏 |
| 一致性与 append-only | missing raw→corrupt；missing marker→orphan；同 ID 幂等/conflict；normalized/binding 禁止覆盖 |
| 路径与资源 | URI/身份/来源上下文不匹配、symlink、重复文件、gzip bomb、raw/part 数/配额上限 |
| gate 与完整追踪 | 默认关闭、缺 Reader/缺 binding/缺 raw、synthetic 绕过拒绝；DB/report/output/binding 篡改不宣称 full trace |
| 历史与 schema | Resolver 拒绝旧格式；旧引用 read-model 不调用 Reader/不回填；测试库持久化前后 sqlite_master 一致 |

三个既有测试文件有必要的合同断言维护：`test_tdx_information.py` 把直接物理路径读取改为 ArchiveReader；`test_persistence_read_models.py` 校验新增 calculation.validation_result；`test_persistence_schema_v1_1.py` 把 #012 的历史范围检查固定到 #012A 基线，仍逐字校验冻结 schema 与原 migration 集合。业务状态、缓存、三源范围及历史冻结断言保持。

历史 #012B 验证使用 Linux 目录 fsync 合同。2026-10-08 的 #014-R2 已获批修订此假设：Windows 默认文件级耐久并显式记录 `FILE_SYNC_ONLY`；Linux 默认 `DIRECTORY_SYNC`，也可显式选择文件级合同。缺失／`UNVERIFIED` 等级不能被认定为已归档。详见[修订合同](frozen/EVIDENCE_ARCHIVE_SIDECAR_V1.0.md)及 [#014-R2 总结](evidence/014_R2_SUMMARY.md)。Windows 实机 E2 仍由 qi 验证；本修订不授予真实库 apply 权限。

最终验证（2026-10-04）：

```
BASELINE_RESULT=584/584_PASS
IMPLEMENTATION_RESULT=659/659_PASS
NEW_TEST_COUNT=75
FULL_SUITE_COMMAND=PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover -s tests -q
FULL_SUITE_DURATION_SECONDS=93.270
GIT_DIFF_CHECK=PASS
PARENT_FROZEN_BYTES_UNCHANGED=PASS
DB_SCHEMA_V1_0_BYTES_UNCHANGED=PASS
DB_SCHEMA_V1_1_BYTES_UNCHANGED=PASS
SCHEMA_PY_BYTES_UNCHANGED=PASS
MIGRATION_BYTES_UNCHANGED=PASS
```

对以上五个冻结/schema/migration 文件逐字与 `BASELINE_MAIN` 比较通过。测试包括 syscall 已产生效果后返回错误的 rename/link 场景，均撤销本次提交 marker，不暴露可用证据。最终工作区只包含上述实现、文档和必要测试维护，未提交运行归档、测试数据库、凭据或缓存文件。
