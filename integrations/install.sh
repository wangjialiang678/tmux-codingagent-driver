#!/usr/bin/env bash
# 把 codex-worker 技能装进 Claude Code。
# 用法：bash integrations/install.sh
#
# 默认用软链接：装完之后本仓 git pull / 本地改动即时生效，不存在"本机副本变旧"的问题。
set -euo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SRC="$REPO/integrations/claude-code/SKILL.md"
DIR="$HOME/.claude/skills/codex-worker"

[ -f "$SRC" ] || { echo "找不到 $SRC"; exit 1; }
mkdir -p "$DIR"

# 已存在真实文件且与仓库不同 → 先备份，避免吞掉本机上未回流的改动
if [ -f "$DIR/SKILL.md" ] && [ ! -L "$DIR/SKILL.md" ] && ! cmp -s "$DIR/SKILL.md" "$SRC"; then
  backup="$DIR/SKILL.md.bak-$(date +%Y%m%d%H%M%S)"
  cp "$DIR/SKILL.md" "$backup"
  echo "⚠ 本机副本与仓库不一致，已备份到 $backup"
  echo "  若那边有你想保留的改动，回流到 $SRC 再提交。"
fi

rm -f "$DIR/SKILL.md"
if ln -s "$SRC" "$DIR/SKILL.md" 2>/dev/null; then
  echo "✓ codex-worker 技能已软链接到 $DIR/SKILL.md"
  echo "  指向：${SRC}（仓库即唯一版本源，更新自动生效）"
else
  cp "$SRC" "$DIR/SKILL.md"
  echo "✓ codex-worker 技能已复制到 $DIR/SKILL.md"
  echo "  本文件系统不支持软链接：仓库更新后需重跑本脚本。"
fi

echo
echo "新开一个 Claude Code 会话，说「派 Codex 做 X」即可触发。"
