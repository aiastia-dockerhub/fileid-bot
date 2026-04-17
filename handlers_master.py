"""主Bot管理命令处理器 - 处理用户Bot的添加、查看、删除等操作"""
import logging

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes

from database import (
    add_user_bot, get_user_bots_by_owner, get_user_bot_by_id,
    get_user_bot_by_token, get_user_bot_by_telegram_id,
    delete_user_bot as db_delete_user_bot,
    update_user_bot_status, get_platform_stats
)

logger = logging.getLogger(__name__)


def get_bot_manager():
    """获取全局 BotManager 实例"""
    import __main__
    return getattr(__main__, 'bot_manager', None)


async def master_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """主Bot /start 命令"""
    text = """🤖 *FileID Bot 托管平台*

我可以帮你创建属于自己的 FileID Bot！
每个 Bot 都有完整的文件ID互转功能。

📌 *管理命令：*
• `/addbot` — 添加你的 Bot（提供 Token）
• `/mybots` — 查看我的 Bot 列表
• `/delbot` — 删除 Bot
• `/botstatus` — 查看 Bot 运行状态

💡 *使用方法：*
1. 先在 @BotFather 创建一个 Bot
2. 使用 `/addbot` 添加到本平台
3. 直接向你的 Bot 发送文件即可使用

所有 Bot 共享服务器资源，你无需部署！"""
    await update.message.reply_text(text, parse_mode="Markdown")


async def add_bot_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/addbot 添加用户Bot"""
    user_id = update.effective_user.id

    if not context.args:
        await update.message.reply_text(
            "🔑 *添加 Bot*\n\n"
            "请使用以下命令格式：\n"
            "`/addbot <Bot Token>`\n\n"
            "例如：`/addbot 123456:ABCdefGHIjklMNOpqrS`",
            parse_mode="Markdown"
        )
        return

    token = context.args[0].strip()

    # 基本格式校验
    if ":" not in token:
        await update.message.reply_text("❌ Token 格式不正确，请检查后重试。")
        return

    # 检查是否已添加
    existing = get_user_bot_by_token(token)
    if existing:
        await update.message.reply_text(
            f"⚠️ Bot @{existing['bot_username']} 已经添加过了。"
        )
        return

    status_msg = await update.message.reply_text("⏳ 正在校验 Token...")

    # 校验Token
    from telegram import Bot
    try:
        test_bot = Bot(token=token)
        bot_info = await test_bot.get_me()
    except Exception as e:
        await status_msg.edit_text(f"❌ Token 校验失败：{str(e)[:100]}\n\n请检查Token是否正确。")
        return
    finally:
        if not test_bot.session.closed:
            await test_bot.session.close()

    # 检查是否是同一个 Bot 被不同 token 添加
    existing_by_id = get_user_bot_by_telegram_id(bot_info.id)
    if existing_by_id:
        await status_msg.edit_text(
            f"⚠️ Bot @{bot_info.username} 已被添加（可能是不同 Token）。"
        )
        return

    # 检查用户Bot数量限制
    from config import MAX_BOTS_PER_USER
    user_bots = get_user_bots_by_owner(user_id)
    if len(user_bots) >= MAX_BOTS_PER_USER:
        await status_msg.edit_text(
            f"⚠️ 每个用户最多添加 {MAX_BOTS_PER_USER} 个 Bot。"
        )
        return

    # 保存到数据库
    record_id = add_user_bot(
        owner_id=user_id,
        bot_token=token,
        bot_id=bot_info.id,
        bot_username=bot_info.username or "",
        bot_firstname=bot_info.first_name,
    )

    if not record_id:
        await status_msg.edit_text("❌ 添加失败，请重试。")
        return

    # 注册到 BotManager 并启动
    mgr = get_bot_manager()
    if mgr:
        bot_record = get_user_bot_by_id(record_id)
        success = await mgr.start_bot(bot_record)
        if success:
            await status_msg.edit_text(
                f"✅ *Bot 添加成功！*\n\n"
                f"🤖 名称：{bot_info.first_name}\n"
                f"📌 用户名：@{bot_info.username}\n"
                f"🆔 Bot ID：`{bot_info.id}`\n\n"
                f"现在直接向 @{bot_info.username} 发送文件即可使用！\n"
                f"发送代码即可获取文件。",
                parse_mode="Markdown"
            )
            logger.info("用户 %s 添加了 Bot @%s", user_id, bot_info.username)
        else:
            await status_msg.edit_text(
                f"⚠️ Bot 已保存但启动失败，请联系管理员。\n\n"
                f"🤖 @{bot_info.username}"
            )
    else:
        await status_msg.edit_text("❌ BotManager 未初始化，请联系管理员。")


async def my_bots_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/mybots 查看用户的Bot列表"""
    user_id = update.effective_user.id
    bots = get_user_bots_by_owner(user_id)

    if not bots:
        await update.message.reply_text(
            "📭 你还没有添加任何 Bot。\n\n使用 `/addbot <Token>` 开始添加！",
            parse_mode="Markdown"
        )
        return

    mgr = get_bot_manager()
    text = "📋 *我的 Bot 列表：*\n\n"
    for i, bot in enumerate(bots, 1):
        is_running = mgr and bot['id'] in mgr.get_all_apps()
        status_emoji = "🟢" if is_running else "🔴"
        text += (
            f"{i}. {status_emoji} *{bot['bot_firstname']}*\n"
            f"   @{bot['bot_username']} | ID: `{bot['bot_id']}`\n\n"
        )

    text += f"共 {len(bots)} 个 Bot"
    await update.message.reply_text(text, parse_mode="Markdown")


async def delete_bot_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/delbot 删除用户Bot"""
    user_id = update.effective_user.id

    if not context.args:
        await update.message.reply_text(
            "请提供 Bot 的用户名或编号。\n"
            "用法：`/delbot @BotUsername` 或 `/delbot 编号`\n\n"
            "使用 `/mybots` 查看你的 Bot 列表。",
            parse_mode="Markdown"
        )
        return

    bots = get_user_bots_by_owner(user_id)
    if not bots:
        await update.message.reply_text("📭 你没有可删除的 Bot。")
        return

    arg = context.args[0].strip()
    target_bot = None

    # 按编号删除
    try:
        idx = int(arg) - 1
        if 0 <= idx < len(bots):
            target_bot = bots[idx]
    except ValueError:
        pass

    # 按用户名删除
    if not target_bot:
        username = arg.lstrip('@')
        for bot in bots:
            if bot['bot_username'].lower() == username.lower():
                target_bot = bot
                break

    if not target_bot:
        await update.message.reply_text("❌ 未找到指定的 Bot。使用 `/mybots` 查看列表。", parse_mode="Markdown")
        return

    # 先停止Bot
    mgr = get_bot_manager()
    if mgr:
        await mgr.stop_bot(target_bot['id'])

    # 从数据库软删除
    db_delete_user_bot(target_bot['id'])

    await update.message.reply_text(
        f"✅ Bot @{target_bot['bot_username']} 已删除。",
    )
    logger.info("用户 %s 删除了 Bot @%s", user_id, target_bot['bot_username'])


async def bot_status_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/botstatus 查看Bot运行状态"""
    user_id = update.effective_user.id
    bots = get_user_bots_by_owner(user_id)

    if not bots:
        await update.message.reply_text(
            "📭 你没有 Bot。使用 `/addbot <Token>` 添加！",
            parse_mode="Markdown"
        )
        return

    mgr = get_bot_manager()
    text = "🚀 *Bot 运行状态：*\n\n"
    for bot in bots:
        is_running = mgr and bot['id'] in mgr.get_all_apps()
        status = "🟢 运行中" if is_running else "🔴 已停止"
        text += f"- @{bot['bot_username']}: {status}\n"

    await update.message.reply_text(text, parse_mode="Markdown")


async def platform_stats_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/platform 管理员查看平台统计"""
    from config import ADMIN_IDS
    user_id = update.effective_user.id
    if user_id not in ADMIN_IDS:
        await update.message.reply_text("⛔ 此命令仅限管理员使用。")
        return

    stats = get_platform_stats()
    mgr = get_bot_manager()
    running = mgr.active_count if mgr else 0

    text = (
        f"📊 *平台统计*\n\n"
        f"🤖 活跃 Bot 数: {stats['bot_count']} (运行中: {running})\n"
        f"👥 Bot 所有者数: {stats['owner_count']}\n"
        f"📁 总文件数: {stats['file_count']}\n"
        f"📦 总集合数: {stats['col_count']}"
    )
    await update.message.reply_text(text, parse_mode="Markdown")