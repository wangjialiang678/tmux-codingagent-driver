# tcd 文档索引

**当前版本 v0.4.0。** 这里只保留仍然成立的文档。2026-03 的 v0.1.0 时期文档
（prd.md / design.md / scenarios.md / integration-guide.md / prd-worktree.md /
prd-event-log.md、各类过程记录、四份竞品调研）已删除——它们描述的行为与代码多处
冲突，**过时的设计文档比没有文档更危险**。有效内容已吸收进 architecture.md，
原文可在 git 历史中找回。

| 文档 | 内容 |
|---|---|
| [architecture.md](architecture.md) | **从这里开始**。需求与定位、四个关键设计决策及其代价、技术架构与 job 生命周期、事件日志、架构债与改进方向、七条不变量 |
| [workflow-issues.md](workflow-issues.md) | 事故与修复登记册。2026-03 段是开发期问题，2026-08 段是两个月生产使用后的复盘 |
| [feature-request-parallel-batch-start.md](feature-request-parallel-batch-start.md) | 未实现的提案：`tcd batch` 批量并行启动。原定 v0.4.0，未纳入 |
| [research/2026-08-structured-output-vs-screen-scraping.md](research/2026-08-structured-output-vs-screen-scraping.md) | 检测层调研。结论：交互式为主、结构化为辅，含重新评估的触发条件 |
| [research/acp-vs-tmux-comprehensive-report.md](research/acp-vs-tmux-comprehensive-report.md) | ACP 协议深度调研（2026-03）。**结论已被上一篇修正**，保留是因为它仍是本仓唯一的 ACP 协议参考 |

仓库根目录还有 [README.md](../README.md)（命令速查与用法）和
[CHANGELOG.md](../CHANGELOG.md)（版本变更，含 breaking change）。
