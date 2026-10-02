# TDX 本机实时 Provider 与单向 Push Bridge（Task #005）

本实现消费已冻结的 ProviderEnvelope、DataNormalizer 与 EvidenceBuilder，不修改其契约。
源码及运行均仅依赖 Python 标准库；建议 Python 3.11 或以上。

```text
qi Windows: TdxW.exe → TdxLocalHttpProvider → durable outbox → Normalizer / Evidence
                                                       ↓ HTTPS POST /realtime
AWS reference ingress: auth → validate → deduplicate → freshness → evidence + receipt
                                                       ↓
                                      IngressStore.current(stock_id)
```

`source=TDX_OFFICIAL_LOCAL_HTTP`、`runtime_node=QI_WINDOWS_LOCAL`、
`transport=WINDOWS_TO_AWS_PUSH` 恒定；接收器是 transport，没有注册数据源、回调地址或出站网络客户端。
本任务没有部署任何 Windows 计划任务、AWS 服务、生产反向代理或数据库。

## 005A 协议与状态

`TdxLocalHttpProvider.fetch("601518.SH")` 可直接调用；契约调用形式如下：

```python
provider.fetch({
    "stock_id": "601518.SH", "data_kind": "realtime_quote",
    "request_id": "request_1", "system_run_id": "run_1", "collection_task_id": "task_1",
})
```

请求只使用 `get_market_snapshot`，包含 `jsonrpc="2.0"`、请求 ID 和
`params.stock_code`；Content-Type 为 `application/json; charset=utf-8`。
必须满足 `result.ErrorId == "0"`（字符串）且最小字段/五档完整、数值有限且非负。
若返回 RPC ID，则必须等于请求 ID。字段不补零、不猜单位、不派生金额。
`Now/LastClose/Open/Max/Min/Volume/Amount/Average` 与四组五档原值映射进入 snapshot。
数据方时钟当前未在已验证协议中提供，`provider_timestamp=null`。

连接拒绝、超时、客户端退出或返回失败时，payload 中
`realtime_quote_status=MISSING`、`snapshot=null`。ProviderStatus 分别沿用
`UNAVAILABLE/TIMEOUT/VALIDATION_FAILED`；云端运行默认 `UNKNOWN`，不连接 TDX。
成功为 `AVAILABLE`。调用方不会因连接故障中断，也不会触发重试或切换来源。
失败响应如已收到仍携带 byte-exact raw 和 hash；没有响应时二者为 null。
不返回上次价格。默认 transport 仅在 Windows 允许执行，Linux 离线测试使用注入 mock。
本机 endpoint 仅允许数字 loopback、HTTP、根路径，无用户信息或 query；不使用代理。
`health_check` 只做 HEAD HTTP 探针，不抓行情，连接/读取各 2.5 秒。

## 配置

所有配置从进程环境读取。凭据由操作者在 Git 仓库之外提供；本文不展示任何凭据值。

| 环境变量 | 默认值 / 约束 |
|---|---|
| TDX_LOCAL_ENDPOINT | `http://127.0.0.1:17709/`；仅数字 loopback |
| TDX_CONNECT_TIMEOUT | 3 秒，正有限数 |
| TDX_READ_TIMEOUT | 10 秒，正有限数；按本任务 v1.5 执行 |
| REALTIME_POLL_SECONDS | 60 秒，至少 30 秒；Windows 本地 marker 限制自动采集间隔 |
| REALTIME_STALE_AFTER_SECONDS | 300 秒，正有限数；后续开盘实测调优 |
| TDX_INGRESS_URL | Windows 必填；HTTPS，禁止用户名、query、fragment；HTTP 仅可用于数字 loopback 本地参考服务 |
| TDX_INGRESS_TOKEN | 双端必填；非空 ASCII、无空白；只经 Authorization header 发送 |
| TDX_VERIFIED_UNITS_JSON | `{}`；只允许已核实的 price/volume/amount 声明，未核实保留空字典 |
| TDX_SKIP_CLOSED_SESSIONS | `0`；`1` 时只在北京时间工作日 09:30–11:30、13:00–15:00 采集 |
| REALTIME_MISSING_AFTER_SECONDS | 云端默认 `2 × REALTIME_POLL_SECONDS`；收不到新的已提交包后进入 MISSING |
| TDX_PUSH_STATE_DIR | Windows 默认用户目录下 `tdx-push-state`；必须在仓库外 |
| TDX_OUTBOX_MAX_BYTES | 100 MiB；至少 1 MiB；保留一包空间，达到容量前暂停新采集并尝试补传 |
| TDX_INGRESS_STATE_DIR | 参考服务默认用户目录下 `tdx-ingress-state`；必须在仓库外 |
| TDX_INGRESS_BIND / TDX_INGRESS_PORT | `127.0.0.1` / `8080`，由操作者部署 HTTPS 前置入口 |

`TDX_VERIFIED_UNITS_JSON` 中价格仅接受 `元/yuan/cny`，量接受
`股/share/shares/手/lot/lots`，金额接受 `元/yuan/cny/万元/万人民币/10k yuan`。
单位声明是经实测核实后的配置输入，不能据数值大小判断单位。
Normalizer 消费该声明，保存字段五元组与转换规则；未知单位没有 canonical 输出。
本卡尚未确认真实 TDX 单位，默认不猜；测试转换用的是合成单位声明。

时间窗口仅按星期和北京时间判断，未接入交易日历；国定假日不能自动识别。
操作者可用 Task Scheduler 日历停止节假日采集。停采期间云端按超时输出 MISSING。
停采与 `--push-only` 不妨碍积压补传。轮询只由 Task Scheduler 触发，脚本没有循环或休眠调度。

## Windows Task Scheduler 配置

1. 在 qi 本机安装 Python，确认 `TdxW.exe` 已运行并登录，TDX HTTP 服务由通达信客户端提供。
2. 将代码放入 Windows checkout；状态目录选在 checkout 外。配置上述环境变量，
   只在 Windows 用户环境或受控进程环境提供 ingestion credential；计划任务参数中不能放凭据。
   修改用户环境后重新登录，确保计划任务进程能读取新的环境。
3. 在“任务计划程序”选择“创建任务”，名称可为 `TDX Realtime Push`。
   使用 qi 的 Windows 用户，选择“仅当用户登录时运行”，不要求管理员权限。
4. “触发器”创建按交易日运行的计划，重复间隔 **1 分钟**；持续时间覆盖交易时段。
   也可全天每分钟运行并使用 `TDX_SKIP_CLOSED_SESSIONS=1`。不要配置低于 30 秒的外部调用。
5. “操作”选择“启动程序”：程序指向 Python 可执行文件；参数
   `-m bridge.windows_tdx_push 601518.SH --collection-mode AUTO`；
   “起始于”指向 checkout 根目录。示例证券代码不是持仓信息。
6. “设置”中选择“如果任务已在运行：不启动新实例”，取消快速失败重试。
   使用相同 state-dir 的 collector 还有 OS 文件锁；进程退出自动释放锁。
7. 手动运行一次任务，查看仓库外 `collector.log` 与 `pending/`，并检查云端确认记录。
   实际 Windows/TDX/HTTPS 验收需操作者在开盘时进行，本任务仅做离线验证。

手动采集：从 checkout 根目录运行
`python -m bridge.windows_tdx_push 601518.SH --collection-mode MANUAL`。
仅恢复发送：`python -m bridge.windows_tdx_push 601518.SH --push-only --batch-size 20`。
MANUAL 信封始终与 AUTO 区分；手动调用不由自动 marker 节流，但同样只能单进程发送。
每个 state-dir 配一只证券；此最小脚本不提供多证券调度器。

退出码：`0` 表示本轮待发送全部确认，或跳过采集且无积压；`1` 表示本轮 TDX MISSING；
`2` 表示 ingress/配置/存储失败、容量暂停，或单批结束仍有积压。
若 TDX 与 ingress 同时不可用，返回 `1`，日志仍记录 PUSH_PENDING 与剩余数。
这是单次执行，不在一次失败后自行重试；下一次 Task Scheduler 调用恢复发送。

## 接收契约、鉴权与幂等

`POST /realtime`，JSON body 最大 1 MiB，Authorization 使用 Bearer ingestion credential。
接收器只暴露该写入接口，没有数据库、AWS 管理能力或 Windows 控制接口。
生产部署需由操作者提供 HTTPS 前置入口，服务使用最低权限 OS 用户与独立状态目录。
凭据可通过更换环境变量并重启 receiver 单独吊销；禁止使用具有 AWS 管理权限的凭据。
参考启动命令为 `python -m bridge.ingress`，本任务不执行服务启动或生产配置。

信封是严格字段集合，包含卡片 R4 的全部 12 字段，以及 transport、provider_status、
realtime_quote_status、snapshot、units、raw_response_b64。
`snapshot_timestamp` 是完成本机请求的采集时钟，不是交易所时钟。
缺快照事件仍上传相同身份字段，snapshot_timestamp/raw hash 可为 null。
原始响应按 bytes 做 SHA-256，再 base64 传输；云端解码核对 hash、重新字段映射并核对 snapshot，
不信任客户端传入的价格，不接收客户端控制的路径。
路径只由合法 run/task/request ID 拼接；未知字段、未来时钟、身份错误、敏感键或凭据内容拒绝入库。

| HTTP 状态 | 确认语义 |
|---|---|
| 200 | `{stored, request_id, packet_hash, raw_response_hash, freshness, evidence_id}` |
| 401 | 无凭据或不匹配；先鉴权再解析 body |
| 409 | `IDEMPOTENCY_CONFLICT`；同 request_id 不同完整信封内容 |
| 422 | 信封、raw hash、字段、时钟或单位声明无效 |
| 503 | 写入失败，无确认；下次发送原包 |

`request_id` 是全局幂等键。信封 hash 覆盖全部字段；同键同包返回首次的完全相同确认，
即使后来已变旧也不改变首次确认。重复不会刷新云端在线心跳。
conflict 必须保留队列文件，并在 collector 日志记录固定 `IDEMPOTENCY_CONFLICT` 事件；
操作者需处理冲突包，不自动覆盖或丢弃。
单进程 receiver 的文件锁防止多个进程写同目录；串行 HTTPServer 防止同进程写入竞态。

## 持久化与故障恢复

Windows 在首次 HTTP 上传前 fsync 原信封到 `pending/`。发送前将原始响应、Normalizer
结果和 EvidenceBuilder 元数据保存到 `evidence/<run>/<task>/<request>/`。
接收器也保存 byte-exact `raw.json` 与不可变 `evidence.json`，随后原子提交一份 receipt。
receipt 同时包含完整信封、结果和第一次确认，是唯一业务生效点；没有单独更新的“最新价格”文件。
写入失败不会返回成功；中途退出留下的 sidecar 会在下一次同包重试时验证并复用。
POSIX 对目录也 fsync；Windows 使用文件 fsync 与原子 replace，不能声称验证过断电硬件持久性。

Windows 只在核对成功确认的 request_id、packet_hash、raw hash 与 stored 后移除 pending 文件。
确认丢失或进程重启会重新发送相同信封，云端返回第一次结果，避免重复生效或永久漏发。
每轮最多 20 包，可设 1–100；按采集时间恢复，扫描用 `heapq.nsmallest`，内存随批大小有界。
默认待发送信封容量上限 100 MiB；满时先停止新采集，留下现有包并报告容量暂停，继续分批恢复。
磁盘写满会失败并返回 2，不能保证保存尚未成功写盘的样本。
已归档 Evidence/receipt 只追加，未实现自动清理；容量规划与长期归档需后续阶段指定。

`IngressStore.current(stock_id)` 按 snapshot_timestamp（MISSING 按 request_timestamp）选择最新事件，
不是按收包顺序。晚到旧包仍保留 raw/evidence，但当前行情不会倒退。
空集合、明确 MISSING、超过 receipt 静默阈值分别输出 MISSING；刚收到但采集已超 300 秒输出 STALE。
计算使用读取时钟，禁止永远保留首次 REALTIME 状态。默认静默阈值 120 秒，PC 关机只能在该时限后被发现。
旧包重新上传不能使离线节点变在线；休市停采也按相同规则显示 MISSING。

采集 freshness 与冻结 canonical 的行情 freshness 是两个语义。
当前协议 provider_timestamp 缺失，canonical 的 quote_timestamp 保持 null、freshness 保持 UNKNOWN，
绝不把采集时间填成行情时间。只有声明单位后才可能得到带 UNKNOWN 的 canonical。
STALE/MISSING 当前视图强制 canonical_data 为空；`usable_for_realtime` 还要求 canonical 行情时间已验证。
当前已验证协议下该标记为 false；可以追溯快照，但不能声称已验证交易所实时性。
模型/页面后续任务必须消费该限制，不能直接使用 raw 或历史 receipt；本任务没有实现模型或页面。

## 红线落实

| 红线 | 落实 |
|---|---|
| R1 | 默认 TDX transport Windows-only；receiver 无出站客户端、无 provider 注册 |
| R2 | 只有 Windows POST 与确认；无远程指令、控制或回调 |
| R3 | 不可达发布 MISSING；静默失联超时；不读取旧包充当实时、不换源 |
| R4 | 严格信封字段集合；source/runtime/transport 与 AUTO/MANUAL 校验 |
| R5 | request_id + 完整包 hash；耐久 receipt；同包首次确认、异包 409 |
| R6 | 环境变量凭据；只写 realtime transport；不记 headers/body/异常文本；拒绝敏感内容 |
| R7 | byte-exact raw.json + SHA-256 + EvidenceBuilder 路径；双端 evidence |
| R8 | 读取时再算 REALTIME/STALE/MISSING；行情时钟 UNKNOWN 独立保留 |
| R9 | Task Scheduler 单次进程；批量有界、容量可见；标准库、无重型服务 |
| R10 | AUTO/MANUAL 进入信封和 receipt，不混淆 |

未实现：真实 Windows 行情/推送实测、生产部署、数据库快照表 schema、TDX 真实单位核实、
数据方时间字段确认、完整交易日历、模型/页面限制实现、长期 Evidence 归档与 #004-R1 底座补审。
本 checkout 未包含卡片所述 `docs/reference/tdx-tq-local-SKILL.md`；协议采用任务卡提供的 Windows 实测内容。
七种故障验证与执行环境原始报错见 `TDX_REALTIME_VERIFICATION.md`。
