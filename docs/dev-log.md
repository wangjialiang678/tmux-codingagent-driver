# 开发过程日志

<!-- 执行阶段每次验证运行后自动追加记录 -->

---
## Round 1 — 2026-03-02 Step 1-9 单元测试回归

阶段: P0
触发原因: Steps 1-9 实现完成（项目脚手架、tmux_adapter、provider、job、codex provider、notify_hook、output_cleaner、collector、CLI）

### 验证结果
| 测试项 | 判定标准 | 实际命令 | 结果 |
|--------|---------|---------|------|
| P0-1: 依赖安装 | uv sync 退出码=0 | `uv sync` | PASS |
| P0-2: 模块导入 | import 不报错 | `uv run python -c "import tcd"` | PASS |
| P0-3: 编译检查 | py_compile 无错误 | `uv run tcd --help`（隐式） | PASS |
| P0-4: 单元测试 | pytest 退出码=0 | `uv run pytest tests/ -v` → 62/62 passed | PASS |
| P0-5: CLI 入口 | tcd --help 含子命令 | `uv run tcd --help` | PASS |
| P0-6: tmux 检测 | 无 tmux 给清晰错误 | 通过单元测试验证 | PASS |

---
## Round 2 — 2026-03-02 Step 10 E2E 集成测试（首次）

阶段: P1 端到端
触发原因: Step 10 — 真实 Codex CLI E2E 测试

### 失败分析

- **测试项**: P1-3a: Codex 启动
- **失败原因 1**: `Unknown provider: 'codex'. Available: (none)` — CodexProvider 模块未在 import 时注册
- **修复操作**: 在 `src/tcd/__init__.py` 添加 `import tcd.providers.codex  # noqa: F401`
- **尝试次数**: 1

- **测试项**: P1-3b / P1-5a: 完成检测 / wait
- **失败原因 2**: `tcd wait` 超时（exit 2），Codex TUI 收到 prompt 文本但未提交（Enter 无效）
- **根因分析**: `_escape_single_quotes()` 将 `'` 转为 `'\''`，但 `subprocess.run` 用列表参数不经 shell，所以转义字符被原样发送到 TUI，导致文本损坏（`'\''Hello'\''` 而非 `'Hello'`）
- **修复操作 A**: 移除 `_escape_single_quotes`，改用 tmux `send-keys -l`（literal 模式）
- **尝试次数**: 1

- **失败原因 3**: 修复后 prompt 文本正确，但 Enter 仍未触发提交
- **根因分析**: Codex TUI 初始化慢（>1s），`cli.py` 只等了 1 秒就注入 prompt。文本注入后紧接着的 Enter 被 TUI 吞掉
- **修复操作 B**:
  1. `send_keys` 中在文本和 Enter 之间加 0.2s 延迟
  2. `cli.py` 中将固定 `sleep(1)` 改为轮询 TUI 就绪（检测 `›` 字符，最多 10s）
- **尝试次数**: 2

### 命令调整
- 原建议命令: 固定 `time.sleep(1)` 等待 TUI
- 实际使用: 轮询 `capture_pane` 检测 `›` 字符
- 调整原因: Codex TUI 初始化时间不固定，固定等待不可靠

---
## Round 3 — 2026-03-02 Step 10 E2E 集成测试（修复后）

阶段: P0 + P1 全量
触发原因: 修复 send_keys 和 TUI 等待后的全量验证

### P0 验证结果
| 测试项 | 判定标准 | 实际命令 | 结果 |
|--------|---------|---------|------|
| P0-1: 依赖安装 | uv sync 退出码=0 | `uv sync` | PASS |
| P0-2: 模块导入 | import 不报错 | `uv run python -c "import tcd; print(tcd.__version__)"` → 0.1.0 | PASS |
| P0-3: 编译检查 | 无错误 | `uv run tcd --help` 正常输出 | PASS |
| P0-4: 单元测试 | pytest 退出码=0 | `uv run pytest tests/ -v` → 61/61 passed | PASS |
| P0-5: CLI 入口 | 含子命令 | `uv run tcd --help` → 列出 10 个子命令 | PASS |
| P0-6: 子命令帮助 | 所有 --help OK | 逐一验证 9 个子命令 --help | PASS |

### P1 验证结果（E2E 真实 Codex CLI）
| 测试项 | 判定标准 | 实际命令 | 结果 |
|--------|---------|---------|------|
| P1-1a: 创建/销毁 session | exists 正确 | pytest test_tmux_adapter | PASS |
| P1-1b: send_keys 短文本 | capture 含内容 | pytest test_tmux_adapter | PASS |
| P1-1c: send_long_text 长文本 | capture 含内容 | pytest test_tmux_adapter | PASS |
| P1-1d: capture_pane | 返回非空 | pytest test_tmux_adapter | PASS |
| P1-2a: 创建 Job | 退出码=0, 含 hex ID | `tcd start -p codex -m "..."` → Job 03439993 | PASS |
| P1-2b: JSON 持久化 | JSON 可解析 | `cat ~/.tcd/jobs/03439993.json \| python3 -m json.tool` | PASS |
| P1-2c: Job 列表 | 含 ID 和状态 | `tcd jobs` → 表格输出 | PASS |
| P1-2d: Job 列表 JSON | 合法 JSON 数组 | `tcd jobs --json \| python3 -m json.tool` | PASS |
| P1-2e: Job 状态 | 显示 status/turn_count | `tcd status 03439993` | PASS |
| P1-2f: Job 状态 JSON | 合法 JSON | `tcd status 03439993 --json` → 含 all fields | PASS |
| P1-2g: Kill Job | kill 后 status=failed | `tcd kill 03439993` + `tcd status` → failed | PASS |
| P1-2h: Clean Jobs | JSON 被删除 | `tcd clean` + `tcd jobs` → No jobs | PASS |
| P1-2i: 无效 ID 报错 | 退出码≠0, 友好消息 | `tcd status nonexistent123` → "not found" | PASS |
| P1-3a: Codex 启动 | tmux session 存在 | `tmux has-session -t tcd-codex-03439993` → 0 | PASS |
| P1-3b: 完成检测 | check exit=0, signal 存在 | `tcd check` → 0, `.turn-complete` 存在 | PASS |
| P1-3c: 响应收集 | 无 ANSI 序列 | `tcd output` + python3 ANSI 检查 → 0 sequences | PASS |
| P1-3d: session 解析 | 输出含摘要 | `tcd output` → 含 Codex 回复内容 | PASS |
| P1-4a: 发送后续指令 | turn_count 递增 | `tcd send "..."` → turn_count: 1→2 | PASS |
| P1-4b: send 后检测 | idle exit=0 | `tcd wait` → 0, `tcd check` → 0 | PASS |
| P1-5a: wait 正常完成 | exit=0 | `tcd wait --timeout 30` → 0 | PASS |
| P1-5b: wait 超时 | exit=2 | `tcd wait --timeout 1` → 2 | PASS |
| P1-5c: attach | 不报错 | 手动验证（跳过，需交互） | SKIP |

### 摘要
- P0: 6/6 PASS
- P1: 21/22 PASS, 1 SKIP (P1-5c attach 需手动交互)
- 总修复次数: 3（provider 注册、send_keys 转义、TUI 等待）
- 无振荡（修 A 破 B）

---
## Round 4 — 2026-03-02 Code Review 修复

阶段: REVIEW → 修复
触发原因: code-review skill 发现 4 个严重问题

### 修复内容
| 问题 | 文件 | 修复方式 |
|------|------|---------|
| file_path 未验证+资源泄露 | cli.py:297 | 改用 `with open()` + 互斥检查 |
| build_script_command 注入 | tmux_adapter.py:160 | 使用 `shlex.quote()` 转义 log_file |
| notify_hook 路径注入 | codex.py:47 | 改用 `json.dumps()` 序列化路径列表 |
| send_long_text 临时文件泄露 | tmux_adapter.py:106 | 改用 `finally` 块清理 |

### 验证结果
- `uv run pytest tests/ -v` → 62/62 passed（含新增 path quoting 测试）
- 无回归

---
## Round 5 — 2026-03-02 Phase 2-4 完整实施

阶段: P0 + P1 (Phase 2 Claude, Phase 3 Gemini, Phase 4 SDK + README)
触发原因: Phase 2-4 全部步骤实施完成

### 实施内容

#### Phase 2: Claude Code Provider
- `src/tcd/marker_detector.py` — 共享 TCD_REQ/TCD_DONE marker 协议
- `src/tcd/idle_detector.py` — 空闲检测（capture-pane 对比）
- `src/tcd/providers/claude.py` — Claude Code provider（含 `--dangerously-skip-permissions`、`unset CLAUDECODE`）
- 关键修复: `send_text()` 对含换行的 marker prompt 改用 `send_long_text()`（bracketed paste `-p`），解决 Ink TUI 吞 Enter 的问题
- `tui_ready_indicator = "❯"`, 信任对话自动处理

#### Phase 3: Gemini CLI Provider
- `src/tcd/providers/gemini.py` — Gemini CLI provider（`--yolo` 模式）
- `tui_ready_indicator = "Type your message"`, 信任对话 + 重启等待
- 关键修复: cli.py 增加 Gemini 信任对话检测（"Do you trust the files in this folder"）+ 重启后二次等待

#### Phase 4: Python SDK + README
- `src/tcd/sdk.py` — TCD 类（start/check/wait/output/send/jobs/kill/clean）
- `README.md` — 完整文档（CLI 参考、SDK 示例、Provider 支持表、架构图）

### 关键 bug 修复
| 问题 | 根因 | 修复 |
|------|------|------|
| Claude prompt 未提交 | `send-keys -l` 逐字符发送换行=Enter，但 0.2s 延迟不够 | 含换行文本改用 `paste-buffer -p`（bracketed paste） |
| Gemini prompt 丢失 | 信任对话重启后 TUI ready indicator 二次出现，但 prompt 在第一次就发了 | 增加信任对话检测 + 重启等待 |

### 测试结果
| 测试项 | 结果 |
|--------|------|
| 全量单元测试 | 119/119 PASS |
| Codex E2E (Round 3 已验证) | PASS |
| Claude E2E: start → check → output → send → check → kill | PASS |
| Gemini E2E: start → check → send → check → kill | PASS |
| Python SDK 测试 | 18/18 PASS |

### Code Review 修复
| 问题 | 修复 |
|------|------|
| gemini.py `_extract_response` 死代码 | 清理未使用的 `in_response` 逻辑 |
| SDK `send_text` 返回值未检查 | start/send 中检查返回值，失败抛异常 |
| SDK check 中 `except Exception` 过宽 | 改为 `except (OSError, ValueError, KeyError)` |

### 最终验证摘要
- P0: 6/6 PASS（依赖安装、模块导入、编译检查、119 测试、CLI 入口、子命令帮助）
- P1: 全部 3 个 Provider E2E 验证通过
- Python SDK: 18 测试通过
- 总测试: 119
- 代码审查: 通过（严重问题已修复）
