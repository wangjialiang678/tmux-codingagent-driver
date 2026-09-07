#!/usr/bin/env bash
# 可靠的 tcd worktree 完成信号：轮询分支出现「超出 base 的新 commit」。
# 不依赖 tcd 的 state/status（会抖动、被 kill 的 job 误标 failed）。
# 用法: tcd-await-commit.sh <worktree_path> <base_sha> [max_polls=360] [interval_s=20]
# 退出 0 = 检测到新 commit(打印 COMMITTED <sha>)；退出 2 = 超时。
set -u
WT="${1:?worktree path}"; BASE="${2:?base sha}"; MAX="${3:-360}"; IV="${4:-20}"
i=0
while [ "$i" -lt "$MAX" ]; do
  if [ -e "$WT/.git" ]; then
    h=$(git -C "$WT" rev-parse --short HEAD 2>/dev/null || true)
    if [ -n "$h" ] && [ "$h" != "$BASE" ]; then echo "COMMITTED $h (polls=$i)"; exit 0; fi
  fi
  i=$((i+1)); sleep "$IV"
done
echo "TIMEOUT no new commit beyond $BASE after $((MAX*IV))s"; exit 2
