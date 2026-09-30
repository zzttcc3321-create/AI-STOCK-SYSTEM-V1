# 任务生命周期规范 TASK_LIFECYCLE V1.0

- STATUS: FROZEN
- VERSION: 1.0
- 适用范围：AI股票系统 V1 所有工程任务

## 状态定义

1. **TASK_CREATED** —— GPT 创建 Task Card，阿七登记进 TASK_STATUS.md
2. **TASK_ACCEPTED** —— Codex 确认接收（经 qi 中转）；有疑问先澄清，不带病开工
3. **TASK_RUNNING** —— Codex 执行中；阿七跟踪进度
4. **CODE_REVIEW** —— Codex 提交 CODE_REVIEW_PACKAGE（A 部分）→ GPT 填写 B 部分，判 PASS / PASS_WITH_CHANGE / BLOCK
5. **AWS_VERIFY** —— 阿七在 AWS 真机验证
6. **DONE** —— GPT 确认完成；重大任务需 qi 确认
   └— **DEVIATION_REVIEW** —— 任何环节发现偏差即进入，按 DEVIATION_PROCESS.md 处理

## 流转规则

- 必须按顺序流转，不许跳过 CODE_REVIEW 和 AWS_VERIFY
- 每次状态变更，阿七在 TASK_STATUS.md 记录：时间、经手人、结论
- A/B 两部分必须绑定同一 TASK_ID
- Codex 不得自行扩大任务范围；需变更走 Change Request：Codex 提出 → GPT 判断 → 必要时 qi 批准
