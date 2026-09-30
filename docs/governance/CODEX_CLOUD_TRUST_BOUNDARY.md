# Codex Cloud 信任边界

- STATUS: DRAFT
- VERSION: 0.1
- 适用范围：AI 股票系统 V1 中所有 Codex Cloud 任务

## 1. 工程边界

- Codex Cloud 仅用于工程工作：代码、模拟测试、文档和 CI。
- Codex Cloud **绝不接触真实生产数据平面**：不得访问 AWS 生产环境、生产数据库，不得执行真实交易。

## 2. Secrets 隔离

以下信息不得进入 Codex Cloud 的任务描述、文件或日志：

- 真实持仓数据
- Tushare Token
- AWS 凭证
- 任何私钥

## 3. 数据源边界

- 仅允许使用三个冻结数据源：`TUSHARE_MCP`、`TDX_OFFICIAL_LOCAL_HTTP`、`WORKBUDDY_TDX_MCP`。
- 禁止使用腾讯和 Akshare 数据源。

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

## 6. 网络访问

- 默认关闭网络访问。
- 仅当任务明确要求网络访问时方可开启，且不得因此突破上述生产、Secrets 或数据源边界。
