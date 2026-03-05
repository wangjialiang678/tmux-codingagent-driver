# tcd 快速测试指南

从安装到在 Claude Code 中实际使用，5 分钟跑通。

---

## 第 0 步：安装

```bash
# 全局安装（推荐，这样任何终端/Claude Code 都能直接用 tcd 命令）
uv tool install /Users/michael/projects/AI\ 工作流/tmux-codingagent-driver

# 验证
tcd --help
```

如果不想全局装，也可以在项目目录用 `uv run tcd` 代替 `tcd`。

---

## 第 1 步：冒烟测试（不用 AI，验证 tmux 链路）

```bash
# 启动一个 bash session（不走任何 AI CLI）
tmux new-session -d -s tcd-smoke "echo 'TCD_SMOKE_OK'; sleep 30; read"

# 验证 tmux 能捕获输出
sleep 1
tmux capture-pane -t tcd-smoke -p | grep TCD_SMOKE_OK
# 应该输出包含 TCD_SMOKE_OK 的行

# 清理
tmux kill-session -t tcd-smoke
```

---

## 第 2 步：Codex 单任务测试

用 tcd 启动一个简单的 Codex 任务，验证完整链路。

```bash
# 创建临时工作目录
mkdir -p /tmp/tcd-test && cd /tmp/tcd-test

# 启动一个非常简单的任务
tcd start -p codex -m "在当前目录创建一个 hello.py，内容是 print('hello from codex')" -d /tmp/tcd-test

# 记下输出的 job_id，例如 a3f2b1c9

# 检查状态（可以多次运行）
tcd check <job_id>
echo $?  # 0=完成 1=运行中

# 等待完成（最多等 120 秒）
tcd wait <job_id> --timeout 120

# 查看结果
tcd output <job_id>

# 验证文件是否生成
cat /tmp/tcd-test/hello.py

# 清理
tcd clean
```

### 预期结果

- `tcd start` 输出 job_id 和 session name
- `tcd wait` 正常返回（exit 0）
- `tcd output` 显示 Codex 的回复
- `/tmp/tcd-test/hello.py` 存在且内容正确

### 如果失败

```bash
# 方法 1：进入 tmux session 看实际状态
tcd attach <job_id>
# Ctrl+B D 退出

# 方法 2：看 script 日志
cat ~/.tcd/jobs/<job_id>.log

# 方法 3：看 job 元数据
cat ~/.tcd/jobs/<job_id>.json
```

---

## 第 3 步：Claude Code 单任务测试

```bash
mkdir -p /tmp/tcd-test-claude

tcd start -p claude -m "在当前目录创建 greet.py，写一个函数 greet(name) 返回 f'Hello, {name}!'" -d /tmp/tcd-test-claude

tcd wait <job_id> --timeout 180  # Claude Code 启动慢，给够时间

tcd output <job_id>
cat /tmp/tcd-test-claude/greet.py

tcd clean
```

> 注：Claude Code 首次启动可能弹 trust dialog，tcd 会自动处理。如果等待超时，用 `tcd attach <job_id>` 看是否卡在了某个交互提示上。

---

## 第 4 步：在 Claude Code 会话中使用 tcd

这是最终目标场景——你在 Claude Code 中对话，Claude Code 通过 bash 工具调用 tcd 分派任务。

### 4.1 配置 CLAUDE.md

在你的项目 CLAUDE.md 中添加以下内容：

```markdown
## tcd: AI 任务分派器

当需要将独立的编码子任务委派给其他 AI 时，使用 tcd：

### 命令速查
- `tcd start -p <codex|claude|gemini> -m "<prompt>" -d <目录>` — 启动任务
- `tcd check <job_id>` — exit 0=完成, 1=运行中
- `tcd wait <job_id> --timeout 300` — 阻塞等待
- `tcd output <job_id>` — 获取结果
- `tcd send <job_id> "<追加指令>"` — 多轮对话
- `tcd jobs` — 查看所有任务
- `tcd kill <job_id>` — 终止任务

### 使用场景
- 需要并行做两件事时：启动 Codex 做子任务，自己继续主任务
- 需要代码审核时：让另一个 AI 审查当前代码
- 需要多视角时：让不同 AI 分别实现，对比结果
```

### 4.2 测试对话

启动 Claude Code，然后说：

> "用 tcd 启动一个 Codex 任务，让它在 /tmp/tcd-demo 目录创建一个 Python 计算器 CLI，支持加减乘除。等它完成后把结果给我看。"

Claude Code 应该会：
1. `tcd start -p codex -m "..." -d /tmp/tcd-demo`
2. `tcd wait <job_id>` 或轮询 `tcd check`
3. `tcd output <job_id>`
4. 将结果总结给你

### 4.3 并行测试

> "我需要一个用户管理模块。用 tcd 同时启动两个任务：让 Codex 在 /tmp/tcd-parallel 写后端 API（Express + TypeScript），让 Gemini 写前端页面（React）。分别等它们完成后汇总结果。"

---

## 第 5 步：多轮对话测试

```bash
# 启动任务
tcd start -p codex -m "搭建一个 Express.js 项目骨架" -d /tmp/tcd-multi

# 等第一轮完成
tcd wait <job_id>
tcd output <job_id>

# 追加指令
tcd send <job_id> "添加 JWT 认证中间件"
tcd wait <job_id>
tcd output <job_id>

# 再追加
tcd send <job_id> "添加 Dockerfile 和 docker-compose.yml"
tcd wait <job_id>
tcd output <job_id>

# 查看总状态
tcd status <job_id> --json
# turn_count 应该是 3
```

---

## 常见问题

### Q: tcd start 后一直 working，不完成

1. `tcd attach <job_id>` 进去看——可能卡在 update prompt 或 trust dialog
2. 手动操作后 Ctrl+B D 退出，tcd 会继续监控

### Q: tcd output 输出是空的

1. 可能 AI 还在运行——先 `tcd check <job_id>` 确认已完成
2. 查看原始日志：`cat ~/.tcd/jobs/<job_id>.log`
3. 用 `tcd output <job_id> --raw` 看未清洗的输出

### Q: Gemini CLI 总是超时

Gemini 经常不配合 marker 协议（不输出 TCD_DONE），tcd 会回退到空闲检测（15 秒无输出判定完成）。给 `--timeout` 设大一点。

### Q: 想看 Codex 修改了哪些文件

```python
# Python 方式
from tcd import TCD
from tcd.providers.codex import CodexProvider

tcd = TCD()
job = tcd.start("codex", "重构代码", cwd="/project")
tcd.wait(job.id)

provider = CodexProvider()
result = provider.parse_response_structured(job)
print(result.files_modified)  # ['src/main.py', 'src/utils.py']
print(result.tokens)          # {'input': 1234, 'output': 567}
```

---

## 测试清单

| # | 测试项 | 命令 | 预期 |
|---|--------|------|------|
| 1 | 安装 | `tcd --help` | 显示帮助 |
| 2 | Codex 单任务 | `tcd start -p codex ...` → `tcd wait` → `tcd output` | 文件生成 |
| 3 | Claude 单任务 | `tcd start -p claude ...` → `tcd wait` → `tcd output` | 文件生成 |
| 4 | Claude Code 内使用 | 在对话中让 Claude Code 调 tcd | 自动完成 |
| 5 | 多轮对话 | `tcd send` 追加指令 | turn_count 递增 |
| 6 | 并行任务 | 同时 start 2+ 个 job | `tcd jobs` 全部显示 |
| 7 | 失败恢复 | `tcd attach` 进入 session | 能看到 TUI |
| 8 | 清理 | `tcd clean` | 已完成 job 被移除 |
