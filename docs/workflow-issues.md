# 工作流问题分析报告

**日期**: 2026-03-05
**来源**: Codex Code Review & Fix 工作流（2026-03-05 11:10–11:50）
**涉及 Job**: ece8b9e3, 9fc1e82d, 8d45037f, 8e6c6b37

---

## 问题总览（按优先级排序）

| 优先级 | 问题 | 状态 |
|--------|------|------|
| P0 | M-6: `--sandbox` 参数是死代码，未传入 provider | 已确认，待修复 |
| P0 | Codex 自动更新中断正在运行的任务 | 未解决 |
| P1 | `tcd wait` 阻塞 Claude Code 进程，用户无进展反馈 | Skill 已更新（待验证） |
| P1 | 缺少前置写权限检查，修复任务白白消耗 token | 未解决 |
| P2 | review 任务 vs 修复任务沙箱模式不可区分 | 未解决 |
| P2 | 非 git 仓库无法用 git diff 检查 Codex 改动 | 未解决 |
| P2 | Codex 路径空格导致 shell 命令失败 | Codex 自行规避 |
| P3 | Skill 轮询模式实际效果未验证 | 待观察 |

---

## 详细分析

---

### P0-1: M-6 — `--sandbox` 参数是死代码

**问题描述**
`tcd start --sandbox workspace-write` 命令接受了 `--sandbox` 参数，但该参数未传入 Codex provider 的启动命令，导致 Codex 始终以默认沙箱模式运行（只读）。

**根因分析**
经代码确认，这是一个**已修复的历史 bug**。查看当前 `src/tcd/providers/codex.py` 第 153-155 行：

```python
# sandbox mode (default: workspace-write)
sandbox = job.sandbox or "workspace-write"
parts.append(f"-s {sandbox}")
```

`job.sandbox` 已被正确读取并传入 `-s` 参数。

然而在工作流执行时（2026-03-05 11:30），job `8e6c6b37` 仍然报告"只读沙箱"。可能的原因：
1. 工作流执行时代码尚未修复（M-6 是此次 review 要修的 bug 之一）
2. 或 Codex CLI 的 `-s workspace-write` 参数实际未生效（需验证 codex 版本行为）

**影响评估**
- 严重程度：**阻塞级**（修复任务完全无法执行）
- 频率：100%（每次修复任务都触发）
- 波及：4 次修复尝试全部失败，累计浪费约 80k tokens

**已有解决方案**
当前代码（codex.py L154）已包含 sandbox 传参逻辑。需验证：
1. 修复是否已生效（重新安装 `uv tool install .`）
2. `codex -s workspace-write` 参数格式是否正确（codex v0.110.0 可能变更了参数格式）

**推荐改进方案**
1. 在 `build_launch_command` 中加日志：`logger.info("sandbox=%s, cmd=%s", sandbox, inner_cmd)`
2. `tcd start` 完成后输出实际启动命令（debug 模式），便于验证参数是否正确传入
3. 编写测试：`test_codex_provider.py::test_sandbox_flag_included_in_command`
4. 在 `tcd start` 输出中显示沙箱模式：`Sandbox: workspace-write`

**优先级**: P0

---

### P0-2: Codex 自动更新中断任务

**问题描述**
Job `9fc1e82d` 启动后，Codex CLI 自动更新从 v0.106.0 到 v0.110.0，进程重启，导致任务中断。`tcd wait` 超时（15 分钟），任务以 `killed by user` 失败（`turn_state: working`，`turn_count: 0`）。

**根因分析**
Codex CLI 在检测到新版本时会在 TUI 内弹出更新提示并自动执行更新。tcd 的 `_wait_for_tui()` 只处理了信任对话框（trust dialog），未处理更新重启场景。更新后进程重启，原 tmux session 内的 Codex 实例可能状态不一致，notify-hook 未被触发，信号文件永远不会写入，完成检测失效。

**影响评估**
- 严重程度：**高**（导致任务完全失败，无输出）
- 频率：不可预测（取决于 Codex 发版频率，活跃开发期可能每周触发）
- 波及：浪费约 5k tokens（本次），可能导致后续重试成本倍增

**已有解决方案**
无。

**推荐改进方案**

方案 A（推荐）：固定 Codex 版本，禁用自动更新
```bash
# 使用 npm 安装固定版本
npm install -g @openai/codex@0.110.0
```
或在启动命令中添加环境变量禁用更新检查（需调研 Codex 是否支持 `CODEX_DISABLE_UPDATE_CHECK` 等变量）。

方案 B：在 `_wait_for_tui()` 中检测更新提示并处理
在 `sdk.py:_wait_for_tui()` 和 `cli.py:start()` 中增加对更新提示的检测：
```python
update_phrases = ["A new version", "Updating", "Restarting after update"]
if any(phrase in pane for phrase in update_phrases):
    time.sleep(5)  # 等待更新完成
    trust_handled = True  # 重置，等待重启后的 TUI
    continue
```

方案 C：`tcd check` 增加"更新检测"状态
检测到 Codex 更新重启时，自动重新注入 prompt。

**优先级**: P0

---

### P1-1: `tcd wait` 阻塞导致用户无反馈

**问题描述**
调用 `tcd wait <job_id>` 后，Claude Code 进程在一个 Bash 调用中阻塞，用户在等待期间（最长达 15 分钟）看不到任何进展输出。

**根因分析**
`tcd wait` 是一个阻塞式 while 循环（`cli.py:280-312`）：
```python
while time.time() < deadline:
    ...
    time.sleep(poll_interval)
```
当作为子进程在 Claude Code 的 Bash 工具中调用时，整个 Bash 调用被阻塞，Claude Code 无法在等待期间向用户输出任何内容。

**影响评估**
- 严重程度：**中**（用户体验差，不阻塞功能）
- 频率：每次使用旧 Skill 时触发
- 波及：2 次任务（ece8b9e3 5分钟，9fc1e82d 15分钟）

**已有解决方案**
codex-worker Skill 已更新为**轮询模式**：禁止使用 `tcd wait`，改为每 15 秒一次独立 Bash 调用（`tcd check` + `tcd output | tail -30`），并在两次调用之间向用户输出进展摘要。

**推荐改进方案**
1. Skill 更新已覆盖此场景（见 SKILL.md Step 2）
2. 在 `tcd wait` 命令添加警告：`"Warning: tcd wait blocks the caller. Use tcd check in a loop for interactive use."`
3. 可选：在 `tcd wait` 中添加 `--progress` 标志，定期向 stderr 输出进度（`elapsed: 30s, state: working`）

**优先级**: P1

---

### P1-2: 缺少前置写权限检查，重复浪费 token

**问题描述**
修复任务启动前未验证 Codex 是否有写权限，导致 Codex 读完代码、分析完毕后才发现无法写文件，4 次重试共浪费约 80k tokens。

**根因分析**
当前工作流：启动 → 注入提示词 → Codex 读代码（20-40k tokens）→ 准备写文件 → 发现权限拒绝 → 报错退出。

缺少前置检查步骤：在注入修复任务提示词之前，先验证 Codex 能否在目标目录写文件。

**影响评估**
- 严重程度：**高**（资金损失，token 费用）
- 频率：每次 sandbox 配置错误时必然触发
- 波及：3 次修复任务 × 约 25k tokens = 75k tokens 浪费

**已有解决方案**
无。

**推荐改进方案**

方案 A（推荐）：在 codex-worker Skill 中添加前置检查步骤
在 Step 1（启动任务）前插入验证步骤：
```bash
# 验证写权限（发送探针提示词）
tcd start -p codex -m "执行：touch .tcd-write-probe && echo OK || echo READONLY" -d <dir>
# 检查输出，确认包含 OK 再继续正式任务
```

方案 B：`tcd start` 添加 `--verify-write` 标志
在 provider 启动后、注入正式提示词前，先注入一个写权限探针命令，验证通过后再注入真实 prompt。

方案 C：在提示词中前置声明（最轻量）
在修复类提示词开头加：
```
首先，执行 touch .tcd-probe 验证写权限。如果失败（operation not permitted），立即停止并报告，不要继续读代码。
```

**优先级**: P1

---

### P2-1: review 任务 vs 修复任务沙箱模式混用

**问题描述**
Code review 任务只需读权限（只读沙箱即可），但修复任务必须有写权限（`workspace-write`）。当前 tcd 没有任务类型概念，编排者需要手动为修复任务指定 `--sandbox workspace-write`，容易遗漏。

**根因分析**
`tcd start` 的 `--sandbox` 是可选参数，默认值由 provider 决定（codex.py L154: `job.sandbox or "workspace-write"`）。当前默认值已是 `workspace-write`，但在修复 M-6 之前，该默认值未生效。即使修复后，编排者仍需手动区分任务类型。

**影响评估**
- 严重程度：**中**（误配置会导致任务失败）
- 频率：低（仅在工作流设计不当时触发）

**已有解决方案**
codex-worker Skill 的"注意事项"中已提及 `workspace-write` 沙箱模式，但未明确区分两种任务。

**推荐改进方案**
在 codex-worker Skill 中明确区分两种任务模式：
- **review 模式**：提示词包含"不要修改代码"，`--sandbox read-only`（如 Codex 支持）
- **修复模式**：默认 `workspace-write`，并在 Skill 中强调

在 `tcd start` 输出中显示实际沙箱模式：
```
Job started: 8e6c6b37
Provider: codex
Sandbox: workspace-write
tmux session: tcd-codex-8e6c6b37
```

**优先级**: P2

---

### P2-2: 非 git 仓库无法用 git diff 检查 Codex 改动

**问题描述**
编排者想通过 `git diff` 检查 Codex 做了哪些修改，但项目目录不是 git 仓库，命令失败。

**根因分析**
项目目录 `/Users/michael/projects/AI 工作流/tmux-codingagent-driver` 没有 `.git` 目录（或不在 git tracking 范围内）。Codex 的 `parse_response_structured()` 方法返回 `files_modified` 列表（通过 NDJSON `apply_patch` 事件），但该信息未暴露给编排者。

**影响评估**
- 严重程度：**低**（不影响任务执行，只影响验证）
- 频率：中（任何非 git 项目都会遇到）

**推荐改进方案**
1. 使用 `tcd` 的结构化输出获取改动文件列表：
   ```bash
   # 通过 Python SDK
   from tcd.providers.codex import CodexProvider
   output = prov.parse_response_structured(job)
   print(output.files_modified)
   ```
2. 在 `tcd output` 中添加 `--files-modified` 标志，显示 Codex 修改的文件列表
3. 对改动文件做内容 hash 比对（修改前后），替代 git diff
4. 在 codex-worker Skill 中提示：验证改动时，优先用 `tcd output --files-modified`，git diff 作为补充

**优先级**: P2

---

### P2-3: Codex 路径空格导致 shell 命令失败

**问题描述**
Codex 执行 shell 命令时，项目路径（`/Users/michael/projects/AI 工作流/tmux-codingagent-driver`）中的中文空格导致命令失败。

**根因分析**
Shell 命令中未引用的路径遇到空格会被分割为多个参数。这是 Codex 生成 shell 命令时的典型问题。

**影响评估**
- 严重程度：**低**（Codex 有时能自行修正）
- 频率：中（所有含空格路径的项目都可能触发）

**已有解决方案**
本次工作流中 Codex 自行发现并用引号规避了此问题。

**推荐改进方案**
1. 在 codex-worker Skill 的提示词模板中加入路径引用提示
2. `tcd start` 的 `-d` 参数传入前对路径加引号（`shlex.quote(cwd)`）
3. 长期：项目目录避免含空格（最根本解法）

**优先级**: P2

---

### P3: Skill 轮询模式实际效果待验证

**问题描述**
更新后的 codex-worker Skill 要求每次轮询是独立的 Bash 调用。但另一个独立运行的 Claude Code 进程是否会严格按新 Skill 执行尚未验证。

**根因分析**
Claude Code 读取 Skill 后，执行策略由模型决定。Skill 中的关键约束（"每次轮询必须是独立的 Bash 调用"）依赖模型遵从文本指令，不是强制约束。在高负载或 context 较长时，模型可能"退化"为 while 循环。

**影响评估**
- 严重程度：**低**（仅影响用户体验）
- 频率：不确定

**推荐改进方案**
1. 下次使用 codex-worker Skill 时，观察 Claude Code 是否按轮询模式执行
2. 如果发现退化，在 Skill 中加更强的约束语言：`"CRITICAL: Never use while loops or tcd wait."`
3. 长期：考虑在 `tcd check` 中添加 `--watch` 模式，自动输出进度到 stdout（规避 Bash 阻塞问题）

**优先级**: P3

---

## 根因链

```
M-6（sandbox 参数未传入）
    → Codex 以只读沙箱运行
    → review 任务无法写文件（影响可接受）
    → 修复任务无法写文件（阻塞器）
        → 重试 3 次（每次 ~25k tokens）
            → 80k tokens 浪费
                → 前置检查缺失是放大因子
```

---

## 行动项

### 立即执行（P0）

- [ ] 验证 M-6 修复是否生效：`tcd start --sandbox workspace-write`，确认 codex 以 `workspace-write` 模式运行
- [ ] 验证 `codex -s workspace-write` 参数格式（codex v0.110.0 changelog）
- [ ] 调研 Codex 自动更新禁用方法（环境变量 / npm 固定版本）

### 短期（P1，本周）

- [ ] 在修复类提示词开头加写权限探针：`touch .tcd-probe && echo OK || echo READONLY && exit`
- [ ] 在 `tcd start` 输出中显示实际沙箱模式
- [ ] 为 codex provider 添加启动命令日志（debug 级别）

### 中期（P2，下个迭代）

- [ ] `tcd output --files-modified`：显示 Codex 修改的文件列表
- [ ] codex-worker Skill：明确区分 review 模式和修复模式
- [ ] 编写测试：`test_sandbox_flag_in_command`

---

## 参考

- 相关 Job 记录：`~/.tcd/jobs/ece8b9e3.json`（review），`8e6c6b37.json`（第三次修复尝试）
- 工作流日志：`docs/workflow-log.md`
- Codex provider 源码：`src/tcd/providers/codex.py`
- Codex Worker Skill：`~/.claude/skills/codex-worker/SKILL.md`
