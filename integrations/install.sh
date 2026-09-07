#!/usr/bin/env bash
# 把 codex-worker 技能装进所有支持的 agent 客户端。
# 用法：bash integrations/install.sh            # 装到检测到的全部客户端
#      bash integrations/install.sh claude     # 只装 Claude Code
#      bash integrations/install.sh workbuddy  # 只装 WorkBuddy
#
# 默认用软链接：装完之后本仓 git pull / 本地改动即时生效，不存在"本机副本变旧"的问题。
# （2026-09-07 教训：WorkBuddy 侧是 7 月的复制副本，落后仓库两个月且无人察觉。）
set -euo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SRC_SKILL="$REPO/integrations/claude-code/SKILL.md"
SRC_SCRIPTS="$REPO/integrations/claude-code/scripts"

[ -f "$SRC_SKILL" ] || { echo "找不到 $SRC_SKILL"; exit 1; }

# 客户端名 → 技能根目录
declare -a TARGETS=()
case "${1:-all}" in
  claude)    TARGETS=("Claude Code|$HOME/.claude/skills") ;;
  workbuddy) TARGETS=("WorkBuddy|$HOME/.workbuddy/skills") ;;
  all)
    [ -d "$HOME/.claude/skills" ]    && TARGETS+=("Claude Code|$HOME/.claude/skills")
    [ -d "$HOME/.workbuddy/skills" ] && TARGETS+=("WorkBuddy|$HOME/.workbuddy/skills")
    ;;
  *) echo "用法: bash integrations/install.sh [claude|workbuddy|all]"; exit 1 ;;
esac

[ ${#TARGETS[@]} -gt 0 ] || { echo "未检测到任何受支持的客户端技能目录"; exit 1; }

# 软链接一个路径，已存在的真实文件若与仓库不同则先备份
link_one() {
  local src="$1" dst="$2" label="$3"
  if [ -e "$dst" ] && [ ! -L "$dst" ]; then
    if ! diff -rq "$src" "$dst" >/dev/null 2>&1; then
      local backup="${dst}.bak-$(date +%Y%m%d%H%M%S)"
      cp -R "$dst" "$backup"
      echo "  ⚠ 本机 $label 与仓库不一致，已备份到 $backup"
      echo "    若那边有想保留的改动，回流到仓库再提交。"
    fi
  fi
  rm -rf "$dst"
  if ln -s "$src" "$dst" 2>/dev/null; then
    echo "  ✓ $label → 软链接（仓库即唯一版本源）"
  else
    cp -R "$src" "$dst"
    echo "  ✓ $label → 复制（本文件系统不支持软链接，仓库更新后需重跑本脚本）"
  fi
}

for entry in "${TARGETS[@]}"; do
  NAME="${entry%%|*}"
  ROOT="${entry#*|}"
  DIR="$ROOT/codex-worker"
  echo "[$NAME] $DIR"
  mkdir -p "$DIR"
  link_one "$SRC_SKILL"   "$DIR/SKILL.md" "SKILL.md"
  [ -d "$SRC_SCRIPTS" ] && link_one "$SRC_SCRIPTS" "$DIR/scripts" "scripts/"
  echo
done

echo "装好了。新开一个会话，说「派 Codex 做 X」即可触发。"
echo "验证：ls -l <技能目录>/codex-worker  应看到指向本仓的软链接。"
