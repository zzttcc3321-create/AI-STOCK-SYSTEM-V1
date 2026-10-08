# #014-R2 Windows durability 返修总结

TASK_ID=AI-STOCK-V1-014-R2-REWORK  
BASELINE=041c78b550440003f0c8799873114c7c336cc718  
ORIGINAL_PR_HEAD=d4be4c57dfd53b6d04a8c84c22fb1bdffcec6fad  
BRANCH=codex/014-r2-windows-durability  
PR=[#36](https://github.com/zzttcc3321-create/AI-STOCK-SYSTEM-V1/pull/36)

## 授权与修复对照

2026-10-08 任务卡正式确认 `APPROVE_CONTRACT_REVISION`，并批准 B02 扩大至只读投影层。按 [DEVIATION_PROCESS](../governance/DEVIATION_PROCESS.md) 处理类型 3 冻结合同修订；本次沿用已批准合同，不再次修改冻结文档。任务卡路径为 `/home/ubuntu/e2e_014r2_rework_taskcard.md`。

| Review 项 | 本次修复 | 验证 |
| --- | --- | --- |
| B01 | `tools/e2e_014_r1.py` 仅对 `docs/frozen/EVIDENCE_ARCHIVE_SIDECAR_V1.0.md` 设置文件级白名单；保留其他冻结文件、migration、migration.py、schema_v1_1.py 保护断言。E0 单独列出批准例外。 | 3 个保护断言测试 PASS；包括相似文件名、其他冻结文档、migration/schema 与批准例外一起出现时仍拒绝。 |
| B02 | 仅修改 `persistence/read_models.py` 只读查询结果：Evidence 返回 `archive_durability_levels` 与最弱 `durability_level`；分析上下文增加实际 binding 等级，分析/字段追踪投影转交最弱等级。 | 新增 5 个耐久投影测试和 2 个分析绑定测试；归档集成模块共 20 个测试 PASS。 |
| D01 | [Evidence Identity](../EVIDENCE_IDENTITY_V1_1_014_R1.md) 明确缺失 durability 的旧归档按 UNVERIFIED 拒绝；只有显式合法耐久等级的 sidecar 才适用旧身份字段兼容检查。 | raw、normalized、canonical 阶段缺失/UNVERIFIED/非法等级均拒绝；无历史推断或回填。 |
| D02 | 补齐本文件，修复 [#012B 实施说明](../EVIDENCE_ARCHIVE_012B.md) 的现有总结链接。 | 本文件纳入同一返修分支。 |

## 只读结果语义

`archive_durability_levels` 只列当前查询已验证的相关阶段：`raw`、`normalized`、`canonical_evidence`；`for_analysis()` 对 used Evidence 追加 `analysis_binding`。每个值读取各自 sealed sidecar，而非复用 raw 的等级。`evidence()` 的 `durability_level` 是这些阶段中的最弱等级；`for_analysis()` 的整体等级覆盖全部 input Evidence 和 used binding。未配置 Reader、旧格式引用、缺失或损坏阶段保持 `UNVERIFIED`。

`integrity_status=VERIFIED` 表示内容/Hash/上下文校验成功，`REPORT_FULL_TRACE` 表示内容及分析绑定验证成功；两者都不承诺目录级掉电耐久。完整性 VERIFIED 与 FILE_SYNC_ONLY 可以同时成立。Windows 默认 FILE_SYNC_ONLY，Linux 默认 DIRECTORY_SYNC；显式文件级合同允许，失败不自动切换合同。

只读测试启用 SQLite `PRAGMA query_only=ON`，检查 `total_changes` 不变，并逐字比较读取前后的归档文件。无 DDL、migration 或数据库写入逻辑修改。

## 实际验证与交付状态

| 验证 | 实际结果 |
| --- | --- |
| `REQUIRE_STREAMLIT=1 PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover -s tests -v` | **787/787 PASS，0 FAIL，0 ERROR，0 SKIP，377.159s，退出码 0**；在主工作区执行。原 777 项 + 本次新增 10 项。 |
| 耐久投影新增模块 | 5/5 PASS；每个阶段单独降低等级、无 Reader/旧引用、缺失/非法/UNVERIFIED 都覆盖。 |
| Runtime closure 保护测试 | 3/3 PASS；精确文件例外与其他保护路径继续拒绝。 |
| 归档集成模块 | 20/20 PASS（含本次新增 2 项分析 binding 测试），43.422s。 |
| `REQUIRE_STREAMLIT=1 PYTHONDONTWRITEBYTECODE=1 python3 tools/e2e_014_r1.py` | **本地 PASS，退出码 0**；在 `/tmp/014r2-rework-checkout` 的同名分支干净检出执行，源码与主工作区 151 个文件逐字一致。 |
| Runtime closure E0 / UI | `PASS_DEV_SCOPE` / `PASS_DEV_APPTEST`；`worktree_clean=true`；唯一批准例外为指定冻结合同文件；脚本 dev-scope 结论为 `READY_FOR_REVIEW`。 |
| 语法与差异 | 修改 Python 文件 AST parse PASS；`git diff --check` PASS；D02 链接目标存在。 |
| 保护与范围检查 | 对 d4be4c5：冻结文档、migration、migration.py、schema_v1_1.py、repository.py、archive_writer.py 无返修变更；对 main 基线：只有已批准冻结合同修订。 |

运行环境：dev-box Linux，Python 3.10.12，Streamlit 1.50.0。日志与 E2E JSON 在 `/tmp/014r2-rework-delivery/`，未纳入 Git。

远端原始 head 的 Runtime closure（run [37739627755](https://github.com/zzttcc3321-create/AI-STOCK-SYSTEM-V1/actions/runs/37739627755)）为 FAILURE，Governance CI 为 SUCCESS。实际读取该 run 的 job/日志，原 777 项测试通过，失败位于 `tools/e2e_014_r1.py:35` 的 `assert not protected`，与 B01 根因一致。返修后的本地验证通过，**远端 Runtime closure CI 尚未重跑，不能声明 CI PASS**。

交付阻因与可复用结果：

- 主工作区 `.git` 只读，`git add`/`git commit` 不能创建 `index.lock`（`Read-only file system`）。主工作区源文件已修复，但 HEAD 仍为 d4be4c5。
- `/tmp/014r2-rework-checkout` 使用现有 `codex/014-r2-windows-durability` 分支的本地检出，已产生实现增量提交 `fedcbaad5456a02877039032a7677c552679776b`，之后追加本总结结果提交。没有创建新分支名称，没有改变主工作区 origin。
- CLI 无法解析 github.com/API 连接失败；GitHub 连接器可读 PR，但 create_tree/create_commit/update_ref 写工具被审批策略 `never` 拒绝（`MCP tool call requires approval, but approval policy is never`）。远端 head 仍为 d4be4c5，无法上传返修产生新 CI。
- 同分支增量提交的 bundle、补丁、保留原文并追加 B01/B02/D01/D02 对照的完整 PR 正文草稿，以及校验哈希位于 `/tmp/014r2-rework-delivery/`。远端分支、PR 正文和 Runtime closure CI 更新仍未完成；需可写 Git/网络环境接续交付。

CONCLUSION=BLOCKED_WITH_ROOT_CAUSE（返修代码、本地全量回归和本地 Runtime closure 已通过；原工作区 Git 提交与远端 CI/PR 交付受权限及网络限制）。

验证限于 dev-box Linux 的人工构造 bytes、mock 与临时目录/内存 SQLite；真实 Windows E1/E2/E7 未执行。本次不新增分支、不修改 origin、不 push main、不合并 PR、不接触真实数据库、持仓或运行凭据。
