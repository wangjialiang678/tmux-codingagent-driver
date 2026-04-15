# tcd Usage Scenarios

**Date**: 2026-03-02

This document describes specific usage scenarios for tmux-codingagent-driver (tcd). Each scenario includes prerequisites, complete command steps, and expected results.

---

## Scenario 1: Claude Code Delegates Backend Work to Codex

**Summary**: Claude Code, working on a full-stack project, hands off the backend API implementation to Codex.

### Prerequisites

- Claude Code is running inside a Next.js full-stack project
- The project CLAUDE.md has tcd usage instructions configured
- Codex CLI is installed and authenticated

### Workflow

Claude Code automatically invokes tcd via the bash tool during execution:

```bash
# 1. Claude Code decides to delegate the backend API to Codex
tcd start -p codex -m "Implement the user registration API under src/api/:
- POST /api/auth/register
- Parameters: email, password, name
- Use Prisma ORM for database operations
- Add Zod validation
- Write unit tests" -d /Users/michael/projects/myapp

# Output:
# Job started: a3f2b1c9
# Provider: codex
# tmux session: tcd-codex-a3f2b1c9

# 2. Claude Code continues working on frontend while polling Codex progress
tcd check a3f2b1c9
# exit 1 → still running

# ... Claude Code does some frontend work ...

tcd check a3f2b1c9
# exit 0 → complete

# 3. Get Codex's result
tcd output a3f2b1c9
# Output:
# I've implemented the user registration API:
# - Created src/api/auth/register.ts with POST handler
# - Added Zod validation schema
# - Created Prisma migration for users table
# - Added 5 unit tests in __tests__/register.test.ts
# All tests passing.

# 4. Claude Code decides next steps based on the result
tcd status a3f2b1c9 --json
# {"id":"a3f2b1c9","provider":"codex","status":"completed","turn_count":1,...}
```

### Expected Results

- Codex completes the backend implementation in an isolated tmux session
- Claude Code continues frontend work during the wait
- Total time ≈ max(frontend time, backend time), not their sum

### Features Involved

- FR-1 (Provider: Codex)
- FR-3 (Job management)
- FR-4 (Completion detection: notify-hook)
- FR-6 (CLI: start / check / output / status)
- FR-8 (CLAUDE.md integration)

---

## Scenario 2: Claude Code Drives Codex for Code Review

**Summary**: Claude Code delegates the entire project code review to Codex while continuing other work.

### Prerequisites

- Project code is committed to git
- Claude Code is working on new feature development

### Workflow

```bash
# 1. Claude Code starts Codex for code review
tcd start -p codex -m "Review the code quality of the entire src/ directory:
1. Check for security vulnerabilities (SQL injection, XSS, hardcoded secrets, etc.)
2. Check whether error handling is adequate
3. Check for performance issues (N+1 queries, memory leaks, etc.)
4. Check whether test coverage is sufficient
5. Output a markdown review report saved to docs/code-review.md" \
  -d /Users/michael/projects/myapp

# Output:
# Job started: b7e4c2d1
# Provider: codex

# 2. Claude Code continues its development tasks and checks occasionally
tcd check b7e4c2d1
# exit 1 → still reviewing

# 3. Wait for review to complete
tcd wait b7e4c2d1 --timeout 600

# 4. View review results
tcd output b7e4c2d1
# Outputs review report summary...

# 5. Claude Code fixes issues based on the review results
```

### Expected Results

- Codex thoroughly reviews the code and generates `docs/code-review.md`
- Claude Code completes other development tasks in parallel during the wait
- The review report contains specific file locations and fix suggestions

### Features Involved

- FR-1 (Provider: Codex)
- FR-6 (CLI: start / check / wait / output)
- FR-5 (Response collection)

---

## Scenario 3: Parallel Dispatch — Frontend to Gemini, Backend to Codex, Docs to Claude Code

**Summary**: The main agent simultaneously starts three AIs for different modules: frontend to Gemini CLI, backend to Codex CLI, documentation to Claude Code CLI.

### Prerequisites

- All three AI CLIs are installed (codex / claude / gemini)
- Project structure is clear with separated frontend, backend, and documentation directories

### Workflow

```bash
# 1. Start three jobs simultaneously (can be called in parallel by an upstream agent or batched by a shell script)

# Backend → Codex (strong at code implementation)
tcd start -p codex -m "Implement REST API:
- GET/POST/PUT/DELETE /api/products
- Prisma schema + migration
- Complete CRUD handlers
- Error handling middleware
- Unit tests" -d /Users/michael/projects/shop
# Job started: 001-codex

# Frontend → Gemini CLI (strong at UI and frontend)
tcd start -p gemini -m "Implement the product listing page using React + TailwindCSS:
- Product card component (image, name, price, add-to-cart button)
- Search and filter functionality
- Responsive layout (mobile-first)
- Pagination component
- Loading skeleton screens" -d /Users/michael/projects/shop
# Job started: 002-gemini

# Documentation → Claude Code CLI (strong at analysis and writing)
tcd start -p claude -m "Write technical documentation for this e-commerce project:
- README.md (project overview, quick start, architecture description)
- API documentation (generate OpenAPI spec from code in src/api/)
- Deployment documentation (Docker + Vercel)" -d /Users/michael/projects/shop
# Job started: 003-claude

# 2. Monitor all task progress
tcd jobs --json
# [
#   {"id":"001-codex", "provider":"codex", "status":"running", "turn_state":"working"},
#   {"id":"002-gemini", "provider":"gemini", "status":"running", "turn_state":"working"},
#   {"id":"003-claude", "provider":"claude", "status":"running", "turn_state":"working"}
# ]

# 3. Check completion status one by one
tcd check 001-codex   # exit 0 → Codex done
tcd check 002-gemini  # exit 1 → Gemini still working
tcd check 003-claude  # exit 0 → Claude done

# 4. Collect Codex and Claude results first
tcd output 001-codex
tcd output 003-claude

# 5. Wait for Gemini to complete
tcd wait 002-gemini --timeout 300
tcd output 002-gemini

# 6. Clean up after all are done
tcd clean
```

### Expected Results

- Three AIs work in parallel in their own independent tmux sessions
- Backend API, frontend UI, and technical documentation produced simultaneously
- Total time ≈ the slowest AI's time, not the sum of all three
- `tcd jobs` shows real-time status of all jobs

### Features Involved

- FR-1 (Provider: Codex + Claude + Gemini)
- FR-2 (tmux Adapter: multiple parallel sessions)
- FR-3 (Job management: multiple jobs)
- FR-6 (CLI: start / jobs / check / wait / output / clean)

---

## Scenario 4: OpenClaw Drives Codex for a Small Task

**Summary**: An OpenClaw agent uses the Python SDK to call tcd, having Codex complete an isolated small programming task.

### Prerequisites

- tcd Python package is installed in the OpenClaw agent environment
- Codex CLI is installed

### Workflow

```python
# OpenClaw Agent Python code
from tcd import TCD
import time

driver = TCD()

# 1. Start Codex to write a CLI tool
job = driver.start(
    provider="codex",
    prompt="""Write a Python CLI tool csv2json:
    - Read a CSV file and output JSON
    - Support --pretty parameter for pretty-printing
    - Support --filter 'column=value' for row filtering
    - Use the click framework
    - Include pyproject.toml and basic tests""",
    cwd="/tmp/csv2json"
)

print(f"Started job: {job.id}")

# 2. Poll for completion
while True:
    result = driver.check(job.id)
    if result.state == "idle":
        break
    if result.state == "context_limit":
        print("Warning: context limit reached")
        break
    time.sleep(3)

# 3. Get the result
output = driver.output(job.id)
print(f"Codex output:\n{output}")

# 4. Clean up
driver.clean()
```

### Expected Results

- OpenClaw seamlessly invokes tcd via the Python SDK
- Codex completes the CLI tool development in a background tmux session
- OpenClaw receives the complete implementation report

### Features Involved

- FR-7 (Python SDK)
- FR-1 (Provider: Codex)
- FR-4 (Completion detection: notify-hook)

---

## Scenario 5: Pipeline — Codex Writes Code → Claude Code Reviews → Gemini Writes Tests

**Summary**: Three AIs work in serial relay, forming a "code → review → test" pipeline.

### Prerequisites

- All three AI CLIs are installed

### Workflow

```bash
#!/bin/bash
# pipeline.sh — AI programming pipeline

PROJECT_DIR="/Users/michael/projects/mylib"

# ===== Phase 1: Codex implements =====
echo "=== Phase 1: Codex implementing ==="
IMPL_JOB=$(tcd start -p codex -m "Implement a TypeScript date utility library:
- formatDate(date, pattern) — format a date
- parseDate(str, pattern) — parse a date string
- diffDays(date1, date2) — calculate day difference
- addDays(date, n) — add days
Place it in src/date-utils.ts" -d "$PROJECT_DIR" | grep "Job started:" | awk '{print $NF}')

echo "Implementation job: $IMPL_JOB"
tcd wait "$IMPL_JOB" --timeout 300
IMPL_EXIT=$?

if [ $IMPL_EXIT -ne 0 ]; then
    echo "Implementation failed!"
    tcd output "$IMPL_JOB"
    exit 1
fi

echo "Implementation done."
tcd output "$IMPL_JOB"

# ===== Phase 2: Claude Code reviews =====
echo "=== Phase 2: Claude reviewing ==="
REVIEW_JOB=$(tcd start -p claude -m "Review the code in src/date-utils.ts:
1. Are there unhandled edge cases (leap years, timezones, invalid input)?
2. Are there performance issues?
3. Is the API design reasonable?
4. If there are issues, fix them directly in the code
5. Output a review summary" -d "$PROJECT_DIR" | grep "Job started:" | awk '{print $NF}')

echo "Review job: $REVIEW_JOB"
tcd wait "$REVIEW_JOB" --timeout 300

echo "Review done."
tcd output "$REVIEW_JOB"

# ===== Phase 3: Gemini writes tests =====
echo "=== Phase 3: Gemini writing tests ==="
TEST_JOB=$(tcd start -p gemini -m "Write comprehensive unit tests for src/date-utils.ts:
- Use the vitest framework
- Cover all exported functions
- Include normal cases + edge cases + error handling
- Place test file at tests/date-utils.test.ts
- Ensure vitest can run them directly" -d "$PROJECT_DIR" | grep "Job started:" | awk '{print $NF}')

echo "Test job: $TEST_JOB"
tcd wait "$TEST_JOB" --timeout 300

echo "Tests done."
tcd output "$TEST_JOB"

echo "=== Pipeline complete ==="
tcd jobs
```

### Expected Results

- Three phases execute serially; each AI can see the previous phase's output (in the same project directory)
- Code goes through the complete write → review → test pipeline
- Final project has implementation code + review report + complete tests

### Features Involved

- FR-1 (three providers used serially)
- FR-6 (CLI: start / wait / output / jobs)

---

## Scenario 6: Shell Script Batch Dispatch

**Summary**: Use a shell script to batch-submit multiple independent small tasks to Codex.

### Prerequisites

- A set of independent small tasks to execute

### Workflow

```bash
#!/bin/bash
# batch.sh — batch task submission

TASKS=(
    "Write a Python fibonacci function with memoization optimization and tests"
    "Write a Go HTTP health check endpoint returning JSON"
    "Write a Rust CLI argument parser using clap"
)

JOB_IDS=()

# 1. Batch submit
for task in "${TASKS[@]}"; do
    JOB_ID=$(tcd start -p codex -m "$task" -d /tmp/batch-tasks | grep "Job started:" | awk '{print $NF}')
    JOB_IDS+=("$JOB_ID")
    echo "Started: $JOB_ID — $task"
done

# 2. Wait for all to complete
echo "Waiting for all jobs to complete..."
ALL_DONE=false
while [ "$ALL_DONE" = false ]; do
    ALL_DONE=true
    for job_id in "${JOB_IDS[@]}"; do
        tcd check "$job_id" 2>/dev/null
        if [ $? -eq 1 ]; then
            ALL_DONE=false
        fi
    done
    [ "$ALL_DONE" = false ] && sleep 5
done

# 3. Collect all results
echo "=== Results ==="
for job_id in "${JOB_IDS[@]}"; do
    echo "--- Job: $job_id ---"
    tcd output "$job_id"
    echo ""
done

# 4. Clean up
tcd clean
```

### Expected Results

- 3 Codex instances run in parallel in 3 tmux sessions
- Results collected all at once after all complete
- Total time ≈ the slowest task's time

### Features Involved

- FR-2 (tmux Adapter: multiple sessions)
- FR-6 (CLI: start / check / output / clean)

---

## Scenario 7: Multi-Turn Conversation — Appending Instructions

**Summary**: Send follow-up instructions to a running AI for multi-turn interaction.

### Prerequisites

- An existing running job

### Workflow

```bash
# 1. Start a Codex task
tcd start -p codex -m "Set up an Express.js project skeleton with TypeScript configuration" \
  -d /Users/michael/projects/newapp
# Job started: c1d2e3f4

# 2. Wait for the first turn to complete
tcd wait c1d2e3f4

# 3. After viewing the result, append instructions
tcd send c1d2e3f4 "Good. Now add the following:
1. JWT authentication middleware
2. Request logging middleware (using morgan)
3. Error handling middleware
4. CORS configuration"

# 4. Wait for the second turn to complete
tcd check c1d2e3f4     # exit 1 → running
# ... a few seconds later ...
tcd check c1d2e3f4     # exit 0 → done

# 5. View this turn's result
tcd output c1d2e3f4

# 6. Append another turn
tcd send c1d2e3f4 "Add Docker support: Dockerfile + docker-compose.yml, including PostgreSQL"

tcd wait c1d2e3f4
tcd output c1d2e3f4

# 7. View overall status
tcd status c1d2e3f4 --json
# {"id":"c1d2e3f4", "turn_count":3, "turn_state":"idle", ...}
```

### Expected Results

- Codex maintains the same session; context accumulates automatically
- Each appended instruction only sends new content, not the full history (token-efficient)
- `turn_count` increments correctly
- Output from each turn is retrievable

### Features Involved

- FR-6 (CLI: start / wait / send / check / output / status)
- FR-4 (Completion detection: multi-turn)

---

## Scenario 8: Python SDK Programmatic Integration

**Summary**: Use the tcd SDK in a Python automation script to orchestrate multiple AIs.

### Prerequisites

- `pip install tcd` or `uv add tcd`

### Workflow

```python
from tcd import TCD
import concurrent.futures
import time

driver = TCD()

def run_ai_task(provider: str, prompt: str, cwd: str) -> dict:
    """Run an AI task and return the result"""
    job = driver.start(provider=provider, prompt=prompt, cwd=cwd)
    driver.wait(job.id, timeout=300)
    output = driver.output(job.id)
    status = driver.status(job.id)
    return {
        "job_id": job.id,
        "provider": provider,
        "output": output,
        "turn_count": status.turn_count,
    }

# Submit three tasks in parallel
tasks = [
    ("codex", "implement CRUD for user service", "/projects/app"),
    ("gemini", "implement the user profile page component", "/projects/app"),
    ("claude", "write API integration tests", "/projects/app"),
]

with concurrent.futures.ThreadPoolExecutor(max_workers=3) as executor:
    futures = {
        executor.submit(run_ai_task, provider, prompt, cwd): provider
        for provider, prompt, cwd in tasks
    }

    for future in concurrent.futures.as_completed(futures):
        provider = futures[future]
        result = future.result()
        print(f"[{provider}] Done — {len(result['output'])} chars output")

# Clean up
driver.clean()
```

### Expected Results

- Python concurrency model (ThreadPoolExecutor) works correctly with tcd
- Three AI tasks run truly in parallel
- Results returned as dicts for downstream processing

### Features Involved

- FR-7 (Python SDK)
- FR-1 (three providers)
- FR-4 (Completion detection)
- FR-5 (Response collection)

---

## Scenario 9: Failure Recovery and Retry

**Summary**: Recovery strategies after a task times out or the AI crashes.

### Prerequisites

- A job that has failed due to timeout or AI abnormality

### Workflow

```bash
# 1. Discover a failed job
tcd jobs
# ID         PROVIDER  STATUS    AGE     TURN
# d4e5f6a7   codex     failed    15m     1

# 2. View failure reason
tcd status d4e5f6a7 --json
# {"id":"d4e5f6a7", "status":"failed", "error":"timeout after 60 minutes", ...}

# 3. View partial output completed before failure
tcd output d4e5f6a7
# Outputs partial content up to the timeout...

# 4. Try debugging: if the tmux session is still alive
tcd attach d4e5f6a7
# (enter tmux session to see actual state, Ctrl+B D to detach)

# 5. Option A: restart a new job with a longer timeout
tcd start -p codex -m "Continue the previous incomplete task: [paste previous prompt]" \
  -d /Users/michael/projects/myapp --timeout 120

# 6. Option B: if the session is still alive, send a follow-up instruction
tcd send d4e5f6a7 "Please continue the previous task"

# 7. Clean up failed job
tcd kill d4e5f6a7
tcd clean
```

### Expected Results

- Failed job retains logs and partial output; no data loss
- Can enter the tmux session directly via `attach` for debugging
- Can choose to restart or append instructions to recover

### Features Involved

- FR-3 (Job management: failed state)
- FR-6 (CLI: jobs / status / output / attach / send / kill / clean)
- NFR-2 (Reliability: log persistence)

---

## Scenario Summary Matrix

| # | Scenario | Upstream Caller | Provider | Mode | Core Features |
|---|----------|----------------|----------|------|---------------|
| 1 | Backend delegation | Claude Code | Codex | Single task async | start/check/output |
| 2 | Code review | Claude Code | Codex | Single task blocking | start/wait/output |
| 3 | Parallel dispatch | Agent/Script | Codex+Gemini+Claude | Parallel multi-task | start×3/jobs/check/wait |
| 4 | Small task | OpenClaw | Codex | SDK single task | Python SDK |
| 5 | Pipeline | Shell Script | Codex→Claude→Gemini | Serial chain | start/wait/output serial |
| 6 | Batch dispatch | Shell Script | Codex×3 | Parallel batch | start×N/check loop |
| 7 | Multi-turn | Manual/Agent | Codex | Multi-turn | send/check/output |
| 8 | SDK programmatic | Python Script | Codex+Gemini+Claude | Parallel SDK | ThreadPoolExecutor |
| 9 | Failure recovery | Manual | Any | Debug recovery | attach/output/kill |
