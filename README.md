# AI-STOCK-SYSTEM-V1

AI 股票投研辅助系统 V1 主仓库。本仓库是唯一的版本事实源（single source of truth）。

- `docs/frozen/` — 已冻结的设计文档（P1/P2/P3），只读，不直接修改
- `docs/governance/` — 协作治理文件（任务生命周期、代码审查、偏差处理）
- `docs/decisions/` — 决策记录

安全红线：真实持仓、token、密钥永不进仓库。详见 `.gitignore`（Gate 0）。

Dashboard 数据库写入门禁：启动前用 `REAL_DB_PROTECTED_PATHS` 配置受保护数据库文件，
多个路径以逗号分隔；路径去除首尾空白、展开 `~` 并解析为绝对路径（含符号链接）后匹配。
持久化层每次运行时写事务都会检查连接中的实际数据库文件；临时库（由
`is_temporary_database()` 判定）直接放行。非临时库采用 fail-closed：未配置保护路径
（含空值或仅空白、逗号）时拒绝写入并报 `REAL_DB_PROTECTED_PATHS_UNCONFIGURED`。
已配置且命中保护集合时，日常写入的唯一授权是 `REAL_DB_WRITE=YES`，
否则报 `REAL_DB_WRITE_NOT_AUTHORIZED`；撤销授权后下一次写入立即被拒绝。
已配置但未命中的普通运行时库允许写入；`readonly=True` 打开和纯读不受影响。
`APPLY_REAL_DB=YES` 仅用于 `apply_migration()`：所有非临时库迁移均须此授权，
独立于运行时保护路径配置；`REAL_DB_WRITE=YES` 不能授权迁移，
`APPLY_REAL_DB=YES` 也不能授权日常写入。临时库迁移始终放行。
配置与授权仅通过运行环境传入，不要把真实数据库路径或数据提交到仓库。
