# Changelog

## v0.3.0 — 2026-03-05

Git worktree 并行隔离、增量输出、活动提取、日志系统。

### Git Worktree 支持

- 新增 `src/tcd/worktree.py`：git worktree 原语（create/remove/merge/delete_branch）
- `tcd start --worktree [--wt-name NAME]`：在独立 worktree 中启动任务
- `tcd merge <job_id> [--squash] [--no-cleanup]`：合并 worktree 分支回主分支
- SDK `start()` 新增 `worktree`/`worktree_name` 参数，新增 `merge_worktree()` 方法
- `kill` 自动清理 worktree
- Job 模型新增 `worktree_path`/`worktree_branch` 字段

### 增量输出与活动提取

- `tcd output --tail N`：只输出最后 N 行
- `tcd output --since-line N`：增量轮询（输出第 N 行之后的内容）
- `tcd check --json` 新增 `activity` 字段：从 scrollback 正则提取有意义的操作行（Edited, Created, Ran 等）
- 输出行数通过 stderr `__lines_total=N` 暴露，支持外部轮询追踪

### 日志系统

- `tcd -v`（INFO）/ `tcd -vv`（DEBUG）详细日志，输出到 stderr
- INFO 级别覆盖所有关键流程：start, check, send, kill, merge, refresh_status
- WARNING 级别覆盖异常路径：TUI 超时、context_limit、合并冲突、worktree 清理失败、session 消失

### 默认 Sandbox 变更

- Codex 默认 sandbox 从 `workspace-write` 改为 `danger-full-access`
- 诊断规则 R1 只对显式指定 `workspace-write`/`workspace-read` 时警告

### 测试

- 新增 32 个测试用例（worktree 原语 12 + SDK 集成 12 + CLI 7 + 诊断 1）
- 总测试数: 191 -> 223

---

## v0.2.0 — 2026-03-05

事件日志与诊断系统，全面提升 tcd 的可观测性。详见 `docs/prd-event-log.md`。

### Phase 1: 事件日志

- 新增 `src/tcd/event_log.py`：append-only JSONL 事件日志（emit + load_events）
- 在 cli.py / sdk.py 关键路径埋入 7 类事件：job.created, job.tui_ready, job.tui_timeout, job.prompt_sent, job.checked, job.turn_complete, job.message_sent, job.killed
- 新增 `tcd log` 命令：查看事件日志，支持 `--tail N` 和 `--event <type>` 过滤
- `config.py` 新增 `job_events_path()`，clean 命令同步清理 `.events.jsonl` 文件

### Phase 2: 诊断引擎

- 新增 `src/tcd/diagnostics.py`：4 条规则自动检测问题
  - SANDBOX_MISMATCH：prompt 含写意图但沙箱模式为 workspace-write
  - STALL：连续 4 次 check 无状态变化且超过 60s
  - PERMISSION_ERROR：pane 输出中发现权限拒绝信息
  - TURN0_STUCK：turn 0 持续 working 超过 120s
- `tcd check --json`：输出结构化 JSON（state, elapsed_s, turn_count, warnings, pane_tail）
- SDK 新增 `check_with_diagnostics()` 方法和 `DiagnosticCheckResult` 数据类

### Phase 3: Skill 更新

- `codex-worker` Skill 改用 `tcd check --json` 轮询
- 新增 4 种 warnings 自动响应策略
- 文件系统布局补充 `.events.jsonl`，其他命令补充 `tcd log`

### Phase 4: Token 记录

- `CompletionResult` 新增 `tokens` 字段
- Codex provider `detect_completion()` 从 NDJSON session 文件解析 token_count
- `Job` 模型新增 `total_tokens` 字段，每轮累计
- `tcd status` 和 `tcd status --json` 展示累计 token 用量
- `job.turn_complete` 事件记录 tokens 数据

### 测试

- 新增 23 个测试用例（事件日志、诊断规则、token 累计、CLI JSON 输出等）
- 总测试数: 160 → 183

---

## v0.1.1 — 2026-03-05

Code review 修复轮次，修复 3 个 Critical + 4 个 Major + 2 个 Minor 问题。

### Critical 修复

- **C-2**: Claude/Gemini 多轮会话 turn_count 递增——之前 turn_count 永远为 0 导致 req_id 冲突
- **C-3**: Provider 启动命令注入防护——model 参数添加正则白名单校验 + shlex.quote 转义
- **C-1**: Marker 检测从子串匹配改为严格整行匹配，避免前缀误命中

### Major 修复

- **M-1**: Gemini 响应提取不再包含用户 prompt 文本
- **M-4**: Session 消失时区分 completed（正常）/ failed（异常中断）
- **M-6**: `--sandbox` 参数从 CLI 贯通到 Codex provider 启动命令（之前是死代码）

### Minor 修复

- **m-1**: CLI start/send 检查 send_text 返回值，失败时标记 job 为 failed
- **m-3**: 缩小 except Exception 范围为具体异常类型，添加 logger.exception 日志

### 测试

- 新增 13 个测试用例（model 注入、marker 严格匹配、session 消失状态区分等）
- 总测试数: 147 → 160

---

## v0.1.0 — 2026-03-02

首次发布。Phase 1-4 全部完成，119 单元测试通过，3 个 Provider E2E 验证通过。

### Phase 1: 基础框架 + Codex Driver

- tmux Adapter: create/kill session, send_keys, send_long_text (bracketed paste), capture_pane
- Provider 抽象基类 + 注册表
- Codex Provider: notify-hook 完成检测, JSONL session 解析
- Job 管理: JSON 持久化, 状态机 (pending → running → completed/failed)
- Response Collector: session file → capture-pane → log fallback
- ANSI 输出清理 (CSI/OSC/DCS/ESC 序列移除)
- CLI 入口: start, send, status, output, check, wait, jobs, attach, kill, clean

### Phase 2: Claude Code Driver

- Claude Code Provider: `--dangerously-skip-permissions`, `unset CLAUDECODE`
- Marker 协议: TCD_REQ/TCD_DONE 注入与扫描
- 空闲检测模块 (20s 阈值)
- 信任对话自动处理 ("Yes, I trust this folder")

### Phase 3: Gemini CLI Driver

- Gemini CLI Provider: `--yolo` 模式
- Marker + 空闲检测 (15s 阈值)
- 信任对话 + 重启等待处理

### Phase 4: Python SDK + 文档

- Python SDK: `from tcd import TCD` (start/check/wait/output/send/jobs/kill/clean)
- README.md: CLI 参考, SDK 示例, Provider 支持表, 架构图

### 关键技术决策

- 多行文本使用 `paste-buffer -p` (bracketed paste) 而非 `send-keys -l`，解决 Ink TUI 换行提交问题
- 三层完成检测: signal file → marker scan → idle detect
- 平台自适应 `script` 命令 (macOS vs Linux)
