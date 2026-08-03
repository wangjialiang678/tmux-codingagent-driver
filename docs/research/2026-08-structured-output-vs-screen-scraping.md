# 检测层调研：结构化输出 vs 抓屏

**日期**: 2026-08-03 · **背景**: tcd v0.4.0 架构复盘 §4.4
**问题**: tcd 所有状态判断都是对 TUI 文本的子串匹配，上游改文案就静默失效。现在
各家 CLI 的 headless / 结构化模式是否已经成熟到可以替代？

**前置去重**: 本仓库已有 [acp-vs-tmux-comprehensive-report.md](acp-vs-tmux-comprehensive-report.md)
（2026-03-05），结论是"ACP 是未来方向但 acpx 仍在 alpha，tmux 更实用"。**那份调研
早于各家 headless 模式成熟，本文是针对性重做，不是重复。**

---

## 一、结论

**不是"tmux vs headless"的二选一，而是分层：tmux 继续做进程托管，结构化模式做协议。**

在 tmux 会话里跑 CLI 的**结构化模式**（而不是交互式 TUI），读它的 JSONL 事件流而
不是抓屏。tmux 的全部好处（进程持久、断连不死、一 job 一进程、`tcd attach` 调试）
一条不丢，最脆的那层（子串匹配）被替换成一等事件。

**但有一个真实阻塞项**：`codex exec` 会自动取消 MCP 工具调用（见 §3.1），而 tcd
v0.3.2 特意保留了 `context7` MCP。这决定了迁移必须是**能力探测 + 优雅降级**，而不
是一刀切。

---

## 二、三家 CLI 的现状（2026-08）

tcd 现在靠抓屏推断的每一件事，现在都有一等事件对应。

### Codex

| 能力 | 命令 / 事件 |
|---|---|
| 结构化流 | `codex exec --json` → stdout 变成 JSONL 事件流 |
| 事件类型 | `thread.started`、`turn.started`、**`turn.completed`**、`item.*`、`error` |
| 多轮续跑 | `codex exec resume --last "..."` 或 `codex exec resume <SESSION_ID>` |
| **结构化产出契约** | `--output-schema` 约束最终回复符合 JSON Schema；`-o <path>` 落盘 |
| 进度 | 进度走 stderr，最终结果走 stdout |

来源：[Non-interactive mode · OpenAI Codex Docs](https://learn.chatgpt.com/docs/non-interactive-mode)

`--output-schema` 值得单独注意：它**就是架构文档 §4.3 提的"验收契约"**，上游已经
提供，不用自己造。

### Claude Code

| 能力 | 命令 |
|---|---|
| 结构化流 | `claude -p --output-format stream-json` → 逐行 JSON 事件 |
| 多轮 | `--input-format stream-json` 支持持续多轮会话；`--resume <session_id>` |
| token 级增量 | `--include-partial-messages` |
| 免阻塞 | `--allowedTools` + `--permission-mode` 预授权，无人值守不会卡在确认 |

来源：[Claude Code headless mode](https://www.buildthisnow.com/blog/guide/development/claude-code-headless-mode)、
[SDK / headless mode](https://claudecode101.com/en/tutorial/advanced/headless-mode)

### Gemini CLI

| 能力 | 命令 / 值 |
|---|---|
| 触发 headless | 非 TTY 环境，或带 `-p` / `--prompt` |
| 输出格式 | `--output-format` → 单个 JSON 对象，或 JSONL 事件流 |
| 事件类型 | `init`、`message`、`tool_use`、`tool_result`、`error`、**`result`**（完成信号） |
| **退出码语义** | `0` 成功 / `1` 一般错误或 API 失败 / `42` 输入错误 / **`53` turn limit 超限** |

来源：[gemini-cli/docs/cli/headless.md](https://github.com/google-gemini/gemini-cli/blob/main/docs/cli/headless.md)

注意 `53 = turn limit exceeded`：tcd 现在靠正则匹配
`_CONTEXT_LIMIT_KEYWORDS`（"context window is full" 等六个短语）来判断的东西，
Gemini 已经给了一个退出码。

### 映射关系

| tcd 现在怎么判断 | 结构化模式给什么 |
|---|---|
| pane 里有没有 `esc to interrupt` / `esc to cancel` | `turn.started` / `turn.completed` 事件 |
| 扫 `TCD_DONE:<req_id>` 标记 | `turn.completed`（不用再往 prompt 里塞标记） |
| 六个短语匹配"上下文超限" | gemini exit 53；codex `error` 事件 |
| 正则匹配 provider API 报错 | `error` 事件类型 |
| `_ACTIVITY_PATTERNS` 抓活动行 | `item.*` 事件（结构化的文件操作、命令执行） |
| 无（"任务完成"不存在） | codex `--output-schema` 约束产出 |

---

## 三、反驳证据与风险

### 3.1 阻塞项：`codex exec` 会自动取消 MCP 工具调用

> In `codex exec` (non-interactive), MCP tool calls are auto-cancelled because
> stdin is closed and no config key suppresses the approval prompt.

来源：[openai/codex#24135](https://github.com/openai/codex/issues/24135)（仍开放）

绕过方式是 `--dangerously-bypass-approvals-and-sandbox`，即**放弃沙箱**。

对 tcd 的影响是直接的：v0.3.2 明确保留了 `context7` MCP（"headless 编码不需要大部分
交互式 MCP，但保留 context7 用于查库文档"）。改走 `codex exec` 会让这个能力失效，
除非接受关掉沙箱。**这一条足以否定"全面切换"，但不否定"能用则用"。**

### 3.2 headless 的固有短板

> `claude -p "prompt"` seems ideal for headless use, but it has critical
> limitations: Buffers ALL output until completion — no streaming, no progress
> visibility · No session continuity · No mid-flight interaction — can't
> course-correct or add context.

来源：[Tmux Keeps AI Coding Agents Alive](https://www.implicator.ai/tmux-keeps-ai-coding-agents-running-for-days-after-you-disconnect/)

前两条已被 `--output-format stream-json` + `--resume` 解决。**第三条没有**：向一个
**正在跑的 turn** 中途注入消息，交互式 TUI 能做，headless 不能。tcd 的 `tcd send`
目前主要用在 turn 之间，影响有限，但要清楚这是真实损失。

### 3.3 进程模型会变

`codex exec resume` 的模型是"每一轮一个新进程"，tcd 现在是"一个长驻会话"。迁移后
job 的身份从 tmux session 变成 thread/session id，`tcd send` 的语义从"往会话里打字"
变成"起一个 resume 进程"。这是**架构变更，不是替换实现**。

### 3.4 tmux 本身的边界（一直存在）

> tmux persistence only protects against client disconnects, not server
> shutdown — killing the server or rebooting ends every running process.

### 3.5 外部佐证：send-keys 打 prompt 本来就不可靠

> do not use send-keys to type prompts into CC. Special characters, quotes,
> newlines, and shell metacharacters all break unpredictably.

来源：同 3.2

这正是 tcd 踩过两次的坑（2026-07 的 `(eval):33: unmatched "`、Claude 的排队消息
未提交）。tcd 长文本已改用 `load-buffer` + `paste-buffer`（推荐做法），但**只要还在
往 TUI 里打字，这类问题就是结构性的**。走结构化模式是从根上消除它。

### 3.6 ACP 的现状

生态比 2026-03 那份调研时成熟得多：已被 JetBrains、Google、GitHub 采用，25+ agents
实现，2026-01 上线了 ACP Registry；Claude Code 和 Codex 各自通过
`claude-agent-acp` / `codex-acp` **适配器**接入（都不是官方原生）。

来源：[Agent Client Protocol](https://agentclientprotocol.com/get-started/agents)、
[ACP 介绍](https://blog.marcnuri.com/agent-client-protocol-acp-introduction)

**但对 tcd 不是现在的选择**：ACP 的形状是 editor↔agent（面向 IDE 的会话式交互），
tcd 要的是 agent↔agent 的批量派发；而且要多一层适配器依赖。各家自己的结构化模式
今天就能用、无额外依赖，更直接。ACP 值得作为"以后要支持任意 agent"的备选保留观察。

---

## 四、建议路线

**分级传输 + 能力探测**，而不是一刀切：

| 级别 | 做法 | 适用 |
|---|---|---|
| **L1 结构化** | tmux 里跑 `codex exec --json` / `claude -p --output-format stream-json` / `gemini --output-format stream-json`，解析 JSONL | 默认首选 |
| **L2 交互式** | 现在的实现：TUI + 子串匹配 | provider 不支持结构化，或需要 MCP（见 3.1），或需要 turn 中途干预 |

配套改动：

1. **`tcd doctor` 的职责扩展**：从"验证子串假设还成立"变成"探测这个 provider 能走
   到哪一级"，并在 L2 时说明原因（MCP 需求 / 不支持）。
2. **provider 抽象加一层**：`supports_structured_mode` + 事件流解析器，
   `detect_completion` 在 L1 下读事件而不是抓屏。
3. **`--output-schema` 直接用于验收契约**（架构文档 §4.3），不用自研。
4. **L2 保留不删**：MCP 阻塞项没解决前，codex + MCP 的组合仍需交互式路径。

**先做哪个**：建议从 **gemini 或 claude 开始**做 L1（它们没有 MCP 阻塞），验证事件
流解析这套架构，codex 保持 L2 直到 #24135 有进展。这样风险最小，而且恰好先补上了
覆盖最差的两个 provider（见架构文档的 provider 能力表）。

---

## 五、对已有结论的修正

[acp-vs-tmux-comprehensive-report.md](acp-vs-tmux-comprehensive-report.md)
（2026-03-05）的结论"tmux 是更实用的选择"**依然成立，但理由要更新**：

- 当时：因为 ACP 还是 alpha，没有更好的结构化选项
- 现在：因为 tmux 解决的是**进程托管**问题（持久、断连、隔离），这个问题结构化模式
  并不解决——两者是互补而不是竞争。真正该被替换的不是 tmux，是**抓屏**。
