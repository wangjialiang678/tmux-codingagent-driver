# tmux-bridge vs tcd Comparative Research Report

> Research date: 2026-03-02
> Research objective: Analyze positioning differences, technical overlap, and integration opportunities between the two local projects

## 1. Project Positioning Comparison

| Dimension | tmux-bridge | tcd (tmux-codingagent-driver) |
|-----------|------------|-------------------------------|
| **One-line positioning** | Low-level tmux session driver layer (single task) | Multi-AI orchestration middleware (multi-provider + task management) |
| **Core metaphor** | "Bridge" — connects the caller to the AI CLI | "Driver" — manages multiple AI vehicles working in parallel |
| **Abstraction level** | Low: one prompt → one tmux session → one result | High: job queue + provider registry + SDK + CLI |
| **Target users** | Nanobot/OpenClaw/Claude Code Skill | Claude Code/custom orchestrators/Python scripts |
| **Supported AIs** | Primarily Codex (Codex behavior hardcoded) | Codex + Claude Code + Gemini CLI (pluggable) |

### Key Differences

**tmux-bridge** is a "single-task driver": give it a prompt, and it handles tmux session creation, text transport, completion detection, and output cleaning, returning the result. It does not concern itself with relationships between tasks.

**tcd** is a "multi-task orchestrator": it manages the lifecycle of a set of jobs (creation, status tracking, persistence, multi-turn conversation, bulk cleanup) and supports different AI CLI backends through a provider abstraction.

## 2. Architecture Comparison

### tmux-bridge Architecture (Flat Modular)

```
Caller → CLI/Python API
           ↓
    ┌─────────────────────────────────┐
    │  session.py    → session lifecycle   │
    │  transport.py  → text transport      │
    │  capture.py    → output capture      │
    │  completion.py → completion detection│
    │  output.py     → output cleaning/parsing │
    │  cli.py        → CLI entry point     │
    └─────────────────────────────────┘
           ↓
       tmux + Codex CLI
```

### tcd Architecture (Layered + Plugin-Based)

```
Caller → CLI (click) / Python SDK
           ↓
    ┌─────────────────────────────────────┐
    │  Orchestration layer                │
    │  ├── sdk.py        → Python API     │
    │  ├── cli.py        → CLI entry point│
    │  └── job.py        → Job state machine│
    ├─────────────────────────────────────┤
    │  Detection layer                    │
    │  ├── collector.py  → 3-tier response collection │
    │  ├── marker_detector.py → marker protocol │
    │  └── idle_detector.py  → idle detection  │
    ├─────────────────────────────────────┤
    │  Driver layer                       │
    │  ├── tmux_adapter.py → tmux primitives │
    │  ├── provider.py     → ABC + registry  │
    │  ├── output_cleaner.py → ANSI cleaning │
    │  └── providers/                     │
    │      ├── codex.py                   │
    │      ├── claude.py                  │
    │      └── gemini.py                  │
    └─────────────────────────────────────┘
           ↓
       tmux + (Codex | Claude Code | Gemini)
```

## 3. Core Module Feature Comparison

### 3.1 Session Management

| Feature | tmux-bridge | tcd |
|---------|------------|-----|
| Session creation | `TmuxSession.create()` | `tmux_adapter.create_session()` |
| Session naming | `tmux-bridge-{job_id}` | `tcd-{job_id}` |
| `script -q` wrapping | Yes | Yes |
| Platform detection (macOS/Linux) | Yes | Yes |
| Keep-alive (`read`) | Yes | Yes |
| Scrollback configuration | Yes (50000) | Yes (50000) |
| Update prompt skip | Yes (send "3" + Enter) | No (handled at provider level) |
| Trust dialog handling | No | Yes (Claude Code trust dialog) |
| Job JSON persistence | Simple JSON | Full state machine (pending→running→completed→failed) |
| Multi-turn conversation | `send` command | `send` + turn_count tracking |

### 3.2 Text Transport

| Feature | tmux-bridge | tcd |
|---------|------------|-----|
| Short text routing | `send-keys -l` (< 4096B and no newline) | `send-keys -l` (< 5000 chars) |
| Long text routing | `load-buffer` + `paste-buffer` | `load-buffer` + `paste-buffer -p` |
| UTF-8 safe chunking | Yes (4096B byte-level) | Yes (5000 char-level) |
| Chunking threshold | 4096 bytes | 5000 characters |
| Newline-based routing | Yes (newline present → buffer) | No (length only) |
| Bracketed paste (`-p`) | No | Yes (resolves Ink TUI issue) |

**Key difference**: tmux-bridge counts in bytes (more rigorous, avoids tmux hard limits); tcd counts in characters (simpler). tcd's `-p` flag is a key improvement for handling Ink framework TUIs.

### 3.3 Completion Detection

| Strategy | tmux-bridge | tcd |
|----------|------------|-----|
| Signal file | Yes (notify-hook → `.turn-complete`) | Yes (notify-hook → `.turn-complete`) |
| Marker protocol | Yes (SESSION_COMPLETE_MARKER) | Yes (TCD_REQ/TCD_DONE protocol) |
| Idle detection | No | Yes (continuous capture comparison, configurable threshold per provider) |
| Timeout detection | Yes (log mtime) | Yes (job-level timeout) |
| Session death detection | Yes (has-session) | Yes (session_exists) |
| Context exhaustion detection | No | Yes (context_limit state) |

**Key difference**: tmux-bridge's marker is passive (echo after CLI exits); tcd's is active (injects a prompt requiring the AI to output `TCD_DONE`). tcd adds idle detection as a "universal fallback."

### 3.4 Output Processing

| Feature | tmux-bridge | tcd |
|---------|------------|-----|
| ANSI cleaning | Yes (CSI/OSC/DCS/ESC + carriage return handling) | Yes (CSI/OSC + deduplication) |
| TUI noise filtering | Yes (progress bars, status lines) | Yes (context left, markers) |
| NDJSON parsing | Yes (4-layer JSON extraction) | Yes (Codex/Claude JSONL) |
| Semantic depth constants | Yes (STATUS/HEALTH/CONTEXT/CHECKPOINT/FULL) | No (fixed logic) |
| Output source fallback | capture-pane → log file | Provider parsing → capture-pane → script log |
| Structured CodexOutput | Yes (thread_id, files_modified, tokens) | No (returns str) |

**Key difference**: tmux-bridge's output processing is more fine-grained (4-layer JSON extraction, structured CodexOutput); tcd prioritizes cross-provider generality.

### 3.5 CLI Interface

| Command | tmux-bridge | tcd |
|---------|------------|-----|
| Start task | `start <prompt>` | `start -p <provider> -m <prompt>` |
| Send message | `send <id> <msg>` | `send <id> <msg>` |
| View status | `status <id>` | `status <id>` + `check <id>` |
| Get output | `output <id>` | `output <id>` |
| Kill task | `kill <id>` | `kill <id>` |
| List tasks | `list` | `jobs` |
| Attach session | No (code has it, CLI doesn't) | `attach <id>` |
| Wait for completion | No (caller must poll) | `wait <id> --timeout N` |
| Clean up | No | `clean [--all] [--before 7d]` |
| Output format | All JSON | Human-readable by default, `--json` optional |

## 4. Technology Stack Comparison

| Dimension | tmux-bridge | tcd |
|-----------|------------|-----|
| Python version | 3.11+ | 3.10+ |
| Runtime dependencies | **Zero** (pure stdlib) | `click>=8.0` |
| Dev dependencies | pytest | pytest |
| Package manager | uv | uv |
| Build system | setuptools | hatchling |
| CLI framework | argparse (built-in) | click |
| Code volume | ~800 LOC | ~2000 LOC |
| Test count | 36 | 119 |
| Module count | 6 | 12 |

## 5. Design Philosophy Comparison

| Principle | tmux-bridge | tcd |
|-----------|------------|-----|
| Dependency strategy | Zero-dependency minimalism | Minimal dependencies (click only) |
| Provider extension | Via SessionConfig parameterization (same code path) | ABC + registry + independent provider subclasses |
| State management | Stateless (job info in caller's memory) | Stateful (JSON file persistence, job state machine) |
| Error handling | Return values + exceptions | State machine (failed state) |
| Configuration model | SessionConfig dataclass | Provider attributes + CLI flags |
| Testing strategy | Mock subprocess (no tmux required) | Mock subprocess (no tmux required) |

## 6. Shared Design Origins

Both projects researched the same 4 open-source projects and reused similar design elements:

| Source project | tmux-bridge adopted | tcd adopted |
|---------------|--------------------|--------------------|
| **codex-orchestrator** | `script -q` logging, `read` keep-alive, notify-hook | tmux operation patterns, notify-hook, ANSI cleaning |
| **NTM** | 4096B UTF-8 chunking, semantic depth constants | — |
| **MCO** | Protocol contract, 4-layer JSON extraction | — |
| **claude_code_bridge** | Completion marker protocol | Provider abstraction, marker protocol, idle detection |

## 7. Code Overlap Estimates

| Feature Domain | Overlap | Notes |
|---------------|---------|-------|
| tmux session creation | **90%** | Nearly identical: detach + script + read |
| Text transport | **80%** | Same dual-routing strategy; thresholds and details differ slightly |
| Completion detection (signal file) | **95%** | Completely identical notify-hook mechanism |
| ANSI cleaning | **70%** | Both cover CSI/OSC; tmux-bridge is more comprehensive |
| Job management | **20%** | tcd has a full state machine; tmux-bridge only has JobInfo |
| Provider abstraction | **0%** | tmux-bridge has no such concept |
| CLI | **40%** | Command sets overlap but implementations differ |

**Overall overlap: approximately 50–60%**

## 8. Respective Advantages

### tmux-bridge Unique Advantages

1. **Zero dependencies**: Pure stdlib; runs in any Python environment
2. **Finer-grained output parsing**: 4-layer JSON extraction, structured CodexOutput (includes thread_id, files_modified, tokens)
3. **Semantic depth constants**: STATUS(20) / HEALTH(50) / CONTEXT(500) / FULL(-1); callers can choose as needed
4. **Nanobot adapter**: Ready-made Tool ABC adapter
5. **UTF-8 byte-level chunking**: More rigorous handling of tmux hard limits
6. **Codex update prompt skip**: Automatically sends "3" + Enter

### tcd Unique Advantages

1. **Multi-provider support**: Codex + Claude Code + Gemini, pluggable extension
2. **Job state persistence**: JSON files + atomic writes; recovers after process restart
3. **Idle detection**: Continuous capture-pane comparison; solves "AI doesn't follow the marker protocol" problem
4. **Multi-turn tracking**: turn_count + req_id mechanism
5. **Python SDK**: `from tcd import TCD`, object-oriented API
6. **`wait` command**: Blocking wait for completion (eliminates need for polling logic)
7. **Bracketed paste**: `paste-buffer -p` resolves Ink TUI multi-line input
8. **Trust dialog handling**: Automatic handling of Claude Code's trust folder dialog
9. **Context exhaustion detection**: Identifies the AI's context_limit state
10. **`clean` command**: Job file lifecycle management

## 9. Integration Recommendations

### Option A: tcd Depends on tmux-bridge (Recommended)

```
Caller → tcd (CLI/SDK)
           ↓
    ┌───────────────────────────┐
    │  tcd layer (new value)     │
    │  ├── Job state machine     │
    │  ├── Provider registry     │
    │  ├── Multi-turn management │
    │  ├── SDK                   │
    │  └── wait/clean commands   │
    ├───────────────────────────┤
    │  tmux-bridge layer (reused)│
    │  ├── session management    │
    │  ├── transport             │
    │  ├── capture               │
    │  ├── completion detection  │
    │  └── output cleaning/parsing│
    └───────────────────────────┘
           ↓
       tmux + AI CLIs
```

**Pros**:
- Eliminates ~1000 LOC of duplicate code (tmux adapter + completion detection + output cleaning)
- tmux-bridge's fine-grained output parsing capabilities are shared by all tcd providers
- tmux-bridge stays zero-dependency and can be used independently

**Cons**:
- Adds a dependency
- tmux-bridge's Codex assumptions may need generalization

### Option B: tcd Absorbs tmux-bridge Essentials (Lightweight Alternative)

Port the parts of tmux-bridge that are better than tcd's directly:
- 4-layer JSON extraction → `tcd/output_cleaner.py`
- Semantic depth constants → `tcd/tmux_adapter.py`
- UTF-8 byte-level chunking → `tcd/tmux_adapter.py`
- Structured CodexOutput → `tcd/providers/codex.py`

**Pros**: Stay single-package, no extra dependencies
**Cons**: Two projects continue to evolve independently; overlap persists

### Option C: Merge into One Project

Make tmux-bridge a foundation module of tcd (`tcd.bridge`), consolidating all functionality.

**Pros**: Completely eliminates overlap
**Cons**: tmux-bridge's independent users (Nanobot/OpenClaw) need to migrate

## 10. Conclusion

| Dimension | Assessment |
|-----------|-----------|
| **Feature coverage** | tcd is a superset of tmux-bridge (multi-provider + job management + SDK) |
| **Low-level quality** | tmux-bridge is more fine-grained (byte-level chunking, 4-layer JSON, structured output) |
| **Architectural extensibility** | tcd is better (pluggable providers, state persistence) |
| **Dependency purity** | tmux-bridge is better (zero dependencies) |
| **Maintenance efficiency** | Currently ~50–60% overlapping code; maintaining two projects long-term is uneconomical |
| **Recommended path** | **Option A** (tcd depends on tmux-bridge) or **Option B** (port essentials) |

**Core conclusion**: The relationship between the two projects is "low-level driver layer vs. high-level orchestration layer" — not competitors. tmux-bridge handles finer-grained tmux interaction; tcd handles more complete multi-AI management. The ideal state is layered reuse, avoiding double maintenance of homogeneous code.
