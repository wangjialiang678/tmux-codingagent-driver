# CCB vs Codex-Orchestrator Comparative Analysis

**Date**: 2026-03-02

## One-Line Summary

| Project | Positioning |
|---------|-------------|
| **CCB** (claude_code_bridge) | Multi-AI collaboration platform, 5 providers, daemon architecture, production-grade |
| **codex-orchestrator** | Single-AI driver, focused on Codex CLI, lightweight job system |

---

## Core Dimension Comparison

| Dimension | CCB (bfly123) | codex-orchestrator (kingbootoshi) |
|-----------|--------------|----------------------------------|
| **Language** | Python | TypeScript/Bun |
| **Code volume** | ~5000+ lines | ~1500 lines |
| **Supported AIs** | Claude, Codex, Gemini, OpenCode, Droid (5 types) | Codex CLI only |
| **Architecture pattern** | Daemon (TCP Server + Worker Pool) | CLI tool + Job JSON files |
| **Communication** | tmux/WezTerm send-keys + log reading | tmux send-keys + capture-pane + notify-hook |
| **Completion detection** | CCB_DONE:{req_id} custom marker protocol | notify-hook signal file + echo marker |
| **Concurrency model** | Per-session worker thread pool | Single job serial (multiple jobs each have independent tmux sessions) |
| **Log persistence** | Each AI's native session files | `script -q` recording + Codex JSONL |
| **Long prompt handling** | No special handling (relies on terminal inject) | Short: send-keys; long: load-buffer + paste-buffer |
| **Async notification** | completion_hook.py (supports email) | notify-hook.ts → signal file |
| **Anti-loop mechanism** | CLAUDE.md prompt-level guardrail | None (assumes Claude behavior is controllable) |
| **Cross-AI context** | ContextTransfer (8K token budget, deduplication) | None (single AI) |
| **Maturity** | v5.2.6, extensive edge-case fixes | Early-stage project, feature-complete but newer |

---

## Similarities

1. **Terminal as bus**: Both inject prompts via tmux send-keys rather than through an API
2. **Session persistence**: Both leverage AI CLI self-maintained sessions; no history resend → token-efficient
3. **Log readback**: Both read AI responses from terminal output/log files
4. **Job/Request abstraction**: Both have task state management (pending → running → completed/failed)
5. **Timeout fallback**: Both have idle timeout mechanisms (CCB: 60s daemon idle; orchestrator: 60min job inactive)

## Key Differences

### 1. Architectural Complexity

CCB is a **heavyweight daemon architecture**: TCP server + worker pool + idle monitor + parent process monitor. Suited for long-running multi-AI collaboration scenarios.

codex-orchestrator is a **lightweight CLI tool**: executes commands on each invocation, stores state in JSON files. Simple and direct; suited for single-AI task dispatch.

### 2. Completion Detection Strategy

CCB uses a **custom protocol marker** (`CCB_DONE:{req_id}`) injected into the prompt, requiring the AI to output the marker at the end of its reply. Advantage: universal (any AI can output a text marker). Disadvantage: the AI sometimes doesn't comply.

codex-orchestrator uses **Codex's native notify-hook**: Codex calls an external script when an agent turn ends. Advantage: reliable (native CLI support). Disadvantage: only Codex has this mechanism.

### 3. Multi-AI Support

CCB natively supports multiple providers with a unified abstraction layer. codex-orchestrator only supports Codex, but the architecture is clean and extensible.

---

## Reuse Value for tmux-codingagent-driver

### Directly Reusable

| Source | Technique | How to Reuse |
|--------|-----------|-------------|
| orchestrator | tmux long-prompt paste-buffer strategy | Port as-is |
| orchestrator | `script -q` log recording | Port as-is |
| orchestrator | `echo marker; read` prevent session exit | Port as-is |
| orchestrator | Sleep timing empirical values (1s init, 0.3s send) | Use as reference |
| CCB | CCB_DONE marker protocol (universal completion detection) | Adapt for Claude/Gemini |
| CCB | Per-session serialization | Prevent concurrent contamination |
| CCB | Async guardrail (prohibit polling loop) | Prompt-level constraint |
| CCB | Terminal backend abstraction layer | Multi-terminal support |

### Not Recommended for Direct Reuse

| Technique | Reason |
|-----------|--------|
| CCB full daemon suite | Too heavyweight; we don't need a TCP server |
| CCB Memory-First three-tier storage | Out of MVP scope |
| orchestrator's Codex-specific logic | We need multi-AI support |
