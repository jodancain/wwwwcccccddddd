# WeChatAI - Cursor 版微信

AI 增强的微信桌面客户端。左侧实时显示微信对话，右侧集成 AI 助手面板，类似 Cursor 对 VS Code 的增强方式。

## 功能

- **实时消息同步** — 每 7 秒自动同步微信消息，WebSocket 推送更新
- **完整消息渲染** — 文本、图片、表情包、链接卡片、语音、视频、名片等
- **AI 助手面板** — 分析对话、生成回复建议、总结对话内容
- **多 AI 引擎** — 支持 Gemini、OpenAI、DeepSeek、本地模型 (Ollama/LM Studio)
- **时间轴** — 日历跳转 + 侧边时间轴滑块，快速定位历史消息
- **发送消息** — 通过 UI 自动化操作微信窗口发送消息
- **历史会话** — AI 对话记录持久化保存，切换对话自动加载历史

## 环境要求

- **Windows 10/11** (微信桌面版仅支持 Windows)
- **微信桌面版** — 已登录且保持运行
- **Python 3.10+**
- **Node.js 18+**

## 安装

### 1. 克隆项目

```bash
git clone <repo-url>
cd WeChatai
```

### 2. 安装后端依赖

```bash
cd backend
pip install -r requirements.txt
```

### 3. 安装前端依赖

```bash
cd frontend
npm install
```

### 4. 配置

```bash
# 在项目根目录
cp .env.example .env
# 编辑 .env，填入你的 AI API Key
```

必填项：
- `GEMINI_API_KEY` — 使用 Gemini 时填写 ([获取](https://aistudio.google.com/apikey))
- 或 `OPENAI_API_KEY` + `OPENAI_BASE_URL` — 使用 OpenAI/DeepSeek 时填写

同时将 `.env` 复制到 `backend/` 目录：
```bash
cp .env backend/.env
```

## 启动

### 开发模式（推荐）

需要两个终端：

**终端 1 — 后端：**
```bash
cd backend
python run.py
```

**终端 2 — 前端：**
```bash
cd frontend
npm run dev
```

打开浏览器访问 **http://localhost:5175**

### 生产模式（单服务器）

```bash
# 构建前端
cd frontend
npm run build

# 启动后端（会自动托管前端）
cd ../backend
python run.py
```

访问 **http://localhost:8090**

### 一键启动

Windows:
```bash
start.bat
```

## 验证与回归测试

启动后端和前端后，可以运行一键验证脚本：

```bash
scripts\verify_all.bat
```

或在 `frontend/` 目录中运行：

```bash
npm run verify
```

它会依次执行：
- Python 编译检查
- 后端运行时烟测（消息、联系人、同步、AI 聊天、人物画像、训练状态、开放 API）
- 前端烟测（Vite 入口、Vue 模块转换、前端代理）
- 前端生产构建

推送到 GitHub 时，`.github/workflows/verify.yml` 会自动执行不依赖本地微信数据的检查：源码健康、Python 编译和前端构建。运行时烟测仍需在本机启动后端和前端后执行。

如果需要指定地址：

```bash
scripts\verify_all.bat --backend-url http://127.0.0.1:8090 --frontend-url http://127.0.0.1:5175
```

如果需要覆盖会写出训练数据或触发同步的重型检查：

```bash
scripts\verify_all.bat --include-heavy
```

或：

```bash
cd frontend
npm run verify:heavy
```

## 使用说明

### 基本操作

1. 确保**微信桌面版已登录**并保持运行
2. 启动应用后，首次会自动解密并同步微信数据库（可能需要 30-60 秒）
3. 左侧显示对话列表，点击进入对话
4. 右侧 AI 助手面板可以：
   - 点击 **帮我回复** — AI 根据上下文生成回复
   - 点击 **总结对话** — AI 总结对话要点
   - 点击 **分析语气** — AI 分析对方语气和意图
   - 点击 **快速回复** — AI 生成 3 条回复建议
   - 直接输入问题 — 随意问 AI 关于对话的问题

### 时间轴

- 点击顶部 📅 按钮打开日历，选择日期跳转
- 拖动右侧时间轴滑块快速定位

### 发送消息

- 在消息底部输入框输入文字，按 Enter 发送
- AI 生成的回复可以点击"发送"直接发送到微信
- 发送前会弹出确认框，确认后通过 UI 自动化操作微信窗口

## 项目结构

```
WeChatai/
├── backend/                    # FastAPI 后端
│   ├── app/
│   │   ├── api/                # REST API 路由
│   │   ├── ai/                 # AI 提供商 (Gemini/OpenAI)
│   │   ├── sync/               # 消息同步引擎
│   │   ├── storage/            # SQLite 数据库
│   │   ├── wechat_reader/      # 微信数据库解密与解析
│   │   └── wechat_sender/      # 微信消息发送 (UI 自动化)
│   ├── requirements.txt
│   └── run.py
├── frontend/                   # Vue 3 + TypeScript 前端
│   ├── src/
│   │   ├── components/         # Vue 组件
│   │   ├── composables/        # 组合式函数
│   │   ├── api/                # API 客户端
│   │   └── styles/             # CSS 样式
│   ├── package.json
│   └── vite.config.ts
├── .env.example                # 配置模板
├── start.bat                   # Windows 一键启动
└── README.md
```

## API

| 端点 | 说明 |
|------|------|
| `GET /api/messages/conversations` | 对话列表 |
| `GET /api/messages/?talker=xxx` | 查询消息 |
| `GET /api/messages/dates?talker=xxx` | 日期列表（时间轴） |
| `GET /api/messages/by-date?talker=xxx&date=xxx` | 按日期跳转 |
| `POST /api/ai/chat` | AI 对话 |
| `POST /api/ai/chat/stream` | AI 流式对话 (SSE) |
| `POST /api/ai/suggest-replies` | AI 回复建议 |
| `GET /api/media/image/{local_id}` | 获取图片 |
| `POST /api/send/text` | 发送微信消息 |
| `GET /api/sync/status` | 同步状态 |

## 配置说明

| 变量 | 默认值 | 说明 |
|------|--------|------|
| `AI_PROVIDER` | `gemini` | AI 引擎：`gemini` 或 `openai` |
| `GEMINI_API_KEY` | - | Gemini API Key |
| `OPENAI_API_KEY` | - | OpenAI/兼容 API Key |
| `OPENAI_BASE_URL` | `https://api.openai.com/v1` | API 地址（DeepSeek: `https://api.deepseek.com`） |
| `OPENAI_MODEL` | `gpt-4o` | 模型名称 |
| `SYNC_INTERVAL_SECONDS` | `7` | 消息同步间隔 |
| `APP_PORT` | `8090` | 后端端口 |

## 注意事项

- 本项目仅用于**个人学习和研究**目的
- 微信数据解密使用 [wdecipher](https://github.com/gndlwch2w/wdecipher) 库
- 消息发送通过 PyAutoGUI 操控微信窗口，发送时微信窗口会短暂弹出
- 所有数据存储在本地 SQLite 数据库，不会上传到任何服务器
