# Research Report: claude_code_bridge (bfly123) — In-Depth Source Architecture Analysis

**Date**: 2026-03-02
**Version**: v5.2.6
**Repository**: https://github.com/bfly123/claude_code_bridge
**Objective**: Deep-dive into CCB's daemon architecture, token efficiency mechanism, and cross-AI collaboration protocol

---

## Research Summary

Claude Code Bridge (CCB) is a multi-AI collaboration platform built on terminal split-panes, driving Claude, Codex, Gemini, OpenCode, and Droid in independent panes via tmux/WezTerm. Its core innovation is a "Terminal-as-Bus" architecture: instead of going through an API, it achieves bidirectional communication through terminal injection and log reading. Each call only sends a task instruction (50–200 tokens); the AI's full context is preserved in its own CLI session. This is a mature, production-grade solution (v5.2.6, with a CHANGELOG documenting numerous edge-case fixes).

---

## 1. Overall Architecture

```
User / Script
    │
    ▼
bin/ask (unified entry) ─── JSON-RPC over TCP ──► askd daemon (TCP server)
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

### Core Design Philosophy

- **No API calls**: All AIs run in the terminal via CLI tools (claude, codex, gemini CLI, etc.)
- **Terminal as bus**: Injecting text into a terminal pane = sending a request; reading the session log file = receiving a response
- **Session persistence**: Each AI maintains its own independent session; context is preserved on the AI side, no need to resend the full history each time
- **Token efficiency**: Instructions are compact (50–200 tokens/call); the AI session self-maintains the full history

---

## 2. Daemon Architecture

### 2.1 Daemon Hierarchy

```
askd (unified entry daemon, bin/askd)
  └─ dispatches by provider to each daemon module:
       ├─ askd.daemon/caskd  - Claude daemon (lask/laskd)
       ├─ askd.daemon/gaskd  - Gemini daemon
       ├─ askd.daemon/oaskd  - OpenCode daemon
       ├─ askd.daemon/daskd  - Droid daemon
       └─ askd.daemon/laskd  - Generic Claude daemon (alternate implementation)
```

Note: `providers.py` registers 5 providers:

| Provider | Daemon Key | Protocol Prefix | Session File |
|----------|------------|-----------------|--------------|
| Claude   | caskd      | cask            | .codex-session |
| Gemini   | gaskd      | gask            | .gemini-session |
| OpenCode | oaskd      | oask            | .opencode-session |
| Claude2  | laskd      | lask            | .claude-session |
| Droid    | daskd      | dask            | .droid-session |

### 2.2 Daemon Lifecycle

- **Auto-start**: When the first request arrives, the client checks whether the daemon is running; if not, it forks and starts it
- **Idle auto-stop**: Automatically shuts down after 60 seconds with no requests (via idle monitor thread)
- **Parent process monitoring**: If the process that started the daemon exits, the daemon auto-closes
- **State file**: The daemon writes `~/.ccb/run/{prefix}d.json` containing pid/host/port/token

### 2.3 TCP Server Implementation (askd_server.py)

```python
class Server(socketserver.ThreadingTCPServer):
    allow_reuse_address = True
```

- Creates a new thread per connection (ThreadingTCPServer pattern)
- Token authentication (randomly generated, written to state file)
- Three message types: ping / shutdown / request
- Two monitoring daemon threads: idle_timeout_monitor + parent_process_monitor
- Shared state: `active_requests` counter + `last_activity` timestamp (protected by activity_lock)

### 2.4 Per-Session Worker Pool (worker_pool.py)

```
PerSessionWorkerPool
  └─ dict[session_key → BaseSessionWorker]
       └─ BaseSessionWorker (threading.Thread)
            └─ internal queue, serializes requests within the same session
```

- Each session has a dedicated worker thread, ensuring requests within a session are serialized
- Different sessions run in parallel
- Workers are automatically rebuilt when they die (health check in pool)

---

## 3. Communication Protocol (ccb_protocol.py)

### 3.1 Request ID Format

```
CCB_REQ_ID: YYYYMMDD-HHMMSS-mmm-PID-counter
```

Example: `CCB_REQ_ID: 20260302-143022-456-12345-001`

### 3.2 Prompt Wrapping Mechanism (wrap_codex_prompt)

The prompt sent to the AI is wrapped as:

```
CCB_REQ_ID: {req_id}
CCB_BEGIN:
{actual user message, 50-200 tokens}
Append at the end of your reply: CCB_DONE:{req_id}
```

**This is the key to token efficiency**:
- Only the task instruction is sent (extremely short)
- Conversation history is self-maintained by the AI's session; not resent with each request
- The completion marker tells the poller when to stop reading

### 3.3 Completion Detection

```python
def is_done_text(text: str, req_id: str) -> bool:
    """Scan backward from the end of the response to find a valid CCB_DONE:{req_id} marker"""
```

- Tolerates trailing noise (blank lines, other markers)
- Scans backward from the end of the text
- Filters out generic markers that the harness may append

### 3.4 JSON-RPC Protocol (askd_rpc.py)

Client → Daemon communication:

```json
Request:  {"type": "{prefix}.request", "v": 1, "id": "...", "token": "...",
           "work_dir": "...", "timeout_s": float, "message": "...", "quiet": bool}

Response: {"type": "{prefix}.response", "reply": "...", "exit_code": int}
```

Message format: newline-delimited JSON over TCP socket

---

## 4. Per-AI Communication Layer

### 4.1 Unified Abstraction Pattern

Each AI has a corresponding `*_comm.py` file implementing two core classes:

```
XxxLogReader      - Reads the AI session log file
XxxCommunicator   - Bidirectional communication (inject + read)
    ├── ask_async()  - Async send, no wait for response
    └── ask_sync()   - Sync send, waits for CCB_DONE marker
```

### 4.2 Claude Communication (claude_comm.py)

- **Log location**: Session files under `~/.claude/projects/<key>/`
- **Session discovery**: Multiple strategies (env var → filesystem scan → index lookup)
- **Message injection**: Via tmux `send-keys` or WezTerm text injection
- **Response reading**: Parses Claude's JSONL session log, extracts response items, filters thinking blocks
- **Subagent support**: Can track Claude's sub-agent logs

### 4.3 Codex Communication (codex_comm.py)

- **Log location**: `~/.codex/sessions/`
- **Dual transport mode**:
  - tmux mode: sent via FIFO pipe (more reliable)
  - WezTerm mode: direct text injection into terminal
- **Reverse line iteration**: Reads from the end of the log, avoids re-reading the full file
- **Health check**: Verifies whether the codex process and terminal pane are still alive

### 4.4 Gemini Communication (gemini_comm.py)

Special challenges:
- Gemini 0.29 introduced a **dual hash strategy** for session discovery (compatible with two hash algorithms)
- Sometimes does not output a completion marker → 15-second idle detection fallback
- `--autostart` flag for offline daemon auto-start

### 4.5 OpenCode Communication (opencode_comm.py)

- **Storage format**: JSON files + SQLite dual-source (flexible migration)
- **Paths**: `storage/session/<projectID>/ses_*.json`, `../opencode.db`
- **Cancellation detection**: Monitors `MessageAbortedError` + log tail (dual mechanism)
- **Session filtering**: Routes to specific sessions via `session_id_filter`

---

## 5. Token Efficiency Mechanism (How 50–200 Tokens/Call Is Achieved)

### 5.1 Why So Low?

A traditional API call must send: system prompt + full conversation history + new message = thousands of tokens

CCB's approach:
1. **AI CLI maintains session**: claude/codex/gemini CLI manages conversation history itself
2. **Only send the new message**: `CCB_REQ_ID: ... \n CCB_BEGIN: \n {new instruction} \n Append CCB_DONE:... at end of reply`
3. **Terminal injection bypasses the API**: Simulates keyboard input directly, equivalent to typing manually
4. **No overhead**: No API envelope, headers, or repeated system prompt

### 5.2 Context Transfer (memory/transfer.py)

When context needs to be passed across sessions:
- `ContextTransfer` class implements an 8000-token budget manager
- Deduplication: `ConversationDeduper` removes duplicate message pairs
- Truncation: `last_n` parameter retains only the last N turns
- Pipeline: parse → dedupe → collapse tool calls → build pairs → truncate → estimate tokens → format → send
- Persistence: transfer records saved to `./.ccb/history/`

### 5.3 Memory-First Architecture (docs/memory-first-agent-architecture.md)

Document defines the high-level design philosophy:
- **Role A (Memory Keeper)**: Maintains persistent knowledge across sessions
- **Role B (Context Builder)**: Assembles short-term context for executors
- **Role C (Executor)**: Stateless execution, receives pre-assembled context
- **Role T (Task Tracker)**: Prevents context bloat from multi-window tasks
- **Three-tier storage**: L1 hot (Redis) / L2 warm (SQLite) / L3 cold (ChromaDB)
- **Core principle**: "Don't let the model remember — let the model query"

---

## 6. Session Management System

### 6.1 Session Registry (pane_registry.py)

```
~/.ccb/run/ccb-session-{id}.json
```

- TTL: 7 days
- Atomic JSON writes
- Supports legacy flat keys and new nested `providers` structure (auto-migration)
- Fields: terminal backend, pane ID, provider session paths, work dir, project ID

### 6.2 Multi-Level Lookup Strategy

1. Direct lookup by session ID
2. Match by Claude pane identifier
3. Combination of project ID + provider (enforces directory isolation)

### 6.3 Pane Recovery Mechanism (gaskd_session.py, laskd_session.py)

```
ensure_pane():
  1. backend.is_alive(pane_id) → if alive, use directly
  2. Search pane by title marker
  3. tmux respawn_pane() to respawn the pane
  4. Save crash log before respawning
```

- Supports tmux auto-respawn
- Pane title marker serves as fallback identifier
- `maybe_auto_transfer()` context migration triggered on session switch

---

## 7. WezTerm/tmux Integration Layer (terminal.py)

### 7.1 Backend Abstraction

```python
class TerminalBackend:
    def send_keys(pane_id, text)
    def capture_pane(pane_id)
    def is_alive(pane_id)
    def respawn_pane(pane_id, cmd)
    def new_pane(cmd)
    def get_pane_title(pane_id)
```

### 7.2 Auto-Detection

- Linux/macOS/WSL: uses tmux
- Windows: uses WezTerm + PowerShell
- Can be overridden via environment variable

### 7.3 WezTerm Differences

- WezTerm does not support FIFO; uses direct text injection instead
- PowerShell wrapper handles Windows command-line length limits (via stdin piping)
- Wrapper files with `.cmd` / `.bat` suffix are filtered out (completion hook requires Python direct execution)

---

## 8. Cross-AI Task Delegation Mechanism

### 8.1 Claude as Orchestrator

The `bin/ask` script provides a unified interface to all providers:

```bash
ask codex "implement feature X"     # Claude instructs Codex
ask gemini "review this code"       # Claude instructs Gemini
ask opencode "..."                  # Claude instructs OpenCode
```

Claude's SKILL.md defines how to use the `ask` command to delegate tasks to other AIs.

### 8.2 Codex → OpenCode Delegation

Implemented via `codex_dual_bridge.py`:
- DualBridge reads FIFO input from Claude (JSON payload)
- Forwards commands to the Codex terminal
- Essentially a one-way command injection bridge, not true peer-to-peer

### 8.3 Async Anti-Loop Mechanism (format_guardrails.py)

Key design: after Claude submits an `ask`, **polling is prohibited**

```
Hard rule in claude-md-ccb.md:
"END YOUR TURN NOW. Reply ONLY '[Provider] processing...', then stop."
```

- Enforced at the prompt level via CLAUDE.md skill rules
- Prevents Claude from continuing to poll after an async submission → deadlock/loop
- v5.2.5 specifically fixed this problem

### 8.4 Completion Hook (completion_hook.py)

Async task completion notification system:
- Executes in a background thread (does not block the daemon)
- Notifies the caller via the `ccb-completion-hook` script
- Supports email integration (SMTP, 3 retries, max 8s backoff)
- Timeout: 60 seconds

---

## 9. Directory Structure Key File Index

```
claude_code_bridge/
├── bin/
│   ├── ask              - unified task dispatch entry (all providers)
│   ├── askd             - daemon manager
│   ├── cask/gask/oask   - provider-specific clients
│   ├── cpend/gpend      - query pending responses
│   ├── cping/gping      - connectivity check
│   ├── ccb-completion-hook - async completion notification
│   └── ctx-transfer     - manual cross-AI context migration
├── lib/
│   ├── ccb_protocol.py    - core protocol (REQ_ID, BEGIN, DONE markers)
│   ├── askd_server.py     - TCP daemon server
│   ├── askd_client.py     - daemon client + RPC
│   ├── askd_rpc.py        - JSON-RPC over TCP
│   ├── askd_runtime.py    - daemon path/log utilities
│   ├── worker_pool.py     - per-session worker pool
│   ├── providers.py       - 5-provider registry
│   ├── terminal.py        - tmux/WezTerm abstraction layer
│   ├── pane_registry.py   - session registry (JSON persistence)
│   ├── completion_hook.py - async completion callback
│   ├── claude_comm.py     - Claude communication layer
│   ├── codex_comm.py      - Codex communication layer
│   ├── gemini_comm.py     - Gemini communication layer
│   ├── opencode_comm.py   - OpenCode communication layer
│   ├── droid_comm.py      - Droid communication layer
│   ├── codex_dual_bridge.py - Claude→Codex command bridge
│   ├── format_guardrails.py - code format guardrails
│   ├── ctx_transfer_utils.py - context migration utilities
│   ├── laskd_session.py   - Claude session management
│   ├── gaskd_session.py   - Gemini session management
│   ├── oaskd_session.py   - OpenCode session management
│   └── memory/
│       ├── transfer.py    - context transfer (8K token budget)
│       ├── deduper.py     - conversation deduplication
│       ├── formatter.py   - context formatting
│       └── session_parser.py - session log parser
└── docs/
    └── memory-first-agent-architecture.md - high-level architecture design doc
```

---

## 10. Relevance to the Elvis Codex + ClaudeCode Project

### 10.1 Designs Worth Directly Reusing

| CCB Technique | How Elvis Can Reuse It |
|--------------|----------------------|
| `ccb_protocol.py` REQ_ID + CCB_DONE marker | Implement Claude subagent completion detection |
| `worker_pool.py` per-session serialization | Prevent concurrent contamination within the same session |
| `askd_server.py` TCP daemon + idle timeout | Reference for daemon lifecycle management |
| Session registry JSON structure | Multi-AI session state persistence |
| `completion_hook.py` async notification pattern | Async task completion callback design |
| Async guardrail (prohibit polling) pattern | Prevent orchestrator AI loops |

### 10.2 Parts Not Suitable for Direct Reuse

| CCB Technique | Reason |
|--------------|--------|
| Full tmux/WezTerm injection architecture | Elvis uses nanobot SDK, not terminal injection |
| Complete daemon suite | Too heavyweight; not suitable for embedding in nanobot |
| Memory-First three-tier storage | Too complex, out of MVP scope |

### 10.3 Key Insights

1. **Token efficiency comes from session persistence**: Let the AI maintain its own session; only send new instructions each time
2. **Completion detection is the core challenge**: Each AI has a different completion signal (CCB spent many versions iterating to solve this)
3. **Async guardrails are mandatory**: Without constraining the orchestrator at the prompt level, loops are inevitable
4. **Cross-platform communication polymorphism**: The same interface has different implementations under tmux/WezTerm/FIFO

---

## 11. Summary

CCB is currently the most mature open-source multi-AI collaboration terminal solution (v5.2.6, actively maintained). Its core innovations:

1. **No API calls** — bidirectional communication via terminal injection + log reading
2. **Token efficiency** — each AI CLI self-maintains its session; history is not resent
3. **CCB_DONE marker protocol** — the key to unified completion detection
4. **Per-session worker pool** — prevents concurrent contamination
5. **Async guardrail** — prevents orchestrator loops at the CLAUDE.md/prompt level

Most valuable to the Elvis project: session management patterns, completion detection protocol design, and the async guardrail anti-loop mechanism.

---

## References

- [bfly123/claude_code_bridge GitHub](https://github.com/bfly123/claude_code_bridge)
- [ccb_protocol.py](https://raw.githubusercontent.com/bfly123/claude_code_bridge/main/lib/ccb_protocol.py)
- [askd_server.py](https://raw.githubusercontent.com/bfly123/claude_code_bridge/main/lib/askd_server.py)
- [worker_pool.py](https://raw.githubusercontent.com/bfly123/claude_code_bridge/main/lib/worker_pool.py)
- [memory/transfer.py](https://raw.githubusercontent.com/bfly123/claude_code_bridge/main/lib/memory/transfer.py)
- [docs/memory-first-agent-architecture.md](https://raw.githubusercontent.com/bfly123/claude_code_bridge/main/docs/memory-first-agent-architecture.md)
- [completion_hook.py](https://raw.githubusercontent.com/bfly123/claude_code_bridge/main/lib/completion_hook.py)
- [providers.py](https://raw.githubusercontent.com/bfly123/claude_code_bridge/main/lib/providers.py)
