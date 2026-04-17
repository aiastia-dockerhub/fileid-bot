"""
Bot 管理器
管理所有用户子Bot的生命周期：创建、启动、停止、删除
每个用户Bot拥有独立的 Application 实例和完整的 FileID 功能
"""
import asyncio
import logging
from typing import Dict, Optional, List

from telegram import Update
from telegram.ext import (
    Application, ApplicationBuilder, CommandHandler, MessageHandler,
    CallbackQueryHandler, filters
)

from database import get_all_active_user_bots

logger = logging.getLogger(__name__)


class BotManager:
    """
    用户Bot管理器

    负责：
    - 注册 / 注销用户Bot
    - 为每个用户Bot创建独立的 Application 和消息处理器
    - 管理用户Bot的运行状态
    - 动态添加/移除Bot
    """

    def __init__(self):
        self._apps: Dict[int, Application] = {}  # bot_db_id -> Application

    def _create_user_bot_app(self, token: str) -> Application:
        """为用户Bot创建 Application 实例，注册所有 FileID 处理器"""
        from handlers_commands import (
            start_command, create_collection_cmd, done_collection_cmd,
            cancel_collection_cmd, get_id_command, my_collections_cmd,
            delete_collection_cmd, stats_command, export_command
        )
        from handlers_messages import (
            handle_attachment, handle_text, handle_forward,
            handle_group_media, handle_forwarded_media
        )
        from handlers_callbacks import button_callback

        async def user_bot_post_init(application):
            """用户Bot初始化后注册命令"""
            commands = [
                ("start", "开始使用 / 查看帮助"),
                ("help", "查看帮助"),
                ("create", "创建集合 create 名称"),
                ("done", "完成集合"),
                ("cancel", "取消当前操作"),
                ("getid", "回复消息获取文件ID"),
                ("mycol", "查看我的集合"),
                ("delcol", "删除集合 delcol 代码"),
                ("stats", "统计信息"),
                ("export", "导出数据"),
            ]
            try:
                await application.bot.set_my_commands(commands)
                logger.info("用户Bot @%s 已注册 %d 个命令", application.bot.username, len(commands))
            except Exception as e:
                logger.warning("用户Bot注册命令失败: %s", e)

        application = ApplicationBuilder().token(token).post_init(user_bot_post_init).build()

        # 注册命令处理器
        application.add_handler(CommandHandler("start", start_command))
        application.add_handler(CommandHandler("help", start_command))
        application.add_handler(CommandHandler("create", create_collection_cmd))
        application.add_handler(CommandHandler("done", done_collection_cmd))
        application.add_handler(CommandHandler("cancel", cancel_collection_cmd))
        application.add_handler(CommandHandler("getid", get_id_command))
        application.add_handler(CommandHandler("mycol", my_collections_cmd))
        application.add_handler(CommandHandler("delcol", delete_collection_cmd))
        application.add_handler(CommandHandler("stats", stats_command))
        application.add_handler(CommandHandler("export", export_command))

        # 转发的图片消息
        application.add_handler(MessageHandler(
            filters.FORWARDED & filters.PHOTO,
            handle_forwarded_media
        ))

        # 转发的其他媒体消息
        application.add_handler(MessageHandler(
            filters.FORWARDED & (filters.VIDEO | filters.Document.ALL | filters.AUDIO | filters.VOICE),
            handle_forwarded_media
        ))

        # 转发的非媒体消息
        application.add_handler(MessageHandler(
            filters.FORWARDED & filters.TEXT & ~filters.COMMAND,
            handle_forward
        ))

        # 图片处理
        application.add_handler(MessageHandler(
            filters.PHOTO,
            handle_group_media
        ))

        # 其他媒体处理
        application.add_handler(MessageHandler(
            filters.VIDEO | filters.Document.ALL | filters.AUDIO | filters.VOICE,
            handle_group_media
        ))

        # 文本消息
        application.add_handler(MessageHandler(
            filters.TEXT & ~filters.COMMAND,
            handle_text
        ))

        # 回调按钮
        application.add_handler(CallbackQueryHandler(button_callback))

        # 错误处理
        async def user_bot_error_handler(update: object, context):
            logger.error("用户Bot @%s 错误: %s", context.bot.username, context.error, exc_info=True)
            if update and hasattr(update, 'effective_message') and update.effective_message:
                try:
                    await update.effective_message.reply_text("❌ 处理请求时发生内部错误，请稍后重试。")
                except Exception:
                    pass

        application.add_error_handler(user_bot_error_handler)

        return application

    async def start_bot(self, bot_record: dict) -> bool:
        """创建并启动一个用户Bot"""
        bot_db_id = bot_record['id']
        if bot_db_id in self._apps:
            logger.info("Bot @%s 已在运行，跳过", bot_record.get('bot_username', 'unknown'))
            return True

        try:
            app = self._create_user_bot_app(bot_record['bot_token'])
            app.bot_data['bot_record'] = bot_record

            await app.initialize()
            await app.start()
            await app.updater.start_polling(
                drop_pending_updates=True,
                allowed_updates=Update.ALL_TYPES
            )

            self._apps[bot_db_id] = app
            logger.info("用户Bot @%s 启动成功 (db_id=%s)", bot_record.get('bot_username'), bot_db_id)
            return True

        except Exception as e:
            logger.error("启动用户Bot失败: %s", e, exc_info=True)
            return False

    async def stop_bot(self, bot_db_id: int) -> bool:
        """停止一个用户Bot"""
        app = self._apps.pop(bot_db_id, None)
        if not app:
            return False

        try:
            await app.updater.stop()
            await app.stop()
            await app.shutdown()
            logger.info("用户Bot (db_id=%s) 已停止", bot_db_id)
            return True
        except Exception as e:
            logger.error("停止用户Bot失败: %s", e)
            return False

    async def load_all(self) -> int:
        """从数据库加载所有活跃的用户Bot"""
        bots = get_all_active_user_bots()
        logger.info("从数据库加载 %d 个用户Bot", len(bots))

        loaded = 0
        for bot in bots:
            success = await self.start_bot(bot)
            if success:
                loaded += 1
                logger.info("  ✅ @%s 已加载", bot.get('bot_username', 'unknown'))
            else:
                logger.error("  ❌ @%s 加载失败", bot.get('bot_username', 'unknown'))

        return loaded

    async def stop_all(self):
        """停止所有用户Bot"""
        for bot_db_id in list(self._apps.keys()):
            await self.stop_bot(bot_db_id)

    def get_all_apps(self) -> Dict[int, Application]:
        """获取所有用户Bot的Application实例"""
        return self._apps

    @property
    def active_count(self) -> int:
        """当前活跃Bot数量"""
        return len(self._apps)