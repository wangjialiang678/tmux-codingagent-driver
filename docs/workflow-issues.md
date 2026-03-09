# 工作流问题分析报告

**日期**: 2026-03-05
**来源**: Codex Code Review & Fix 工作流（2026-03-05 11:10–11:50）
**涉及 Job**: ece8b9e3, 9fc1e82d, 8d45037f, 8e6c6b37

---

## 问题总览（按优先级排序）

| 优先级 | 问题 | 状态 |
|--------|------|------|
| P0 | M-6: `--sandbox` 参数是死代码，未传入 provider | 已修复（codex.py + start 输出显示 sandbox） |
| P0 | Codex 自动更新中断正在运行的任务 | 未解决（外部问题） |
| P1 | `tcd wait` 阻塞 Claude Code 进程，用户无进展反馈 | Skill 已更新（待验证） |
| P1 | 缺少前置写权限检查，修复任务白白消耗 token | 未解决（Skill 层改进） |
| P2 | review 任务 vs 修复任务沙箱模式不可区分 | 未解决 |
| P2 | 非 git 仓库无法用 git diff 检查 Codex 改动 | 未解决 |
| P2 | Codex 路径空格导致 shell 命令失败 | Codex 自行规避 |
| P3 | Skill 轮询模式实际效果未验证 | 待观察 |
| **P0** | **`tcd merge` worktree 不存在时 FileNotFoundError + 假成功**（#2） | **已修复（2026-03-09）** |
| P1 | `worktree_repo_root` 字段未持久化到 job JSON（#2） | 已确认为版本问题，代码正确 |
| P2 | Job 完成后 status 仍为 "running"（#2） | **已修复（merge 后自动更新 status）** |
| P2 | `tcd merge` 成功消息与实际结果不一致（#2） | **已修复（MergeResult + noop 检测）** |
| **P0** | **AI 在 worktree 中不 commit，merge 时 noop**（#3） | **已修复（Skill prompt + merge pre-check, 2026-03-09）** |
| P1 | 自举场景：用旧版 tcd 修 tcd 自身（#3） | **已缓解（Skill 自举警告, 2026-03-09）** |
| P2 | STALL/TURN0_STUCK 误报（AI 在生成长文本时）（#3） | **已修复（pane_hash 检测, 2026-03-09）** |
| P2 | `tcd merge` 无法区分"无 commit"和"已 merge"（#3） | **已修复（branch_has_new_commits pre-check, 2026-03-09）** |

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

---
---

# 工作流问题分析报告 #2

**日期**: 2026-03-08
**来源**: feishu-cli auto-dev 工作流（2026-03-08 22:40–23:30）
**场景**: 使用 tcd 并行 worktree 模式为 feishu-cli 项目开发 8 个新 CLI 功能
**涉及 Job**: 3b18e3c1（Group A: export+import）, e76f3bcb（Group B: copy+move+folder）, 16370815（Group C: bitable-search+bitable-delete）, 4af58644（Group D: wiki-spaces）

---

## 问题总览（按优先级排序）

| 优先级 | 问题 | 状态 |
|--------|------|------|
| P0 | `tcd merge` 在 worktree 目录不存在时报 FileNotFoundError，但误报合并成功 | 未解决 |
| P1 | `tcd merge` cleanup 阶段 `remove_worktree` 找不到已消失的 worktree 目录 | 未解决（P0 的子问题） |
| P1 | `worktree_repo_root` 字段未持久化到 job JSON | 待确认 |
| P2 | Job 完成后 status 仍为 "running"，`completed_at` 为 null | 未解决 |
| P2 | `tcd merge` 成功消息与实际结果不一致（用户被误导） | 未解决 |

---

## 详细分析

---

### P0-3: `tcd merge` FileNotFoundError + 假成功

**问题描述**

在 feishu-cli 项目（路径 `/Users/michael/projects/组件模块/feishu-cli`）中，使用 `tcd start --worktree` 启动 4 个并行 Codex 任务。所有任务编码完成并提交到各自的 `tcd/<job_id>` 分支。之后执行 `tcd merge <job_id>` 时：

1. 命令输出了 `"Merged tcd/3b18e3c1 (merge)."` 成功消息
2. 紧接着抛出 `FileNotFoundError: No such file or directory` 指向 worktree 路径
3. **实际检查 main 分支发现：代码并未被合并**
4. 手动执行 `git merge --no-ff tcd/3b18e3c1` 才真正完成合并

4 个 Job 全部复现此问题，100% 触发率。

**复现步骤**

```bash
# 1. 在含中文路径的 repo 中启动 worktree 任务
cd /Users/michael/projects/组件模块/feishu-cli
tcd start -p codex -m "..." --worktree
# Job: 3b18e3c1, worktree: /Users/michael/projects/组件模块/feishu-cli-wt-3b18e3c1

# 2. 等待 Codex 完成编码（status 检查显示 turn_state: idle）

# 3. 执行 merge
tcd merge 3b18e3c1
# 输出: "Merged tcd/3b18e3c1 (merge)."
# 然后: FileNotFoundError: [Errno 2] No such file or directory: '/Users/michael/projects/组件模块/feishu-cli-wt-3b18e3c1'

# 4. 检查 main 分支 — 代码未合并
git log --oneline -3  # 看不到 merge commit

# 5. 手动 merge 成功
git merge --no-ff tcd/3b18e3c1  # 正常合并，无冲突
```

**根因分析**

问题出在 `cli.py:693-737` 的 `merge()` 函数，分两层：

**层 1：`repo_root` 计算可能指向错误目录**

```python
# cli.py L708
repo_root = Path(job.worktree_repo_root) if job.worktree_repo_root else get_main_repo_root(job.cwd)
```

- `job.worktree_repo_root`：在 job JSON 中**缺失**（见下方 P1-3），导致走 fallback 路径
- `get_main_repo_root(job.cwd)` 中 `job.cwd` = worktree 路径 `/Users/michael/projects/组件模块/feishu-cli-wt-3b18e3c1`
- 如果 worktree 目录已被清理，`subprocess.run(cwd=str(path))` 会抛 `FileNotFoundError`
- 如果 worktree 目录仍存在但 git 状态异常，`get_main_repo_root` 可能返回错误的 repo root
- `merge_branch()` 在错误的 repo root 下执行 `git merge`，可能是 no-op（already up to date），returncode=0，误报成功

**层 2：cleanup 阶段缺少防御**

```python
# cli.py L723-725
if not no_cleanup and job.worktree_path:
    try:
        remove_worktree(job.worktree_path)  # FileNotFoundError 在这里抛出
```

`remove_worktree()` 在 `worktree.py:124-127` 中：
```python
def remove_worktree(worktree_path):
    wt = Path(worktree_path)
    if not wt.exists():
        return  # 这行应该防御了，但 FileNotFoundError 仍然抛出
```

说明 `wt.exists()` 返回 True（目录存在）但后续的 `subprocess.run(cwd=str(wt))` 时目录被并发删除，或者 `common_dir_result` 的 `subprocess.run` 的 `cwd` 解析失败。

**实际 Job 数据证据**

从 `~/.tcd/jobs/3b18e3c1.json` 可以看到：

```json
{
  "cwd": "/Users/michael/projects/组件模块/feishu-cli-wt-3b18e3c1",
  "worktree_path": "/Users/michael/projects/组件模块/feishu-cli-wt-3b18e3c1",
  "worktree_branch": "tcd/3b18e3c1",
  "status": "running",          // ← 应该是 completed
  "completed_at": null           // ← 应该有时间戳
  // 注意：没有 worktree_repo_root 字段！
}
```

**影响评估**
- 严重程度：**阻塞级**（merge 假成功导致用户以为代码已合并，实际未合并）
- 频率：100%（所有 4 个 job 全部触发）
- 波及：需要手动 `git merge --no-ff` 补救，增加约 10 分钟手动操作。如果用户未手动检查，可能导致代码丢失

**推荐修复方案**

方案 A（推荐，分 3 步）：

1. **修复 `repo_root` 计算逻辑**（`cli.py:708`）：

```python
# 优先用 worktree_repo_root（主仓库原始路径）
if job.worktree_repo_root:
    repo_root = Path(job.worktree_repo_root)
elif job.worktree_path and Path(job.worktree_path).exists():
    repo_root = get_main_repo_root(job.worktree_path)
else:
    # worktree 已不存在，尝试从 branch name 推断原始 repo
    # 或者直接报错让用户手动 merge
    click.echo(f"Error: worktree at {job.worktree_path} no longer exists.", err=True)
    click.echo(f"Manual merge: git merge --no-ff {job.worktree_branch}", err=True)
    sys.exit(1)
```

2. **合并后验证**（`cli.py:719` 后添加）：

```python
# 验证 merge 确实生效：检查 branch 的 HEAD 是否是当前 HEAD 的祖先
verify = subprocess.run(
    ["git", "merge-base", "--is-ancestor", job.worktree_branch, "HEAD"],
    cwd=str(repo_root), capture_output=True
)
if verify.returncode != 0:
    click.echo(f"Warning: merge may not have taken effect. Verify with: git log --oneline -5", err=True)
```

3. **cleanup 防御加强**（`worktree.py:remove_worktree`）：

```python
def remove_worktree(worktree_path):
    wt = Path(worktree_path)
    if not wt.exists():
        return  # 已不存在，静默返回
    try:
        # ... 现有逻辑 ...
    except (FileNotFoundError, WorktreeError):
        # worktree 在检查后被并发删除，忽略
        logger.warning("Worktree %s disappeared during cleanup", wt)
```

方案 B（最小改动应急）：

在 `merge()` 函数开头就计算并验证 `repo_root`，如果无法获取则提示手动命令后退出：

```python
def merge(job_id, squash, no_cleanup):
    # ... load job ...

    # 计算 repo_root，优先用持久化的原始路径
    repo_root = None
    if job.worktree_repo_root:
        repo_root = Path(job.worktree_repo_root)
    else:
        # Fallback: 从 worktree_path 推导
        wt = Path(job.worktree_path) if job.worktree_path else None
        if wt and wt.exists():
            repo_root = get_main_repo_root(str(wt))
        elif job.cwd and Path(job.cwd).exists():
            repo_root = get_main_repo_root(job.cwd)

    if repo_root is None or not repo_root.exists():
        click.echo(f"Error: cannot determine repo root. Worktree may be deleted.", err=True)
        click.echo(f"Run manually: git merge --no-ff {job.worktree_branch}", err=True)
        sys.exit(1)
```

**优先级**: P0

---

### P1-3: `worktree_repo_root` 字段未持久化到 Job JSON

**问题描述**

`Job` dataclass 在 `job.py:57` 定义了 `worktree_repo_root: str | None = None`，且 `cli.py:142` 在创建 worktree 后正确设置了 `job.worktree_repo_root = cwd`。但实际保存的 job JSON 中此字段**缺失**。

**证据**

4 个 job 的 JSON 文件中都没有 `worktree_repo_root` 字段：

```bash
# 检查所有 4 个相关 job
grep -l "worktree_repo_root" ~/.tcd/jobs/{3b18e3c1,e76f3bcb,16370815,4af58644}.json
# 无输出 — 字段不存在
```

但 `Job.to_dict()` 使用 `dataclasses.asdict()` 应该序列化所有字段，包括值为 None 的。

**根因分析**

两种可能：

1. **版本不一致**：用户通过 `uv tool install .` 安装了 tcd，但安装的版本可能早于添加 `worktree_repo_root` 字段的提交。源码已有此字段，但运行时的 `tcd` 可执行文件是旧版本。这意味着 `cli.py:142` 中的 `job.worktree_repo_root = cwd` 实际上只是在运行时对象上设了一个非 dataclass 字段的属性，`asdict()` 不会序列化它。

2. **`save_job` 时序问题**：`worktree_repo_root` 在 `save_job` 之前被设置（`cli.py:142-149`），所以理论上应该被保存。如果是旧版本问题，则 `save_job` 调用的 `to_dict()` 不包含此字段。

**验证方法**

```bash
# 检查安装的 tcd 版本是否包含 worktree_repo_root
python3 -c "from tcd.job import Job; print('worktree_repo_root' in Job.__dataclass_fields__)"

# 检查源码版本
grep worktree_repo_root src/tcd/job.py
```

**影响评估**
- 严重程度：**高**（直接导致 P0-3，merge 时无法找到正确的 repo root）
- 频率：100%（如果确实是版本问题，所有 worktree job 都受影响）

**推荐修复方案**

1. 确认安装版本：`tcd --version` vs `git log --oneline -1 src/tcd/job.py`
2. 如果版本不一致：重新安装 `uv tool install . --force`
3. 添加版本校验：在 `tcd start --worktree` 中打印 job 保存后的字段列表（debug 级别），确认 `worktree_repo_root` 被序列化
4. 长期：在 `merge()` 函数中添加对 `worktree_repo_root is None` 的明确警告

**优先级**: P1

---

### P2-4: Job 完成后 status 仍为 "running"

**问题描述**

4 个 Codex job 全部完成编码并提交了 commit，但 job JSON 中 `status` 仍然是 `"running"`，`completed_at` 为 `null`。

**证据**

```json
// ~/.tcd/jobs/3b18e3c1.json
{
  "status": "running",
  "completed_at": null,
  "turn_count": 1,
  "turn_state": "idle",
  "last_agent_message": "Implemented scripts/export.js and scripts/import.js..."
}
```

`turn_state: "idle"` 和有效的 `last_agent_message` 表明 Codex 确实完成了任务，但 job 状态未被更新为 `"completed"`。

**根因分析**

`tcd start` 命令中的 wait 循环（`cli.py` 的 start 函数）负责检测任务完成并更新状态。可能的原因：

1. `tcd start` 命令的 wait 阶段在检测到完成之前就被外部中断（调用者 Ctrl-C 或 timeout）
2. 完成信号文件（`.tcd/jobs/<id>.turn-complete`）的检测逻辑与 Codex 的实际完成信号不匹配
3. 后台运行的 `tcd start` 进程在 Claude Code 上下文压缩后丢失

**影响评估**
- 严重程度：**中**（不影响合并，但导致 `tcd status` 显示不准确，且 auto-cleanup 逻辑不会触发）
- 频率：需进一步调查（可能与 tcd 被作为后台进程调用有关）

**推荐修复方案**

1. `tcd merge` 和 `tcd output` 中检测到 `turn_state == "idle"` 且 `last_agent_message` 非空时，自动将状态更新为 `"completed"`
2. `tcd check` 命令增加对实际完成但状态未更新的检测（从 tmux session 状态推断）
3. 添加 `tcd fix-status <job_id>` 子命令，手动触发状态修正

**优先级**: P2

---

### P2-5: `tcd merge` 成功消息与实际结果不一致

**问题描述**

`tcd merge` 输出 `"Merged tcd/3b18e3c1 (merge)."` 但代码并未实际合并到 main 分支。用户收到成功消息后认为合并完成，直到后续操作发现代码缺失才意识到问题。

**根因分析**

`cli.py:711` 中 `merge_branch()` 返回 `True`（`git merge` returncode=0），但可能的情况：
- `git merge` 在错误的 `repo_root` 下执行，结果是 "Already up to date"（returncode=0 但无实际合并）
- 或者 merge 确实在某个目录下成功了，但那不是用户期望的 main 分支

`merge_branch()` 只检查 returncode，不验证是否真的产生了 merge commit：

```python
# worktree.py:181-187
result = subprocess.run(cmd, cwd=str(repo_path), capture_output=True, text=True)
return result.returncode == 0  # "Already up to date" 也返回 0！
```

**推荐修复方案**

1. `merge_branch()` 检查 stdout 是否包含 "Already up to date"，如果是则返回特殊状态
2. merge 成功后验证：检查 `git log -1 --format=%H` 是否变化
3. 输出更详细的信息：`"Merged tcd/xxx (merge): 3 files changed, 204 insertions(+)"`

**优先级**: P2

---

## 根因链

```
worktree_repo_root 未持久化（P1-3，可能是版本问题）
    → merge() 无法获取原始 repo 路径
    → fallback 到 get_main_repo_root(job.cwd)
    → job.cwd 指向 worktree 路径（可能已不存在或状态异常）
        → 情况 A：目录不存在 → FileNotFoundError
        → 情况 B：目录存在但 repo_root 计算错误 → git merge 在错误目录执行
            → "Already up to date" → returncode=0 → 误报成功
                → 用户以为合并完成，实际代码未合并
    → cleanup 阶段 remove_worktree 再次触发 FileNotFoundError
```

辅助因素：
- Job status 未正确更新为 completed（P2-4），导致 auto-cleanup 未触发
- merge 成功消息缺乏验证（P2-5），用户被误导

---

## 行动项

### 立即执行（P0）

- [ ] 验证安装版本 vs 源码版本：`python3 -c "from tcd.job import Job; print(Job.__dataclass_fields__.keys())"` 并对比源码
- [ ] 如版本不一致，重新安装：`cd ~/projects/AI\ 工作流/tmux-codingagent-driver && uv tool install . --force`
- [ ] 在 `merge()` 函数中：fallback 到 `worktree_repo_root` 失败时，打印手动 merge 命令而非假成功
- [ ] `merge_branch()` 返回后验证 merge 确实生效（用 `git merge-base --is-ancestor` 或检查 HEAD 变化）

### 短期（P1，本周）

- [ ] `merge()` 中对 `worktree_repo_root is None` 添加 warning 日志
- [ ] `remove_worktree()` 添加 try/except 防御，目录消失时不抛异常
- [ ] `merge_branch()` 区分 "Already up to date" 和真正的合并成功
- [ ] merge 成功后输出 `git diff --stat` 摘要

### 中期（P2，下个迭代）

- [ ] `tcd check` 增加对 "实际完成但状态未更新" 的检测和自动修正
- [ ] `tcd merge` 增加 `--dry-run` 模式，显示将要执行的操作但不实际执行
- [ ] 添加集成测试：在含非 ASCII 路径（如中文）的 repo 中执行完整 worktree 生命周期

---

## 复现环境

- macOS Darwin 24.6.0
- tcd 源码版本：v0.3.0（`~/projects/AI 工作流/tmux-codingagent-driver`）
- 项目路径：`/Users/michael/projects/组件模块/feishu-cli`（注意中文 `组件模块`）
- Codex provider
- 4 个并行 worktree job 同时触发

## 参考

- 相关 Job 记录：`~/.tcd/jobs/3b18e3c1.json`、`e76f3bcb.json`、`16370815.json`、`4af58644.json`
- 源码：`src/tcd/cli.py:693-737`（merge 函数）、`src/tcd/worktree.py:100-161`（worktree 操作）、`src/tcd/job.py:36-74`（Job 数据模型）
- 手动修复记录：feishu-cli 项目 git log（`git merge --no-ff tcd/3b18e3c1` 等 4 条）

---
---

# 工作流问题分析报告 #3

**日期**: 2026-03-09
**来源**: tcd 自身 bug 修复 + Codex Code Review + 并行 worktree 修复工作流
**场景**: 使用 tcd 驱动 Codex 审核并修复 tcd 自身的 worktree merge 代码

---

## 问题总览

| 优先级 | 问题 | 状态 |
|--------|------|------|
| P0 | AI 在 worktree 中不 commit，导致 merge 时 "Already up to date" | 已修复（Skill prompt 指令 + merge pre-check） |
| P1 | 自举场景：用旧版 tcd 修 tcd 自身，旧版包含正在修的 bug | 已缓解（Skill 自举警告） |
| P2 | STALL/TURN0_STUCK 误报（AI 在生成长文本时） | 已修复（pane_hash 变化检测） |
| P2 | merge 无法区分"无 commit"和"已合并" | 已修复（branch_has_new_commits pre-check） |

---

## 详细分析

### P0-4: AI 在 worktree 中不 commit

**问题描述**

使用 `tcd start --worktree` 派发任务给 Codex。Codex 完成代码修改并通过测试，但没有执行 `git commit`。worktree 分支上没有新 commit。执行 `tcd merge` 时 `git merge` 返回 "Already up to date"（returncode=0），旧版 tcd 报告合并成功，实际无变化。cleanup 阶段删除 worktree 目录，AI 的修改永久丢失。

**根因分析**

tcd 的 worktree 功能假设 AI 会自行 commit 修改，但 Codex 在 full-auto 模式下默认不 commit。这是 tcd（传输层）和 Skill（编排层）之间的**契约缺失**：
- tcd 提供 `create → merge → cleanup` 生命周期原语
- Skill 负责在 prompt 中指导 AI 的行为（包括 commit）
- 但 codex-worker Skill 之前没有包含 commit 指令

**修复方案**（已实施）

1. **Skill 层**（`codex-worker/skill.md`）：worktree 场景下 prompt 必须追加 commit 指令
2. **tcd 层**（`worktree.py`）：新增 `branch_has_new_commits()` 函数，merge 前预检查
3. **tcd 层**（`cli.py` + `sdk.py`）：merge 前调用 pre-check，无新 commit 时输出明确诊断信息并退出

### P1-4: 自举场景——用旧版 tcd 修 tcd

**问题描述**

开发流程中使用 `tcd merge` 合并 Codex 修复的代码，但全局安装的 tcd 是旧版（包含 merge 假成功 bug）。结果：
- Group B worktree 被旧版 `tcd merge` 的 cleanup 删除，代码丢失
- 必须手动重新实现 Group B 的所有修改

**缓解方案**（已实施）

在 codex-worker Skill 注意事项中添加自举警告：当修改目标是 tcd 自身时，建议先手动合并再更新全局版本。

### P2-6: STALL 误报

**问题描述**

Codex 在生成长文本（如代码审核报告）时，`tcd check` 连续 4 次检测到 `state=working`，span > 60s，触发 STALL 警告。但 AI 实际在正常工作，只是输出时间较长。

**根因分析**

原 STALL 规则只检查 `job.checked` 事件的 state 字段是否变化，不检查 pane 内容是否在更新。长时间 state=working 不等于卡住。

**修复方案**（已实施）

1. `cli.py` 和 `sdk.py` 的 check 流程在 state=working 时计算 pane 内容的 md5 hash，写入 `job.checked` 事件
2. `diagnostics.py` R2 规则检查 `pane_hash`：如果 hash 在连续检查中变化，说明 AI 在活跃输出，不触发 STALL
3. 向后兼容：无 hash 数据时（旧事件）仍按原逻辑触发

### P2-7: merge 无法区分"无 commit"和"已合并"

**问题描述**

`git merge` 对于"分支无新 commit"和"分支已被合并"都返回 "Already up to date"（returncode=0）。之前的 noop 检测只能在 merge 后发现，无法在 merge 前区分两种情况。

**修复方案**（已实施）

新增 `branch_has_new_commits(repo_path, branch)` 函数，使用 `git log HEAD..branch` 预检查。merge 前调用，0 commit 时直接报错并提示用户检查 worktree。

---

## 责任边界分析

本轮修复明确了 tcd 与 Skill 的责任边界：

| 职责 | tcd（传输层） | Skill（编排层） |
|------|--------------|----------------|
| AI 必须 commit | 提供 pre-check 和清晰错误信息 | 在 prompt 中包含 commit 指令 |
| 自举检测 | 不涉及 | 在注意事项中提醒 |
| 卡住检测 | 用 pane_hash 区分真卡住和在工作 | 根据 STALL 警告决定是否干预 |
| 分支检查 | 提供 `branch_has_new_commits()` | 不涉及 |

**核心原则**：tcd 提供工具和信号，Skill 提供策略和决策。

---

## 参考

- 修复 commit: 见 `git log --oneline -5` on main
- 受影响文件: `src/tcd/worktree.py`, `src/tcd/cli.py`, `src/tcd/sdk.py`, `src/tcd/diagnostics.py`
- 新增测试: `test_r2_stall_suppressed_when_pane_hash_changes`, `test_r2_stall_triggers_with_same_pane_hash`, `test_merge_command_no_new_commits`
- Skill 更新: `~/.claude/skills/codex-worker/skill.md`
