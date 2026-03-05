# tcd 使用场景文档

**日期**: 2026-03-02

本文档描述 tmux-codingagent-driver (tcd) 的具体使用场景。每个场景包含前置条件、完整命令步骤和预期结果。

---

## 场景 1: Claude Code 把后端任务委派给 Codex

**一句话**: Claude Code 在做全栈项目时，把后端 API 实现交给 Codex 执行。

### 前置条件

- Claude Code 正在一个 Next.js 全栈项目中运行
- 项目 CLAUDE.md 中已配置 tcd 使用说明
- Codex CLI 已安装并登录

### 操作流程

Claude Code 在执行过程中，通过 bash 工具自动调用 tcd：

```bash
# 1. Claude Code 决定把后端 API 交给 Codex
tcd start -p codex -m "在 src/api/ 目录下实现用户注册 API：
- POST /api/auth/register
- 参数：email, password, name
- 使用 Prisma ORM 操作数据库
- 添加 zod 验证
- 写单元测试" -d /Users/michael/projects/myapp

# 输出：
# Job started: a3f2b1c9
# Provider: codex
# tmux session: tcd-codex-a3f2b1c9

# 2. Claude Code 继续做自己的前端工作，同时轮询 Codex 进度
tcd check a3f2b1c9
# exit 1 → 还在执行

# ... Claude Code 做了一些前端工作 ...

tcd check a3f2b1c9
# exit 0 → 完成了

# 3. 获取 Codex 的结果
tcd output a3f2b1c9
# 输出：
# I've implemented the user registration API:
# - Created src/api/auth/register.ts with POST handler
# - Added Zod validation schema
# - Created Prisma migration for users table
# - Added 5 unit tests in __tests__/register.test.ts
# All tests passing.

# 4. Claude Code 根据结果决定下一步
tcd status a3f2b1c9 --json
# {"id":"a3f2b1c9","provider":"codex","status":"completed","turn_count":1,...}
```

### 预期结果

- Codex 在独立 tmux session 中完成后端实现
- Claude Code 在等待期间继续做前端工作
- 总时间 ≈ max(前端时间, 后端时间)，而非两者之和

### 涉及功能

- FR-1 (Provider: Codex)
- FR-3 (Job 管理)
- FR-4 (完成检测: notify-hook)
- FR-6 (CLI: start / check / output / status)
- FR-8 (CLAUDE.md 集成)

---

## 场景 2: Claude Code 驱动 Codex 做代码审核

**一句话**: Claude Code 将整个项目的代码审核任务委派给 Codex，自己继续做其他工作。

### 前置条件

- 项目代码已提交到 git
- Claude Code 正在处理新功能开发

### 操作流程

```bash
# 1. Claude Code 启动 Codex 做代码审核
tcd start -p codex -m "审核整个 src/ 目录的代码质量：
1. 检查是否有安全漏洞（SQL 注入、XSS、硬编码密钥等）
2. 检查错误处理是否完善
3. 检查是否有性能问题（N+1 查询、内存泄漏等）
4. 检查测试覆盖率是否足够
5. 输出一份 markdown 格式的审核报告，保存到 docs/code-review.md" \
  -d /Users/michael/projects/myapp

# 输出：
# Job started: b7e4c2d1
# Provider: codex

# 2. Claude Code 继续自己的开发任务，期间偶尔检查
tcd check b7e4c2d1
# exit 1 → 还在审核

# 3. 等审核完成
tcd wait b7e4c2d1 --timeout 600

# 4. 查看审核结果
tcd output b7e4c2d1
# 输出审核报告摘要...

# 5. Claude Code 根据审核结果修复问题
```

### 预期结果

- Codex 全面审查代码并生成 `docs/code-review.md`
- Claude Code 在等待期间并行完成其他开发任务
- 审核报告包含具体文件位置和修复建议

### 涉及功能

- FR-1 (Provider: Codex)
- FR-6 (CLI: start / check / wait / output)
- FR-5 (响应收集)

---

## 场景 3: 并行分派——前端→Gemini，后端→Codex，文档→Claude Code

**一句话**: 主 Agent 同时启动三个 AI 做不同模块，前端给 Gemini CLI，后端给 Codex CLI，文档给 Claude Code CLI。

### 前置条件

- 三种 AI CLI 都已安装（codex / claude / gemini）
- 项目结构清晰，前后端和文档目录分离

### 操作流程

```bash
# 1. 同时启动三个 Job（可由上游 Agent 并行调用，或 shell 脚本批量提交）

# 后端 → Codex（擅长代码实现）
tcd start -p codex -m "实现 REST API：
- GET/POST/PUT/DELETE /api/products
- Prisma schema + migration
- 完整的 CRUD handler
- 错误处理中间件
- 单元测试" -d /Users/michael/projects/shop
# Job started: 001-codex

# 前端 → Gemini CLI（擅长 UI 和前端）
tcd start -p gemini -m "使用 React + TailwindCSS 实现商品列表页面：
- 商品卡片组件（图片、名称、价格、加购按钮）
- 搜索和筛选功能
- 响应式布局（mobile-first）
- 分页组件
- 加载骨架屏" -d /Users/michael/projects/shop
# Job started: 002-gemini

# 文档 → Claude Code CLI（擅长分析和写作）
tcd start -p claude -m "为这个电商项目编写技术文档：
- README.md（项目概述、快速开始、架构说明）
- API 文档（基于 src/api/ 目录的代码生成 OpenAPI spec）
- 部署文档（Docker + Vercel）" -d /Users/michael/projects/shop
# Job started: 003-claude

# 2. 监控所有任务进度
tcd jobs --json
# [
#   {"id":"001-codex", "provider":"codex", "status":"running", "turn_state":"working"},
#   {"id":"002-gemini", "provider":"gemini", "status":"running", "turn_state":"working"},
#   {"id":"003-claude", "provider":"claude", "status":"running", "turn_state":"working"}
# ]

# 3. 逐个检查完成状态
tcd check 001-codex   # exit 0 → Codex 完成了
tcd check 002-gemini  # exit 1 → Gemini 还在做
tcd check 003-claude  # exit 0 → Claude 完成了

# 4. 先收 Codex 和 Claude 的结果
tcd output 001-codex
tcd output 003-claude

# 5. 等待 Gemini 完成
tcd wait 002-gemini --timeout 300
tcd output 002-gemini

# 6. 全部完成后清理
tcd clean
```

### 预期结果

- 三个 AI 在各自独立的 tmux session 中并行工作
- 后端 API、前端 UI、技术文档同时产出
- 总耗时 ≈ 最慢那个 AI 的时间，而非三者之和
- `tcd jobs` 实时显示所有任务状态

### 涉及功能

- FR-1 (Provider: Codex + Claude + Gemini)
- FR-2 (tmux Adapter: 多 session 并行)
- FR-3 (Job 管理: 多 Job)
- FR-6 (CLI: start / jobs / check / wait / output / clean)

---

## 场景 4: OpenClaw 驱动 Codex 写一个小任务

**一句话**: OpenClaw Agent 通过 Python SDK 调用 tcd，让 Codex 完成一个独立的编程小任务。

### 前置条件

- OpenClaw Agent 环境中已安装 tcd Python 包
- Codex CLI 已安装

### 操作流程

```python
# OpenClaw Agent 的 Python 代码
from tcd import TCD
import time

driver = TCD()

# 1. 启动 Codex 写一个 CLI 工具
job = driver.start(
    provider="codex",
    prompt="""写一个 Python CLI 工具 csv2json：
    - 读取 CSV 文件，输出 JSON
    - 支持 --pretty 参数美化输出
    - 支持 --filter 'column=value' 筛选行
    - 使用 click 框架
    - 包含 pyproject.toml 和基本测试""",
    cwd="/tmp/csv2json"
)

print(f"Started job: {job.id}")

# 2. 轮询等待完成
while True:
    result = driver.check(job.id)
    if result.state == "idle":
        break
    if result.state == "context_limit":
        print("Warning: context limit reached")
        break
    time.sleep(3)

# 3. 获取结果
output = driver.output(job.id)
print(f"Codex output:\n{output}")

# 4. 清理
driver.clean()
```

### 预期结果

- OpenClaw 通过 Python SDK 无缝调用 tcd
- Codex 在后台 tmux session 中完成 CLI 工具开发
- OpenClaw 收到完整的实现报告

### 涉及功能

- FR-7 (Python SDK)
- FR-1 (Provider: Codex)
- FR-4 (完成检测: notify-hook)

---

## 场景 5: 流水线——Codex 写代码 → Claude Code 审查 → Gemini 写测试

**一句话**: 三个 AI 串行接力，形成"编码→审查→测试"流水线。

### 前置条件

- 三种 AI CLI 都已安装

### 操作流程

```bash
#!/bin/bash
# pipeline.sh — AI 编程流水线

PROJECT_DIR="/Users/michael/projects/mylib"

# ===== 阶段 1: Codex 写代码 =====
echo "=== Phase 1: Codex implementing ==="
IMPL_JOB=$(tcd start -p codex -m "实现一个 TypeScript 日期处理库：
- formatDate(date, pattern) — 格式化日期
- parseDate(str, pattern) — 解析日期字符串
- diffDays(date1, date2) — 计算天数差
- addDays(date, n) — 添加天数
放在 src/date-utils.ts" -d "$PROJECT_DIR" | grep "Job started:" | awk '{print $NF}')

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

# ===== 阶段 2: Claude Code 审查 =====
echo "=== Phase 2: Claude reviewing ==="
REVIEW_JOB=$(tcd start -p claude -m "审查 src/date-utils.ts 的代码：
1. 是否有边界情况没处理（闰年、时区、无效输入）
2. 是否有性能问题
3. API 设计是否合理
4. 如果有问题，直接修改代码修复
5. 输出审查总结" -d "$PROJECT_DIR" | grep "Job started:" | awk '{print $NF}')

echo "Review job: $REVIEW_JOB"
tcd wait "$REVIEW_JOB" --timeout 300

echo "Review done."
tcd output "$REVIEW_JOB"

# ===== 阶段 3: Gemini 写测试 =====
echo "=== Phase 3: Gemini writing tests ==="
TEST_JOB=$(tcd start -p gemini -m "为 src/date-utils.ts 编写全面的单元测试：
- 使用 vitest 框架
- 覆盖所有导出函数
- 包含正常情况 + 边界情况 + 错误处理
- 测试文件放在 tests/date-utils.test.ts
- 确保 vitest 可以直接运行通过" -d "$PROJECT_DIR" | grep "Job started:" | awk '{print $NF}')

echo "Test job: $TEST_JOB"
tcd wait "$TEST_JOB" --timeout 300

echo "Tests done."
tcd output "$TEST_JOB"

echo "=== Pipeline complete ==="
tcd jobs
```

### 预期结果

- 三个阶段串行执行，每个阶段的 AI 都能看到前一阶段的产出（在同一项目目录中）
- 代码经过编写→审查→测试的完整流程
- 最终项目有实现代码 + 审查报告 + 完整测试

### 涉及功能

- FR-1 (三个 Provider 串行使用)
- FR-6 (CLI: start / wait / output / jobs)

---

## 场景 6: Shell 脚本批量驱动

**一句话**: 用 shell 脚本批量提交多个独立小任务给 Codex。

### 前置条件

- 有一批独立的小任务要执行

### 操作流程

```bash
#!/bin/bash
# batch.sh — 批量提交任务

TASKS=(
    "写一个 Python 的 fibonacci 函数，含 memoization 优化和测试"
    "写一个 Go 的 HTTP 健康检查 endpoint，返回 JSON"
    "写一个 Rust 的 CLI 参数解析器，使用 clap"
)

JOB_IDS=()

# 1. 批量提交
for task in "${TASKS[@]}"; do
    JOB_ID=$(tcd start -p codex -m "$task" -d /tmp/batch-tasks | grep "Job started:" | awk '{print $NF}')
    JOB_IDS+=("$JOB_ID")
    echo "Started: $JOB_ID — $task"
done

# 2. 等待全部完成
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

# 3. 收集所有结果
echo "=== Results ==="
for job_id in "${JOB_IDS[@]}"; do
    echo "--- Job: $job_id ---"
    tcd output "$job_id"
    echo ""
done

# 4. 清理
tcd clean
```

### 预期结果

- 3 个 Codex 实例在 3 个 tmux session 中并行执行
- 全部完成后一次性收集结果
- 总耗时 ≈ 最慢任务的耗时

### 涉及功能

- FR-2 (tmux Adapter: 多 session)
- FR-6 (CLI: start / check / output / clean)

---

## 场景 7: 多轮对话——追加指令

**一句话**: 对运行中的 AI 发送后续指令，实现多轮交互。

### 前置条件

- 已有一个 running 的 Job

### 操作流程

```bash
# 1. 启动一个 Codex 任务
tcd start -p codex -m "搭建一个 Express.js 项目骨架，含 TypeScript 配置" \
  -d /Users/michael/projects/newapp
# Job started: c1d2e3f4

# 2. 等待第一轮完成
tcd wait c1d2e3f4

# 3. 查看结果后，追加指令
tcd send c1d2e3f4 "很好。现在添加以下功能：
1. JWT 认证中间件
2. 请求日志中间件（使用 morgan）
3. 错误处理中间件
4. CORS 配置"

# 4. 等待第二轮完成
tcd check c1d2e3f4     # exit 1 → 在执行
# ... 几秒后 ...
tcd check c1d2e3f4     # exit 0 → 完成

# 5. 查看本轮结果
tcd output c1d2e3f4

# 6. 再追加一轮
tcd send c1d2e3f4 "添加 Docker 支持：Dockerfile + docker-compose.yml，包含 PostgreSQL"

tcd wait c1d2e3f4
tcd output c1d2e3f4

# 7. 查看总体状态
tcd status c1d2e3f4 --json
# {"id":"c1d2e3f4", "turn_count":3, "turn_state":"idle", ...}
```

### 预期结果

- Codex 保持同一个 session，上下文自动累积
- 每轮追加指令只发新内容，不重发历史（token 高效）
- `turn_count` 正确递增
- 每轮输出能获取

### 涉及功能

- FR-6 (CLI: start / wait / send / check / output / status)
- FR-4 (完成检测: 多 Turn)

---

## 场景 8: Python SDK 编程集成

**一句话**: 在 Python 自动化脚本中使用 tcd SDK 编排多个 AI。

### 前置条件

- `pip install tcd` 或 `uv add tcd`

### 操作流程

```python
from tcd import TCD
import concurrent.futures
import time

driver = TCD()

def run_ai_task(provider: str, prompt: str, cwd: str) -> dict:
    """运行一个 AI 任务并返回结果"""
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

# 并行提交三个任务
tasks = [
    ("codex", "实现 user service 的 CRUD", "/projects/app"),
    ("gemini", "实现 user profile 页面组件", "/projects/app"),
    ("claude", "编写 API 集成测试", "/projects/app"),
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

# 清理
driver.clean()
```

### 预期结果

- Python 并发模型（ThreadPoolExecutor）与 tcd 配合正常
- 三个 AI 任务真正并行执行
- 结果以 dict 返回，方便后续处理

### 涉及功能

- FR-7 (Python SDK)
- FR-1 (三个 Provider)
- FR-4 (完成检测)
- FR-5 (响应收集)

---

## 场景 9: 失败恢复与重试

**一句话**: 任务超时或 AI crash 后的恢复策略。

### 前置条件

- 一个 Job 因超时或 AI 异常而 failed

### 操作流程

```bash
# 1. 发现一个 failed 的 job
tcd jobs
# ID         PROVIDER  STATUS    AGE     TURN
# d4e5f6a7   codex     failed    15m     1

# 2. 查看失败原因
tcd status d4e5f6a7 --json
# {"id":"d4e5f6a7", "status":"failed", "error":"timeout after 60 minutes", ...}

# 3. 查看已完成的部分输出
tcd output d4e5f6a7
# 输出到超时前的部分内容...

# 4. 尝试调试：如果 tmux session 还在
tcd attach d4e5f6a7
# （进入 tmux session 查看实际状态，Ctrl+B D 退出）

# 5. 方案 A: 重新启动一个新 Job（使用更长超时）
tcd start -p codex -m "继续完成上一个未完成的任务：[粘贴之前的 prompt]" \
  -d /Users/michael/projects/myapp --timeout 120

# 6. 方案 B: 如果 session 还活着，发送追加指令
tcd send d4e5f6a7 "请继续完成之前的任务"

# 7. 清理失败的 Job
tcd kill d4e5f6a7
tcd clean
```

### 预期结果

- Failed Job 保留日志和部分输出，不丢失数据
- 可通过 `attach` 直接进入 tmux session 调试
- 可选择重新启动或追加指令恢复

### 涉及功能

- FR-3 (Job 管理: failed 状态)
- FR-6 (CLI: jobs / status / output / attach / send / kill / clean)
- NFR-2 (可靠性: 日志持久化)

---

## 场景总结矩阵

| # | 场景 | 上游调用方 | Provider | 模式 | 核心功能 |
|---|------|----------|----------|------|---------|
| 1 | 后端委派 | Claude Code | Codex | 单任务异步 | start/check/output |
| 2 | 代码审核 | Claude Code | Codex | 单任务阻塞 | start/wait/output |
| 3 | 并行分派 | Agent/Script | Codex+Gemini+Claude | 并行多任务 | start×3/jobs/check/wait |
| 4 | 小任务 | OpenClaw | Codex | SDK 单任务 | Python SDK |
| 5 | 流水线 | Shell Script | Codex→Claude→Gemini | 串行链式 | start/wait/output 串行 |
| 6 | 批量驱动 | Shell Script | Codex×3 | 并行批量 | start×N/check循环 |
| 7 | 多轮对话 | 手动/Agent | Codex | 多 Turn | send/check/output |
| 8 | SDK 编程 | Python Script | Codex+Gemini+Claude | 并行 SDK | ThreadPoolExecutor |
| 9 | 失败恢复 | 手动 | 任意 | 调试恢复 | attach/output/kill |
