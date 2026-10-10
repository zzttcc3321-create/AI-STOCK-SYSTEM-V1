# 联合回归报告（2026-10-10 v2，返修后）

## 返修说明
上一版分支建错位置，缺少 H-R2 代码。本版从正确的 worktree 重建，
经总指挥 Git Blob 核验方法确认四文件均与已审查版本一致。

## 组合范围
- 基线：main=cfedc92408c52b8974b8ee7d59526dfd42c78a49
- H-R2 代码：55771aebcddd819df2175efedc400f774f8e4049（a91d052）
- H-I1 代码：37a6550 + 505d05d + 2945a3b（c1b6f2f）
- HEAD：c1b6f2f5eabd560691e98bbaa1bdce298a256b07
- 隔离 worktree 执行，git status 干净，无关变更：无

## 变更文件（4 个，228 行）
- bridge/contracts.py（H-R2：math.isfinite 严格数值转换）
- providers/tdx_information.py（H-I1：unclassified_text_seen）
- tests/test_tdx_realtime.py（51 个测试方法，H-R2 版）
- tests/test_tdx_information.py（112 个测试方法，H-I1 版）

## 测试结果
- 命令：PYTHONPATH=tests:. python3 -m unittest discover -s tests
- 结果：Ran 824 tests in 349.569s，OK，EXIT_CODE=0
- git diff --check：PASS

## 原始日志
docs/joint_regression_824_v2.txt（含 Git HEAD 记录）
