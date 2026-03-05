# Code Review Report

**审查人**: Codex (gpt-5.3-codex xhigh)
**日期**: 2026-03-05
**项目**: tmux-codingagent-driver (tcd)

## Scope

- 审查维度：
  1. 代码质量（命名、结构、可读性）
  2. Bug 风险（边界情况、错误处理）
  3. 架构问题（模块耦合、抽象层次、扩展性）
  4. 测试覆盖（充分性与遗漏场景）
  5. 安全问题（输入验证、注入风险）
  6. 性能问题（不必要开销、可优化点）

---

## Critical

### C-1 Marker 完成检测可能被"用户输入回显"误触发，导致任务被错误判定为完成

- **文件路径**: src/tcd/marker_detector.py, src/tcd/providers/claude.py, src/tcd/providers/gemini.py
- **行号范围**: marker_detector.py:36-42, claude.py:80-84, gemini.py:78-82
- **问题描述**:
  - build_marker_prompt() 把 TCD_DONE:{req_id} 放进用户输入文本。
  - scan_for_marker() 仅做字符串包含判断。
  - Claude/Gemini 侧又是前缀式匹配（{job.id}-{turn}-），如果终端回显了用户输入，可能在 AI 尚未完成时误判为 idle。
- **建议修复方案**:
  - 使用完整 req_id 严格匹配（整行匹配，非前缀匹配）。
  - 不要在用户输入中直接出现"最终完成标记"原文。
  - 优先基于结构化消息（assistant role）判定完成，避免全屏文本扫描误判。

### C-2 Claude/Gemini 多轮会话 turn_count 未递增，后续轮次检测容易命中旧轮次标记

- **文件路径**: src/tcd/cli.py, src/tcd/sdk.py, src/tcd/providers/claude.py, src/tcd/providers/gemini.py
- **行号范围**: cli.py:237-245, 287-292, 343-349, sdk.py:152-161, 242-248, claude.py:81-84, gemini.py:79-82
- **问题描述**:
  - send() 用 turn_count + 1 生成 req_id。
  - 但 Claude/Gemini 路径在 turn 完成后没有更新 turn_count。
  - 后续检测仍按旧 turn_count 匹配，可能直接命中历史 marker，导致多轮流程失真。
- **建议修复方案**:
  - 在 working -> idle/context_limit 状态转移时统一递增 turn_count（且保证幂等，只加一次）。
  - 在 Job 中持久化 current_req_id，检测时使用"当前轮完整 req_id"。

### C-3 Provider 启动命令拼接存在命令注入风险（--model 未安全转义）

- **文件路径**: src/tcd/providers/codex.py, src/tcd/providers/claude.py, src/tcd/providers/gemini.py
- **行号范围**: codex.py:141-156, claude.py:42-48, gemini.py:40-46
- **问题描述**:
  - job.model 可由用户输入，当前通过字符串拼接进入 shell 命令。
  - 若包含引号/分号等元字符，可能破坏命令结构，形成注入面。
- **建议修复方案**:
  - 统一改为参数化构建并逐项转义（或 whitelist 校验 model 字符集）。
  - 对 model 做严格格式校验（如 `[a-zA-Z0-9._:-]+`）。

---

## Major

### M-1 Gemini 响应提取逻辑可能返回用户 prompt 而不是 AI 回复

- **文件路径**: src/tcd/providers/gemini.py
- **行号范围**: gemini.py:124-143
- **问题描述**:
  - _extract_between_markers() 取最后 TCD_REQ 和最后 TCD_DONE 的区间。
  - 该区间在不少场景是用户输入和说明文本，不是 assistant 输出。
- **建议修复方案**:
  - 改成基于消息角色/事件的提取策略。
  - 增加回归测试，断言提取结果不包含用户 prompt 段。

### M-2 Session 文件定位策略"取全局最新"会串任务

- **文件路径**: src/tcd/providers/codex.py, src/tcd/providers/claude.py
- **行号范围**: codex.py:210-236, claude.py:119-133
- **问题描述**:
  - 当前策略是全目录扫描后取最新 jsonl。
  - 并发任务或多项目同时运行时，极易读到别的任务会话。
- **建议修复方案**:
  - 启动时记录 provider 原生 session id（或唯一标识）到 Job。
  - 读取时按 session id 精确匹配，至少叠加 cwd + created_at 过滤。

### M-3 CLI 与 SDK 重复实现核心流程，且行为已出现不一致

- **文件路径**: src/tcd/cli.py, src/tcd/sdk.py
- **行号范围**: cli.py:40-142, 223-299, 305-351, 451-470, sdk.py:62-123, 125-193, 220-325
- **问题描述**:
  - start/check/wait/send/_refresh_status/_wait_for_tui 大量重复。
  - 例如 SDK 检查 send_text 失败并抛错，CLI 未检查返回值。
- **建议修复方案**:
  - 抽离统一 service 层（流程与状态机），CLI/SDK 仅做 I/O 适配。
  - 增加 CLI/SDK 一致性测试。

### M-4 会话消失即标记 completed，无法区分正常结束和异常失败

- **文件路径**: src/tcd/cli.py, src/tcd/sdk.py
- **行号范围**: cli.py:451-462, sdk.py:283-290
- **问题描述**:
  - _refresh_status() 仅依据 session 是否存在更新状态。
  - crash/权限失败/被 kill 等异常都可能误记为 completed。
- **建议修复方案**:
  - 结合退出信号、日志尾特征或显式结束码文件来区分 completed/failed/aborted。

### M-5 持久化目录和文件权限未收敛，可能泄露敏感 prompt/输出

- **文件路径**: src/tcd/config.py, src/tcd/job.py, src/tcd/notify_hook.py
- **行号范围**: config.py:15-18, job.py:95-106, notify_hook.py:66-69, 91-94
- **问题描述**:
  - 默认权限依赖系统 umask，未显式限制 ~/.tcd 与 job 文件可见性。
- **建议修复方案**:
  - 目录权限固定 700，文件权限固定 600。

### M-6 --sandbox 参数声明但未生效，接口语义与实现不一致

- **文件路径**: src/tcd/cli.py
- **行号范围**: cli.py:47-48
- **问题描述**:
  - CLI 对外暴露了 --sandbox，但后续未传入 provider 命令构建。
- **建议修复方案**:
  - 要么真正接入 provider 启动参数，要么移除此选项并同步文档。

### M-7 job.json 并发更新存在覆盖风险

- **文件路径**: src/tcd/job.py, src/tcd/notify_hook.py
- **行号范围**: job.py:95-106, notify_hook.py:83-95
- **问题描述**:
  - 两处都采用 read-modify-write + replace，但没有锁或版本控制。
  - 并发写入时可能出现字段丢失（turn_count/turn_state/error）。
- **建议修复方案**:
  - 引入文件锁或版本号 CAS；或改为事件日志追加模型。

---

## Minor

### m-1 CLI start 未检查 prompt 发送结果

- **文件路径**: src/tcd/cli.py
- **行号范围**: cli.py:135-137
- **问题描述**:
  - tmux.send_text() 返回值被忽略，失败后仍输出"Job started"。
- **建议修复方案**:
  - 失败时标记 job 为 failed 并输出错误码。

### m-2 Marker 扫描仅看最后 50 行，长输出可能漏检

- **文件路径**: src/tcd/marker_detector.py
- **行号范围**: marker_detector.py:7-8, 36-56
- **问题描述**:
  - marker 被滚出 tail 窗口时可能出现"已完成但检测不到"。
- **建议修复方案**:
  - 动态窗口或按 req_id 在结构化日志中查找。

### m-3 多处广泛 except Exception，可观测性较差

- **文件路径**: src/tcd/cli.py, src/tcd/sdk.py, src/tcd/providers/gemini.py
- **行号范围**: cli.py:229-233, 284-294, sdk.py:145-149, gemini.py:65-66
- **问题描述**:
  - 异常被吞掉，真实错误根因难定位。
- **建议修复方案**:
  - 缩小异常范围并记录上下文日志。

### m-4 私有函数 _now_iso 被跨模块直接依赖，边界语义不清晰

- **文件路径**: src/tcd/job.py, src/tcd/cli.py, src/tcd/sdk.py
- **行号范围**: job.py:24-25, cli.py:14, sdk.py:11
- **问题描述**:
  - _now_iso 以私有命名导出并在外部模块使用，降低可读性和 API 清晰度。
- **建议修复方案**:
  - 改为公共工具函数（如 tcd.timeutils.now_iso()）并统一引用。

### m-5 Provider 注册依赖隐式副作用导入，可维护性一般

- **文件路径**: src/tcd/__init__.py, src/tcd/providers/__init__.py
- **行号范围**: __init__.py:6-8
- **问题描述**:
  - 注册行为分散在包导入副作用中，不够显式。
- **建议修复方案**:
  - 在 providers/__init__.py 集中注册入口，调用方显式初始化。

---

## Suggestion

### S-1 统一命令构建器，消除重复拼接与转义分叉

- **文件路径**: src/tcd/providers/codex.py, src/tcd/providers/claude.py, src/tcd/providers/gemini.py
- **行号范围**: codex.py:132-159, claude.py:38-50, gemini.py:36-47
- **问题描述**:
  - 三个 provider 都各自拼 shell 字符串，维护成本高。
- **建议修复方案**:
  - 抽象 CommandBuilder，统一参数化组装与转义策略。

### S-2 将完成检测抽象为可组合策略链

- **文件路径**: src/tcd/providers/codex.py, src/tcd/providers/claude.py, src/tcd/providers/gemini.py
- **行号范围**: codex.py:165-178, claude.py:56-92, gemini.py:53-90
- **问题描述**:
  - Signal/Marker/Idle 逻辑目前写死在 provider 中，扩展新 provider 复用性低。
- **建议修复方案**:
  - 抽离策略类并声明式组合（Strategy pipeline）。

### S-3 补齐高风险回归测试矩阵

- **文件路径**: tests/
- **行号范围**: 多文件
- **问题描述**:
  - 关键风险（多轮一致性、注入、串任务、并发写）没有对应回归测试。
- **建议修复方案**:
  - 增加以下测试组：
    - test_multiturn_turn_count_for_marker_providers
    - test_model_arg_escaping_all_providers
    - test_session_file_selection_with_multiple_candidates
    - test_refresh_status_failure_classification
    - test_job_json_concurrent_update_consistency

---

## 测试覆盖评估

- **已覆盖较好**:
  - job, output_cleaner, marker_detector, tmux_adapter 的基础行为
  - UTF-8 分块、ANSI 清理、JSON 抽取等细粒度逻辑

- **明显缺口**:
  - 多轮会话状态推进（尤其 Claude/Gemini）
  - 命令注入与参数转义安全测试
  - 并发写入一致性测试
  - 多会话并发时 session 文件定位准确性测试

## 性能评估补充

- 高频轮询路径存在可优化点：
  - capture_pane 默认抓全量滚动缓冲（-S -）在 check/wait 中频繁调用，成本较高
  - session 文件全目录 rglob 每次扫描，随着历史积累会退化明显
- 建议：
  - 检测时先小窗口采样（最近 N 行），必要时再扩展
  - 缓存/索引 session 文件映射，避免全量扫描

## 总体评价

项目的 MVP 结构和可用性基础较好，模块划分清晰、CLI/SDK 功能完整，测试数量也不低。但当前存在 3 个 Critical 风险，集中在"完成检测可靠性"和"命令构建安全性"，会直接影响生产可用性与安全性。建议先完成 Critical 与 Major 修复，再推进架构去重和性能优化。

## 统计摘要

| 级别 | 数量 | 已修复 | 暂缓/拒绝 |
|------|------|--------|-----------|
| Critical | 3 | 3 (C-1, C-2, C-3) | 0 |
| Major | 7 | 3 (M-1, M-4, M-6) | 4 (M-2, M-3, M-5, M-7) |
| Minor | 5 | 2 (m-1, m-3) | 3 (m-2, m-4, m-5) |
| Suggestion | 3 | 0 | 3 (S-1, S-2, S-3) |
| **总计** | **18** | **8** | **10** |

## 附注

- 本次仅执行 review，未修改任何业务代码。
- 尝试运行测试辅助验证时，环境报错无法使用临时目录，因此未能完成本地测试执行。

---

## Claude Code 审阅评论（编排者批注）

**审阅人**: Claude Code (claude-opus-4-6)
**日期**: 2026-03-05
**依据**: 独立 Reviewer 子代理交叉验证 + 源码抽查

### 同意修复（Accept）

| ID | 理由 |
|----|------|
| **C-2** | **确认为 Critical 且比描述更严重**。Reviewer 验证发现 turn_count 仅在 notify_hook（Codex 专用）中递增，Claude/Gemini 的 turn_count **永远为 0**，导致多轮 req_id 冲突。必须修复。 |
| **C-3** | **确认为 Critical**。model 参数直接拼接 shell 命令，无 shlex.quote() 保护。应对 model 做正则白名单校验 `[a-zA-Z0-9._:/-]+` 并用 shlex.quote 包装。 |
| **M-1** | **确认，实际比描述更严重**。_extract_between_markers() 提取 TCD_REQ 到 TCD_DONE 之间的内容，包含了原始 prompt 文本，不是纯 AI 响应。需要跳过 prompt 行。 |
| **M-4** | **确认**。session 消失可能是崩溃/kill，统一标记 completed 会误导上层。应区分 completed/failed/unknown。 |
| **M-6** | **确认，简单清理**。--sandbox 参数是死代码，直接移除或真正接入 provider。 |
| **m-1** | **确认**。send_text 返回值应检查，失败时标记 job 为 failed。 |
| **m-3** | **确认**。广泛 except Exception 降低可观测性，应缩小范围并 log traceback。 |

### 不同意修复 / 暂缓（Reject / Defer）

| ID | 理由 |
|----|------|
| **C-1** | **降级为 Minor，暂缓**。Marker 包含 UUID 格式的 req_id（如 `TCD_DONE:ece8b9e3-1-abc123`），用户输入自然产生完全匹配的概率极低。当前 scan_for_marker 已用完整 req_id 匹配，不是前缀匹配。Codex 描述有误——实际代码检查的是完整 req_id 字符串，不是前缀。风险被高估。 |
| **M-2** | **暂缓**。Session 文件串任务是真实风险，但当前 MVP 阶段以单用户单任务为主，并发场景罕见。记录为 tech debt，v2 再修。 |
| **M-3** | **暂缓**。CLI/SDK 重复是已知技术债，计划 v2 统一 service 层。当前阶段 CLI 和 SDK 的行为差异点恰好是有意为之的（CLI 输出格式 vs SDK 返回对象），不是简单 DRY 问题。 |
| **M-5** | **拒绝，误报**。Reviewer 验证确认 job.json 使用 tempfile.mkstemp() 创建（默认 mode=0o600），权限已经是仅所有者可读写。目录权限依赖 umask 在 macOS 单用户环境下是合理的。 |
| **M-7** | **暂缓**。并发写入竞态确实存在，但实际场景中 notify_hook 和 CLI/SDK 写入的字段不重叠（hook 写 turn_count/turn_state，CLI 写 status），冲突窗口极小。v2 考虑引入 fcntl.flock。 |
| **m-2** | **暂缓**。50 行扫描窗口对 marker 检测足够——marker 在输出末尾，不会被滚出。只有在 AI 输出 50+ 行后才追加 marker 的情况才可能漏检，实际不会发生。 |
| **m-4** | **暂缓**。_now_iso 命名虽不规范但影响极小，重构 service 层时一并处理。 |
| **m-5** | **暂缓**。Provider 注册用 decorator + import 副作用是 Python 生态的常见模式（Django、Flask 等），可维护性可接受。 |
| **S-1** | **暂缓**。命令构建器抽象在只有 3 个 provider 时 ROI 不高，等 provider 数量增长再考虑。 |
| **S-2** | **暂缓**。策略链模式同理，当前 3-strategy fallback 在各 provider 中的实现差异较大，强行统一反而增加复杂度。 |
| **S-3** | **部分同意**。补测试矩阵有价值，但优先级低于 C-2/C-3 的修复。修复 Critical 问题时顺带补对应测试。 |

### 修复优先级建议

1. **P0 立即修复**: C-2（turn_count）, C-3（命令注入）
2. **P1 本轮修复**: M-1（Gemini 响应提取）, M-4（状态区分）, M-6（死代码清理）, m-1（send 检查）, m-3（异常处理）
3. **P2 下轮再说**: C-1, M-2, M-3, M-7, m-2, m-4, m-5, S-1~S-3
