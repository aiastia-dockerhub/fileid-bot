# 🤖 FileID Bot 托管平台

基于 [python-telegram-bot](https://github.com/python-telegram-bot/python-telegram-bot) 的多Bot托管平台，用户可以通过主Bot创建和管理自己的 FileID Bot，无需独立服务器。

## ✨ 特性

- 🏠 **多Bot托管** - 一个服务器运行多个Bot，用户自助管理
- 🔄 **文件ID互转** - 发送文件获取代码，发送代码获取文件
- 📦 **集合功能** - 批量打包文件，一次发送多个
- 🔐 **加密存储** - 文件ID加密存储，安全可靠
- 🐳 **Docker部署** - 一键部署，开箱即用
- 📊 **管理面板** - 平台统计数据一目了然

## 🏗️ 架构

```
主Bot (MasterBot)          ← 管理Bot，用户注册/管理
  ├── 用户Bot A (UserBot)   ← 独立Bot，完整FileID功能
  ├── 用户Bot B (UserBot)   ← 独立Bot，完整FileID功能
  └── 用户Bot C (UserBot)   ← 独立Bot，完整FileID功能
```

所有Bot共享同一进程和数据库，资源占用低。

## 🚀 快速开始

### 1. 创建主Bot

在 [@BotFather](https://t.me/BotFather) 创建一个Bot，这个Bot将作为管理Bot（平台入口）。

### 2. 配置环境

```bash
cp .env.example .env
```

编辑 `.env` 文件：

```env
# 主Bot Token
BOT_TOKEN=123456:ABC-DEF...

# 管理员ID（从 @userinfobot 获取）
ADMIN_IDS=123456789

# 加密密钥（首次运行自动生成）
ENCRYPTION_KEY=

# 每用户最大Bot数
MAX_BOTS_PER_USER=5
```

### 3. Docker 部署（推荐）

```bash
docker compose up -d
```

### 4. 手动部署

```bash
pip install -r requirements.txt
python main.py
```

## 📱 使用方法

### 平台用户流程

1. **创建Bot** - 在 [@BotFather](https://t.me/BotFather) 创建自己的Bot
2. **添加到平台** - 向主Bot发送 `/addbot <Token>`
3. **使用Bot** - 直接向自己的Bot发送文件即可

### 主Bot命令

| 命令 | 说明 |
|------|------|
| `/start` | 查看平台介绍和使用说明 |
| `/addbot <Token>` | 添加你的Bot到平台 |
| `/mybots` | 查看你的Bot列表和状态 |
| `/delbot @username` | 删除指定Bot |
| `/botstatus` | 查看Bot运行状态 |
| `/platform` | 平台统计（管理员） |

### 用户Bot命令

| 命令 | 说明 |
|------|------|
| `/start` | 查看帮助 |
| `/create 名称` | 创建集合 |
| `/done` | 完成集合并生成代码 |
| `/cancel` | 取消当前操作 |
| `/getid` | 回复消息获取文件ID |
| `/mycol` | 查看我的集合 |
| `/delcol 代码` | 删除集合 |
| `/stats` | 统计信息 |
| `/export` | 导出数据 |

## 📁 项目结构

```
├── main.py              # 主入口：启动主Bot + 加载用户Bot
├── config.py            # 配置管理
├── database.py          # 数据库操作（含 user_bots 表）
├── bot_manager.py       # Bot管理器：动态创建/停止用户Bot
├── handlers_master.py   # 主Bot命令处理器
├── handlers_commands.py # 用户Bot命令处理器
├── handlers_messages.py # 用户Bot消息处理器
├── handlers_callbacks.py# 用户Bot回调处理器
├── senders.py           # 文件发送逻辑
├── utils.py             # 工具函数
├── crypto.py            # 加密模块
├── build.py             # 构建脚本
├── Dockerfile           # Docker镜像
├── docker-compose.yml   # Docker Compose配置
└── requirements.txt     # Python依赖
```

## 🔧 配置说明

| 环境变量 | 必填 | 默认值 | 说明 |
|----------|------|--------|------|
| `BOT_TOKEN` | ✅ | - | 主Bot Token |
| `ADMIN_IDS` | ❌ | - | 管理员Telegram ID |
| `ENCRYPTION_KEY` | ❌ | 自动生成 | 文件ID加密密钥 |
| `MAX_BOTS_PER_USER` | ❌ | 5 | 每用户最大Bot数 |
| `CODE_PREFIX` | ❌ | Bot用户名 | 文件代码前缀 |

##  License

MIT