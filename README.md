# AI-STOCK-SYSTEM-V1

AI 股票投研辅助系统 V1 主仓库。本仓库是唯一的版本事实源（single source of truth）。

- `docs/frozen/` — 已冻结的设计文档（P1/P2/P3），只读，不直接修改
- `docs/governance/` — 协作治理文件（任务生命周期、代码审查、偏差处理）
- `docs/decisions/` — 决策记录

安全红线：真实持仓、token、密钥永不进仓库。详见 `.gitignore`（Gate 0）。

Dashboard 数据库写入门禁：启动前用 `REAL_DB_PROTECTED_PATHS` 配置受保护数据库文件，
多个路径以逗号分隔；路径去除首尾空白、展开 `~` 并解析为绝对路径（含符号链接）后匹配。
持久化层每次写事务都会检查连接中的实际数据库文件；命中保护集合时默认拒绝，
只有 `REAL_DB_WRITE=YES` 或 `APPLY_REAL_DB=YES` 才允许写入，撤销授权后下一次写入立即被拒绝。
未配置或未命中保护集合的普通运行时库保持原行为；`readonly=True` 打开和读取不受影响。
配置与授权仅通过运行环境传入，不要把真实数据库路径或数据提交到仓库。
