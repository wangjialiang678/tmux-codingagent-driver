# tcd Worktree 闭环测试方案

**版本**: v0.3.0
**日期**: 2026-03-05
**状态**: COMPLETED（Phase 1-3 全部验证通过，222 tests pass）
**基线**: 191 tests pass (v0.2.0)

---

## P0: 基础构建检查

每完成一个逻辑单元后必须全部通过。

- [x] **P0-1: 依赖安装**
  判定标准: `uv sync` 退出码=0
  建议命令: `uv sync`

- [x] **P0-2: 模块导入**
  判定标准: `from tcd.worktree import create_worktree` 不报 ImportError
  建议命令: `uv run python -c "from tcd.worktree import create_worktree, remove_worktree, merge_branch; print('OK')"`

- [x] **P0-3: 全量单元测试（回归）**
  判定标准: `pytest` 退出码=0，测试数 >= 191（基线不退步）
  建议命令: `uv run pytest tests/ -q`
  结果: 222 passed

- [x] **P0-4: CLI 入口**
  判定标准: `tcd --help` 退出码=0，输出包含 "merge" 子命令
  建议命令: `uv run tcd --help`

---

## P1: Phase 1 — Worktree 原语

### 功能 1: Git 检测

- [x] **P1-1a: is_git_repo 正确判断 git 目录**
  判定标准: 在 git repo 内返回 True，在 /tmp 返回 False
  验证方式: 单元测试 test_worktree.py

- [x] **P1-1b: get_repo_root 返回正确根目录**
  判定标准: 在 repo 子目录中调用返回 repo 根，非 git 目录 raise WorktreeError
  验证方式: 单元测试

### 功能 2: Worktree 创建与清理

- [x] **P1-2a: create_worktree 创建隔离目录**
  判定标准: 返回路径存在，是独立 git worktree，分支名以 `tcd/` 开头
  验证方式: 集成测试（真实 git repo）

- [x] **P1-2b: create_worktree 重复名称报错**
  判定标准: 同名 worktree 再次创建 raise WorktreeError
  验证方式: 单元测试

- [x] **P1-2c: remove_worktree 清理干净**
  判定标准: 移除后目录不存在，`git worktree list` 不含该路径
  验证方式: 集成测试

- [x] **P1-2d: remove_worktree 不存在路径不报错**
  判定标准: 对不存在的路径调用不 raise
  验证方式: 单元测试

### 功能 3: 合并与分支删除

- [x] **P1-3a: merge_branch 正常合并返回 True**
  判定标准: merge 成功返回 True，目标分支的文件在主分支可见
  验证方式: 集成测试（创建 worktree → 提交文件 → merge）

- [x] **P1-3b: merge_branch squash 模式**
  判定标准: `strategy="squash"` 合并后 commit 历史为单个 squash commit
  验证方式: 集成测试

- [x] **P1-3c: merge_branch 冲突返回 False**
  判定标准: 主分支和 worktree 分支修改同一文件同一行，merge 返回 False
  验证方式: 集成测试

- [x] **P1-3d: delete_branch 删除分支**
  判定标准: 已合并分支被删除，`git branch` 不再列出
  验证方式: 集成测试

---

## P1: Phase 2 — Job + SDK 集成

### 功能 4: Job 数据结构

- [x] **P1-4a: Job 新增 worktree 字段**
  判定标准: `Job(worktree_path="/tmp/x", worktree_branch="tcd/x")` 序列化/反序列化正确
  验证方式: 单元测试

- [x] **P1-4b: 旧 Job JSON 向后兼容**
  判定标准: 不含 worktree 字段的 JSON 反序列化不报错，字段为 None
  验证方式: 单元测试

### 功能 5: SDK start() worktree 支持

- [x] **P1-5a: start(worktree=True) 创建 worktree**
  判定标准: Job.worktree_path 非空，Job.cwd 指向 worktree 目录
  验证方式: 单元测试（mock git 操作）

- [x] **P1-5b: start(worktree=True) 非 git 目录报错**
  判定标准: raise TCDError 含 "not a git repository"
  验证方式: 单元测试

- [x] **P1-5c: start(worktree=True) 有未提交改动报错**
  判定标准: raise TCDError 含 "uncommitted changes"
  验证方式: 单元测试

### 功能 6: SDK merge_worktree()

- [x] **P1-6a: merge_worktree 正常合并**
  判定标准: 返回 True，worktree 被清理，分支被删除
  验证方式: 单元测试（mock）

- [x] **P1-6b: merge_worktree 冲突**
  判定标准: 返回 False，worktree 和分支保留（供手动解决）
  验证方式: 单元测试

- [x] **P1-6c: merge_worktree 无 worktree 的 Job 报错**
  判定标准: raise TCDError 含 "no worktree"
  验证方式: 单元测试

### 功能 7: kill/clean 自动清理

- [x] **P1-7a: kill 自动清理 worktree**
  判定标准: kill 后 worktree 目录被移除
  验证方式: 单元测试（mock remove_worktree 验证被调用）

- [x] **P1-7b: clean 自动清理 worktree**
  判定标准: clean 后相关 worktree 被移除
  验证方式: 单元测试（注：实际由 kill 路径覆盖，clean 调用 kill）

### 功能 8: 事件日志

- [x] **P1-8a: worktree_created 事件**
  判定标准: start(worktree=True) 后事件日志含 `job.worktree_created`
  验证方式: 单元测试

- [x] **P1-8b: worktree_merged 事件**
  判定标准: merge_worktree 后事件日志含 `job.worktree_merged`
  验证方式: 单元测试

- [x] **P1-8c: worktree_removed 事件**
  判定标准: kill 带 worktree 的 Job 后事件日志含 `job.worktree_removed`
  验证方式: 单元测试

---

## P1: Phase 3 — CLI 集成

### 功能 9: tcd start --worktree

- [x] **P1-9a: --worktree flag 解析**
  判定标准: `tcd start --worktree -p codex -m "test" -d .` 传递 worktree=True
  验证方式: 单元测试（mock）

- [x] **P1-9b: --wt-name 自定义名称**
  判定标准: `tcd start --worktree --wt-name auth -p codex -m "test"` 传递 worktree_name="auth"
  验证方式: 单元测试

### 功能 10: tcd merge 命令

- [x] **P1-10a: tcd merge 正常合并**
  判定标准: `tcd merge <id>` 退出码=0，输出包含 "Merged"
  验证方式: 单元测试（mock）

- [x] **P1-10b: tcd merge --squash**
  判定标准: `tcd merge <id> --squash` 传递 strategy="squash"
  验证方式: 单元测试

- [x] **P1-10c: tcd merge 冲突**
  判定标准: merge 冲突时退出码!=0，输出包含 "conflict"
  验证方式: 单元测试

- [x] **P1-10d: tcd merge --no-cleanup**
  判定标准: 传递 cleanup=False（不调用 remove_worktree）
  验证方式: 单元测试

---

## 验证纪律

- **判定标准已锁定**：上述所有判定标准在用户确认后不允许修改
- **基线保护**：每轮验证后测试数 >= 191（不允许删除现有测试）
- **每次修复只改业务代码**：不允许放宽判定标准
- **止损条件**：5 次修复同一项 / 振荡 2 次 / 15 次总修复 / 连续 3 轮 P0 失败 → 停止，人工介入
