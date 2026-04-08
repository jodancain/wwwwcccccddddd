# 设计书：个人聊天风格模型训练与 API 部署

## 1. 项目目标

基于用户在微信中的所有聊天记录，训练一个能模仿用户说话风格的 AI 模型，并作为独立 API 提供服务。启用后，该 API 可以代替用户回复微信消息，保持用户本人的语气、用词习惯和思维方式。

**参考项目**：[Chat-Style-Bot](https://github.com/Chain-Mao/Chat-Style-Bot) — 基于 LLaMA-Factory 的微信聊天风格模型微调方案。

---

## 2. 整体架构

```
┌─────────────────────────────────────────────────────────┐
│                    WeChatAI 现有系统                      │
│                                                         │
│  ┌──────────┐    ┌──────────┐    ┌──────────────────┐   │
│  │ WeChat   │───▶│ SQLite   │───▶│ 数据导出模块     │   │
│  │ 解密同步  │    │ 消息数据库│    │ (新增)            │   │
│  └──────────┘    └──────────┘    └────────┬─────────┘   │
│                                           │              │
│                                    JSON 训练数据          │
│                                           │              │
│  ┌──────────────────────────────────────────┐            │
│  │         训练管道 (新增)                    │            │
│  │                                          │            │
│  │  ┌─────────┐  ┌──────────┐  ┌─────────┐ │            │
│  │  │数据预处理│─▶│ LoRA     │─▶│模型合并  │ │            │
│  │  │多轮对话  │  │微调训练   │  │导出      │ │            │
│  │  └─────────┘  └──────────┘  └─────────┘ │            │
│  └──────────────────────────────────────────┘            │
│                         │                                │
│                   训练好的模型                             │
│                         │                                │
│  ┌──────────────────────────────────────────┐            │
│  │         推理 API (新增)                    │            │
│  │                                          │            │
│  │  ┌─────────┐  ┌──────────┐  ┌─────────┐ │            │
│  │  │vLLM /   │─▶│OpenAI    │─▶│WeChatAI │ │            │
│  │  │llama.cpp│  │兼容 API   │  │自动回复  │ │            │
│  │  └─────────┘  └──────────┘  └─────────┘ │            │
│  └──────────────────────────────────────────┘            │
└─────────────────────────────────────────────────────────┘
```

---

## 3. 模块设计

### 3.1 数据导出模块

**目的**：从现有 SQLite 数据库提取用户的所有聊天记录，转换为 LLaMA-Factory 训练格式。

**数据来源**：`backend/data/app.db` → `messages` 表

**过滤逻辑**：
```sql
-- 提取所有包含用户消息的对话
SELECT * FROM messages
WHERE talker IN (
    SELECT DISTINCT talker FROM messages WHERE is_sender = 1
)
AND type = 1  -- 仅文本消息
ORDER BY talker, create_time ASC
```

**输出格式（LLaMA-Factory sharegpt 格式）**：
```json
[
  {
    "conversations": [
      {"from": "human", "value": "今天天气怎么样？"},
      {"from": "gpt", "value": "深圳今天热死了 中午差点晒化"},
      {"from": "human", "value": "哈哈 注意防晒"},
      {"from": "gpt", "value": "已经黑了 来不及了[捂脸]"}
    ]
  }
]
```

**关键处理逻辑**：
- `is_sender = 1` 的消息 → `"from": "gpt"`（这是要模仿的目标）
- `is_sender = 0` 的消息 → `"from": "human"`（这是对方说的话）
- 按 `talker` 分组，按 `create_time` 排序
- **对话切分**：超过 30 分钟无消息则切分为新对话
- **群聊处理**：提取用户在群聊中的发言，对方消息合并为 human
- **过滤**：跳过 `[图片]`、`[语音]`、`[链接]` 等非文本内容
- **去重**：跳过系统消息（type 10000/10002）

**新增文件**：
- `backend/app/training/data_exporter.py` — 数据导出逻辑
- API: `POST /api/training/export-data` — 触发导出，返回 JSON 文件路径

---

### 3.2 训练管道

**基座模型选择**：Qwen2.5-7B-Instruct（推荐）
- 中文能力最强的开源 7B 模型
- 支持 LoRA/QLoRA 微调
- 推理时 VRAM 需求：~16GB（FP16）或 ~4GB（4bit 量化）

**微调方法**：LoRA
- 仅训练 ~1-5% 参数，显存需求 ~16-20GB
- 如果 GPU 显存不足，可降级为 QLoRA（~10GB）

**训练配置**：
```yaml
# config/train_my_style.yaml
model_name_or_path: Qwen/Qwen2.5-7B-Instruct
stage: sft
finetuning_type: lora
lora_rank: 16
lora_alpha: 32
lora_target: all
dataset: my_wechat_style
template: qwen
cutoff_len: 512           # 微信消息较短
per_device_train_batch_size: 4
gradient_accumulation_steps: 4
num_train_epochs: 3
learning_rate: 5e-5
lr_scheduler_type: cosine
warmup_ratio: 0.1
output_dir: ./output/my_style_lora
bf16: true
```

**训练流程**：
1. 导出训练数据 → `data/my_wechat_style.json`
2. 注册数据集 → `data/dataset_info.json`
3. 下载基座模型 → `models/Qwen2.5-7B-Instruct/`
4. 启动训练 → `llamafactory-cli train config/train_my_style.yaml`
5. 合并 LoRA → `llamafactory-cli export config/merge_lora.yaml`

**新增文件**：
- `backend/app/training/trainer.py` — 训练管理（启动、监控、停止）
- `backend/app/training/config.py` — 训练配置管理
- `config/train_my_style.yaml` — 默认训练配置

---

### 3.3 推理 API

**部署方式**：vLLM（高性能）或 llama.cpp（低资源）

**vLLM 部署**（推荐，需 GPU）：
```bash
python -m vllm.entrypoints.openai.api_server \
  --model ./output/my_style_merged \
  --port 8090 \
  --max-model-len 2048
```

**llama.cpp 部署**（CPU 可用）：
```bash
./llama-server \
  -m ./output/my_style.gguf \
  --port 8090 \
  -c 2048
```

两种方式都提供 **OpenAI 兼容 API**，格式：
```
POST http://localhost:8090/v1/chat/completions
{
  "model": "my-style",
  "messages": [
    {"role": "user", "content": "你好"}
  ]
}
```

**集成到 WeChatAI**：
- 在 `.env` 中配置：
  ```
  AI_PROVIDER=openai
  OPENAI_BASE_URL=http://localhost:8090/v1
  OPENAI_MODEL=my-style
  ```
- 现有的 `OpenAIProvider` 即可直接调用，无需改代码
- 或者新增 `MY_MODEL_` 开头的配置项，支持同时使用 Gemini + 本地模型

**新增文件**：
- `backend/app/training/inference.py` — 推理服务管理（启动、停止、状态）
- API: `POST /api/training/start-server` — 启动推理服务
- API: `GET /api/training/server-status` — 检查服务状态

---

### 3.4 自动回复集成

当个人模型 API 运行时，WeChatAI 可以启用"自动回复"模式：

**工作流程**：
1. 新消息到达（通过 7 秒同步检测到）
2. 判断是否启用了该对话的自动回复
3. 将最近 N 条对话历史 + 新消息发送给个人模型 API
4. 模型生成回复
5. 通过 UI 自动化（pyautogui）发送到微信

**前端 UI**：
- 对话列表右键 → "启用自动回复（个人模型）"
- AI 面板增加"自动回复"开关
- 显示模型回复预览，可选择：立即发送 / 编辑后发送 / 取消

---

## 4. 数据库变更

```sql
-- 训练任务跟踪
CREATE TABLE IF NOT EXISTS training_jobs (
    id TEXT PRIMARY KEY,
    status TEXT DEFAULT 'pending',  -- pending/exporting/training/merging/done/failed
    model_name TEXT DEFAULT '',
    dataset_path TEXT DEFAULT '',
    output_dir TEXT DEFAULT '',
    config_json TEXT DEFAULT '{}',
    total_steps INTEGER DEFAULT 0,
    current_step INTEGER DEFAULT 0,
    loss REAL DEFAULT 0,
    started_at DATETIME,
    finished_at DATETIME,
    error TEXT DEFAULT '',
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP
);

-- 自动回复配置
CREATE TABLE IF NOT EXISTS auto_reply_config (
    talker TEXT PRIMARY KEY,
    enabled INTEGER DEFAULT 0,
    model_url TEXT DEFAULT '',  -- 个人模型 API 地址
    delay_seconds INTEGER DEFAULT 3,  -- 延迟回复（更自然）
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP
);
```

---

## 5. API 端点

| 端点 | 方法 | 说明 |
|------|------|------|
| `/api/training/export-data` | POST | 导出聊天记录为训练数据 |
| `/api/training/export-status` | GET | 导出进度 |
| `/api/training/start` | POST | 启动模型训练 |
| `/api/training/status` | GET | 训练状态（进度、loss） |
| `/api/training/stop` | POST | 停止训练 |
| `/api/training/start-server` | POST | 启动推理服务 |
| `/api/training/server-status` | GET | 推理服务状态 |
| `/api/training/stop-server` | POST | 停止推理服务 |
| `/api/training/auto-reply/config` | GET/POST | 自动回复配置 |

---

## 6. 前端界面

### 训练管理页面（新增 Tab 或侧边栏入口）

```
┌─────────────────────────────────────────┐
│  🤖 个人模型训练                          │
├─────────────────────────────────────────┤
│                                         │
│  📊 数据统计                             │
│  ├ 总消息数: 242,543                     │
│  ├ 我的消息: 48,231                      │
│  ├ 对话数: 353                           │
│  └ 可用训练对话: 12,847                   │
│                                         │
│  ┌──────────────────────────────────┐   │
│  │ Step 1: 导出训练数据              │   │
│  │ [开始导出]  状态: 已完成 ✅        │   │
│  │ 导出对话: 12,847 条               │   │
│  └──────────────────────────────────┘   │
│                                         │
│  ┌──────────────────────────────────┐   │
│  │ Step 2: 选择基座模型              │   │
│  │ ○ Qwen2.5-7B (推荐)              │   │
│  │ ○ Llama3-8B                      │   │
│  │ ○ GLM-4-9B                       │   │
│  │ ○ 自定义路径: [________]          │   │
│  └──────────────────────────────────┘   │
│                                         │
│  ┌──────────────────────────────────┐   │
│  │ Step 3: 开始训练                  │   │
│  │ [开始训练]  [停止]                │   │
│  │ 进度: ████████░░ 80%             │   │
│  │ Step: 2400/3000  Loss: 0.42      │   │
│  │ 预计剩余: 15 分钟                  │   │
│  └──────────────────────────────────┘   │
│                                         │
│  ┌──────────────────────────────────┐   │
│  │ Step 4: 部署 API                  │   │
│  │ [启动推理服务]  [停止]            │   │
│  │ 状态: 运行中 🟢                   │   │
│  │ API: http://localhost:8090       │   │
│  │ [切换为默认 AI 引擎]              │   │
│  └──────────────────────────────────┘   │
│                                         │
└─────────────────────────────────────────┘
```

---

## 7. 硬件需求

| 阶段 | 最低配置 | 推荐配置 |
|------|---------|---------|
| **数据导出** | 无特殊要求 | - |
| **LoRA 训练** | RTX 3090 (24GB) | RTX 4090 (24GB) |
| **QLoRA 训练** | RTX 3080 (10GB) | RTX 3090+ |
| **推理 (FP16)** | 16GB VRAM | RTX 4090 |
| **推理 (4bit)** | 6GB VRAM | RTX 3060+ |
| **推理 (CPU)** | 16GB RAM | 32GB RAM |

**当前机器**：需确认 GPU 配置。如果没有 GPU，可以：
1. 使用云 GPU（AutoDL、恒源云等）训练
2. 本地用 llama.cpp 4bit 量化推理（纯 CPU）
3. 或将训练好的模型上传到云端 API 服务

---

## 8. 实施计划

### Phase 1：数据导出（1-2 天）
- [ ] 实现 `data_exporter.py`
- [ ] 实现导出 API
- [ ] 前端"导出数据"按钮
- [ ] 验证输出格式正确

### Phase 2：训练管道（2-3 天）
- [ ] 集成 LLaMA-Factory
- [ ] 训练配置管理
- [ ] 训练启动/监控/停止 API
- [ ] 前端训练管理界面

### Phase 3：推理部署（1-2 天）
- [ ] vLLM / llama.cpp 服务管理
- [ ] OpenAI 兼容 API 验证
- [ ] 集成到现有 AI Provider 体系
- [ ] 前端部署控制界面

### Phase 4：自动回复（1-2 天）
- [ ] 自动回复配置
- [ ] 消息检测 → 模型推理 → 发送
- [ ] 前端开关和预览
- [ ] 延迟和过滤规则

---

## 9. 文件结构（新增）

```
backend/
├── app/
│   ├── training/                    # 新增：训练模块
│   │   ├── __init__.py
│   │   ├── data_exporter.py         # 聊天记录导出为训练数据
│   │   ├── trainer.py               # 训练任务管理
│   │   ├── inference.py             # 推理服务管理
│   │   └── config.py                # 训练配置
│   └── api/
│       └── training.py              # 训练相关 API 路由
├── config/
│   ├── train_my_style.yaml          # 训练配置模板
│   └── merge_lora.yaml              # LoRA 合并配置
└── data/
    ├── training/                    # 导出的训练数据
    │   └── my_wechat_style.json
    └── models/                      # 基座模型和输出
        ├── base/                    # 下载的基座模型
        └── output/                  # 训练输出（LoRA + 合并后模型）
```

---

## 10. 风险与注意事项

1. **GPU 依赖**：训练需要 GPU，如果本地无 GPU 需要云方案
2. **训练时间**：7B 模型 LoRA 训练 ~1-3 小时（取决于数据量和 GPU）
3. **隐私安全**：训练数据包含私人聊天记录，模型文件需妥善保管
4. **回复质量**：模型不是 100% 准确模仿，可能产生不当回复
5. **自动回复风险**：建议初期设为"预览模式"，人工确认后再发送
6. **存储空间**：基座模型 ~15GB，训练输出 ~1-5GB，总计需 ~25GB 磁盘空间
