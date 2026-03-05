# 调研报告: kingbootoshi/codex-orchestrator 源码架构分析

**日期**: 2026-03-02
**来源**: https://github.com/kingbootoshi/codex-orchestrator
**任务**: 深度分析 codex-orchestrator 如何通过 tmux 驱动 Codex CLI、解析输出、分发任务和收集结果

---

## 调研摘要

codex-orchestrator 是一个 TypeScript/Bun 项目，通过 tmux 会话以交互模式启动 Codex CLI（TUI），用 `send-keys` 注入提示词，用 `script` 命令持久化日志，通过 notify-hook 机制感知 agent turn 完成，通过 `capture-pane` 轮询状态。整体架构简洁，约 6 个核心模块，约 1500 行代码，是一个成熟可借鉴的参考实现。

---

## 项目概况

| 属性 | 值 |
|------|-----|
| 运行时 | Bun（TypeScript，不编译直接运行） |
| 依赖 | tmux, Codex CLI (`codex`), Bun, OpenAI API Key |
| 唯一生产依赖 | `glob@^10` |
| 默认模型 | `gpt-5.3-codex`（fast: `gpt-5.3-codex-spark`） |
| 默认 reasoning | `xhigh` |
| Job 存储 | `~/.codex-agent/jobs/` |
| tmux 前缀 | `codex-agent-{jobId}` |

---

## 文件结构

```
src/
  config.ts          - 全局配置（模型、路径、超时）
  tmux.ts            - tmux 操作原语（create/send/capture/kill）
  jobs.ts            - Job 生命周期管理
  watcher.ts         - turn-complete 信号文件机制
  notify-hook.ts     - Codex notify 回调脚本（agent turn 结束时触发）
  session-parser.ts  - 解析 ~/.codex/sessions/ 的 JSONL/JSON 会话文件
  output-cleaner.ts  - 清理 ANSI 和 TUI 噪音
  cli.ts             - CLI 入口（start/status/send/capture/jobs/watch/attach）
bin/
  codex-agent        - 主可执行文件
  codex-bg           - Bash 封装，后台运行 + 轮询完成
plugins/
  codex-orchestrator/skills/codex-orchestrator/SKILL.md  - Claude 技能描述
```

---

## 核心机制 1：tmux 驱动 Codex CLI

### 会话创建（`tmux.ts: createSession`）

**完整流程**：

```typescript
// 1. 把长提示写入文件，避免 shell 转义问题
fs.writeFileSync(promptFile, options.prompt);

// 2. 构建 codex 命令行，关键参数：
//    -a never     : 自动批准（无需人工确认文件操作）
//    -s sandbox   : 沙箱模式（read-only / workspace-write / danger-full-access）
//    notify hook  : 通知脚本，agent 每完成一个 turn 触发
const codexArgs = [
  `-c`, `model="${options.model}"`,
  `-c`, `model_reasoning_effort="${options.reasoningEffort}"`,
  `-c`, `skip_update_check=true`,
  `-c`, `'notify=["bun","run","${notifyHook}","${options.jobId}"]'`,
  `-a`, `never`,
  `-s`, options.sandbox,
].join(" ");

// 3. 用 script 命令记录全部终端输出到 .log 文件
//    会话结束后打印 "[codex-agent: Session complete...]"，read 防止会话立即退出
const shellCmd = `script -q "${logFile}" codex ${codexArgs}; echo "\\n\\n[codex-agent: Session complete. Press Enter to close.]"; read`;

// 4. 创建 detached tmux 会话
execSync(`tmux new-session -d -s "${sessionName}" -c "${options.cwd}" '${shellCmd}'`);

// 5. 等待 Codex TUI 初始化（1秒）
spawnSync("sleep", ["1"]);

// 6. 跳过更新提示（发送 "3" + Enter）
execSync(`tmux send-keys -t "${sessionName}" "3"`);
spawnSync("sleep", ["0.5"]);
execSync(`tmux send-keys -t "${sessionName}" Enter`);
spawnSync("sleep", ["1"]);

// 7. 注入提示词
if (options.prompt.length < 5000) {
  // 短提示：直接 send-keys
  execSync(`tmux send-keys -t "${sessionName}" '${escapedPrompt}'`);
  spawnSync("sleep", ["0.3"]);  // 等待 TUI 处理文本
  execSync(`tmux send-keys -t "${sessionName}" Enter`);
} else {
  // 长提示（≥5000字符）：tmux buffer paste
  execSync(`tmux load-buffer "${promptFile}"`);
  execSync(`tmux paste-buffer -t "${sessionName}"`);
  spawnSync("sleep", ["0.3"]);
  execSync(`tmux send-keys -t "${sessionName}" Enter`);
}
```

**关键设计决策**：
- 使用 `script -q` 记录日志，即使 tmux 会话被 kill 后日志依然保留
- 用 `read` 命令让 shell 等待，防止会话在 codex 退出后立即关闭（便于 capture-pane 读最终输出）
- 短提示用 `send-keys`，长提示（≥5000字符）用 `load-buffer` + `paste-buffer`，解决命令行长度限制
- 单引号转义：`message.replace(/'/g, "'\\''")`

### 向运行中会话发送消息（`sendMessage`）

```typescript
const escapedMessage = message.replace(/'/g, "'\\''");
execSync(`tmux send-keys -t "${sessionName}" '${escapedMessage}'`);
spawnSync("sleep", ["0.3"]);  // 等待 TUI 处理
execSync(`tmux send-keys -t "${sessionName}" Enter`);
```

---

## 核心机制 2：Codex TUI 输出解析

### 策略 1：`capture-pane`（实时状态检测）

```typescript
// 读取最近 N 行（适合状态检测）
execSync(`tmux capture-pane -t "${sessionName}" -p`);

// 读取完整 scrollback（-S - 表示从历史开始）
execSync(`tmux capture-pane -t "${sessionName}" -p -S -`, { maxBuffer: 50 * 1024 * 1024 });
```

**完成检测**：检查 capture-pane 输出中是否包含 `"[codex-agent: Session complete"` 字符串（由 shell 在 codex 退出后 echo 打印）。

### 策略 2：`script` 日志文件（持久化后备）

- 路径：`~/.codex-agent/jobs/{jobId}.log`
- 优先用 tmux capture，session 不存在时 fallback 到读日志文件
- 可用于解析 session ID（`extractSessionId(logContent)`）

### 策略 3：Codex 原生会话文件（结构化数据）

Codex 会在 `~/.codex/sessions/` 存储 JSONL/JSON 会话文件，包含 token 用量、文件修改列表、摘要等。

```typescript
// session-parser.ts 解析流程：
// 1. 从 .log 文件提取 session ID（正则匹配 "session id: xxx"）
const sessionId = extractSessionId(logContent);  // 正则 /session id:\s*([0-9a-f-]{8,})/i

// 2. 在 ~/.codex/sessions/ 目录树中找到对应的 .jsonl 或 .json 文件
const sessionFile = findSessionFile(sessionId);

// 3. 解析获取结构化数据
const data = parseSessionFile(sessionFile);
// data = { tokens: {input, output, context_window, context_used_pct}, files_modified: [...], summary: "..." }
```

JSONL 格式解析关键逻辑：
- `event_msg + token_count` → 解析 token 用量
- `event_msg + agent_message` → 提取摘要
- `response_item + apply_patch tool call` → 提取修改的文件路径

### ANSI 清理（`output-cleaner.ts`）

大量正则处理 TUI 终端输出噪音，包括：
- ANSI CSI/OSC/DCS/ESC 序列清除
- Codex Chrome TUI 特有的噪音行（`esc to interrupt`, `% context left`, `background terminal running` 等）
- 重复行去重
- URL 重绘 artifact 清理
- "typing artifact" 检测（短单词重复序列 heuristic）
- 重排输出为干净文本

---

## 核心机制 3：notify-hook（Turn 完成检测）

**最优雅的部分**。Codex 支持 `notify` 配置项，每个 agent turn 结束时执行指定命令，并传入 JSON payload。

### 配置方式

```typescript
// 在 codex 命令行中配置
`-c 'notify=["bun","run","${notifyHook}","${options.jobId}"]'`
```

### notify-hook.ts 处理

```typescript
// 接收 Codex 的 agent-turn-complete 事件
function main(): void {
  const jobId = process.argv[2];
  const rawPayload = process.argv[3];   // Codex 传入的 JSON payload

  const payload = parsePayload(rawPayload);
  if (payload.type !== "agent-turn-complete") return;

  const event: TurnEvent = {
    turnId: payload["turn-id"],
    lastAgentMessage: payload["last-assistant-message"],
    timestamp: new Date().toISOString(),
  };

  // 写入信号文件 ~/.codex-agent/jobs/{jobId}.turn-complete
  writeSignalFile(jobId, event);
  // 更新 job.json 中的 turnCount, lastTurnCompletedAt, lastAgentMessage, turnState="idle"
  updateJobTurn(jobId, event);
}
```

### 信号文件机制（`watcher.ts`）

```typescript
// 信号文件路径
const signalPath = `~/.codex-agent/jobs/${jobId}.turn-complete`;

// 写入信号
writeSignalFile(jobId, event);     // 创建 .turn-complete 文件

// 检测是否 idle（Claude 轮询此文件）
signalFileExists(jobId);           // 检查文件是否存在

// 读取 turn 事件详情
readSignalFile(jobId);             // 读取 JSON

// 清除信号（发送新消息时清除）
clearSignalFile(jobId);            // 删除文件
```

**核心优势**：Claude 不需要轮询 tmux pane，直接检测信号文件存在与否，低 CPU 消耗，高可靠性。

---

## 核心机制 4：任务分发与结果收集架构

### Job 数据结构

```typescript
interface Job {
  id: string;                          // 4字节随机hex（如 "a3f2b1c9"）
  status: "pending" | "running" | "completed" | "failed";
  prompt: string;
  model: string;
  reasoningEffort: "low" | "medium" | "high" | "xhigh";
  sandbox: "read-only" | "workspace-write" | "danger-full-access";
  parentSessionId?: string;            // 多层级 agent 追踪
  cwd: string;
  createdAt: string;                   // ISO 时间戳
  startedAt?: string;
  completedAt?: string;
  tmuxSession?: string;                // "codex-agent-{jobId}"
  result?: string;                     // 完整输出
  error?: string;
  // Turn 状态追踪
  turnCount?: number;
  lastTurnCompletedAt?: string;
  lastAgentMessage?: string;           // 截断到 500 字符
  turnState?: "working" | "idle" | "context_limit";
}
```

所有 Job 以 JSON 文件存储在 `~/.codex-agent/jobs/{jobId}.json`。

### 任务生命周期

```
startJob()
  → 生成 jobId (randomBytes(4).hex)
  → 保存 job.json (status: "pending")
  → createSession() 启动 tmux
  → 更新 job.json (status: "running", tmuxSession)

[Codex 执行中...]
  → notify-hook 触发 → 写入 .turn-complete 信号文件
  → 外部轮询 signalFileExists() 感知 turn 结束

refreshJobStatus(jobId)  [轮询调用]
  → sessionExists()? → 否 → status: "completed"
  → capturePane(-20行) 含 "[codex-agent: Session complete"? → status: "completed"
  → isInactiveTimedOut()? (60分钟无活动) → killSession() → status: "failed"

getJobsJson()  [结构化输出]
  → 对每个 completed job 调用 loadSessionData()
  → 解析 ~/.codex/sessions/*.jsonl 获取 tokens + files_modified + summary
```

### 超时机制

- 以 `.log` 文件的 `mtime` 为最后活动时间（log 文件随 Codex 输出实时更新）
- 若 `Date.now() - lastActivityMs > 60分钟`，kill session，标记为 failed
- 没有 log 文件时 fallback 到 `job.startedAt`

### 结果收集优先级

```
getJobOutput() / getJobFullOutput():
1. 优先：tmux capture-pane（session 仍存在时）
2. 后备：读取 ~/.codex-agent/jobs/{jobId}.log

getJobsJson() (结构化数据):
1. 从 .log 提取 session ID
2. 在 ~/.codex/sessions/ 找到 JSONL 文件
3. 解析获取 tokens + files_modified + summary
```

---

## 核心机制 5：错误处理与重试

### 错误处理策略

1. **tmux 操作包裹在 try/catch**：`createSession`、`sendMessage`、`capturePane` 等函数在失败时返回 `false`/`null` 而非抛出异常
2. **session 不存在检查前置**：所有操作先调用 `sessionExists()` 验证
3. **超时兜底**：60 分钟无活动自动 kill，标记 failed
4. **auxiliaryFiles 清理**：`deleteJob()` 同时清理 `.prompt`、`.log`、`.turn-complete` 文件

### 上下文耗尽检测

CLI 的 `await` 命令会检测 `turnState === "context_limit"` 状态，并以 exit code 2 退出，供上游 orchestrator 区分正常完成（0）和上下文耗尽（2）。

### 重试机制

**原生无重试**。设计哲学是"不要杀死 agent 重新开始，而是用 `send` 命令发后续指令"。通过 `sendToJob()` / `codex-agent send {jobId} "消息"` 重定向运行中的 agent，而不是重启。

---

## `codex-bg` 后台封装（Bash）

```bash
# codex-bg 做了 cli.ts 没有直接提供的功能：
# 1. 提取 job ID
JOB_ID=$(codex-agent start ... | grep "Job ID:" | awk '{print $NF}')

# 2. 轮询 turn 完成信号（检查 .turn-complete 文件）
if [ -f "$JOBS_DIR/$JOB_ID.turn-complete" ]; then
  export CODEX_AGENT_TURN_COMPLETE=1
fi

# 3. 轮询 job 状态直到完成
while true; do
  STATUS=$(codex-agent status $JOB_ID --json | jq -r .status)
  if [ "$STATUS" = "completed" ] || [ "$STATUS" = "failed" ]; then
    export CODEX_AGENT_DONE=$STATUS
    break
  fi
  sleep $POLL_INTERVAL
done

# 4. 可选：完成后触发回调命令
if [ -n "$NOTIFY_CMD" ]; then eval "$NOTIFY_CMD"; fi
```

---

## 对我们项目的借鉴价值

### 可直接移植的模式

| 模式 | 原始实现 | 我们可以... |
|------|---------|------------|
| tmux 驱动 Codex | `createSession()` in `tmux.ts` | 直接翻译为 Python subprocess |
| 长提示 paste-buffer | `tmux load-buffer` + `paste-buffer` | 同样适用，5000字符阈值合理 |
| notify-hook 信号文件 | `watcher.ts` + `notify-hook.ts` | 完全移植，无语言障碍 |
| Session 完成标记 | `echo "[codex-agent: Session complete...]"` | 自定义 marker 策略 |
| 超时检测 via log mtime | `getLastActivityMs()` | Python `os.stat().st_mtime` |
| 输出 fallback 策略 | capture-pane → log file | 两级 fallback 必须实现 |
| JSONL 会话解析 | `session-parser.ts` | 解析 `~/.codex/sessions/` 获取 tokens |

### 值得注意的实现细节

1. **sleep 时序**：创建会话后 sleep 1s（等待 TUI 初始化），send-keys 后 sleep 0.3s（等待 TUI 处理）。这些延迟是经验值，在 Python 中需要同等处理。

2. **单引号转义**：`message.replace(/'/g, "'\\''")` - 在 Python 中对应 `message.replace("'", "'\\''")` 或用 shlex。

3. **`script` 命令差异**：macOS 的 `script` 参数顺序与 Linux 不同（`script -q file cmd` vs `script -q -c cmd file`）。需要平台检测。

4. **`-a never` 参数**：关键！让 Codex 自动批准所有文件操作，否则 TUI 会等待人工确认导致挂起。

5. **notify hook 格式**：Codex 的 notify 配置是数组格式 `["cmd", "arg1", "arg2"]`，payload 通过 stdin 或 argv 传入（项目用 argv[3]）。

6. **`read` 命令防止会话退出**：`codex ...; echo "...complete..."; read` - 这个 pattern 让会话在 codex 退出后继续存活，确保 capture-pane 能读到最终输出。

---

## 架构图

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
           │ [Codex TUI 运行中]
           │
           ├── notify-hook.ts (agent turn 结束)
           │     └── 写入 {id}.turn-complete 信号文件
           │
           └── echo "[codex-agent: Session complete...]" + read
                 │
                 └── jobs.ts: refreshJobStatus() 检测到 complete marker

Claude Code 轮询:
    ├── signalFileExists({id}) → turn 完成 → 读摘要 → 发送后续指令
    ├── capturePane(-20行) → 检测 complete marker
    └── getJobsJson() → 解析 ~/.codex/sessions/*.jsonl → tokens + files + summary
```

---

## 参考资料

- [kingbootoshi/codex-orchestrator (GitHub)](https://github.com/kingbootoshi/codex-orchestrator)
- [源码: tmux.ts](https://raw.githubusercontent.com/kingbootoshi/codex-orchestrator/main/src/tmux.ts)
- [源码: jobs.ts](https://raw.githubusercontent.com/kingbootoshi/codex-orchestrator/main/src/jobs.ts)
- [源码: notify-hook.ts](https://raw.githubusercontent.com/kingbootoshi/codex-orchestrator/main/src/notify-hook.ts)
- [源码: watcher.ts](https://raw.githubusercontent.com/kingbootoshi/codex-orchestrator/main/src/watcher.ts)
- [源码: session-parser.ts](https://raw.githubusercontent.com/kingbootoshi/codex-orchestrator/main/src/session-parser.ts)
- [源码: output-cleaner.ts](https://raw.githubusercontent.com/kingbootoshi/codex-orchestrator/main/src/output-cleaner.ts)
- [源码: config.ts](https://raw.githubusercontent.com/kingbootoshi/codex-orchestrator/main/src/config.ts)
