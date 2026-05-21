# 多 Bot 框架模板开发指南

> 本文档指导你如何基于 fileid-bot 项目作为模板，快速开发新的多 Bot 应用。

---

## 一、框架能力概览

fileid-bot 本质上是一个 **Telegram 多 Bot 托管框架**，提供了以下开箱即用的能力：

| 能力 | 实现模块 | 复用程度 |
|------|---------|---------|
| 🔧 配置管理 | `config.py` + `.env` | ✅ 完全复用 |
| 🤖 动态 Bot 生命周期管理 | `bot_manager.py` | ✅ 完全复用 |
| 🗄️ 数据库层（异步 ODM） | `db/` 包 | 🔄 替换模型和 CRUD |
| 📨 异步发送队列（限速+重试） | `send_queue.py` + `senders.py` | ✅ 完全复用 |
| 🔴 Redis 缓存（可降级） | `redis_manager.py` | ✅ 完全复用 |
| ⏰ 定时任务调度 | `scheduler.py` | 🔄 调整任务内容 |
| 🌐 Webhook 服务器 | `webhook_server.py` | ✅ 完全复用 |
| 🏗️ Worker 节点 API | `worker_server.py` | ✅ 完全复用 |
| 🐳 Docker 部署编排 | `docker-compose.yml` + `Dockerfile` | ✅ 完全复用 |
| 🎛️ Master Bot 管理面板 | `handlers/master/` | 🔄 调整管理命令 |
| 💬 子 Bot Handler | `handlers/` | ❌ 完全重写 |

**图例**：✅ 完全复用 | 🔄 部分修改 | ❌ 完全重写

---

## 二、框架分层架构

```
┌───────────────────────────────────────────────────────────┐
│                    你的业务逻辑层                           │
│  (需要开发的部分)                                          │
│  ┌─────────────┐  ┌─────────────┐  ┌──────────────────┐  │
│  │ 子Bot Handler │  │ 数据模型     │  │ Master 管理命令   │  │
│  │ handlers/     │  │ db/models.py │  │ handlers/master/ │  │
│  └──────┬───────┘  └──────┬──────┘  └────────┬─────────┘  │
└─────────┼──────────────────┼──────────────────┼────────────┘
          │                  │                  │
┌─────────▼──────────────────▼──────────────────▼────────────┐
│                    框架基础设施层                             │
│  (直接复用，无需修改)                                        │
│  ┌────────────┐ ┌───────────┐ ┌─────────┐ ┌────────────┐  │
│  │ BotManager  │ │ SendQueue │ │  Redis  │ │ Scheduler  │  │
│  │ bot_manager │ │ send_queue│ │ redis   │ │ scheduler  │  │
│  └────────────┘ └───────────┘ └─────────┘ └────────────┘  │
│  ┌────────────┐ ┌───────────┐ ┌─────────┐ ┌────────────┐  │
│  │ WebhookServer│ │WorkerAPI │ │ Senders │ │  Config    │  │
│  │ webhook     │ │ worker    │ │ senders │ │  config    │  │
│  └────────────┘ └───────────┘ └─────────┘ └────────────┘  │
└────────────────────────────────────────────────────────────┘
```

---

## 三、开发新 Bot 的步骤

### 步骤 1：复制项目模板

```bash
# 复制整个项目作为新项目的基础
cp -r fileid-bot/ my-new-bot/
cd my-new-bot/

# 清理业务相关的 handler（保留框架基础设施）
rm -rf handlers/master/newbot.py  # 如果不需要"创建Bot"功能
```

### 步骤 2：定义数据模型

编辑 `db/models.py`，替换为你的业务数据模型。

**当前模型**（FileID Bot 业务）：
```python
# 当前模型关系
Bot → Collection → File     # 文件管理
Bot → VIPUser               # VIP 会员
Bot → BotStats              # 使用统计
Worker                      # 工作节点
```

**示例：假设开发一个"问卷 Bot"**：
```python
# models.py - 问卷 Bot 的数据模型

class Bot(Document):
    """子 Bot 配置 - 框架必需，保留"""
    name: str
    token: str
    owner_id: int
    description: str
    is_active: bool
    welcome_text: str
    created_at: datetime
    # ... 其他框架字段

class Survey(Document):
    """问卷 - 替代 Collection"""
    bot_name: str           # 所属 Bot
    title: str              # 问卷标题
    description: str        # 问卷描述
    is_active: bool         # 是否激活
    questions: list         # 问题列表
    created_at: datetime

class Response(Document):
    """答卷 - 替代 File"""
    survey_id: str          # 所属问卷
    user_id: int            # 答题用户
    answers: list           # 答案列表
    submitted_at: datetime

class SurveyStats(Document):
    """统计 - 替代 BotStats"""
    bot_name: str
    date: str
    responses_count: int
    created_at: datetime
```

### 步骤 3：实现 CRUD 操作

为每个新模型创建对应的 CRUD 模块：

```
db/
├── core.py          # ✅ 不动 - 数据库连接
├── models.py        # 🔄 修改 - 你的数据模型
├── bots.py          # ✅ 不动/微调 - Bot CRUD
├── surveys.py       # 🆕 新增 - 问卷 CRUD
├── responses.py     # 🆕 新增 - 答卷 CRUD
└── stats.py         # 🔄 修改 - 统计 CRUD
```

**CRUD 模块模板**：
```python
# db/surveys.py
from db.models import Survey
from datetime import datetime

async def create_survey(bot_name: str, title: str, description: str, **kwargs) -> Survey:
    """创建问卷"""
    survey = Survey(
        bot_name=bot_name,
        title=title,
        description=description,
        created_at=datetime.utcnow(),
        **kwargs
    )
    await survey.insert()
    return survey

async def get_survey(survey_id: str) -> Survey | None:
    return await Survey.get(survey_id)

async def get_bot_surveys(bot_name: str) -> list[Survey]:
    return await Survey.find(Survey.bot_name == bot_name).to_list()

async def delete_survey(survey_id: str) -> bool:
    survey = await Survey.get(survey_id)
    if survey:
        await survey.delete()
        return True
    return False
```

### 步骤 4：实现子 Bot Handler

这是你的核心业务逻辑，完全重写 `handlers/` 目录：

```
handlers/
├── __init__.py       # ✅ 不动
├── commands.py       # ❌ 重写 - 你的命令
├── callbacks.py      # ❌ 重写 - 你的回调
└── messages.py       # ❌ 重写 - 你的消息处理
```

**命令处理模板** (`handlers/commands.py`)：

```python
from telegram import Update
from telegram.ext import ContextTypes
from db import surveys

async def start_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """子Bot的 /start 命令"""
    bot_name = context.bot.username
    text = (
        f"👋 欢迎！我是 {context.bot.first_name}\n\n"
        "📋 可用命令：\n"
        "/surveys - 查看所有问卷\n"
        "/help - 帮助信息"
    )
    await update.message.reply_text(text)

async def surveys_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """列出所有问卷"""
    bot_name = context.bot.username
    survey_list = await surveys.get_bot_surveys(bot_name)

    if not survey_list:
        await update.message.reply_text("暂无问卷。")
        return

    text = "📋 可用问卷：\n\n"
    for s in survey_list:
        text += f"• {s.title}\n"
    await update.message.reply_text(text)

# 所有子Bot命令列表（供 bot_manager.py 注册用）
SUB_BOT_HANDLERS = [
    # (handler_type, args)
    ("command", "start", start_cmd),
    ("command", "surveys", surveys_cmd),
    # ("callback", button_callback),
    # ("message", message_handler),
]
```

### 步骤 5：修改 Bot Manager 注册逻辑

编辑 `bot_manager.py` 中的 handler 注册函数，替换为你的新 handler：

```python
# bot_manager.py - 找到注册 handler 的函数，替换导入和注册逻辑

# 原来：
# from handlers.commands import ...
# from handlers.callbacks import ...
# from handlers.messages import ...

# 改为：
from handlers.commands import SUB_BOT_HANDLERS

# 在注册函数中：
for handler_type, *args in SUB_BOT_HANDLERS:
    if handler_type == "command":
        app.add_handler(CommandHandler(args[0], args[1]))
    elif handler_type == "callback":
        app.add_handler(CallbackQueryHandler(args[0]))
    elif handler_type == "message":
        app.add_handler(MessageHandler(args[0], args[1]))
```

### 步骤 6：调整 Master Bot Handler

根据你的需求调整 `handlers/master/`：

| 文件 | 建议 |
|------|------|
| `start.py` | 🔄 修改 Master Bot 的欢迎语和菜单 |
| `newbot.py` | ✅ 通常保留（创建新Bot的核心流程） |
| `manage.py` | 🔄 调整 Bot 配置选项 |
| `admin.py` | 🔄 调整管理员命令 |
| `blacklist.py` | ✅ 通常保留 |
| `gifts.py` | 🔄 根据是否需要付费功能 |
| `stars.py` | 🔄 根据是否需要 Stars 支付 |

### 步骤 7：调整配置

编辑 `config.py` 和 `.env.example`：

```bash
# .env.example - 添加你的业务配置
BOT_TOKEN=你的主Bot_Token
ADMIN_IDS=123456789

# 数据库
DB_TYPE=sqlite
DB_PATH=./data/mybot.db

# 新增业务配置
MAX_SURVEYS_PER_BOT=50
RESPONSE_EXPIRE_DAYS=90
```

在 `config.py` 中添加对应的读取逻辑。

### 步骤 8：调整入口文件

编辑 `main.py`，更新 handler 注册部分：

```python
# main.py - 主要修改 Master Bot 的 handler 注册
# 1. 导入你的 master handler
# 2. 替换 handler 注册列表
# 3. 其他框架逻辑保持不变
```

---

## 四、文件修改清单

基于此框架开发新 Bot 时，各文件的修改程度：

### ✅ 无需修改（框架核心）

| 文件 | 说明 |
|------|------|
| `send_queue.py` | 异步发送队列，通用组件 |
| `senders.py` | 消息发送封装，通用组件 |
| `redis_manager.py` | Redis 管理，通用组件 |
| `webhook_server.py` | Webhook 服务，通用组件 |
| `worker_server.py` | Worker API，通用组件 |
| `utils.py` | 工具函数 |
| `run.py` | 启动脚本 |
| `Dockerfile` | Docker 构建 |
| `docker-compose.yml` | 编排配置（可能微调端口/环境变量） |

### 🔄 部分修改

| 文件 | 修改内容 |
|------|---------|
| `config.py` | 添加新的业务配置项 |
| `db/core.py` | 通常不动，除非换数据库 |
| `db/bots.py` | 可能添加新的 Bot 字段 |
| `db/stats.py` | 根据业务调整统计维度 |
| `bot_manager.py` | 修改 handler 注册逻辑 |
| `main.py` | 修改 Master handler 注册 |
| `scheduler.py` | 调整定时任务内容 |
| `.env.example` | 添加新的环境变量 |

### ❌ 完全重写

| 文件 | 说明 |
|------|------|
| `db/models.py` | 你的业务数据模型 |
| `handlers/commands.py` | 子 Bot 命令处理 |
| `handlers/callbacks.py` | 子 Bot 回调处理 |
| `handlers/messages.py` | 子 Bot 消息处理 |

### 🆕 可能新增

| 文件 | 说明 |
|------|------|
| `db/<your_module>.py` | 新的 CRUD 模块 |
| `handlers/<your_handler>.py` | 新的 handler |
| `services/<your_service>.py` | 业务逻辑服务层（推荐） |

---

## 五、推荐的项目结构优化

对于较复杂的 Bot 项目，建议增加一个 **服务层** 来解耦业务逻辑和 Handler：

```
my-new-bot/
├── main.py
├── config.py
├── bot_manager.py
│
├── models/                  # 🆕 数据模型独立目录
│   ├── __init__.py
│   ├── bot.py
│   ├── survey.py
│   └── response.py
│
├── db/                      # 数据库 CRUD
│   ├── __init__.py
│   ├── core.py
│   ├── bots.py
│   ├── surveys.py
│   └── responses.py
│
├── services/                # 🆕 业务逻辑服务层
│   ├── __init__.py
│   ├── survey_service.py    # 问卷业务逻辑
│   └── analytics_service.py # 分析业务逻辑
│
├── handlers/                # Handler 层（尽量薄）
│   ├── __init__.py
│   ├── commands.py          # 只做路由和格式化
│   ├── callbacks.py
│   └── messages.py
│
├── handlers/master/         # Master Bot Handler
│   ├── ...
│
├── infrastructure/          # 🆕 基础设施层（更清晰）
│   ├── __init__.py
│   ├── send_queue.py
│   ├── senders.py
│   ├── redis_manager.py
│   ├── scheduler.py
│   └── webhook_server.py
│
└── tests/                   # 🆕 测试目录
    ├── test_models.py
    ├── test_services.py
    └── test_handlers.py
```

---

## 六、常见新 Bot 类型示例

### 6.1 内容订阅 Bot

```
Bot → Channel (频道) → Post (帖子)
Bot → Subscription (订阅) → User (用户)
Bot → Delivery (投递记录)
```

### 6.2 客服 Bot

```
Bot → FAQ (常见问题)
Bot → Ticket (工单) → Message (消息)
Bot → Agent (客服) → Conversation (会话)
```

### 6.3 投票/问卷 Bot

```
Bot → Survey (问卷) → Question (问题)
Bot → Response (回答) → Answer (答案)
Bot → SurveyStats (统计)
```

### 6.4 文件转换 Bot

```
Bot → ConversionJob (转换任务)
Bot → Template (模板)
Bot → UserQuota (用户配额)
```

### 6.5 群管理 Bot

```
Bot → Group (群组) → Rule (规则)
Bot → Member (成员) → Warning (警告)
Bot → AutoResponse (自动回复)
```

---

## 七、开发检查清单

开发新 Bot 时，按此清单逐项确认：

### 基础配置
- [ ] 复制项目并重命名
- [ ] 修改 `.env.example` 为你的配置
- [ ] 创建 `.env` 文件并填入真实配置
- [ ] 修改 `config.py` 中新增的配置项
- [ ] 更新 `requirements.txt`（如需新依赖）

### 数据层
- [ ] 设计数据模型 (`db/models.py`)
- [ ] 实现各模型的 CRUD 模块 (`db/*.py`)
- [ ] 在 `db/__init__.py` 中注册新模型
- [ ] 在 `db/core.py` 中添加新模型的初始化

### Handler 层
- [ ] 实现子 Bot 命令 (`handlers/commands.py`)
- [ ] 实现子 Bot 回调 (`handlers/callbacks.py`)
- [ ] 实现子 Bot 消息处理 (`handlers/messages.py`)
- [ ] 修改 `bot_manager.py` 中的 handler 注册

### Master Bot
- [ ] 修改 Master 欢迎语 (`handlers/master/start.py`)
- [ ] 调整创建 Bot 流程 (`handlers/master/newbot.py`)
- [ ] 调整 Bot 管理选项 (`handlers/master/manage.py`)
- [ ] 更新 `main.py` 中的 Master handler 注册

### 定时任务
- [ ] 调整 `scheduler.py` 中的定时任务
- [ ] 添加新的定时任务（如需要）

### 部署
- [ ] 更新 `docker-compose.yml` 配置
- [ ] 更新 `Dockerfile`（如需新依赖）
- [ ] 测试本地运行
- [ ] 测试 Docker 运行

---

## 八、注意事项

### 8.1 Handler 注册的关键点

`bot_manager.py` 中注册 handler 时，注意：

- 所有子 Bot 共享**同一套 handler 函数**
- 通过 `context.bot.username` 或 `context.bot_data` 区分不同子 Bot
- 每个 Bot 的 `user_data` 和 `bot_data` 是独立的

### 8.2 异步编程注意

- 所有 handler 和数据库操作都是 **async** 的
- 使用 `asyncio.gather()` 并发执行多个独立任务
- 避免在 handler 中使用阻塞操作（如 `time.sleep`，用 `asyncio.sleep` 代替）

### 8.3 错误处理

- 在 `main.py` 中注册全局错误处理器 `error_handler`
- handler 内部应有 try/except 防止单个错误导致 Bot 崩溃
- 使用 logging 记录错误详情

### 8.4 限流和性能

- 使用 `send_queue.py` 管理发送频率，避免 Telegram API 限流
- 使用 `redis_manager.py` 的 `rate_limit_check` 进行用户级限流
- 数据库查询注意添加索引和分页

### 8.5 安全

- Bot Token 不要硬编码，使用环境变量
- Worker 通信使用 `WORKER_SECRET` 验证
- 管理员操作使用 `ADMIN_IDS` 过滤