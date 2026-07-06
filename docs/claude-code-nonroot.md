# Claude Code 非 root 账户运行注意事项

本文说明为什么建议把 Claude Code companion channel 跑在非 root 账户中，以及在 Tidal Echo 这类 relay + Claude Code channel 架构里需要注意哪些配置、权限和排查点。

## 为什么不要直接用 root 跑 Claude Code

Claude Code 是一个可以读写项目文件、执行工具、调用模型和加载本地 channel 的 agent。直接用 root 运行会带来几个风险：

1. 误操作影响范围过大：模型或工具写错路径时可能改到系统级文件。
2. 文件所有权混乱：生成的代码、缓存、日志、lockfile 都变成 root 所有，后续普通用户无法维护。
3. 凭据边界不清：Claude Code 的配置、模型 key、SSH/Git 凭据容易和 `/root` 的其它敏感文件混在一起。
4. 服务排查困难：进程身份、`HOME`、配置目录不一致时，很难判断它到底读了哪份配置。

推荐做法是创建一个专用用户，例如 `claude-code`，只让它运行 Claude Code companion channel。

## 建议的职责边界

推荐分工：

```text
root / service-manager:
  启动 Tidal Echo relay、本地 gateway、Tailscale、系统服务
  管理 systemd/service-manager、端口、证书、系统网络

claude-code:
  只运行 Claude Code companion channel
  读取自己的 ~/.claude 配置
  通过 /channel/in 连接 relay
  收网页消息并调用 /channel/out 回复
```

这样即使 Claude Code 出错，影响范围也主要限制在专用用户可访问的项目和配置内。

## 创建专用用户

```bash
useradd -m -s /bin/bash claude-code
```

如果用户已存在，确认 HOME 和 shell：

```bash
getent passwd claude-code
```

期望 HOME 类似：

```text
/home/claude-code
```

## 启动时必须设置正确 HOME

推荐启动方式：

```bash
sudo -u claude-code -H bash -lc '
  set -a
  . ~/.claude/claude-code-deepseek.env
  set +a
  cd /root/Tidal_Echo
  exec claude --dangerously-load-development-channels server:companion
'
```

关键点是 `sudo -u claude-code -H`：

- `-u claude-code`：进程身份切到专用用户。
- `-H`：把 `HOME` 设置为 `/home/claude-code`。
- `bash -lc`：按登录 shell 风格加载环境，保证 PATH 和 shell 展开一致。

不要只写：

```bash
sudo -u claude-code claude ...
```

这种写法容易出现进程用户是 `claude-code`，但 `HOME` 或 PATH 仍不符合预期的情况。

## Claude Code 配置目录

非 root 用户运行后，Claude Code 默认读取：

```bash
/home/claude-code/.claude/
```

不是：

```bash
/root/.claude/
```

如果 root 账户里已经配置好了 Claude Code，可以只复制 Claude Code 必需配置：

```bash
mkdir -p /home/claude-code/.claude
cp -a /root/.claude/. /home/claude-code/.claude/
chown -R claude-code:claude-code /home/claude-code/.claude
chmod -R go-rwx /home/claude-code/.claude
```

常见需要检查的文件：

```bash
/home/claude-code/.claude/claude-code-deepseek.env
/home/claude-code/.claude/settings.json
/home/claude-code/.claude/settings.local.json
/home/claude-code/.claude.json
```

不要把整个 `/root`、`/root/.ssh`、`/root/.config` 直接复制给 `claude-code`。只复制 Claude Code 运行必需的配置。

## 环境变量

Claude Code companion channel 必须拿到 relay 地址和 relay secret。具体变量名取决于 channel 配置，但至少要确认它能连接：

```bash
http://127.0.0.1:3011/channel/in
```

如果使用环境文件，启动时要显式 source：

```bash
set -a
. ~/.claude/claude-code-deepseek.env
set +a
```

验证环境是否生效：

```bash
sudo -u claude-code -H bash -lc '
  set -a
  . ~/.claude/claude-code-deepseek.env
  set +a
  env | grep -E "RELAY|ANTHROPIC|DEEPSEEK|OPENAI|DASHSCOPE" | sed "s/=.*/=<set>/"
'
```

注意：排查时不要把真实 key 打到日志、issue 或 commit 里。

## 项目目录权限

如果项目放在：

```bash
/root/Tidal_Echo
```

普通用户默认无法进入 `/root`。`claude-code` 要运行项目里的 channel 或读取仓库文件，需要同时满足：

```bash
/root
/root/Tidal_Echo
```

两层权限都允许访问。

临时可用但不够干净的做法：

```bash
chmod o+x /root
chown -R claude-code:claude-code /root/Tidal_Echo
```

更推荐把仓库放到普通服务目录：

```bash
/opt/Tidal_Echo
/home/claude-code/Tidal_Echo
```

然后只给 `claude-code` 访问项目所需的读写权限。

常见需要写权限的位置：

```bash
backend/
web/
channel/
scripts/
```

运行态和密钥建议继续放到单独目录，例如：

```bash
/root/.config/tidal-echo
/home/claude-code/.claude
```

不要为了省事把整个 `/root` 改成普通用户可读写。

## PATH 和可执行文件

root 能找到 `claude`，不代表 `claude-code` 也能找到。

检查：

```bash
sudo -u claude-code -H bash -lc 'which claude'
sudo -u claude-code -H bash -lc 'which node'
sudo -u claude-code -H bash -lc 'which bun'
sudo -u claude-code -H bash -lc 'which git'
```

如果 `claude` 只安装在 root 私有路径，例如：

```bash
/root/.local/bin/claude
```

应给 `claude-code` 单独安装 Claude Code，或在它自己的 shell 配置/环境文件中补 PATH。不要依赖 root 的私有 PATH。

## Git 权限

如果希望 Claude Code 也能提交代码，需要给 `claude-code` 配 Git 身份：

```bash
sudo -u claude-code -H git config --global user.name "Your Name"
sudo -u claude-code -H git config --global user.email "you@example.com"
```

如果通过 SSH 推送，需要配置：

```bash
/home/claude-code/.ssh/
```

权限：

```bash
chown -R claude-code:claude-code /home/claude-code/.ssh
chmod 700 /home/claude-code/.ssh
chmod 600 /home/claude-code/.ssh/id_*
```

如果通过 HTTPS 推送，需要确认该用户有自己的 credential helper 或 token。不要复用 root 的 Git 凭据目录。

## tmux / service-manager 启动

用 tmux 运行时：

```bash
tmux new-session -d -s tidal-echo-claude "sudo -u claude-code -H bash -lc '
  set -a
  . ~/.claude/claude-code-deepseek.env
  set +a
  cd /root/Tidal_Echo
  exec claude --dangerously-load-development-channels server:companion
'"
```

Claude Code 启动 development channel 时可能出现确认框。无人值守时需要自动送一次 Enter：

```bash
sleep 3
tmux send-keys -t tidal-echo-claude Enter
```

服务化启动时，也要确保服务命令中包含：

```bash
sudo -u claude-code -H bash -lc '...'
```

并且环境文件、工作目录、PATH 都按 `claude-code` 用户视角验证过。

## 如何判断真的接上 relay

看 relay 健康状态：

```bash
curl http://127.0.0.1:23087/relay/healthz
```

正常应看到：

```json
{"ok":true,"plugin_subs":1,"app_subs":0}
```

关键字段：

- `plugin_subs=1`：Claude Code companion channel 已经连上 `/channel/in`。
- `plugin_subs=0`：Claude Code 没连上 relay，网页发消息不会被 Claude Code 收到。
- `app_subs`：前端 PWA 当前是否保持 SSE 连接。

## 日志排查

常用命令：

```bash
tmux capture-pane -pt tidal-echo-claude -S -200
curl http://127.0.0.1:23087/relay/healthz
service-manager logs tidal-echo
```

重点看：

1. Claude Code 进程是否还在。
2. 是否卡在 development channel 确认框。
3. 是否读到了 `~/.claude` 下的模型配置。
4. 是否拿到了 relay secret。
5. 是否持续连接 `/channel/in`。
6. 网页发消息后，Claude Code 窗口是否出现 channel 事件。
7. Claude Code 回复后，relay 是否收到 `/channel/out`。

## 常见问题

### `plugin_subs=0`

说明 Claude Code channel 没连上 relay。检查：

```bash
ps -ef | grep claude
tmux capture-pane -pt tidal-echo-claude -S -200
sudo -u claude-code -H bash -lc 'echo "$HOME"; which claude'
```

再确认环境文件里 relay 地址和 secret 正确。

### `permission denied`

通常是项目目录或配置目录权限不对。

检查：

```bash
namei -l /root/Tidal_Echo
sudo -u claude-code -H bash -lc 'cd /root/Tidal_Echo && pwd'
```

如果 `cd` 都失败，说明目录执行权限不足。

### root 能跑，`claude-code` 不能跑

通常是 PATH、HOME 或 `.claude` 配置没迁移。

分别检查：

```bash
sudo -u claude-code -H bash -lc 'echo "$HOME"'
sudo -u claude-code -H bash -lc 'ls -la ~/.claude'
sudo -u claude-code -H bash -lc 'which claude'
```

### Claude Code 收到消息但不回复

检查模型环境变量和 Claude Code 配置是否真的在 `claude-code` 用户下可用。不要只看 root 用户下是否可用。

## 安全底线

1. 不要让 Claude Code 拿 root shell。
2. 不要把 root 的 SSH key、系统配置、云厂商凭据整体复制给 `claude-code`。
3. API key 文件权限设为 `600` 或更严格。
4. 真实 key 不写进仓库。
5. 只给项目目录必要权限。
6. 以 `plugin_subs=1` 作为 channel 在线的最终判断。

最关键的一句话：

> Claude Code 可以非 root 跑，但它必须有自己的 HOME、自己的 `.claude` 配置、正确的环境变量、可访问的项目目录，并且 relay health 里 `plugin_subs=1` 才算真正接上。
