# tcd 集成指南

tcd 提供两种使用姿势：**完整编排**（CLI/SDK）和**轻量驱动**（直接 import 底层模块）。按你的场景选择。

---

## 场景 1：Claude Code 中使用

通过 CLAUDE.md 配置 + bash 工具调用 tcd CLI。

### 配置

在项目 CLAUDE.md 中添加：

```markdown
## tcd: AI 任务分派器

可用命令：
- `tcd start -p <provider> -m "<prompt>" -d <cwd>` — 启动任务，返回 job_id
- `tcd check <job_id>` — 非阻塞检查（exit 0=完成, 1=运行中）
- `tcd wait <job_id> --timeout <秒>` — 阻塞等待完成
- `tcd output <job_id>` — 获取结果
- `tcd send <job_id> "<message>"` — 发送后续指令
- `tcd jobs` — 列出所有任务
- `tcd kill <job_id>` — 终止任务
- `tcd clean` — 清理已完成任务

Provider: codex / claude / gemini
```

### 使用示例

```bash
# 启动 Codex 做后端
tcd start -p codex -m "实现用户注册 API" -d /path/to/project
# → Job started: a3f2b1c9

# 继续做其他事，稍后检查
tcd check a3f2b1c9  # exit 0 = 完成

# 获取结果
tcd output a3f2b1c9
```

### 并行分派

```bash
tcd start -p codex -m "实现后端 API" -d /project
tcd start -p gemini -m "实现前端页面" -d /project
tcd start -p claude -m "编写技术文档" -d /project

# 监控
tcd jobs --json
```

---

## 场景 2：OpenClaw 插件

OpenClaw 是 TypeScript 项目，通过 `child_process` 调用 tcd CLI（JSON 输出）。

### 调用方式

```typescript
import { execSync } from "child_process";

// 启动任务
const startResult = JSON.parse(
  execSync(`tcd start -p codex -m "Fix the bug" -d /project --json`).toString()
);
const jobId = startResult.id;

// 检查完成
const status = JSON.parse(
  execSync(`tcd status ${jobId} --json`).toString()
);

// 获取输出
const output = execSync(`tcd output ${jobId}`).toString();
```

### OpenClaw Tool 定义模板

```typescript
import { Type } from "@sinclair/typebox";

export function createTcdTool(): AnyAgentTool {
  return {
    name: "tcd_dispatch",
    description: "Dispatch a coding task to an AI CLI agent via tcd",
    parameters: Type.Object({
      provider: Type.Union([
        Type.Literal("codex"),
        Type.Literal("claude"),
        Type.Literal("gemini"),
      ]),
      prompt: Type.String({ description: "Task description" }),
      cwd: Type.Optional(Type.String()),
      timeout: Type.Optional(Type.Number({ minimum: 0 })),
    }),
    execute: async (_id, params) => {
      const { provider, prompt, cwd, timeout } = params as any;
      const args = [`-p`, provider, `-m`, prompt];
      if (cwd) args.push(`-d`, cwd);
      if (timeout) args.push(`--timeout`, String(timeout));

      const result = execSync(`tcd start ${args.join(" ")} --json`).toString();
      return { content: [{ type: "text", text: result }] };
    },
  };
}
```

---

## 场景 3：Nanobot / Python 编排

直接 import Python SDK 或底层模块。

### 方式 A：完整 SDK（推荐）

```python
from tcd import TCD

tcd = TCD()
job = tcd.start("codex", "实现 CRUD API", cwd="/project")
result = tcd.wait(job.id, timeout=300)
output = tcd.output(job.id)
tcd.clean()
```

### 方式 B：轻量驱动（只用 tmux 交互层）

不走 Job 管理和 Provider 注册表，直接操作 tmux session：

```python
from tcd.tmux_adapter import TmuxAdapter, CaptureDepth
from tcd.output_cleaner import clean_output, extract_json_payloads

adapter = TmuxAdapter()

# 创建 session
adapter.create_session(
    name="my-codex-job",
    cmd="codex -a never --prompt 'Fix the login bug'",
    cwd="/path/to/project",
)

# 发送文本（自动选择 send-keys 或 load-buffer）
adapter.send_text("my-codex-job", "Add unit tests")

# 捕获输出（语义深度）
raw = adapter.capture_pane("my-codex-job", depth=CaptureDepth.CONTEXT)
clean = clean_output(raw)

# 提取 JSON（4 层策略）
payloads = extract_json_payloads(raw)

# 清理
adapter.kill_session("my-codex-job")
```

### 方式 C：Nanobot Tool Adapter 模板

```python
from tcd import TCD


class CodexTmuxTool:
    """Nanobot Tool ABC adapter for tcd."""

    name = "codex_dispatch"
    description = "Dispatch a coding task to Codex via tmux"

    def __init__(self, timeout: int = 3600):
        self.tcd = TCD()
        self.timeout = timeout

    def run(self, params: dict, context=None) -> str:
        job = self.tcd.start(
            provider="codex",
            prompt=params["task"],
            cwd=params.get("cwd", "."),
        )
        self.tcd.wait(job.id, timeout=self.timeout)
        output = self.tcd.output(job.id) or ""
        self.tcd.clean()
        return output
```

---

## 场景 4：Shell 脚本批量编排

### 并行批量

```bash
#!/bin/bash
JOBS=()
for task in "写 fibonacci" "写 HTTP server" "写 CLI parser"; do
    JOB_ID=$(tcd start -p codex -m "$task" -d /tmp/batch | grep "Job started:" | awk '{print $NF}')
    JOBS+=("$JOB_ID")
done

# 等待全部完成
for job_id in "${JOBS[@]}"; do
    tcd wait "$job_id" --timeout 300
    echo "=== $job_id ==="
    tcd output "$job_id"
done

tcd clean
```

### 串行流水线

```bash
#!/bin/bash
# Codex 写代码 → Claude 审查 → Gemini 写测试
IMPL=$(tcd start -p codex -m "实现日期处理库" -d /project | awk '/Job started:/{print $NF}')
tcd wait "$IMPL"

REVIEW=$(tcd start -p claude -m "审查 src/date-utils.ts" -d /project | awk '/Job started:/{print $NF}')
tcd wait "$REVIEW"

TEST=$(tcd start -p gemini -m "为 date-utils 写测试" -d /project | awk '/Job started:/{print $NF}')
tcd wait "$TEST"

tcd clean
```

---

## 场景 5：结构化 Codex 输出

需要提取 Codex 的 thread ID、修改的文件列表、token 用量时：

```python
from tcd import TCD
from tcd.providers.codex import CodexProvider

tcd = TCD()
job = tcd.start("codex", "重构 auth 模块", cwd="/project")
tcd.wait(job.id)

# 结构化解析
provider = CodexProvider()
result = provider.parse_response_structured(job)
if result:
    print(f"Thread: {result.thread_id}")
    print(f"Files modified: {result.files_modified}")
    print(f"Tokens: {result.tokens}")
    print(f"Summary: {result.summary}")
```

---

## API 速查

### 完整 SDK (`from tcd import TCD`)

| 方法 | 返回值 | 说明 |
|------|--------|------|
| `start(provider, prompt, cwd, model, timeout)` | `Job` | 启动任务 |
| `check(job_id)` | `CheckResult` | 非阻塞状态检查 |
| `wait(job_id, timeout)` | `CheckResult` | 阻塞等待 |
| `output(job_id)` | `str \| None` | 获取清洗后输出 |
| `send(job_id, message)` | `bool` | 发送后续指令 |
| `status(job_id)` | `Job` | 获取 Job 完整状态 |
| `jobs(status)` | `list[Job]` | 列出任务 |
| `kill(job_id)` | `bool` | 终止任务 |
| `clean()` | `int` | 清理已完成任务 |

### 底层模块（轻量 import）

| 模块 | 核心函数/类 | 说明 |
|------|------------|------|
| `tcd.tmux_adapter` | `TmuxAdapter` | tmux 操作原语 |
| `tcd.tmux_adapter` | `CaptureDepth` | 语义捕获深度 |
| `tcd.output_cleaner` | `clean_output()` | ANSI + TUI 噪声清洗 |
| `tcd.output_cleaner` | `strip_ansi()` | 仅清洗 ANSI |
| `tcd.output_cleaner` | `extract_json_payloads()` | 4 层 JSON 提取 |
| `tcd.providers.codex` | `CodexOutput` | 结构化 Codex 输出 |
| `tcd.providers.codex` | `parse_codex_ndjson()` | 解析 Codex NDJSON |

### CLI 命令

| 命令 | 说明 |
|------|------|
| `tcd start -p <provider> -m <prompt>` | 启动任务 |
| `tcd check <id>` | 非阻塞检查 (exit: 0/1/2/3) |
| `tcd wait <id> [--timeout N]` | 阻塞等待 |
| `tcd output <id> [--full] [--raw]` | 获取输出 |
| `tcd send <id> <message>` | 发送后续指令 |
| `tcd status <id> [--json]` | 查看状态 |
| `tcd jobs [--status S] [--json]` | 列出任务 |
| `tcd attach <id>` | 连接 tmux session |
| `tcd kill <id> [--all]` | 终止任务 |
| `tcd clean [--all] [--before 7d]` | 清理 |
