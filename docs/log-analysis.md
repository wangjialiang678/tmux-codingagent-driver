# tcd 工作流日志系统分析报告

**日期**: 2026-03-05
**背景**: 分析一次 Claude Code 编排 Codex 做 code review + 修复的多轮工作流（7 个 job，历时约 1 小时）

---

## 一、现有日志覆盖度评估

### 1.1 日志来源与覆盖矩阵

| 信息类型 | workflow-log.md | job.json | .turn-complete | .log (ANSI) |
|----------|:--------------:|:--------:|:--------------:|:-----------:|
| Job ID | 有 | 有 | - | - |
| 启动时间 | 粗略（分钟级） | 精确 ISO 时间 | - | - |
| 完成时间 | 无 | 精确 ISO 时间 | 有时间戳 | - |
| 耗时 | 估算（"~5分钟"） | 可计算 | - | - |
| provider/model | 有 | provider 有，model=null | - | - |
| sandbox 参数 | 文字描述 | 有（最新 job 才有字段） | - | - |
| prompt 内容 | 摘要 | 完整 | - | - |
| 失败原因 | 人工描述 | "killed by user"（不精确） | 最终消息 | - |
| 沙箱错误详情 | 有（从 last_message 提取） | 截断消息 | 截断消息 | 完整但有 ANSI |
| Token 消耗 | 人工估算（粗） | 无 | 无 | 在 NDJSON 中 |
| Codex 版本 | 有（v0.106→v0.110） | 无 | 无 | 可能在日志里 |
| 自动更新事件 | 有描述 | 无 | 无 | 有 |
| 重试次数/原因 | 人工记录 | 无（每次是独立 job） | - | - |
| 工作流关联 | 有（人工写） | 无 | 无 | - |
| tcd Skill 版本变更 | 有描述 | 无 | 无 | - |
| M-6 手动修复事件 | 有 | 无 | 无 | - |

**覆盖度评分**: 机器可读信息约覆盖 40%，上层工作流信息 100% 依赖人工记录。

### 1.2 各日志文件的实际质量

**job.json（最可靠的机器数据）**

优点：
- 时间戳精确（ISO 8601，含时区）
- prompt 完整保存
- cwd、provider 准确

缺陷：
- `error` 字段永远是 `"killed by user"`，无论是沙箱失败、超时还是用户主动终止——三种截然不同的失败原因被混为一谈
- `model` 字段全部为 `null`（Codex 实际用了 gpt-5.3-codex xhigh，但 tcd 没有捕获）
- `sandbox` 字段仅最新的 fc73d94b 才有（旧 job 无此字段，但 8e6c6b37 正是因 sandbox bug 失败的）
- `turn_count` 对 Codex 无意义（Codex 用 notify-hook，turn_count 始终 0 或 1）
- `result` 字段永远是 `null`

**.turn-complete（信息密度最高的文件）**

优点：
- 包含 Codex 给出的最终消息（最长、最详细）
- 有 ISO 时间戳
- 有 `turnId`（Codex 内部 turn UUID）

缺陷：
- 消息被截断（JSON 字段硬截断，不完整）——三个信号文件均如此
- 没有 token 计数
- 没有 files_modified 信息
- 只存在于已完成 turn 的 job（9fc1e82d、82705ea4、7f9e6ede 无信号文件）

**workflow-log.md（信息最全但全靠人工）**

优点：
- 记录了机器日志完全没有的信息：Codex 版本、自动更新事件、M-6 被手动修复、工作流决策过程、token 估算

缺陷：
- 时间粒度是分钟（无法重建精确事件序列）
- 需要人工维护，极易遗漏
- 没有覆盖后 3 个 job（82705ea4、7f9e6ede、fc73d94b）——工作流日志中断了

---

## 二、缺失信息清单

### P0 级缺失（严重影响事后分析和问题复现）

1. **失败原因分类不准**：所有被 `tcd kill` 的 job 都记为 `"error": "killed by user"`，无法区分：
   - 沙箱只读导致任务无法完成，用户被迫 kill
   - Codex 自动更新导致进程中断
   - 超时被 kill
   - 用户主动放弃

2. **Token 消耗无机器记录**：Codex NDJSON 事件流中有 `event_msg.token_count`，`parse_codex_ndjson()` 已能解析，但从未写入 job.json。workflow-log.md 里靠人工估算（"~30k tokens"），误差大且不可查证。

3. **.turn-complete 消息被截断**：三个信号文件的 `lastAgentMessage` 均在关键信息处截断（比如沙箱报错的具体文件路径）。截断不是 JSON 限制，是 Codex notify-hook 本身的截断。

4. **无工作流上下文关联**：7 个 job 是独立的 JSON 文件，没有任何字段表明它们属于同一个工作流、哪个是重试、哪个是前置依赖。

### P1 级缺失（影响调试效率）

5. **Codex 版本未记录**：`model` 字段为 null，Codex CLI 版本（从 v0.106 自动更新到 v0.110）完全没有记录。自动更新是本次最主要的干扰事件之一。

6. **sandbox 参数历史缺失**：job 8e6c6b37 因 sandbox bug 失败，但它的 json 里没有 `sandbox` 字段（字段是后来加的）。无法从日志重建"曾经尝试传 --sandbox workspace-write 但未生效"这一事实。

7. **工作流日志在第 5 个 job 后中断**：82705ea4、7f9e6ede、fc73d94b 三个 job 完全没有人工记录，原因是工作流节奏加快后没时间写日志。

8. **Codex 内部 session 文件未关联**：`~/.codex/sessions/` 里有 Codex 的完整 NDJSON 事件流，但 job.json 没有记录对应的 session 文件路径（靠"最新文件"启发式匹配，不可靠）。

### P2 级缺失（影响效率分析）

9. **TUI 初始化耗时未记录**：每次 `tcd start` 要等 Codex TUI 就绪，这段时间（有时因 trust dialog 长达 10s）没有记录。

10. **`tcd check` 轮询次数未记录**：从 Skill 改为轮询模式后，轮询了多少次才检测到完成，未记录。

11. **提示词版本无差异对比**：9fc1e82d 和 8d45037f 使用几乎相同的 prompt，但后者实际有效（得到了 Codex 的分析输出），原因可能是 Codex 重启后 context 清空。这种细节无法从日志中重建。

---

## 三、优化建议

### 3.1 tcd 层（最高优先级，改动小收益大）

**建议 T-1：细化 error 原因分类**

当前：所有 kill 都写 `"killed by user"`

改进：在 `kill()` 和 `_refresh_status()` 区分：
- `"killed_by_user"` — 用户主动 kill
- `"session_disappeared"` — tmux session 消失（Codex 崩溃/自动更新）
- `"timeout"` — tcd wait 超时
- `"task_blocked"` — 任务无法完成但 agent 已响应（需 Skill 层标记）

实现位置：`cli.py:_kill_job()`、`sdk.py:kill()`、`cli.py:_refresh_status()`

**建议 T-2：将 token 消耗写入 job.json**

`parse_codex_ndjson()` 已能解析 `tokens`，在 `detect_completion()` 或 notify-hook 触发时，将 token 数写入 job.json。

Job 数据类增加字段：
```python
tokens_input: int | None = None
tokens_output: int | None = None
```

实现位置：`providers/codex.py:detect_completion()` 或 `notify_hook.py`

**建议 T-3：记录 Codex session 文件路径**

在 `detect_completion()` 成功时，将 `_find_session_file()` 找到的路径写入 job.json：

```python
session_file: str | None = None
```

避免后续 `parse_response_structured()` 再次猜测。

**建议 T-4：防止 .turn-complete 消息截断**

当前 Codex notify-hook 写入的消息会被截断。建议在 `notify_hook.py` 中写入完整消息，或至少记录截断位置（`"truncated": true`）。

### 3.2 Skill 层（中优先级）

**建议 S-1：工作流 ID 传递给 tcd**

在 Skill 启动 job 时，通过 prompt 元数据或未来的 `--tag` 参数传入工作流 ID，使同一工作流的多个 job 可以关联。

短期方案：在 prompt 开头加结构化注释：
```
<!-- workflow:codex-review-fix-20260305 retry:3 -->
```

**建议 S-2：Skill 自动追加结构化事件到工作流日志**

codex-worker Skill 在以下时刻自动追加一行到 `docs/workflow-log.md`：

```
[11:20:15] START job=8d45037f provider=codex sandbox=none retry=2
[11:41:19] DONE  job=8d45037f status=idle tokens=40k elapsed=6m33s
[11:41:19] FAIL  job=8d45037f reason=sandbox_readonly
```

格式：机器可解析的单行，便于后续 `tcd log` 命令汇总。

**建议 S-3：前置环境检查**

修复任务开始前，Skill 先运行一个探针 job（`touch .tcd_probe && rm .tcd_probe`）验证写权限，失败则报错而非浪费一个完整 job。

### 3.3 `tcd log` 命令（低优先级，长期方向）

新增 `tcd log [--workflow <id>] [--since <date>]` 命令，自动汇总工作流：

```
$ tcd log --since today

WORKFLOW: codex-review-fix-20260305
  11:09:21  ece8b9e3  codex  failed(6m3s)   killed_by_user  tokens=?  [sandbox:readonly]
  11:19:25  9fc1e82d  codex  failed(15m21s) session_disappeared      [codex_autoupdate]
  11:34:46  8d45037f  codex  failed(6m32s)  killed_by_user  tokens=?  [sandbox:readonly]
  11:41:19  8e6c6b37  codex  failed(8m20s)  killed_by_user           [sandbox_bug_M6]
  11:49:39  82705ea4  codex  failed(5m4s)   session_disappeared
  11:54:56  7f9e6ede  codex  failed(4m45s)  session_disappeared
  11:59:58  fc73d94b  codex  running(?)

SUMMARY: 6/7 failed, total ~46min, ~110k tokens (est.)
```

实现依赖：T-1（错误分类）+ S-2（事件追加）。

---

## 四、自动化方案草案

### 方案 A：最小改动（2-3天）

只改 tcd 层，不改 Skill：

1. T-1：细化 error 分类（1h，改 3 处 kill/refresh 调用）
2. T-2：token 写入 job.json（2h，改 notify_hook.py + job.py）
3. T-3：session 文件路径写入（1h，改 codex provider）

效果：job.json 从"残缺的元数据"变成"可信的结构化日志"，不需要人工补充核心指标。

### 方案 B：Skill 层自动日志（1周）

在方案 A 基础上：

4. S-2：codex-worker Skill 自动追加事件行
5. S-3：前置写权限探针

效果：workflow-log.md 的时间线部分可以自动生成，人工只需写决策和分析部分。

### 方案 C：`tcd log` 命令（2周）

在方案 B 基础上：

6. 新增 `tcd log` 命令（需要工作流关联机制，即 T-4 或 --tag 参数）
7. 新增 `--tag` 参数给 `tcd start`，用于工作流分组

效果：完整工作流可视化，事后分析只需一个命令。

---

## 五、关键数据重建（基于现有日志）

### 7个 job 的精确时间线

| Job ID | 开始时间 (UTC) | 结束时间 (UTC) | 耗时 | 最终状态 | 实际失败原因 |
|--------|--------------|--------------|------|----------|------------|
| ece8b9e3 | 03:09:21 | 03:19:25 | 10m4s | failed | 沙箱只读，用户 kill |
| 9fc1e82d | 03:19:25 | 03:34:46 | 15m21s | failed | Codex 自动更新/重启，turn_count=0 |
| 8d45037f | 03:34:46 | 03:41:19 | 6m33s | failed | 沙箱只读，用户 kill |
| 8e6c6b37 | 03:41:19 | 03:49:39 | 8m20s | failed | M-6 bug 导致 sandbox 参数未传，仍只读 |
| 82705ea4 | 03:49:39 | 03:54:43 | 5m4s | failed | turn_count=0，原因不明（可能仍在更新）|
| 7f9e6ede | 03:54:56 | 03:59:41 | 4m45s | failed | turn_count=0，M-6 已修但仍失败 |
| fc73d94b | 03:59:58 | running | - | running | 首个成功开始修改代码的 job |

**注意**：7f9e6ede 到 fc73d94b 有 17 秒间隔（03:54:41 → 03:59:58），推测是重新生成 prompt 的时间。

### turn_count=0 的诊断意义

9fc1e82d、82705ea4、7f9e6ede 三个 job 的 `turn_count=0` 且无 `.turn-complete` 文件，意味着 Codex 从未完成一个完整 turn。这是比 `"killed by user"` 更准确的诊断：这些 job 很可能在 TUI 初始化阶段就失败了（Codex 自动更新重启，或沙箱在初始化时就拒绝了所有操作）。

---

## 六、结论

本次工作流暴露的日志问题可归纳为一个核心矛盾：**tcd 记录的是 job 生命周期，但调试需要的是工作流故事**。

当前两者之间的桥梁完全依赖人工（workflow-log.md），且在工作流加速时会断裂。

最高价值的改动是 T-1（错误分类）和 T-2（token 记录）——两者合计约 3-4 小时工作量，能将事后分析质量提升 50% 以上，且不改变任何外部接口。
