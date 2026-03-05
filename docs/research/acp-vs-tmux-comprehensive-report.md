# ACP (Agent Client Protocol) 深度调研报告

**项目**: tmux-codingagent-driver (tcd)
**日期**: 2026-03-05
**作者**: Michael (AI-assisted research)
**状态**: 完成

---

## 摘要

本报告对 Agent Client Protocol (ACP) 进行全面技术调研，涵盖协议原理、生态现状、已知问题，并与本项目（tcd）的 tmux 方案进行系统对比。核心结论：**ACP 在协议设计上代表了 AI coding agent 通信的未来方向，但当前实现（特别是 acpx）仍处于 alpha 阶段，存在多个严重已知问题；tmux 方案在进程稳定性、通用性和可调试性上具有明确优势，是当下更实用的选择。**

---

## 目录

1. [ACP 协议概述](#一acp-协议概述)
2. [ACP 技术原理详解](#二acp-技术原理详解)
3. [OpenClaw 与 acpx 架构](#三openclaw-与-acpx-架构)
4. [ACP 生态：支持的工具与平台](#四acp-生态支持的工具与平台)
5. [ACP 进程生命周期与已知问题](#五acp-进程生命周期与已知问题)
6. [Claude Code 与 ACP 的关系](#六claude-code-与-acp-的关系)
7. [OpenClaw ACP 配置与部署](#七openclaw-acp-配置与部署)
8. [ACP vs tmux 方案：系统对比](#八acp-vs-tmux-方案系统对比)
9. [用户体验维度分析](#九用户体验维度分析)
10. [tcd 项目的演进建议](#十tcd-项目的演进建议)
11. [参考资料](#十一参考资料)

---

## 一、ACP 协议概述

### 1.1 什么是 ACP

Agent Client Protocol (ACP) 是一个基于 **JSON-RPC 2.0** 的开放标准协议，用于统一代码编辑器/IDE 与 AI coding agent 之间的结构化双向通信。它由 **Zed Industries** 于 2025 年 8 月发布（Apache License 2.0），定位为 AI agent 领域的 **LSP（Language Server Protocol）**。

### 1.2 诞生背景

Zed 官方在 PromptLayer 博客中的原话精准描述了 ACP 的诞生动因：

> "We were already running Gemini CLI inside our embedded terminal... but we needed a more structured way of communicating than ANSI escape codes."
>
> ——"我们已经在嵌入式终端中运行 Gemini CLI 了，但我们需要一种比 ANSI 转义码更结构化的通信方式。"

在 ACP 之前，IDE 与 AI agent 的通信主要依赖两种方式：

| 方式 | 问题 |
|------|------|
| **终端嵌入（PTY/tmux）** | ANSI 转义码解析脆弱，状态感知困难，输出不结构化 |
| **API 直连** | 每次调用需重发完整上下文，token 浪费严重，不能利用 CLI 的本地工具能力 |

ACP 的目标是提供第三种方式：**结构化的进程间通信协议**，让 IDE 和 agent 像 LSP 中的编辑器和语言服务器一样协作。

### 1.3 核心设计理念

| 理念 | 说明 |
|------|------|
| **语义级通信** | 用 JSON 消息传递意图，而非原始字符流 |
| **双向请求** | Agent 可以主动向客户端请求文件操作、终端创建等 |
| **能力协商** | 连接时双方声明各自能力，协议自适应 |
| **会话持久化** | 会话跨进程存活，支持恢复和分叉 |
| **权限门控** | 客户端可以拦截和审批 agent 的操作请求 |

---

## 二、ACP 技术原理详解

### 2.1 传输层

ACP 支持两种传输方式：

| 方式 | 适用场景 | 技术细节 |
|------|---------|---------|
| **stdio pipe** | 本地 Agent | 编辑器 spawn agent 子进程，通过 stdin/stdout 管道通信 |
| **HTTP/WebSocket** | 远程 Agent | 通过网络连接远程 agent 服务 |

消息编码采用 **NDJSON（Newline-Delimited JSON）**——每条消息占一行，以换行符分隔。

### 2.2 协议基础：JSON-RPC 2.0

所有 ACP 消息遵循 JSON-RPC 2.0 规范，分三种类型：

**Request（请求）**——需要对方响应：
```json
{
  "jsonrpc": "2.0",
  "id": "req-001",
  "method": "session/prompt",
  "params": {
    "sessionId": "abc-123",
    "content": [
      { "type": "text", "text": "Implement user registration" }
    ]
  }
}
```

**Response（响应）**——回复 Request：
```json
{
  "jsonrpc": "2.0",
  "id": "req-001",
  "result": {
    "content": [
      { "type": "text", "text": "I'll implement..." }
    ]
  }
}
```

**Notification（通知）**——单向消息，无需响应（用于流式更新）：
```json
{
  "jsonrpc": "2.0",
  "method": "session/update",
  "params": {
    "sessionId": "abc-123",
    "type": "agent_message_chunk",
    "content": "..."
  }
}
```

### 2.3 连接生命周期（三阶段）

```
┌──────────────────────────────────────────────────────────────┐
│                    ACP 连接生命周期                            │
│                                                              │
│  阶段 1: 初始化 (Initialize)                                 │
│  ┌─────────┐                          ┌─────────┐           │
│  │ Client  │ ── initialize ────────→  │  Agent  │           │
│  │         │ ←─ InitializeResult ───  │         │           │
│  └─────────┘    (能力协商+版本)        └─────────┘           │
│                                                              │
│  阶段 2: 认证 (可选)                                         │
│  ┌─────────┐                          ┌─────────┐           │
│  │ Client  │ ── authenticate ──────→  │  Agent  │           │
│  │         │ ←─ AuthResult ─────────  │         │           │
│  └─────────┘                          └─────────┘           │
│                                                              │
│  阶段 3: 就绪 (Ready)                                        │
│  ┌─────────┐                          ┌─────────┐           │
│  │ Client  │ ←── ready ────────────── │  Agent  │           │
│  │         │     可以创建会话了         │         │           │
│  └─────────┘                          └─────────┘           │
└──────────────────────────────────────────────────────────────┘
```

**初始化阶段的能力声明示例：**

客户端声明自己支持的能力：
```json
{
  "capabilities": {
    "filesystem": { "read": true, "write": true },
    "terminal": { "create": true },
    "prompts": { "sections": true },
    "resources": { "mcp": true }
  }
}
```

Agent 回应自身能力：
```json
{
  "capabilities": {
    "streaming": true,
    "tools": true,
    "sessions": { "persistent": true, "fork": true }
  }
}
```

### 2.4 会话生命周期

```
Client                              Agent
  │                                   │
  │─── session/new ─────────────────→ │  创建会话
  │←── response { sessionId } ──────  │  返回 sessionId
  │                                   │
  │─── session/prompt ──────────────→ │  发送用户输入
  │←── session/update (thought) ────  │  推理过程（流式）
  │←── session/update (tool_call) ──  │  工具调用通知
  │                                   │
  │←── fs/read_text_file ───────────  │  Agent 请求读文件（反向请求!）
  │─── response { content } ────────→ │  客户端返回文件内容
  │                                   │
  │←── session/update (chunk) ──────  │  响应内容（流式分块）
  │←── session/update (chunk) ──────  │  继续流式
  │←── PromptResponse (final) ──────  │  最终完成
  │                                   │
  │─── session/prompt ──────────────→ │  多轮：发送后续指令
  │    ...                            │
  │                                   │
  │─── session/cancel (notification)→ │  取消当前处理
  │                                   │
```

### 2.5 核心 JSON-RPC 方法完整列表

#### 客户端 → Agent（请求类）

| 方法 | 说明 | 参数 |
|------|------|------|
| `initialize` | 初始化连接，能力协商 | capabilities, clientInfo, protocolVersion |
| `authenticate` | 认证（可选） | credentials |
| `session/new` | 创建新会话 | label?, directory? |
| `session/load` | 恢复已有会话 | sessionId |
| `session/prompt` | 发送用户输入 | sessionId, content[], _meta? |
| `session/set_mode` | 设置会话模式 | sessionId, mode |
| `session/set_config_option` | 设置配置项 | sessionId, key, value |

#### 客户端 → Agent（通知类）

| 方法 | 说明 |
|------|------|
| `session/cancel` | 取消当前处理 |
| `initialized` | 确认初始化完成 |

#### Agent → 客户端（流式 Notification）

| 更新类型 | 说明 |
|----------|------|
| `agent_thought_chunk` | 推理过程输出（thinking/chain-of-thought） |
| `agent_message_chunk` | 响应内容分块（最终输出的流式片段） |
| `tool_call` | 工具调用声明（Agent 要执行什么工具） |
| `tool_call_update` | 工具调用进度和结果 |
| `plan` | 多步骤执行计划 |

#### Agent → 客户端（回调请求，需客户端响应）

这是 ACP 最独特的设计——**Agent 可以主动向客户端发起请求**：

| 方法 | 说明 | 权限控制 |
|------|------|---------|
| `fs/read_text_file` | 请求读取文件 | approve-reads 即可 |
| `fs/write_text_file` | 请求写入文件 | 需要 approve-all |
| `fs/list_directory` | 请求列出目录 | approve-reads 即可 |
| `terminal/create` | 请求创建终端 | 需要 approve-all |
| `terminal/output` | 接收终端输出 | - |
| `terminal/wait_for_exit` | 等待命令结束 | - |
| `terminal/kill` | 终止终端进程 | - |

### 2.6 内容块（Content Blocks）

`session/prompt` 支持富内容类型：

```json
{
  "content": [
    { "type": "text", "text": "Fix the login bug" },
    { "type": "image", "resource_link": { "uri": "file:///screenshot.png", "mimeType": "image/png" } },
    { "type": "resource", "uri": "mcp://server/resource" }
  ]
}
```

支持的类型：
- **text**：纯文本
- **image**：图像附件（含 MIME 类型）
- **audio**：音频内容
- **resources**：MCP resource 引用

### 2.7 会话管理高级特性

| 特性 | 方法/参数 | 说明 |
|------|----------|------|
| **持久化** | `session/load` + sessionId | 会话跨进程存活，可恢复 |
| **命名** | label 参数 | 支持人类可读名称（如 "backend"、"frontend"） |
| **重置** | `_meta.resetSession: true` | 清空对话历史但保留 session ID |
| **分叉** | fork 能力 | 从已有会话派生新会话（保留部分上下文） |
| **删除** | session/delete | 显式删除会话及其历史 |
| **目录路由** | directory 参数 | 按工作目录自动匹配/创建会话 |

### 2.8 与 LSP 的类比

| 维度 | LSP | ACP |
|------|-----|-----|
| 目的 | 统一编辑器与语言服务器通信 | 统一编辑器与 AI agent 通信 |
| 传输 | stdio / socket | stdio / HTTP / WebSocket |
| 消息格式 | JSON-RPC 2.0 | JSON-RPC 2.0 |
| 双向性 | 服务器可发诊断/补全 | Agent 可请求文件/终端 |
| 能力协商 | 有 | 有 |
| 会话状态 | 无（无状态） | 有（持久化会话） |

---

## 三、OpenClaw 与 acpx 架构

### 3.1 OpenClaw ACP Bridge 架构

OpenClaw 在标准 ACP 之上增加了一层 **WebSocket Gateway 中间层**：

```
┌────────────┐    ACP (stdio)    ┌──────────────────┐    WebSocket    ┌──────────────────┐
│  IDE/Editor │ ←─────────────→  │ openclaw-acp-    │ ←────────────→ │ OpenClaw Gateway │
│  (Zed 等)   │    NDJSON        │ bridge           │                │ (:18789)         │
└────────────┘                   └──────────────────┘                └──────────────────┘
```

**消息翻译映射：**

| ACP 消息 | OpenClaw Gateway 消息 |
|----------|----------------------|
| `initialize` | 连接并注册 |
| `newSession` | 创建 `acp:<uuid>` 会话 |
| `loadSession` | 恢复 Gateway 会话 |
| `prompt` | → `chat.send` |
| `cancel` | → `chat.abort` |
| Gateway 流式事件 | → ACP `message` / `tool_call` updates |

**会话 Key 规则：**
- 默认：`acp:<uuid>`（隔离会话）
- 覆盖：`--session agent:main:main` 可指向已有 Gateway 会话
- per-request：`_meta` 对象中含 `sessionKey`、`sessionLabel`、`resetSession`、`requireExisting`

### 3.2 acpx 架构

acpx 是 ACP 的 **无头 CLI 客户端（headless CLI client）**，专为 agent-to-agent 自动化设计：

```
┌──────────────────────────────────────────────────────┐
│                      acpx CLI                         │
│                                                       │
│  ┌─────────────────────────────────────────────────┐  │
│  │  Session Manager                                 │  │
│  │  ├── State: ~/.acpx/sessions/*.json             │  │
│  │  ├── Directory-walk routing (git root match)    │  │
│  │  ├── TTL-based queue ownership (默认 300s)      │  │
│  │  └── IPC coordination (多实例协调)              │  │
│  └─────────────────────────────────────────────────┘  │
│                                                       │
│  ┌─────────────────────────────────────────────────┐  │
│  │  Output Formatter                                │  │
│  │  ├── text       (人类可读)                       │  │
│  │  ├── json       (自动化，含元数据)               │  │
│  │  ├── json-strict (纯 JSON，无 stderr)           │  │
│  │  └── quiet      (仅最终文本)                     │  │
│  └─────────────────────────────────────────────────┘  │
└──────────────────────┬───────────────────────────────┘
                       │ spawn as child process (stdio pipe)
                       ▼
         ┌──────────────────────────────┐
         │       ACP Adapter Layer       │
         ├──────────────────────────────┤
         │ claude-agent-acp → Claude Code│  (Zed 官方, npx)
         │ codex-acp → Codex CLI        │  (Zed 官方, npx)
         │ gemini (native) → Gemini CLI │  (原生 ACP)
         │ opencode (native) → OpenCode │  (原生 ACP)
         │ pi-acp → Pi Agent            │  (社区, npx)
         │ kimi (native) → Kimi         │  (原生 ACP)
         └──────────────────────────────┘
```

**acpx 进程模型——队列所有者模型：**

```
acpx prompt "fix bug"
  │
  ├── 查找现有 session（按 git root 匹配）
  │   ├── 找到且进程存活 → 直接发送 prompt
  │   ├── 找到但进程已死 → 重新 spawn + session/load 恢复
  │   └── 未找到 → 创建新 session
  │
  └── Queue Owner 持有进程所有权
      ├── 默认 TTL: 300s（空闲后释放）
      ├── --ttl 0: 永久保活
      └── 多 acpx 实例通过 IPC 协调
```

**acpx CLI 命令：**

| 命令 | 功能 |
|------|------|
| `acpx <agent> prompt "..."` | 发送 prompt 到已有/新建会话 |
| `acpx <agent> exec "..."` | 一次性临时会话（用完即弃） |
| `acpx <agent> sessions list` | 列出所有会话 |
| `acpx <agent> sessions new` | 显式创建新会话 |
| `acpx <agent> sessions close` | 关闭会话 |
| `acpx <agent> -s <name> "..."` | 指定命名会话（并行工作流） |

**适配器内部架构（以 codex-acp 为例）：**

- **技术栈**：Rust + Tokio current-thread 异步运行时
- **核心模块**：
  - `agent/core.rs`：处理 ACP 请求（initialize, session/new, session/prompt 等）
  - `agent/events.rs`：将 Codex 事件转换为 ACP update 通知
  - `agent/commands.rs`：处理斜杠命令
  - `SessionManager`：集中管理会话状态、客户端通知、上下文
- **文件操作**：内置 MCP 服务器（acp_fs）处理，避免 shell 调用

**NDJSON 信封格式（稳定 schema）：**

```json
{
  "sessionId": "abc-123",
  "requestId": "req-001",
  "seq": 5,
  "type": "agent_message_chunk",
  "content": "I've implemented the user registration..."
}
```

---

## 四、ACP 生态：支持的工具与平台

### 4.1 支持 ACP 的 Agent（28 个）

#### 原生支持（25 个）——Agent 自身实现了 ACP 协议

| Agent 名称 | 开发方 | 特点 | 成熟度 |
|-----------|--------|------|--------|
| **Gemini CLI** | Google | 深度代码库理解，多模态 | 高 |
| **GitHub Copilot** | GitHub/Microsoft | AI 编程助手 | 公开预览版 |
| **Junie** | JetBrains | JetBrains 官方 Agent | 高 |
| **OpenClaw** | OpenClaw | 自托管，可作 Client 也可作 Agent | 中 |
| **OpenCode** | 开源社区 | 完全开源 | 中 |
| **Cline** | 开源社区 | 支持多编辑器（JetBrains/Zed/Neovim/Emacs） | 中 |
| **Goose** | Block/Square | 开源 Agent | 中 |
| **OpenHands** | 开源社区 | - | 中 |
| **Kimi CLI** | Moonshot AI | 多语言支持 | 中 |
| **Kiro CLI** | Amazon | - | 中 |
| **Qwen Code** | 阿里 | 多语言支持强 | 中 |
| Augment Code (Auggie) | Augment | 大规模重构 | 中 |
| AutoDev | 开源 | 自动化开发 | 早期 |
| Blackbox AI | Blackbox | 代码搜索生成 | 中 |
| Docker cagent | Docker | 容器化 Agent | 早期 |
| Factory Droid | Factory | 自动化工作流 | 早期 |
| Mistral Vibe | Mistral AI | 轻量快速 | 早期 |
| AgentPool, Code Assistant, fast-agent, fount, Minion Code, Qoder CLI, Stakpak, VT Code | 各方 | 其余 8 个 | 早期 |

#### 通过适配器支持（3 个）——需要额外 ACP wrapper

| Agent 名称 | 适配器 | 维护方 | 原因 |
|-----------|--------|--------|------|
| **Claude Code** | `@zed-industries/claude-agent-acp` | Zed 官方 | TUI 程序（Ink/React），无法直接说 JSON-RPC |
| **Codex CLI** | `@zed-industries/codex-acp` | Zed 官方 | 同上，需要适配器翻译 |
| **Pi** | `pi-acp` | 社区 | - |

**为什么 Claude Code 和 Codex 需要适配器？** 因为它们是 TUI（Text User Interface）程序，使用 Ink（基于 React 的终端 UI 框架）渲染界面，需要 raw mode TTY。它们原生通过终端字符流通信，不直接支持 JSON-RPC 消息交换。适配器的作用是在 ACP JSON-RPC 协议和底层 agent 的内部 API 之间做翻译。

### 4.2 支持 ACP 的 Client 端（编辑器/IDE）

| 编辑器 | 支持方式 | 状态 |
|--------|---------|------|
| **Zed** | 原生支持（ACP 发起者） | 生产就绪 |
| **JetBrains IDEs** | AI Assistant 插件（2025.3.2+） | 生产就绪 |
| **VS Code** | ACP Client 扩展 | 预览 |
| **Neovim** | 社区插件 | 早期 |
| **Emacs** | 社区插件 | 早期 |
| **Obsidian** | obsidian-agent-client 插件 | 早期 |
| **Marimo** | 数据科学 notebook | 早期 |
| **acpx** | 无头 CLI 客户端 | Alpha |

### 4.3 ACP Agent Registry

JetBrains 于 2026 年 1 月上线了 **ACP Agent Registry**，作为 Agent 发现和分发的中心化平台（类似 npm registry 对 package 的作用），标志着 ACP 生态进入标准化阶段。

---

## 五、ACP 进程生命周期与已知问题

### 5.1 进程模型

```
acpx (或 OpenClaw)
  │ spawn child process
  │ stdio: ['pipe', 'pipe', 'inherit']
  ▼
ACP Adapter (如 codex-acp)
  │ manage
  ▼
Actual Agent Process (如 Codex CLI)
```

**关键特征：**
- acpx 通过 stdio pipe 与适配器通信
- 适配器作为 agent 的管理进程
- 形成两层进程树

### 5.2 已知严重问题

#### 问题 1：PTY 崩溃（Issue #28786）

**状态**：已于 2026-03-04 修复（PR #34020）

**现象**：acpx 以 pipe 模式 spawn Claude Code/Codex 子进程，但这两个工具都需要 raw mode TTY（基于 Ink/React 的终端 UI），导致启动后立即崩溃：

```
Raw mode is not supported on the current process.stdin
```

**影响**：`sessions_spawn runtime="acp"` 持续失败，claude 和 codex 两个主力 Agent 都无法使用。

**修复**：session-bootstrap 可靠性改进，包含回退硬化和显式失败处理。

#### 问题 2：静默权限失败（Issue #29195）

**状态**：未完全解决

**现象**：默认的 `permissionMode: approve-reads` 在非交互模式下，导致 Codex 的写文件/执行命令请求被静默拒绝。错误仅记录在内部日志，不通知调用方。`sessions_spawn` 返回 "accepted" 后，进程悄然进入僵尸状态。

**后果**：
- 父 Agent 无法监控子 ACP 会话进度（forbidden 错误）
- Codex 进程在"完成"后持续运行 80+ 分钟（0% CPU）不退出
- 调用方以为任务成功，实际什么都没做

**缓解配置**：
```bash
openclaw config set plugins.entries.acpx.config.permissionMode approve-all
openclaw config set plugins.entries.acpx.config.nonInteractivePermissions fail
```

#### 问题 3：孤儿进程与僵尸进程

**现象**：当 acpx/OpenClaw 主进程异常退出时：
- 子 agent 进程变成孤儿进程（orphan process）
- 由于 stdio pipe 断裂，agent 收不到新指令也发不出响应
- 但 agent 进程本身不一定退出——缺乏超时机制，可能无限期占用资源
- 已有记录显示 Codex 进程运行 80+ 分钟不退出

**acpx 的崩溃恢复机制**：
1. 下次调用时检测到已保存的 session PID 已死亡
2. 自动重新 spawn Agent，尝试 `session/load` 恢复会话
3. 失败时透明回退到 `session/new`（丢失之前的上下文）

### 5.3 与 tmux 方案的进程稳定性对比

| 场景 | acpx/ACP | tmux (tcd) |
|------|----------|------------|
| **主控进程崩溃** | Agent 变孤儿，通信断裂，状态不确定 | **tmux session 完全不受影响**，tmux server 独立运行 |
| **Agent 进程崩溃** | acpx 下次调用时尝试恢复（可能丢上下文） | tcd 通过 `has-session` 检测退出，从 `.log` 收集最后输出 |
| **长时间运行** | 可能出现僵尸进程（无超时机制） | tmux session 稳定运行，可无限期 |
| **多 Agent 并行** | 支持命名会话，受 `maxConcurrentSessions` 限制 | 每个 tmux session 完全独立，无限制 |
| **可见性** | 无 PTY，输出仅通过 ACP 协议传递，调试困难 | **`tmux attach` 直接看到 Agent 实时 TUI 输出** |
| **权限控制** | 非交互模式下权限提示导致静默失败 | 可通过 `send-keys` 模拟交互确认 |

---

## 六、Claude Code 与 ACP 的关系

### 6.1 角色澄清

**ACP 协议定义了两种角色：**

```
┌─────────────────┐                    ┌──────────────────┐
│   ACP Client     │  ←── ACP ────→    │   ACP Agent      │
│   (发起方)       │                    │   (响应方)        │
│                  │                    │                   │
│  - Zed           │                    │  - Claude Code    │
│  - JetBrains     │                    │  - Codex CLI      │
│  - VS Code       │                    │  - Gemini CLI     │
│  - acpx          │                    │  - OpenClaw       │
│  - OpenClaw*     │                    │  - Cline          │
└─────────────────┘                    └──────────────────┘

* OpenClaw 可同时扮演两种角色
```

**关键结论：Claude Code 在 ACP 中是 Agent（服务端），不是 Client（客户端）。**

这意味着：
1. Claude Code 可以**被** IDE/acpx 通过 ACP 调用
2. Claude Code **不能**通过 ACP 主动调用其他 Agent（如 Codex）
3. ACP 协议设计中，客户端是编辑器/IDE，不是 AI Agent

### 6.2 Claude Code 调用 Codex CLI 的可行方案

| 方案 | 路径 | 结构化输出 | 会话管理 | 推荐度 |
|------|------|----------|---------|--------|
| **A. tcd（本项目）** | Claude Code → bash → `tcd start -p codex` → tmux → Codex | 有（JSON） | 有（多轮） | 最高 |
| **B. MCP 工具** | Claude Code → MCP → `codex-subagents-mcp` → Codex 子进程 | 有 | 无（单次） | 高 |
| **C. 直接 Shell** | Claude Code → bash → `codex -q "prompt"` | 无 | 无 | 中 |
| **D. OpenClaw 编排** | OpenClaw 作为 ACP Client 同时调度 Claude + Codex | 有 | 有 | 中 |
| **E. ACP 直接调用** | Claude Code → ACP → Codex | **不可行** | - | 不可行 |

**方案 A（tcd）的核心优势**：
- 进程隔离好（tmux session 独立）
- 支持多轮对话（`tcd send` 追加指令）
- 可调试（`tcd attach` 看实时输出）
- 结果收集完整（ANSI 清理 + 多策略 fallback）
- 不依赖 agent 实现任何协议

**方案 B（MCP）的部署步骤**：

```bash
# 1. 安装 MCP 服务器
npm install -g codex-subagents-mcp

# 2. 配置 Claude Code 的 MCP
# ~/.claude/mcp_settings.json
{
  "mcpServers": {
    "codex": {
      "command": "codex-subagents-mcp",
      "args": []
    }
  }
}

# 3. Claude Code 即可通过 MCP 工具调用 Codex
```

---

## 七、OpenClaw ACP 配置与部署

OpenClaw 支持两种 ACP 集成模式：

### 模式 1：OpenClaw 作为 ACP Client（通过 acpx 调用外部 Agent）

**适用场景**：让 OpenClaw 调度 Codex、Claude Code、Gemini CLI 等 Agent 执行编码任务。

```
OpenClaw → acpx 插件 → ACP 适配器 → Agent 进程
```

**部署步骤：**

```bash
# Step 1: 安装 acpx 插件
openclaw plugins install acpx
openclaw config set plugins.entries.acpx.enabled true

# Step 2: 配置权限（关键!否则写操作静默失败）
openclaw config set plugins.entries.acpx.config.permissionMode approve-all
openclaw config set plugins.entries.acpx.config.nonInteractivePermissions fail

# Step 3: 健康检查
/acp doctor
```

**openclaw.json 配置：**

```json5
{
  acp: {
    enabled: true,
    dispatch: { enabled: true },
    backend: "acpx",
    defaultAgent: "codex",
    allowedAgents: ["pi", "claude", "codex", "opencode", "gemini", "kimi"],
    maxConcurrentSessions: 8,
    stream: {
      coalesceIdleMs: 300,     // 流式输出合并间隔
      maxChunkChars: 1200,     // 单块最大字符数
    },
    runtime: {
      ttlMinutes: 120,         // 会话空闲超时
    },
  },
}
```

### 模式 2：OpenClaw 作为 ACP Agent（供 IDE 调用）

**适用场景**：让 Zed/JetBrains 等 IDE 通过 ACP 调用 OpenClaw。

```
IDE (Zed) → ACP stdio → openclaw acp → WebSocket → OpenClaw Gateway
```

**部署步骤：**

```bash
# Step 1: 配置 Gateway 连接
openclaw config set gateway.remote.url wss://gateway-host:18789
openclaw config set gateway.remote.token-file ~/.openclaw-token  # 推荐用 token-file

# Step 2: 在 Zed settings.json 中注册
```

```json
{
  "agent_servers": [
    {
      "name": "openclaw",
      "command": "openclaw",
      "args": ["acp"]
    }
  ]
}
```

```bash
# Step 3: 可选 - 会话路由到特定 Agent
openclaw acp --session agent:main:main
openclaw acp --session agent:design:main --label "design-work"
```

---

## 八、ACP vs tmux 方案：系统对比

### 8.1 架构层面

| 维度 | ACP 协议 | tmux/PTY 方案 (tcd) |
|------|----------|-------------------|
| **通信层** | JSON-RPC 2.0 over stdio pipe | 二进制字符流（终端仿真） |
| **消息语义** | 结构化 JSON，含方法名、参数、元数据 | 原始字符 I/O，无语义分层 |
| **状态追踪** | 显式会话 ID + 协议级状态 | 终端状态隐式，需解析 ANSI 码推断 |
| **双向通信** | 原生支持（Agent 可主动请求文件/终端） | 单向 I/O 重定向（只能注入和读取） |
| **权限控制** | 显式能力声明 + 权限门控（approve-reads/all） | 命令直接执行，无拦截层 |
| **完成检测** | 协议内建 `PromptResponse` 消息 | 三策略 fallback（signal/marker/空闲检测） |
| **输出解析** | 结构化 JSON，零解析成本 | 需 ANSI 转义码清理，脆弱 |
| **并发管理** | 多会话原生支持，IPC 协调 | 每 Job 一个独立 tmux session |

### 8.2 运维层面

| 维度 | ACP/acpx | tmux (tcd) |
|------|----------|------------|
| **进程稳定性** | Alpha，有已知严重 bug | 久经考验，30+ 年历史 |
| **进程隔离** | 子进程模型，有孤儿进程风险 | tmux session 天然隔离，与控制方解耦 |
| **可调试性** | 无 PTY，只能看 JSON 日志 | `tcd attach` 直接看 Agent 实时 TUI |
| **崩溃恢复** | 有协议级恢复（但实现不成熟） | tmux session 不受控制方崩溃影响 |
| **依赖链** | Node.js + npx + 适配器 + ACP 协议栈 | tmux + Python（极简） |
| **通用性** | 仅支持实现了 ACP 的 Agent | 任意终端程序，零适配成本 |

### 8.3 功能层面

| 功能 | ACP | tcd (tmux) |
|------|-----|------------|
| **启动 Agent** | `acpx codex prompt "..."` | `tcd start -p codex -m "..."` |
| **多轮对话** | `session/prompt` 到同一 session | `tcd send <id> "..."` |
| **完成检测** | 协议内建（零延迟） | 三策略 fallback（1-20s 延迟） |
| **取消操作** | `session/cancel`（协议级） | `Ctrl+C` send-keys（不保证生效） |
| **获取输出** | JSON 流式分块 | `tcd output`（ANSI 清理后） |
| **并行任务** | 命名会话（`-s backend`） | 多 tmux session |
| **Agent 切换** | 同一接口切换 agent | 同一接口切换 provider |
| **会话恢复** | `session/load`（协议级） | tmux session 持久化 |
| **实时观察** | 无（JSON 日志） | `tcd attach` |

### 8.4 调用同一个 Agent 时的具体差异

以调用 Codex CLI 为例：

| 环节 | tcd (tmux) | ACP/acpx |
|------|-----------|----------|
| 启动 | `tmux new-session` + `script` 录制 + `codex` CLI | `acpx codex prompt "..."` → spawn `codex-acp` 适配器 |
| 发送 prompt | `tmux send-keys '文本' Enter`（字符注入） | `session/prompt` JSON-RPC 消息 |
| 接收输出 | `tmux capture-pane` + ANSI 清理 + marker 扫描 | 收取 `session/update` NDJSON 流 |
| 完成检测 | signal file → marker → 空闲检测（三层 fallback） | `PromptResponse` 消息明确标记 |
| 多轮对话 | `tmux send-keys` 再次注入 | `session/prompt` 同一 session |
| 结果解析 | 从 JSONL session file / capture-pane 提取 | 直接取 JSON content 字段 |

**一句话总结**：tmux 是在终端里"假装是人在打字和看屏幕"，ACP 是程序间的结构化对话。

---

## 九、用户体验维度分析

### 9.1 ACP 的用户体验优势

1. **输出质量高**：JSON 结构化输出，不需要 ANSI 清理，不会截断，不丢字符
2. **完成检测可靠**：协议级 `PromptResponse`，不需要 marker hack 和空闲猜测
3. **实时流式反馈**：思考过程、工具调用、响应分块都有独立事件，可做精细 UI 呈现
4. **取消操作可靠**：`session/cancel` 一发就停
5. **权限可控**：可拦截 agent 的文件写入和命令执行
6. **IDE 原生集成**：Zed、JetBrains、Neovim 都已支持，用户体验更流畅
7. **会话管理丰富**：命名、分叉、重置、跨进程恢复

### 9.2 ACP 的用户体验劣势

1. **Agent 覆盖有限**：只能驱动有 ACP 适配器的 Agent，无法驱动任意 CLI
2. **额外依赖**：需要 Node.js（npx 下载适配器），增加环境配置成本
3. **Alpha 阶段不稳定**：已知 PTY 崩溃、静默权限失败等严重问题
4. **调试困难**：无法像 tmux attach 那样直接"看到" agent 在干什么
5. **孤儿进程**：主控崩溃后 agent 可能变僵尸，用户需手动清理
6. **配置复杂**：权限配置不当会导致静默失败，排查困难

### 9.3 tmux (tcd) 的用户体验优势

1. **通用性强**：任何能在终端跑的程序都能被驱动，不需要目标程序做任何适配
2. **可调试性强**：`tcd attach` 直接看到 agent 的实时 TUI 输出，问题一目了然
3. **零额外依赖**：只需 tmux + Python
4. **进程稳定**：tmux 是 Unix 基石级工具，30+ 年历史
5. **低延迟启动**：不需要协议握手和能力协商
6. **进程隔离完美**：tmux session 独立于所有控制方进程

### 9.4 tmux (tcd) 的用户体验劣势

1. **ANSI 解析脆弱**：输出清理是永远的痛点，AI CLI 的 TUI 渲染千变万化
2. **完成检测有延迟**：最差情况需 15-20s 空闲检测，marker 协议不保证被遵守
3. **取消不可靠**：`send-keys Ctrl+C` 不保证 agent 正确响应
4. **无权限拦截**：agent 在 tmux 中可执行任何操作
5. **输出不结构化**：需要额外解析才能提取有用信息

---

## 十、tcd 项目的演进建议

### 10.1 短期策略（当下）

**继续坚持 tmux 方案。** 理由：
- tcd 已完成 Phase 1-4，119 tests 通过，是可用的工具
- ACP/acpx 仍有严重已知 bug（PTY 崩溃刚修复，静默权限失败未解决）
- Claude Code → Codex 这个核心场景，ACP 根本做不到（角色模型限制），tcd 天然支持
- tmux 的通用性保证了对任何新 CLI 工具的即时支持

### 10.2 中期策略（3-6 个月）

**观察 ACP 生态成熟度**，关注以下信号：
- acpx 从 alpha 进入 beta/stable
- 静默权限失败问题彻底解决
- 孤儿进程问题有正式的超时/清理机制
- Claude Code 或 Codex 原生支持 ACP（不需要适配器）

### 10.3 长期策略（6-12 个月）

**考虑混合架构**——保留 tmux 做进程隔离容器，对支持 ACP 的 Agent 用 ACP 替换通信层：

```
┌─────────────────────────────────────────────────────┐
│                    tcd v2（混合架构）                  │
│                                                       │
│  ┌─────────────┐  ┌─────────────┐  ┌─────────────┐  │
│  │ ACP Channel  │  │ PTY Channel  │  │ Hybrid      │  │
│  │ (结构化)     │  │ (通用)       │  │ Channel     │  │
│  └──────┬──────┘  └──────┬──────┘  └──────┬──────┘  │
│         │                │                │           │
│         ▼                ▼                ▼           │
│   支持 ACP 的 Agent  不支持 ACP 的     tmux 隔离 +    │
│   (Claude, Codex)   CLI 工具         ACP 通信        │
└─────────────────────────────────────────────────────┘
```

**混合方案的价值**：
- tmux 继续提供进程隔离和可调试性
- ACP 解决 ANSI 解析脆弱和完成检测不可靠的痛点
- 不支持 ACP 的 Agent 继续走 PTY 降级路径
- 渐进式迁移，风险可控

### 10.4 决策矩阵

| 条件 | 行动 |
|------|------|
| acpx 仍是 alpha + 有严重 bug | 维持 tmux 方案 |
| acpx 稳定 + Agent 覆盖广 | 评估混合架构 POC |
| Claude Code 原生支持 ACP | 优先为 Claude Provider 接入 ACP |
| 新 CLI 工具不支持 ACP | 保留 tmux PTY 通道 |

---

## 十一、参考资料

### 协议规范与官方文档
- [Agent Client Protocol 官网](https://agentclientprotocol.com/)
- [ACP 协议规范 GitHub](https://github.com/agentclientprotocol/agent-client-protocol)
- [ACP 协议概览 (hexdocs/acpex)](https://hexdocs.pm/acpex/protocol_overview.html)
- [Zed ACP 页面](https://zed.dev/acp)
- [JetBrains ACP 文档](https://www.jetbrains.com/help/ai-assistant/acp.html)

### OpenClaw 与 acpx
- [acpx GitHub 仓库](https://github.com/openclaw/acpx)
- [acpx AGENTS.md](https://github.com/openclaw/acpx/blob/main/AGENTS.md)
- [OpenClaw ACP Agents 文档](https://docs.openclaw.ai/tools/acp-agents)
- [OpenClaw docs.acp.md](https://github.com/openclaw/openclaw/blob/main/docs.acp.md)
- [OpenClaw ACP 2026 完整指南](https://dev.to/czmilo/2026-complete-guide-openclaw-acp-bridge-your-ide-to-ai-agents-3hl8)

### 适配器
- [zed-industries/claude-agent-acp](https://github.com/zed-industries/claude-agent-acp)
- [codex-acp 适配器](https://github.com/cola-io/codex-acp)
- [codex-subagents-mcp](https://github.com/leonardsellem/codex-subagents-mcp)

### 分析与评论
- [PromptLayer: ACP - The LSP for AI Coding Agents](https://blog.promptlayer.com/agent-client-protocol-the-lsp-for-ai-coding-agents/)
- [goose blog: Intro to ACP](https://block.github.io/goose/blog/2025/10/24/intro-to-agent-client-protocol-acp/)
- [JetBrains ACP Agent Registry 发布博客](https://blog.jetbrains.com/ai/2026/01/acp-agent-registry/)
- [AI SDK ACP Community Provider](https://ai-sdk.dev/providers/community-providers/acp)

### 已知问题
- [Issue #28786: PTY 崩溃问题](https://github.com/openclaw/openclaw/issues/28786)
- [Issue #29195: Codex 静默权限失败](https://github.com/openclaw/openclaw/issues/29195)

### 本项目相关
- [tcd PRD](../prd.md)
- [tcd 设计文档](../design.md)
- [对比报告: CCB vs codex-orchestrator](comparison-ccb-vs-codex-orchestrator.md)
