# tmux-bridge vs tcd 对比调研报告

> 调研日期：2026-03-02
> 调研目的：分析两个本地项目的定位差异、技术重叠与整合机会

## 1. 项目定位对比

| 维度 | tmux-bridge | tcd (tmux-codingagent-driver) |
|------|------------|-------------------------------|
| **一句话定位** | 底层 tmux 会话驱动层（单任务） | 多 AI 编排中间件（多 Provider + 任务管理） |
| **核心隐喻** | "桥"——连接调用方与 AI CLI | "驾驶员"——管理多辆 AI 车并行工作 |
| **抽象层级** | 低层：一个 prompt → 一个 tmux session → 一个结果 | 高层：Job 队列 + Provider 注册 + SDK + CLI |
| **目标用户** | Nanobot/OpenClaw/Claude Code Skill | Claude Code/自定义 Orchestrator/Python 脚本 |
| **支持的 AI** | 主要为 Codex（硬编码了 Codex 行为） | Codex + Claude Code + Gemini CLI（可插拔） |

### 关键差异

**tmux-bridge** 是"单任务驱动器"：给它一个 prompt，它帮你管好 tmux session 的创建、文本传输、完成检测、输出清洗，返回结果。它不关心任务之间的关系。

**tcd** 是"多任务编排器"：它管理一组 Job 的生命周期（创建、状态追踪、持久化、多轮对话、批量清理），并通过 Provider 抽象支持不同的 AI CLI 后端。

## 2. 架构对比

### tmux-bridge 架构（扁平模块化）

```
调用方 → CLI/Python API
           ↓
    ┌─────────────────────────────────┐
    │  session.py    → 会话生命周期     │
    │  transport.py  → 文本传输        │
    │  capture.py    → 输出捕获        │
    │  completion.py → 完成检测        │
    │  output.py     → 输出清洗/解析    │
    │  cli.py        → CLI 入口       │
    └─────────────────────────────────┘
           ↓
       tmux + Codex CLI
```

### tcd 架构（分层 + 插件化）

```
调用方 → CLI (click) / Python SDK
           ↓
    ┌─────────────────────────────────────┐
    │  编排层                              │
    │  ├── sdk.py        → Python API     │
    │  ├── cli.py        → CLI 入口       │
    │  └── job.py        → Job 状态机     │
    ├─────────────────────────────────────┤
    │  检测层                              │
    │  ├── collector.py  → 3 层响应收集    │
    │  ├── marker_detector.py → 标记协议  │
    │  └── idle_detector.py  → 空闲检测   │
    ├─────────────────────────────────────┤
    │  驱动层                              │
    │  ├── tmux_adapter.py → tmux 原语    │
    │  ├── provider.py     → ABC + 注册   │
    │  ├── output_cleaner.py → ANSI 清洗  │
    │  └── providers/                     │
    │      ├── codex.py                   │
    │      ├── claude.py                  │
    │      └── gemini.py                  │
    └─────────────────────────────────────┘
           ↓
       tmux + (Codex | Claude Code | Gemini)
```

## 3. 核心模块功能对比

### 3.1 会话管理

| 特性 | tmux-bridge | tcd |
|------|------------|-----|
| 会话创建 | `TmuxSession.create()` | `tmux_adapter.create_session()` |
| session 命名 | `tmux-bridge-{job_id}` | `tcd-{job_id}` |
| `script -q` 包裹 | ✅ | ✅ |
| 平台检测 (macOS/Linux) | ✅ | ✅ |
| keep-alive (`read`) | ✅ | ✅ |
| scrollback 配置 | ✅ (50000) | ✅ (50000) |
| 更新提示跳过 | ✅ (send "3" + Enter) | ❌（Provider 级处理） |
| 信任对话框处理 | ❌ | ✅ (Claude Code trust dialog) |
| Job JSON 持久化 | 简单 JSON | 完整状态机 (pending→running→completed→failed) |
| 多轮对话 | `send` 命令 | `send` + turn_count 追踪 |

### 3.2 文本传输

| 特性 | tmux-bridge | tcd |
|------|------------|-----|
| 短文本路由 | `send-keys -l` (< 4096B 且无换行) | `send-keys -l` (< 5000 chars) |
| 长文本路由 | `load-buffer` + `paste-buffer` | `load-buffer` + `paste-buffer -p` |
| UTF-8 安全分块 | ✅ (4096B 字节级) | ✅ (5000 char 级) |
| 分块阈值 | 4096 字节 | 5000 字符 |
| 换行检测路由 | ✅ (有换行→buffer) | ❌ (仅按长度) |
| Bracketed paste (`-p`) | ❌ | ✅ (解决 Ink TUI 问题) |

**关键差异**：tmux-bridge 以字节计（更严谨，避免 tmux 硬限制），tcd 以字符计（更简洁）。tcd 的 `-p` flag 是处理 Ink 框架 TUI 的关键改进。

### 3.3 完成检测

| 策略 | tmux-bridge | tcd |
|------|------------|-----|
| 信号文件 | ✅ (notify-hook → `.turn-complete`) | ✅ (notify-hook → `.turn-complete`) |
| 标记协议 | ✅ (SESSION_COMPLETE_MARKER) | ✅ (TCD_REQ/TCD_DONE 协议) |
| 空闲检测 | ❌ | ✅ (连续 capture 比对，Provider 可配阈值) |
| 超时检测 | ✅ (log mtime) | ✅ (Job 级 timeout) |
| 会话死亡检测 | ✅ (has-session) | ✅ (session_exists) |
| 上下文耗尽检测 | ❌ | ✅ (context_limit 状态) |

**关键差异**：tmux-bridge 的标记是被动的（CLI 退出后 echo），tcd 的是主动的（注入 prompt 要求 AI 输出 `TCD_DONE`）。tcd 新增了空闲检测作为"万能后备"。

### 3.4 输出处理

| 特性 | tmux-bridge | tcd |
|------|------------|-----|
| ANSI 清理 | ✅ (CSI/OSC/DCS/ESC + 回车处理) | ✅ (CSI/OSC + 去重) |
| TUI 噪声过滤 | ✅ (进度条、status 行) | ✅ (context left, markers) |
| NDJSON 解析 | ✅ (4 层 JSON 提取) | ✅ (Codex/Claude JSONL) |
| 语义深度常量 | ✅ (STATUS/HEALTH/CONTEXT/CHECKPOINT/FULL) | ❌ (固定逻辑) |
| 输出源 fallback | capture-pane → log file | Provider 解析 → capture-pane → script log |
| CodexOutput 结构化 | ✅ (thread_id, files_modified, tokens) | ❌ (返回 str) |

**关键差异**：tmux-bridge 的输出处理更精细（4 层 JSON 提取、结构化 CodexOutput），tcd 更注重跨 Provider 的通用性。

### 3.5 CLI 接口

| 命令 | tmux-bridge | tcd |
|------|------------|-----|
| 启动任务 | `start <prompt>` | `start -p <provider> -m <prompt>` |
| 发送消息 | `send <id> <msg>` | `send <id> <msg>` |
| 查看状态 | `status <id>` | `status <id>` + `check <id>` |
| 获取输出 | `output <id>` | `output <id>` |
| 终止任务 | `kill <id>` | `kill <id>` |
| 列出任务 | `list` | `jobs` |
| 附加会话 | ❌ (CLI 无，代码有) | `attach <id>` |
| 等待完成 | ❌ (调用方轮询) | `wait <id> --timeout N` |
| 清理 | ❌ | `clean [--all] [--before 7d]` |
| 输出格式 | 全 JSON | 默认人类可读，`--json` 可选 |

## 4. 技术栈对比

| 维度 | tmux-bridge | tcd |
|------|------------|-----|
| Python 版本 | 3.11+ | 3.10+ |
| 运行时依赖 | **零** (纯标准库) | `click>=8.0` |
| 开发依赖 | pytest | pytest |
| 包管理 | uv | uv |
| 构建系统 | setuptools | hatchling |
| CLI 框架 | argparse (内建) | click |
| 代码量 | ~800 LOC | ~2000 LOC |
| 测试数量 | 36 | 119 |
| 模块数量 | 6 | 12 |

## 5. 设计哲学对比

| 原则 | tmux-bridge | tcd |
|------|------------|-----|
| 依赖策略 | 零依赖极简主义 | 最小依赖（仅 click） |
| Provider 扩展 | 通过 SessionConfig 参数化（同一代码路径） | ABC + 注册表 + 独立 Provider 子类 |
| 状态管理 | 无状态（Job 信息在调用方内存中） | 有状态（JSON 文件持久化，Job 状态机） |
| 错误处理 | 返回值 + 异常 | 状态机（failed 状态） |
| 配置模型 | SessionConfig dataclass | Provider 属性 + CLI flags |
| 测试策略 | mock subprocess（无需 tmux） | mock subprocess（无需 tmux） |

## 6. 共同设计来源

两个项目都调研了相同的 4 个开源项目，且复用了相似的设计要素：

| 来源项目 | tmux-bridge 采纳 | tcd 采纳 |
|----------|------------------|----------|
| **codex-orchestrator** | `script -q` 日志、`read` keep-alive、notify-hook | tmux 操作模式、notify-hook、ANSI 清理 |
| **NTM** | 4096B UTF-8 分块、语义深度常量 | - |
| **MCO** | Protocol 契约、4 层 JSON 提取 | - |
| **claude_code_bridge** | 完成标记协议 | Provider 抽象、标记协议、idle 检测 |

## 7. 重叠代码估算

| 功能领域 | 重叠程度 | 说明 |
|----------|---------|------|
| tmux session 创建 | **90%** | 几乎相同：detach + script + read |
| 文本传输 | **80%** | 相同双路由策略，阈值和细节略不同 |
| 完成检测 (信号文件) | **95%** | 完全相同的 notify-hook 机制 |
| ANSI 清洗 | **70%** | 都覆盖 CSI/OSC，tmux-bridge 更全面 |
| Job 管理 | **20%** | tcd 有完整状态机，tmux-bridge 只有 JobInfo |
| Provider 抽象 | **0%** | tmux-bridge 无此概念 |
| CLI | **40%** | 命令集重叠但实现不同 |

**总体重叠率：约 50-60%**

## 8. 各自优势

### tmux-bridge 独有优势

1. **零依赖**：纯标准库，任何 Python 环境即可运行
2. **输出解析更精细**：4 层 JSON 提取、结构化 CodexOutput（含 thread_id、files_modified、tokens）
3. **语义深度常量**：STATUS(20) / HEALTH(50) / CONTEXT(500) / FULL(-1)，调用方可按需选择
4. **Nanobot 适配器**：现成的 Tool ABC adapter
5. **UTF-8 字节级分块**：更严谨地处理 tmux 硬限制
6. **Codex 更新提示跳过**：自动 send "3" + Enter

### tcd 独有优势

1. **多 Provider 支持**：Codex + Claude Code + Gemini，可插拔扩展
2. **Job 状态持久化**：JSON 文件 + 原子写入，进程重启后恢复
3. **空闲检测**：连续 capture-pane 比对，解决"AI 不配合标记协议"的问题
4. **多轮对话追踪**：turn_count + req_id 机制
5. **Python SDK**：`from tcd import TCD`，面向对象 API
6. **`wait` 命令**：阻塞式等待完成（省去轮询逻辑）
7. **Bracketed paste**：`paste-buffer -p` 解决 Ink TUI 多行输入
8. **信任对话框处理**：Claude Code 的 trust folder dialog 自动处理
9. **上下文耗尽检测**：识别 AI 的 context_limit 状态
10. **`clean` 命令**：Job 文件的生命周期管理

## 9. 整合建议

### 方案 A：tcd 依赖 tmux-bridge（推荐）

```
调用方 → tcd (CLI/SDK)
           ↓
    ┌───────────────────────────┐
    │  tcd 层（新增价值）         │
    │  ├── Job 状态机            │
    │  ├── Provider 注册表       │
    │  ├── 多轮对话管理          │
    │  ├── SDK                  │
    │  └── wait/clean 等命令     │
    ├───────────────────────────┤
    │  tmux-bridge 层（复用）     │
    │  ├── session 管理          │
    │  ├── transport 传输        │
    │  ├── capture 捕获          │
    │  ├── completion 检测       │
    │  └── output 清洗/解析      │
    └───────────────────────────┘
           ↓
       tmux + AI CLIs
```

**优点**：
- 消除 ~1000 LOC 重复代码（tmux adapter + 完成检测 + 输出清洗）
- tmux-bridge 的精细输出解析能力被 tcd 所有 Provider 共享
- tmux-bridge 保持零依赖，可独立使用

**缺点**：
- 增加一个依赖
- tmux-bridge 的 Codex 假设可能需要泛化

### 方案 B：tcd 吸收 tmux-bridge 精华（轻量替代）

将 tmux-bridge 中优于 tcd 的部分直接移植：
- 4 层 JSON 提取 → `tcd/output_cleaner.py`
- 语义深度常量 → `tcd/tmux_adapter.py`
- UTF-8 字节级分块 → `tcd/tmux_adapter.py`
- 结构化 CodexOutput → `tcd/providers/codex.py`

**优点**：保持单包，无额外依赖
**缺点**：两个项目继续各自演化，重叠持续存在

### 方案 C：合并为一个项目

将 tmux-bridge 作为 `tcd` 的底层模块 (`tcd.bridge`)，整合全部功能。

**优点**：彻底消除重叠
**缺点**：tmux-bridge 的独立用户（Nanobot/OpenClaw）需要迁移

## 10. 结论

| 维度 | 判断 |
|------|------|
| **功能覆盖** | tcd 是 tmux-bridge 的超集（多 Provider + Job 管理 + SDK） |
| **底层质量** | tmux-bridge 更精细（字节级分块、4 层 JSON、结构化输出） |
| **架构扩展性** | tcd 更好（Provider 插件化、状态持久化） |
| **依赖纯净度** | tmux-bridge 更好（零依赖） |
| **维护效率** | 目前有 ~50-60% 重叠代码，长期维护两个项目不经济 |
| **推荐路径** | **方案 A**（tcd 依赖 tmux-bridge）或 **方案 B**（移植精华）|

**核心结论**：两个项目的关系是"底层驱动层 vs 上层编排层"，不是竞争关系。tmux-bridge 做了更精细的 tmux 交互处理，tcd 做了更完整的多 AI 管理。理想状态是分层复用，避免同质代码的双重维护。
