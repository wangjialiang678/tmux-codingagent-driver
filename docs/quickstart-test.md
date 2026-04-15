# tcd Quick-Start Test Guide

From installation to actual use inside Claude Code — up and running in 5 minutes.

---

## Step 0: Install

```bash
# Global install (recommended — makes the tcd command available in any terminal or Claude Code session)
uv tool install /Users/michael/projects/AI\ 工作流/tmux-codingagent-driver

# Verify
tcd --help
```

If you prefer not to install globally, you can use `uv run tcd` in place of `tcd` from the project directory.

---

## Step 1: Smoke Test (no AI, verify tmux pipeline)

```bash
# Start a bash session (bypasses all AI CLI)
tmux new-session -d -s tcd-smoke "echo 'TCD_SMOKE_OK'; sleep 30; read"

# Verify tmux can capture output
sleep 1
tmux capture-pane -t tcd-smoke -p | grep TCD_SMOKE_OK
# Should output a line containing TCD_SMOKE_OK

# Cleanup
tmux kill-session -t tcd-smoke
```

---

## Step 2: Codex Single-Task Test

Use tcd to launch a simple Codex task and verify the full pipeline.

```bash
# Create a temporary working directory
mkdir -p /tmp/tcd-test && cd /tmp/tcd-test

# Start a very simple task
tcd start -p codex -m "Create a hello.py in the current directory with the content: print('hello from codex')" -d /tmp/tcd-test

# Note the job_id in the output, e.g. a3f2b1c9

# Check status (can run multiple times)
tcd check <job_id>
echo $?  # 0=done 1=running

# Wait for completion (up to 120 seconds)
tcd wait <job_id> --timeout 120

# View results
tcd output <job_id>

# Verify the file was created
cat /tmp/tcd-test/hello.py

# Cleanup
tcd clean
```

### Expected Results

- `tcd start` outputs job_id and session name
- `tcd wait` returns normally (exit 0)
- `tcd output` shows Codex's reply
- `/tmp/tcd-test/hello.py` exists with the correct content

### If It Fails

```bash
# Method 1: enter the tmux session to see the actual state
tcd attach <job_id>
# Ctrl+B D to exit

# Method 2: view the script log
cat ~/.tcd/jobs/<job_id>.log

# Method 3: view job metadata
cat ~/.tcd/jobs/<job_id>.json
```

---

## Step 3: Claude Code Single-Task Test

```bash
mkdir -p /tmp/tcd-test-claude

tcd start -p claude -m "Create greet.py in the current directory with a function greet(name) that returns f'Hello, {name}!'" -d /tmp/tcd-test-claude

tcd wait <job_id> --timeout 180  # Claude Code starts slowly; allow enough time

tcd output <job_id>
cat /tmp/tcd-test-claude/greet.py

tcd clean
```

> Note: Claude Code may show a trust dialog on first launch; tcd handles it automatically. If the wait times out, use `tcd attach <job_id>` to check whether it is stuck on an interactive prompt.

---

## Step 4: Using tcd Inside a Claude Code Session

This is the target scenario — you are in a Claude Code conversation, and Claude Code dispatches tasks via bash tool calls to tcd.

### 4.1 Configure CLAUDE.md

Add the following to your project's CLAUDE.md:

```markdown
## tcd: AI Task Dispatcher

When you need to delegate independent coding subtasks to other AI agents, use tcd:

### Command Reference
- `tcd start -p <codex|claude|gemini> -m "<prompt>" -d <directory>` — start a task
- `tcd check <job_id>` — exit 0=done, 1=running
- `tcd wait <job_id> --timeout 300` — blocking wait
- `tcd output <job_id>` — retrieve results
- `tcd send <job_id> "<follow-up instruction>"` — multi-turn conversation
- `tcd jobs` — view all tasks
- `tcd kill <job_id>` — terminate a task

### Use Cases
- When you need to do two things in parallel: start a Codex subtask and continue the main task
- When code review is needed: have another AI review the current code
- When multiple perspectives are needed: have different AIs implement separately and compare results
```

### 4.2 Test Conversation

Start Claude Code, then say:

> "Use tcd to start a Codex task to create a Python calculator CLI in /tmp/tcd-demo that supports add, subtract, multiply, and divide. Wait for it to finish and show me the results."

Claude Code should:
1. `tcd start -p codex -m "..." -d /tmp/tcd-demo`
2. `tcd wait <job_id>` or poll with `tcd check`
3. `tcd output <job_id>`
4. Summarize the results for you

### 4.3 Parallel Test

> "I need a user management module. Use tcd to launch two tasks simultaneously: have Codex write the backend API (Express + TypeScript) in /tmp/tcd-parallel, and have Gemini write the frontend page (React). Wait for each to finish and summarize the results."

---

## Step 5: Multi-turn Conversation Test

```bash
# Start a task
tcd start -p codex -m "Set up an Express.js project skeleton" -d /tmp/tcd-multi

# Wait for the first turn to complete
tcd wait <job_id>
tcd output <job_id>

# Send a follow-up instruction
tcd send <job_id> "Add JWT authentication middleware"
tcd wait <job_id>
tcd output <job_id>

# Send another follow-up
tcd send <job_id> "Add a Dockerfile and docker-compose.yml"
tcd wait <job_id>
tcd output <job_id>

# View overall status
tcd status <job_id> --json
# turn_count should be 3
```

---

## FAQ

### Q: tcd start stays in "working" state and never completes

1. `tcd attach <job_id>` to inspect — may be stuck on an update prompt or trust dialog
2. Manually interact, then Ctrl+B D to exit; tcd will continue monitoring

### Q: tcd output produces empty output

1. The AI may still be running — confirm completion with `tcd check <job_id>` first
2. Check the raw log: `cat ~/.tcd/jobs/<job_id>.log`
3. Use `tcd output <job_id> --raw` to see uncleaned output

### Q: Gemini CLI always times out

Gemini frequently does not cooperate with the marker protocol (does not output TCD_DONE); tcd falls back to idle detection (declares completion after 15 seconds of no output). Set `--timeout` higher.

### Q: How to see which files Codex modified

```python
# Python approach
from tcd import TCD
from tcd.providers.codex import CodexProvider

tcd = TCD()
job = tcd.start("codex", "Refactor the code", cwd="/project")
tcd.wait(job.id)

provider = CodexProvider()
result = provider.parse_response_structured(job)
print(result.files_modified)  # ['src/main.py', 'src/utils.py']
print(result.tokens)          # {'input': 1234, 'output': 567}
```

---

## Test Checklist

| # | Test Item | Command | Expected |
|---|--------|------|------|
| 1 | Install | `tcd --help` | Help displayed |
| 2 | Codex single task | `tcd start -p codex ...` → `tcd wait` → `tcd output` | File created |
| 3 | Claude single task | `tcd start -p claude ...` → `tcd wait` → `tcd output` | File created |
| 4 | Use inside Claude Code | Ask Claude Code to call tcd in conversation | Completes automatically |
| 5 | Multi-turn conversation | `tcd send` follow-up instructions | turn_count increments |
| 6 | Parallel tasks | Simultaneously start 2+ jobs | `tcd jobs` shows all |
| 7 | Failure recovery | `tcd attach` to enter session | TUI visible |
| 8 | Cleanup | `tcd clean` | Completed jobs removed |
