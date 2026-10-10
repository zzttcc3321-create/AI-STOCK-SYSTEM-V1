# CODE_REVIEW_PACKAGE Part A｜AI-STOCK-V1-014-E2-HR2-TDX-NUMERIC

TASK_ID=AI-STOCK-V1-014-E2-HR2-TDX-NUMERIC
（#014-E2-HR2｜TDX 数值字符串适配）
基线 main=cfedc92408c52b8974b8ee7d59526dfd42c78a49
分支 codex/014-e2-hr2-tdx-numeric → commit 55771aebcddd819df2175efedc400f774f8e4049（已 push）
执行模型：GPT-6.1 Sol + High（dev-box）

## 1. 范围（Scope）

仅改 2 个文件：
- bridge/contracts.py（+12/-2）：map_snapshot() 的 numeric() 增加严格数值转换
- tests/test_tdx_realtime.py（+94/-1）：新增字符串快照 fixture + 4 个测试

## 2. Diff 要点

在 `map_snapshot()` 的 `numeric()` 函数中：
- bool 类型先排除（raise ValueError）
- 字符串：strip → 正则 `^[+-]?(\d+(\.\d+)?|\.\d+)([eE][+-]?\d+)?$` 词法检查
  → `float()` → 必须有限且非负
- 纯整数词法（如 '121746'）转 int 保真；小数/科学计数法转 float
- 拒绝：bool、空串、NaN、Infinity、负数、千位分隔符、非法词法
- 原 int/float 行为不变；SCALARS/BOOK 全数值项走同一转换
- 五档恰好 5 项检查、`ErrorId == "0"` 字符串判断、raw bytes/Hash/Evidence 均未动
- 转换后的 snapshot 使用 JSON 数字，由 validate_packet() 重新对照原始响应校验

## 3. 测试

- tests/test_tdx_realtime.py：51/51 通过
  - 全字符串快照正例：映射成功且值等于 JSON 数字
  - 反例：空串/'NaN'/'Infinity'/'-1'/'1,234'/'0x10'/bool True 全部判非法
  - '  4.2  ' strip 后接受
  - 全链路：raw bytes/hash/evidence 不变且 validate_packet() 通过
- 全量回归：813/813 通过，0 失败（718s）
- git diff --check：通过
- 测试日志：docs/hr2_full_test_813.log

## 4. 静态分析

- 无新增依赖；正则 + math.isfinite 双重把关
- 仅 2 文件变更，无新文件

## 5. 影响与风险

- 影响面：仅 TDX 实时快照数值解析路径（realtime_quote provider 边界）
- 其他 provider 不受影响
- 风险：低。转换规则比原先更严格（原先直接拒绝字符串，现接受合法子集），
  拒绝集合是原先拒绝集合的超集。原始响应、Hash、Evidence 链完整保留。

## 6. 红线核对

- 未 push main、未建 PR、未合并；未改 git remote origin
- 未碰真实数据库；无凭据/持仓/Tokens 进 Git
- 未部署、未恢复 E2/E3/E7
- H-I1/B1 范围未碰

## 7. 阿七独立核验

- 分支 `codex/014-e2-hr2-tdx-numeric` 存在于远端，HEAD=55771ae
- `git diff cfedc92..origin/codex/014-e2-hr2-tdx-numeric --stat` 确认仅 2 文件
- 亲自检查 bridge/contracts.py 的 diff，确认与裁决要求一致：
  bool 排除、正则词法、有限非负、int 保真、拒绝集合完整
- 未亲自重跑全量测试（采信执行方 813/813 日志）

结论：READY_FOR_REVIEW，交 GPT Review B。
