# #011 Persistence Readiness：RESUME_AUTHORIZED

## RESUME 当前执行状态（2026-10-03）

```text
EXECUTION_STATUS=RESUME_AUTHORIZED
PERSISTENCE_READINESS=READY
READINESS_SCOPE=V1.1_MAPPING_AND_SCHEMA_CONTRACT
G0_PERSISTENCE_READINESS=BLOCKED
SCHEMA_UNFREEZE=APPROVED_BY_QI
#011_SCHEMA_UNFREEZE_DECISION=APPROVED
QI_DECISION=OPTION_A
DB_SCHEMA_TARGET=V1.1
UNFREEZE_SCOPE=DB_SCHEMA_ONLY
REAL_DB_CONNECTION=FORBIDDEN
REAL_DB_WRITE=NO
DDL=NO
DML=NO
MIGRATION_APPLY=NO
```

qi 已选 A，GPT D22 限定七表增补；本轮只执行任务卡 RESUME 第 1–6 项。
[冻结 V1.1](frozen/DB_SCHEMA_V1.1.md)、[决策记录](decisions/DB_SCHEMA_V1_1_PERSISTENCE_UNFREEZE_20261003.md)
与 [恢复映射/完整 schema inventory 投影](DB_COMPATIBILITY_MAPPING_011.md)构成本次契约。
下方原 G0 报告全文保留为历史证据；其 BLOCKED/未批准/未开始实施等描述指解冻前状态。
原 G0 对 legacy schema 信息不足的判断仍成立，V1.1 测试不重建或伪造旧 32 表。
后续 #012 的 repository/正式 migration/真实库检查尚未实施；本轮的 READY 仅针对已授权的映射契约。
最终测试结果及可复跑验收代码置于末尾。inventory 本身不提交，schema 元数据投影已纳入映射文档。

## G0 原始阻塞审计（历史保留）

日期：2026-10-03。审计基线：`eaa928a2ad99121154563e8ecc660841beee5fcb`。

```text
PERSISTENCE_READINESS=BLOCKED
STOP_AND_REPORT=YES
STOP_REASON=REQUIRED_SCHEMA_MIGRATION_NOT_AUTHORIZED_IN_011
PERSISTENCE_MAPPING_GAP=YES
REAL_DB_WRITE=0
PRODUCTION_DB_WRITE=0
AWS_DB_WRITE=0
DDL=0
DML=0
MIGRATION_APPLY=0
DASHBOARD=NO
IMPLEMENTATION_STARTED=NO
```

本轮在 G0 确认必须新增/扩展事实库 schema 后停止实施，触发任务卡
STOP_AND_REPORT 条件 4。这两份文档是只读审计与待决要求，**不是已冻结、可直接编码的
Persistence Contract**；没有创建 persistence 模块、测试 schema、新增测试或 migration SQL。
逐字段结论见 [兼容映射报告](DB_COMPATIBILITY_MAPPING_011.md)。

## G0 输入及核验

唯一实际 schema 输入：`docs/freeze/DB_EXISTING_SCHEMA_INVENTORY_V1.json`，
118662 字节，SHA-256：
`c9eaf6d1bc8927c8c0dbdb6dd0404ddc52751b2bcefbbad1c5b66a406fbadbd2`。
该文件为任务卡批准的 Windows 事实库导出元数据，未跟踪、仅只读，不纳入交付或提交。
未连接 Windows、AWS 或本地事实数据库；未读取业务行，也未输出 row_count、账户值、
持仓数量、成本或其他业务记录。

| 核验 | 结果及证据边界 |
| --- | --- |
| 表数量 | 声明 table_count=32，实际 32 个独立表名 |
| 表级元数据 | 每表都有 name / row_count / columns / primary_key / indexes / foreign_keys；不导出 row_count |
| 列定义 | 每列包含 name / type / not_null / pk_position；列名无重复 |
| 主键 | columns.pk_position 的顺序与每表 primary_key 完全一致 |
| 索引 | 共 226 个；均有 name / unique / columns，引用列均存在 |
| 外键 | 共 11 条；from / table / to 均完整，源列、目标表与目标列均存在 |
| foreign_key_violations | inventory 记录为 0；没有在本轮重新运行真实库检查 |
| journal_mode | inventory 记录为 wal |
| sqlite_version / user_version | inventory 记录为 3.49.1 / 0；不能从 user_version=0 推断未做过历史迁移 |
| quick_check | inventory 记录为 ok |
| 完整 integrity_check | inventory 无独立 integrity_check 字段；quick_check=ok 不冒称完整 integrity_check 或本轮现场复核 |
| PRAGMA foreign_keys | inventory 未提供连接级外键启用状态；不能由违规数为 0 推断启用 |
| 三个关键物理表 | analysis_tasks / analysis_reports / evidence_records 均不存在 |
| 支撑实体 | users / stocks / collection_tasks / system_runs 也未发现同名表；不将旧业务表自动视为等价 |

冻结 `docs/frozen/DB_SCHEMA_V1.0.md` 第 2.12–2.14 节将任务/证据表列为缺失，
将 analysis_history 列为报告候选，允许后续兼容迁移。
因此本次发现是 **MISSING / PARTIAL_EQUIVALENT**，未发现需要判为 CONFLICT 的实证；
不能把冻结的逻辑实体列表解释为真实库已有同名物理表。

## 阻塞及待 qi 决策

1. 真实库没有冻结三实体的完整承载位置。analysis_history 只有旧历史分析字段，
   无七字段结构、task_id、human_confirm、确认日志、revision 或 supersedes 字段。
   不能无迁移实现要求的原子 finalization。
2. 冻结逻辑 schema 也没有 #010 ConfirmationEvent、HumanOverride、
   FinalAnalysisEvidence、完整快照/修订元数据和有序 evidence relations 的明确位置。
   通用 TEXT 或 JSON 列的存在不能证明兼容，不能借 reasoning、raw_result、
   context_snapshot 或 decision_signals.evidence_json 隐藏这些对象。
3. inventory 没有原始 CREATE TABLE / CREATE INDEX / trigger SQL、列默认值、
   CHECK/UNIQUE 表约束细节、索引表达式/条件/排序/排序规则、外键动作等元数据。
   足以证明上述缺失；不足以忠实重建实际 schema 做完整约束 round-trip/rollback 测试。
   不按猜测补齐，也不生成理想 schema。

```text
REQUIRES_QI_DECISION=
  A. 解冻/修订 DB Schema，批准承载 #010 对象的最小字段/实体及兼容迁移范围；或
  B. 批准专门的 sidecar persistence contract，包括身份、修订、历史与原子提交协议。
```

A/B 均由 qi 决定，本轮没有选定、设计或创建新表。
现有冻结实体缺失的问题仍需受控兼容迁移授权；**B 单独不能使三张缺失物理表出现，
也不能让文件写入自动获得 SQLite 事务原子性**。
解阻还需提供只含 schema 的实际 DDL/约束快照；无需任何业务行。
若要证明外键启用或完整 integrity_check，需补充相应只读检查结果。

## 后续合同必须满足的事务与历史约束（未实施）

未来原子单元必须是：

```text
BEGIN
  persist analysis task（已存在的同身份任务只校验/关联；不可覆盖）
  persist final report revision
  persist confirmation artifacts（完整有序事件、override）
  persist evidence links / final evidence metadata / approved sidecar bindings
COMMIT
任何步骤失败（含 COMMIT 失败）→ ROLLBACK；读模型看不到半成品
```

在同一个 SQLite transaction/connection 中校验身份、历史链接、引用证据和 hash 绑定。
相同 identity + 相同不可变内容的重试必须有明确幂等约定；同 identity + 不同内容必须拒绝。
task 与模型运行可以被多个报告 revision 共用；不得在每次 revision 静默替换 task。
需要额外批准的身份映射见兼容报告，当前不宣称 run_id 等于某个物理 PK。

```text
INSERT_ONLY_OBJECTS=confirmation_events / human_overrides / final revisions / evidence history
EVIDENCE_UPDATE=FORBIDDEN
EVIDENCE_DELETE=FORBIDDEN
CONFIRMATION_EVENT_UPDATE=FORBIDDEN
CONFIRMATION_EVENT_DELETE=FORBIDDEN
FINAL_SNAPSHOT_UPDATE=FORBIDDEN
OLD_CONCLUSION_UPDATE=FORBIDDEN
CORRECTION=new revision + explicit supersedes link
```

HumanConfirm 仅 PENDING / CONFIRMED / REJECTED；MODIFIED 是字段决定，不是 HumanConfirm。
纠错追加驳回事件，原 final snapshot / conclusion / override 不变。
新 revision 必须保留不同 report_id / revision_id / final_evidence_id 与两类 supersedes
链接；关联必须可验证，拒绝自引用、循环和无目标链接。并发分支处理规则需在合同中明确，
当前不能用时间戳或 ID 字典序擅自选择分支。

snapshot 中 human_confirm=CONFIRMED 是生成时的历史事实；当前认可状态必须由后续
append-only 事件推导。冻结 analysis_reports.human_confirm 单列没有说明它是历史事实
还是可变 projection。无追加日志承载时不能覆盖此列代替历史。
只有明确批准 projection 语义及可重建日志后，才可讨论更新非历史 projection；
本轮不批准 UPDATE final report。

若 B 被批准，必须定义文件预写/验证/发布、提交绑定和恢复协议，或选择同一原子
存储边界。文件写失败应阻止 DB COMMIT；事务回滚后的未引用文件不能被读模型暴露。
还要解决 DB COMMIT 后文件缺失/损坏与崩溃恢复。普通 BEGIN/COMMIT 不涵盖外部文件；
本轮未选协议、路径、manifest 或新表。

## Serialization 后续要求及既有 hash 算法

以下由 #007–#010 源码核对，作为后续实现必须保持的边界；没有声明已通过 DB round-trip：

| 值 | 规范 |
| --- | --- |
| 结构化字段、replacement、calculation、确认/快照元数据 | JSON object；有序事件/ID 集合的编码为 JSON array，顺序须保留 |
| HumanConfirm / EvidenceSource / 状态 / 决定 | TEXT enum，显式白名单验证；HUMAN 仅 origin，不是 EvidenceSource |
| ID | 保留原字符串，禁止转 INTEGER、改大小写、截断或重新生成 |
| timestamp | 有时区 ISO8601 TEXT，保留原字符串表示及 offset，不做隐式 SQLite DATETIME 转换 |
| hash | 64 位小写 SHA-256 hex；不能存 Python repr 或修改 hash 输入 |

canonical JSON 必须匹配 `analysis.contracts.stable_json`：UTF-8、ensure_ascii=False、
sort_keys=True、separators=(",", ":")、allow_nan=False。
保留 bool/int/float 区分、数值表示、null、Unicode 原文及数组顺序；不做 Unicode、
timestamp 或数字归一化改变已有 hash。拒绝 NaN / Infinity（含解码后溢出非有限值）、
重复解码键（包括转义后相同键）、非字符串键；不得通过 str(key) 或别名重命名
产生/吞掉语义重复键。

| hash | 已有重算输入 |
| --- | --- |
| model_output_hash | 原模型响应文本的原样 UTF-8 bytes；不能用 parse 后再序列化的 JSON 替代 |
| validated_output_hash | #009 draft.output 的 canonical JSON；FinalSnapshot.original_validated_output_hash 对应同一值 |
| final_snapshot_hash | FinalAnalysisSnapshot.to_dict() 去掉 final_snapshot_hash 的 canonical JSON |
| confirmation_log_hash | 快照生成时完整有序 confirmation_events 的 canonical JSON；后续事件不改旧快照日志/hash |
| input_hash | #007 PromptEnvelope rendered input（剔除 input_hash 及计数字段后）的 canonical JSON，遵循 output_validator.prompt_inputs |
| prepared_snapshot_hash | PreparedFinalSnapshot.to_dict() 去掉 prepared_snapshot_hash 的 canonical JSON |

重算 model_output_hash / input_hash 需要原响应/原 input envelope 的可靠归档位置。
只有 hash 字符串不够；冻结表没有 run provenance 的完整存储映射。
`_gate_digest` 是进程内 Gate 标记，不作存储认证凭证，不在反序列化时自行生成并
宣称上游 Gate 已通过。后续 read DTO 必须与可继续操作的 sealed domain 对象明确分开。

## 后续只读 repository 边界（要求，尚未实现）

| Repository | 应提供的查询及返回信息 |
| --- | --- |
| AnalysisReadRepository | latest_confirmed_analysis(stock_id)、current_effective_revision(report_id)、analysis_history(stock_id)：返回稳定身份、revision/supersedes、七字段原值/有效值/决定、生成时与当前状态、模型与 hash、rejected/superseded 历史；链异常不得宣称有效 |
| EvidenceReadRepository | evidence(evidence_id)、field_evidence_trace(report_id, field)：区分 input / model-used / human-reference IDs；返回三源、时间、原响应引用、calculation、hash 校验状态与缺失原因；拒绝把人工来源当 Provider |
| ConfirmationReadRepository | pending_confirmations(stock_id)、confirmation_history(report_id)、current_confirmation_state(report_id)：返回必要项、actor/reason、完整有序事件、override 与当前派生状态，含事后纠错；无日志不能推导已确认 |

未来 Dashboard 仅消费上述 DTO，不依赖 ConfirmationFlow tuple、`_gate_digest`、
SQLite 列名/类型或 sidecar 文件布局。pending 清单还依赖 #009 待审项/初始 review items
的保存或可验证重建机制，当前无承载，不得用 human_confirm 单列代替。
“latest”必须结合显式 revision 链和当前确认/证据有效性；被驳回的新 revision
不能使已被纠错驳回的旧 revision 自动恢复为有效。

## 后续 migration plan 的前置顺序（无 SQL）

1. qi 决定 A/B、run/task 与证券/用户身份映射、human_confirm 语义和分支规则。
2. 补齐只含 schema 的真实 DDL/约束快照及必要只读检查元数据。
3. 审批最小兼容迁移与数据归档/恢复合同，保留旧表、历史查询和外键；冻结版本同步修订
   由获授权任务承担。本轮不修改 docs/frozen/*。
4. 后续获授权任务先做可验证一致性备份（含 WAL/SHM），版本化增量迁移及历史兼容检查。
   不执行冻结文档已废弃的 001/002 SQL，不创建平行事实库。
5. 从获批真实 schema snapshot 构建临时 SQLite；验证 serialize→insert→read→deserialize
   的 ID / enum / timezone / seven_fields / 所有 hash / revision 链不变量。
   在每个事务步骤和 COMMIT 注入失败，确认所有行/关联均回滚，并覆盖并发与重试。
6. 通过后才允许 #012 实现生产适配；本报告不构成写库/迁移授权。

## 本轮验证与验收边界

`python3 -m unittest discover -s tests`：528 项，42.909 秒，OK。
本轮未新增代码，所以没有 #011 新增测试；没有临时 SQLite round-trip/rollback 实测，
这些验收项明确未完成，不能报告全 30 条验收通过。
inventory 结构/主键/索引一致性只读核验通过；缺失与阻塞判定以真实元数据为依据。
变更只包含本报告及兼容映射报告，原 untracked inventory 和 __pycache__ 不提交。

## RESUME 可复跑新增验收测试

为遵守“不新增 RESUME 节之外的文件”，测试以本授权文档代码块交付，无额外测试文件。
运行于仓库根目录；仅使用既有离线 synthetic fixtures 与新七表获批契约，不读取真实业务行，
全局 sqlite3.connect guard 只允许 :memory:；不把 V1.1 当作 G0 旧 schema。
这些是 schema contract / in-memory serialization 与 rollback 验证，#012 repository 仍未实现。

执行：

```bash
python3 -B - <<'PY'
from pathlib import Path
text = Path('docs/PERSISTENCE_READINESS_011.md').read_text()
code = text.split('\n<!-- RESUME_TESTS_BEGIN -->\n', 1)[1].split('\n<!-- RESUME_TESTS_END -->', 1)[0]
code = code.split('```python\n', 1)[1].rsplit('\n```', 1)[0]
exec(compile(code, '<011-resume-contract-tests>', 'exec'))
PY
```

<!-- RESUME_TESTS_BEGIN -->
```python
import ast
from collections.abc import Mapping
from dataclasses import asdict, fields
from enum import Enum
import math
from datetime import datetime
import hashlib
import importlib
import json
from pathlib import Path
import re
import sqlite3
import subprocess
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path('tests').resolve()))
from persistence import schema_v1_1 as schema
from analysis import AnalysisPackageBuilder, AnalysisRunner, ConfirmationFlow, PromptBuilder, finalize_analysis
from analysis.contracts import safe_json, stable_json
from analysis.output_contracts import OutputParser, digest
from evidence import EvidenceSource, HumanConfirm
from test_analysis_runner import FakeModelExecutor
from test_analysis_package import package_arguments
from test_analysis_output import deterministic_response
from test_human_confirmation import ACTOR, STAMP, changed_field, confirm_remaining, source_records

EXPECTED = ('analysis_tasks', 'analysis_reports', 'evidence_records',
            'analysis_confirmation_events', 'analysis_human_overrides',
            'analysis_snapshots', 'analysis_final_evidence')
WHITELIST = {'docs/frozen/DB_SCHEMA_V1.1.md',
             'docs/decisions/DB_SCHEMA_V1_1_PERSISTENCE_UNFREEZE_20261003.md',
             'docs/DB_COMPATIBILITY_MAPPING_011.md',
             'docs/PERSISTENCE_READINESS_011.md', 'persistence/schema_v1_1.py'}
BASELINE = 'eaa928a2ad99121154563e8ecc660841beee5fcb'
REAL_CONNECT = sqlite3.connect

def memory_only(database, *args, **kwargs):
    if database != ':memory:':
        raise AssertionError('REAL_DB_CONNECTION=FORBIDDEN')
    return REAL_CONNECT(database, *args, **kwargs)

# Global guard, including all imports and test fixture calls after this point.
sqlite3.connect = memory_only

def insert(db, table, row):
    columns = ','.join(row)
    db.execute(f'INSERT INTO {table} ({columns}) VALUES ({",".join("?" for _ in row)})',
               tuple(row.values()))

def encode(value):
    # Persistence provenance contains raw_response_hash keys. Input-domain
    # privacy filters are not a persistence serializer; validate JSON types here.
    def validate(item):
        if isinstance(item, Enum):return validate(item.value)
        if isinstance(item, Mapping):
            for key,child in item.items():
                if type(key) is not str:raise ValueError('JSON keys must be text')
                validate(child)
        elif isinstance(item,(tuple,list)):
            for child in item:validate(child)
        elif item is None or type(item) in (str,bool,int):pass
        elif type(item) is float and math.isfinite(item):pass
        else:raise ValueError('only finite JSON values')
    validate(value)
    return stable_json(value)

def fixture():
    package = AnalysisPackageBuilder().build(**package_arguments(quote=True))
    execution = AnalysisRunner(FakeModelExecutor()).run(package)
    assert execution.status == 'UNCONFIRMED'
    flow = ConfirmationFlow(execution.draft, execution.evidence, analysis_report_id='REPORT-011-001')
    flow.modify('risk_level', 'REVIEW_FIELD', changed_field(flow), actor_id=ACTOR,
                reason='人工上调风险', referenced_evidence_ids=flow.run_evidence.input_evidence_ids,
                created_at=STAMP)
    flow.reject('operation_plan', 'REVIEW_ADVICE', actor_id=ACTOR, reason='暂不采纳', created_at=STAMP)
    confirm_remaining(flow)
    prepared = flow.prepare_final_snapshot()
    flow.confirm_final_report(prepared, actor_id=ACTOR, created_at=STAMP)
    final = finalize_analysis(flow, prepared, evidence_records=source_records(flow.run_evidence),
                              final_evidence_id='FINAL-EVIDENCE-011-001')
    envelope = execution.prompt_envelope.to_dict()
    raw = json.dumps(deterministic_response(execution.prompt_envelope), ensure_ascii=False)
    run = flow.run_evidence
    task = dict(id=run.analysis_run_id, stock_id=run.stock_id, task_date='2026-10-03',
                prompt_version=run.prompt_version, model_version=run.model_version,
                status=execution.status, created_by='fixture-creator-011', created_at=run.created_at,
                run_evidence_json=encode(run.to_dict()), validated_draft_json=encode(flow.draft.to_dict()),
                prompt_envelope_json=encode(envelope), raw_model_output=raw)
    rows = [('analysis_tasks', task)]
    for evidence in source_records(run):
        value = asdict(evidence)
        metadata = {key:value[key] for key in ('endpoint_or_tool','request_params','provider_timestamp',
                    'validation_result','raw_response_hash','normalized_data_hash','analysis_output_hash')}
        rows.append(('evidence_records', dict(id=evidence.evidence_id, analysis_report_id=None,
            source=evidence.source.value, timestamp=evidence.request_timestamp,
            raw_data_path=evidence.raw_response_path, calculation=encode({'normalization_rule':value['normalization_rule']}),
            model_version=evidence.model_version, human_confirm=evidence.human_confirm.value,
            collection_task_id=evidence.collection_task_id, system_run_id=evidence.system_run_id,
            created_at=STAMP, metadata_json=encode(metadata), supersedes_evidence_id=evidence.supersedes_evidence_id)))
    rows.extend(revision_rows(flow, prepared, final))
    return execution, flow, prepared, final, rows

def revision_rows(flow, prepared, final):
    snapshot, evidence, conclusion = final.snapshot, final.evidence, final.conclusion
    report = dict(id=snapshot.analysis_report_id, task_id=snapshot.analysis_run_id,
        **{key:encode(value) for key,value in snapshot.to_dict()['seven_fields'].items()},
        human_confirm=snapshot.human_confirm.value, created_at=snapshot.confirmed_at,
        revision_id=snapshot.revision_id, model_version=conclusion.model_version,
        calculation=encode(conclusion.calculation), evidence_ids=encode(conclusion.evidence_ids),
        supersedes_analysis_report_id=snapshot.supersedes_analysis_report_id,
        final_snapshot_hash=snapshot.final_snapshot_hash, final_evidence_id=evidence.final_evidence_id)
    rows = [('analysis_reports', report)]  # Deferred forward references exercise COMMIT.
    for override in flow.human_overrides:
        row = override.to_dict()
        for key in ('replacement','referenced_evidence_ids'):row[key]=encode(row[key])
        rows.append(('analysis_human_overrides', row))
    for candidate,kind,hash_key in ((prepared,'PREPARED','prepared_snapshot_hash'),
                                   (snapshot,'FINAL','final_snapshot_hash')):
        rows.append(('analysis_snapshots', dict(snapshot_hash=getattr(candidate,hash_key),
            snapshot_kind=kind, analysis_run_id=candidate.analysis_run_id,
            analysis_report_id=candidate.analysis_report_id, revision_id=candidate.revision_id,
            payload_json=encode(candidate.to_dict()))))
    for ordinal,event in enumerate(flow.events):
        rows.append(('analysis_confirmation_events', dict(**event.to_dict(),event_sequence=ordinal)))
    row=evidence.to_dict()
    for key in ('input_evidence_ids','used_evidence_ids'):row[key]=encode(row[key])
    rows.append(('analysis_final_evidence',row))
    return rows

class ResumeSchemaTests(unittest.TestCase):
    def setUp(self):
        self.db=sqlite3.connect(':memory:')
        self.addCleanup(self.db.close)
        self.db.row_factory=sqlite3.Row
        self.db.executescript(schema.TEST_SCHEMA_DDL)
        self.execution,self.flow,self.prepared,self.final,self.rows=fixture()

    def commit_fixture(self):
        self.db.execute('BEGIN')
        for table,row in self.rows:insert(self.db,table,row)
        self.db.commit()

    def test_01_import_has_no_connection_or_io(self):
        with patch('sqlite3.connect',side_effect=AssertionError('no connection')):
            importlib.reload(schema)
        tree=ast.parse(Path('persistence/schema_v1_1.py').read_text())
        self.assertFalse(any(isinstance(n,(ast.Import,ast.ImportFrom)) for n in ast.walk(tree)))
        self.assertFalse(any(isinstance(n,ast.Call) and isinstance(n.func,ast.Name)
                             and n.func.id in ('open','exec','eval','__import__') for n in ast.walk(tree)))
        with self.assertRaisesRegex(AssertionError,'FORBIDDEN'):sqlite3.connect('stock_analysis.db')

    def test_02_gate32_exactly_seven_new_tables(self):
        actual={r[0] for r in self.db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        self.assertEqual(schema.NEW_TABLES,EXPECTED)
        self.assertEqual(actual,set(EXPECTED))
        self.assertEqual(len(schema.INSERT_ONLY_TABLES),5)
        self.assertNotIn('analysis_conclusions',actual)
        self.assertEqual(self.db.execute('PRAGMA foreign_keys').fetchone()[0],1)
        self.assertEqual(self.db.execute('PRAGMA recursive_triggers').fetchone()[0],1)

    def test_03_roundtrip_run_draft_input_and_raw_hashes(self):
        self.commit_fixture()
        row=self.db.execute('SELECT * FROM analysis_tasks').fetchone()
        run=json.loads(row['run_evidence_json']); draft=json.loads(row['validated_draft_json'])
        self.assertEqual(run,self.flow.run_evidence.to_dict())
        self.assertEqual(draft,self.flow.draft.to_dict())
        self.assertEqual(row['id'],run['analysis_run_id'])
        self.assertEqual(digest(draft['output']),run['validated_output_hash'])
        self.assertEqual(hashlib.sha256(row['raw_model_output'].encode('utf-8')).hexdigest(),run['model_output_hash'])
        envelope=json.loads(row['prompt_envelope_json'])
        for key in ('input_hash','NETWORK_CALL_COUNT','MODEL_CALL_COUNT','DATABASE_WRITE'):envelope.pop(key)
        self.assertEqual(digest(envelope),run['input_hash'])
        self.assertNotIn('_gate_digest',run)

    def test_04_roundtrip_prepared_and_final_hashes(self):
        self.commit_fixture()
        for kind,candidate,hash_key in (('PREPARED',self.prepared,'prepared_snapshot_hash'),
                                        ('FINAL',self.final.snapshot,'final_snapshot_hash')):
            with self.subTest(kind=kind):
                row=self.db.execute('SELECT * FROM analysis_snapshots WHERE snapshot_kind=?',(kind,)).fetchone()
                payload=json.loads(row['payload_json'])
                self.assertEqual(payload,candidate.to_dict())
                self.assertEqual(payload['final_state'],kind)
                self.assertEqual(payload['confirmation_log_hash'],digest(payload['confirmation_events']))
                claimed=payload.pop(hash_key)
                self.assertEqual(claimed,digest(payload))
                self.assertEqual(row['snapshot_hash'],claimed)

    def test_05_seven_fields_original_modified_rejected(self):
        self.commit_fixture()
        row=self.db.execute('SELECT * FROM analysis_reports').fetchone()
        for key,value in self.final.snapshot.to_dict()['seven_fields'].items():
            self.assertEqual(json.loads(row[key]),value)
        self.assertEqual(json.loads(row['risk_level'])['DECISION'],'MODIFIED')
        self.assertEqual(json.loads(row['risk_level'])['ORIGIN'],'HUMAN')
        self.assertIsNone(json.loads(row['operation_plan'])['EFFECTIVE_VALUE'])
        self.assertEqual(json.loads(row['evidence_ids']),list(self.final.conclusion.evidence_ids))
        self.assertEqual(json.loads(row['calculation']),json.loads(encode(self.final.conclusion.calculation)))

    def test_06_event_order_enums_and_timezone(self):
        self.commit_fixture()
        rows=list(self.db.execute('SELECT * FROM analysis_confirmation_events ORDER BY event_sequence'))
        self.assertEqual([r['event_sequence'] for r in rows],list(range(len(self.flow.events))))
        for row,event in zip(rows,self.flow.events):
            restored=dict(row);restored.pop('event_sequence')
            self.assertEqual(restored,event.to_dict())
            self.assertEqual(row['created_at'],STAMP)
            self.assertIsNotNone(datetime.fromisoformat(row['created_at']).utcoffset())
            self.assertIsInstance(HumanConfirm(row['current_state']),HumanConfirm)
        self.assertEqual(digest([e.to_dict() for e in self.flow.events]),self.final.snapshot.confirmation_log_hash)

    def test_07_overrides_and_provider_metadata_roundtrip(self):
        self.commit_fixture()
        stored=dict(self.db.execute('SELECT * FROM analysis_human_overrides').fetchone())
        for key in ('replacement','referenced_evidence_ids'):stored[key]=json.loads(stored[key])
        self.assertEqual(stored,self.flow.human_overrides[0].to_dict())
        for original in source_records(self.flow.run_evidence):
            row=self.db.execute('SELECT * FROM evidence_records WHERE id=?',(original.evidence_id,)).fetchone()
            metadata=json.loads(row['metadata_json'])
            self.assertEqual(row['source'],original.source.value)
            self.assertEqual(metadata['raw_response_hash'],original.raw_response_hash)
            self.assertEqual(metadata['normalized_data_hash'],original.normalized_data_hash)
            self.assertIsNone(metadata['analysis_output_hash'])
            self.assertIsNone(metadata['provider_timestamp'])
            self.assertEqual(metadata['validation_result'],{'status':'PASS','failures':[]})
            self.assertEqual(json.loads(row['calculation'])['normalization_rule'],list(original.normalization_rule))

    def test_08_final_evidence_is_distinct_and_roundtrips(self):
        self.commit_fixture()
        row=dict(self.db.execute('SELECT * FROM analysis_final_evidence').fetchone())
        for key in ('input_evidence_ids','used_evidence_ids'):row[key]=json.loads(row[key])
        self.assertEqual(row,self.final.evidence.to_dict())
        self.assertNotIn('source',row)
        self.assertEqual(len(EvidenceSource),3)
        invalid=next(dict(r) for t,r in self.rows if t=='evidence_records')
        invalid.update(id='HUMAN-EVIDENCE-FORBIDDEN',source='HUMAN')
        with self.assertRaises(sqlite3.IntegrityError):insert(self.db,'evidence_records',invalid)

    def test_09_only_task_status_update_allowed(self):
        self.commit_fixture()
        self.db.execute("UPDATE analysis_tasks SET status='ARCHIVED'")
        self.assertEqual(self.db.execute('SELECT status FROM analysis_tasks').fetchone()[0],'ARCHIVED')
        for table in EXPECTED:
            columns=[r[1] for r in self.db.execute(f'PRAGMA table_info({table})')]
            for column in columns:
                if table=='analysis_tasks' and column=='status':continue
                with self.subTest(table=table,column=column),self.assertRaisesRegex(sqlite3.IntegrityError,'UPDATE_FORBIDDEN'):
                    self.db.execute(f'UPDATE {table} SET {column}={column}')
            with self.subTest(table=table),self.assertRaisesRegex(sqlite3.IntegrityError,'DELETE_FORBIDDEN'):
                self.db.execute(f'DELETE FROM {table}')

    def test_10_replace_cannot_overwrite_immutable_identity(self):
        self.commit_fixture()
        for table,row in self.rows:
            with self.subTest(table=table),self.assertRaisesRegex(sqlite3.IntegrityError,'IMMUTABLE_IDENTITY'):
                self.db.execute(f'INSERT OR REPLACE INTO {table} ({",".join(row)}) VALUES ({",".join("?" for _ in row)})',tuple(row.values()))

    def test_11_rollback_failure_at_every_insert_step(self):
        for failure in range(len(self.rows)):
            with self.subTest(failure=failure):
                self.db.execute('BEGIN')
                try:
                    for step,(table,row) in enumerate(self.rows):
                        insert(self.db,table,row)
                        if step==failure:raise RuntimeError('injected transaction failure')
                    self.fail('injection was not reached')
                except RuntimeError:self.db.rollback()
                for table in EXPECTED:self.assertEqual(self.db.execute(f'SELECT count(*) FROM {table}').fetchone()[0],0)

    def test_12_deferred_commit_failure_rolls_back(self):
        self.db.execute('BEGIN')
        for table,row in self.rows:
            if table!='analysis_final_evidence':insert(self.db,table,row)
        with self.assertRaises(sqlite3.IntegrityError):self.db.commit()
        self.db.rollback()
        for table in EXPECTED:self.assertEqual(self.db.execute(f'SELECT count(*) FROM {table}').fetchone()[0],0)

    def test_13_pending_events_and_prepared_before_final_report(self):
        self.db.execute('BEGIN')
        for table,row in self.rows:
            if table in ('analysis_tasks','evidence_records','analysis_human_overrides') or (
                    table=='analysis_snapshots' and row['snapshot_kind']=='PREPARED') or (
                    table=='analysis_confirmation_events' and row['target']!='FINAL_REPORT'):
                insert(self.db,table,row)
        self.db.commit()
        self.assertEqual(self.db.execute('SELECT count(*) FROM analysis_reports').fetchone()[0],0)
        self.assertGreater(self.db.execute('SELECT count(*) FROM analysis_confirmation_events').fetchone()[0],0)

    def test_14_correction_revision_preserves_history_and_explicit_links(self):
        self.commit_fixture()
        old=self.db.execute('SELECT payload_json FROM analysis_snapshots WHERE snapshot_kind=\'FINAL\'').fetchone()[0]
        correction=self.flow.reject('market_view','REVIEW_FIELD',actor_id=ACTOR,
                                    reason='事后发现原判断需修订',created_at=STAMP)
        insert(self.db,'analysis_confirmation_events',dict(**correction.to_dict(),
               event_sequence=len(self.flow.events)-1))
        self.db.commit()
        flow=self.flow.new_revision(analysis_report_id='REPORT-011-002')
        confirm_remaining(flow)
        prepared=flow.prepare_final_snapshot()
        flow.confirm_final_report(prepared,actor_id=ACTOR,created_at=STAMP)
        final=finalize_analysis(flow,prepared,evidence_records=source_records(flow.run_evidence),
                                final_evidence_id='FINAL-EVIDENCE-011-002')
        self.db.execute('BEGIN')
        for table,row in revision_rows(flow,prepared,final):insert(self.db,table,row)
        self.db.commit()
        report=self.db.execute('SELECT * FROM analysis_reports WHERE id=?',(final.snapshot.analysis_report_id,)).fetchone()
        evidence=self.db.execute('SELECT * FROM analysis_final_evidence WHERE final_evidence_id=?',(final.evidence.final_evidence_id,)).fetchone()
        self.assertEqual(report['supersedes_analysis_report_id'],self.final.snapshot.analysis_report_id)
        self.assertEqual(evidence['supersedes_final_evidence_id'],self.final.evidence.final_evidence_id)
        self.assertNotEqual(report['revision_id'],self.final.snapshot.revision_id)
        self.assertEqual(self.db.execute('SELECT payload_json FROM analysis_snapshots WHERE snapshot_hash=?',
                        (self.final.snapshot.final_snapshot_hash,)).fetchone()[0],old)
        self.assertEqual(list(self.db.execute('PRAGMA foreign_key_check')),[])

    def test_15_missing_supersedes_target_is_rejected(self):
        self.commit_fixture()
        row=next(dict(r) for t,r in self.rows if t=='evidence_records')
        row.update(id='NEW-EVIDENCE',supersedes_evidence_id='MISSING-PREDECESSOR')
        with self.assertRaises(sqlite3.IntegrityError):insert(self.db,'evidence_records',row)

    def test_16_stable_json_and_invalid_json(self):
        value={'中文':{'array':[None,True,1,1.0],'time':STAMP},'first':'文本'}
        canonical=encode(value)
        self.assertEqual(canonical,encode(dict(reversed(list(value.items())))))
        decoded=OutputParser().parse(self.db.execute('SELECT CAST(? AS TEXT)',(canonical,)).fetchone()[0])
        self.assertEqual(encode(decoded),canonical)
        self.assertEqual(digest(decoded),digest(value))
        for invalid in ('{"a":1,"a":2}','{"a":1,"\\u0061":2}','{"x":NaN}',
                        '{"x":Infinity}','{"x":1e999}'):
            with self.subTest(invalid=invalid),self.assertRaises(ValueError):OutputParser().parse(invalid)
        for invalid in ({1:'value'},{'x':float('nan')},{'x':float('inf')}):
            with self.assertRaises(ValueError):encode(invalid)

    def test_17_real_inventory_projection_complete_without_business_counts(self):
        text=Path('docs/DB_COMPATIBILITY_MAPPING_011.md').read_text()
        block=text.split('<!-- G0_INVENTORY_BEGIN -->',1)[1].split('<!-- G0_INVENTORY_END -->',1)[0]
        data=json.loads(block.split('```json\n',1)[1].split('\n```',1)[0])
        self.assertEqual(len(data['tables']),data['table_count'])
        self.assertEqual(data['table_count'],32)
        self.assertTrue(set(EXPECTED).isdisjoint(t['name'] for t in data['tables']))
        self.assertEqual(sum(len(t['indexes']) for t in data['tables']),226)
        self.assertEqual(sum(len(t['foreign_keys']) for t in data['tables']),11)
        self.assertTrue(all('row_count' not in t for t in data['tables']))
        self.assertEqual(data['quick_check'],'ok')
        self.assertEqual(data['journal_mode'],'wal')
        canonical=json.dumps(data,ensure_ascii=False,sort_keys=True,separators=(',',':'))
        self.assertIn(hashlib.sha256(canonical.encode()).hexdigest(),text)
        local=Path('docs/freeze/DB_EXISTING_SCHEMA_INVENTORY_V1.json')
        if local.exists():  # PR checkout needs only the embedded projection.
            raw=local.read_bytes()
            self.assertEqual(hashlib.sha256(raw).hexdigest(),'c9eaf6d1bc8927c8c0dbdb6dd0404ddc52751b2bcefbbad1c5b66a406fbadbd2')
            original=json.loads(raw)
            self.assertEqual(data['tables'],[{k:t[k] for k in ('name','columns','primary_key','indexes','foreign_keys')}
                                            for t in original['tables']])

    def test_18_gates31_33_34_35_and_scope(self):
        frozen=Path('docs/frozen/DB_SCHEMA_V1.1.md').read_text()
        for marker in ('VERSION=V1.1','STATUS=FROZEN_FOR_IMPLEMENTATION','SUPERSEDES=DB_SCHEMA_V1.0',
                       'CHANGE_REASON=PERSISTENCE_G0_BLOCKED_UNFREEZE','MIGRATION_POLICY=ADDITIVE_ONLY'):
            self.assertIn(marker,frozen)
        for table in EXPECTED:
            self.assertIn('### '+table,frozen)
            for definition in schema.TABLE_DEFINITIONS[table]:
                if not definition.startswith(('CHECK(', 'UNIQUE(')):self.assertIn('`'+definition.split()[0]+'`',frozen)
        decision=Path('docs/decisions/DB_SCHEMA_V1_1_PERSISTENCE_UNFREEZE_20261003.md').read_text()
        for marker in ('#011_SCHEMA_UNFREEZE_DECISION=APPROVED','QI_DECISION=OPTION_A',
                       'DB_SCHEMA_TARGET=V1.1','UNFREEZE_SCOPE=DB_SCHEMA_ONLY'):self.assertIn(marker,decision)
        self.assertIn('只有 PERSISTENCE_READINESS=READY 才允许定义 #012 正式 migration',frozen)
        self.assertFalse(re.search(r'\b(DROP|ALTER)\s+(TABLE|INDEX)|\bRENAME\s+TO',schema.TEST_SCHEMA_DDL,re.I))
        original=subprocess.check_output(['git','show',BASELINE+':docs/frozen/DB_SCHEMA_V1.0.md'])
        self.assertEqual(Path('docs/frozen/DB_SCHEMA_V1.0.md').read_bytes(),original)
        changed=subprocess.check_output(['git','diff',BASELINE,'--name-only']).decode().splitlines()
        self.assertTrue(set(changed)<=WHITELIST,changed)
        self.assertFalse(any(Path('migration').glob('*.sql')))

if __name__ == '__main__':
    unittest.main(verbosity=2)
```
<!-- RESUME_TESTS_END -->

## RESUME 最终验证与验收结论（2026-10-03）

```text
PERSISTENCE_READINESS=READY
EXECUTION_STATUS=RESUME_AUTHORIZED
BASELINE_TESTS=528/528 PASS
RESUME_CONTRACT_TESTS=18/18 PASS
DB_SCHEMA_V1_1=PASS
NEW_TABLES_EXACTLY=analysis_tasks,analysis_reports,evidence_records,analysis_confirmation_events,analysis_human_overrides,analysis_snapshots,analysis_final_evidence
LEGACY_32_TABLES_CHANGE=0
#011_SCHEMA_UNFREEZE_DECISION=APPROVED
QI_DECISION=OPTION_A
DB_SCHEMA_TARGET=V1.1
UNFREEZE_SCOPE=DB_SCHEMA_ONLY
REAL_DB_CONNECTION=FORBIDDEN
REAL_DB_WRITE=NO
REAL_DB_DDL=0
REAL_DB_DML=0
MIGRATION_APPLY=0
```

main 基线 `python3 -B -m unittest discover -s tests`：528 tests，44.493 秒，OK。
上述文档内新增契约测试：18 tests，7.094 秒，OK；含每个 INSERT 步骤失败回滚及 deferred COMMIT
失败回滚、真实 #009/#010 synthetic 对象的 JSON/hash/ID/enum/timezone/revision 往返。
测试只在 :memory: 对明确获批的新七表执行 DDL/DML；生产 DDL/DML=0。

| Gate | 结果 / 证据 |
| --- | --- |
| 31 DB_SCHEMA_V1_1 | PASS：完整版本/状态/原因、七表 94 个字段定义、payload 公共字段及冻结约束；test_18 |
| 32 NEW_TABLES_EXACTLY | PASS：NEW_TABLES 与 sqlite_master 精确七表；test_02 |
| 33 旧 32 表零改动 | PASS：仅五个 RESUME 白名单文件；schema DDL 只 CREATE 新七表与其索引/触发器，无 DROP/RENAME/ALTER；V1.0 字节不变，inventory 原 hash 不变；test_17/18 |
| 34 READY 前置 | PASS：当前映射契约 READY，#012 正式 migration 只允许在该状态后定义；本轮没有 migration/*.sql、真实库连接或迁移应用；test_18 |
| 35 解冻决策 | PASS：APPROVED / OPTION_A / V1.1 / DB_SCHEMA_ONLY 均见独立决策记录；test_18 |

原 30 项中，基线、映射、serialization、append-only/revision、事务合同、临时 rollback 与
read repository 接口合同已给出可核验依据；完整生产 integrity_check/连接 FK 启用状态、
legacy DDL 约束重建、repository/read-model/迁移运行均非本轮实测，不冒称生产验收全过。
G0 inventory quick_check=ok 仍不能替代完整 integrity_check；此限度由获授权 #012 流程补齐。
READY 是最小 schema / 映射可实施，不改变 REAL_DB_CONNECTION=FORBIDDEN。

#012 必须实施的只读 repository contract：AnalysisReadRepository 提供 latest_confirmed_analysis /
current_effective_revision / analysis_history；EvidenceReadRepository 提供 evidence / field_evidence_trace；
ConfirmationReadRepository 提供 pending_confirmations / confirmation_history / current_confirmation_state。
返回只读 DTO，完整区分 original/effective/decision、Provider 与人工参考证据、旧历史 record_origin、
当前派生状态与生成时 CONFIRMED、拒绝/被取代 revision；链或 hash 异常不得标有效。
接口定义沿用上述 G0 要求并由 V1.1 提供明确 backing，实现属于 #012，#011 不建 Dashboard。

## 交付环境状态

契约/测试已完成，schema readiness 仍为 READY；远端发布未完成。

```text
DELIVERY_STATUS=LOCAL_COMMITTED_REMOTE_PUBLICATION_BLOCKED
GIT_BRANCH=codex/v1-persistence-readiness-011
WRITABLE_GIT_METADATA=/tmp/task-011-git
GIT_PUSH=FAILED_DNS_RESOLUTION
GH_PR_CREATE=FAILED_API_CONNECTION
GITHUB_CONNECTOR_WRITE=REJECTED_APPROVAL_POLICY_NEVER
PR_URL=NOT_CREATED
```

主工作区 .git 挂载只读，git add 无法创建 index.lock；在 /tmp/task-011-git 复制可写 Git
元数据，沿用原分支名称完成五个授权文件的 add/commit。主工作区的受保护 .git 没有改动。
终端 git push 报 Could not resolve host: github.com；gh pr create 报 error connecting to api.github.com。
GitHub connector 可只读访问仓库，但创建 blob 的写调用被自动审批拒绝，原因为
“MCP tool call requires approval, but approval policy is never”。没有远端提交/PR 被创建，
不伪造 PR URL。PR 正文已准备于 /tmp/TASK_011_PR_BODY.md；后续须在允许写入并可联网的环境发布。
这项发布阻碍不改变本轮真实数据库连接/写入为零，也不授权扩大解冻范围。
