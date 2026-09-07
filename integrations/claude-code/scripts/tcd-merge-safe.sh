#!/usr/bin/env bash
# 安全合并 tcd worktree 工作：若 worktree「干完活但漏 commit」，先代提交，再合并分支。
# 绝不在合并/清理前丢工（错杀防护）。在 tcd kill 之前调用。
# 用法: tcd-merge-safe.sh <main_repo> <worktree_path> <branch> <base_sha> ["commit msg"]
set -u
REPO="${1:?main repo}"; WT="${2:?worktree}"; BR="${3:?branch}"; BASE="${4:?base sha}"
MSG="${5:-auto: secure uncommitted worktree work before merge}"
head=$(git -C "$WT" rev-parse --short HEAD 2>/dev/null || echo "")
if [ -z "$head" ]; then echo "ERR: worktree 不可读: $WT（若已被清理，尝试 git -C $REPO merge $BR 救回已提交分支）"; exit 1; fi
if [ "$head" = "$BASE" ]; then
  if [ -n "$(git -C "$WT" status --porcelain)" ]; then
    echo "worktree 有未提交改动且无新 commit → DRIVER 代提交（防丢工）…"
    git -C "$WT" add -A && git -C "$WT" commit -q -m "$MSG" || { echo "ERR: 代提交失败"; exit 1; }
  else
    echo "WARN: 分支无新 commit 且工作区干净 —— 该 job 可能没产出。请先人工核查，未自动合并。"; exit 3
  fi
fi
echo "merge $BR -> $(git -C "$REPO" rev-parse --abbrev-ref HEAD)"
git -C "$REPO" merge --no-edit "$BR"
