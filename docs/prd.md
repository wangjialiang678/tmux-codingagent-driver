# PRD: tmux-codingagent-driver (tcd)

**版本**: v0.1.0
**日期**: 2026-03-02
**状态**: IMPLEMENTED（Phase 1-4 全部完成，119 tests pass）
**作者**: Michael

---

## 1. 概述

tmux-codingagent-driver（简称 **tcd**）是一个通过 tmux 驱动多种 AI CLI 工具的编程任务分派器。它让上游 Agent（Claude Code、OpenClaw 或 shell 脚本）可以启动、监控和收集下游 AI CLI（Codex、Claude Code、Gemini CLI）的执行结果，实现多 AI 并行编程。

**一句话**：tmux 是总线，AI CLI 是 worker，tcd 是调度器。

---

## 2. 背景与动机

### 2.1 问题

现代 AI 编程助手（Claude Code、Codex、Gemini CLI）各有所长，但都是独立运行的 CLI 工具，没有标准的跨工具编排方案。开发者面临：

- **单 Agent 瓶颈**：一个 Claude Code session 一次只能做一件事，无法并行利用多个 AI
- **手动切换成本**：在不同 AI 工具间手动复制粘贴上下文、切换终端窗口
- **API 成本高**：通过 API 编排需要每次发送完整上下文（数千 tokens），而 CLI 工具自维护 session 只需发新指令（50-200 tokens）

### 2.2 现有方案不足

| 方案 | 不足 |
|------|------|
| **claude_code_bridge (CCB)** | 架构过重（daemon/TCP/worker pool），耦合深，难以独立使用 |
| **codex-orchestrator** | 只支持 Codex，TypeScript 实现，无法驱动 Claude/Gemini |
| **直接用 API** | 每次调用重发上下文，token 浪费严重；不能利用 CLI 的文件操作能力 |
| **手动多终端** | 不可编程，无法自动化，人工成本高 |

### 2.3 核心洞察

AI CLI 工具（codex、claude、gemini）在终端中运行时自维护完整对话历史。通过 tmux 向终端注入文字 = 发送请求，读取终端输出/日志 = 接收响应。**每次调用只需发新指令（50-200 tokens），上下文由 AI CLI 自己管理**。

---

## 3. 目标用户与使用环境

### 3.1 用户画像

- 使用 Claude Code / Codex / Gemini CLI 进行日常编程的个人开发者
- 有 tmux 使用经验
- 希望让一个 AI Agent 编排多个 AI 工具并行工作

### 3.2 环境约束

| 项 | 要求 |
|----|------|
| OS | macOS（主要）, Linux（兼容） |
| 终端复用器 | tmux（必须已安装） |
| Python | 3.10+ |
| AI CLI | 至少安装一种：`codex` / `claude` / `gemini` |
| 网络 | AI CLI 各自需要有效的 API key / 登录态 |

---

## 4. 目标与非目标

### 4.1 目标（Goals）

- **G1**: 提供统一的 CLI 接口，驱动 Codex、Claude Code、Gemini CLI 三种 AI 工具
- **G2**: 支持并行启动多个 AI 任务，互不干扰
- **G3**: 可靠检测任务完成并收集结果
- **G4**: 上游 Agent（Claude Code / OpenClaw）可通过 bash 命令或 Python SDK 调用
- **G5**: 支持向运行中的 AI 发送后续指令（多轮对话）
- **G6**: 个人工具级别的文档、测试和 CI

### 4.2 非目标（Non-Goals）

- **NG1**: 不做 API 代理——tcd 只驱动 CLI，不替代 API 调用
- **NG2**: 不做 GUI——纯 CLI + SDK
- **NG3**: 不做跨机器分布式——只在本地 tmux 内
- **NG4**: 不做 AI 决策——tcd 只负责调度执行，不决定"该用哪个 AI"
- **NG5**: 不做 Windows 支持（MVP 阶段）
- **NG6**: 不做社区开源运营（无贡献指南）

---

## 5. 核心概念

| 概念 | 定义 |
|------|------|
| **Provider** | 一种 AI CLI 的适配器（如 CodexProvider、ClaudeProvider、GeminiProvider），封装启动参数、完成检测、响应解析的差异 |
| **Job** | 一次编程任务的全生命周期记录，从创建到完成，持久化为 JSON 文件 |
| **Turn** | AI CLI 的一个执行轮次。一个 Job 可能包含多个 Turn（用户追加指令） |
| **Signal File** | 信号文件（`.turn-complete`），表示一个 Turn 执行结束，用于低成本完成检测 |
| **Marker** | 注入到 prompt 中的完成标记（如 `TCD_DONE:{req_id}`），要求 AI 在回复末尾输出 |
| **Notify Hook** | AI CLI 原生支持的回调机制（目前仅 Codex 支持），Turn 结束时自动执行外部脚本 |
| **Session** | AI CLI 维护的对话上下文，存储在各自的日志目录中 |
| **tmux Session** | tmux 创建的终端会话，每个 Job 对应一个独立的 tmux session |

---

## 6. 系统架构

```
┌──────────────────────────────────────────────────────────────────┐
│                        上游调用方                                 │
│                                                                  │
│  ┌──────────┐  ┌──────────┐  ┌──────────┐  ┌───────────────┐   │
│  │Claude    │  │OpenClaw  │  │Shell     │  │Python Script  │   │
│  │Code      │  │Agent     │  │Script    │  │(SDK import)   │   │
│  └────┬─────┘  └────┬─────┘  └────┬─────┘  └──────┬────────┘   │
│       │              │             │                │             │
│       └──────────────┼─────────────┼────────────────┘             │
│                      │  tcd CLI / Python SDK                      │
└──────────────────────┼────────────────────────────────────────────┘
                       │
                       ▼
┌──────────────────────────────────────────────────────────────────┐
│                   tmux-codingagent-driver (tcd)                   │
│                                                                  │
│  ┌────────────┐                                                  │
│  │  CLI       │  tcd start / send / status / output / check     │
│  │  (cli.py)  │  / wait / jobs / attach / kill / clean          │
│  └─────┬──────┘                                                  │
│        │                                                         │
│  ┌─────▼──────┐  ┌──────────────┐  ┌────────────────┐           │
│  │ Job Manager│  │Provider      │  │Response        │           │
│  │ (job.py)   │  │Registry      │  │Collector       │           │
│  │            │  │(provider.py) │  │(collector.py)  │           │
│  │ - create   │  │              │  │                │           │
│  │ - status   │  │ - codex      │  │ - session file │           │
│  │ - update   │  │ - claude     │  │ - capture-pane │           │
│  │ - cleanup  │  │ - gemini     │  │ - log file     │           │
│  └─────┬──────┘  └──────┬───────┘  └───────┬────────┘           │
│        │                │                   │                    │
│  ┌─────▼────────────────▼───────────────────▼──────┐             │
│  │              tmux Adapter (tmux_adapter.py)      │             │
│  │                                                  │             │
│  │  create_session()   send_keys()                  │             │
│  │  session_exists()   send_long_text()             │             │
│  │  capture_pane()     kill_session()               │             │
│  └─────────────────────┬────────────────────────────┘             │
└────────────────────────┼─────────────────────────────────────────┘
                         │
           ┌─────────────┼─────────────┐
           ▼             ▼             ▼
      ┌─────────┐  ┌─────────┐  ┌─────────┐
      │ tmux    │  │ tmux    │  │ tmux    │
      │ session │  │ session │  │ session │
      │         │  │         │  │         │
      │ codex   │  │ claude  │  │ gemini  │
      │ CLI     │  │ CLI     │  │ CLI     │
      └─────────┘  └─────────┘  └─────────┘
```

### 数据流

```
启动：tcd start → Job Manager 创建 Job JSON → Provider 构建命令
      → tmux Adapter 创建 session → 注入 prompt → AI 开始执行

检测：tcd check → 检查 signal file（优先）
      → Provider.detect_completion()（marker/空闲）→ 返回状态

收集：tcd output → Response Collector → session file / capture-pane / log file
      → 清理 ANSI → 返回干净文本
```

### 文件系统布局

```
~/.tcd/                           # tcd 运行时数据根目录
├── jobs/
│   ├── {id}.json                 # Job 元数据
│   ├── {id}.log                  # script 录制的终端日志
│   ├── {id}.prompt               # 原始 prompt 文件
│   └── {id}.turn-complete        # Turn 完成信号文件
└── config.toml                   # 可选全局配置
```

---

## 7. 功能需求

### FR-1: Provider 系统

**描述**: 可插拔的 AI CLI 适配器，每个 Provider 封装一种 AI CLI 的启动、通信和结果解析逻辑。

**统一接口**:

| 方法 | 职责 |
|------|------|
| `build_launch_command(job)` | 构建启动 AI CLI 的 shell 命令字符串 |
| `build_prompt_wrapper(message, req_id)` | 包装 prompt（添加 marker 等） |
| `detect_completion(job)` | 检测 Turn 是否完成 |
| `parse_response(job)` | 从 session 文件/日志解析 AI 响应 |
| `get_session_log_path(job)` | 返回 AI 原生 session 文件路径 |

**验收标准**:
- [ ] 新增一个 Provider 只需实现上述 5 个方法，代码量 < 150 行
- [ ] Provider 通过名称字符串注册和查找（`get_provider("codex")`）

---

### FR-2: tmux Adapter

**描述**: tmux 操作原语的 Python 封装，隔离所有 tmux 命令调用。

**核心操作**:

| 操作 | 对应 tmux 命令 | 说明 |
|------|---------------|------|
| `create_session(name, cmd, cwd)` | `tmux new-session -d -s {name} -c {cwd} '{cmd}'` | 创建 detached session |
| `session_exists(name)` | `tmux has-session -t {name}` | 检查 session 是否存在 |
| `send_keys(session, text)` | `tmux send-keys -t {session} '{text}' Enter` | 短文本注入（< 5000 字符） |
| `send_long_text(session, text)` | `tmux load-buffer {file}` + `tmux paste-buffer -t {session}` + `send-keys Enter` | 长文本注入（≥ 5000 字符） |
| `capture_pane(session, lines)` | `tmux capture-pane -t {session} -p [-S -]` | 读取终端内容 |
| `kill_session(session)` | `tmux kill-session -t {session}` | 销毁 session |

**关键设计决策**:
- 短文本阈值：5000 字符（来自 codex-orchestrator 经验值）
- 单引号转义：`text.replace("'", "'\\''")`
- 所有 subprocess 调用 timeout = 10s

**验收标准**:
- [ ] 创建 session 后 `session_exists()` 返回 True
- [ ] 5000 字符以上文本通过 `send_long_text` 正确注入
- [ ] `kill_session` 后 `session_exists()` 返回 False
- [ ] tmux 未安装时给出清晰错误信息

---

### FR-3: Job 管理

**描述**: 任务全生命周期管理，JSON 文件持久化。

**Job 状态机**:

```
                 ┌──────────┐
                 │ pending  │
                 └────┬─────┘
                      │ create_session 成功
                      ▼
                 ┌──────────┐
          ┌─────►│ running  │◄────┐
          │      └──┬───┬───┘     │
          │         │   │         │ send（追加指令）
          │         │   └─────────┘
          │         │
          │    ┌────┴────┐
          │    ▼         ▼
     ┌─────────┐   ┌─────────┐
     │completed│   │ failed  │
     └─────────┘   └─────────┘
```

**Job 数据结构**:

```python
@dataclass
class Job:
    id: str                           # 8 字节 hex (e.g. "a3f2b1c9")
    provider: str                     # "codex" | "claude" | "gemini"
    status: Literal["pending", "running", "completed", "failed"]
    prompt: str                       # 原始 prompt
    cwd: str                          # 工作目录
    tmux_session: str                 # "tcd-{provider}-{id}"
    model: str | None                 # 可选模型覆盖
    created_at: str                   # ISO 8601
    started_at: str | None
    completed_at: str | None
    result: str | None                # 最终输出
    error: str | None                 # 错误信息
    turn_count: int                   # Turn 计数
    turn_state: Literal["working", "idle", "context_limit"] | None
    last_agent_message: str | None    # 最近一条 AI 消息（截断 500 字符）
    timeout_minutes: int              # 超时时间，默认 60
```

**验收标准**:
- [ ] Job JSON 原子写入（写临时文件 → rename）
- [ ] `tcd jobs` 列出所有 Job，显示 id / provider / status / age
- [ ] `tcd clean` 清理 completed/failed 的 Job 及关联文件（.log / .prompt / .turn-complete）
- [ ] 超时 Job 自动标记为 failed

---

### FR-4: 完成检测

**描述**: 三层策略检测 AI Turn 是否完成，按优先级 fallback。

**策略优先级**:

```
1. Signal File  →  检查 ~/.tcd/jobs/{id}.turn-complete 是否存在
                   （由 notify-hook 或 marker scanner 写入）
   │
   │ 不存在
   ▼
2. Marker 检测  →  capture-pane / 读日志，扫描 TCD_DONE:{req_id}
   │
   │ 未找到
   ▼
3. 空闲检测     →  连续 N 秒 capture-pane 无变化 → 视为完成
   │
   │ 仍在变化
   ▼
4. 返回 "working"
```

**各 Provider 的检测策略**:

| Provider | 策略 1 (Signal File) | 策略 2 (Marker) | 策略 3 (空闲) |
|----------|---------------------|-----------------|--------------|
| Codex | notify-hook 写入（原生支持） | 不需要 | 不需要 |
| Claude Code | marker scanner 写入 | TCD_DONE 扫描 | 20s 空闲 |
| Gemini CLI | marker scanner 写入 | TCD_DONE 扫描 | 15s 空闲 |

**Marker 协议格式**:

```
TCD_REQ:{req_id}
{用户的实际消息}
请在回复完成后，在最后一行输出：TCD_DONE:{req_id}
```

- `req_id` 格式：`{job_id}-{turn_count}-{timestamp}`
- 从输出末尾向前扫描（避免读全文）
- 容忍末尾空行和噪音

**验收标准**:
- [ ] Codex Job 通过 notify-hook 在 Turn 结束后 < 1s 内检测到完成
- [ ] Claude/Gemini Job 通过 marker 扫描在 Turn 结束后 < 5s 内检测到完成
- [ ] Gemini 不输出 marker 时，空闲检测在 15s 后 fallback 成功
- [ ] `tcd check {id}` 的 exit code: 0=idle, 1=working, 2=context_limit

---

### FR-5: 响应收集

**描述**: 从多个数据源收集 AI 响应，多策略 fallback。

**收集优先级**:

```
1. Provider 专属 session 文件（结构化数据，如 Codex JSONL）
2. tmux capture-pane（session 仍存活时）
3. script 日志文件（session 已退出后的 fallback）
```

**ANSI 清理**:
- 移除所有 ANSI CSI / OSC / DCS / ESC 序列
- 移除 AI CLI 的 TUI 噪音行（状态栏、进度条、更新提示）
- 去除重复行
- 去除 marker 标记本身（TCD_REQ / TCD_DONE）

**验收标准**:
- [ ] Codex Job 完成后能解析出 token 用量 + 修改文件列表 + 摘要
- [ ] Claude Job 完成后能从 JSONL 提取响应文本
- [ ] tmux session 被 kill 后仍能从 .log 文件读取输出
- [ ] 输出不包含 ANSI 转义序列和 TUI 噪音

---

### FR-6: CLI 接口

**描述**: `tcd` 命令族，所有子命令。

#### tcd start

启动一个新 Job。

```bash
tcd start --provider <name> --prompt <text> [options]
```

| 参数 | 必填 | 默认值 | 说明 |
|------|------|--------|------|
| `--provider` / `-p` | 是 | — | codex / claude / gemini |
| `--prompt` / `-m` | 是 | — | 任务描述（也支持从 stdin 读取） |
| `--cwd` / `-d` | 否 | `.` | 工作目录 |
| `--model` | 否 | Provider 默认 | 模型名覆盖 |
| `--timeout` | 否 | 60 | 超时分钟数 |
| `--sandbox` | 否 | Provider 默认 | Codex sandbox 模式 |

**输出**:

```
Job started: a3f2b1c9
Provider: codex
tmux session: tcd-codex-a3f2b1c9
```

#### tcd send

向运行中的 Job 发送后续指令。

```bash
tcd send <job-id> <message>
tcd send <job-id> --file <path>    # 从文件读取长消息
```

#### tcd status

查看 Job 状态。

```bash
tcd status <job-id>              # 人类可读
tcd status <job-id> --json       # JSON（方便 Agent 解析）
```

**JSON 输出示例**:

```json
{
  "id": "a3f2b1c9",
  "provider": "codex",
  "status": "running",
  "turn_state": "idle",
  "turn_count": 1,
  "last_agent_message": "I've implemented the user registration...",
  "elapsed_seconds": 45
}
```

#### tcd output

获取 Job 输出。

```bash
tcd output <job-id>              # 最终响应（清理后）
tcd output <job-id> --full       # 完整 scrollback（含 TUI 输出）
tcd output <job-id> --raw        # 原始日志（含 ANSI）
```

#### tcd check

非阻塞完成检测（设计给上游 Agent 轮询）。

```bash
tcd check <job-id>
# exit 0 → idle（Turn 完成，可发新指令或读结果）
# exit 1 → working（仍在执行）
# exit 2 → context_limit（上下文耗尽）
# exit 3 → not_found（Job 不存在）
```

#### tcd wait

阻塞等待 Job 完成。

```bash
tcd wait <job-id> --timeout 300   # 等待最多 300 秒
# exit 0 → completed
# exit 1 → failed
# exit 2 → timeout
```

#### tcd jobs

列出所有 Job。

```bash
tcd jobs                         # 列出所有
tcd jobs --status running        # 筛选状态
tcd jobs --json                  # JSON 格式
```

#### tcd attach

连接到 Job 的 tmux session（调试用）。

```bash
tcd attach <job-id>              # tmux attach-session -t tcd-codex-a3f2b1c9
```

#### tcd kill

终止 Job。

```bash
tcd kill <job-id>                # kill session + 标记 failed
tcd kill --all                   # kill 所有 running Job
```

#### tcd clean

清理已完成的 Job。

```bash
tcd clean                        # 清理 completed + failed
tcd clean --all                  # 清理所有（含 running）
tcd clean --before 7d            # 清理 7 天前的
```

**验收标准**:
- [ ] 所有子命令的 `--help` 输出清晰
- [ ] `--json` 输出可被 `jq` 解析
- [ ] 无效 job-id 给出友好错误而非 traceback
- [ ] 支持 stdin 输入 prompt：`echo "fix bug" | tcd start -p codex -m -`

---

### FR-7: Python SDK

**描述**: 可编程调用接口，支持 import 使用。

```python
from tcd import TCD

driver = TCD()

# 启动任务
job = driver.start(provider="codex", prompt="实现用户注册", cwd="/path/to/project")

# 非阻塞检测
result = driver.check(job.id)   # -> CheckResult(state="working" | "idle" | "context_limit")

# 阻塞等待
driver.wait(job.id, timeout=300)

# 获取输出
output = driver.output(job.id)  # -> str

# 发送后续指令
driver.send(job.id, "添加单元测试")

# 列出任务
jobs = driver.jobs(status="running")  # -> list[Job]

# 清理
driver.kill(job.id)
driver.clean()
```

**验收标准**:
- [ ] `from tcd import TCD` 不报错
- [ ] SDK 接口与 CLI 功能 1:1 对应
- [ ] 所有方法有 type hints

---

### FR-8: 上游集成接口

**描述**: 让 Claude Code / OpenClaw 能方便地使用 tcd。

#### 8a. CLAUDE.md Skill 集成

提供一个标准的 CLAUDE.md 片段，让 Claude Code 知道如何使用 tcd：

```markdown
## 可用工具：tcd（AI 任务分派）

使用 `tcd` 命令将子任务分派给其他 AI CLI 执行。

### 启动任务
tcd start -p <provider> -m "<prompt>" -d <cwd>
- provider: codex（擅长代码实现）/ claude（擅长分析和文档）/ gemini（擅长前端和审查）
- 返回 Job ID

### 检查完成
tcd check <job-id>
- exit 0 = 完成（可获取结果）
- exit 1 = 进行中（继续等待）

### 获取结果
tcd output <job-id>

### 发送后续指令
tcd send <job-id> "<message>"

### 重要规则
- start 后用 check 轮询，间隔 3-5 秒
- 不要用 sleep 长时间等待
- 检查到完成后立即用 output 获取结果
- 一个 job 完成前不要启动同 provider 的新 job（防止资源竞争）
```

#### 8b. MCP Server（Phase 4 预留）

未来封装为 MCP server，提供 tools：
- `tcd_start(provider, prompt, cwd)` → job_id
- `tcd_check(job_id)` → state
- `tcd_output(job_id)` → text
- `tcd_send(job_id, message)` → ok

**验收标准**:
- [ ] CLAUDE.md 片段可直接复制使用
- [ ] Claude Code 通过 bash 工具成功调用 tcd 完成任务

---

## 8. Provider 详细规格

### 8.1 Codex Provider

| 项 | 值 |
|----|-----|
| CLI 命令 | `codex` |
| 默认模型 | CLI 默认（不覆盖） |
| 权限模式 | `-a never`（自动批准所有操作） |
| Sandbox | 可配置：`read-only` / `workspace-write` / `danger-full-access` |
| 完成检测 | notify-hook（原生支持） |
| Session 文件 | `~/.codex/sessions/*.jsonl` |

**启动命令模板**:

```bash
script -q "{log_file}" codex \
  -c 'notify=["python3", "{tcd_notify_hook}", "{job_id}"]' \
  -a never \
  -s {sandbox} \
  [-c 'model="{model}"'] \
  [-c 'model_reasoning_effort="{effort}"'] \
  ; echo "\n\n[tcd: session complete]"; read
```

**注意事项**:
- macOS 和 Linux 的 `script` 命令参数顺序不同，需平台检测
- 启动后 sleep 1s 等待 TUI 初始化
- 可能需要跳过更新提示（send-keys "3" + Enter）
- send-keys 后 sleep 0.3s 等待 TUI 处理

**Notify Hook 工作流**:

```
Codex agent turn 结束
  → 调用 tcd-notify-hook {job_id}（传入 JSON payload via argv）
  → 解析 payload，检查 type == "agent-turn-complete"
  → 写入 ~/.tcd/jobs/{job_id}.turn-complete（JSON: turnId, lastAgentMessage, timestamp）
  → 更新 job.json 中的 turn_count, turn_state, last_agent_message
```

**Session 文件解析**:

从 `~/.codex/sessions/` 解析 JSONL 获取：
- Token 用量（input / output / context_window）
- 修改的文件列表（`apply_patch` tool call）
- 任务摘要（最后一条 agent_message）

---

### 8.2 Claude Code Provider

| 项 | 值 |
|----|-----|
| CLI 命令 | `claude` |
| 权限模式 | `--dangerously-skip-permissions` |
| 完成检测 | TCD_DONE marker + 空闲检测（20s） |
| Session 文件 | `~/.claude/projects/{key}/*.jsonl` |

**启动命令模板**:

```bash
script -q "{log_file}" claude \
  --dangerously-skip-permissions \
  [-m "{model}"] \
  ; echo "\n\n[tcd: session complete]"; read
```

**Prompt 包装**:

```
TCD_REQ:{req_id}
{用户消息}
请在回复完成后，在最后一行输出：TCD_DONE:{req_id}
```

**Session 文件发现**:

Claude Code 的 session 文件位于 `~/.claude/projects/` 下，路径含项目哈希。需要：
1. 从 tmux session 环境变量获取（如果可用）
2. 按 mtime 扫描最新的 session 文件
3. 匹配 job 创建时间后的 session

**注意事项**:
- Claude Code 的 `--dangerously-skip-permissions` 跳过所有权限检查
- Claude Code 可能触发子代理（subagent），需要等待所有子代理完成
- 空闲检测阈值设为 20s（Claude 思考时间较长）

---

### 8.3 Gemini CLI Provider

| 项 | 值 |
|----|-----|
| CLI 命令 | `gemini` |
| 完成检测 | TCD_DONE marker + 空闲检测（15s） |
| Session 文件 | 无标准位置，依赖 capture-pane |

**启动命令模板**:

```bash
script -q "{log_file}" gemini \
  ; echo "\n\n[tcd: session complete]"; read
```

**注意事项**:
- Gemini CLI 经常不遵守 marker 输出要求，空闲检测是主要依赖
- Gemini 0.29+ 的 session 发现有 dual hash 问题（参考 CCB 经验）
- 空闲检测阈值 15s（Gemini 响应通常较快）

---

## 9. 非功能需求

### NFR-1: 性能

| 指标 | 目标 |
|------|------|
| Job 启动（从命令到 AI 开始接收 prompt） | < 3s |
| 完成检测延迟（AI 完成到 tcd 感知） | < 3s（notify-hook）/ < 20s（空闲检测） |
| `tcd check` 执行时间 | < 0.5s |
| `tcd status --json` 执行时间 | < 0.5s |
| 并行 Job 数 | 至少 5 个不互相干扰 |

### NFR-2: 可靠性

| 机制 | 说明 |
|------|------|
| 日志持久化 | `script -q` 录制，tmux session 退出后仍可读 |
| 超时兜底 | 默认 60 分钟无活动自动 kill + 标记 failed |
| 原子写入 | Job JSON 通过 tmpfile + rename 防止写入中断导致损坏 |
| Session 存活检测 | `tmux has-session` 检查，session 已退出则标记 completed |
| 进程隔离 | 每个 Job 独立 tmux session，互不影响 |

### NFR-3: 可维护性

| 指标 | 目标 |
|------|------|
| 新增 Provider | < 150 行代码 |
| 测试覆盖率 | 核心模块 > 80% |
| 文档 | README + PRD + 场景文档 + CLI help |

### NFR-4: 日志与调试

| 功能 | 说明 |
|------|------|
| `tcd attach` | 直接连接 tmux session 查看实时输出 |
| `tcd output --raw` | 查看原始日志（含 ANSI） |
| Job JSON | 完整状态快照，可用 `cat` / `jq` 直接查看 |
| tcd 自身日志 | `~/.tcd/tcd.log`，DEBUG 级别可开启 |

---

## 10. 错误处理与边界情况

### 10.1 tmux Session 意外退出

**触发**: AI CLI crash、OOM、用户手动 kill
**检测**: `session_exists()` 返回 False
**处理**:
1. 读取 `.log` 文件获取最后输出
2. 标记 Job 为 `completed`（如果有 "session complete" marker）或 `failed`
3. 保留 `.log` 文件供事后分析

### 10.2 AI CLI 超时/挂起

**触发**: AI 进入无限循环、网络断开、API 限流
**检测**: `.log` 文件 mtime 超过 timeout 分钟无更新
**处理**:
1. kill tmux session
2. 标记 Job 为 `failed`，error = "timeout after {N} minutes"

### 10.3 并发冲突

**触发**: 同一项目目录同时启动多个 Job
**处理**: 允许（每个 Job 独立 tmux session），但发出警告：
```
Warning: Another job (xxx) is already running in the same directory.
```

### 10.4 长提示处理

**触发**: prompt > 5000 字符
**处理**: 自动切换到 `send_long_text`（load-buffer + paste-buffer）

### 10.5 AI 不遵守 Marker

**触发**: Claude/Gemini 忽略 TCD_DONE 输出要求
**处理**: 空闲检测自动兜底（15-20s 无新输出 → 视为完成）

### 10.6 tmux 未安装

**触发**: 系统没有 tmux
**处理**: `tcd` 启动时检测，给出安装指引：
```
Error: tmux not found. Install with: brew install tmux (macOS) or apt install tmux (Linux)
```

### 10.7 AI CLI 未安装

**触发**: 指定的 provider CLI 不在 PATH 中
**处理**: `tcd start` 时检测，给出提示：
```
Error: codex not found in PATH. Install from: https://github.com/openai/codex
```

---

## 11. MVP 分阶段计划

### Phase 1: 基础框架 + Codex Driver

**目标**: 能通过 `tcd start -p codex` 启动 Codex 并获取结果

交付物：
- [x] tmux_adapter.py — tmux 操作封装
- [x] provider.py — Provider 抽象基类 + 注册表
- [x] providers/codex.py — Codex Provider 实现
- [x] job.py — Job 数据结构 + 持久化
- [x] collector.py — 响应收集（session file + capture-pane + log fallback）
- [x] output_cleaner.py — ANSI 清理
- [x] notify_hook.py — Codex notify hook 脚本
- [x] cli.py — tcd CLI 入口（start / status / output / check / wait / jobs / attach / kill / clean）
- [x] pyproject.toml — 项目配置
- [x] tests/ — 核心模块单元测试
- [x] README.md

### Phase 2: Claude Code Driver

**目标**: 能通过 `tcd start -p claude` 驱动 Claude Code

交付物：
- [x] providers/claude.py — Claude Code Provider
- [x] marker 协议实现（TCD_REQ / TCD_DONE 注入与扫描）
- [x] 空闲检测模块
- [x] Claude session file 解析

### Phase 3: Gemini CLI Driver

**目标**: 能通过 `tcd start -p gemini` 驱动 Gemini CLI

交付物：
- [x] providers/gemini.py — Gemini CLI Provider
- [x] Gemini 特异性处理（dual hash、空闲检测为主）

### Phase 4: 高级功能

- [x] Python SDK 封装（`from tcd import TCD`）
- [x] CLAUDE.md skill 模板
- [ ] MCP server 封装（可选，未实现）
- [ ] 跨 AI context transfer（可选，未实现）
- [ ] `tcd pipe`：流水线模式（可选，未实现）

---

## 12. 技术决策记录

| # | 决策 | 选项 | 选择 | 理由 |
|---|------|------|------|------|
| TD-1 | 实现语言 | Python / TypeScript / Rust | **Python 3.10+** | CCB 参考实现是 Python；Claude Code 生态友好；subprocess 调用简单 |
| TD-2 | 包管理 | pip / poetry / uv | **uv** | 速度快，现代，lockfile 支持好 |
| TD-3 | CLI 框架 | argparse / click / typer | **click** | 轻量成熟，子命令支持好，不需要 typer 的 type hint 魔法 |
| TD-4 | 架构模式 | daemon (TCP) / CLI + 文件 | **CLI + 文件** | codex-orchestrator 验证了此模式足够，避免 CCB 的 daemon 复杂度 |
| TD-5 | 状态存储 | SQLite / JSON 文件 | **JSON 文件** | 简单直接，可用 `cat`/`jq` 调试，不需要 migration |
| TD-6 | 完成检测 | 纯轮询 / 纯 hook / 混合 | **混合三策略** | notify-hook 最可靠但仅 Codex 支持；marker 通用但不保证；空闲检测兜底 |
| TD-7 | 日志录制 | tmux capture / script / 两者 | **script + capture 双备** | script 持久化（session 退出后仍可读），capture 实时（低延迟） |

---

## 13. 风险与缓解

| 风险 | 影响 | 概率 | 缓解 |
|------|------|------|------|
| AI CLI 更新导致行为变化 | Provider 失效 | 中 | 版本检测 + 日志告警 + Provider 隔离（坏一个不影响其他） |
| Marker 协议不可靠（AI 不输出） | 无法检测完成 | 高（Gemini） | 空闲检测兜底，15-20s 延迟可接受 |
| tmux send-keys 特殊字符转义问题 | 命令注入 / 丢失字符 | 中 | 长文本走 load-buffer；短文本严格转义 |
| macOS vs Linux 的 `script` 参数差异 | 启动失败 | 低 | 平台检测，两套参数模板 |
| 并行 Job 资源竞争（CPU/内存） | AI 变慢或 OOM | 低 | 文档建议最多 3-5 个并行 Job |
| Claude Code subagent 导致 marker 被子进程吞掉 | 完成检测失败 | 中 | 空闲检测兜底 + 监控主 session 状态 |

---

## 14. 成功标准

### MVP（Phase 1 完成时）

- [ ] `tcd start -p codex -m "写一个 hello world" -d /tmp/test` 在 3s 内启动
- [ ] Codex 完成后 `tcd check` 返回 0（idle）
- [ ] `tcd output` 返回干净的 Codex 响应文本
- [ ] `tcd send` 能追加指令并触发新 Turn
- [ ] `tcd jobs` 正确显示所有 Job

### 全量（Phase 3 完成时）

- [ ] 三种 Provider 都能正常启动、检测完成、收集结果
- [ ] Claude Code 能通过 bash 工具调用 tcd 分派任务给 Codex
- [ ] 能同时运行 3 个不同 Provider 的 Job 互不干扰
- [ ] 测试覆盖率 > 80%
- [ ] README 文档完整

---

## 附录 A: 参考项目

| 项目 | 仓库 | 复用价值 |
|------|------|---------|
| claude_code_bridge (CCB) | github.com/bfly123/claude_code_bridge | 多 Provider 设计、marker 协议、防循环 guardrail |
| codex-orchestrator | github.com/kingbootoshi/codex-orchestrator | tmux 操作模式、notify-hook、Job 管理、ANSI 清理 |

详细分析见 [对比报告](research/comparison-ccb-vs-codex-orchestrator.md)。

## 附录 B: 名词缩写

| 缩写 | 全称 |
|------|------|
| tcd | tmux-codingagent-driver |
| CCB | claude_code_bridge |
| TUI | Text User Interface |
| ANSI | American National Standards Institute（终端转义序列） |
| JSONL | JSON Lines（每行一个 JSON 对象） |
| MCP | Model Context Protocol |
