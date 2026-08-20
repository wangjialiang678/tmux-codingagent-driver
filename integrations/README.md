# integrations —— 让你的 AI 会用 tcd

tcd 本身是命令行工具，`--help` 就能看懂。但**驱动方 AI 需要知道的不止参数**：什么时候派工、怎么写提示词、怎么判断"真的干完了"、上线前还要验什么——那些实战经验在这个目录里。

## claude-code/SKILL.md

Claude Code 技能（`codex-worker`）。安装：

```bash
mkdir -p ~/.claude/skills/codex-worker
cp integrations/claude-code/SKILL.md ~/.claude/skills/codex-worker/SKILL.md
```

新开会话后，说"派 Codex 做 X"即可自动触发。

**本仓库是这份技能的版本源**：技能是从实战里长出来的（每条纪律背后都有一次翻车），改动请先改这里再同步到 `~/.claude/skills/`，别只改本机副本——本机 `.claude/` 不在版本控制里，只改那边等于没留下。

技能里几条最贵的经验：

- **验收契约**：派工时用 `--require-file` / `--require-cmd` 声明完成判定，`tcd verify` 通过才算完（病史：Codex "写一半就 idle"，靠人肉点名催）
- **运行面验证**：测试证明的是仓库里那份代码；用户实际跑的那份要单独验一次（病史：仓库已修、测试全绿、文档写着"已上线"，但服务器上跑的还是老代码）
- **AGENTS.md 优先级高于技能**：目标项目里放执行代理契约，避免无人值守会话"等一个不存在的批准者"
