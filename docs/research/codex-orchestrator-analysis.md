# Research Report: kingbootoshi/codex-orchestrator — Source Architecture Analysis

**Date**: 2026-03-02
**Source**: https://github.com/kingbootoshi/codex-orchestrator
**Objective**: Deep-dive into how codex-orchestrator drives the Codex CLI via tmux, parses output, distributes tasks, and collects results

---

## Research Summary

codex-orchestrator is a TypeScript/Bun project that launches the Codex CLI (TUI) in interactive mode via a tmux session, injects prompts via `send-keys`, persists logs via the `script` command, detects agent turn completion via a notify-hook mechanism, and polls status via `capture-pane`. The overall architecture is clean — approximately 6 core modules and ~1,500 lines of code — and is a mature, reference-worthy implementation.

---

## Project Overview

| Attribute | Value |
|-----------|-------|
| Runtime | Bun (TypeScript, runs directly without compilation) |
| Dependencies | tmux, Codex CLI (`codex`), Bun, OpenAI API Key |
| Single production dependency | `glob@^10` |
| Default model | `gpt-5.3-codex` (fast: `gpt-5.3-codex-spark`) |
| Default reasoning | `xhigh` |
| Job storage | `~/.codex-agent/jobs/` |
| tmux prefix | `codex-agent-{jobId}` |

---

## File Structure

```
src/
  config.ts          - Global config (model, paths, timeouts)
  tmux.ts            - tmux operation primitives (create/send/capture/kill)
  jobs.ts            - Job lifecycle management
  watcher.ts         - turn-complete signal file mechanism
  notify-hook.ts     - Codex notify callback script (triggered when agent turn ends)
  session-parser.ts  - Parses JSONL/JSON session files under ~/.codex/sessions/
  output-cleaner.ts  - Cleans ANSI and TUI noise
  cli.ts             - CLI entry point (start/status/send/capture/jobs/watch/attach)
bin/
  codex-agent        - Main executable
  codex-bg           - Bash wrapper, runs in background + polls for completion
plugins/
  codex-orchestrator/skills/codex-orchestrator/SKILL.md  - Claude skill description
```

---

## Core Mechanism 1: Driving Codex CLI via tmux

### Session Creation (`tmux.ts: createSession`)

**Full flow**:

```typescript
// 1. Write long prompts to a file to avoid shell escaping issues
fs.writeFileSync(promptFile, options.prompt);

// 2. Build the codex command line; key arguments:
//    -a never     : auto-approve (no manual confirmation for file operations)
//    -s sandbox   : sandbox mode (read-only / workspace-write / danger-full-access)
//    notify hook  : notification script, triggered when agent completes each turn
const codexArgs = [
  `-c`, `model="${options.model}"`,
  `-c`, `model_reasoning_effort="${options.reasoningEffort}"`,
  `-c`, `skip_update_check=true`,
  `-c`, `'notify=["bun","run","${notifyHook}","${options.jobId}"]'`,
  `-a`, `never`,
  `-s`, options.sandbox,
].join(" ");

// 3. Use the script command to record all terminal output to a .log file
//    After the session ends, prints "[codex-agent: Session complete...]"; read prevents the session from immediately exiting
const shellCmd = `script -q "${logFile}" codex ${codexArgs}; echo "\\n\\n[codex-agent: Session complete. Press Enter to close.]"; read`;

// 4. Create a detached tmux session
execSync(`tmux new-session -d -s "${sessionName}" -c "${options.cwd}" '${shellCmd}'`);

// 5. Wait for Codex TUI to initialize (1 second)
spawnSync("sleep", ["1"]);

// 6. Skip the update prompt (send "3" + Enter)
execSync(`tmux send-keys -t "${sessionName}" "3"`);
spawnSync("sleep", ["0.5"]);
execSync(`tmux send-keys -t "${sessionName}" Enter`);
spawnSync("sleep", ["1"]);

// 7. Inject the prompt
if (options.prompt.length < 5000) {
  // Short prompt: send-keys directly
  execSync(`tmux send-keys -t "${sessionName}" '${escapedPrompt}'`);
  spawnSync("sleep", ["0.3"]);  // Wait for TUI to process text
  execSync(`tmux send-keys -t "${sessionName}" Enter`);
} else {
  // Long prompt (>=5000 chars): tmux buffer paste
  execSync(`tmux load-buffer "${promptFile}"`);
  execSync(`tmux paste-buffer -t "${sessionName}"`);
  spawnSync("sleep", ["0.3"]);
  execSync(`tmux send-keys -t "${sessionName}" Enter`);
}
```

**Key design decisions**:
- Uses `script -q` to record logs; logs are preserved even after the tmux session is killed
- Uses `read` to keep the shell waiting, preventing the session from immediately closing after codex exits (allows capture-pane to read final output)
- Short prompts use `send-keys`; long prompts (>=5000 chars) use `load-buffer` + `paste-buffer`, resolving command-line length limits
- Single-quote escaping: `message.replace(/'/g, "'\\''")`

### Sending Messages to a Running Session (`sendMessage`)

```typescript
const escapedMessage = message.replace(/'/g, "'\\''");
execSync(`tmux send-keys -t "${sessionName}" '${escapedMessage}'`);
spawnSync("sleep", ["0.3"]);  // Wait for TUI to process
execSync(`tmux send-keys -t "${sessionName}" Enter`);
```

---

## Core Mechanism 2: Parsing Codex TUI Output

### Strategy 1: `capture-pane` (Real-Time Status Detection)

```typescript
// Read the last N lines (suitable for status detection)
execSync(`tmux capture-pane -t "${sessionName}" -p`);

// Read the full scrollback (-S - means start from history)
execSync(`tmux capture-pane -t "${sessionName}" -p -S -`, { maxBuffer: 50 * 1024 * 1024 });
```

**Completion detection**: Checks whether the capture-pane output contains the string `"[codex-agent: Session complete"` (echoed by the shell after codex exits).

### Strategy 2: `script` Log File (Persistent Fallback)

- Path: `~/.codex-agent/jobs/{jobId}.log`
- Prefer tmux capture; fall back to reading the log file when the session no longer exists
- Can be used to extract the session ID (`extractSessionId(logContent)`)

### Strategy 3: Codex Native Session Files (Structured Data)

Codex stores JSONL/JSON session files under `~/.codex/sessions/`, containing token usage, modified file lists, summaries, etc.

```typescript
// session-parser.ts parsing flow:
// 1. Extract session ID from the .log file (regex match "session id: xxx")
const sessionId = extractSessionId(logContent);  // regex /session id:\s*([0-9a-f-]{8,})/i

// 2. Locate the corresponding .jsonl or .json file in the ~/.codex/sessions/ directory tree
const sessionFile = findSessionFile(sessionId);

// 3. Parse for structured data
const data = parseSessionFile(sessionFile);
// data = { tokens: {input, output, context_window, context_used_pct}, files_modified: [...], summary: "..." }
```

Key logic for JSONL format parsing:
- `event_msg + token_count` → parse token usage
- `event_msg + agent_message` → extract summary
- `response_item + apply_patch tool call` → extract modified file paths

### ANSI Cleaning (`output-cleaner.ts`)

Extensive regex processing of TUI terminal output noise, including:
- ANSI CSI/OSC/DCS/ESC sequence removal
- Codex Chrome TUI-specific noise lines (`esc to interrupt`, `% context left`, `background terminal running`, etc.)
- Duplicate line deduplication
- URL redraw artifact cleanup
- "Typing artifact" detection (short repeated word sequence heuristic)
- Rearranges output into clean text

---

## Core Mechanism 3: notify-hook (Turn Completion Detection)

**The most elegant part.** Codex supports a `notify` config option that executes a specified command at the end of each agent turn, passing in a JSON payload.

### Configuration

```typescript
// Configured on the codex command line
`-c 'notify=["bun","run","${notifyHook}","${options.jobId}"]'`
```

### notify-hook.ts Handler

```typescript
// Receives Codex's agent-turn-complete event
function main(): void {
  const jobId = process.argv[2];
  const rawPayload = process.argv[3];   // JSON payload from Codex

  const payload = parsePayload(rawPayload);
  if (payload.type !== "agent-turn-complete") return;

  const event: TurnEvent = {
    turnId: payload["turn-id"],
    lastAgentMessage: payload["last-assistant-message"],
    timestamp: new Date().toISOString(),
  };

  // Write signal file ~/.codex-agent/jobs/{jobId}.turn-complete
  writeSignalFile(jobId, event);
  // Update job.json: turnCount, lastTurnCompletedAt, lastAgentMessage, turnState="idle"
  updateJobTurn(jobId, event);
}
```

### Signal File Mechanism (`watcher.ts`)

```typescript
// Signal file path
const signalPath = `~/.codex-agent/jobs/${jobId}.turn-complete`;

// Write signal
writeSignalFile(jobId, event);     // Create .turn-complete file

// Check if idle (Claude polls this file)
signalFileExists(jobId);           // Check whether the file exists

// Read turn event details
readSignalFile(jobId);             // Read JSON

// Clear signal (cleared when sending a new message)
clearSignalFile(jobId);            // Delete the file
```

**Key advantage**: Claude does not need to poll the tmux pane; it simply checks whether the signal file exists — low CPU usage, high reliability.

---

## Core Mechanism 4: Task Distribution and Result Collection Architecture

### Job Data Structure

```typescript
interface Job {
  id: string;                          // 4-byte random hex (e.g., "a3f2b1c9")
  status: "pending" | "running" | "completed" | "failed";
  prompt: string;
  model: string;
  reasoningEffort: "low" | "medium" | "high" | "xhigh";
  sandbox: "read-only" | "workspace-write" | "danger-full-access";
  parentSessionId?: string;            // Multi-level agent tracking
  cwd: string;
  createdAt: string;                   // ISO timestamp
  startedAt?: string;
  completedAt?: string;
  tmuxSession?: string;                // "codex-agent-{jobId}"
  result?: string;                     // Full output
  error?: string;
  // Turn state tracking
  turnCount?: number;
  lastTurnCompletedAt?: string;
  lastAgentMessage?: string;           // Truncated to 500 characters
  turnState?: "working" | "idle" | "context_limit";
}
```

All jobs are stored as JSON files at `~/.codex-agent/jobs/{jobId}.json`.

### Task Lifecycle

```
startJob()
  → generate jobId (randomBytes(4).hex)
  → save job.json (status: "pending")
  → createSession() launches tmux
  → update job.json (status: "running", tmuxSession)

[Codex executing...]
  → notify-hook fires → write .turn-complete signal file
  → external poller checks signalFileExists() to detect turn end

refreshJobStatus(jobId)  [called by poller]
  → sessionExists()? → no → status: "completed"
  → capturePane(-20 lines) contains "[codex-agent: Session complete"? → status: "completed"
  → isInactiveTimedOut()? (60 minutes no activity) → killSession() → status: "failed"

getJobsJson()  [structured output]
  → for each completed job, call loadSessionData()
  → parse ~/.codex/sessions/*.jsonl for tokens + files_modified + summary
```

### Timeout Mechanism

- Uses the `.log` file's `mtime` as the last activity timestamp (log file is updated in real time as Codex outputs)
- If `Date.now() - lastActivityMs > 60 minutes`, kills session, marks as failed
- Falls back to `job.startedAt` when the log file doesn't exist

### Result Collection Priority

```
getJobOutput() / getJobFullOutput():
1. Preferred: tmux capture-pane (while session still exists)
2. Fallback: read ~/.codex-agent/jobs/{jobId}.log

getJobsJson() (structured data):
1. Extract session ID from .log
2. Locate JSONL file in ~/.codex/sessions/
3. Parse for tokens + files_modified + summary
```

---

## Core Mechanism 5: Error Handling and Retry

### Error Handling Strategy

1. **tmux operations wrapped in try/catch**: `createSession`, `sendMessage`, `capturePane`, etc. return `false`/`null` on failure rather than throwing exceptions
2. **Session existence check up front**: All operations first call `sessionExists()` to verify
3. **Timeout fallback**: Automatically killed and marked failed after 60 minutes of inactivity
4. **Auxiliary file cleanup**: `deleteJob()` also cleans up `.prompt`, `.log`, `.turn-complete` files

### Context Exhaustion Detection

The CLI's `await` command detects `turnState === "context_limit"` status and exits with code 2, allowing the upstream orchestrator to distinguish normal completion (0) from context exhaustion (2).

### Retry Mechanism

**No native retry**. The design philosophy is "don't kill the agent and restart from scratch — use the `send` command to issue follow-up instructions." Redirect a running agent via `sendToJob()` / `codex-agent send {jobId} "message"` rather than restarting.

---

## `codex-bg` Background Wrapper (Bash)

```bash
# codex-bg provides functionality that cli.ts doesn't directly offer:
# 1. Extract job ID
JOB_ID=$(codex-agent start ... | grep "Job ID:" | awk '{print $NF}')

# 2. Poll turn completion signal (check .turn-complete file)
if [ -f "$JOBS_DIR/$JOB_ID.turn-complete" ]; then
  export CODEX_AGENT_TURN_COMPLETE=1
fi

# 3. Poll job status until complete
while true; do
  STATUS=$(codex-agent status $JOB_ID --json | jq -r .status)
  if [ "$STATUS" = "completed" ] || [ "$STATUS" = "failed" ]; then
    export CODEX_AGENT_DONE=$STATUS
    break
  fi
  sleep $POLL_INTERVAL
done

# 4. Optionally trigger a callback command after completion
if [ -n "$NOTIFY_CMD" ]; then eval "$NOTIFY_CMD"; fi
```

---

## Applicability to Our Project

### Directly Portable Patterns

| Pattern | Original Implementation | We can... |
|---------|------------------------|-----------|
| tmux drives Codex | `createSession()` in `tmux.ts` | Translate directly to Python subprocess |
| Long prompt paste-buffer | `tmux load-buffer` + `paste-buffer` | Equally applicable; 5000-char threshold is reasonable |
| notify-hook signal file | `watcher.ts` + `notify-hook.ts` | Port directly; no language barrier |
| Session completion marker | `echo "[codex-agent: Session complete...]"` | Custom marker strategy |
| Timeout detection via log mtime | `getLastActivityMs()` | Python `os.stat().st_mtime` |
| Output fallback strategy | capture-pane → log file | Must implement two-level fallback |
| JSONL session parsing | `session-parser.ts` | Parse `~/.codex/sessions/` for tokens |

### Implementation Details Worth Noting

1. **Sleep timing**: Sleep 1s after creating a session (wait for TUI initialization), sleep 0.3s after send-keys (wait for TUI to process). These delays are empirical values; equivalent handling is needed in Python.

2. **Single-quote escaping**: `message.replace(/'/g, "'\\''")` — in Python: `message.replace("'", "'\\''")` or use shlex.

3. **`script` command differences**: macOS's `script` argument order differs from Linux (`script -q file cmd` vs `script -q -c cmd file`). Platform detection is required.

4. **`-a never` argument**: Critical! Makes Codex auto-approve all file operations; otherwise the TUI waits for manual confirmation and hangs.

5. **notify hook format**: Codex's notify config is an array format `["cmd", "arg1", "arg2"]`; payload is passed via stdin or argv (this project uses argv[3]).

6. **`read` prevents session exit**: `codex ...; echo "...complete..."; read` — this pattern keeps the session alive after codex exits, ensuring capture-pane can read the final output.

---

## Architecture Diagram

```
Claude Code (orchestrator)
    │
    │ startJob(prompt) → JobId
    ▼
jobs.ts: startJob()
    │
    │ createSession()
    ▼
tmux.ts
    ├── new-session -d -s "codex-agent-{id}" → script -q {id}.log codex -c ... -a never
    ├── send-keys "3" + Enter (skip update)
    ├── send-keys {prompt} + Enter
    └── [tmux session running]
           │
           │ [Codex TUI running]
           │
           ├── notify-hook.ts (agent turn ends)
           │     └── write {id}.turn-complete signal file
           │
           └── echo "[codex-agent: Session complete...]" + read
                 │
                 └── jobs.ts: refreshJobStatus() detects complete marker

Claude Code polling:
    ├── signalFileExists({id}) → turn complete → read summary → send follow-up instruction
    ├── capturePane(-20 lines) → detect complete marker
    └── getJobsJson() → parse ~/.codex/sessions/*.jsonl → tokens + files + summary
```

---

## References

- [kingbootoshi/codex-orchestrator (GitHub)](https://github.com/kingbootoshi/codex-orchestrator)
- [Source: tmux.ts](https://raw.githubusercontent.com/kingbootoshi/codex-orchestrator/main/src/tmux.ts)
- [Source: jobs.ts](https://raw.githubusercontent.com/kingbootoshi/codex-orchestrator/main/src/jobs.ts)
- [Source: notify-hook.ts](https://raw.githubusercontent.com/kingbootoshi/codex-orchestrator/main/src/notify-hook.ts)
- [Source: watcher.ts](https://raw.githubusercontent.com/kingbootoshi/codex-orchestrator/main/src/watcher.ts)
- [Source: session-parser.ts](https://raw.githubusercontent.com/kingbootoshi/codex-orchestrator/main/src/session-parser.ts)
- [Source: output-cleaner.ts](https://raw.githubusercontent.com/kingbootoshi/codex-orchestrator/main/src/output-cleaner.ts)
- [Source: config.ts](https://raw.githubusercontent.com/kingbootoshi/codex-orchestrator/main/src/config.ts)
