# tcd 架构文档

**版本**: v0.4.0 · **更新**: 2026-08-03 · **状态**: 权威文档

本文是 tcd 的需求、设计思路与技术架构的**唯一权威说明**。2026-03 的 v0.1.0 时期
文档（prd.md / design.md / scenarios.md 等）已删除，其中仍成立的内容已吸收进本文，
原文可在 git 历史中找回（见 [docs/README.md](README.md)）。

---

## 一、需求

### 1.1 要解决的问题

一个 AI 编码 agent 一次只能做一件事。要并行、要让不同模型各司其职，就需要一个能
**启动、监控、收割**其他 AI CLI 的调度层。

走 API 编排的代价是每次都要重传完整上下文（数千 token）；而 CLI 工具自己维护会话，
追加一条指令只要 50–200 token。**tcd 的存在理由就是把这个成本差利用起来**：不碰
API，驱动 CLI 本身。

一句话：**tmux 是总线，AI CLI 是工人，tcd 是调度器。**

### 1.1.1 为什么不用现成方案（2026-03 逐个否决，结论仍成立）

| 方案 | 否决理由 |
|---|---|
| claude_code_bridge (CCB) | 架构太重（daemon / TCP / worker pool），耦合紧，无法独立使用 |
| codex-orchestrator | 只支持 Codex，TypeScript 实现，驱动不了 Claude / Gemini |
| 直接调 API | 每次重传全量上下文，token 浪费严重；用不上 CLI 的文件操作能力 |
| 手工多终端 | 不可编程、无法自动化、人力成本高 |
| tmux-bridge（同仓姊妹项目） | 单任务驱动器，没有 job 队列 / provider 注册表 / 多轮会话；tcd 是它的上层 |

### 1.2 使用者

tcd 没有人类界面，它的用户是**另一个 agent**：

| 调用方 | 通过什么 | 典型场景 |
|---|---|---|
| Claude Code（人口头指派） | bash 调 CLI | 把后端任务派给 Codex，自己做前端 |
| auto-dev skill | bash 调 CLI | PRD → 并行 worktree 开发 → 集成验证 |
| ~~Python 编排脚本~~ | ~~`from tcd import TCD`~~ | SDK 已于 v0.6.0 删除，见 §4.1 |

这决定了两条设计约束：**输出必须机器可读**（`--json`），**退出码必须有语义**。

更一般地说，tcd 是**一个 agent 通过 tmux 调用另一个 agent 的通用机制**，用来解决
通用工程问题——不限于"多 AI 并行编程"这一个场景。**它是要给别人用的**（PRD 里
"个人工具级别""不做社区运营"两条已作废），所以接口稳定性和开箱可用的权重要高于
早期文档的设定。

### 1.3 不做什么

- 不做 agent（不写代码、不做判断，只搬运）
- 不管"任务是否完成"——只管"这一轮对话是否结束"（见 §3.4，这是个已知缺口）
- 不做跨机器调度、不做队列、不做鉴权
- 不做外部通知渠道（飞书等）——所有交互留在调用它的主 agent 里，这样别人装上就能用

---

## 二、设计思路：四个关键决策及其代价

架构里真正定性的是这四个选择。每个都有明确的收益和明确的代价，代价至今仍在付。

### 决策 1：用 tmux 驱动 TUI，而不是调 API 或用 headless 模式

**收益**：零 API 成本、复用 CLI 自己的会话与鉴权、任何有 TUI 的 AI CLI 都能接入、
进程与调用方解耦（调用方崩溃不影响任务）。

**代价**：**所有状态判断都退化成对屏幕文本的字符串匹配。** 就绪、忙碌、完成、
上下文超限、API 报错，全靠 pane 里有没有某个子串（`›`、`❯`、`Working (`、
`esc to interrupt`、`esc to cancel`）。上游 CLI 改一次文案，判断就**静默失效**——
不报错，而是变成"永远 working"或"立刻 idle"。

这是 tcd 最深的技术债，也是它能存在的原因，二者是同一件事。`tcd doctor` 是缓解
（让假设可检验），不是解决。

### 决策 2：worktree 隔离，而不是容器或目录拷贝

**收益**：并行任务天然隔离，产出以 git 分支形态交付，合并语义清晰。

**代价**：tcd 必须动调用方的 git 仓库——建 worktree、建分支、**stash 调用方未提交
的改动**。这一步把 tcd 从"只读的调度器"变成了"会改你工作区的工具"，2026-07 那批
被遗弃的 stash 就是代价兑现（见 [workflow-issues.md](workflow-issues.md)）。

由此产生一条**不可协商的不变量**：*凡在 start 阶段动了调用方环境的东西，必须在
所有终止路径上有明确处置*——merge、kill、clean、超时、崩溃，一条都不能漏。

### 决策 3：job 记录是文件，不是进程内状态

**收益**：无守护进程；任何一次 CLI 调用都能读到全量状态；调用方崩溃不丢信息。

**代价**：`~/.tcd/jobs/*.json` 是**写入时刻的快照**，而真相在别处（tmux 是存活性的
真相，git 是产出的真相）。快照必然漂移——实测出现过 54 个标记 running 的 job 对
9 个活会话，最老的一个自称"已运行 30 天"。

由此产生第二条不变量：*镜像外部进程的状态，读取时必须对账，不能相信写入时的快照*。

### 决策 4：Provider 抽象 + 注册表

**收益**：codex / claude / gemini 走同一套流程，加新 provider 只需实现 5 个方法。

**代价**：抽象是干净的，**能力覆盖却不是**。历史上每个修复都只打在踩到问题的那个
provider 上（v0.3.2 的四项启动加固只给了 codex），而基类默认值恰好是"重现所有已知
bug"。v0.4.0 把安全值提为基类默认，改成 provider **主动退出**保护而不是主动加入。

由此产生第三条不变量：*默认值必须是安全的那一侧；让新实现者被迫重新发现旧 bug 的
默认值等于没修*。

---

## 三、技术架构

### 3.1 分层

```
┌────────────────────────────────────────────────────────┐
│  调用方：Claude Code / auto-dev / Python 脚本            │
└───────────────┬────────────────────────┬───────────────┘
                │ CLI（退出码 + --json，唯一入口）
                ▼
┌────────────────────────────────────────────────────────┐
│  cli.py                                                 │
├────────────────────────────────────────────────────────┤
│  编排层                                                  │
│    job.py         JobManager：记录的读写、清理、资源持有     │
│    readiness.py   TUI 就绪等待、prompt 投递校验与重发       │
│    diagnostics.py 规则化告警（STALL / TURN0_STUCK / …）    │
│    doctor.py      检测假设的自检（静态 + 活体）             │
├────────────────────────────────────────────────────────┤
│  provider 层（ABC + 注册表）                              │
│    codex.py / claude.py / gemini.py                     │
│    各自负责：启动命令、prompt 包装、完成检测、响应解析        │
├────────────────────────────────────────────────────────┤
│  基础设施                                                 │
│    tmux_adapter.py  会话生命周期、send-keys、capture-pane  │
│    worktree.py      worktree / 分支 / stash + 仓库级锁     │
│    event_log.py     每 job 一份 append-only JSONL（见 §3.5）│
└────────────────────────────────────────────────────────┘
```

### 3.2 一个 job 的生命周期

```
start ──► 建 job 记录 ──► auto_stash(打 job id 标签) ──► 建 worktree
                                                          │
                     ┌────────────────────────────────────┘
                     ▼
            起 tmux 会话 ──► 等 TUI 稳定 ──► 投递 prompt ──► 校验投递
                     │
        ┌────────────┴────────────┬──────────────┐
        ▼                         ▼              ▼
   check(轮询)               send(追问)      output(取增量)
        │
        └──► 终止路径（**每条都必须处置资源**）
               merge  → 合并分支 → 删 worktree → 还 stash
               kill   → 杀会话 → 保留有产出的 worktree → 还 stash
               clean  → 删记录（持有资源的 job 会被跳过）
               崩溃    → 下次 `tcd jobs` 对账收尾
```

顺序不是随意的：**job 记录先于 stash 建立**，因为 stash 一旦产生就必须立刻有主；
中间任何失败都不能留下无人认领的 stash。

### 3.3 状态与真相来源

| 事实 | 真相在哪 | 记录在哪 | 如何对账 |
|---|---|---|---|
| 会话是否存活 | tmux | `job.status` | `tcd jobs` 用一次 `list-sessions` 批量核对 |
| 这一轮是否结束 | pane 文本 | `job.turn_state` | provider 的 `detect_completion` |
| 产出 | git 分支 / 工作区 | `worktree_branch` | `branch_has_new_commits` |
| 调用方被 stash 的改动 | git stash 栈 | `worktree_stash_ref` | 按 SHA 重新解析位置 |

stash 栈是**仓库级共享**且**位置寻址**的，所以并发 job 必须靠仓库级 `flock`
串行化"解析位置 → pop"，并用 job id 标签识别自己的条目，不能读栈顶。

### 3.5 事件日志

每个 job 一份 append-only JSONL（`~/.tcd/jobs/<id>.events.jsonl`），`tcd log <id>`
查看。设计意图：job.json 是**当前状态**，事件流是**发生过什么**——排查"它到底卡在
哪一步"时只有后者管用。

当前事件类型（以代码为准，`grep -ro 'emit([^,]*, "[^"]*"' src/`）：

| 阶段 | 事件 |
|---|---|
| 创建 | `job.created`、`job.stashed`、`job.worktree_created` |
| 启动 | `job.tui_ready` / `job.tui_timeout`、`job.prompt_sent`、`job.prompt_confirmed` / `job.prompt_resend` / `job.prompt_unconfirmed` |
| 运行 | `job.checked`、`job.turn_complete`、`job.message_submit_retry` |
| 收尾 | `job.killed`、`job.worktree_merged`、`job.worktree_removed` / `job.worktree_kept`、`job.stash_restored` / `job.stash_restore_failed`、`job.reconciled` |

### 3.4 完成语义：turn vs task

tcd 原本只有一个概念：**turn（一轮对话）**。`tcd check` 退出 0 = 这一轮空闲了。

但调用方要的是**任务完成**，二者不等价：agent 会自己 spawn 子代理、会写完测试就
停在空输入框。v0.5.0 起这个语义进入架构：

| 问题 | 命令 | 依据 |
|---|---|---|
| 这一轮结束了吗 | `tcd check` | pane 文本 / 信号文件 |
| **任务做完了吗** | **`tcd verify`** | **调用方声明的验收契约** |

契约在 `start` 时声明（`--require-file` / `--require-cmd` / `--require-commit`），
由 tcd 评估——**任何"完成条件能被写成判据"的任务，都不再需要人来确认**。

---

## 四、架构债与改进方向

按"改动收益 / 改动风险"排序。**这一节是本文的重点。**

### 4.1 CLI 与 SDK 是两份并行实现（**已解决**，v0.6.0 删除 SDK）

`cli.py` 和 `sdk.py` 曾各自实现 start / merge / kill / clean 的完整逻辑。每个 bug
都要**改两遍**，而它们真的漂移过：readiness 的修复曾只进了 SDK；`TCD.clean()` 绕过
了 CLI 的资源保护；SDK 的 kill 至删除时仍把 stash 恢复挂在 `worktree_path` 上，
CLI 早已修掉那条路径。

**已删除**（v0.6.0）。实际用法只有两条，都走 CLI；SDK 无人使用却是**行为不同的第二
套产品**——它没有验收契约、遇到脏仓库直接拒绝而不是 auto-stash、kill 的 stash 恢复
仍挂在 `worktree_path` 上、`jobs()` 不做 tmux 对账、`clean()` 绕过资源保护。留着它
等于留一条能绕过全部不变量的入口。

51 个 SDK 测试一并删除（约占当时 345 项的 15%），属于删除死路径。对外是
**breaking change**：`from tcd import TCD` 不再可用，机器可读接入改用
`tcd start --json` + `tcd check --json` + `tcd verify --json`。

> **2026-08-04 评审结论**（Codex gpt-5.6-sol xhigh 对 A–G 七条提案的对抗性评审）：
> 大部分提案**不值得现在做**，而评审在代码里找到的 5 个真实缺陷优先级高于全部提案，
> 已在 v0.6.0 修复（见 [workflow-issues.md](workflow-issues.md) 2026-08-04 段）。
> 下面各条的判断已按评审结论更新。

### 4.2 资源生命周期是"每个命令自己记得清理"（P1，方案已被推翻重来）

worktree / 分支 / stash / tmux 会话四种资源，清理逻辑散落在 start 的回滚块、
merge、kill、clean 五处。v0.4.0 把这五处补齐了，但**结构没变**——加第六条终止路径
时，还是要靠人记得。

**原方案（通用 `ResourceSet` + `released` 布尔 + 插件式释放器）已被评审否决**，理由
成立：
- `release_all(job, force)` 表达不了现有语义——merge 的 `--no-cleanup` 仍要还 stash、
  squash 与普通 merge 的分支策略不同、kill 默认必须保留脏 worktree、clean 根本不释放
  只跳过。用一个 `force` 抹平这些差异，等于把安全策略藏进释放器。
- `released: bool` 会制造**第二份真相**，直接违背不变量 3。现在的 `held_resources()`
  至少是查外部实态。

**修正后的方向**：`inspect_outstanding(job)`（每次向 tmux / git / stash 栈查实态，
job 字段只表示 owner claim）+ `settle_resources(job, intent)`，`intent` 区分
`START_ROLLBACK / MERGED / KILLED / RECOVER`。**获取侧的窗口比释放侧更危险**：
stash 已产生但 ref 未存、worktree 已建但字段未存——先修这个，再谈统一释放。

### 4.3 "任务完成"没有进入架构（**已实现**，v0.5.0 验收契约）

现状：tcd 只报 turn 空闲，验收全靠调用方约定。结果是同一套"检查交付物"的逻辑在
codex-worker skill、auto-dev 里各写了一遍，且都可能被 LLM 跳过。

**实现**（v0.5.0）：调用方在 `start` 时声明契约——`--require-file`（文件必须存在）、
`--require-cmd`（命令必须退出 0）、`--require-commit`（分支必须有超出 HEAD 的 commit）。
`tcd verify <job>` 给出判定（退出码 0=complete / 1=incomplete / **2=没声明契约** /
3=not found），`tcd check --json` 在 idle 时附带 `task_state` 与逐条 `checks`。

关键取舍：**"没有契约"是独立的退出码 2，不是 0**。如果没声明也返回成功，这个能力就
会变成又一个"看起来验过了"的假象——正是它要消灭的东西。

**为什么是 P0**：作者确认的第一性假设是"**凡是可以自我验证的任务，AI 就能自动完成，
不需要人参与**"。验收契约就是这条假设在底座层的实现——没有它，"闭环"只是"跑完了"，
而跑完但不敢用，人还是要回来检查，注意力照样被拉走。

Codex 的 `--output-schema` 已经提供了一半（约束最终产出符合 JSON Schema），不用自研。

### 4.4 检测层与 TUI 文案强耦合（P2 观察项，长期风险）

§2 决策 1 的代价。`tcd doctor` 只能告诉你"假设可能失效了"，无法自愈。

**方向（已调研，见
[research/2026-08-structured-output-vs-screen-scraping.md](research/2026-08-structured-output-vs-screen-scraping.md)）**：
不是"tmux vs headless"，而是**tmux 继续做进程托管、结构化模式做协议**——在 tmux
会话里跑 CLI 的结构化模式，读 JSONL 事件流而不是抓屏。tcd 现在抓屏推断的每一件事
（turn 结束、上下文超限、API 报错、活动行）在三家 CLI 里都已有一等事件。

**决定（2026-08-03）：交互式仍是默认，结构化作为辅助路径，未来验证后再考虑提权。**

理由是 `codex exec` 会自动取消 MCP 工具调用
（[openai/codex#24135](https://github.com/openai/codex/issues/24135)，未修复）——
stdin 关闭导致审批弹窗无人应答，调用被静默取消；唯一绕法是
`--dangerously-bypass-approvals-and-sandbox`，等于连沙箱一起关。而 tcd 特意保留了
`context7` MCP 供 headless 编码查库文档。**换句话说，主力路径（codex）恰好是受阻
最重的那个**，此时把结构化设为默认没有收益只有风险。

所以：

- **默认不变**：交互式 TUI + 现有检测，配 `tcd doctor` 让假设可检验
- **结构化作为可选路径**先在 gemini / claude 上试（它们无此阻塞，且恰好是目前支持
  最弱的两个），验证事件流解析这套架构是否可靠
- 上游修复 #24135、或试点结果足够好之后，再讨论是否提为默认

这条因此**不是近期工作项**，而是一个有明确触发条件的观察项。

### 4.5 Provider 能力靠 duck-typing 发现（**不单独立项**）

`hasattr(prov, "check_cli")`、`getattr(prov, "supports_sandbox", False)`、
`hasattr(type(provider), "has_queued_message_notice")`——可选能力散落在调用点用
反射发现。新 provider 漏实现不会报错，只会**静默少一层保护**，正是 §2 决策 4 的
代价没有被架构挡住。

**评审结论：不值得单开一次重构。** 主要目标其实已经达成——`tui_stable_secs`、
`verify_prompt_delivery`、`working_markers`、`supports_sandbox` 四个安全关键项都已有
基类默认（`provider.py:70-100`），测试也钉住了。剩余反射只有 `check_cli` 和 Claude
特有的 queued-message notice，都不是事故来源。**将来碰 provider 层时顺手补两个基类
默认即可**，不要引入 `Capabilities` 对象/枚举/feature registry。

### 4.6 并发只在 stash 层被治理（P2，改为 D-lite）

v0.4.0 加了仓库级 `flock` 保护 stash 栈，但更上层没有并发模型：两个 job 指向同一
仓库、同一分支名、同一 worktree 路径时，没有统一的冲突检测；`--wt-name` 撞名只会
在 `git worktree add` 时报错。

**评审修正**：查 job 记录再创建仍是 TOCTOU（两个 start 可同时查到空闲），而且互斥键
不是"同一仓库"（同仓多 worktree 正是核心功能），而是 **canonical common repo +
精确 branch/path**。git 本身已经对重复 branch/path 做原子拒绝，job 记录只能作为
"可能属于哪个 job"的提示，不能作为放行真相——否则违背不变量 3。

**D-lite**：①仓库锁身份规范化（**已于 v0.6.0 修复**）；②stash 之前做无副作用预检，
冲突时给出清楚的错误和占用者提示；③真正的裁决仍交给 git。不引入通用 reservation。

### 4.7 文档结构本身（P2）

16 份文档 ~5900 行，全部写于 2026-03 的 v0.1.0 时期，无索引，与 v0.4.0 的实际行为
多处冲突。**过时的设计文档比没有文档更危险**——它会让下一个人（或下一个 agent）
按错误的模型改代码。本文与 [docs/README.md](README.md) 是第一步整改。

---

## 五、不变量清单

改动 tcd 时，下面每一条都不能破坏。它们各自对应一次真实事故。

1. **start 阶段动了调用方环境的东西，所有终止路径都要处置**（merge/kill/clean/超时/崩溃）
2. **记了 id 就用 id 操作**——任何"栈顶 / 最近一个 / 默认那个"在并发下都是错的
3. **镜像外部进程的状态，读取时必须对账**，不能信写入时的快照
4. **接受了的参数要么生效要么报错**，不能静默忽略（更不能还打印一行确认）
5. **默认值取安全侧**，让 provider 主动退出保护，而不是主动加入
6. **turn 结束 ≠ 任务完成**，任何"完成"判断都要落到交付物上
7. **检测用的字符串是版本耦合的**，改动前先跑 `tcd doctor`
