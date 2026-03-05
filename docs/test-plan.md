# tcd 闭环测试方案

**版本**: v0.1.0
**日期**: 2026-03-02
**状态**: COMPLETED（Phase 1-4 全部验证通过，119 tests pass）

---

## P0: 基础构建检查

每完成一个逻辑单元后必须全部通过。

- [x] **P0-1: 依赖安装**
  判定标准: `uv sync` 退出码=0，无报错
  建议命令: `cd /Users/michael/projects/AI\ 工作流/tmux-codingagent-driver && uv sync`

- [x] **P0-2: 模块导入**
  判定标准: `from tcd import ...` 不报 ImportError
  建议命令: `uv run python -c "from tcd.tmux_adapter import TmuxAdapter; print('OK')"`

- [x] **P0-3: 类型检查**
  判定标准: mypy 或 pyright 无 error（warning 可接受）
  建议命令: `uv run python -m py_compile src/tcd/*.py`（最低限度编译检查）

- [x] **P0-4: 单元测试**
  判定标准: `pytest` 退出码=0，所有测试通过
  建议命令: `uv run pytest tests/ -v`

- [x] **P0-5: CLI 入口可执行**
  判定标准: `tcd --help` 退出码=0，输出包含 "start" "check" "output" 等子命令
  建议命令: `uv run tcd --help`

- [x] **P0-6: tmux 可用性检测**
  判定标准: tmux 未安装时给出清晰错误信息（含安装建议），不抛 traceback
  建议命令: `PATH=/usr/bin:/bin uv run tcd start -p codex -m "test" 2>&1`（临时移除 tmux PATH，视环境调整）

---

## P1: 核心功能验证（Phase 1 — Codex Driver MVP）

### 功能 1: tmux Adapter 基础操作

- [x] **P1-1a: 创建和销毁 session** [CLI]
  判定标准: `create_session` 后 `session_exists` 返回 True；`kill_session` 后返回 False
  建议命令: pytest 单元测试（需 tmux 集成测试）
  ```python
  # 示例测试逻辑
  adapter = TmuxAdapter()
  adapter.create_session("tcd-test-001", "echo hello; read", "/tmp")
  assert adapter.session_exists("tcd-test-001") == True
  adapter.kill_session("tcd-test-001")
  assert adapter.session_exists("tcd-test-001") == False
  ```

- [x] **P1-1b: send_keys 短文本注入** [CLI]
  判定标准: 注入文本 < 5000 字符后，capture_pane 能读到注入的内容
  建议命令: pytest 集成测试

- [x] **P1-1c: send_long_text 长文本注入** [CLI]
  判定标准: 注入 6000+ 字符文本后，capture_pane 能读到完整内容（或其末尾部分）
  建议命令: pytest 集成测试

- [x] **P1-1d: capture_pane 读取输出** [CLI]
  判定标准: 返回字符串非空，包含 session 中实际显示的内容
  建议命令: pytest 集成测试

### 功能 2: Job 管理生命周期

- [x] **P1-2a: 创建 Job** [CLI]
  判定标准: `tcd start -p codex -m "echo hello"` 退出码=0，输出包含 "Job started:" 和 8 字符 hex ID
  建议命令: `uv run tcd start -p codex -m "echo hello" -d /tmp`

- [x] **P1-2b: Job JSON 持久化** [文件]
  判定标准: `~/.tcd/jobs/{id}.json` 存在，JSON 可被 jq 解析，含 id/provider/status/prompt 字段
  建议命令: `cat ~/.tcd/jobs/*.json | jq .`

- [x] **P1-2c: Job 列表** [CLI]
  判定标准: `tcd jobs` 退出码=0，输出包含已创建 Job 的 ID 和状态
  建议命令: `uv run tcd jobs`

- [x] **P1-2d: Job 列表 JSON** [CLI]
  判定标准: `tcd jobs --json` 输出是合法 JSON 数组，每项含 id/provider/status
  建议命令: `uv run tcd jobs --json | jq .`

- [x] **P1-2e: Job 状态查询** [CLI]
  判定标准: `tcd status {id}` 退出码=0，显示 status/provider/turn_count 等信息
  建议命令: `uv run tcd status {id}`

- [x] **P1-2f: Job 状态 JSON** [CLI]
  判定标准: `tcd status {id} --json` 输出合法 JSON，含 id/status/turn_state
  建议命令: `uv run tcd status {id} --json | jq .`

- [x] **P1-2g: Kill Job** [CLI]
  判定标准: `tcd kill {id}` 退出码=0，之后 `tcd status {id}` 显示 failed
  建议命令: `uv run tcd kill {id} && uv run tcd status {id}`

- [x] **P1-2h: Clean Jobs** [CLI]
  判定标准: `tcd clean` 退出码=0，completed/failed 的 Job JSON 文件被删除
  建议命令: `uv run tcd clean && ls ~/.tcd/jobs/`

- [x] **P1-2i: 无效 Job ID 友好报错** [CLI]
  判定标准: `tcd status invalid-id` 退出码≠0，stderr 含友好错误信息，无 Python traceback
  建议命令: `uv run tcd status nonexistent123 2>&1`

### 功能 3: Codex Provider 启动与完成检测

- [x] **P1-3a: Codex 启动** [CLI]
  判定标准: `tcd start -p codex -m "say hello"` 后，对应 tmux session 存在（`tmux has-session -t tcd-codex-{id}` 退出码=0）
  建议命令:
  ```bash
  JOB_ID=$(uv run tcd start -p codex -m "say hello" -d /tmp | grep -oE '[a-f0-9]{8}')
  tmux has-session -t "tcd-codex-$JOB_ID"
  ```

- [x] **P1-3b: Codex 完成检测（notify-hook）** [CLI]
  判定标准: Codex 完成后，`tcd check {id}` 退出码=0（idle），signal file `~/.tcd/jobs/{id}.turn-complete` 存在
  建议命令:
  ```bash
  uv run tcd wait $JOB_ID --timeout 120
  uv run tcd check $JOB_ID
  echo "exit code: $?"
  ls ~/.tcd/jobs/${JOB_ID}.turn-complete
  ```

- [x] **P1-3c: Codex 响应收集** [CLI]
  判定标准: `tcd output {id}` 输出非空，不含 ANSI 转义序列（无 `\x1b[` 或 `\033[`），不含 TUI 噪音行
  建议命令:
  ```bash
  OUTPUT=$(uv run tcd output $JOB_ID)
  echo "$OUTPUT"
  echo "$OUTPUT" | grep -P '\x1b\[' && echo "FAIL: contains ANSI" || echo "PASS: clean output"
  ```

- [x] **P1-3d: Codex session file 解析** [CLI]
  判定标准: `tcd output {id}` 能从 Codex JSONL session 文件解析出摘要文本
  建议命令: `uv run tcd output $JOB_ID`

### 功能 4: 多轮对话（send）

- [x] **P1-4a: 发送后续指令** [CLI]
  判定标准: `tcd send {id} "追加指令"` 退出码=0，`tcd status {id} --json` 中 turn_count 递增
  建议命令:
  ```bash
  uv run tcd send $JOB_ID "now add a test"
  sleep 2
  uv run tcd status $JOB_ID --json | jq .turn_count
  ```

- [x] **P1-4b: send 后完成检测** [CLI]
  判定标准: send 后 check 先返回 exit 1（working），完成后返回 exit 0（idle）
  建议命令:
  ```bash
  uv run tcd send $JOB_ID "add comments to the code"
  uv run tcd check $JOB_ID; echo "immediate: $?"
  uv run tcd wait $JOB_ID --timeout 120
  uv run tcd check $JOB_ID; echo "after wait: $?"
  ```

### 功能 5: 阻塞等待与 attach

- [x] **P1-5a: wait 正常完成** [CLI]
  判定标准: `tcd wait {id} --timeout 120` 退出码=0（completed）
  建议命令: `uv run tcd wait $JOB_ID --timeout 120; echo "exit: $?"`

- [x] **P1-5b: wait 超时** [CLI]
  判定标准: `tcd wait {id} --timeout 1` 退出码=2（timeout）
  建议命令: 先启动一个长任务，然后 `uv run tcd wait $JOB_ID --timeout 1; echo "exit: $?"`

- [x] **P1-5c: attach 连接** [CLI]（手动验证 PASS）
  判定标准: `tcd attach {id}` 能进入 tmux session（人工确认即可，或检查命令不报错）
  建议命令: `uv run tcd attach $JOB_ID`（手动 Ctrl+B D 退出）
  注: 此项可在最终集成测试时手动验证

---

## P1 补充测试（Phase 2/3 扩展，Phase 1 不执行）

以下测试项在 Phase 2/3 实现对应 Provider 后才执行：

- [x] **P1-EXT-1: Claude Provider 启动与 marker 完成检测** [CLI]（Phase 2 E2E 验证通过）
- [x] **P1-EXT-2: Gemini Provider 启动与空闲检测 fallback** [CLI]（Phase 3 E2E 验证通过）
- [ ] **P1-EXT-3: 并行 3 个不同 Provider Job** [CLI]（未测试）
- [x] **P1-EXT-4: Python SDK `from tcd import TCD`** [CLI]（18 单元测试通过）

---

## 手动测试（不纳入自动化 P1）

- [x] **手动-1: attach 交互调试**
  操作: 启动 Job → `tcd attach` → 在 tmux 中观察 AI 执行 → Ctrl+B D 退出
  判定: 能看到 AI CLI 实时输出，退出 attach 不影响 Job 执行

- [x] **手动-2: AI CLI 未安装时报错**
  操作: `tcd start -p codex -m "test"` 在 codex 不在 PATH 时执行
  判定: 给出清晰安装指引，不抛 traceback

---

## 测试 Fixtures

本项目 P1 测试主要依赖实际的 AI CLI 工具运行。需确保：
- Codex CLI 已登录且 API key 有效
- 测试用的 prompt 使用简单任务（如 "say hello" "write a hello world"），减少等待时间
- 测试工作目录使用 `/tmp/tcd-test-{timestamp}/`，测试后清理

---

## 验证纪律

- **判定标准已锁定**：上述所有判定标准在用户确认后不允许修改（除非用户明确批准）
- **验证命令可调整**：建议命令允许根据实际环境灵活调整，调整时记录到 docs/dev-log.md
- **每次修复只改业务代码**：不允许放宽判定标准来让测试通过
