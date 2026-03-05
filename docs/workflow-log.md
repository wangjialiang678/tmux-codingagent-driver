# 工作流日志：Codex Code Review & Fix

## 元信息

- **日期**: 2026-03-05
- **编排者**: Claude Code (claude-opus-4-6)
- **子代理**: Codex CLI (gpt-5.3-codex xhigh, v0.106.0 → v0.110.0)
- **验证代理**: Reviewer 子代理
- **目标**: 用 Codex 做 code review → 审阅 → 修复 → 更新文档

## 时间线

### 11:10 - Step 1: 派 Codex 做 Code Review

- **Job ID**: ece8b9e3
- **方式**: `tcd start -p codex` + `tcd wait`（旧 Skill）
- **耗时**: ~5 分钟
- **结果**: Codex 完成了全面 review（18 个问题），但因沙箱只读无法写入 docs/code-review.md
- **处理**: 从 `tcd output` 提取内容，由编排者手动创建 docs/code-review.md
- **问题**: 使用 `tcd wait` 阻塞了整个进程，用户等待期间无进展反馈

### 11:15 - Step 2: 编排者审阅 + Reviewer 交叉验证

- **方式**: 启动 Reviewer 子代理并行验证所有发现
- **耗时**: ~1 分钟
- **关键发现**:
  - C-1 被高估（实际用完整 req_id 匹配，非前缀）→ 降级
  - C-2 比描述更严重（Claude/Gemini turn_count 永不递增）
  - M-5 是误报（mkstemp 已默认 0o600）
- **结果**: 在 docs/code-review.md 底部添加了详细评论，标注 7 个 Accept、12 个 Defer/Reject

### 11:20 - Step 3: 第一次尝试让 Codex 修复

- **Job ID**: 9fc1e82d
- **方式**: `tcd start` + `tcd wait`
- **结果**: Codex 自动更新到 v0.110.0，需要重启
- **问题**: `tcd wait` 超时（10 分钟），用户无进展可见

### 11:25 - Step 4: 第二次尝试让 Codex 修复

- **Job ID**: 8d45037f
- **方式**: `tcd start` + `tcd wait`
- **结果**: Codex 审阅了编排者评论，**同意 Accept 列表，对 C-1 有异议**（认为应修复）
- **问题**: 仍然沙箱只读，无法写文件或运行测试
- **Codex 反馈摘要**:
  - 确认最终修复清单: P0(C-2, C-3) + P1(M-1, M-4, M-6, m-1, m-3) + C-1(异议)
  - 对 C-1: "当前 provider 侧仍使用 req_id 前缀匹配，误判风险并未真正消除"

### 11:30 - Step 5: 第三次尝试（加 --sandbox workspace-write）

- **Job ID**: 8e6c6b37
- **方式**: `tcd start --sandbox workspace-write` + `tcd wait`
- **结果**: 仍然失败——`--sandbox` 参数是死代码（M-6），未传入 provider 命令
- **讽刺发现**: 我们想修的 bug M-6 正是阻止 Codex 执行修复的原因

## 问题总结

### 工作流层面

| 问题 | 影响 | 改进方向 |
|------|------|---------|
| `tcd wait` 阻塞 | 用户等待期间无进展反馈 | Skill 已更新为轮询模式（Step 2 改进） |
| Codex 沙箱只读 | 无法写文件、无法运行测试 | 需修复 M-6（sandbox 参数传递）再重试 |
| Codex 自动更新 | 中断了正在执行的任务 | 考虑固定版本或禁用自动更新 |
| 非 git 仓库 | 无法用 git diff 检查改动 | 对比文件时间戳或内容 hash |

### 沟通效率

| 轮次 | 任务 | 结果 | token 成本 |
|------|------|------|-----------|
| 1 | Review | 完成但无法写文件 | ~30k |
| 2 | 修复 | Codex 自动更新，浪费 | ~5k |
| 3 | 修复 | 审阅完成但无法写文件 | ~40k |
| 4 | 修复 | 仍然无法写文件 | ~35k |

**总浪费**: 约 80k tokens 在重复的"读代码→发现写不了"循环中。

### 根因分析

1. **M-6 是阻塞器**: `--sandbox` 参数未传入 provider，Codex 始终以默认沙箱模式运行
2. **沙箱模式与任务不匹配**: Code review 不需要写权限，但修复任务必须有写权限
3. **缺少前置检查**: 应在启动修复任务前验证写权限

### 11:35 - Step 6: 编排者手动修复 M-6

- **改动**: 将 `--sandbox` 参数传入 Job → create_job → codex provider 的 `build_launch_command`
- **文件**: job.py（加 sandbox 字段）, cli.py（传递参数）, sdk.py（传递参数）, codex.py（`-s {sandbox}`）
- **测试**: 147 passed, 0 failed
- **问题**: `uv tool install . --force` 未真正重新构建，需要 `--reinstall`

### 11:40 - Step 7: 第四/五次尝试让 Codex 修复（旧 binary）

- **Job ID**: 82705ea4, 7f9e6ede
- **方式**: `tcd start` + 轮询（新 Skill）
- **结果**: 两次都因安装的 tcd binary 仍是旧版（无 `-s` 参数）而失败
- **发现**: `uv tool install . --force` 用了缓存，需要 `--reinstall` 强制重新构建
- **新 Skill 体验**: 轮询模式工作正常，能实时看到 Codex 进展

### 11:55 - Step 8: 编排者修复安装问题

- **操作**: `uv tool install . --force --reinstall` 强制重新构建
- **验证**: `ps aux` 确认 `-s workspace-write` 出现在进程参数中

### 11:59 - Step 9: Codex 成功执行修复（最终）

- **Job ID**: fc73d94b
- **方式**: `tcd start` + 轮询（新 Skill）
- **耗时**: ~13 分钟
- **进展时间线**:
  - 0-35s: 读取 review 文档和源码
  - 35s-3m: 定位所有修复点
  - 3m-5m: **C-2 修复**（turn_count 递增 + _advance_turn_if_needed）
  - 5m-6m: **C-3 修复**（MODEL_RE 白名单 + shlex.quote）
  - 6m-7m: **M-1 修复**（Gemini 响应提取跳过 prompt）
  - 7m-8m: **M-4 修复**（session 消失区分 completed/failed）
  - 8m-9m: **m-1 修复**（send_text 返回值检查）
  - 9m-10m: **m-3 修复**（缩小 except Exception）
  - 10m-11m: **C-1 修复**（marker 严格匹配，Codex 自主决定）
  - 11m-13m: 修复测试 + 生成 fix-report.md
- **测试结果**: 156 passed, 4 failed（tmux 集成测试受 sandbox 限制）
- **本地验证**: 160 passed, 0 failed

## 最终修复统计

| 问题 ID | 严重度 | 修复者 | 状态 |
|---------|--------|--------|------|
| C-1 | Critical→Minor | Codex（自主决定） | 已修复 |
| C-2 | Critical | Codex | 已修复 |
| C-3 | Critical | Codex | 已修复 |
| M-1 | Major | Codex | 已修复 |
| M-4 | Major | Codex | 已修复 |
| M-6 | Major | Claude Code（编排者） | 已修复 |
| m-1 | Minor | Codex | 已修复 |
| m-3 | Minor | Codex | 已修复 |

**共修复 8 项**（Codex 7 项 + 编排者 1 项），新增 13 个测试用例。

## 工作流经验总结

### 成功点

1. **轮询模式显著优于 tcd wait**: 用户可实时看到 Codex 进展，且编排者可在等待间隙做其他事
2. **交叉验证有价值**: Reviewer 子代理发现了 Codex 的高估/低估和误报
3. **催促机制有效**: `tcd send` 发送催促消息后 Codex 加速了行动
4. **Codex 自主判断**: C-1 虽然编排者认为暂缓，Codex 仍自主修复了，最终证明是合理的

### 改进点

1. **沙箱前置检查**: 启动修复任务前应验证 Codex 有写权限（touch 测试文件）
2. **安装验证**: `uv tool install` 后应验证安装的代码确实更新了（grep 关键修改）
3. **任务拆分**: 大修复任务应拆分为独立的小任务，避免一次 context 消耗过大
4. **轮询间隔**: 前 1 分钟每 15 秒，之后每 30-60 秒更合适（减少无效轮询）
5. **Codex 读取过多**: Codex 倾向于反复阅读所有文件再动手，应在 prompt 中更强调"直接写代码"
