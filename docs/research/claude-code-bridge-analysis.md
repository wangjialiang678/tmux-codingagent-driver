# 调研报告: claude_code_bridge (bfly123) 源码架构深度分析

**日期**: 2026-03-02
**版本**: v5.2.6
**仓库**: https://github.com/bfly123/claude_code_bridge
**任务**: 深度分析 CCB 的 daemon 架构、token 效率机制、跨 AI 协作协议

---

## 调研摘要

Claude Code Bridge (CCB) 是一个基于终端分屏的多 AI 协作平台，通过 tmux/WezTerm 驱动 Claude、Codex、Gemini、OpenCode、Droid 在独立窗格中并行运行。其核心创新是"Terminal-as-Bus"架构：不走 API，直接通过终端注入/日志读取实现双向通信，每次调用只发送任务指令（50-200 tokens），AI 的完整上下文保存在各自的 CLI session 中。这是一套成熟的生产级方案（v5.2.6，CHANGELOG 记录了大量边界情况修复）。

---

## 一、整体架构概览

```
用户 / 脚本
    │
    ▼
bin/ask (统一入口) ─── JSON-RPC over TCP ──► askd daemon (TCP server)
                                                    │
                      ┌────────────────────────────┼────────────────────────────┐
                      ▼                            ▼                            ▼
               caskd (Claude)              gaskd (Gemini)              oaskd (OpenCode)
                      │                            │                            │
               terminal inject              terminal inject              terminal inject
               log read                    log read                    log read
                      │                            │                            │
               tmux/WezTerm pane            tmux/WezTerm pane            tmux/WezTerm pane
               (claude session)            (gemini session)            (opencode session)
```

### 核心设计哲学

- **不用 API**：所有 AI 都通过 CLI 工具在终端中运行（claude、codex、gemini CLI 等）
- **终端即总线**：向终端窗格注入文字 = 发送请求；读取 session 日志文件 = 接收响应
- **会话持久化**：每个 AI 维持自己的独立 session，上下文在 AI 侧保留，不需要每次发全历史
- **Token 效率**：指令精简（50-200 tokens/call），全历史由 AI session 自维护

---

## 二、Daemon 架构详解

### 2.1 Daemon 层次结构

```
askd (统一入口 daemon, bin/askd)
  └─ 按 provider 分派到各 daemon module:
       ├─ askd.daemon/caskd  - Claude daemon (lask/laskd)
       ├─ askd.daemon/gaskd  - Gemini daemon
       ├─ askd.daemon/oaskd  - OpenCode daemon
       ├─ askd.daemon/daskd  - Droid daemon
       └─ askd.daemon/laskd  - 通用 Claude daemon (另一套)
```

注意：providers.py 中注册了 5 个 provider：

| Provider | Daemon Key | Protocol Prefix | Session File |
|----------|------------|-----------------|--------------|
| Claude   | caskd      | cask            | .codex-session |
| Gemini   | gaskd      | gask            | .gemini-session |
| OpenCode | oaskd      | oask            | .opencode-session |
| Claude2  | laskd      | lask            | .claude-session |
| Droid    | daskd      | dask            | .droid-session |

### 2.2 Daemon 生命周期

- **自动启动**：第一个请求到来时，client 检查 daemon 是否运行，未运行则 fork 启动
- **空闲自停**：60 秒无请求后自动关闭（通过 idle monitor 线程）
- **父进程监控**：如果启动 daemon 的父进程退出，daemon 自动关闭
- **状态文件**：daemon 写 `~/.ccb/run/{prefix}d.json`，包含 pid/host/port/token

### 2.3 TCP Server 实现 (askd_server.py)

```python
class Server(socketserver.ThreadingTCPServer):
    allow_reuse_address = True
```

- 每个连接创建新线程（ThreadingTCPServer 模式）
- Token 认证（随机生成，写入状态文件）
- 三类消息：ping / shutdown / request
- 两个监控守护线程：idle_timeout_monitor + parent_process_monitor
- 共享状态：`active_requests` 计数器 + `last_activity` 时间戳（通过 activity_lock 保护）

### 2.4 Per-Session Worker Pool (worker_pool.py)

```
PerSessionWorkerPool
  └─ dict[session_key → BaseSessionWorker]
       └─ BaseSessionWorker (threading.Thread)
            └─ 内部 queue，串行处理同一 session 的请求
```

- 每个 session 有独立 worker 线程，保证 session 内请求串行
- 不同 session 之间并行
- Worker 死亡时自动重建（health check in pool）

---

## 三、通信协议 (ccb_protocol.py)

### 3.1 请求 ID 格式

```
CCB_REQ_ID: YYYYMMDD-HHMMSS-mmm-PID-counter
```

示例：`CCB_REQ_ID: 20260302-143022-456-12345-001`

### 3.2 Prompt 包装机制 (wrap_codex_prompt)

发送给 AI 的 prompt 被包装为：

```
CCB_REQ_ID: {req_id}
CCB_BEGIN:
{实际用户消息，50-200 tokens}
在回复末尾加上：CCB_DONE:{req_id}
```

**这就是 token 效率的关键**：
- 只发送任务指令（极短）
- 历史上下文由 AI 的 session 自维护，不随每次请求重发
- 完成标记让 poller 知道何时停止读取

### 3.3 完成检测

```python
def is_done_text(text: str, req_id: str) -> bool:
    """从响应末尾向前扫描，找到有效的 CCB_DONE:{req_id} 标记"""
```

- 容忍末尾噪音（空行、其他标记）
- 从文本末尾向前扫描
- 过滤掉 harness 可能附加的通用标记

### 3.4 JSON-RPC 协议 (askd_rpc.py)

Client → Daemon 通信：

```json
请求: {"type": "{prefix}.request", "v": 1, "id": "...", "token": "...",
       "work_dir": "...", "timeout_s": float, "message": "...", "quiet": bool}

响应: {"type": "{prefix}.response", "reply": "...", "exit_code": int}
```

消息格式：newline-delimited JSON over TCP socket

---

## 四、各 AI 通信层实现

### 4.1 统一抽象模式

每个 AI 有对应的 `*_comm.py` 文件，实现两个核心类：

```
XxxLogReader      - 读取 AI session 日志文件
XxxCommunicator   - 双向通信（注入 + 读取）
    ├── ask_async()  - 异步发送，不等响应
    └── ask_sync()   - 同步发送，等待 CCB_DONE 标记
```

### 4.2 Claude 通信 (claude_comm.py)

- **日志位置**：`~/.claude/projects/<key>/` 目录下的 session 文件
- **会话发现**：多种策略（环境变量 → 文件系统扫描 → index 查找）
- **消息注入**：通过 tmux `send-keys` 或 WezTerm 文字注入
- **响应读取**：解析 Claude 的 JSONL session 日志，提取 response items，过滤 thinking blocks
- **Subagent 支持**：可跟踪 Claude 的子代理日志

### 4.3 Codex 通信 (codex_comm.py)

- **日志位置**：`~/.codex/sessions/`
- **双传输模式**：
  - tmux 模式：通过 FIFO 管道发送（更可靠）
  - WezTerm 模式：直接文字注入到终端
- **逆向行迭代**：从日志末尾往前读，避免重读全文
- **健康检查**：验证 codex 进程和终端窗格是否还活着

### 4.4 Gemini 通信 (gemini_comm.py)

特殊挑战：
- Gemini 0.29 引入了 **dual hash strategy** 用于 session 发现（两种哈希算法兼容）
- 有时不输出完成标记 → 15 秒空闲检测兜底
- `--autostart` flag 用于离线 daemon 自动启动

### 4.5 OpenCode 通信 (opencode_comm.py)

- **存储格式**：JSON 文件 + SQLite 双源（灵活迁移）
- **路径**：`storage/session/<projectID>/ses_*.json`、`../opencode.db`
- **取消检测**：监控 `MessageAbortedError` + 日志 tail（双重机制）
- **session 过滤**：通过 `session_id_filter` 路由到特定会话

---

## 五、Token 效率机制（50-200 tokens/call 的实现原理）

### 5.1 为什么这么低？

传统 API 调用每次需要发送：system prompt + 完整对话历史 + 新消息 = 数千 tokens

CCB 的做法：
1. **AI CLI 维持 session**：claude/codex/gemini CLI 自己管理对话历史
2. **只发新消息**：`CCB_REQ_ID: ... \n CCB_BEGIN: \n {新指令} \n 回复末尾加 CCB_DONE:...`
3. **终端注入不经 API**：直接模拟键盘输入，相当于手动打字
4. **无 overhead**：没有 API envelope、headers、system prompt 重复

### 5.2 Context Transfer (memory/transfer.py)

当需要跨 session 传递上下文时：
- `ContextTransfer` 类实现 8000 token 预算管理
- 去重：`ConversationDeduper` 移除重复 message pairs
- 截断：`last_n` 参数只保留最近 N 轮
- Pipeline：parse → dedupe → collapse tool calls → build pairs → truncate → estimate tokens → format → send
- 持久化：`./.ccb/history/` 目录保存传输记录

### 5.3 Memory-First 架构 (docs/memory-first-agent-architecture.md)

文档定义了高级设计哲学：
- **Role A (Memory Keeper)**：跨 session 维护持久知识
- **Role B (Context Builder)**：组装短期 context 给执行者
- **Role C (Executor)**：无状态执行，接受预组装 context
- **Role T (Task Tracker)**：防止多窗口任务的 context 膨胀
- **三级存储**：L1 热(Redis) / L2 温(SQLite) / L3 冷(ChromaDB)
- **核心原则**："不让模型记忆，让模型查询"

---

## 六、Session 管理系统

### 6.1 Session Registry (pane_registry.py)

```
~/.ccb/run/ccb-session-{id}.json
```

- TTL：7 天
- JSON 原子写入
- 支持 legacy flat keys 和新版嵌套 `providers` 结构（自动迁移）
- 字段：terminal backend、pane ID、provider session paths、work dir、project ID

### 6.2 多层查找策略

1. Session ID 直接查找
2. Claude pane identifier 匹配
3. Project ID + provider 组合（强制目录隔离）

### 6.3 Pane 恢复机制 (gaskd_session.py, laskd_session.py)

```
ensure_pane():
  1. backend.is_alive(pane_id) → 存活则直接用
  2. 通过 title marker 搜索 pane
  3. tmux respawn_pane() 重生 pane
  4. 重生前保存 crash log
```

- 支持 tmux 自动 respawn
- Pane title marker 作为 fallback 标识
- Session 切换时触发 `maybe_auto_transfer()` 上下文迁移

---

## 七、WezTerm/tmux 集成层 (terminal.py)

### 7.1 Backend 抽象

```python
class TerminalBackend:
    def send_keys(pane_id, text)
    def capture_pane(pane_id)
    def is_alive(pane_id)
    def respawn_pane(pane_id, cmd)
    def new_pane(cmd)
    def get_pane_title(pane_id)
```

### 7.2 自动检测

- Linux/macOS/WSL：使用 tmux
- Windows：使用 WezTerm + PowerShell
- 通过环境变量覆盖

### 7.3 WezTerm 差异

- WezTerm 不支持 FIFO，改用直接文字注入
- PowerShell wrapper 处理 Windows 命令行长度限制（通过 stdin piping）
- `.cmd` / `.bat` 后缀的 wrapper 文件会被过滤（completion hook 需要 Python 直接执行）

---

## 八、跨 AI 任务委派机制

### 8.1 Claude 作为 Orchestrator

`bin/ask` 脚本向所有 provider 提供统一接口：

```bash
ask codex "实现 X 功能"      # Claude 指挥 Codex
ask gemini "审查这段代码"    # Claude 指挥 Gemini
ask opencode "..."           # Claude 指挥 OpenCode
```

Claude 的 SKILL.md 中定义了如何使用 `ask` 命令委派任务给其他 AI。

### 8.2 Codex → OpenCode 委派

通过 `codex_dual_bridge.py` 实现：
- DualBridge 读取来自 Claude 的 FIFO 输入（JSON payload）
- 转发命令到 Codex 终端
- 本质是单向命令注入桥，不是真正的 peer-to-peer

### 8.3 异步防循环机制 (format_guardrails.py)

关键设计：Claude 提交 `ask` 之后**禁止 polling**

```
claude-md-ccb.md 中的强制规则：
"END YOUR TURN NOW. Reply ONLY '[Provider] processing...', then stop."
```

- 通过 CLAUDE.md skill 规则在 prompt 层面硬约束
- 防止 Claude 在 async 提交后继续 polling → 死锁/循环
- v5.2.5 专门修复了这个问题

### 8.4 Completion Hook (completion_hook.py)

异步任务完成通知系统：
- 在后台线程中执行（不阻塞 daemon）
- 通过 `ccb-completion-hook` 脚本通知调用方
- 支持 email 集成（SMTP，3次重试，最大 8s backoff）
- 超时：60 秒

---

## 九、目录结构关键文件索引

```
claude_code_bridge/
├── bin/
│   ├── ask              - 统一任务分派入口（所有 provider）
│   ├── askd             - daemon 管理器
│   ├── cask/gask/oask   - 各 provider 专用客户端
│   ├── cpend/gpend      - 查询 pending 响应
│   ├── cping/gping      - 连通性检测
│   ├── ccb-completion-hook - 异步完成通知
│   └── ctx-transfer     - 手动跨 AI context 迁移
├── lib/
│   ├── ccb_protocol.py    - 核心协议（REQ_ID, BEGIN, DONE 标记）
│   ├── askd_server.py     - TCP daemon server
│   ├── askd_client.py     - daemon client + RPC
│   ├── askd_rpc.py        - JSON-RPC over TCP
│   ├── askd_runtime.py    - daemon 路径/日志工具
│   ├── worker_pool.py     - per-session worker pool
│   ├── providers.py       - 5 个 provider 注册表
│   ├── terminal.py        - tmux/WezTerm 抽象层
│   ├── pane_registry.py   - session 注册表（JSON 持久化）
│   ├── completion_hook.py - 异步完成回调
│   ├── claude_comm.py     - Claude 通信层
│   ├── codex_comm.py      - Codex 通信层
│   ├── gemini_comm.py     - Gemini 通信层
│   ├── opencode_comm.py   - OpenCode 通信层
│   ├── droid_comm.py      - Droid 通信层
│   ├── codex_dual_bridge.py - Claude→Codex 命令桥
│   ├── format_guardrails.py - 代码格式守卫
│   ├── ctx_transfer_utils.py - context 迁移工具
│   ├── laskd_session.py   - Claude session 管理
│   ├── gaskd_session.py   - Gemini session 管理
│   ├── oaskd_session.py   - OpenCode session 管理
│   └── memory/
│       ├── transfer.py    - context transfer（8K token 预算）
│       ├── deduper.py     - 对话去重
│       ├── formatter.py   - context 格式化
│       └── session_parser.py - session 日志解析
└── docs/
    └── memory-first-agent-architecture.md - 高级架构设计文档
```

---

## 十、与 Elvis Codex + ClaudeCode 项目的关联性

### 10.1 可直接借鉴的设计

| CCB 技术 | Elvis 可复用方式 |
|----------|----------------|
| `ccb_protocol.py` REQ_ID + CCB_DONE 标记 | 实现 Claude subagent 完成检测 |
| `worker_pool.py` per-session 串行化 | 防止同一 session 并发污染 |
| `askd_server.py` TCP daemon + idle timeout | 参考 daemon 生命周期管理 |
| Session registry JSON 结构 | 多 AI session 状态持久化 |
| completion_hook.py 异步通知模式 | 异步任务完成回调设计 |
| async guardrail（禁止 polling）模式 | 防止 orchestrator AI 死循环 |

### 10.2 不适合直接复用的部分

| CCB 技术 | 原因 |
|----------|------|
| 整体 tmux/WezTerm 注入架构 | Elvis 走 nanobot SDK，不是终端注入 |
| 完整 daemon 套件 | 依赖太深，不适合嵌入 nanobot |
| Memory-First 三层存储 | 过于复杂，超出 MVP 范围 |

### 10.3 关键启发

1. **Token 效率来自 session 持久化**：让 AI 维持自己的 session，每次只发新指令
2. **完成检测是核心挑战**：每个 AI 的完成信号不同（CCB 花了大量版本迭代解决此问题）
3. **Async guardrail 是必须的**：不在 prompt 层面约束 orchestrator，必然出现循环
4. **跨平台通信多态**：同一接口在 tmux/WezTerm/FIFO 下有不同实现

---

## 十一、总结

CCB 是目前最成熟的开源多 AI 协作终端方案（v5.2.6，活跃维护）。其核心创新：

1. **不走 API**，通过终端注入+日志读取实现双向通信
2. **Token 效率来源**是让各 AI CLI 自维护 session，不重发历史
3. **CCB_DONE 标记协议**是统一完成检测的关键
4. **Per-session worker pool** 防止并发污染
5. **Async guardrail** 在 CLAUDE.md/prompt 层面防止 orchestrator 循环

对 Elvis 项目最有价值的是：session 管理模式、完成检测协议设计、以及 async guardrail 防循环机制。

---

## 参考资料

- [bfly123/claude_code_bridge GitHub](https://github.com/bfly123/claude_code_bridge)
- [ccb_protocol.py](https://raw.githubusercontent.com/bfly123/claude_code_bridge/main/lib/ccb_protocol.py)
- [askd_server.py](https://raw.githubusercontent.com/bfly123/claude_code_bridge/main/lib/askd_server.py)
- [worker_pool.py](https://raw.githubusercontent.com/bfly123/claude_code_bridge/main/lib/worker_pool.py)
- [memory/transfer.py](https://raw.githubusercontent.com/bfly123/claude_code_bridge/main/lib/memory/transfer.py)
- [docs/memory-first-agent-architecture.md](https://raw.githubusercontent.com/bfly123/claude_code_bridge/main/docs/memory-first-agent-architecture.md)
- [completion_hook.py](https://raw.githubusercontent.com/bfly123/claude_code_bridge/main/lib/completion_hook.py)
- [providers.py](https://raw.githubusercontent.com/bfly123/claude_code_bridge/main/lib/providers.py)
