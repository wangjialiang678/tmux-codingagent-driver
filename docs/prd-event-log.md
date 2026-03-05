# PRD: tcd 事件日志与诊断系统

**版本**: v0.2.0
**日期**: 2026-03-05
**状态**: DONE
**前置**: v0.1.1（Codex code review 修复已合入，160 tests pass）

---

## 1. 问题

在一次 7 轮 Codex 工作流中（详见 docs/workflow-log.md），暴露了严重的可观测性缺陷：

| 问题 | 影响 |
|------|------|
| 所有 killed job 的 error 都是 "killed by user" | 无法区分沙箱失败、自动更新、超时 |
| token 消耗未记录 | 代码已能解析但未持久化 |
| model 字段全为 null | Codex 实际用 gpt-5.3-codex，tcd 未捕获 |
| 7 个 job 之间无关联 | 不知谁是重试、谁依赖谁 |
| 调用方等 10 分钟无反馈 | `tcd wait` 阻塞，无中间状态 |
| 问题发现依赖人工 | 沙箱不匹配、stall 等模式需人眼识别 |

**核心洞察**：tcd 记录了机器事实（job.json），调用方记录了叙事日志（workflow-log.md），**中间层完全空白**——没有结构化事件流，没有规则诊断。

---

## 2. 设计原则

- **tcd 记录事实，不做决策** — 事件日志 + 规则诊断，不含 LLM
- **调用方做语义理解和决策** — 通过 Skill 指导行为
- **追加写入，不修改** — 事件日志是 append-only JSONL
- **零配置默认开启** — 不需要 `--verbose`，基础事件始终记录
- **向后兼容** — 不改 job.json 结构，新增 events 文件

---

## 3. 方案

### 3.1 第一层：事件日志（tcd 自动记录）

每个 job 一个 `~/.tcd/jobs/<id>.events.jsonl`，追加写入：

```jsonl
{"ts":"2026-03-05T04:00:00Z","event":"job.created","provider":"codex","sandbox":"workspace-write","cwd":"/path"}
{"ts":"2026-03-05T04:00:01Z","event":"job.tui_ready","elapsed_ms":1200}
{"ts":"2026-03-05T04:00:02Z","event":"job.prompt_sent","bytes":1234,"req_id":"fc73d94b-0-1741147202"}
{"ts":"2026-03-05T04:01:00Z","event":"job.checked","state":"working","pane_lines":45}
{"ts":"2026-03-05T04:03:00Z","event":"job.checked","state":"idle","turn_count":1}
{"ts":"2026-03-05T04:03:01Z","event":"job.turn_complete","turn":0,"method":"signal_file","tokens":{"in":5000,"out":3000}}
{"ts":"2026-03-05T04:05:00Z","event":"job.killed","reason":"user"}
```

**事件类型**：

| 事件 | 触发点 | 关键字段 |
|------|--------|---------|
| `job.created` | `JobManager.create_job()` | provider, sandbox, cwd, model |
| `job.tui_ready` | `_wait_for_tui()` 完成 | elapsed_ms, trust_handled |
| `job.tui_timeout` | `_wait_for_tui()` 超时 | elapsed_ms |
| `job.prompt_sent` | `send_text()` 成功 | bytes, req_id |
| `job.prompt_failed` | `send_text()` 失败 | error |
| `job.checked` | `check()` / `wait()` 每次轮询 | state, pane_lines |
| `job.turn_complete` | 检测到 idle/context_limit | turn, method, tokens |
| `job.message_sent` | `send()` | bytes, req_id, turn |
| `job.completed` | 正常完成 | elapsed_s |
| `job.failed` | 异常结束 | error, reason |
| `job.killed` | 用户 kill | reason |

**实现方式**：新增 `src/tcd/event_log.py`

```python
"""Append-only event log for job lifecycle tracking."""

import json
import time
from pathlib import Path
from tcd.config import JOBS_DIR
from tcd.job import _now_iso


def job_events_path(job_id: str) -> Path:
    return JOBS_DIR / f"{job_id}.events.jsonl"


def emit(job_id: str, event: str, **data) -> None:
    """Append a single event to the job's event log."""
    entry = {"ts": _now_iso(), "event": event, **data}
    path = job_events_path(job_id)
    with open(path, "a") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")
```

**埋点位置**（在现有代码中加 `emit()` 调用）：

| 文件 | 位置 | 事件 |
|------|------|------|
| `cli.py:84` | `create_job` 后 | `job.created` |
| `cli.py:134` | TUI ready 后 | `job.tui_ready` / `job.tui_timeout` |
| `cli.py:141` | `send_text` 成功/失败 | `job.prompt_sent` / `job.prompt_failed` |
| `cli.py:240-258` | `check()` 每次调用 | `job.checked` |
| `cli.py:360` | `send()` 成功 | `job.message_sent` |
| `cli.py:499` | `_kill_job()` | `job.killed` |
| `sdk.py` | 同上镜像位置 | 同上 |

### 3.2 第二层：诊断引擎（tcd 规则检测）

新增 `src/tcd/diagnostics.py`，纯规则引擎，不需要 LLM：

```python
"""Rule-based diagnostics for job health."""

@dataclass
class Warning:
    code: str
    message: str
    severity: Literal["info", "warn", "error"]


def diagnose(job: Job, pane_tail: str | None = None) -> list[Warning]:
    """Run diagnostic rules against a job's current state."""
    warnings = []

    # R1: Sandbox mismatch
    if job.sandbox in (None, "workspace-write"):
        prompt_lower = job.prompt.lower()
        write_keywords = ["修改", "修复", "fix", "edit", "write", "create", "save"]
        if any(kw in prompt_lower for kw in write_keywords):
            warnings.append(Warning(
                code="SANDBOX_MISMATCH",
                message=f"Prompt contains write intent but sandbox={job.sandbox or 'workspace-write'}",
                severity="warn",
            ))

    # R2: Stall detection
    events = load_events(job.id)
    check_events = [e for e in events if e["event"] == "job.checked"]
    if len(check_events) >= 4:
        recent = check_events[-4:]
        if all(e.get("state") == "working" for e in recent):
            span = _time_diff(recent[0]["ts"], recent[-1]["ts"])
            if span > 60:
                warnings.append(Warning(
                    code="STALL",
                    message=f"No state change in {span:.0f}s ({len(recent)} checks)",
                    severity="warn",
                ))

    # R3: Permission error in pane
    if pane_tail:
        permission_phrases = ["Operation not permitted", "Permission denied", "read-only"]
        for phrase in permission_phrases:
            if phrase in pane_tail:
                warnings.append(Warning(
                    code="PERMISSION_ERROR",
                    message=f"Found '{phrase}' in pane output",
                    severity="error",
                ))
                break

    # R4: Turn-0 stuck
    if job.turn_count == 0 and job.turn_state == "working":
        elapsed = _elapsed_seconds(job)
        if elapsed > 120:
            warnings.append(Warning(
                code="TURN0_STUCK",
                message=f"Still on turn 0 after {elapsed}s",
                severity="warn",
            ))

    return warnings
```

### 3.3 CLI 集成

**增强 `tcd check`**：

```bash
# 现有行为不变（exit code 0/1/2/3）
tcd check <job_id>

# 新增 --json 输出（含诊断）
tcd check <job_id> --json
```

输出：
```json
{
  "state": "working",
  "elapsed_s": 120,
  "turn_count": 0,
  "warnings": [
    {"code": "SANDBOX_MISMATCH", "severity": "warn", "message": "..."},
    {"code": "TURN0_STUCK", "severity": "warn", "message": "..."}
  ],
  "pane_tail": "... last 5 lines ..."
}
```

**新增 `tcd log`**：

```bash
# 查看事件日志
tcd log <job_id>                    # 所有事件
tcd log <job_id> --tail 10          # 最近 10 条
tcd log <job_id> --event job.checked # 按类型过滤
```

### 3.4 Skill 集成

更新 `codex-worker` Skill 的轮询策略：

```
每次轮询用 tcd check <job_id> --json，而非裸 tcd check。
解析 JSON 中的 warnings：
- SANDBOX_MISMATCH → 自动 kill + 用 full-auto 重启
- PERMISSION_ERROR → 告诉用户 Codex 遇到权限问题
- STALL → 抓 pane_tail 做语义分析，汇报给用户
- TURN0_STUCK → 可能 TUI 没启动成功，建议 attach 查看
```

---

## 4. 实施计划

### Phase 1: 事件日志（核心）

- [x] 新增 `src/tcd/event_log.py`（emit + load_events + 路径）
- [x] 在 `config.py` 加 `job_events_path()`
- [x] 在 `cli.py` 关键路径埋点（8 个事件）
- [x] 在 `sdk.py` 镜像埋点
- [x] `JobManager._remove_job_files()` 清理 events 文件
- [x] 新增 `tcd log` CLI 命令
- [x] 测试：事件写入/读取/清理

### Phase 2: 诊断引擎

- [x] 新增 `src/tcd/diagnostics.py`（4 条规则）
- [x] `tcd check --json` 集成诊断输出
- [x] `tcd check --json` 附加 pane_tail（最后 5 行）
- [x] 测试：每条规则的触发/不触发

### Phase 3: Skill 更新

- [x] 更新 `codex-worker` Skill 使用 `tcd check --json`
- [x] 添加 warnings 处理策略
- [x] 实测验证

### Phase 4: Token 记录（Codex 特有）

- [x] `detect_completion()` 中解析 Codex NDJSON 的 token_count
- [x] 写入 `job.turn_complete` 事件的 tokens 字段
- [x] 在 `tcd status --json` 中展示累计 token

---

## 5. 影响范围

| 文件 | 改动类型 |
|------|---------|
| `src/tcd/event_log.py` | **新增** |
| `src/tcd/diagnostics.py` | **新增** |
| `src/tcd/config.py` | 加 `job_events_path()` |
| `src/tcd/cli.py` | 埋点 + `tcd log` + `tcd check --json` |
| `src/tcd/sdk.py` | 埋点 |
| `src/tcd/job.py` | `_remove_job_files` 加 events 清理 |
| `~/.claude/skills/codex-worker/SKILL.md` | 更新轮询策略 |
| `tests/test_event_log.py` | **新增** |
| `tests/test_diagnostics.py` | **新增** |

**不改动**：provider 代码、tmux_adapter、collector、output_cleaner

---

## 6. 非目标

- ❌ 工作流关联（workflow_id）— 留给上层编排系统
- ❌ LLM 语义分析 — 调用方 Skill 负责
- ❌ 实时推送/WebSocket — tcd 是 CLI 工具，轮询足够
- ❌ 日志轮转/压缩 — `tcd clean` 已覆盖生命周期
- ❌ 修改 job.json 结构 — 事件日志独立存储
