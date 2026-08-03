> ⚠️ **本文结论已被
> [2026-08-structured-output-vs-screen-scraping.md](2026-08-structured-output-vs-screen-scraping.md)
> 修正**：当年判断"tmux 更实用"是因为 ACP 仍在 alpha；现在的理由是 tmux 解决的是
> 进程托管、结构化输出解决的是协议，二者互补而非竞争。保留本文是因为它仍是本仓
> 唯一的 ACP 协议内部机制参考。

# ACP (Agent Client Protocol) Deep Research Report

**Project**: tmux-codingagent-driver (tcd)
**Date**: 2026-03-05
**Author**: Michael (AI-assisted research)
**Status**: Complete

---

## Summary

This report provides a comprehensive technical investigation of Agent Client Protocol (ACP), covering protocol principles, ecosystem status, known issues, and a systematic comparison with this project's (tcd) tmux approach. Core conclusion: **ACP represents the future direction of AI coding agent communication in protocol design, but the current implementation (particularly acpx) is still in alpha stage with several serious known issues; the tmux approach has clear advantages in process stability, generality, and debuggability, making it the more practical choice today.**

---

## Table of Contents

1. [ACP Protocol Overview](#i-acp-protocol-overview)
2. [ACP Technical Internals](#ii-acp-technical-internals)
3. [OpenClaw and acpx Architecture](#iii-openclaw-and-acpx-architecture)
4. [ACP Ecosystem: Supported Tools and Platforms](#iv-acp-ecosystem-supported-tools-and-platforms)
5. [ACP Process Lifecycle and Known Issues](#v-acp-process-lifecycle-and-known-issues)
6. [Claude Code and ACP Relationship](#vi-claude-code-and-acp-relationship)
7. [OpenClaw ACP Configuration and Deployment](#vii-openclaw-acp-configuration-and-deployment)
8. [ACP vs tmux Approach: System Comparison](#viii-acp-vs-tmux-approach-system-comparison)
9. [User Experience Dimension Analysis](#ix-user-experience-dimension-analysis)
10. [tcd Project Evolution Recommendations](#x-tcd-project-evolution-recommendations)
11. [References](#xi-references)

---

## I. ACP Protocol Overview

### 1.1 What Is ACP

Agent Client Protocol (ACP) is an open standard protocol based on **JSON-RPC 2.0** for structured bidirectional communication between code editors/IDEs and AI coding agents. It was published by **Zed Industries** in August 2025 (Apache License 2.0), positioned as the **LSP (Language Server Protocol)** for the AI agent domain.

### 1.2 Origin

Zed officially described ACP's motivation precisely in a PromptLayer blog post:

> "We were already running Gemini CLI inside our embedded terminal... but we needed a more structured way of communicating than ANSI escape codes."

Before ACP, IDE-to-AI-agent communication relied primarily on two approaches:

| Approach | Problems |
|----------|---------|
| **Terminal embedding (PTY/tmux)** | ANSI escape code parsing is fragile, state awareness is difficult, output is unstructured |
| **Direct API** | Full context must be resent every call, severe token waste; cannot leverage CLI's local tool capabilities |

ACP's goal is to provide a third approach: **a structured inter-process communication protocol**, letting IDEs and agents collaborate like editors and language servers do in LSP.

### 1.3 Core Design Philosophy

| Philosophy | Description |
|-----------|-------------|
| **Semantic communication** | Convey intent via JSON messages rather than raw character streams |
| **Bidirectional requests** | Agent can proactively request file operations, terminal creation, etc. from the client |
| **Capability negotiation** | Both sides declare their capabilities at connection time; protocol adapts |
| **Session persistence** | Sessions survive across processes, supporting resumption and forking |
| **Permission gating** | Client can intercept and approve agent operation requests |

---

## II. ACP Technical Internals

### 2.1 Transport Layer

ACP supports two transport modes:

| Mode | Use Case | Technical Details |
|------|----------|------------------|
| **stdio pipe** | Local Agent | Editor spawns agent as child process, communicates via stdin/stdout pipe |
| **HTTP/WebSocket** | Remote Agent | Connects to remote agent service over network |

Message encoding uses **NDJSON (Newline-Delimited JSON)** — each message occupies one line, separated by newlines.

### 2.2 Protocol Foundation: JSON-RPC 2.0

All ACP messages follow the JSON-RPC 2.0 specification in three types:

**Request** — requires a response from the other party:
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

**Response** — replies to a Request:
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

**Notification** — one-way message, no response needed (used for streaming updates):
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

### 2.3 Connection Lifecycle (Three Phases)

```
┌──────────────────────────────────────────────────────────────┐
│                    ACP Connection Lifecycle                    │
│                                                              │
│  Phase 1: Initialize                                         │
│  ┌─────────┐                          ┌─────────┐           │
│  │ Client  │ ── initialize ────────→  │  Agent  │           │
│  │         │ ←─ InitializeResult ───  │         │           │
│  └─────────┘    (capability negotiation + version)           │
│                                                              │
│  Phase 2: Authenticate (optional)                            │
│  ┌─────────┐                          ┌─────────┐           │
│  │ Client  │ ── authenticate ──────→  │  Agent  │           │
│  │         │ ←─ AuthResult ─────────  │         │           │
│  └─────────┘                          └─────────┘           │
│                                                              │
│  Phase 3: Ready                                              │
│  ┌─────────┐                          ┌─────────┐           │
│  │ Client  │ ←── ready ────────────── │  Agent  │           │
│  │         │     can now create sessions           │         │
│  └─────────┘                          └─────────┘           │
└──────────────────────────────────────────────────────────────┘
```

**Capability declaration example during initialization:**

Client declares supported capabilities:
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

Agent responds with its own capabilities:
```json
{
  "capabilities": {
    "streaming": true,
    "tools": true,
    "sessions": { "persistent": true, "fork": true }
  }
}
```

### 2.4 Session Lifecycle

```
Client                              Agent
  │                                   │
  │─── session/new ─────────────────→ │  create session
  │←── response { sessionId } ──────  │  return sessionId
  │                                   │
  │─── session/prompt ──────────────→ │  send user input
  │←── session/update (thought) ────  │  reasoning process (streaming)
  │←── session/update (tool_call) ──  │  tool call notification
  │                                   │
  │←── fs/read_text_file ───────────  │  Agent requests file read (reverse request!)
  │─── response { content } ────────→ │  client returns file content
  │                                   │
  │←── session/update (chunk) ──────  │  response content (streaming chunks)
  │←── session/update (chunk) ──────  │  continue streaming
  │←── PromptResponse (final) ──────  │  final completion
  │                                   │
  │─── session/prompt ──────────────→ │  multi-turn: send follow-up
  │    ...                            │
  │                                   │
  │─── session/cancel (notification)→ │  cancel current processing
  │                                   │
```

### 2.5 Complete JSON-RPC Method List

#### Client → Agent (Request type)

| Method | Description | Parameters |
|--------|-------------|-----------|
| `initialize` | Initialize connection, capability negotiation | capabilities, clientInfo, protocolVersion |
| `authenticate` | Authentication (optional) | credentials |
| `session/new` | Create new session | label?, directory? |
| `session/load` | Resume existing session | sessionId |
| `session/prompt` | Send user input | sessionId, content[], _meta? |
| `session/set_mode` | Set session mode | sessionId, mode |
| `session/set_config_option` | Set configuration option | sessionId, key, value |

#### Client → Agent (Notification type)

| Method | Description |
|--------|-------------|
| `session/cancel` | Cancel current processing |
| `initialized` | Confirm initialization complete |

#### Agent → Client (Streaming Notifications)

| Update Type | Description |
|-------------|-------------|
| `agent_thought_chunk` | Reasoning process output (thinking/chain-of-thought) |
| `agent_message_chunk` | Response content chunks (streaming fragments of final output) |
| `tool_call` | Tool call declaration (what tool the Agent intends to execute) |
| `tool_call_update` | Tool call progress and result |
| `plan` | Multi-step execution plan |

#### Agent → Client (Callback Requests, client must respond)

This is ACP's most distinctive design — **Agent can proactively send requests to the client**:

| Method | Description | Permission Control |
|--------|-------------|-------------------|
| `fs/read_text_file` | Request file read | approve-reads sufficient |
| `fs/write_text_file` | Request file write | requires approve-all |
| `fs/list_directory` | Request directory listing | approve-reads sufficient |
| `terminal/create` | Request terminal creation | requires approve-all |
| `terminal/output` | Receive terminal output | — |
| `terminal/wait_for_exit` | Wait for command completion | — |
| `terminal/kill` | Terminate terminal process | — |

### 2.6 Content Blocks

`session/prompt` supports rich content types:

```json
{
  "content": [
    { "type": "text", "text": "Fix the login bug" },
    { "type": "image", "resource_link": { "uri": "file:///screenshot.png", "mimeType": "image/png" } },
    { "type": "resource", "uri": "mcp://server/resource" }
  ]
}
```

Supported types:
- **text**: plain text
- **image**: image attachment (with MIME type)
- **audio**: audio content
- **resources**: MCP resource references

### 2.7 Advanced Session Management Features

| Feature | Method/Parameter | Description |
|---------|-----------------|-------------|
| **Persistence** | `session/load` + sessionId | Session survives across processes, resumable |
| **Naming** | label parameter | Supports human-readable names (e.g. "backend", "frontend") |
| **Reset** | `_meta.resetSession: true` | Clears conversation history while retaining session ID |
| **Forking** | fork capability | Derive a new session from an existing one (retains partial context) |
| **Deletion** | session/delete | Explicitly delete session and its history |
| **Directory routing** | directory parameter | Auto-match/create session by working directory |

### 2.8 Analogy with LSP

| Dimension | LSP | ACP |
|-----------|-----|-----|
| Purpose | Unify editor-language server communication | Unify editor-AI agent communication |
| Transport | stdio / socket | stdio / HTTP / WebSocket |
| Message format | JSON-RPC 2.0 | JSON-RPC 2.0 |
| Bidirectionality | Server can send diagnostics/completions | Agent can request files/terminals |
| Capability negotiation | Yes | Yes |
| Session state | None (stateless) | Yes (persistent sessions) |

---

## III. OpenClaw and acpx Architecture

### 3.1 OpenClaw ACP Bridge Architecture

OpenClaw adds a **WebSocket Gateway middleware layer** on top of standard ACP:

```
┌────────────┐    ACP (stdio)    ┌──────────────────┐    WebSocket    ┌──────────────────┐
│  IDE/Editor │ ←─────────────→  │ openclaw-acp-    │ ←────────────→ │ OpenClaw Gateway │
│  (Zed etc.) │    NDJSON        │ bridge           │                │ (:18789)         │
└────────────┘                   └──────────────────┘                └──────────────────┘
```

**Message translation mapping:**

| ACP Message | OpenClaw Gateway Message |
|-------------|-------------------------|
| `initialize` | Connect and register |
| `newSession` | Create `acp:<uuid>` session |
| `loadSession` | Resume Gateway session |
| `prompt` | → `chat.send` |
| `cancel` | → `chat.abort` |
| Gateway streaming events | → ACP `message` / `tool_call` updates |

**Session Key rules:**
- Default: `acp:<uuid>` (isolated session)
- Override: `--session agent:main:main` can point to an existing Gateway session
- Per-request: `_meta` object contains `sessionKey`, `sessionLabel`, `resetSession`, `requireExisting`

### 3.2 acpx Architecture

acpx is a **headless CLI client** for ACP, designed specifically for agent-to-agent automation:

```
┌──────────────────────────────────────────────────────┐
│                      acpx CLI                         │
│                                                       │
│  ┌─────────────────────────────────────────────────┐  │
│  │  Session Manager                                 │  │
│  │  ├── State: ~/.acpx/sessions/*.json             │  │
│  │  ├── Directory-walk routing (git root match)    │  │
│  │  ├── TTL-based queue ownership (default 300s)   │  │
│  │  └── IPC coordination (multi-instance)         │  │
│  └─────────────────────────────────────────────────┘  │
│                                                       │
│  ┌─────────────────────────────────────────────────┐  │
│  │  Output Formatter                                │  │
│  │  ├── text       (human-readable)                 │  │
│  │  ├── json       (automation, with metadata)      │  │
│  │  ├── json-strict (pure JSON, no stderr)          │  │
│  │  └── quiet      (final text only)               │  │
│  └─────────────────────────────────────────────────┘  │
└──────────────────────┬───────────────────────────────┘
                       │ spawn as child process (stdio pipe)
                       ▼
         ┌──────────────────────────────┐
         │       ACP Adapter Layer       │
         ├──────────────────────────────┤
         │ claude-agent-acp → Claude Code│  (Zed official, npx)
         │ codex-acp → Codex CLI        │  (Zed official, npx)
         │ gemini (native) → Gemini CLI │  (native ACP)
         │ opencode (native) → OpenCode │  (native ACP)
         │ pi-acp → Pi Agent            │  (community, npx)
         │ kimi (native) → Kimi         │  (native ACP)
         └──────────────────────────────┘
```

**acpx process model — queue owner model:**

```
acpx prompt "fix bug"
  │
  ├── find existing session (match by git root)
  │   ├── found and process alive → send prompt directly
  │   ├── found but process dead → re-spawn + session/load to restore
  │   └── not found → create new session
  │
  └── Queue Owner holds process ownership
      ├── default TTL: 300s (release after idle)
      ├── --ttl 0: keep alive permanently
      └── multiple acpx instances coordinate via IPC
```

**acpx CLI commands:**

| Command | Function |
|---------|----------|
| `acpx <agent> prompt "..."` | Send prompt to existing/new session |
| `acpx <agent> exec "..."` | One-shot temporary session (disposable) |
| `acpx <agent> sessions list` | List all sessions |
| `acpx <agent> sessions new` | Explicitly create new session |
| `acpx <agent> sessions close` | Close session |
| `acpx <agent> -s <name> "..."` | Specify named session (parallel workflows) |

**Adapter internal architecture (using codex-acp as example):**

- **Tech stack**: Rust + Tokio current-thread async runtime
- **Core modules**:
  - `agent/core.rs`: handles ACP requests (initialize, session/new, session/prompt, etc.)
  - `agent/events.rs`: converts Codex events into ACP update notifications
  - `agent/commands.rs`: handles slash commands
  - `SessionManager`: centralized session state, client notification, and context management
- **File operations**: handled by built-in MCP server (acp_fs), avoiding shell calls

**NDJSON envelope format (stable schema):**

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

## IV. ACP Ecosystem: Supported Tools and Platforms

### 4.1 Agents Supporting ACP (28 total)

#### Native Support (25) — Agent implements ACP protocol directly

| Agent Name | Developer | Characteristics | Maturity |
|-----------|-----------|----------------|---------|
| **Gemini CLI** | Google | Deep codebase understanding, multimodal | High |
| **GitHub Copilot** | GitHub/Microsoft | AI programming assistant | Public preview |
| **Junie** | JetBrains | JetBrains official Agent | High |
| **OpenClaw** | OpenClaw | Self-hosted, can act as Client or Agent | Medium |
| **OpenCode** | Open source community | Fully open source | Medium |
| **Cline** | Open source community | Multi-editor support (JetBrains/Zed/Neovim/Emacs) | Medium |
| **Goose** | Block/Square | Open source Agent | Medium |
| **OpenHands** | Open source community | — | Medium |
| **Kimi CLI** | Moonshot AI | Multi-language support | Medium |
| **Kiro CLI** | Amazon | — | Medium |
| **Qwen Code** | Alibaba | Strong multi-language support | Medium |
| Augment Code (Auggie) | Augment | Large-scale refactoring | Medium |
| AutoDev | Open source | Automated development | Early |
| Blackbox AI | Blackbox | Code search and generation | Medium |
| Docker cagent | Docker | Containerized Agent | Early |
| Factory Droid | Factory | Automated workflows | Early |
| Mistral Vibe | Mistral AI | Lightweight and fast | Early |
| AgentPool, Code Assistant, fast-agent, fount, Minion Code, Qoder CLI, Stakpak, VT Code | Various | Remaining 8 | Early |

#### Via Adapter (3) — Requires additional ACP wrapper

| Agent Name | Adapter | Maintainer | Reason |
|-----------|---------|-----------|--------|
| **Claude Code** | `@zed-industries/claude-agent-acp` | Zed official | TUI program (Ink/React), cannot speak JSON-RPC directly |
| **Codex CLI** | `@zed-industries/codex-acp` | Zed official | Same; needs adapter for translation |
| **Pi** | `pi-acp` | Community | — |

**Why do Claude Code and Codex need adapters?** Because they are TUI (Text User Interface) programs using Ink (a React-based terminal UI framework) to render their interface, requiring raw mode TTY. They natively communicate through terminal character streams and do not directly support JSON-RPC message exchange. The adapter's role is to translate between the ACP JSON-RPC protocol and the agent's internal API.

### 4.2 ACP Client Side (Editors/IDEs)

| Editor | Support Method | Status |
|--------|---------------|--------|
| **Zed** | Native support (ACP originator) | Production ready |
| **JetBrains IDEs** | AI Assistant plugin (2025.3.2+) | Production ready |
| **VS Code** | ACP Client extension | Preview |
| **Neovim** | Community plugin | Early |
| **Emacs** | Community plugin | Early |
| **Obsidian** | obsidian-agent-client plugin | Early |
| **Marimo** | Data science notebook | Early |
| **acpx** | Headless CLI client | Alpha |

### 4.3 ACP Agent Registry

JetBrains launched the **ACP Agent Registry** in January 2026 as a centralized platform for agent discovery and distribution (analogous to npm registry for packages), marking the ACP ecosystem's entry into the standardization phase.

---

## V. ACP Process Lifecycle and Known Issues

### 5.1 Process Model

```
acpx (or OpenClaw)
  │ spawn child process
  │ stdio: ['pipe', 'pipe', 'inherit']
  ▼
ACP Adapter (e.g. codex-acp)
  │ manage
  ▼
Actual Agent Process (e.g. Codex CLI)
```

**Key characteristics:**
- acpx communicates with the adapter via stdio pipe
- Adapter acts as the agent's management process
- Forms a two-layer process tree

### 5.2 Known Serious Issues

#### Issue 1: PTY Crash (Issue #28786)

**Status**: Fixed on 2026-03-04 (PR #34020)

**Symptom**: acpx spawns Claude Code/Codex child processes in pipe mode, but both tools require raw mode TTY (Ink/React-based terminal UI), causing them to crash immediately after launch:

```
Raw mode is not supported on the current process.stdin
```

**Impact**: `sessions_spawn runtime="acp"` consistently fails; claude and codex — the two primary agents — cannot be used.

**Fix**: Session-bootstrap reliability improvements including fallback hardening and explicit failure handling.

#### Issue 2: Silent Permission Failure (Issue #29195)

**Status**: Not fully resolved

**Symptom**: The default `permissionMode: approve-reads` in non-interactive mode causes Codex's write-file/execute-command requests to be silently rejected. Errors are only logged internally and the caller is not notified. `sessions_spawn` returns "accepted" and then the process quietly enters a zombie state.

**Consequences**:
- Parent agent cannot monitor child ACP session progress (forbidden errors)
- Codex process continues running 80+ minutes (0% CPU) without exiting after "completion"
- Caller believes the task succeeded; nothing was actually done

**Mitigation configuration**:
```bash
openclaw config set plugins.entries.acpx.config.permissionMode approve-all
openclaw config set plugins.entries.acpx.config.nonInteractivePermissions fail
```

#### Issue 3: Orphan Processes and Zombie Processes

**Symptom**: When the acpx/OpenClaw main process exits abnormally:
- Child agent processes become orphan processes
- Due to stdio pipe breakage, the agent cannot receive new instructions or send responses
- But the agent process itself doesn't necessarily exit — no timeout mechanism; may occupy resources indefinitely
- Documented cases of Codex processes running 80+ minutes without exiting

**acpx crash recovery mechanism**:
1. Next invocation detects that the saved session PID has died
2. Automatically re-spawns the Agent, attempts `session/load` to restore the session
3. Transparently falls back to `session/new` on failure (losing previous context)

### 5.3 Process Stability Comparison with tmux Approach

| Scenario | acpx/ACP | tmux (tcd) |
|----------|----------|------------|
| **Controller process crashes** | Agent becomes orphan; communication breaks; state uncertain | **tmux session completely unaffected**; tmux server runs independently |
| **Agent process crashes** | acpx attempts recovery on next invocation (may lose context) | tcd detects exit via `has-session`; collects last output from `.log` |
| **Long-running tasks** | May produce zombie processes (no timeout mechanism) | tmux sessions run stably; no time limit |
| **Multiple parallel agents** | Named sessions supported; limited by `maxConcurrentSessions` | Each tmux session fully independent; no limit |
| **Visibility** | No PTY; output only via ACP protocol; debugging is difficult | **`tmux attach` shows Agent real-time TUI output directly** |
| **Permission control** | Permission prompts in non-interactive mode cause silent failures | Can simulate interactive confirmation via `send-keys` |

---

## VI. Claude Code and ACP Relationship

### 6.1 Role Clarification

**ACP protocol defines two roles:**

```
┌─────────────────┐                    ┌──────────────────┐
│   ACP Client     │  ←── ACP ────→    │   ACP Agent      │
│   (initiator)    │                    │   (responder)     │
│                  │                    │                   │
│  - Zed           │                    │  - Claude Code    │
│  - JetBrains     │                    │  - Codex CLI      │
│  - VS Code       │                    │  - Gemini CLI     │
│  - acpx          │                    │  - OpenClaw       │
│  - OpenClaw*     │                    │  - Cline          │
└─────────────────┘                    └──────────────────┘

* OpenClaw can play both roles
```

**Key conclusion: In ACP, Claude Code is an Agent (server side), not a Client (client side).**

This means:
1. Claude Code **can be** called by an IDE/acpx via ACP
2. Claude Code **cannot** proactively call other Agents (like Codex) via ACP
3. In ACP's protocol design, the client is the editor/IDE, not the AI Agent

### 6.2 Viable Options for Claude Code to Call Codex CLI

| Option | Path | Structured Output | Session Management | Recommendation |
|--------|------|------------------|-------------------|---------------|
| **A. tcd (this project)** | Claude Code → bash → `tcd start -p codex` → tmux → Codex | Yes (JSON) | Yes (multi-turn) | Highest |
| **B. MCP tool** | Claude Code → MCP → `codex-subagents-mcp` → Codex subprocess | Yes | No (single-call) | High |
| **C. Direct shell** | Claude Code → bash → `codex -q "prompt"` | No | No | Medium |
| **D. OpenClaw orchestration** | OpenClaw as ACP Client scheduling Claude + Codex simultaneously | Yes | Yes | Medium |
| **E. Direct ACP call** | Claude Code → ACP → Codex | **Not feasible** | — | Not feasible |

**Core advantages of Option A (tcd)**:
- Good process isolation (tmux session is independent)
- Supports multi-turn conversation (`tcd send` for follow-up)
- Debuggable (`tcd attach` for real-time output)
- Complete result collection (ANSI cleanup + multi-strategy fallback)
- Does not require the agent to implement any protocol

**Option B (MCP) deployment steps**:

```bash
# 1. Install MCP server
npm install -g codex-subagents-mcp

# 2. Configure Claude Code MCP
# ~/.claude/mcp_settings.json
{
  "mcpServers": {
    "codex": {
      "command": "codex-subagents-mcp",
      "args": []
    }
  }
}

# 3. Claude Code can now call Codex via MCP tools
```

---

## VII. OpenClaw ACP Configuration and Deployment

OpenClaw supports two ACP integration modes:

### Mode 1: OpenClaw as ACP Client (calling external agents via acpx)

**Use case**: Let OpenClaw schedule Codex, Claude Code, Gemini CLI and other agents to execute coding tasks.

```
OpenClaw → acpx plugin → ACP adapter → Agent process
```

**Deployment steps:**

```bash
# Step 1: Install acpx plugin
openclaw plugins install acpx
openclaw config set plugins.entries.acpx.enabled true

# Step 2: Configure permissions (critical! otherwise write operations silently fail)
openclaw config set plugins.entries.acpx.config.permissionMode approve-all
openclaw config set plugins.entries.acpx.config.nonInteractivePermissions fail

# Step 3: Health check
/acp doctor
```

**openclaw.json configuration:**

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
      coalesceIdleMs: 300,     // streaming output coalesce interval
      maxChunkChars: 1200,     // max chars per chunk
    },
    runtime: {
      ttlMinutes: 120,         // session idle timeout
    },
  },
}
```

### Mode 2: OpenClaw as ACP Agent (called by IDEs)

**Use case**: Let Zed/JetBrains and other IDEs call OpenClaw via ACP.

```
IDE (Zed) → ACP stdio → openclaw acp → WebSocket → OpenClaw Gateway
```

**Deployment steps:**

```bash
# Step 1: Configure Gateway connection
openclaw config set gateway.remote.url wss://gateway-host:18789
openclaw config set gateway.remote.token-file ~/.openclaw-token  # recommended: use token-file

# Step 2: Register in Zed settings.json
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
# Step 3: Optional - route session to a specific Agent
openclaw acp --session agent:main:main
openclaw acp --session agent:design:main --label "design-work"
```

---

## VIII. ACP vs tmux Approach: System Comparison

### 8.1 Architecture Level

| Dimension | ACP Protocol | tmux/PTY Approach (tcd) |
|-----------|-------------|------------------------|
| **Communication layer** | JSON-RPC 2.0 over stdio pipe | Binary character stream (terminal emulation) |
| **Message semantics** | Structured JSON with method name, parameters, metadata | Raw character I/O; no semantic layering |
| **State tracking** | Explicit session ID + protocol-level state | Terminal state implicit; must infer from ANSI codes |
| **Bidirectional communication** | Native support (Agent can proactively request files/terminals) | Unidirectional I/O redirection (inject and read only) |
| **Permission control** | Explicit capability declaration + permission gating (approve-reads/all) | Commands execute directly; no interception layer |
| **Completion detection** | Protocol-native `PromptResponse` message | Three-strategy fallback (signal/marker/idle detection) |
| **Output parsing** | Structured JSON; zero parsing cost | Requires ANSI escape code cleanup; fragile |
| **Concurrency management** | Multi-session native support; IPC coordination | One independent tmux session per job |

### 8.2 Operations Level

| Dimension | ACP/acpx | tmux (tcd) |
|-----------|----------|------------|
| **Process stability** | Alpha; serious known bugs | Battle-tested; 30+ year history |
| **Process isolation** | Subprocess model; orphan process risk | tmux session naturally isolated from controller |
| **Debuggability** | No PTY; can only see JSON logs | `tcd attach` shows Agent real-time TUI directly |
| **Crash recovery** | Protocol-level recovery (implementation immature) | tmux session unaffected by controller crashes |
| **Dependency chain** | Node.js + npx + adapter + ACP stack | tmux + Python (minimal) |
| **Generality** | Only supports ACP-implementing agents | Any terminal program; zero adaptation cost |

### 8.3 Feature Level

| Feature | ACP | tcd (tmux) |
|---------|-----|------------|
| **Launch Agent** | `acpx codex prompt "..."` | `tcd start -p codex -m "..."` |
| **Multi-turn conversation** | `session/prompt` to same session | `tcd send <id> "..."` |
| **Completion detection** | Protocol-native (zero latency) | Three-strategy fallback (1–20s latency) |
| **Cancel operation** | `session/cancel` (protocol level) | `Ctrl+C` send-keys (not guaranteed) |
| **Get output** | JSON streaming chunks | `tcd output` (after ANSI cleanup) |
| **Parallel tasks** | Named sessions (`-s backend`) | Multiple tmux sessions |
| **Agent switching** | Same interface, switch agent | Same interface, switch provider |
| **Session recovery** | `session/load` (protocol level) | tmux session persistence |
| **Real-time observation** | None (JSON logs) | `tcd attach` |

### 8.4 Calling the Same Agent: Specific Differences

Using Codex CLI as an example:

| Stage | tcd (tmux) | ACP/acpx |
|-------|-----------|----------|
| Launch | `tmux new-session` + `script` recording + `codex` CLI | `acpx codex prompt "..."` → spawn `codex-acp` adapter |
| Send prompt | `tmux send-keys 'text' Enter` (character injection) | `session/prompt` JSON-RPC message |
| Receive output | `tmux capture-pane` + ANSI cleanup + marker scan | Receive `session/update` NDJSON stream |
| Completion detection | signal file → marker → idle detection (three-layer fallback) | `PromptResponse` message explicitly marks completion |
| Multi-turn | `tmux send-keys` inject again | `session/prompt` to same session |
| Result parsing | Extract from JSONL session file / capture-pane | Take JSON content field directly |

**One-sentence summary**: tmux is "pretending to be a human typing and looking at the screen in a terminal"; ACP is structured dialogue between programs.

---

## IX. User Experience Dimension Analysis

### 9.1 ACP User Experience Advantages

1. **High output quality**: JSON structured output; no ANSI cleanup needed; no truncation; no dropped characters
2. **Reliable completion detection**: protocol-level `PromptResponse`; no marker hacks or idle guessing
3. **Real-time streaming feedback**: reasoning process, tool calls, and response chunks all have independent events, enabling refined UI rendering
4. **Reliable cancel**: `session/cancel` stops immediately
5. **Controllable permissions**: can intercept agent file writes and command execution
6. **Native IDE integration**: Zed, JetBrains, Neovim all supported; smoother user experience
7. **Rich session management**: naming, forking, reset, cross-process recovery

### 9.2 ACP User Experience Disadvantages

1. **Limited agent coverage**: can only drive agents with ACP adapters; cannot drive arbitrary CLIs
2. **Extra dependencies**: requires Node.js (npx downloads adapters); increases environment setup cost
3. **Alpha instability**: serious known issues (PTY crash just fixed, silent permission failure unresolved)
4. **Difficult to debug**: cannot "see" what the agent is doing as directly as with tmux attach
5. **Orphan processes**: agent may become zombie after controller crashes; user must clean up manually
6. **Complex configuration**: misconfigured permissions cause silent failures that are hard to diagnose

### 9.3 tmux (tcd) User Experience Advantages

1. **Strong generality**: any program that runs in a terminal can be driven; no adaptation needed from target program
2. **Strong debuggability**: `tcd attach` shows the agent's real-time TUI output directly; issues immediately visible
3. **Zero extra dependencies**: only tmux + Python
4. **Process stability**: tmux is a Unix cornerstone tool; 30+ year history
5. **Low-latency launch**: no protocol handshake or capability negotiation needed
6. **Perfect process isolation**: tmux session is independent from all controller processes

### 9.4 tmux (tcd) User Experience Disadvantages

1. **Fragile ANSI parsing**: output cleanup is a perpetual pain point; AI CLI TUI rendering varies endlessly
2. **Completion detection latency**: worst case requires 15–20s idle detection; marker protocol is not guaranteed
3. **Cancel unreliable**: `send-keys Ctrl+C` does not guarantee the agent responds correctly
4. **No permission interception**: agent in tmux can execute any operation
5. **Unstructured output**: additional parsing needed to extract useful information

---

## X. tcd Project Evolution Recommendations

### 10.1 Short-term Strategy (now)

**Continue with the tmux approach.** Rationale:
- tcd has completed Phases 1–4 with 119 tests passing; it is a usable tool
- ACP/acpx still has serious known bugs (PTY crash just fixed; silent permission failure unresolved)
- The core scenario of Claude Code → Codex is fundamentally impossible with ACP (role model limitation); tcd supports it naturally
- tmux's generality guarantees instant support for any new CLI tool

### 10.2 Medium-term Strategy (3–6 months)

**Monitor ACP ecosystem maturity**, watching for these signals:
- acpx moves from alpha to beta/stable
- Silent permission failure issue fully resolved
- Orphan process issue has an official timeout/cleanup mechanism
- Claude Code or Codex natively supports ACP (no adapter needed)

### 10.3 Long-term Strategy (6–12 months)

**Consider hybrid architecture** — retain tmux for process isolation container; use ACP to replace the communication layer for agents that support it:

```
┌─────────────────────────────────────────────────────┐
│                    tcd v2 (hybrid architecture)       │
│                                                       │
│  ┌─────────────┐  ┌─────────────┐  ┌─────────────┐  │
│  │ ACP Channel  │  │ PTY Channel  │  │ Hybrid      │  │
│  │ (structured) │  │ (universal)  │  │ Channel     │  │
│  └──────┬──────┘  └──────┬──────┘  └──────┬──────┘  │
│         │                │                │           │
│         ▼                ▼                ▼           │
│   ACP-supporting     Non-ACP CLI       tmux isolation │
│   Agents             tools             + ACP comms    │
│   (Claude, Codex)                                     │
└─────────────────────────────────────────────────────┘
```

**Value of the hybrid approach**:
- tmux continues providing process isolation and debuggability
- ACP solves the fragile ANSI parsing and unreliable completion detection pain points
- Non-ACP agents continue via PTY fallback path
- Gradual migration; risk is controllable

### 10.4 Decision Matrix

| Condition | Action |
|-----------|--------|
| acpx still alpha + serious bugs | Maintain tmux approach |
| acpx stable + broad agent coverage | Evaluate hybrid architecture POC |
| Claude Code natively supports ACP | Prioritize connecting ACP for Claude Provider |
| New CLI tool doesn't support ACP | Retain tmux PTY channel |

---

## XI. References

### Protocol Specifications and Official Documentation
- [Agent Client Protocol website](https://agentclientprotocol.com/)
- [ACP Protocol Specification GitHub](https://github.com/agentclientprotocol/agent-client-protocol)
- [ACP Protocol Overview (hexdocs/acpex)](https://hexdocs.pm/acpex/protocol_overview.html)
- [Zed ACP page](https://zed.dev/acp)
- [JetBrains ACP documentation](https://www.jetbrains.com/help/ai-assistant/acp.html)

### OpenClaw and acpx
- [acpx GitHub repository](https://github.com/openclaw/acpx)
- [acpx AGENTS.md](https://github.com/openclaw/acpx/blob/main/AGENTS.md)
- [OpenClaw ACP Agents documentation](https://docs.openclaw.ai/tools/acp-agents)
- [OpenClaw docs.acp.md](https://github.com/openclaw/openclaw/blob/main/docs.acp.md)
- [OpenClaw ACP 2026 Complete Guide](https://dev.to/czmilo/2026-complete-guide-openclaw-acp-bridge-your-ide-to-ai-agents-3hl8)

### Adapters
- [zed-industries/claude-agent-acp](https://github.com/zed-industries/claude-agent-acp)
- [codex-acp adapter](https://github.com/cola-io/codex-acp)
- [codex-subagents-mcp](https://github.com/leonardsellem/codex-subagents-mcp)

### Analysis and Commentary
- [PromptLayer: ACP - The LSP for AI Coding Agents](https://blog.promptlayer.com/agent-client-protocol-the-lsp-for-ai-coding-agents/)
- [goose blog: Intro to ACP](https://block.github.io/goose/blog/2025/10/24/intro-to-agent-client-protocol-acp/)
- [JetBrains ACP Agent Registry launch blog](https://blog.jetbrains.com/ai/2026/01/acp-agent-registry/)
- [AI SDK ACP Community Provider](https://ai-sdk.dev/providers/community-providers/acp)

### Known Issues
- [Issue #28786: PTY crash issue](https://github.com/openclaw/openclaw/issues/28786)
- [Issue #29195: Codex silent permission failure](https://github.com/openclaw/openclaw/issues/29195)

### This Project
- [tcd 架构文档](../architecture.md)（已取代当年的 prd.md / design.md）
