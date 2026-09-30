# DECISION_LOG


| 日期 | 决策 | 拍板人 |
|---|---|---|
| 2026-09-29 | 正式数据源只留三源（TUSHARE_MCP / TDX_OFFICIAL_LOCAL_HTTP / WORKBUDDY_TDX_MCP），删除 Tencent、Akshare；2GB 轻量优先 | qi |
| 2026-09-30 | P1 数据库迁移完成（COMPATIBILITY_FIRST，Windows stock_analysis.db 为唯一事实源） | GPT 评审 + qi 批准 |
| 2026-10-01 | 分工定版：qi=决策人，GPT=总指挥+总监察，Codex=执行层，阿七=项目联络人 | qi |
| 2026-10-01 | V2.0 协作模式冻结；只建一个主仓库 AI-STOCK-SYSTEM-V1，不建归档仓库 | qi |
| 2026-10-01 | P2 三份设计冻结（Provider / Normalizer / Evidence Pipeline V1.0）+ 8 条系统铁律 | qi |
| 2026-10-01 | P3.1 选 B：证据不足时允许低置信度条件式建议，强制人工确认（PENDING→CONFIRMED/REJECTED），AI 永不下单 | qi |
| 2026-10-01 | P3.1 人工确认保持三项，删掉第 4 项"数据源有效性确认"（属过度确认，状态在系统健康页展示） | qi |
| 2026-10-01 | P3 三份 V1.0 冻结；P3 闭环 | qi |
| 2026-10-01 | CODEX_CLOUD_TRUST_BOUNDARY_V0.2 已通过 GPT CODE_REVIEW_PROTOCOL Part B 审查（FINAL_REVIEW=PASS），PR #1 已合并 | GPT 评审 + qi 批准 |
