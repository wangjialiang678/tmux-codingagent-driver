# PRD: tcd Git Worktree 支持

**版本**: v0.3.0
**日期**: 2026-03-05
**状态**: PENDING
**前置**: v0.2.0（事件日志 + 诊断系统已完成，184 tests pass）

---

## 1. 问题

在闭环生态系统的模式 D（并行 Codex + Worktree）中，需要多个 Codex 实例在同一项目上并行工作。当前 tcd 支持多并发 job（各自独立 tmux session），但缺少 git worktree 生命周期管理，调用方需要手动：

1. `git worktree add` 创建隔离工作目录
2. 把 worktree 路径传给 `tcd start -d`
3. Job 完成后手动 `git merge` + `git worktree remove`

这些操作是机械的、易出错的，且没有与 job 生命周期绑定。

**核心需求**：tcd 提供 worktree 原语（创建/清理/合并），调用方决定何时使用。

---

## 2. 设计原则

- **tcd 提供工具，不提供策略** — 不在 tcd 内判断"该不该用 worktree"
- **worktree=False 是默认值** — 不影响现有用法，完全透明
- **非 git 项目直接报错** — 简单明确，不做 fallback
- **Job 生命周期绑定** — kill/clean 时自动清理 worktree
- **合并是显式操作** — 不自动 merge，调用方决定时机和策略

---

## 3. 方案

### 3.1 新增 `src/tcd/worktree.py` — Git Worktree 原语

纯 git 操作封装，不依赖 tcd 其他模块：

```python
"""Git worktree primitives for parallel job isolation."""

import subprocess
from pathlib import Path


class WorktreeError(Exception):
    """Git worktree operation failed."""


def is_git_repo(path: str | Path) -> bool:
    """Check if path is inside a git repository."""
    result = subprocess.run(
        ["git", "rev-parse", "--is-inside-work-tree"],
        cwd=str(path), capture_output=True, text=True,
    )
    return result.returncode == 0


def get_repo_root(path: str | Path) -> Path:
    """Get the root of the git repository containing path."""
    result = subprocess.run(
        ["git", "rev-parse", "--show-toplevel"],
        cwd=str(path), capture_output=True, text=True,
    )
    if result.returncode != 0:
        raise WorktreeError(f"Not a git repo: {path}")
    return Path(result.stdout.strip())


def create_worktree(repo_path: str | Path, branch_name: str) -> Path:
    """Create a worktree in a sibling directory.

    Creates: <repo_parent>/<repo_name>-wt-<branch_name>/
    Branch: tcd/<branch_name>

    Returns the worktree path.
    Raises WorktreeError on failure.
    """
    repo = get_repo_root(repo_path)
    wt_path = repo.parent / f"{repo.name}-wt-{branch_name}"
    full_branch = f"tcd/{branch_name}"

    result = subprocess.run(
        ["git", "worktree", "add", "-b", full_branch, str(wt_path)],
        cwd=str(repo), capture_output=True, text=True,
    )
    if result.returncode != 0:
        raise WorktreeError(f"git worktree add failed: {result.stderr.strip()}")
    return wt_path


def remove_worktree(worktree_path: str | Path) -> None:
    """Remove a worktree and prune."""
    wt = Path(worktree_path)
    if not wt.exists():
        return
    # Find the main repo to run git commands
    result = subprocess.run(
        ["git", "worktree", "remove", str(wt), "--force"],
        capture_output=True, text=True,
    )
    if result.returncode != 0:
        raise WorktreeError(f"git worktree remove failed: {result.stderr.strip()}")


def merge_branch(
    repo_path: str | Path,
    branch: str,
    *,
    strategy: str = "merge",  # "merge" | "squash"
) -> bool:
    """Merge a branch into the current HEAD.

    Returns True on success, False on conflict.
    Does NOT auto-resolve conflicts.
    """
    cmd = ["git", "merge", branch]
    if strategy == "squash":
        cmd = ["git", "merge", "--squash", branch]

    result = subprocess.run(
        cmd, cwd=str(repo_path), capture_output=True, text=True,
    )
    return result.returncode == 0


def delete_branch(repo_path: str | Path, branch: str) -> None:
    """Delete a local branch after merge."""
    subprocess.run(
        ["git", "branch", "-d", branch],
        cwd=str(repo_path), capture_output=True, text=True,
    )
```

### 3.2 扩展 Job 数据结构

在 `Job` dataclass 中新增两个可选字段：

```python
@dataclass
class Job:
    # ... existing fields ...
    worktree_path: str | None = None
    worktree_branch: str | None = None
```

这两个字段在 `from_dict()` 中已自动兼容（未知 key 被过滤，缺失 key 用默认值）。

### 3.3 SDK 集成

#### `start()` 增加 `worktree` 参数

```python
def start(
    self,
    provider: str,
    prompt: str,
    cwd: str = ".",
    *,
    model: str | None = None,
    timeout: int = 60,
    sandbox: str | None = None,
    worktree: bool = False,       # 新增
    worktree_name: str | None = None,  # 新增，默认用 job_id
) -> Job:
```

当 `worktree=True` 时：
1. 检查 `cwd` 是否是 git repo（否则 raise `TCDError`）
2. 检查是否有未提交改动（`git status --porcelain`），有则 raise
3. `create_worktree(cwd, name)` → 得到 worktree 路径
4. 把 `cwd` 替换为 worktree 路径
5. 在 Job 中记录 `worktree_path` 和 `worktree_branch`

#### `kill()` / `clean()` 自动清理

```python
def kill(self, job_id: str) -> None:
    # ... existing kill logic ...
    if job.worktree_path:
        try:
            remove_worktree(job.worktree_path)
        except WorktreeError:
            pass  # best-effort cleanup
```

#### 新增 `merge_worktree()`

```python
def merge_worktree(
    self,
    job_id: str,
    *,
    strategy: str = "merge",
    cleanup: bool = True,
) -> bool:
    """Merge a worktree job's branch back and clean up.

    Returns True if merge succeeded, False if there were conflicts.
    """
    job = self._mgr.load_job(job_id)
    if not job or not job.worktree_branch:
        raise TCDError(f"Job {job_id} has no worktree")

    repo_root = get_repo_root(job.cwd)  # cwd is the worktree, get main repo
    success = merge_branch(repo_root, job.worktree_branch, strategy=strategy)

    if success and cleanup:
        remove_worktree(job.worktree_path)
        delete_branch(repo_root, job.worktree_branch)
        job.worktree_path = None
        job.worktree_branch = None
        self._mgr.save_job(job)

    emit(job.id, "job.worktree_merged", success=success, strategy=strategy)
    return success
```

### 3.4 CLI 集成

#### `tcd start` 新增 `--worktree` flag

```bash
# 在 worktree 中启动 Codex
tcd start -p codex --worktree -m "实现用户认证" -d /path/to/project

# 自定义 worktree 名称
tcd start -p codex --worktree --wt-name auth -m "实现用户认证" -d /path/to/project
```

#### 新增 `tcd merge` 命令

```bash
# 合并 worktree 分支回主分支
tcd merge <job_id>

# squash merge
tcd merge <job_id> --squash

# 仅合并不清理
tcd merge <job_id> --no-cleanup
```

### 3.5 事件日志集成

新增事件类型：

| 事件 | 触发点 | 关键字段 |
|------|--------|---------|
| `job.worktree_created` | worktree 创建成功 | worktree_path, branch |
| `job.worktree_merged` | merge 操作完成 | success, strategy |
| `job.worktree_removed` | worktree 清理完成 | worktree_path |

---

## 4. 实施计划

### Phase 1: Worktree 原语

- [ ] 新增 `src/tcd/worktree.py`（~80 行）
- [ ] 函数：`is_git_repo`, `get_repo_root`, `create_worktree`, `remove_worktree`, `merge_branch`, `delete_branch`
- [ ] 新增 `tests/test_worktree.py`（需要真实 git repo，类似 test_tmux_adapter.py 的集成测试风格）

### Phase 2: Job + SDK 集成

- [ ] `Job` dataclass 加 `worktree_path`, `worktree_branch` 字段
- [ ] `sdk.py` 的 `start()` 加 `worktree` / `worktree_name` 参数
- [ ] `sdk.py` 新增 `merge_worktree()` 方法
- [ ] `kill()` / `clean()` 中自动清理 worktree
- [ ] 事件日志埋点（3 个事件）
- [ ] 新增 `tests/test_worktree_sdk.py`（mock git 操作的单元测试）

### Phase 3: CLI 集成

- [ ] `tcd start` 加 `--worktree` / `--wt-name` 选项
- [ ] 新增 `tcd merge` 命令
- [ ] 测试：CLI 参数解析

---

## 5. 影响范围

| 文件 | 改动类型 |
|------|---------|
| `src/tcd/worktree.py` | **新增** |
| `src/tcd/job.py` | 加 2 个字段 |
| `src/tcd/sdk.py` | `start()` 扩展 + `merge_worktree()` |
| `src/tcd/cli.py` | `--worktree` flag + `tcd merge` 命令 |
| `tests/test_worktree.py` | **新增**（集成测试） |
| `tests/test_worktree_sdk.py` | **新增**（单元测试） |

**不改动**：provider 代码、tmux_adapter、collector、event_log、diagnostics

---

## 6. 非目标

- 不在 tcd 内判断"该不该用 worktree" — 调用方决策
- 不自动解决 merge 冲突 — 返回 False，调用方处理
- 不支持 `git stash` 自动暂存 — 有未提交改动时直接报错
- 不管理依赖安装（node_modules 等） — worktree 内的 install 由调用方/Codex 处理
- 不做跨 worktree 的文件锁 — 通过任务拆分（不改同一文件）避免冲突

---

## 7. 调用方使用示例

### SDK 并行 Codex

```python
from tcd import TCD

tcd = TCD()

# 并行启动 3 个 Codex，各在独立 worktree
jobs = []
for task in ["auth", "articles", "dashboard"]:
    job = tcd.start(
        "codex",
        f"实现 {task} 模块，代码放在 src/features/{task}/",
        cwd="/path/to/project",
        worktree=True,
        worktree_name=task,
    )
    jobs.append(job)

# 等待全部完成
for job in jobs:
    tcd.wait(job.id, timeout=600)

# 逐个合并
for job in jobs:
    success = tcd.merge_worktree(job.id)
    if not success:
        print(f"Merge conflict on {job.worktree_branch}, manual resolution needed")
```

### codex-worker Skill 集成

```
调用方（Claude Code）判断：
1. len(tasks) >= 2 且功能独立 → 建议并行 worktree
2. 用户确认 → 创建 worktree + 并行 Codex
3. 全部完成 → 逐个 merge + 集成验证
```
