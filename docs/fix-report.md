# 修复报告（2026-03-05）

按 `docs/code-review.md` 底部编排者批注与用户清单执行，逐项直接改代码并在每项后运行：

`UV_CACHE_DIR=.uv-cache uv run pytest tests/ -q`

## 修复清单结果

| 项目 | 结果 | 说明 |
|---|---|---|
| C-2 | 已修复 | Claude/Gemini 在 `working -> idle/context_limit` 时推进 `turn_count`（幂等），并将 `send` 的 `req_id` 改为基于当前 `turn_count`，避免多轮冲突。 |
| C-3 | 已修复 | `codex/claude/gemini` 的 `model` 参数增加白名单校验（`[a-zA-Z0-9._:/-]+`）并使用 `shlex.quote` 进行 shell 转义。 |
| M-1 | 已修复 | Gemini `_extract_between_markers` 改为只提取同一 `req_id` 的“首个 DONE（提示回显）与最终 DONE（模型输出）”之间内容，避免把 prompt 当回复。 |
| M-4 | 已修复 | `cli/sdk` 的 `_refresh_status` 对 session 消失做区分：`turn_state=working` 标记 `failed`，否则标记 `completed`。 |
| m-1 | 已修复 | CLI `start/send` 现在检查 `tmux.send_text()` 返回值，失败时落盘 `failed`+错误信息并退出。 |
| m-3 | 已修复 | 缩小 `except Exception`：`cli` 改为捕获明确异常并记录 traceback；`gemini` 的 signal 读取异常也改为窄捕获+日志。 |
| C-1（可选） | 已修复 | `scan_for_marker` 从子串匹配改为严格整行匹配；前缀模式要求时间戳数字，避免 `turn 1` 误命中 `turn 10`。 |
| M-6 | 未改（按要求） | 用户明确说明“已修复，不需要改”。 |

## 主要改动文件

- `src/tcd/cli.py`
- `src/tcd/sdk.py`
- `src/tcd/providers/codex.py`
- `src/tcd/providers/claude.py`
- `src/tcd/providers/gemini.py`
- `src/tcd/marker_detector.py`
- `tests/test_cli.py`
- `tests/test_sdk.py`
- `tests/test_codex_provider.py`
- `tests/test_claude_provider.py`
- `tests/test_gemini_provider.py`
- `tests/test_marker_detector.py`

## 每项修复后的测试记录

1. C-2 后：`143 passed, 4 failed`
2. C-3 后：`146 passed, 4 failed`
3. M-1 后：`146 passed, 4 failed`
4. M-4 后：`150 passed, 4 failed`
5. m-1 后：`152 passed, 4 failed`
6. m-3 后：`152 passed, 4 failed`
7. C-1（可选）后：`156 passed, 4 failed`

## 当前剩余失败（环境相关）

始终失败的 4 项均为 tmux 适配器集成测试，失败点一致：当前环境无法成功创建/操作 tmux session（`tmux new-session` 返回非 0）。

- `tests/test_tmux_adapter.py::test_create_and_kill_session`
- `tests/test_tmux_adapter.py::test_send_keys_and_capture`
- `tests/test_tmux_adapter.py::test_send_long_text`
- `tests/test_tmux_adapter.py::test_send_text_auto_selects`
