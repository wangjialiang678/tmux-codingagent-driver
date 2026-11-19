# Antigravity CLI 接入

TCD 的 `agy` provider 会在独立 tmux 服务中启动本机官方 Antigravity CLI，并通过交互界面提交任务。它使用 CLI 当前登录的账号；TCD 不另行保存或配置 Antigravity 凭据。可用模型由本机 CLI 提供，使用 `agy models` 查看。

```bash
tcd start -p agy --model gemini-4-neon-medium -d /path/to/workspace -m '任务内容' --json
tcd check <job-id>
tcd output <job-id>
tcd send <job-id> '继续处理下一步'
tcd kill <job-id>
```

`--model` 和 `--effort` 会作为当前 CLI 会话参数传入。TCD 会在会话级设置中清除可能切换认证通道的模型配置，并隔离相关环境变量，使任务使用 Antigravity CLI 当前登录的账号；不会修改用户的 CLI 设置文件。现有 HTTP(S) 代理设置优先；macOS 没有显式代理变量时，TCD 会尝试使用系统 HTTP(S) 代理。

送达状态 `unconfirmed` 表示 TCD 无法从终端输出确认该消息已进入新一轮处理，不代表任务一定没有启动。可用 `tcd check` 和 `tcd output` 查看当前状态与输出；送达不明确时，TCD 不会自动重发，以免重复执行任务。

TCD 驱动的是本机 Antigravity CLI 交互界面，不能保证 CLI 所有版本的终端界面变化都能被自动识别。首次使用或 CLI 更新后，请核对任务输出及实际产物。使用者应自行确认其使用方式符合 Antigravity 当前条款和账号政策。

官方资料：[CLI 安装与登录](https://antigravity.google/docs/install?tab=cli)、[CLI 命令与设置](https://antigravity.google/docs/cli/reference)。
