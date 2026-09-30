# Codex Cloud 信任边界

- STATUS: DRAFT
- VERSION: 0.2
- 适用范围：AI 股票系统 V1 中所有 Codex Cloud 任务

## 1. 工程边界

- Codex Cloud 仅用于工程工作：代码、模拟测试、文档和 CI。
- Codex Cloud **绝不接触真实生产数据平面**：不得访问 AWS 生产环境、生产数据库，不得执行真实交易。
- Git 仓库边界：AI-STOCK-SYSTEM-V1 是代码版本的唯一事实来源。Codex Cloud 的所有变更必须通过 Git 提交，任何生产变更不得绕过 Git。
- Provider 层边界：Codex 不得为工程便利在 Provider 层添加业务逻辑或判断。

## 2. Secrets 隔离

以下信息不得进入 Codex Cloud 的任务描述、文件或日志：

- 真实持仓数据
- Tushare Token
- AWS 凭证
- 任何私钥
- 账户信息、生产路径及其他敏感信息

Codex 任务描述不得包含上述真实持仓、账户信息、Token、生产路径或其他敏感信息。

## 3. 数据源边界

- 仅允许使用三个冻结数据源：`TUSHARE_MCP`、`TDX_OFFICIAL_LOCAL_HTTP`、`WORKBUDDY_TDX_MCP`。
- 禁止使用腾讯和 Akshare 数据源。
- Codex Cloud 不得修改历史 Evidence 记录或生产 RAW 数据。

## 4. 旧代码隔离

- 旧探索性代码仅保留在 Windows `D:` 盘。
- 旧探索性代码不得提交到本仓库。

## 5. 代码变更审查链

任何代码变更必须依次经过：

1. Codex 填写代码审查协议 A 部分（代码事实）。
2. GPT 审查并填写 B 部分（规则判断）。
3. 阿七在 AWS 验证。
4. 重大变更由 qi 批准。

不得跳过代码审查或 AWS 验证；发现偏差时按偏差处理流程执行。

- 代码审查结果必须存入仓库的 `docs/evidence` 或指定审查记录目录，形成可追溯记录。
- Codex Cloud 不得自行批准架构变更、冻结变更或生产发布。

## 6. 网络访问

- 默认关闭网络访问。
- 仅当任务明确要求网络访问时方可开启，且不得因此突破上述生产、Secrets 或数据源边界。
- 网络访问审批链：申请网络访问 → GPT 判断必要性 → 涉及外部数据时由 qi 批准 → 临时开启 → 任务结束时关闭。
