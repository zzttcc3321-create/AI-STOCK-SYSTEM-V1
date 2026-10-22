#!/bin/bash
# dev-box 上运行 Codex 分析的启动脚本（由 dual_channel.py 上传调用）
# 用法: run_codex.sh <prompt文件> <结果文件> <完成标记文件>
export PATH=/usr/local/node22/bin:$HOME/.npm-global/bin:$PATH
PROMPT_FILE="$1"
RESULT_FILE="$2"
DONE_FILE="$3"
cd ~
codex exec --model gpt-6.1-sol --config 'model_reasoning_effort="high"' \
  --skip-git-repo-check "$(cat "$PROMPT_FILE")" > "$RESULT_FILE" 2>&1
echo done > "$DONE_FILE"
