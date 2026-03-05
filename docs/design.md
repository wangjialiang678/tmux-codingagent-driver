# tmux-codingagent-driver 设计方案

**日期**: 2026-03-02
**状态**: IMPLEMENTED（Phase 1-4 全部完成）

---

## 1. 项目定位

一个通用的 **tmux-based AI CLI driver**，让上游 Agent（Claude Code、OpenClaw 或任何编排器）通过 tmux 驱动下游 AI CLI 工具（Codex、Claude Code、Gemini CLI）执行编程任务。

**核心价值**：不走 API，利用 AI CLI 的原生 session 管理，实现极低 token 开销（50-200 tokens/call）的多 AI 编程任务分派。

```
┌─────────────────────────────────────────────────────────┐
│  上游 Agent（Claude Code / OpenClaw / 自定义脚本）       │
│  通过 CLI 命令或 Python/TS SDK 调用                      │
└──────────────────────┬──────────────────────────────────┘
                       │
                       ▼
┌─────────────────────────────────────────────────────────┐
│             tmux-codingagent-driver                      │
│                                                         │
│  ┌─────────┐  ┌──────────┐  ┌──────────┐               │
│  │ Job Mgr │  │ Provider │  │ Response │               │
│  │         │  │ Registry │  │ Collector│               │
│  └────┬────┘  └────┬─────┘  └────┬─────┘               │
│       │            │              │                      │
│       ▼            ▼              ▼                      │
│  ┌─────────────────────────────────────┐                │
│  │         tmux Adapter Layer          │                │
│  │  send-keys / capture-pane /         │                │
│  │  load-buffer / new-session          │                │
│  └──────────────────┬──────────────────┘                │
└─────────────────────┼───────────────────────────────────┘
                      │
        ┌─────────────┼─────────────┐
        ▼             ▼             ▼
   ┌─────────┐  ┌─────────┐  ┌─────────┐
   │ Codex   │  │ Claude  │  │ Gemini  │
   │ CLI     │  │ Code    │  │ CLI     │
   │ (tmux)  │  │ (tmux)  │  │ (tmux)  │
   └─────────┘  └─────────┘  └─────────┘
```

---

## 2. 设计原则

1. **CLI-first**：主入口是命令行工具，方便任何 Agent 通过 `bash` 调用
2. **Provider 可插拔**：每种 AI CLI 是一个 provider，统一接口，独立实现
3. **无 Daemon**：不搞 TCP server，用 Job 文件 + 信号文件管理状态（借鉴 codex-orchestrator）
4. **双重完成检测**：notify-hook（如果 CLI 支持）+ marker 协议（通用 fallback）
5. **最小依赖**：Python 3.10+，仅依赖 tmux

---

## 3. 核心模块设计

### 3.1 Provider 抽象

```python
# provider.py
class Provider(ABC):
    """每种 AI CLI 的适配器"""

    name: str                    # "codex" | "claude" | "gemini"
    cli_command: str             # "codex" | "claude" | "gemini"

    @abstractmethod
    def build_launch_command(self, job: Job) -> str:
        """构建启动 AI CLI 的 shell 命令"""

    @abstractmethod
    def build_prompt_wrapper(self, message: str, req_id: str) -> str:
        """包装 prompt（添加完成标记等）"""

    @abstractmethod
    def detect_completion(self, job: Job) -> CompletionResult | None:
        """检测任务是否完成，返回结果或 None"""

    @abstractmethod
    def parse_response(self, job: Job) -> str:
        """从日志/session 文件解析 AI 响应"""

    @abstractmethod
    def get_session_log_path(self, job: Job) -> Path | None:
        """获取 AI 原生 session 文件路径"""
```

### 3.2 已知 Provider 实现策略

| Provider | 启动命令 | 完成检测 | 响应解析 |
|----------|---------|---------|---------|
| **Codex** | `codex -a never -c notify=[hook] ...` | notify-hook 信号文件（原生）| `~/.codex/sessions/*.jsonl` |
| **Claude Code** | `claude --dangerously-skip-permissions` | CCB_DONE marker（prompt 注入）| `~/.claude/projects/` JSONL |
| **Gemini CLI** | `gemini ...` | CCB_DONE marker + 15s 空闲检测 | capture-pane 全文 |

### 3.3 Job 管理

```python
# job.py
@dataclass
class Job:
    id: str                     # 8字节 hex
    provider: str               # "codex" | "claude" | "gemini"
    status: str                 # "pending" | "running" | "completed" | "failed"
    prompt: str
    cwd: str
    tmux_session: str           # "tcd-{provider}-{id}"
    created_at: str
    started_at: str | None
    completed_at: str | None
    result: str | None
    error: str | None
    turn_count: int
    turn_state: str             # "working" | "idle" | "context_limit"
    last_agent_message: str | None

# 存储：~/.tcd/jobs/{id}.json
# 信号：~/.tcd/jobs/{id}.turn-complete
# 日志：~/.tcd/jobs/{id}.log
```

### 3.4 tmux Adapter

```python
# tmux_adapter.py
class TmuxAdapter:
    """tmux 操作原语封装"""

    LONG_PROMPT_THRESHOLD = 5000  # 字符

    def create_session(self, name: str, cmd: str, cwd: str) -> bool
    def session_exists(self, name: str) -> bool
    def send_keys(self, session: str, text: str) -> bool
    def send_long_text(self, session: str, text: str) -> bool:
        """长文本：写临时文件 → load-buffer → paste-buffer"""
    def capture_pane(self, session: str, lines: int = -1) -> str | None
    def kill_session(self, session: str) -> bool
```

### 3.5 Response Collector

```python
# collector.py
class ResponseCollector:
    """统一响应收集，多策略 fallback"""

    def collect(self, job: Job) -> str | None:
        # 1. Provider 专属解析（session 文件）
        result = provider.parse_response(job)
        if result: return result

        # 2. tmux capture-pane
        result = tmux.capture_pane(job.tmux_session)
        if result: return clean_ansi(result)

        # 3. script 日志文件
        log_path = job_log_path(job.id)
        if log_path.exists(): return clean_ansi(log_path.read_text())

        return None
```

---

## 4. CLI 接口设计

```bash
# 启动任务
tcd start --provider codex --prompt "实现用户注册功能" --cwd /path/to/project
tcd start --provider claude --prompt "修复 login bug" --cwd /path/to/project
tcd start --provider gemini --prompt "审查这段代码" --cwd /path/to/project

# 向运行中的任务发送后续指令
tcd send <job-id> "添加单元测试"

# 查看任务状态
tcd status <job-id>           # 单个任务状态
tcd status <job-id> --json    # JSON 格式（方便 Agent 解析）
tcd jobs                      # 列出所有任务

# 获取输出
tcd output <job-id>           # 获取最终输出
tcd output <job-id> --full    # 完整 scrollback

# 等待完成（阻塞）
tcd wait <job-id> --timeout 300

# 检查 turn 完成（非阻塞）
tcd check <job-id>            # exit 0 = idle, exit 1 = working, exit 2 = context_limit

# 连接到 tmux session（调试用）
tcd attach <job-id>

# 清理
tcd kill <job-id>
tcd clean                     # 清理已完成/失败的任务
```

---

## 5. 完成检测双策略

### 策略 A：notify-hook（Provider 原生支持时优先）

Codex 原生支持 `notify` 配置。当 agent turn 结束时，Codex 自动调用我们的 hook 脚本：

```
codex -c 'notify=["python3", "tcd-notify-hook", "{jobId}"]' ...
```

Hook 写入信号文件 `~/.tcd/jobs/{id}.turn-complete`，上游 Agent 轮询此文件即可。

### 策略 B：Marker 协议（通用 fallback）

对于不支持 notify-hook 的 CLI（如 Claude Code、Gemini），注入 marker 到 prompt：

```
TCD_REQ:{req_id}
{实际消息}
回复完成后请在末尾输出：TCD_DONE:{req_id}
```

Collector 扫描日志/capture-pane 输出，找到 `TCD_DONE:{req_id}` 即视为完成。

### 策略 C：空闲检测（兜底）

如果 AI 不输出 marker（Gemini 经常如此），fallback 到空闲检测：
- 每 2 秒 capture-pane，对比内容
- 连续 15 秒无变化 → 视为完成

---

## 6. 上游 Agent 集成方式

### 6.1 Claude Code 集成（通过 CLAUDE.md + bash skill）

在项目 CLAUDE.md 中定义 `tcd` 命令用法，Claude Code 通过 bash 工具调用：

```markdown
# CLAUDE.md
## 可用工具：tcd（AI 任务分派）
- `tcd start --provider codex --prompt "..." --cwd .` 启动 Codex 执行任务
- `tcd check <id>` 检查是否完成（非阻塞）
- `tcd output <id>` 获取结果
- **重要**：start 后立即用 check 轮询，不要 sleep 等待
```

### 6.2 OpenClaw / 自定义 Agent 集成

通过 subprocess 调用 CLI 或导入 Python 模块：

```python
from tcd import start_job, check_job, get_output

job = start_job(provider="codex", prompt="...", cwd="/path")
while not check_job(job.id).is_idle:
    time.sleep(2)
result = get_output(job.id)
```

### 6.3 MCP Server 集成（可选扩展）

未来可以封装为 MCP server，提供 `tcd_start`、`tcd_check`、`tcd_output` 等 tools，让 Claude Code 直接通过 MCP 调用而非 bash。

---

## 7. 对两个参考项目的取舍

### 从 codex-orchestrator 拿来

- [x] tmux 操作核心流程（create → send-keys → capture-pane）
- [x] `script -q` 日志录制
- [x] 长提示 load-buffer/paste-buffer 策略
- [x] notify-hook 信号文件机制
- [x] `echo marker; read` 防会话退出
- [x] Job JSON 文件管理
- [x] sleep 时序经验值
- [x] ANSI 清理逻辑

### 从 CCB 拿来

- [x] 多 Provider 抽象层设计
- [x] CCB_DONE marker 协议思路（我们的 TCD_DONE）
- [x] Per-session 串行化（防并发）
- [x] Async guardrail 防循环模式
- [x] 终端 backend 抽象（未来支持 WezTerm）
- [x] Context transfer 思路（跨 AI 上下文迁移）

### 不拿

- CCB 的 daemon TCP server 架构（过重）
- CCB 的 Memory-First 三层存储（过早优化）
- codex-orchestrator 的 Codex 专属硬编码（我们要通用）

---

## 8. MVP 范围

### Phase 1：Codex Driver（可直接复用 codex-orchestrator 90% 逻辑）

- [x] tmux adapter 基础操作
- [x] Codex provider（启动、notify-hook、session 解析）
- [x] Job 管理（start/status/output/check/kill）
- [x] CLI 入口（tcd 命令）
- [x] ANSI 输出清理

### Phase 2：Claude Code Driver

- [x] Claude provider（启动、marker 协议、JSONL session 解析）
- [x] 空闲检测 fallback

### Phase 3：Gemini CLI Driver

- [x] Gemini provider（启动、marker + 空闲检测）

### Phase 4：高级功能

- [x] Python SDK 封装（`from tcd import TCD`）
- [ ] MCP server 封装（未实现）
- [ ] 跨 AI context transfer（未实现）
- [ ] WezTerm backend（未实现）
- [ ] 并行多 Job 编排（未实现）

---

## 9. 技术选型

| 选项 | 决定 | 理由 |
|------|------|------|
| 语言 | **Python 3.10+** | 与 CCB 一致，Claude Code 生态友好 |
| 包管理 | **uv** | 快速，现代 |
| CLI 框架 | **click** | 轻量，成熟 |
| 依赖 | tmux (系统) | 最小依赖 |
| 测试 | pytest | 标准 |

---

## 10. 目录结构（预期）

```
tmux-codingagent-driver/
├── src/tcd/
│   ├── __init__.py
│   ├── cli.py              # click CLI 入口
│   ├── tmux_adapter.py     # tmux 操作封装
│   ├── job.py              # Job 数据结构 + 持久化
│   ├── provider.py         # Provider 抽象基类
│   ├── providers/
│   │   ├── codex.py        # Codex provider
│   │   ├── claude.py       # Claude Code provider
│   │   └── gemini.py       # Gemini CLI provider
│   ├── collector.py        # 响应收集（多策略 fallback）
│   ├── notify_hook.py      # notify-hook 脚本
│   ├── output_cleaner.py   # ANSI 清理
│   └── config.py           # 全局配置
├── bin/
│   └── tcd-notify-hook     # notify hook 可执行脚本
├── tests/
├── docs/
│   ├── design.md           # 本文档
│   └── research/           # 调研报告
├── pyproject.toml
└── README.md
```

---

## 11. 结论

**两个参考项目都不能直接拿来用**，但都提供了关键的设计模式和经验：

- **codex-orchestrator** 提供了最直接的 tmux 驱动 Codex 的实现参考（~90% 可移植），但它只支持 Codex 且是 TypeScript
- **CCB** 提供了多 AI 协作的架构蓝图和大量边界修复经验，但架构太重不适合直接嵌入

我们的方案是：**用 codex-orchestrator 的实现模式 + CCB 的架构设计思路**，构建一个 Python 轻量级通用 AI CLI driver。
