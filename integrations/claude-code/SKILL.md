---
description: "通过 tcd 驱动 OpenAI Codex CLI 执行编码任务。当用户提到用 Codex 做任务、让 Codex 帮忙、派 Codex 去做、codex 审核、codex 开发、codex 修 bug，或需要将任务委派给另一个 AI 编码代理时自动触发。"
---

# Codex Worker — 通过 tcd 驱动 Codex CLI

## 你是什么

你（Claude Code）是编排者。Codex CLI 是你的子代理。你通过 `tcd` CLI 工具向 Codex 派发任务，轮询等待完成，获取结果返回给用户。

## 前置条件

- `tmux` 已安装（`/opt/homebrew/bin/tmux`）
- `tcd` 已全局安装（`uv tool install .` 从 `~/projects/AI 工作流/tmux-codingagent-driver`）
- `codex` CLI 已安装（`/opt/homebrew/bin/codex`）

## 标准操作流程

### Step 1: 启动任务

```bash
tcd start -p codex -m "<任务提示词>" -d "<项目绝对路径>" --timeout 10
```

- `-d`：**必须**是用户指定的项目目录的绝对路径。如果用户没指定，用当前工作目录。
- `--timeout`：默认 60 分钟。复杂任务可调大。
- 返回 Job ID 和 tmux session 名称。
- 保存 `job_id` 用于后续操作。
- 初始化行计数器 `_last_line=0`，用于后续增量输出。

> ⚠️ **调用方须知（tcd ≥ v0.3.2，这些都是正常行为，不是卡死）**：
> - **`tcd start` 会阻塞约 10–20 秒才返回 `Job started`**。这是 tcd 在等 Codex TUI 真正就绪、确认 prompt 已投递（必要时自动重发）。**不要因为它没有秒回就以为挂了去 kill 它**。返回 `Job started: <id>` 才算启动成功。
> - **Codex 的交互式 MCP server 会被自动禁用（默认只保留 `context7`）**。headless 编码不需要 pencil/playwright/搜索类 MCP，且它们会拖慢甚至卡死启动（playwright via npx 可卡几分钟）。要调整保留名单，设环境变量 `TCD_CODEX_MCP_KEEP="context7,xxx"`（逗号分隔；空字符串=全禁）。
> - 目录信任弹窗、启动期自动更新都已由 tcd 自动处理，无需干预。

**任务提示词编写原则**：
- 用中文或英文均可，Codex 都能理解
- 明确说清楚要做什么、约束条件（如"不要修改代码"）、输出要求
- 如果需要输出文件，在提示词中指定完整路径
- **Worktree 场景**：如果使用了 `--worktree`，**必须**在提示词末尾追加：
  `完成所有修改后，执行 git add 和 git commit 提交你的改动。确保所有修改都已 commit，不要留下未提交的更改。`
  （不加这句，AI 可能只改文件不 commit，导致 `tcd merge` 时 "Already up to date" 无法合并）

### Step 2: 带进展汇报的轮询等待

**禁止使用 `tcd wait`！** 它会阻塞整个 Claude Code 进程，用户在等待期间看不到任何输出。

**必须使用轮询循环**，每轮做两件事：检查完成状态 + 获取增量输出展示给用户。

> ⚠️ **完成判定用「commit/分支」信号，不要只信 `state`/`status`（实战教训）**：
> - `tcd check` 的 `state` 会在 `idle`/`working` 间**抖动**；`tcd jobs` 的 `status` 对**被 kill 的 job 会标 `failed`**（即便其工作已成功提交）——两者都**不是可靠的"完成"判据**。
> - 可靠"完成"信号 = **worktree 分支出现了超出 base 的新 commit**：`git -C <worktree> rev-parse HEAD`（≠启动时记下的 base）或 `git log --oneline tcd/<job_id>`。守护/轮询据此判完成，而非 `state==idle`。
> - **竞态**：codex 提交有延迟，你查 HEAD 那一刻可能正在提交→看到 base。**别据单次读就断言"未完成/已丢失"**，隔一轮再确认。
> - 守护脚本用"分支 HEAD≠base"触发（见 `scripts/tcd-await-commit.sh`），不要用"全部 idle"。

> ⚠️ **非 worktree 模式的完成判定配方（2026-07-22 实战，两次假阳性后定型）**：
> 没有 worktree 就没有 commit 信号；用"pane 里没有 `Working (`"判完成会被 TUI 滚动/轮换间隙**误触**（实测两次）。可靠配方 = **双条件 + 去抖**：
> 1. **交付物文件已落盘**（派发时就在提示词里定好清单，如"必须新建 src/x.py、tests/test_x.py"，轮询时 `[ -f ... ]` 点名）；
> 2. **pane 空闲**（pane_tail 与 activity 末尾都无 `Working (`/`esc to interrupt`）；
> 3. **两个条件连续 2 个采样周期（间隔 ≥30s）同时满足**才算完成。
> 在 Claude Code 里优先用 Monitor 工具把这个循环挂后台（完成自动唤醒），不要 15 秒手动轮询占主线程。

> ⚠️ **turn 结束 ≠ 任务完成（实战教训）**：Codex 会自己 spawn 并行子代理，"Waiting for agents"结束后可能只写了一半就停在空闲输入框（实测：只写完红灯测试、实现一行没写就 idle 了）。**验收必须对着交付物清单点名**——文件缺了/测试数不对，直接 `tcd send "继续执行：XXX 尚未完成…"` 催，它能无缝续跑，不必重开 job。

#### 单次轮询（每 15 秒一次）

```bash
# 1. 检查状态（含活动摘要）
tcd check <job_id> --json

# 2. 获取增量输出（只看自上次以来的新内容）
tcd output <job_id> --since-line <_last_line> 2>/dev/null
# 从 stderr 读取 __lines_total=N 更新 _last_line
```

`tcd check --json` 返回：
```json
{
  "state": "working",
  "elapsed_s": 45,
  "turn_count": 0,
  "warnings": [...],
  "pane_tail": "...",
  "activity": [
    "• Edited src/tcd/worktree.py (+80 lines)",
    "• Ran uv run pytest tests/test_worktree.py -v",
    "12 passed in 4.46s"
  ]
}
```

**`activity` 字段**是从 Codex 滚动缓冲区提取的有意义操作（文件读写、命令执行、测试结果），比 `pane_tail` 更有用。

#### 轮询策略

1. 每 15 秒调用 `tcd check <job_id> --json`
2. 解析 JSON 中的 `state`：
   - `"idle"` / `"completed"` → 进入 Step 3
   - `"context_limit"` → Codex 上下文已满，需要重启新 job 或结束
   - `"working"` → 继续轮询
3. **检查 `warnings` 并自动响应**：
   - `SANDBOX_MISMATCH` → 告知用户 Codex 可能无法写入文件，建议加 `--sandbox danger-full-access` 重启
   - `PERMISSION_ERROR` → 告知用户 Codex 遇到权限问题
   - `STALL` → Codex 可能卡住，用 `activity` 分析原因
   - `TURN0_STUCK` → TUI 可能没启动成功，建议 `tcd attach` 查看
4. **向用户展示 Codex 真实反馈**（核心改进）：
   - **直接展示** `activity` 数组中的内容，不要自己总结
   - 用 `tcd output <job_id> --since-line <_last_line>` 获取增量内容
   - 将增量内容中有意义的行直接展示给用户
   - 从 stderr 的 `__lines_total=N` 更新 `_last_line`

**展示格式**（直接展示 Codex 的真实操作）：

```
[30s] Codex 活动:
  • Explored: Read cli.py, test_cli.py
  • Explored: Read worktree.py, sdk.py, job.py

[1m30s] Codex 活动:
  • Edited src/tcd/cli.py (+45 lines)
  • Created tests/test_cli_worktree.py
  • Ran uv run pytest tests/test_cli_worktree.py -v
    └ 7 passed in 0.04s

[2m15s] Codex 活动:
  • Ran uv run pytest tests/ -q
    └ 222 passed in 40.52s
  • 已完成，输出最终摘要
```

**关键原则**：
- **禁止自己编造/猜测** Codex 在做什么（如"正在分析代码"）
- **直接展示** Codex 的操作记录，让用户看到真实反馈
- 如果 `activity` 为空且 `state` 仍为 working，只说「Codex 工作中，暂无新活动」

**实现要点**：
- 不要在一个 Bash 调用中用 while 循环，这样用户依然看不到中间输出
- **每次轮询必须是独立的 Bash 调用**，这样 Claude Code 才能在两次调用之间向用户输出进展
- 在轮询间隙（两次 Bash 调用之间），展示 Codex 活动给用户
- 维护 `_last_line` 变量，避免重复展示已看过的内容

### Step 3: 获取结果

```bash
tcd output <job_id>
```

返回 Codex 的完整输出（已清理 ANSI 转义序列和 TUI 噪音）。

也可以只看最后部分：
```bash
tcd output <job_id> --tail 50
```

**结果处理**：
- **先按交付物清单验收**（文件是否齐、测试数是否达标），再看它的总结——它说"完成"不算数，落盘的东西才算数
- **验收测试别抢跑**：pane 刚转空闲时它可能还在收尾写文件（实测撞过 7 个瞬态失败，隔几秒重跑即全绿）；等稳定空闲（或完成判定的去抖通过）再跑测试
- 阅读 output，给用户一份简洁总结
- 如果 Codex 生成了文件，告诉用户文件路径
- 如果 Codex 报错或超时，说明原因并建议下一步；缺件用 `tcd send` 催，不必重开 job

### Step 4: 清理

```bash
tcd kill <job_id>
```

任务完成后清理 tmux session。

## 其他命令

```bash
# 向正在运行的任务发送追加消息
tcd send <job_id> "补充说明..."

# 列出所有任务
tcd jobs

# 查看任务详细状态
tcd status <job_id> --json

# 查看结构化事件日志
tcd log <job_id>                    # 所有事件
tcd log <job_id> --tail 10          # 最近 10 条
tcd log <job_id> --event job.checked # 按类型过滤

# 增量输出（轮询用）
tcd output <job_id> --since-line 150  # 从第 150 行开始
tcd output <job_id> --tail 20         # 最后 20 行

# 附加到 tmux session（调试用）
tcd attach <job_id>

# 清理已完成的任务
tcd clean
```

## 文件系统布局

| 路径 | 内容 |
|------|------|
| `~/.tcd/jobs/<job_id>.json` | 任务元数据 |
| `~/.tcd/jobs/<job_id>.log` | 完整终端输出日志 |
| `~/.tcd/jobs/<job_id>.turn-complete` | 完成信号文件 |
| `~/.tcd/jobs/<job_id>.prompt` | 发送的提示词备份 |
| `~/.tcd/jobs/<job_id>.events.jsonl` | 结构化事件日志（append-only） |

## 注意事项

> **tcd v0.3.2（2026-06-20）起，Codex 启动可靠性问题已在 tcd 层修复**，无需手动干预：
> - **自动更新**：tcd 启动 Codex 时传 `-c check_for_update_on_startup=false`，避免 Codex 启动时自动 `npm install` 后退出（这是之前"调用 Codex 总失败、退回 Claude Code"的首要原因）。
> - **目录信任弹窗**：tcd 预信任工作目录（`-c projects."<cwd>".trust_level="trusted"`），并在 readiness 循环识别"Do you trust"弹窗自动回车。worktree 是全新目录，以前每次都卡这个弹窗。
> - **MCP 启动阻塞**：tcd 自动禁用 `~/.codex/config.toml` 里所有 MCP server（`-c mcp_servers.<name>.enabled=false`）。Codex 会等所有 MCP server 起完才接受输入，慢的（如 playwright via npx）会卡几分钟导致 prompt 丢失；headless 编码不需要交互式 MCP。如某任务确需某 MCP，需单独处理。
> - **prompt 投递校验**：tcd 等 TUI 稳定后再发 prompt，发完校验是否收到，没收到自动重发（最多 2 次）。新事件：`job.prompt_resend` / `job.prompt_confirmed` / `job.prompt_unconfirmed`。

- **worktree 用不用的决策规则**：仓库有**未提交的基线改动**时不要 `--worktree`（worktree 从 HEAD 建分支，拿不到工作区状态）。此时直接在主工作区跑，配合两条纪律：① 提示词里划定**文件域隔离**（Codex 只碰哪些目录，驱动方不碰）并明令**禁止一切 git 操作**；② 派发前 `git status` 记下基线，验收时只看新增差异。实测三个任务包零冲突。
- **模型配置分两层**：`tcd start --model` 可按次覆盖模型名；但 reasoning effort（如 xhigh）只能改 `~/.codex/config.toml` 的 `model_reasoning_effort`，全局生效——改之前告知用户。
- **提示词最佳实践**：大任务按里程碑分包串行派发（后包依赖的 API 契约让前包先落盘）；每包提示词 = 权威执行摘要 + 指向仓库内设计文档（"先读 docs/specs/xxx.md"）+ 交付物清单 + 测试要求（"跑 xxx 必须全绿并贴统计"）。实测接口零返工。
- Codex 运行在 full-auto 模式（`-a never`），会自动执行文件操作，不会卡在确认界面
- **沙箱模式**：默认 `workspace-write`（只能写工作目录）。需要写代码时，加 `--sandbox danger-full-access`
- 如果用户要求"不要修改代码"，必须在提示词中明确告诉 Codex
- 一个 job 可以通过 `tcd send` 发送多轮消息，实现对话式交互
- 长任务请调大 `--timeout`
- tcd 也支持驱动 Claude Code (`-p claude`) 和 Gemini CLI (`-p gemini`)
- 每个 job 自动产生 `.events.jsonl` 结构化事件日志，可通过 `tcd log` 查看
- **Worktree 必须 commit**：使用 `--worktree` 时，AI 必须在完成后 commit 修改。未 commit 的改动在 `tcd merge` 时会被忽略（"Already up to date"），worktree 清理后代码直接丢失。这是最常见的 worktree 使用错误。
- **kill/清理前必须先确保已 commit（DRIVER 职责，防"错杀丢工"）**：codex 常"干完活漏最后 `git commit`"，或卡在它自己 spawn 的 reviewer/子代理上不退出。清理前**先确认分支已出现新 commit**；若没有，**DRIVER 自己进 worktree `git add -A && git commit` 再 merge**。**切勿先 `tcd kill`**——`tcd kill` 会移除 worktree 目录，**未提交改动随之永久丢失**。
- **已 commit 的分支在 kill 后仍保留**：即便 worktree 目录被删，`tcd/<job_id>` 分支与其 commit 仍在，可 `git merge tcd/<job_id>` 直接救回。所以"误判已丢失"前务必先 `git branch -a` / `git fsck` 查证。
- **驱动 helper**：`scripts/tcd-await-commit.sh <worktree> <base>`（轮询分支新 commit，可靠完成信号）；`scripts/tcd-merge-safe.sh <repo> <worktree> <branch> <base>`（无 commit 则先从 worktree 代提交再 merge，绝不丢工）。
- **自举警告**：如果任务目标是修改 tcd 自身（`~/projects/AI 工作流/tmux-codingagent-driver`），注意当前全局安装的 tcd 可能包含你正在修复的 bug。修复完成后建议先手动合并（`git merge`），再 `uv tool install . --force` 更新全局版本，最后再用 `tcd merge` 处理其他 worktree。

## 验收契约（2026-08-18 起为派工标准动作，tcd ≥0.6.0 内置）

**病史**：两单任务"写一半就 idle"（技能门槛等批准 + multi_agent 等子代理的中场空闲被误判完工），靠人肉点名催。tcd 0.6.0 已内置 acceptance contracts，此前没用上。

**派工时必做**——把 prompt 里的"完成判定交付物"同步声明给 tcd：

```bash
tcd start -p codex -d <dir> --timeout 45 \
  --require-file packages/xxx/src/main.tsx \
  --require-file packages/server/test/xxx.test.ts \
  --require-cmd 'npm run typecheck' \
  --require-cmd 'npm test -w @tms/server' \
  -m "<任务提示词>"
```

**轮询判完成**：`idle` 且 `tcd verify <job_id>` 通过才算完；verify 不过 → 自动 `tcd send` 点名缺件（缺什么 verify 输出里有），不必人工比对清单。

**配套纪律**：
- 目标项目放 AGENTS.md 执行代理契约（对端是自动化驱动、技能审批门槛视为预批、勿为等子代理结束回合）——superpowers 官方规定 AGENTS.md 优先级高于技能
- 模型无需更换：gpt-5.6-sol high 即编码旗舰；"写一半停"不是模型缺陷，是指令污染
- 若无人值守场景仍频繁被 multi_agent 中场空闲干扰，可对该场景单独 `-c features.multi_agent=false`

## 运行面验证（2026-08-20 起为交付标准动作）

**原则**：验收契约（测试全绿）证明的是**仓库里那份代码**；只要"用户实际运行的那份"不是你刚测的那份，中间就有一条**部署间隙**，必须在运行的那份上再验一次。写"已上线/已生效"之前，先问一句：**用户跑的和我测的是同一份吗？**

**病史**：一单修复"仓库已改+测试全绿+文档已写'已上线'"，但服务器上没有任何证据证明跑的是新代码——事后靠 md5 比对才收口；核查者还先用错 grep 字面量（代码里是驼峰 `feedbackHistory`，去查了连字符 `feedback-history`）误判一轮。

**判断规则**：
- **无间隙**（纯本地库/脚本，跑的就是源码本身；测试环境=运行环境）→ 验收即完成，**不加仪式**
- **有间隙** → 找到"运行的那份"在哪，对着它验证，证据留档（commit message 或项目运行手册）

**分形态速查**（间隙长什么样 → 怎么验）：

| 运行形态 | 间隙 | 验证动作 |
|---|---|---|
| 服务器常驻服务（systemd 等） | 没同步 / 没重启（进程不重启不加载新代码） | 同步→restart→is-active→在**服务器文件**上 grep 本次关键串或 md5 比对；能 curl 就打一发真实路径 |
| 本地常驻（launchd/后台进程/IDE 插件） | 同上，只是机器在本地 | 重启进程后验证版本或行为 |
| CLI 工具（pipx/npm -g/uv tool 安装） | 装的是旧版本，源码改了装的没变 | 重装或 `--version`/关键行为跑一发，确认用的是新版 |
| 发布的包（npm/PyPI） | 发布物 ≠ 仓库（漏 build、漏文件） | 从 registry 装回来冒烟一次 |
| 前端静态资源/dist | 浏览器/CDN 缓存着老资源 | 确认构建产物已替换 + 带版本号资源或强刷验证（本技能生态踩过三层缓存的坑） |

**通用陷阱**（哪种形态都适用）：
- grep 字面量从**代码原文复制**，不凭记忆拼（驼峰/连字符/中英文标点都坑过）
- 别忘外围件：主同步路径之外的脚本、服务配置片段（drop-in）、env 文件，要单独同步/单独 reload
