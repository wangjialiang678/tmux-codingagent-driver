# tmux-bridge × tcd 整合方案深度分析

> 日期：2026-03-02
> 视角：需求方（消费端）+ 技术架构权衡

---

## 一、先厘清：谁是"需求方"

两个项目的消费者不同，需求也不同。在讨论方案前，必须先明确需求图谱。

### 1.1 tmux-bridge 的消费者

| 消费者 | 调用方式 | 核心需求 |
|--------|---------|---------|
| **Claude Code Skill** | CLI (`tmux-bridge start/status/output`) | 最简接口，一个 prompt 进去、一个结果出来 |
| **Nanobot Tool** | Python API (`TmuxSession.create()`) | 适配 Nanobot Tool ABC，嵌入 Ralph Loop |
| **OpenClaw Plugin** | CLI (TypeScript `child_process`) | JSON stdout，跨语言零依赖 |

**需求特征**：这三类消费者都是**单任务模型**——每次调用只启动一个 Codex 实例，完成后回收。不关心多 Provider、不关心 Job 持久化、不关心多轮对话。

### 1.2 tcd 的消费者

| 消费者 | 调用方式 | 核心需求 |
|--------|---------|---------|
| **Claude Code** (直接 bash) | CLI (`tcd start -p codex ...`) | 并行分派，多 Provider 选择 |
| **Shell 脚本** (pipeline/batch) | CLI (脚本编排) | 批量提交、状态轮询、结果收集、流水线串联 |
| **Python 自动化** | SDK (`from tcd import TCD`) | 面向对象 API，`wait()`/`clean()` 生命周期管理 |
| **自定义 Orchestrator** | SDK/CLI | 多轮对话、失败恢复、context_limit 检测 |

**需求特征**：这些消费者要**多任务编排**——同时管理多个 Job、追踪不同 Provider 的状态、处理失败和超时。

### 1.3 需求交集与差集

```
                  tmux-bridge 需求                tcd 需求
                ┌──────────────┐            ┌──────────────┐
                │ 零依赖       │            │ 多 Provider  │
                │ Nanobot 适配 │            │ Job 状态机   │
                │ 结构化输出   │  ┌──────┐  │ 多轮对话     │
                │ (CodexOutput)│  │ 共同 │  │ wait/clean   │
                │ 语义深度常量 │  │      │  │ SDK (OOP)    │
                │              │  │tmux  │  │ 空闲检测     │
                │              │  │驱动  │  │ Claude/Gemini│
                │              │  │核心  │  │ trust dialog │
                │              │  │      │  │ bracketed    │
                │              │  │      │  │  paste       │
                └──────────────┘  └──────┘  └──────────────┘
```

**共同核心**（~50% 代码）：tmux session 管理、send-keys/load-buffer 传输、信号文件检测、ANSI 清洗。

---

## 二、方案 A 详析：tcd 依赖 tmux-bridge

### 架构形态

```
调用方 → tcd (CLI/SDK)
           ↓
    ┌─────────────────────────────┐
    │  tcd 层（编排 + Provider）    │
    │  ├── sdk.py                 │
    │  ├── cli.py (click)         │
    │  ├── job.py (状态机)         │
    │  ├── provider.py (注册表)    │
    │  ├── idle_detector.py       │
    │  ├── marker_detector.py     │
    │  └── providers/{codex,claude,gemini}.py │
    └──────────────┬──────────────┘
                   │  import tmux_bridge
    ┌──────────────▼──────────────┐
    │  tmux-bridge 层（驱动原语）    │
    │  ├── session.py             │
    │  ├── transport.py           │
    │  ├── capture.py             │
    │  ├── completion.py          │
    │  └── output.py              │
    └──────────────┬──────────────┘
                   ↓
               tmux + AI CLIs
```

### 适合方案 A 的场景

#### 场景 1：tmux-bridge 有独立生命力

如果 Nanobot 和 OpenClaw 是**活跃的、持续演化的**消费者，tmux-bridge 需要独立发版、独立测试、独立维护接口稳定性。

**判断依据**：
- Nanobot 的 Ralph Loop 是否在生产中使用 tmux-bridge？
- OpenClaw 是否已经集成了 tmux-bridge 的 CLI？
- 有没有其他团队/项目在用 tmux-bridge？

如果答案是"是"，则 tmux-bridge 必须保持独立发布，方案 A 是自然选择。

#### 场景 2：你预期未来有更多 tmux 驱动层的消费者

比如：
- 未来可能有一个 **Go 版 orchestrator** 需要调用 tmux-bridge CLI
- 未来可能有一个 **MCP Server** 直接封装 tmux-bridge
- tmux-bridge 作为"tmux + AI CLI 交互的标准库"被复用

这种情况下，保持 tmux-bridge 的独立性和零依赖特性是有价值的。

#### 场景 3：团队分工需要解耦

如果 tmux-bridge 和 tcd 由不同的人维护，或者你希望将来可能这样分工——底层 tmux 交互稳定后交给别人，自己专注上层编排——方案 A 提供了干净的职责边界。

### 方案 A 的利

| 利 | 说明 |
|---|------|
| **消除 ~1000 LOC 重复** | tcd 的 tmux_adapter + 信号文件检测 + ANSI 清洗 改为 import tmux-bridge |
| **获得 tmux-bridge 的精细能力** | 4 层 JSON 提取、语义深度常量、结构化 CodexOutput、字节级 UTF-8 分块 |
| **独立演化** | tmux-bridge 可以独立修 bug、加新功能（如 WezTerm 后端），tcd 自动受益 |
| **零依赖保持** | tmux-bridge 继续零依赖，给轻量级消费者（Nanobot、OpenClaw）一个干净的接口 |
| **关注点分离** | tmux-bridge: "如何与 tmux 交互"；tcd: "如何编排多个 AI" |

### 方案 A 的弊

| 弊 | 说明 | 严重程度 |
|---|------|---------|
| **接口适配成本** | tmux-bridge 的 API 是为单任务设计的（`TmuxSession` + `SessionConfig`），tcd 需要将其适配到多 Provider 模型。比如 tmux-bridge 把 Codex 更新跳过硬编码在 `session.py` 里，但 tcd 的 Provider 各有不同的初始化逻辑。需要 wrapper 或 adapter 层。 | 🟡 中 |
| **版本耦合风险** | tmux-bridge 改了 API（比如 `TmuxSession.create()` 签名变了），tcd 被动跟进。两个包的发布节奏可能不同步。 | 🟡 中 |
| **抽象泄漏** | tmux-bridge 当前假设"代理=Codex"（硬编码了更新提示跳过、notify-hook 格式）。tcd 支持 3 种 Provider，需要 tmux-bridge 泛化这些假设。这意味着**反向改造 tmux-bridge**。 | 🔴 高 |
| **部署复杂度** | tcd 的 `uv tool install` 需要同时解析 tmux-bridge 依赖。本地开发时可能要 editable install 两个包。 | 🟡 中 |
| **tmux-bridge 不支持 bracketed paste** | tcd 发现 Ink TUI 需要 `paste-buffer -p`，tmux-bridge 目前用的是无 `-p` 的 `paste-buffer`。要么改 tmux-bridge，要么 tcd 绕过它。 | 🟡 中 |

### 方案 A 的隐藏前提

**tmux-bridge 必须泛化**。当前 tmux-bridge 的几处硬编码是为 Codex 定制的：

1. `session.py` 的 `_skip_update_prompt()` — 发送 "3" + Enter 跳过 Codex 更新提示
2. `session.py` 的 `_build_agent_command()` — Codex 的 `-c 'notify=[...]'` 格式
3. `completion.py` 的 `SESSION_COMPLETE_MARKER` — tmux-bridge 专用标记
4. `output.py` 的 `CodexOutput` — Codex NDJSON 格式特定

如果不泛化这些，tcd 只能部分使用 tmux-bridge（用 transport 和 capture，但绕过 session 和 completion），这削弱了方案 A 的收益。

---

## 三、方案 B 详析：tcd 吸收 tmux-bridge 精华

### 架构形态

```
调用方 → tcd (CLI/SDK)               tmux-bridge（独立维护）
           ↓                              ↓
    ┌─────────────────────────┐    ┌──────────────┐
    │  tcd 层（全功能一体）     │    │  (照旧运行)   │
    │  ├── sdk.py             │    │              │
    │  ├── cli.py             │    │  Nanobot/    │
    │  ├── job.py             │    │  OpenClaw    │
    │  ├── provider.py        │    │  消费者      │
    │  ├── tmux_adapter.py    │    └──────────────┘
    │  │   (已吸收精华)       │
    │  ├── output_cleaner.py  │
    │  │   (已吸收 4 层 JSON) │
    │  └── providers/...      │
    └─────────────────────────┘
```

### 适合方案 B 的场景

#### 场景 1：tmux-bridge 的外部消费者不活跃或已停用

如果 Nanobot 的 Ralph Loop 已经不走 tmux-bridge（改用 tcd SDK 或其他方案），OpenClaw 也没有实际集成——那么 tmux-bridge 就是一个"历史前身"，不值得为它保持接口兼容性。

#### 场景 2：你希望 tcd 快速迭代，不受上游约束

tcd 在演化中发现了很多 tmux-bridge 没考虑的问题：
- Bracketed paste（Ink TUI）
- 空闲检测（AI 不配合标记协议）
- Trust dialog 自动处理（Claude Code）
- Context limit 检测
- 多轮 turn_count 追踪

这些都是 tcd 在实战中发现并解决的。如果每个改进都要先说服 tmux-bridge 接受、再等 tmux-bridge 发版、再在 tcd 中消费——迭代速度会被严重拖慢。

#### 场景 3：你是唯一维护者，不想管两个包的协调

现实考量：两个包 = 两倍的发版、两倍的 changelog、两倍的 CI 维护、以及接口变更时的协调成本。如果你是唯一维护者，这个开销可能不值得。

#### 场景 4：tcd 的 tmux adapter 已经够好，只缺几个技巧

方案 B 的本质是"cherry-pick"——从 tmux-bridge 移植 4-5 个精华到 tcd 现有代码中：

| 移植项 | 目标位置 | 工作量 |
|-------|---------|--------|
| 4 层 JSON 提取 | `tcd/output_cleaner.py` | ~50 LOC |
| 语义深度常量 | `tcd/tmux_adapter.py` | ~15 LOC |
| UTF-8 字节级分块 | `tcd/tmux_adapter.py` | ~30 LOC |
| 结构化 CodexOutput | `tcd/providers/codex.py` | ~40 LOC |
| DCS 序列清理 | `tcd/output_cleaner.py` | ~10 LOC |

总计 ~145 LOC 移植，一次性完成，无持续协调成本。

### 方案 B 的利

| 利 | 说明 |
|---|------|
| **零协调成本** | tcd 完全自主演化，改 tmux adapter 不需要考虑 tmux-bridge 的兼容性 |
| **一个包部署** | `uv tool install tcd` 就够了，不需要处理依赖解析 |
| **快速迭代** | 发现问题直接改，不经过上游 PR → review → merge → publish 循环 |
| **移植量小** | 只需要 ~145 LOC，半天完成 |
| **tcd 已经更完善** | 多 Provider、bracketed paste、idle detection 等都是 tcd 在 tmux-bridge 基础上的改进，不存在"降级"风险 |

### 方案 B 的弊

| 弊 | 说明 | 严重程度 |
|---|------|---------|
| **重叠代码持续存在** | tmux-bridge 和 tcd 的 tmux 交互层继续各自维护，修 bug 需要两处改 | 🟡 中 |
| **tmux-bridge 的改进不自动流入 tcd** | 如果 tmux-bridge 未来加了 WezTerm 后端，tcd 不会自动受益 | 🟢 低（可以随时手动同步） |
| **叙事割裂** | 两个项目定位相似容易混淆：用户可能问"用哪个？" | 🟡 中 |

---

## 四、还有没有方案 C？

### 方案 C：合并为一个项目（tmux-bridge 归入 tcd）

```
tcd/
├── src/tcd/
│   ├── bridge/              ← 原 tmux-bridge 代码，重命名为子模块
│   │   ├── session.py
│   │   ├── transport.py
│   │   ├── capture.py
│   │   ├── completion.py
│   │   └── output.py
│   ├── cli.py
│   ├── sdk.py
│   ├── job.py
│   ├── provider.py
│   └── providers/...
```

**适合场景**：tmux-bridge 的外部消费者可以迁移到 `from tcd.bridge import TmuxSession`。

**利**：
- 彻底消除重叠，一处修改一处测试
- 保留 tmux-bridge 的精细实现作为 tcd 的底层

**弊**：
- 破坏 tmux-bridge 现有消费者（`import tmux_bridge` → `import tcd.bridge`）
- tmux-bridge 的零依赖承诺被打破（tcd 依赖 click）
- 如果 Nanobot/OpenClaw 只需要底层驱动，被迫安装整个 tcd

**评估**：除非 tmux-bridge 确认没有独立消费者，否则不推荐。

### 方案 D：提取公共底层库

```
tmux-agent-core/     ← 新的公共底层包（零依赖）
├── session.py
├── transport.py
├── capture.py
└── output.py

tmux-bridge/         ← 依赖 tmux-agent-core + 零额外依赖
├── cli.py
├── completion.py
└── adapters/

tcd/                 ← 依赖 tmux-agent-core + click
├── cli.py
├── sdk.py
├── providers/
└── ...
```

**适合场景**：两个项目都活跃，且有共同的底层需要统一维护。

**评估**：过度工程化。三个包维护成本更高，除非有第三个消费者出现。**不推荐。**

---

## 五、决策框架：4 个判断问题

回答以下 4 个问题即可做出选择：

### Q1：tmux-bridge 是否有 tcd 之外的活跃消费者？

| 答案 | 推荐 |
|------|------|
| **是**（Nanobot/OpenClaw 在用，且会持续用） | → 方案 A |
| **否**（已停用或从未真正集成） | → 方案 B 或 C |
| **不确定**（计划中但还没做） | → 方案 B（先快走，未来需要时再拆） |

### Q2：你是否打算让 tmux-bridge 成为通用 tmux-AI 交互标准库？

| 答案 | 推荐 |
|------|------|
| **是**（想让更多项目复用） | → 方案 A（但需要先泛化 tmux-bridge） |
| **否**（只是 tcd 的前身） | → 方案 B |

### Q3：你有多少精力维护两个包的协调？

| 答案 | 推荐 |
|------|------|
| **充足**（或有团队） | → 方案 A |
| **有限**（个人项目，时间宝贵） | → 方案 B |

### Q4：未来 6 个月的重点是什么？

| 答案 | 推荐 |
|------|------|
| **tcd 快速迭代**（加 MCP server、context transfer 等） | → 方案 B（减少依赖管理开销） |
| **生态建设**（让更多工具用上 tmux-bridge） | → 方案 A |
| **稳定运行**（两个都不怎么改了） | → 现状即可，暂不整合 |

---

## 六、我的判断与建议

基于已读到的信息，我做以下事实性推断：

1. **tmux-bridge 是 tcd 的前身**：两者在同一天（2026-03-02）开发，调研了相同的 4 个开源项目。tcd 的 PRD 明确说"CCB 和 codex-orchestrator 不足"，然后设计了更完善的方案。tmux-bridge 更像是第一轮迭代。

2. **tmux-bridge 的消费者尚未成熟**：Nanobot adapter 存在但 Nanobot 编排系统本身还在演化；OpenClaw 的接口调研文档表明是在探索阶段，还没有 PR 合入。

3. **tcd 是你实际的主力工具**：119 个测试 vs 36 个、9 个场景文档、3 个 Provider 实现——投入量级差距明显。

4. **你是唯一维护者**：没有看到团队协作的痕迹。

### 结论：推荐方案 B

**理由**：

- tmux-bridge 尚无不可替代的独立消费者
- tcd 已经是更完善的实现，只差几个锦上添花的技巧
- 你的精力应该投在 tcd 的 v0.2 功能（MCP server、context transfer）上，而不是维护两个包的接口协调
- 移植量只有 ~145 LOC，一次性成本极低

### 具体行动建议

1. **立即做**：从 tmux-bridge 移植 5 项精华到 tcd（~145 LOC，半天工作量）
2. **标记关系**：在 tcd 的 README 和 CHANGELOG 中注明 "吸收了 tmux-bridge 的 XX 能力"
3. **tmux-bridge 转为归档**：README 标注 "已合入 tcd，推荐使用 tcd"
4. **保留可选路径**：如果未来 Nanobot 或 OpenClaw 确实需要一个零依赖的轻量驱动层，可以从 tcd 中再抽出 `tcd.bridge` 子模块发布

### 如果情况变化

| 变化 | 应对 |
|------|------|
| Nanobot 真的要用 tmux-bridge | 从 tcd 中抽出 `tcd.bridge` 包，零依赖发布 |
| 有外部贡献者想维护 tmux-bridge | 方案 A 变得合理，交出底层维护权 |
| 需要 WezTerm/Zellij 后端 | 在 tcd 中新增 backend 抽象层，不需要 tmux-bridge |

---

## 七、总结表

| 维度 | 方案 A (依赖) | 方案 B (移植) | 方案 C (合并) | 方案 D (公共库) |
|------|:---:|:---:|:---:|:---:|
| 消除重复代码 | ✅ 完全 | 🟡 部分 | ✅ 完全 | ✅ 完全 |
| 独立消费者支持 | ✅ 保持 | ✅ 不影响 | ❌ 破坏 | ✅ 保持 |
| 维护成本 | 🟡 两包协调 | ✅ 单包自主 | ✅ 单包 | ❌ 三包协调 |
| 迭代速度 | 🟡 受上游限制 | ✅ 完全自主 | ✅ 完全自主 | 🟡 受公共库限制 |
| 实施成本 | 🔴 高（需泛化 tmux-bridge） | ✅ 低（~145 LOC） | 🟡 中（迁移消费者） | 🔴 高（新建包） |
| 长期扩展性 | ✅ 好 | 🟡 够用 | ✅ 好 | ✅ 最好 |
| **推荐场景** | 有活跃独立消费者 | **当前最佳选择** | 消费者可迁移时 | 三个以上消费方 |
