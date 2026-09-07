# integrations —— 让你的 AI 会用 tcd

tcd 本身是命令行工具，`--help` 就能看懂。但**驱动方 AI 需要知道的不止参数**：什么时候派工、怎么写提示词、怎么判断"真的干完了"、上线前还要验什么——那些实战经验在这个目录里。

## claude-code/ —— 技能本体（Claude Code 与 WorkBuddy 共用）

`codex-worker` 技能：`SKILL.md`（派工纪律与实战教训）+ `scripts/`（三个驱动 helper：
`tcd-await-commit.sh` 可靠完成信号、`tcd-merge-safe.sh` 绝不丢工的合并、
`tcd-watch-progress.sh` 进展轮询）。

> 目录名沿用 `claude-code/` 是历史原因；内容对两个客户端通用，未改名以免打断既有软链接。

安装（默认装到检测到的全部客户端）：

```bash
bash integrations/install.sh            # Claude Code + WorkBuddy
bash integrations/install.sh claude     # 只装 Claude Code
bash integrations/install.sh workbuddy  # 只装 WorkBuddy
```

装的是**软链接**，仓库即唯一版本源——`git pull` 后所有客户端同时生效。

### 为什么强调软链接（2026-09-07 教训）

WorkBuddy 侧此前是 7 月手工复制的副本，落后仓库两个月（216 行 vs 301 行）且无人察觉；
同期发现三个 helper 脚本**只存在于两台安装副本里、从未入库**，而 SKILL.md 正文一直在引用它们。
安装器现在会在覆盖前比对并备份不一致的本机副本，避免吞掉未回流的改动。

装的是**软链接**（`~/.claude/skills/codex-worker/SKILL.md` → 本仓库文件），所以：

- 以后 `git pull` 或本地改这份文件，Claude Code **立刻用上新版**，不需要重装
- 反过来在会话里改技能，改的就是仓库文件，`git commit` 即可回流——不会再出现"两边不一致"
- 已验证 Claude Code 的技能加载器正常跟随软链接（2026-08-20 实测加载成功）
- 文件系统不支持软链接时脚本自动退回复制，并提示"更新后需重跑"

新开会话后，说"派 Codex 做 X"即可自动触发。

**本仓库是这份技能的版本源**：技能是从实战里长出来的（每条纪律背后都有一次翻车）。本机 `.claude/` 不在版本控制里，所以真相放这边。

技能里几条最贵的经验：

- **验收契约**：派工时用 `--require-file` / `--require-cmd` 声明完成判定，`tcd verify` 通过才算完（病史：Codex "写一半就 idle"，靠人肉点名催）
- **运行面验证**：测试证明的是仓库里那份代码；用户实际跑的那份要单独验一次（病史：仓库已修、测试全绿、文档写着"已上线"，但服务器上跑的还是老代码）
- **AGENTS.md 优先级高于技能**：目标项目里放执行代理契约，避免无人值守会话"等一个不存在的批准者"
