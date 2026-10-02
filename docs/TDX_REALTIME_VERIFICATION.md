# Task #005 验证证据与审查交接

任务：`AI-STOCK-V1-TDX-005`，卡片 v1.5，验证日期 2026-10-02。
基线：`84fd8d912c2fc3e30e065aad6d6c5f572e098d68`（main / PR #10）。
工作分支：`codex/v1-tdx-realtime`。
下表版本 V005 指本次实现的 8 个 Python 文件，包括新增测试及最小注册修改，按路径排序，
串联 `path + NUL + file_bytes + NUL` 后的 SHA-256：

```text
V005=2c24829e5692c184027fb17dcfc32db9689290b2863bdec7daf3ac321db79f58
```

精确提交 SHA 与 PR URL 由最终 CODE_REVIEW_PACKAGE_A 提供，避免文档对自身提交 hash 的循环引用。
本证据只证明采集与传输实现的离线行为，不替代 #004-R1 补审或全链路实盘验收。

## 测试执行

```text
env PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover -s tests -v

Ran 79 tests in 1.015s

OK
```

原有 #001/#002/#003/#004 的 32 项测试通过；新增 47 项测试通过。
新测试中 HTTPConnection、urllib opener 和 HTTP handler 的连接对象全部 mock。
测试模块还拦截 socket.socket 与 socket.create_connection，意外真实 TCP 会直接使测试失败。
端到端传输测试将 Windows 原包序列化，经 mock urllib 交给真实 handler 逻辑，再通过真实
Normalizer/EvidenceBuilder/临时文件提交返回 JSON 确认；没有运行真实网络服务。
Windows msvcrt 文件锁分支进行了 mock 验证，未声称在 Windows 上执行过本次实现。
fixture 只采用卡片实测的字段形状，价格和量额均是合成值，不是用户持仓或实测价格。

## §6.C 分类

- **A 类：补充验证证据。** 以下故障注入对应既有 R1–R10；没有引入第四源、数据库写入、模型或页面实现。
- **B 类：新增通过门槛。** 按 qi 已批准的 v1.1 增补执行七种最低结果及逐项证据记录；本次不重新申请授权。
- **C 类：后续实现任务。** 模型结论限制由后续分析/Prompt 实现任务验收；页面展示由 Dashboard 实现任务依据
  `docs/frozen/P3_DASHBOARD_SPEC_V1.0.md`（包括 Mode A 规则）验收。这两类任务依赖本次 current 投影与证据接口，
  各自必须证明 STALE/MISSING/UNKNOWN 不会支持实时结论，且缺失状态明确展示。后续任务 ID 尚未给出，
  本次仅记录依赖与验收门槛，没有创建或声称完成模型/页面任务。

## 七种故障最低预期结果（B 类门槛）

证据均位于 `tests/test_tdx_realtime.py`，表中列出精确测试方法名；实际结果来自上面的 V005 测试运行。

| 故障 | 触发方式 | 预期结果 | 实际结果 | 验证版本 | 证据 |
|---|---|---|---|---|---|
| TDX 退出、行情过期 | mock 连接拒绝/Timeout；将读取/接收时钟推进 301 秒；PC 静默 121 秒 | MISSING/STALE 明确；current 不可用于实时判断；不给旧价格或替代源 | PASS；MISSING/STALE 的 canonical_data=[]、usable_for_realtime=false；重复旧包不能恢复在线状态 | V005 | `test_tdx_exit_and_timeout_are_missing_no_retry`；`test_tdx_exit_masks_previous_snapshot_and_survives_late_replay`；`test_stale_snapshot_blocks_current_realtime_data`；`test_pc_off_no_contact_becomes_missing_without_cache_fallback` |
| 相同内容重复推送 | 同一包收两次，第二次在 400 秒后 | 一份业务 receipt；返回第一次完全相同确认 | PASS；receipt=1；ack 完全相等，首次 freshness 不改写；读取时状态另算 | V005 | `test_duplicate_exact_ack_and_one_effect` |
| 旧数据晚到 | 先接收后采的新包，再接收早 400 秒的旧包 | 当前行情不倒退；旧包 raw/evidence 保留 | PASS；current request_id=new；两份 receipt 和原始证据都存在；旧包确认为 STALE | V005 | `test_old_arrival_keeps_new_quote_and_both_evidence` |
| 同一幂等键不同内容 | 保持 request_id，改 Now；另用例改 collection_mode | 明确冲突，不能静默当重复成功 | PASS；HTTP语义 409/IDEMPOTENCY_CONFLICT；receipt 仍1；Windows pending 保留并记录冲突事件 | V005 | `test_same_key_different_contents_reports_conflict`；`test_same_key_changed_mode_is_conflict_not_duplicate`；`test_cloud_conflict_preserves_pending` |
| 确认丢失、进程重启 | 云端提交后 mock 丢失 ack；丢弃实例并从同目录重建 collector 和 ingress | 原包恢复发送，首次生效结果可重取，既不重复也不漏发 | PASS；重建前 pending=1；重建后发送 byte-equivalent 原包、pending=0、receipt=1；另测 receipt 写失败后重试只提交一次 | V005 | `test_ack_loss_and_process_restart_drain_exact_original_once`；`test_storage_failure_no_ack_then_retry_commits_once` |
| 单位错误、字段缺失 | 注入 cents、不声明单位、Now=null、错误五档/非数值；声明合成 lot/万元 转换规则 | 拒绝或明确不适用；不猜单位、补零或继续计算；保留原始证据 | PASS；cents 拒绝422；单位未知无 canonical 且验证FAIL；缺 Now 为MISSING、raw 原样保留；已声明 lot×100、万元×10000 且有规则 | V005 | `test_units_missing_not_guessed_and_wrong_units_rejected`；`test_required_field_missing_preserves_raw_evidence_without_canonical`；`test_malformed_missing_nonnumeric_and_invalid_book`；`test_raw_byte_evidence_hash_and_normalization_rules` |
| 断线后积压补传 | 5 包入队，mock 断线；恢复后每批2包；另设1MiB容量保护 | 分批恢复；剩余可见；有界运行、不丢原信封；满时停止采集并保留现有包 | PASS；断线剩余5；恢复3→1→0；最终5份receipt；日志有剩余数且无凭据；容量限制前跳过新TDX调用并恢复旧包 | V005 | `test_disconnect_backlog_bounded_visible_and_no_drop`；`test_outbox_capacity_pauses_collection_but_recovers_backlog` |

第一项只验收采集与传输提供的实际限制（current 清空实时 canonical、可用性为 false）。
对模型结论的使用限制和页面提示属于 C 类，不能据此宣称整条分析链路通过。

## 其他强制测试证据

| 范围 | 测试 |
|---|---|
| JSON-RPC 2.0、方法、stock_code、超时 | `test_exact_rpc_request_and_timeouts`；`test_http_post_header_and_split_deadlines_mocked` |
| R1/R2 无云端取数与反向调用 | `test_cloud_runtime_never_connects`；`test_receiver_has_no_network_client_provider_or_control_path` |
| 只读方法 allowlist | `test_provider_rpc_method_allowlist_is_snapshot_only` |
| R4 信封字段 | `test_required_envelope_fields_and_modes`；`test_tampered_snapshot_raw_hash_identity_future_and_extra_fields_rejected` |
| R6 鉴权与凭据隔离 | `test_auth_missing_wrong_token_and_no_persistence`；`test_sensitive_material_rejected_before_evidence`；`test_http_receiver_auth_parse_limits_and_fixed_error_bodies`；`test_run_exit_codes_missing_and_unreachable` |
| R7 byte-exact raw + Builder | `test_raw_byte_evidence_hash_and_normalization_rules`；`test_rpc_identity_failure_remains_missing_with_raw_evidence` |
| R8 阈值边界与 MISSING | `test_freshness_boundary_and_missing`；七种故障表第一项 |
| R9 单进程、最低频率 | `test_process_lock_prevents_overlapping_collectors_and_releases`；`test_windows_lock_branch_uses_standard_library_byte_lock`；`test_throttle_minimum_and_closed_session_do_not_poll` |
| R10 手动/自动与恢复 | `test_manual_mode_and_push_only_recovery`；`test_required_envelope_fields_and_modes` |
| 离线完整传输路径 | `test_offline_mocked_full_wire_path` |

## Execution Environment

git 2.34.1、gh 2.102.0 可用；远端 origin 与任务绑定一致；工作区开工时为空；
分支 HEAD、main、origin/main 同为上述基线，联网 `git ls-remote origin refs/heads/main` 再次确认一致。
初次沙箱检查无法解析 github.com，gh 因同样的网络限制报告登录失效；切换到允许网络的
执行环境后 `gh auth status` 成功确认已登录 zzttcc3321-create，HTTPS 检查返回 HTTP/2 200。
没有改写 origin、凭据、全局模型或其他全局配置。

按卡片要求保留失败步骤的完整报错原文（后续已纠正，不跳过）：

首次 `gh auth status`：

```text
github.com
  X Failed to log in to github.com account zzttcc3321-create (/home/ubuntu/.config/gh/hosts.yml)
  - Active account: true
  - The token in /home/ubuntu/.config/gh/hosts.yml is invalid.
  - To re-authenticate, run: gh auth refresh -h github.com
  - To forget about this account, run: gh auth logout -h github.com -u zzttcc3321-create
```

首次 `curl -I --connect-timeout 10 https://github.com:443`：

```text
  % Total    % Received % Xferd  Average Speed   Time    Time     Time  Current
                                 Dload  Upload   Total   Spent    Left  Speed
  0     0    0     0    0     0      0      0 --:--:-- --:--:-- --:--:--     0curl: (6) Could not resolve host: github.com
```

首次读取猜测的历史 provider 文件名（已改读实际的 providers/tushare.py）：

```text
cat: providers/tushare_daily.py: No such file or directory
```

首次测试命令使用 `python`（环境提供 python3，随即用 python3 完成基线与全部验证）：

```text
/bin/bash: line 1: python: command not found
```

首次 git add 被沙箱的 .git 只读权限阻止，随后使用任务已授权的 Git 写入执行环境继续：

```text
fatal: Unable to create '/home/ubuntu/work/AI-STOCK-SYSTEM-V1/.git/index.lock': Read-only file system
```

## Boundary Check 与审查包范围

新增文件：

- `providers/tdx_realtime.py`
- `bridge/__init__.py`
- `bridge/contracts.py`
- `bridge/pipeline.py`
- `bridge/ingress.py`
- `bridge/windows_tdx_push.py`
- `tests/test_tdx_realtime.py`
- `docs/TDX_REALTIME_BRIDGE.md`
- `docs/TDX_REALTIME_VERIFICATION.md`

修改文件仅 `providers/__init__.py`，新增 TdxLocalHttpProvider export。
总计 10 个文件，9 新增、1 修改，全部属于 §4 白名单。
数据库/AWS生产环境/secrets 均未修改；未修改 main、冻结契约、data_sources、数据库 schema。
提交前执行 git diff --check（包含 staged 检查），并检查完整 diff；没有真实凭据或认证响应入 Git。
测试中的 ingestion 占位符仅 `TEST_TOKEN_ONLY` 及明确的错误测试占位符；不使用真实值。

CODE_REVIEW_PACKAGE_B 留给 GPT 独立审查；本次不自签 APPROVE。
实现说明与已知限制见 `TDX_REALTIME_BRIDGE.md`。
