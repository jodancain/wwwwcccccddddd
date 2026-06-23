# WeChatAI Windows 便携版使用教程

这个便携版把 WeChatAI 后端 API 和前端网页打包进一个 `WeChatAI.exe`。
别人拿到后解压即可运行，不需要安装 Node.js，也不需要手动启动前端。

## 1. 解压

把压缩包解压到一个普通英文或中文目录，例如：

```text
D:\Apps\WeChatAI_Portable
```

目录里至少会有：

```text
WeChatAI.exe
启动 WeChatAI.bat
.env.example
使用教程.md
```

第一次运行后会自动生成：

```text
.env
data\
```

## 2. 配置 API Key

用记事本打开 `.env`。

推荐先用 OpenAI 兼容接口：

```env
AI_PROVIDER=openai
OPENAI_API_KEY=你的_key
OPENAI_BASE_URL=https://api.openai.com/v1
OPENAI_MODEL=gpt-4o
```

如果你用 Claude 聊天记录 Agent，填写：

```env
ANTHROPIC_API_KEY=你的_key
ANTHROPIC_BASE_URL=
CLAUDE_MODEL=claude-sonnet-4-6
```

如果使用中转 API，把 `ANTHROPIC_BASE_URL` 或 `OPENAI_BASE_URL` 改成中转地址即可。
不要把真实 key 发给别人。

## 3. 启动

双击：

```text
WeChatAI.exe
```

或者双击：

```text
启动 WeChatAI.bat
```

启动后会自动打开浏览器：

```text
http://127.0.0.1:8090
```

看到网页后，黑色命令行窗口不要关。关闭窗口就会停止服务。

## 4. 微信聊天记录同步

使用前请确认：

1. Windows 桌面微信已经登录并保持运行。
2. WeChatAI 有权限读取本机微信数据。
3. 如果自动解密失败，在 `.env` 里填写 `WX_DB_DIR` 或 `WX_DB_KEY`。

数据默认保存在：

```text
data\
```

所有聊天记录和解密后的数据库都只保存在本机，不会上传到 WeChatAI 之外的服务器。

## 5. 微信里直接问 Agent

这个 EXE 内置的是 WeChatAI 本地 API 和网页。

如果你已经另外安装并登录了 OpenClaw 的 `WeixinClawBot`，推荐结构是：

```text
微信 WeixinClawBot -> OpenClaw 只转发 -> WeChatAI.exe 本地 API -> Claude/聊天记录 Agent
```

也就是说 OpenClaw 只是微信通道，真正回答问题的是本机 `WeChatAI.exe` 里的 Agent。

网页右下角的 `Agent` 按钮可以打开 Agent 控制台。这里可以查看：

- `transport_mode=openclaw_forward`：微信消息由 OpenClaw 插件转发到 `/api/agent/chat`，这是推荐模式。
- `local_polling`：后端直接轮询本地微信数据库里的入口会话；只有本地数据库能同步到 `WeixinClawBot` 会话时才会显示已绑定。
- 每日总结、智能路由、开发模式、自动执行和 Claude Code planner 开关。

如果状态里 `bound=false` 但 `openclaw_forward_ready=true`，说明 OpenClaw 转发模式可用，不代表微信入口不可用。

常用问题：

```text
总结最近聊天记录
今天微信有什么重点
帮我找最近谁提到了 ETH
总结和某个人的最近聊天
```

没有指定联系人时，Agent 会默认理解为“所有已同步微信聊天记录”。

## 6. 像 Codex 一样自我开发

便携版也带有开发者模式。开启后，你可以在微信里让 Agent 检查项目、提出代码修改、提出命令执行。

在 `.env` 里设置：

```env
AGENT_DEV_MODE_ENABLED=true
AGENT_DEV_WORKSPACE=D:\Apps\WeChatAI_Portable
AGENT_DEV_AUTO_APPLY=false
```

建议保持 `AGENT_DEV_AUTO_APPLY=false`。这样 Agent 只能自动读取和搜索项目；写文件、运行命令会先生成待确认动作。

确认方式：

```text
confirm 动作ID
```

或：

```text
确认 动作ID
```

示例：

```text
给这个项目加一个每天晚上 9 点自动总结微信聊天记录并发送给我的功能
```

Agent 会先分析项目，再给出待确认的文件修改或命令。确认后才会真正执行。

如果你在 Agent 控制台里临时打开“自动执行”，开发 Agent 会像 Codex 一样直接执行它自己提出的安全命令或文件修改。危险命令、删除文件、`git reset --hard`、`git clean` 等仍会被拒绝。

Claude Code planner 的执行顺序是：

```text
Claude Agent SDK -> Claude Code CLI -> Anthropic Messages fallback
```

如果第三方 `ANTHROPIC_BASE_URL` 不兼容 Claude Code CLI 的模型发现或模型名，系统会自动降级到 Messages fallback，不会让微信里的 Agent 卡死。

## 7. 常见问题

### 打不开网页

确认命令行窗口还开着，并访问：

```text
http://127.0.0.1:8090
```

如果提示端口被占用，修改 `.env`：

```env
APP_PORT=8092
```

然后重启 `WeChatAI.exe`。

### 改了配置没生效

修改 `.env` 后必须关闭并重新打开 `WeChatAI.exe`。

### Windows Defender 提示风险

这是本地打包 EXE 常见情况。你可以保留源代码和打包脚本，让对方在自己电脑上重新打包。

### 数据要迁移到另一台电脑

复制整个便携目录即可，尤其是：

```text
.env
data\
```

## 8. 给开发者重新打包

在项目根目录运行：

```powershell
powershell -ExecutionPolicy Bypass -File .\packaging\build_windows_exe.ps1
```

打包完成后产物在：

```text
dist\WeChatAI_Portable\
```
