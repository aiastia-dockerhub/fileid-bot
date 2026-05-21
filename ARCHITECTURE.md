# fileid-bot 项目架构文档

> 本文档详细描述了 fileid-bot 项目的整体架构、模块划分、数据模型和部署方式。

---

## 一、项目定位

fileid-bot 是一个 **Telegram 多 Bot 托管平台**，核心功能：

- 一个 **Master Bot**（主控机器人）允许用户创建和管理多个 **子 Bot**
- 子 Bot 通过 Telegram File ID 实现文件的存储、管理和分发
- 支持 VIP 会员制度（Telegram Stars 支付）
- 支持 **单机部署** 和 **分布式 Master-Worker 部署**

---

## 二、系统架构总览

```
                          ┌─────────────────┐
                          │   Telegram API   │
                          └────────┬────────┘
                                   │
                    ┌──────────────┼──────────────┐
                    │              │              │
              ┌─────▼─────┐ ┌─────▼─────┐ ┌─────▼─────┐
              │ Master Bot │ │  子 Bot A  │ │  子 Bot B  │
              │  (主控Bot)  │ │ (用户创建) │ │ (用户创建) │
              └─────┬─────┘ └─────┬─────┘ └─────┬─────┘
                    │              │              │
                    └──────┬───────┴──────────────┘
                           │
              ┌────────────┼────────────────┐
              │            │                │
        ┌─────▼─────┐ ┌───▼────┐   ┌──────▼──────┐
        │  BotManager│ │  DB层   │   │ Redis/缓存  │
        │ (Bot生命周期)│ │(SQLite)│   │ (可选,降级) │
        └───────────┘ └────────┘   └─────────────┘
              │            │                │
              └────────────┼────────────────┘
                           │
              ┌────────────┼────────────────┐
              │            │                │
        ┌─────▼──────┐ ┌──▼───────┐ ┌─────▼─────┐
        │ SendQueue  │ │Scheduler │ │WebhookServer│
        │ (发送队列)  │ │(定时任务) │ │ (HTTP服务) │
        └────────────┘ └──────────┘ └───────────┘
```

---

## 三、运行模式

项目支持三种运行模式，通过环境变量 `BOT_MODE` 和 `ROLE` 控制：

| 模式 | BOT_MODE | ROLE | 说明 |
|------|----------|------|------|
| **standalone** | `polling` | — | 单机模式，所有功能在一个进程中运行 |
| **master** | `webhook` | `master` | 主控模式，管理子 Bot，分发任务到 Worker |
| **worker** | `webhook` | `worker` | 工作节点，接收 Master 指令运行子 Bot |

### 3.1 Standalone 模式

```
┌─────────────────────────────────┐
│         单个进程                 │
│  Master Bot + 所有子 Bot         │
│  SQLite + 内存缓存              │
│  Polling 轮询消息               │
└─────────────────────────────────┘
```

### 3.2 Master-Worker 分布式模式

```
┌──────────────────────┐     ┌──────────────────────┐
│      Master 节点      │     │     Worker 节点 1     │
│  - Master Bot         │────▶│  - 子 Bot A, B, C    │
│  - Webhook Server     │HTTP │  - Webhook Server    │
│  - Scheduler          │     │  - Worker API        │
│  - SendQueue          │     └──────────────────────┘
└──────────────────────┘
                                  ┌──────────────────────┐
                                  │     Worker 节点 2     │
                                  │  - 子 Bot D, E, F    │
                                  │  - Webhook Server    │
                                  │  - Worker API        │
                                  └──────────────────────┘
```

---

## 四、目录结构与模块划分

```
fileid-bot/
├── main.py                 # 应用入口，Handler 注册
├── run.py                  # 启动脚本
├── config.py               # 配置中心（所有环境变量）
│
├── bot_manager.py          # Bot 生命周期管理（核心）
├── redis_manager.py        # Redis 管理（带内存降级）
├── scheduler.py            # 定时任务调度
├── send_queue.py           # 异步发送队列
├── senders.py              # Telegram 消息发送封装
├── utils.py                # 工具函数
├── webhook_server.py       # Webhook HTTP 服务器
├── worker_server.py        # Worker 节点 HTTP API
│
├── db/                     # 数据库层
│   ├── __init__.py         # 导出 init_db, close_db
│   ├── core.py             # 数据库连接管理
│   ├── models.py           # 数据模型定义
│   ├── bots.py             # Bot CRUD
│   ├── collections.py      # 集合 CRUD
│   ├── files.py            # 文件 CRUD
│   ├── stats.py            # 统计 CRUD
│   ├── vip.py              # VIP 用户 CRUD
│   └── workers.py          # Worker 节点 CRUD
│
├── handlers/               # 子 Bot 消息处理器
│   ├── __init__.py
│   ├── commands.py         # 命令处理（/start, /help, /search...）
│   ├── callbacks.py        # 内联按钮回调处理
│   └── messages.py         # 消息处理（文件上传、File ID 识别）
│
├── handlers/master/        # Master Bot 消息处理器
│   ├── __init__.py
│   ├── _utils.py           # Master handler 工具函数
│   ├── start.py            # Master /start 入口
│   ├── newbot.py           # 创建新 Bot 流程
│   ├── manage.py           # Bot 管理操作
│   ├── admin.py            # 管理员命令
│   ├── blacklist.py        # 黑名单管理
│   ├── gifts.py            # VIP 赠送
│   └── stars.py            # Telegram Stars 支付
│
├── docker-compose.yml      # Docker 编排
├── Dockerfile              # Docker 构建
├── Dockerfile.protected    # 受保护的 Docker 构建
├── requirements.txt        # Python 依赖
├── .env.example            # 环境变量模板
└── .github/workflows/      # CI/CD 工作流
```

---

## 五、核心模块详解

### 5.1 入口与配置

#### `main.py` — 应用入口

**职责**：根据运行模式初始化不同的 Bot 实例并注册所有 Handler。

**核心流程**：

```
main()
 ├── standalone 模式 → 创建 Master Bot (Polling) + 自动加载子 Bot
 ├── master 模式    → 创建 Master Bot (Webhook) + 启动 Webhook 服务器
 └── worker 模式    → 启动 Worker HTTP API 服务器 (FastAPI)
```

**Handler 注册顺序**（standalone/master 模式）：

1. 全局错误处理器 — `error_handler`
2. Master 管理员过滤器 — `AdminFilter`
3. Master 命令：
   - `/start` → `master_start`
   - ConversationHandler: 创建新 Bot (`newbot_conv`)
   - `/mybots` → `my_bots`
   - `/admin` → 管理员面板
   - `/stats` → 统计信息
   - `/gift` → VIP 赠送
   - `/broadcast` → 广播
   - `/addadmin` → 添加管理员
   - `/help` → 帮助
4. Stars 支付相关：
   - `PreCheckoutQueryHandler` → `precheckout`
   - `MessageHandler` (successful_payment) → `successful_payment`
5. Master 管理回调 — `manage_callback`（内联按钮）
6. Master 消息处理 — `master_message`

#### `config.py` — 配置中心

所有配置通过环境变量读取，支持 `.env` 文件：

| 分类 | 变量 | 环境变量 | 默认值 | 说明 |
|------|------|---------|--------|------|
| **Bot** | `BOT_TOKEN` | `BOT_TOKEN` | `''` | 主 Bot Token |
| | `ADMIN_IDS` | `ADMIN_IDS` | `[]` | 管理员 Telegram ID |
| | `CODE_PREFIX` | `CODE_PREFIX` | `''` | 集合码前缀 |
| | `MAX_BOTS_PER_USER` | `MAX_BOTS_PER_USER` | `1` | 每用户最大 Bot 数 |
| **数据库** | `DB_TYPE` | `DB_TYPE` | `'sqlite'` | 数据库类型 |
| | `DATABASE_URL` | `DATABASE_URL` | `''` | 数据库连接 URL |
| | `DB_PATH` | `DB_PATH` | `'./data/fileid.db'` | SQLite 路径 |
| **发送控制** | `SEND_RETRY_COUNT` | `SEND_RETRY_COUNT` | `3` | 发送重试次数 |
| | `SEND_RETRY_DELAY` | `SEND_RETRY_DELAY` | `2.0` | 重试间隔(秒) |
| | `SEND_BATCH_DELAY` | `SEND_BATCH_DELAY` | `1.5` | 批量发送间隔 |
| | `SEND_INDIVIDUAL_DELAY` | `SEND_INDIVIDUAL_DELAY` | `1.0` | 单条发送间隔 |
| | `SEND_MAX_FILES_PER_REQUEST` | `SEND_MAX_FILES_PER_REQUEST` | `30` | 单次最大文件数 |
| | `SEND_MIN_INTERVAL` | `SEND_MIN_INTERVAL` | `1.5` | 最小发送间隔 |
| **超时** | `API_READ_TIMEOUT` | `API_READ_TIMEOUT` | `30.0` | API 读超时 |
| | `API_WRITE_TIMEOUT` | `API_WRITE_TIMEOUT` | `30.0` | API 写超时 |
| | `API_CONNECT_TIMEOUT` | `API_CONNECT_TIMEOUT` | `30.0` | API 连接超时 |
| **模式** | `BOT_MODE` | `BOT_MODE` | `'polling'` | 运行模式 |
| | `ROLE` | `ROLE` | `''` | 角色 (master/worker) |
| | `MASTER_URL` | `MASTER_URL` | `''` | Worker 连接 Master 的 URL |
| | `WORKER_NAME` | `WORKER_NAME` | `''` | Worker 名称 |
| | `WORKER_URL` | `WORKER_URL` | `''` | Worker 对外 URL |
| | `WORKER_PORT` | `WORKER_PORT` | `'8081'` | Worker 端口 |
| | `WORKER_SECRET` | `WORKER_SECRET` | `''` | Worker 通信密钥 |
| | `MAX_WORKER_BOTS` | `MAX_WORKER_BOTS` | `'10'` | Worker 最大 Bot 数 |
| **Webhook** | `WEBHOOK_URL` | `WEBHOOK_URL` | `''` | Webhook 基础 URL |
| | `WEBHOOK_PORT` | `WEBHOOK_PORT` | `'8080'` | Webhook 端口 |
| | `WEBHOOK_PATH` | `WEBHOOK_PATH` | `'/webhook'` | Webhook 路径 |
| | `WEBHOOK_CERT` | `WEBHOOK_CERT` | `''` | Webhook SSL 证书路径 |
| | `WEBHOOK_KEY` | `WEBHOOK_KEY` | `''` | Webhook SSL 私钥路径 |
| **Redis** | `REDIS_URL` | `REDIS_URL` | `''` | Redis 连接 URL |
| **VIP** | `VIP_MONTHS_STARS` | `VIP_MONTHS_STARS` | `'1'` | Stars 购买月数 |
| | `VIP_STARS_PRICE` | `VIP_STARS_PRICE` | `'100'` | Stars 价格 |
| **业务** | `MAX_COLLECTION_FILES` | — | `666` | 集合最大文件数 |
| | `AUTO_SEND_INTERVAL` | — | `5` | 自动发送间隔(秒) |
| | `GROUP_SEND_SIZE` | — | `10` | 群发批量大小 |
| | `CODE_LENGTH` | — | `32` | 集合码长度 |

---

### 5.2 Bot 管理

#### `bot_manager.py` — BotManager

**核心职责**：管理所有子 Bot 的完整生命周期。

```python
class BotManager:
    # 子 Bot 存储
    bots: dict[str, Application]     # bot_name → Application 实例
    bot_data: dict[str, dict]        # bot_name → 运行时数据

    # 公开方法
    async def start_bot(bot_doc, token?)    # 启动一个子 Bot
    async def stop_bot(bot_name)            # 停止一个子 Bot
    async def restart_bot(bot_name)         # 重启一个子 Bot
    async def load_all_bots()               # 从数据库加载所有子 Bot
    async def stop_all()                    # 停止所有子 Bot
    def get_bot(bot_name) → Application     # 获取 Bot 实例
    def is_running(bot_name) → bool         # 检查是否运行中
```

**启动子 Bot 的流程**：

```
start_bot(bot_doc)
 ├── 1. 创建 ApplicationBuilder
 │      └── 配置 Token、超时、连接池
 ├── 2. 注册所有子 Bot Handler
 │      ├── commands.py 的所有命令
 │      ├── callbacks.py 的回调处理
 │      └── messages.py 的消息处理
 ├── 3. 初始化 Application
 │      └── post_init: 设置 bot_data 默认值
 ├── 4. 根据模式启动
 │      ├── polling → application.start_polling()
 │      └── webhook → application.start_webhook()
 └── 5. 缓存到内存
```

---

### 5.3 数据库层

#### 数据库连接 (`db/core.py`)

- 使用异步数据库驱动
- 支持 SQLite（默认）和其他数据库
- `init_db()` 初始化连接，`close_db()` 关闭连接

#### 数据模型 (`db/models.py`)

```
┌─────────────────────────────────────────────────────────┐
│                        Bot                               │
│  name: str (PK)         Bot 名称/唯一标识                │
│  token: str             Telegram Bot Token               │
│  owner_id: int          创建者 Telegram ID               │
│  description: str       Bot 描述                         │
│  worker_id: str         所属 Worker 节点                  │
│  is_active: bool        是否启用                         │
│  is_public: bool        是否公开                         │
│  vip_required: bool     是否需要 VIP                     │
│  auto_approve: bool     是否自动通过                     │
│  welcome_text: str      欢迎语                           │
│  expire_at: datetime    过期时间                         │
│  created_at: datetime   创建时间                         │
│  updated_at: datetime   更新时间                         │
└─────────────────────────┬───────────────────────────────┘
                          │ 1:N
┌─────────────────────────▼───────────────────────────────┐
│                     Collection                            │
│  id: str (PK)           集合 ID                          │
│  bot_name: str          所属 Bot                         │
│  code: str              集合码                           │
│  name: str              集合名称                         │
│  description: str       集合描述                         │
│  cover_file_id: str     封面 File ID                     │
│  is_public: bool        是否公开                         │
│  sort_order: int        排序                             │
│  created_at: datetime   创建时间                         │
└─────────────────────────┬───────────────────────────────┘
                          │ 1:N
┌─────────────────────────▼───────────────────────────────┐
│                       File                                │
│  id: str (PK)           文件 ID                          │
│  collection_id: str     所属集合                          │
│  file_id: str           Telegram File ID                 │
│  file_type: str         文件类型                          │
│  file_name: str         文件名                           │
│  file_size: int         文件大小                          │
│  caption: str           标题/说明                         │
│  thumbnail_file_id: str  缩略图 File ID                  │
│  sort_order: int        排序                             │
│  created_at: datetime   创建时间                         │
└──────────────────────────────────────────────────────────┘

┌──────────────────────────────────────────────────────────┐
│                      VIPUser                              │
│  id: str (PK)           记录 ID                          │
│  bot_name: str          所属 Bot                         │
│  user_id: int           用户 Telegram ID                 │
│  expire_at: datetime    VIP 到期时间                      │
│  is_gifted: bool        是否赠送                         │
│  stars_payment_id: str  Stars 支付 ID                     │
│  created_at: datetime   创建时间                         │
└──────────────────────────────────────────────────────────┘

┌──────────────────────────────────────────────────────────┐
│                    BotStats                               │
│  id: str (PK)           记录 ID                          │
│  bot_name: str          所属 Bot                         │
│  date: str              日期 (YYYY-MM-DD)                │
│  files_sent: int        发送文件数                        │
│  users_served: int      服务用户数                        │
│  created_at: datetime   创建时间                         │
└──────────────────────────────────────────────────────────┘

┌──────────────────────────────────────────────────────────┐
│                      Worker                               │
│  id: str (PK)           Worker ID                        │
│  name: str              Worker 名称                      │
│  url: str               Worker URL                       │
│  secret: str            通信密钥                          │
│  status: str            状态                             │
│  bot_count: int         Bot 数量                         │
│  max_bots: int          最大 Bot 数                       │
│  last_heartbeat: datetime 最后心跳                       │
│  created_at: datetime   创建时间                         │
└──────────────────────────────────────────────────────────┘
```

#### CRUD 模块

| 模块 | 主要操作 |
|------|---------|
| `db/bots.py` | 创建/查询/更新/删除 Bot，按 owner 查询，按 worker 查询 |
| `db/collections.py` | 集合 CRUD，按 bot_name 查询，按 code 查询 |
| `db/files.py` | 文件 CRUD，按 collection_id 查询，批量创建 |
| `db/stats.py` | 统计记录，按日期/Bot 查询，增量更新 |
| `db/vip.py` | VIP 用户管理，检查 VIP 状态，续费/过期 |
| `db/workers.py` | Worker 节点注册，心跳更新，状态查询 |

---

### 5.4 Handler 层

#### 子 Bot Handler (`handlers/`)

**`commands.py`** — 命令处理：

| 命令 | 功能 | VIP |
|------|------|-----|
| `/start` | 开始使用，显示欢迎语 | - |
| `/help` | 帮助信息 | - |
| `/search <query>` | 搜索文件 | ✓ |
| `/collections` | 浏览所有集合 | - |
| `/code <code>` | 通过集合码直接获取 | - |

**`callbacks.py`** — 内联按钮回调（598行）：

| 回调前缀 | 功能 |
|----------|------|
| `p|` | 分页浏览集合 |
| `s|` | 分页发送文件 |
| `sn|` | 发送下一页 |
| `a|` | 自动发送（连续发送） |
| `ps|` | 发送当前页所有文件 |
| `stop_auto` | 停止自动发送 |
| `noop` | 空操作 |

**`messages.py`** — 消息处理：

- 管理员发送文件 → 自动提取 File ID 并保存
- 支持识别：Document、Photo、Video、Audio、Voice、VideoNote、Sticker、Animation
- 非管理员发送 → 提示使用命令

#### Master Bot Handler (`handlers/master/`)

**`start.py`** — `/start` 入口，显示主菜单

**`newbot.py`** — 创建新 Bot 的 ConversationHandler：

```
状态机流程:
选择创建 → 输入Bot名称 → 输入Bot用户名 → 确认Token → 创建成功
```

**`manage.py`** — Bot 管理面板：

- 启动/停止 Bot
- 删除 Bot
- 配置 Bot（公开/私有、VIP 要求等）
- 设置欢迎语

**`admin.py`** — 管理员专用命令

**`blacklist.py`** — 用户黑名单管理

**`gifts.py`** — VIP 赠送功能

**`stars.py`** — Telegram Stars 支付集成

---

### 5.5 基础设施层

#### `redis_manager.py` — Redis 管理

**设计特点**：Redis 不可用时自动降级为内存字典，保证核心功能可用。

```python
class RedisManager:
    # 缓存
    async def cache_get(key) / cache_set(key, value, ttl) / cache_delete(key)
    async def cache_get_json(key) / cache_set_json(key, value, ttl)

    # 限流
    async def rate_limit_check(key, limit, window) → bool  # 滑动窗口
    async def rate_limit_wait(key, limit, window)           # 阻塞等待

    # 计数器
    async def counter_incr(key) / counter_get(key)

    # 队列
    async def queue_push(key, value) / queue_pop(key) / queue_len(key)

    # Bot 状态
    async def set_bot_status(bot_name, status, ttl)
    async def get_bot_status(bot_name)
```

#### `send_queue.py` — 异步发送队列

```python
class SendQueue:
    # 核心方法
    async def submit(chat_id, send_func, priority?)     # 提交发送任务
    async def submit_batch(chat_id, send_funcs, ...)    # 批量提交
    async def start()                                    # 启动队列消费者
    async def stop()                                     # 停止队列
```

**特性**：
- 异步消费，限速发送
- 支持优先级
- 可选 Redis 持久化（崩溃恢复）
- 自动重试机制

#### `senders.py` — 消息发送封装

封装了所有 Telegram 文件类型的发送：

- `send_document`、`send_photo`、`send_video`
- `send_audio`、`send_voice`、`send_video_note`
- `send_sticker`、`send_animation`
- 根据 `file_type` 自动选择对应的发送方法

#### `scheduler.py` — 定时任务

| 任务 | 间隔 | 功能 |
|------|------|------|
| VIP 到期检查 | 定期 | 扫描过期 VIP 用户，更新状态 |
| Bot 过期清理 | 定期 | 清理过期 Bot，释放资源 |
| Worker 心跳监控 | 定期 | 检查 Worker 节点存活状态 |

#### `webhook_server.py` — Webhook 服务器

基于 aiohttp，为 Master Bot 和子 Bot 提供 Webhook 端点：

```
POST /webhook/{token}     → 接收 Telegram Webhook 更新
GET  /health              → 健康检查
```

#### `worker_server.py` — Worker API

基于 FastAPI，Master 通过此 API 管理远程 Worker：

```
POST /api/start_bot       → 启动子 Bot
POST /api/stop_bot        → 停止子 Bot
POST /api/restart_bot     → 重启子 Bot
GET  /api/status          → Worker 状态
GET  /api/bots            → 运行中的 Bot 列表
POST /api/heartbeat       → 心跳上报
```

---

## 六、部署架构

### 6.1 单机部署 (docker-compose)

```yaml
services:
  bot:
    image: aiastia/fileid-bot:latest
    env_file: .env
    volumes: ./data:/app/data
    ports: "8080:8080"
```

### 6.2 分布式部署

```yaml
services:
  master:
    image: aiastia/fileid-bot:latest
    environment:
      ROLE: master
      BOT_MODE: webhook
    ports: "8080:8080"

  worker-1:
    image: aiastia/fileid-bot:latest
    environment:
      ROLE: worker
      MASTER_URL: http://master:8080
      WORKER_PORT: "8081"
    ports: "8081:8081"

  worker-2:
    image: aiastia/fileid-bot:latest
    environment:
      ROLE: worker
      MASTER_URL: http://master:8080
      WORKER_PORT: "8082"
    ports: "8082:8082"
```

---

## 七、依赖清单

| 包 | 用途 |
|----|------|
| `python-telegram-bot[job-queue]` | Telegram Bot 框架 (v22+) |
| `aiohttp` | Webhook HTTP 服务器 |
| `fastapi` + `uvicorn` | Worker API 服务器 |
| `redis` | Redis 异步客户端 (可选) |
| `python-dotenv` | .env 文件加载 |
| `apscheduler` | 定时任务调度 |

---

## 八、关键设计模式

### 8.1 动态 Bot 生命周期管理

`BotManager` 可以在运行时动态创建、启动、停止和重启子 Bot，无需重启主进程。

### 8.2 可降级的基础设施

`RedisManager` 在 Redis 不可用时自动降级为内存字典，保证核心功能不受影响。

### 8.3 异步发送队列

`SendQueue` 通过异步消费 + 限速 + 重试机制，避免触发 Telegram API 限流。

### 8.4 Handler 动态注册

每个子 Bot 启动时，`bot_manager.py` 会动态注册所有 handler，支持完全独立的用户交互。

### 8.5 Master-Worker 通信

Master 通过 HTTP API 管理远程 Worker，Worker 定期上报心跳，实现分布式 Bot 托管。