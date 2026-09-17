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

> **2026-09-09 起默认用监工脚本，不再手写轮询**：`python3 "/Users/michael/projects/AI 工作流/agent-owners/tools/tcd_watch.py" <job_id> <标签> --marker "[DONE:<包名>]" --max-minutes N`（后台跑）。它把"作业掉在地上"的情形变成可见状态并自动处置：未提交（Turn 0 输入框有滞留文字）→ 补回车；停滞（画面去计时器后 25 分钟不变）→ 报警；回合结束无完成标记 → 判断是提问还是半途，发催办（最多 2 次）；**合法停下**（汇报里出现"白名单之外 / 等待驱动方"）→ 不催办，退出码 7 交驱动方裁定；有完成标记 → 自动 `tcd verify` 并透传返回码。返回码：0 verify 通过 / 1 verify 失败 / 2 超时 / 3 未启动 / 4 停滞 / 5 催办后仍未完成 / 6 会话消失 / 7 白名单停下 / **8 卡在需要按键的提示** / **9 明确干不下去（额度、鉴权、重试耗尽；带屏幕上的恢复时间）** / **10 屏幕认不出且已静默（带末屏原文）**。8/9/10 是 2026-09-17 夜新增的活性门（`--silence-minutes`，默认 20；测试重的包调大），判据是日志 mtime × 末屏状态，详见下文「怎么判断作业是真在跑」。观察日志写到 agent-owners 仓 `data/observations/tcd/<job>.jsonl`，详见本文末尾「监工与派单模板附则」。**tcd ≥0.6.4 起监工不再直接碰 tmux**：状态取 `tcd status --json`（delivery / turn_count / last_event / last_agent_message_path），补回车用 `tcd nudge`（每次都是事件流里的 `job.nudge`），完成标记从 `<job>.last-message.md` 整行精确匹配；老版 tcd 自动降级。


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
   - `"working"` → 继续轮询。⚠️ **`state: "working"` 不等于它在干活**——它只反映我方的回合记账。要回答"它还在跑吗"，看同一份 JSON 里的 `liveness` 块（日志 mtime × 末屏状态），见下文「怎么判断作业是真在跑」。2026-09-17 夜六单全停时，`state` 整整一小时都是 `working`
3. **检查 `warnings` 并自动响应**：
   - `SANDBOX_MISMATCH` → 告知用户 Codex 可能无法写入文件，建议加 `--sandbox danger-full-access` 重启
   - `PERMISSION_ERROR` → 告知用户 Codex 遇到权限问题
   - `STALL` / `NO_PROGRESS` → Codex 可能卡住，用 `activity` 与 `liveness` 分析原因
   - `BLOCKED` → 屏幕明说干不下去（额度/鉴权/重试耗尽），带 `recover_at` 时告诉用户等到几点；**交人，不要催办**
   - `AWAITING_INPUT` → 卡在需要按键的提示，**不要替它按键**（可能换模型、花钱），交人
   - `UNKNOWN_PANE` → 屏幕认不出来，把 `liveness.pane_tail` 原样贴给用户，**不要报成"在跑"**
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

# 附加到 tmux session（调试用；默认使用 tcd 的独立 socket）
tmux -L tcd attach -t tcd-codex-<job_id>
tmux -L tcd ls

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
- **模型策略（王佳梁 2026-09-09 定）**：默认 `gpt-5.6-sol` + `model_reasoning_effort = "high"`（`~/.codex/config.toml` 全局值，tcd 不传 `--model` 时即用它）；`gpt-6-astra` 只用于架构讨论和重要复杂问题，按次 `tcd start --model gpt-6-astra`，不改全局。派单后看一眼 Codex 页脚的模型名核对（09-09 修复包 3/4 曾在不知情下跑了 astra medium）。"写一半停"不是模型缺陷，是指令污染
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

## 契约模板（按任务类型，2026-08-30 起）

**契约的上游是目标项目的测试方案**（例如该项目的测试方案文档）：有方案的项目按"任务类型→必含层级"表生成契约，不临场发挥；没有的项目先建方案再批量派工。

派工时按任务类型套模板，`--require-cmd`/`--require-file` 与提示词末尾的"完成判定"必须一致：

| 任务类型 | 契约必含 |
|---|---|
| 通用（有界面） | typecheck + 单元/集成测试 + **浏览器冒烟**（如 `npm run smoke`）+ 各端 build + 目标运行时语法检查（生产 Node 版本 `node --check`，禁 TS 参数属性/enum 等需转译语法） |
| 修客户 bug | **先新建能复现该报告的自动化用例并证明失败（红），再修到通过（绿）**；提示词要求输出"先失败的证据摘录"。没有复现用例的"修好了"不算修好 |
| 新页面 / 新实体 | 上述通用项 + **演示种子数据并断言 demo 环境该列表非空**（功能对但页面空＝客户眼里没做） |
| 多用户写路径 | + 并发提交测试（两笔先后提交都必须留存） |

**监督脚本坑**：解析 `tcd check --json` 时**直接管道**（`tcd check id --json | python3 -c ...`），不要先存变量再 `echo "$j"` 转传——JSON 里的转义序列会被 echo 吃掉导致连环误报（2026-08-31 三连误报实录）。TURN0_STUCK 告警在首轮工作中会误报，判卡死须同时满足 activity 为空。

## 怎么判断作业是**真在跑**（2026-09-17 夜教训，必读）

**这一节回答的不是"额度用完没有"，而是"这个作业到底有没有在推进"。额度耗尽只是众多停因中的一种。**

**病史**：那晚派了 6 单，23:30 负责人问"你怎么确认 codex 还在干活"。主会话用了两种方法确认，**两种都是错的**，六单其实全停了近一小时：

| 错误方法 | 当时的读数 | 为什么无效 |
|---|---|---|
| `tcd check <job> --json` 看 `state` / `elapsed_s` | `state: "working"`、`elapsed_s` 在增长 → 判"都在正常跑" | `state` 是**我方自产物**。它靠 pane 里有没有 `Working (` 之类字样判定；额度耗尽时这行字**消失了**，却没有任何一条规则接手回答"那它现在显示的是什么"，于是默认留在 working。`elapsed_s` 只是 now−started_at，作业死了它照样涨 |
| `grep -i "rate limit\|quota\|429" ~/.tcd/jobs/*.log` | 无命中 → 判"日志里没有限流" | **日志文件本身早就停止写入了**，grep 到的只有历史内容。"搜不到"在这里恰恰是停了的证据，却被读成了健康 |

两条的共性：**判据取自我方自产物（状态字段、关键词表），验的是我方自洽，不是世界的样子。**

**两个有效判据（都是外部证据，优先用它们）**：

```bash
# ① 日志 mtime —— 文件系统说的话。那晚五单静默了 51–60 分钟
python3 -c "import os,sys,time;p=os.path.expanduser('~/.tcd/jobs/%s.log'%sys.argv[1]);print(int(time.time()-os.stat(p).st_mtime),'s since last write')" <job_id>
stat -f %m ~/.tcd/jobs/<job_id>.log     # macOS 原始写法

# ② 抓屏 —— Codex 自己吐的字，不是我们推断的
tmux -L tcd capture-pane -p -t tcd-codex-<job_id> | tail -25
```

**tcd ≥0.6.5 把这两条做进了 CLI**，优先用它，不要自己拼：

```bash
tcd check <job_id> --json | python3 -c "import json,sys;print(json.load(sys.stdin)['liveness'])"
tcd status <job_id>            # 人读格式，直接打印 Liveness 行与末屏证据
tcd check <job_id> --json --silence-seconds 1800   # 跑全量测试的包调大静默阈值
```

`liveness` 块 = **两问交叉**：① 动没动（`silence_s` 日志静默秒数、`silent`）；② 为什么不动（`pane_state` 末屏五类互斥状态）。
`warnings` 里同步出 `BLOCKED` / `AWAITING_INPUT` / `NO_PROGRESS` / `UNKNOWN_PANE`。

| `pane_state` | 含义 | 处置 |
|---|---|---|
| `working` | 有进行中的迹象 | 继续等；**但若同时 `silent`=true → 是停滞**，屏幕在骗你 |
| `awaiting_input` | 等人按键（选择/确认框） | **报人，绝不替它按键**——见下 |
| `blocked` | 明说干不下去（额度耗尽、鉴权失败、重试耗尽…），`recover_at` 是屏幕上的恢复时间 | 人去买额度或等 `recover_at`；催办没有用 |
| `idle_prompt` | 回到空闲输入框 | **可能刚干完，也可能半途停了**——按交付物清单与 `[DONE:包名]` 判，不要据此下任一结论 |
| `unknown` | 认不出来 | **必有的一类**。报"认不出"并附末屏原文（`pane_tail`）交人看，**绝不当成"在跑"**——那晚出事的根因正是认不出的状态被当成了在跑 |

**为什么必须交叉、不能单看一边**：静默 + `idle_prompt` ≠ 挂了（可能刚交完活）；不静默 + `awaiting_input` 也可能是刚弹出来（`awaiting_input_fresh`，再看一轮）。静默多久算"静默"**与作业类型有关**：跑全量测试的包可以十几分钟不出声，只读分析包不该——所以它是参数（`--silence-seconds` / 监工 `--silence-minutes`，默认 20 分钟），派测试重的包记得调大。

**两段屏幕原文特征**（都是 `tail -25` 就能看到的；文案会变，所以两处代码都把特征串集中成了常量表 `src/tcd/liveness.py` 与监工的 `tcd_watch.py`，加新特征往表里加）：

```
■ You've hit your usage limit. Visit https://chatgpt.com/codex/settings/usage to
purchase more credits or try again at Sep 19th, 2026 8:27 PM.
```
→ `blocked`，`recover_at="Sep 19th, 2026 8:27 PM"`。注意这屏下面**还有一个空闲输入框**，只看输入框会误判成"回合结束"。

```
  Approaching rate limits
  Switch to gpt-5.6-luna for lower credit usage?
› 1. Switch to gpt-5.6-luna                 Fast and affordable agentic coding model.
  2. Keep current model
  3. Keep current model (never show again)  Hide future rate limit reminders about switching models.
  Press enter to confirm or esc to go back
```
→ `awaiting_input`（特征 = `Press enter to confirm` + `› 1.` 这样的高亮候选行）。**这种状态下作业永远不会自己恢复**，计时器再走也没用。

> ⚠️ **不要替用户决定切换到更便宜的模型——那是花钱的决定，交给人。** 同日 22:0x 有一单页脚变成了 `gpt-5.6-luna medium`，而派单时从未指定过 luna：极可能就是在上面这个提示上被"自动补回车"误触切过去的，当时无人察觉，**那一单的代码是低配模型跑的**。所以：`awaiting_input` 一律只报警不按键（`tcd nudge` 的补回车只用于 Turn 0 投递未确认的情形，且 tcd 会自己判定）；监工同理。
>
> 更一般地：**认不出的屏幕、需要花钱或授权的选择，都不是自动化该收口的地方**。宁可报"认不出"停下来问人，也不要猜一个状态继续跑。

### 特征串的纪律：显式声明优于模糊匹配（2026-09-18 现场教训）

上面这套东西**上线第一天就自己误报了一次**，值得当成反面教材记住。

监工对作业 `1a71d468` 返回 9（blocked），日志写「服务端返回限流/配额错误」，证据行是：

```
  … +429 lines (ctrl + t to view transcript)
```

而同一屏往下两行是 `• Working (1m 30s • esc to interrupt)`，`silence_s` 还是 0——**作业正在干活**。

根因：特征表里有一条 `\b(?:429|...)\b`，它匹配的 `429` 是 **Codex TUI 折叠提示里的行数**，不是 HTTP 状态码。（顺带一提，同一个东西还骗过一次人工 `grep -c "429"`。）

**这正是本节开头批评的那类错误的又一个实例**：判据取自一个**廉价的替身**（裸字符串 `429`），而不是真正的事实（HTTP 响应状态）。工具本身就是为纠正这类毛病做的，结果它自己的判据里有一条。

由此定下三条纪律，加新特征时照做：

1. **裸数字不能单独作为特征**。`429` / `500` 这类要么带上下文声明它是状态码（`status 429` / `HTTP 429` / `error: 429` / `429 Too Many Requests`），要么就别用。同理，`giving up`、`max retries`、`please log in`、`approve this` 这类**日常英语短语**裸用必撞——代理谈论自己的工作时就会写出这些字（"giving up on the regex approach"）。要锚到程序真正打印的形状（`giving up after \d+`、`max retries exceeded`），或者锚到本 CLI 独有的措辞（`codex login`）。
2. **矛盾时不采信任何一边**。"停了"的特征与同屏的活标志（`Working (` / `esc to interrupt`）不可能同时为真。现在两处代码都会在这种情况下返回 `unknown` 并把**两行都**附上交人看，而不是挑一个更吓人的报出去。这是第二道防线——就算将来又混进一条便宜的特征串，它也变不成结论。
3. **语料库当门**。两个仓库的测试里各有一份 `TUI_CHROME`：健康作业会画出来的行 + 代理干活时写下的话（含 `… +429 lines`、`+429 lines)`、`const RATE_LIMIT = 429;`、`> ` 空引用行等）。**任何特征表里的任何模式命中其中任何一行，测试就红**。加新特征前先往这份语料库里加一行反例，是比人眼审查可靠的做法——这次的 bug 本该在这里被拦下，而当初我只用眼睛扫了一遍表。

> 这条门第一次跑就抓出 **5 条**弱特征（不止闯祸那一条），而复用自 `PROVIDER_ERROR_SIGNS` 的那批**零命中**——因为它们每一条都锚在字面短语上（`unexpected status`、`auth error:`、`Model metadata for`）。**显式声明优于模糊匹配**，这就是现成的对照组。

**监工返回码**（`tcd_watch.py`，agent-owners 仓）：`8` 卡在需要按键的提示 / `9` 明确干不下去（观察日志里带 `recover_at`）/ `10` 屏幕认不出且已静默（带 `pane_tail` 原文）。这三种都**不是"慢"**，是"停了且不会自己恢复"，处置方式与停滞（`4`）完全不同——不要催办，交人。

**停摆处置**（2026-09-09 更新：投递层已修，重派不再是首选）：
- tcd ≥0.6.3 的 start/send 投递校验分三态（running/pasted/absent），且事件流必须出现 confirmed/unconfirmed 之一。`tcd start` 或 `tcd send` **退出码 3 = 投递未确认**，会话仍保留；输入框有滞留文字时先运行 `tcd nudge <id>`，不要直接操作 tmux send-keys
- 仍然停摆时先分辨是哪一种：`tcd log <id> | grep prompt_` 看投递结论（confirmed/unconfirmed）；`tcd check --json` 看 activity 是否为空
  - `prompt_unconfirmed` + activity 空 → 投递确实失败，`tcd kill` 后重派
  - 已 confirmed 但长时间无活动 → 是模型侧卡住（额度/网络），`tcd send` 点名催一次；再无进展换 `codex exec` 非交互模式跑同一任务
- ⚠️ **`tcd kill` 会连同 `--worktree` 创建的工作目录一起删除**——重派前先确认没有未提交产出；需要保留就用 `git worktree add` 手工建（kill 不碰它）

**驱动方纪律**：验收提交只 `git add <该任务文件域>`，**永不 `git add -A`**——同仓并行的其他代理半成品会被卷入（2026-08-30 两次实录）。并行任务优先 `--worktree`。


## 监工与派单模板附则（2026-09-09，来自修复包 3 / E0-A / E0-B 三单实战）

**子代理回合不再误判为主回合完成（2026-09-15，tcd 仓 5a48ade）**
- Codex 原生子代理（thread_spawn）完成时也会触发 notify；旧版 tcd 会 turn_count+1、写 .turn-complete、把子代理小结当 last_agent_message——09-14 两单根目录作业因此被误判 idle。现已按 thread-id 读 rollout 元数据分流：子代理只累加 `subagent_turns`；`detect_completion` 在信号文件存在时再抓 pane，命中 `Working (` / `esc to interrupt` / `Waiting for agents` 返回 inconclusive（`TCD_CODEX_PANE_VALIDATION=0` 可关）。`tcd status/check --json` 新增 `subagent_turns`、`pane_working`；原始通知追加在 `~/.tcd/jobs/<job>.notify.jsonl`。**完成判定规矩不变**：`tcd verify` 全过 + 交付物点名 + `[DONE:包名]`，`turn_state=idle` 不算。
- 派到**仓库根目录**（非 tms-app）的文档/工具类作业也要有 AGENTS.md（TMS 已补根 AGENTS.md，明写「不要为等子代理而结束回合、子代理汇报不是交付」）。

**tcd 投递与完成信号（≥0.6.3）**
- `tcd status --json` 的 `delivery` 和 `last_event` 是投递判定入口；事件流没有 prompt/message confirmed/unconfirmed 之一，就视为校验未发生。
- job.json 与 .turn-complete 里的 `last_agent_message` 仍兼容截断到 **500 字符**；完整汇报读 `last_agent_message_path` 指向的 `<job_id>.last-message.md`，完成标记不得依赖 500 字符字段。
- `tcd status` 的 `status=failed / error=killed by user` 只表示验收后被 kill，不代表失败；判完成看 `.turn-complete` + 最终汇报 + `tcd verify`。

**派单模板必加的三句**
1. 「最终汇报第一行与最后一行都写 `[DONE:<包名>]`（各自单独成行）。」
2. 「不要停下来等确认——需要选择处按契约默认并写进回执『实施假设』。」
3. 「只有白名单之外的文件必须改时才停下，且用固定措辞：`白名单之外：<文件路径>，等待驱动方`。」——监工据此识别合法停下；**自动催办等于授权**（修复包 3 曾因催办文本"如果没做完请继续"被当作允许改白名单外文件），所以合法停下一律交人。

**验收纪律补充**
- 监工的 verify 通过后，驱动方仍要自己复跑门并核 SUMMARY 行（守恒门曾静默失效两批，report 模式把崩溃吞成 rc=0）。
- 执行代理写进索引/登记表的 ID 必须过对应校验器（修复包 3 写了不合法的 work_id，生成器拒绝后才发现）。
