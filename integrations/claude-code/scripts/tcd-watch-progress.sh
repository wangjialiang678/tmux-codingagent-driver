#!/usr/bin/env bash
# 进度可见 + 可靠完成的 tcd worktree 守护。
# 每个心跳窗口回来一次(带 codex 活动快照供 DRIVER 报给用户)，或检测到分支新 commit 即结束。
# 比纯 tcd-await-commit 多了"心跳可见性"：两次提交之间不再是盲区。
# 用法: tcd-watch-progress.sh <worktree> <base_sha> <job_id> [heartbeat_s=90] [interval_s=18]
# exit 0 = COMMITTED <sha>(已完成, 去验收); exit 3 = HEARTBEAT(未完成, 附活动, DRIVER 应 relaunch)
set -u
WT="${1:?worktree}"; BASE="${2:?base sha}"; JOB="${3:?job id}"; HB="${4:-90}"; IV="${5:-18}"
elapsed=0
while [ "$elapsed" -lt "$HB" ]; do
  if [ -e "$WT/.git" ]; then
    h=$(git -C "$WT" rev-parse --short HEAD 2>/dev/null || true)
    if [ -n "$h" ] && [ "$h" != "$BASE" ]; then echo "COMMITTED $h"; exit 0; fi
  fi
  sleep "$IV"; elapsed=$((elapsed+IV))
done
echo "HEARTBEAT no-commit-after-${HB}s | codex 活动快照:"
tcd check "$JOB" --json 2>/dev/null | python3 -c "import sys,json
d=json.load(sys.stdin)
print('  state=',d.get('state'),'elapsed_s=',d.get('elapsed_s'))
for a in (d.get('activity') or [])[-6:]: print('  •',a)" 2>/dev/null || echo "  (tcd 活动获取失败, 但 codex 仍在跑)"
exit 3
