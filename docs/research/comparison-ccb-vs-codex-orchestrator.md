# CCB vs Codex-Orchestrator 对比分析

**日期**: 2026-03-02

## 一句话总结

| 项目 | 定位 |
|------|------|
| **CCB** (claude_code_bridge) | 多 AI 协作平台，5 种 provider，daemon 架构，生产级 |
| **codex-orchestrator** | 单 AI 驱动器，专注 Codex CLI，轻量 Job 系统 |

---

## 核心维度对比

| 维度 | CCB (bfly123) | codex-orchestrator (kingbootoshi) |
|------|--------------|----------------------------------|
| **语言** | Python | TypeScript/Bun |
| **代码量** | ~5000+ 行 | ~1500 行 |
| **支持的 AI** | Claude, Codex, Gemini, OpenCode, Droid (5种) | 仅 Codex CLI |
| **架构模式** | Daemon (TCP Server + Worker Pool) | CLI 工具 + Job JSON 文件 |
| **通信方式** | tmux/WezTerm send-keys + 日志读取 | tmux send-keys + capture-pane + notify-hook |
| **完成检测** | CCB_DONE:{req_id} 自定义标记协议 | notify-hook 信号文件 + echo marker |
| **并发模型** | Per-session worker thread pool | 单 Job 串行（多 Job 各自独立 tmux session） |
| **日志持久化** | 各 AI 原生 session 文件 | `script -q` 录屏 + Codex JSONL |
| **长提示处理** | 无特殊处理（依赖 terminal inject） | 短用 send-keys，长用 load-buffer + paste-buffer |
| **异步通知** | completion_hook.py (支持 email) | notify-hook.ts → 信号文件 |
| **防循环机制** | CLAUDE.md prompt 层 guardrail | 无（假设 Claude 行为可控） |
| **跨 AI 上下文** | ContextTransfer (8K token 预算, 去重) | 无（单 AI） |
| **成熟度** | v5.2.6，大量边界修复 | 早期项目，功能完整但较新 |

---

## 相同点

1. **终端即总线**：都通过 tmux send-keys 注入提示词，而非走 API
2. **Session 持久化**：都利用 AI CLI 自维护的会话，不重发历史 → token 高效
3. **日志回读**：都从终端输出/日志文件中读取 AI 响应
4. **Job/Request 抽象**：都有任务状态管理（pending → running → completed/failed）
5. **超时兜底**：都有空闲超时机制（CCB 60s daemon idle，orchestrator 60min job inactive）

## 关键差异

### 1. 架构复杂度

CCB 是 **重量级 daemon 架构**：TCP server + worker pool + idle monitor + parent process monitor。适合长期运行的多 AI 协作场景。

codex-orchestrator 是 **轻量级 CLI 工具**：每次调用执行命令，状态存 JSON 文件。简单直接，适合单 AI 任务分派。

### 2. 完成检测策略

CCB 用**自定义协议标记** (`CCB_DONE:{req_id}`) 注入到 prompt 中，要求 AI 在回复末尾输出标记。优点是通用（任何 AI 都能输出文字标记），缺点是 AI 有时不遵守。

codex-orchestrator 用 **Codex 原生 notify-hook**，Codex 在 agent turn 结束时调用外部脚本。优点是可靠（CLI 原生支持），缺点是仅 Codex 有此机制。

### 3. 多 AI 支持

CCB 天然支持多 provider，有统一抽象层。codex-orchestrator 只支持 Codex，但架构简洁可扩展。

---

## 对 tmux-codingagent-driver 的复用价值

### 可直接复用

| 来源 | 技术 | 复用方式 |
|------|------|---------|
| orchestrator | tmux 长提示 paste-buffer 策略 | 原样移植 |
| orchestrator | `script -q` 日志录制 | 原样移植 |
| orchestrator | `echo marker; read` 防会话退出 | 原样移植 |
| orchestrator | sleep 时序经验值（1s init, 0.3s send） | 参考值 |
| CCB | CCB_DONE 标记协议（通用完成检测） | 适配 Claude/Gemini |
| CCB | Per-session 串行化 | 防并发污染 |
| CCB | Async guardrail（禁止 polling 循环）| prompt 层约束 |
| CCB | Terminal backend 抽象层 | 多终端支持 |

### 不建议直接复用

| 技术 | 原因 |
|------|------|
| CCB 完整 daemon 套件 | 过重，我们不需要 TCP server |
| CCB Memory-First 三层存储 | 超出 MVP |
| orchestrator 的 Codex 专属逻辑 | 我们需要多 AI 支持 |
