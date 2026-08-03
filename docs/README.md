# tcd 文档索引

**当前版本 v0.4.0。** 下面标注 ⚠️ 的文档写于 2026-03 的 v0.1.0 时期，其中的行为
描述已与代码不符，保留仅作历史记录。**有冲突时以「当前」区的文档为准。**

## 当前（维护中）

| 文档 | 内容 |
|---|---|
| [architecture.md](architecture.md) | **从这里开始**。需求、四个关键设计决策及其代价、技术架构、架构债与改进方向、不变量清单 |
| [workflow-issues.md](workflow-issues.md) | 事故与修复登记册。2026-03 段是开发期问题，2026-08 段是两个月生产使用后的复盘 |
| [../CHANGELOG.md](../CHANGELOG.md) | 版本变更，含 breaking change |
| [../README.md](../README.md) | 命令速查与用法 |

## 历史（v0.1.0 时期，⚠️ 描述已过时）

| 文档 | 仍然有价值的部分 | 已过时的部分 |
|---|---|---|
| ⚠️ [prd.md](prd.md) | 问题定义、竞品对比、为什么选 tmux | 命令行为、状态模型、"IMPLEMENTED v0.1.0" |
| ⚠️ [design.md](design.md) | 分层图的骨架仍成立 | 具体模块职责已变（新增 readiness / doctor / submission_recovery） |
| ⚠️ [scenarios.md](scenarios.md) | 使用场景的意图 | 所有命令示例（缺 `--force`、`doctor`，且示范了已知有害的用法） |
| ⚠️ [integration-guide.md](integration-guide.md) | 两种集成模式的划分 | 建议写进 CLAUDE.md 的命令清单已过时 |
| ⚠️ [prd-worktree.md](prd-worktree.md) | worktree 隔离的设计意图 | **stash 处理方式已完全重写**（v0.4.0 按 ref 恢复 + 仓库锁） |
| ⚠️ [prd-event-log.md](prd-event-log.md) | 事件日志的设计 | 事件类型有新增 |
| ⚠️ [feature-request-parallel-batch-start.md](feature-request-parallel-batch-start.md) | 未实现的提案，仍可参考 | — |

## 过程记录（不需要维护）

`code-review.md` / `dev-log.md` / `fix-report.md` / `log-analysis.md` /
`quickstart-test.md` / `test-plan*.md` / `workflow-log.md` / `research/` —
某次具体工作的快照，读时按时间戳理解。

`research/acp-vs-tmux-comprehensive-report.md` 值得特别注意：它是在各家 CLI 的
headless / 结构化输出模式成熟**之前**做的结论，如果要重新评估传输层
（见 architecture.md §4.4），需要重做而不是直接引用。
