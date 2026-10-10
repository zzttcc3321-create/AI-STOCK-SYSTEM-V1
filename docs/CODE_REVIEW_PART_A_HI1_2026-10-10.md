# CODE_REVIEW_PACKAGE Part A｜AI-STOCK-V1-014-E2-HI1-NODATA-CLASSIFICATION

TASK_ID=AI-STOCK-V1-014-E2-HI1-NODATA-CLASSIFICATION
（#014-E2-HI1｜Wenda 明确无数据通知重分类）
基线 main=cfedc92408c52b8974b8ee7d59526dfd42c78a49
分支 codex/014-e2-hi1-nodata-classification
- commit 37a65508b234a80f9797515b43836c754428b215（初版，已 push）
- commit 505d05d（第 6 条混合场景修复）
- commit 2945a3b（HI1-B02 复合场景修复）
- commit 2bfaa8c（Part A 更新）← 当前 HEAD
执行模型：GPT-6.1 Sol + High（dev-box）

## 1. 范围（Scope）

仅改 2 个文件：
- providers/tdx_information.py：
  - 初版（+9/-1）：_data() 第 470–474 行、第 494–496 行
  - 修复（+11/-2）：新增 unclassified_text_seen 标记及相关门控
- tests/test_tdx_information.py：
  - 初版（+76）：第 848–923 行，新增 9 个测试
  - 修复（+11）：新增第 10 个测试（混合场景两种顺序）

## 2. Diff 要点

### 初版（37a6550）
(1) 在 _data() 的文本块 except json.JSONDecodeError 分支中，
    对 tool in ("wenda_notice_query", "wenda_news_query")：
    - 文本所有非空行 strip 后都被 _no_data() 全匹配 → empty_notice=True，
      continue（不产生业务数据）
    - 否则保持原有行为（continue 跳过，最终 fail-closed）
(2) fail-closed 门 `if content and not values` 增加 `and not empty_notice`
    及 wenda+isError 豁免条件，避免空通知被误判 VALIDATION_FAILED。
(3) _no_data() 本体未改；normalizer/information.py 未动；
    冻结合同、Provider 状态枚举、Schema、Migration 未动。

### 修复（505d05d + 2945a3b，总指挥裁决第 6 条及 HI1-B02）
(4) 新增 `unclassified_text_seen` 标记：wenda 文本块若含非空行但未全匹配
    _no_data()（即未知散文），则标记为 True，使任何空通知声明失效。
(5) fail-closed 门改为 `not (empty_notice and not unclassified_text_seen)`；
    isError 路径改为 `empty_notice and not unclassified_text_seen and not data`。
(6) 效果：空通知与未知散文混合（两种块顺序）→ VALIDATION_FAILED，
    不得误报空；纯空通知 → NOT_AVAILABLE 不变；纯散文 → VALIDATION_FAILED 不变。

## 3. 行为矩阵（已用测试锁定）

| 场景 | 工具 | 结果 |
|---|---|---|
| 文本"暂无相关数据" | wenda_notice_query/news_query | NOT_AVAILABLE |
| 文本"no data available" | wenda_news_query | NOT_AVAILABLE |
| 普通散文（不匹配 _no_data） | wenda_* | VALIDATION_FAILED（维持） |
| 正文偶然出现"暂无数据"（子串/混合行） | wenda_* | VALIDATION_FAILED |
| 空通知 + 未知散文混合（两种顺序） | wenda_* | VALIDATION_FAILED（第 6 条修复） |
| 空通知 + isError=True | wenda_* | NOT_AVAILABLE |
| 空通知 + 业务数据 + isError=True | wenda_* | UNAVAILABLE（不被掩盖） |
| 空通知 + ok=False/坏 JSON | wenda_* | UNAVAILABLE / VALIDATION_FAILED（不被掩盖） |
| tdx_ai_listening 现有行为 | — | 不变 |

状态流转：空通知 → _data 返回 [] → fetch() 判 NOT_AVAILABLE；
isError=True+空通知 → 尾部已有逻辑判 NOT_AVAILABLE。

## 4. 静态分析

- git diff --check：通过
- 仅 2 文件，无新增依赖，无新文件

## 5. 测试

- 初版新增 9 个测试：9/9 通过
- 修复新增第 10 个测试 `test_wenda_empty_notice_mixed_with_unknown_prose_is_not_misreported`：
  两种块顺序均判 VALIDATION_FAILED，通过
- B02 新增第 11 个测试 `test_wenda_empty_json_with_unknown_prose_is_not_misreported`：
  空 JSON + 未知散文混合（三种块顺序）均判 VALIDATION_FAILED，通过
- tests/test_tdx_information.py 定向：112/112 通过
- 全量回归（unittest discover）：820/820 通过，0 失败 0 错误（351s）
- git diff --check：通过
- 测试日志：docs/hi1_b02_regression_820.log
- 注：环境无 pytest（包源 DNS 失败无法安装），用 unittest 执行，
  与仓库既有测试框架一致

## 6. 影响与风险

- 影响面：仅 WORKBUDDY_TDX_MCP 的 wenda_notice_query/wenda_news_query
  两个工具的纯文本空通知路径；financial/research/macro 等其他工具不受影响。
- 风险：_no_data() 是全匹配正则，误判为"空通知"的概率低；
  即使误判，结果也只是 NOT_AVAILABLE（记录、不补填），不会污染业务数据。
- 原始响应、Hash、Evidence、错误状态可追溯性未改动。

## 7. 红线核对

- 未 push main、未建 PR、未合并；未改 git remote origin
- 未碰真实数据库；无凭据/持仓/Tokens 进 Git
- 未部署、未恢复 E2/E3/E7

结论：READY_FOR_REVIEW，交 GPT Review B。
