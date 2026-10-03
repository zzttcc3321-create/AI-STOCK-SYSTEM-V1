# #011 DB Schema V1.1 最小解冻决策记录

日期：2026-10-03。依据：任务卡 #011 末尾 RESUME；qi 已批准，GPT D22 已定义范围。

```text
#011_SCHEMA_UNFREEZE_DECISION=APPROVED
QI_DECISION=OPTION_A
DB_SCHEMA_TARGET=V1.1
UNFREEZE_SCOPE=DB_SCHEMA_ONLY
MIGRATION_POLICY=ADDITIVE_ONLY
STATUS=RESUME_AUTHORIZED
REAL_DB_CONNECTION=FORBIDDEN
REAL_DB_WRITE=NO
DDL=NO
DML=NO
MIGRATION_APPLY=NO
```

## 为何解冻、G0 发现了什么

真实 Windows SQLite 导出 inventory 有 32 表，analysis_tasks / analysis_reports / evidence_records
全部缺失。analysis_history 仅 PARTIAL_EQUIVALENT，无完整七字段、确认日志、revision、supersedes。
ConfirmationEvent、HumanOverride、FinalAnalysisEvidence 和不可变快照没有获批承载位置，
原 #011 因必须新增 schema 且未获授权而 STOP_AND_REPORT / BLOCKED。
完整证据与原始阻塞报告保留于 [PERSISTENCE_READINESS_011](../PERSISTENCE_READINESS_011.md)。

## qi 为何选 A

RESUME 明确记录 qi 选择 OPTION A：解冻 DB Schema，在最小范围内增补 #010 承载，未选纯 sidecar。
技术依据是 G0 缺口需要显式身份、结构化历史、revision/supersedes 和同 SQLite 事务边界；
单独增加 sidecar 不会使缺失的冻结核心表出现，也不会自动保证 DB 与外部文件原子提交。
此处记录获批选择及其技术依据，不额外推测 qi 的其他动机或扩大授权。

## 精确七表与旧表保护

| 新表 | 承载对象 |
| --- | --- |
| analysis_tasks | #009 运行、ValidatedAnalysisDraft、AnalysisRunEvidence 及 hash 重算输入 |
| analysis_reports | 七字段最终报告与 AnalysisConclusion.id / calculation / revision |
| evidence_records | 三源 Provider Evidence，完整 metadata 与 evidence revision |
| analysis_confirmation_events | 有序 append-only ConfirmationEvent |
| analysis_human_overrides | HumanOverride 的原值 hash、replacement、理由、引用 |
| analysis_snapshots | PREPARED / FINAL 两类完整不可变载荷 |
| analysis_final_evidence | analysis-level FinalAnalysisEvidence，独立 supersedes 链 |

AnalysisConclusion 与最终 report revision 一对一，映射为报告与其绑定的逻辑读取，不需要第八表。
新字段、纯测试 DDL 与全部边界见 [DB_SCHEMA_V1.1](../frozen/DB_SCHEMA_V1.1.md)。
INSERT_ONLY 的五表严格禁止 UPDATE/DELETE；正式报告同样不可变。
唯一可 UPDATE 的字段是 analysis_tasks.status。纠错追加 revision 和显式 supersedes，不覆盖旧事实。

analysis_history 不修改，因为现有 INTEGER PK/旧 FK/历史字段与 #010 字符串身份和七字段语义
不能证明等价；强行替换或回填会改变成熟旧入口。其角色冻结为 LEGACY_READ_ONLY_HISTORY，
旧 Dashboard 查询须标 record_origin=LEGACY_HISTORY。其余旧 31 表同样零改动。

HUMAN 代表人工决定/override.origin，不是外部采集事实源。EvidenceSource 仍仅
TUSHARE_MCP / TDX_OFFICIAL_LOCAL_HTTP / WORKBUDDY_TDX_MCP；FinalAnalysisEvidence 是分析级
最终绑定，绝不作为第四源或塞入 Provider EvidenceRecord。

## 文档升级与 #011 / #012 边界

DB_SCHEMA_V1.0 原文完全不动；新增 V1.1，VERSION=V1.1，STATUS=FROZEN_FOR_IMPLEMENTATION，
SUPERSEDES=DB_SCHEMA_V1.0，CHANGE_REASON=PERSISTENCE_G0_BLOCKED_UNFREEZE。
G0 BLOCKED 历史证据保留，恢复状态 RESUME_AUTHORIZED；契约与测试通过后才标 READY。

#011 只交付本决策、冻结候选、真实 inventory 文档投影、映射、审计更新和 schema_v1_1.py。
该模块仅导出 :memory: 测试 DDL，不连接任何 DB，无 migration SQL、repository、domain 修改。
新增验收测试保存于已授权审计文档代码块，避免增加 RESUME 范围之外的文件。

#012 负责正式 migration/*.sql、真实 DB 的 WAL-safe backup、受控迁移、repository、事务原子性、
read-model；顺序为 V1.1 schema → WAL-safe backup → controlled migration → repository implementation
→ transaction atomicity → read-model。前置 PERSISTENCE_READINESS=READY；本轮仍完全禁止真实 DB
connection/write/DDL/DML/migration apply，也不另建事实库、不改 domain、旧表或旧数据。
