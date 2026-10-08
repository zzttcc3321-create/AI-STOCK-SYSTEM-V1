# Evidence Raw Archive / Metadata Sidecar V1.0

```
VERSION=V1.0
STATUS=FROZEN_FOR_IMPLEMENTATION
PARENT=EVIDENCE_PIPELINE_DESIGN_V1.0
IMPLEMENTS=P6_PHYSICAL_ARCHIVE_CONTRACT
TASK_ID=AI-STOCK-V1-EVIDENCE-ARCHIVE-012B
CONTRACT_REVISION=#014-R2-WINDOWS-DURABILITY-CONTRACT
REVISION_TASK_ID=AI-STOCK-V1-014-R2-WINDOWS-DURABILITY-CONTRACT
REVISION_DECISION=APPROVE_CONTRACT_REVISION (2026-10-08)
PHYSICAL_CONTRACT=archive://v1/system_run_id/collection_task_id/request_id/evidence_id
RAW_STORAGE=GZIP_BIT_EXACT_RECOVERABLE
SIDECAR=APPEND_ONLY
HASH=SHA256
ARCHIVE_DISK_LIMIT=5_PERCENT
SOURCE_COUNT=3
FOURTH_SOURCE=NO
REAL_PROVIDER_EVIDENCE_WRITE_DEFAULT=DISABLED
#012B_TASK_AUTHORIZATION != REAL_DB_APPLY_AUTHORIZATION
```

本合同只约束配置启用之后的新归档。父级冻结文档不变；不定义旧引用解释、迁移、回填、删除、DB schema 或 migration。人工确认后 +90 天的父级保留规则继续有效，本实现没有自动删除或后台服务。

#014-R2 经总指挥批准修订耐久合同：目录 fsync 不再是所有平台归档成功的必备条件；文件 flush/fsync、原子提交、显式耐久等级仍为必备条件。以下耐久条款及 sidecar `durability_level` 扩展取代 PR #29 的跨平台目录 fsync 假设，其余约束继续有效。

## 1. 身份与路径

允许的逻辑来源只有 `TUSHARE_MCP`、`TDX_OFFICIAL_LOCAL_HTTP`、`WORKBUDDY_TDX_MCP`。云 MCP 的实际服务名仍为 `TDX_OFFICIAL_CLOUD_MCP`，不构成第四源。

URI 中四段均为显式区分大小写的 `[A-Za-z0-9_-]{1,100}`，严格解析，不进行 URL 解码、大小写转换、路径归一化或旧格式猜测。拒绝绝对路径、斜杠、反斜杠、点段、percent encoding、query、fragment 和多余段。

配置根目录 `root` 后，映射为：

```
root/v1/<system_run_id>/<collection_task_id>/<request_id>/<evidence_id>/
  <part_name>.raw.gz
  raw.evidence.json
  normalized-<normalized_data_sha256>.json
  binding-<sha256(canonical_json(analysis_report_id))>.json
```

根目录及所有祖先、目录、文件不得含 symlink / Windows reparse point；不得使用设备、FIFO 等特殊文件。根目录应由运行用户独占，外部进程不得修改或替换其目录。新建 POSIX 目录为 0700，文件为 0600；既有根目录的访问控制由配置方提供。DB 的新 `raw_data_path` 只存 canonical URI，绝不存物理绝对路径。

`source_reference` 单独保存来源引用。Tushare 必须逐项等于脱敏后的 stock/date 请求；TDX local 必须逐项等于 run/collection/request 三段；云 MCP 引用限固定官方 endpoint/tool。Reader 也复核该约束。旧引用作为当前新请求的来源说明，不成为 Resolver 的输入。

## 2. Raw 与 Hash

每个 raw part 是 HTTP transport 实际读出的 entity body `bytes`，在 JSON parse、标准化、清洗之前保留。不得用解析结果重新编码冒充 raw；空、二进制、非 UTF-8 body 都合法。HTTP header、认证请求 body、Cookie、session header 不进入归档。

Tushare 成功查询的有序三 part 固定为 `daily`、`adj_factor`、`trade_cal`，原始响应分别完整保留。中途失败只归档实际收到的 part，不合成剩余响应。TDX local 单 part 为 `response`；TDX information 每个实际 RPC response 单独生成 Evidence，沿用一个共享请求 context；202/空 initialized notification 沿用既有无证据行为。业务缓存命中继续返回原始 Evidence/context，不冒充新的采集。

每 part 存 `name / file / raw_size / raw_sha256 / stored_size / stored_sha256`。gzip 使用 `mtime=0`，Reader 对压缩文件复算 stored SHA-256，再有界解压并复算 raw SHA-256。必须同时验证大小和两个 Hash。

单 part 的 `raw_response_hash` 等于 raw SHA-256。多 part 的 bundle hash 定义为 SHA-256(以下有序数组的 canonical JSON)：

```
[{"name": part_name, "raw_size": exact_byte_count, "raw_sha256": exact_raw_hash}, ...]
```

part 名、顺序、长度、内容均参与 bundle 身份，禁止重复 part 名。

## 3. Sidecar schema 与追加阶段

所有 sidecar 均为 UTF-8 canonical JSON：key 排序、无空白、`ensure_ascii=False`、禁止 NaN/Infinity/非 JSON 类型/重复 key；Reader 要求存储 bytes 等于重新编码 bytes。统一 envelope 严格只有：

```
{"payload": <stage_payload>, "sha256": SHA256(canonical_json(stage_payload))}
```

Raw payload 严格包含以下字段：

| 字段 | 约束 |
| --- | --- |
| `version` / `kind` | `V1.0` / `RAW` |
| `identity` / `uri` | 四段结构化身份及相同 canonical URI |
| `source` / `source_reference` | 三源之一 / 经上下文校验的来源说明或 null |
| `record` | `EvidenceRecord` 全字段 JSON；Evidence/run/collection、source、URI、raw hash 必须与 manifest 完全一致 |
| `parts` / `raw_response_hash` | 上述 manifest / 单 part 或 bundle hash |
| `attributes` | 非敏感 JSON，例如 http status、response completeness、actual service |
| `durability_level` | 显式三级状态；提交只接受 `DIRECTORY_SYNC` 或 `FILE_SYNC_ONLY`，参与 sidecar SHA-256 |

Raw record 的标准化/分析字段保持首次采集状态，不通过修改 raw sidecar 补齐后续阶段。

Normalized payload 严格包含 `kind=NORMALIZED / uri / normalized_data / normalized_data_hash / normalization_rule / validation_result`。Reader 重新编码 normalized data 复算 SHA-256，并与引用该阶段的 Evidence/DB calculation 中的规则、校验结果、normalized hash 比对。同一 normalized content hash 对应不同规则或结果为身份冲突；不同内容 hash 可追加新阶段，不覆盖前阶段。

Analysis binding payload 为 `kind=ANALYSIS_BINDING / uri / binding / analysis_output_hash`。binding 使用已验证 `FinalAnalysisEvidence.to_dict()`，包括 report/run/final Evidence、model version、input/used IDs、input/model output/validated output/final snapshot/confirmation log hashes、确认信息、supersedes。`analysis_output_hash` 等于 binding 的 `model_output_hash`，定义为 DB 保存的原始模型输出 UTF-8 bytes 的 SHA-256。该值通过读取并复算已有输出验证，归档流程不调用模型。raw Evidence 的未绑定分析 Hash 保持 null；只有分析上下文验证完成后 read-model 才展示绑定的分析 Hash。

#014-R2：normalized、analysis binding 和 canonical evidence 追加事件的顶层 payload 同样必须记录自己的 `durability_level`。各事件按自己的提交合同解释；raw 的等级不能代表后续事件的等级。此字段位于 sidecar/read-model，不增改 `EvidenceRecord`、数据库 schema 或 migration。

Canonical JSON 与自包含 SHA-256 用于完整性检测，不是签名；root 的写权限是信任边界。请求字段递归删除 token/API key/authorization/password/cookie/session/secret 及其组合变体，不存值或 mask。已知运行凭据还检查普通 bytes、JSON 转义及 Unicode 转义 echo；TDX information 原 R6 检查继续在归档前执行，拒绝的响应不存档。

## 4. 提交、幂等与失败

Writer 同步执行，根目录 create-exclusive lock 保证多个 Writer 共享配额检查和提交串行化。锁冲突立即 fail-closed；不等待、不启动 daemon。崩溃遗留 lock 需要人工检查，不自动移除。

归档状态的三级耐久等级：

| durability_level | 提交保证 | Linux | Windows | 可进入 ARCHIVED |
| --- | --- | --- | --- | --- |
| `DIRECTORY_SYNC` | 每个文件 flush/fsync，目录项也按下述顺序 fsync | 默认；能力可用时允许 | 不假定可用；显式请求时必须实际成功 | 是 |
| `FILE_SYNC_ONLY` | 每个文件 flush/fsync，原子 rename/link；不承诺掉电后的目录项持久性 | 显式配置允许 | 默认支持合同 | 是 |
| `UNVERIFIED` | 未验证／缺失耐久等级 | 禁止提交 | 禁止提交 | 否 |

任务裁决原文的 `FILE_ONLY` 是文件级耐久口径的简称；序列化状态统一使用 `FILE_SYNC_ONLY`，不增加第四种状态。

Writer 在写入前选择合同：`ArchiveWriter(root)` 在 Windows 默认 `FILE_SYNC_ONLY`、Linux 默认 `DIRECTORY_SYNC`；调用方可显式传入 `durability_level="FILE_SYNC_ONLY"` 或 `"DIRECTORY_SYNC"`。拒绝 `UNVERIFIED`。目录合同失败时不自动切换合同；文件 flush/fsync 失败始终 fail-closed。

Raw：同一 filesystem 的隐藏 staging directory → exclusive 文件写 → 检查完整写入 → flush/fsync 每个文件（包括记录耐久等级的 sealed sidecar）→ 配额复核 → 不覆盖既有身份的目录 rename → Reader 回读验证。`DIRECTORY_SYNC` 还要求 rename 前 fsync staging、rename 后 fsync parent、新建目录后 fsync 其父目录。`FILE_SYNC_ONLY` 显式省略目录 fsync，不调用 Windows 不支持的目录打开 primitive。metadata 是目录内的提交 marker，提交前不会暴露部分目录。

Normalized/binding/canonical evidence：隐藏 staging file → exclusive 写/flush/fsync → 配额复核 → 原子 create-if-absent hard link → 清理 staging。`DIRECTORY_SYNC` 在 link 后还要求 fsync Evidence directory；`FILE_SYNC_ONLY` 使用文件级合同。最终文件永不原位改写。

归档成功 = 文件完整写入 + 可用且明确选择的耐久合同满足 + 耐久等级已记录 + Reader 回读验证成功。返回 `ARCHIVED` 与 `IDEMPOTENT_NOOP` 的 receipt、raw metadata 和 Reader.read 结果均显式提供 `durability_level`；追加事件在 sealed payload 中记录等级。

任何 raw/sidecar/partial write/flush/file fsync/rename/link/quota failure 不返回成功；`DIRECTORY_SYNC` 的任意 directory fsync failure 同样不返回成功，rename 后 parent fsync 失败撤销本次 marker，raw 留作 `ORPHAN_INCOMPLETE`。既有提交永不覆盖、删除或自动修复；失败清理只针对本次创建的 staging/marker。文件系统必须支持文件 fsync、同盘 rename、hard link；目录 fsync 只在选择 `DIRECTORY_SYNC` 时强制要求。文件级合同不等于完整掉电目录耐久保证，降级信息不可隐藏。

同身份同内容且已有 raw/raw sidecar 验证通过：`IDEMPOTENT_NOOP`，mtime/bytes 不变。同身份不同 raw 或 metadata：`ARCHIVE_IDENTITY_CONFLICT`。既有身份已损坏则拒绝，不修复。committed metadata 缺 raw：`CORRUPT`；raw 无 committed metadata：`ORPHAN_INCOMPLETE`，不作为证据。

幂等比较忽略本次调用所选择的耐久等级，返回既有 sidecar 实际记录的等级，绝不重写或提升既有等级。缺失等级按 `UNVERIFIED` 处理，Reader、幂等重试及追加均拒绝；不根据平台或旧版本推断历史 durability、不静默回填。无等级旧归档需要保留原文件供诊断，并以新身份重新采集，才能获得新的明确耐久合同。

## 5. 资源

配额上限为 root 所在 filesystem 总容量的 1/20，显式 `quota_bytes` 只能降低。统计 root 内全部文件/目录（含 pending/orphan），POSIX 同时考虑 allocated blocks 与 logical bytes，按较大者计；写前保守预留文件块和目录空间，提交前再次检查实际占用。超额立即拒绝，不删旧数据。

Raw 单次最多 8 MiB，最多 8 part；每个 sidecar 最多 2 MiB。Reader 校验以 64 KiB chunk 进行，按 manifest 大小设置解压上限，拒绝 gzip bomb。`include_raw=False` 不物化 raw；读取 raw 总量仍受 8 MiB 上限约束。目录配额统计使用 streaming walk，不加载历史 raw。无 S3、Redis、队列或后台服务。

## 6. #012A gate / read-model

真实 Provider Evidence DB 写入必须同时满足：配置 ArchiveReader、逐条 raw 验证、sidecar/normalized 验证、显式 `enable_real_provider_evidence=True`。最终持久化还必须验证每条 used Evidence 的 analysis binding，保留既有 sealed domain gate，并在 transaction 提交前再次验证物理归档。失败不留下部分 DB 行。

`allow_synthetic_evidence=True` 仅保留临时测试库 + 非 archive 引用的既有测试路径；不能让 canonical URI 绕过真实归档 gate。该开关不授予真实 DB apply 权限。

`calculation` 使用已有 TEXT 列保存 `normalization_rule / validation_result / normalized_data_hash`；无表/列/trigger/index/migration 修改。Reader 不解释旧引用，也不写库。

| 状态 | metadata_availability | integrity_status |
| --- | --- | --- |
| 没有 Reader / 不支持的旧引用 | UNAVAILABLE | NOT_VERIFIED |
| 逐项验证成功 | AVAILABLE | VERIFIED |
| 配置后的 canonical archive 缺失、损坏或绑定不符 | AVAILABLE | CORRUPT |

只有所有 used Evidence 的 raw/normalized/analysis binding 与 DB report/snapshot/run/prompt/原始模型输出/calculation Hash 均验证成功，才返回 `REPORT_FULL_TRACE=True`；字段追踪还要求相关人工引用证据验证成功。无 Reader、旧引用、部分证据、缺失绑定或损坏绝不报告完整追踪。

#012B 合并不构成真实 Windows/AWS DB 执行授权。Windows `stock_analysis.db` 的实际 apply 仍需 qi 独立给出 `APPLY_REAL_DB=YES`。
