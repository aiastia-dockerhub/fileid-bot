"""主Bot管理命令处理器 - 处理用户Bot的添加、查看、删除等操作"""
import html
import logging
import urllib.parse

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    ContextTypes, ConversationHandler, MessageHandler, filters
)

from database import (
    add_user_bot, get_user_bots_by_owner, get_user_bot_by_id,
    get_user_bot_by_token, get_user_bot_by_telegram_id,
    delete_user_bot as db_delete_user_bot,
    update_user_bot_status, get_platform_stats
)

logger = logging.getLogger(__name__)

# Conversation states for /newbot
INPUT_BOT_USERNAME, INPUT_BOT_NAME, INPUT_BOT_TOKEN = range(3)


def get_bot_manager():
    """获取全局 BotManager 实例"""
    import __main__
    return getattr(__main__, 'bot_manager', None)


def escape(text: str) -> str:
    """HTML 转义"""
    return html.escape(str(text), quote=False)


async def master_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """主Bot /start 命令"""
    text = (
        "🤖 <b>FileID Bot 托管平台</b>\n\n"
        "我可以帮你创建属于自己的 FileID Bot！\n"
        "每个 Bot 都有完整的文件ID互转功能。\n\n"
        "📌 <b>管理命令：</b>\n"
        "• /newbot — 一键创建你的 Bot\n"
        "• /addbot — 添加你的 Bot（提供 Token）\n"
        "• /mybots — 查看我的 Bot 列表\n"
        "• /delbot — 删除 Bot\n"
        "• /botstatus — 查看 Bot 运行状态\n\n"
        "💡 <b>使用方法：</b>\n"
        "1. 使用 /newbot 一键创建 Bot\n"
        "2. 或直接 /addbot 添加已有 Bot\n\n"
        "所有 Bot 共享服务器资源，你无需部署！"
    )
    await update.message.reply_text(text, parse_mode="HTML")


# ==================== /newbot 交互式创建 ====================

async def new_bot_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """/newbot 开始交互式创建 Bot"""
    user_id = update.effective_user.id

    from config import MAX_BOTS_PER_USER
    user_bots = get_user_bots_by_owner(user_id)
    if len(user_bots) >= MAX_BOTS_PER_USER:
        existing = user_bots[0]
        await update.message.reply_text(
            f"⚠️ 你已有一个 Bot：@{escape(existing['bot_username'])}\n\n"
            f"请先使用 /delbot 删除后再创建新 Bot。"
        )
        return ConversationHandler.END

    await update.message.reply_text(
        "🤖 <b>创建新 Bot</b>\n\n"
        "请输入 Bot 的 <b>用户名</b>（必须以 <code>bot</code> 结尾）\n\n"
        "例如：<code>myfile_bot</code>\n\n"
        "💡 输入 /cancel 取消操作",
        parse_mode="HTML"
    )
    return INPUT_BOT_USERNAME


async def new_bot_input_username(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """接收 Bot 用户名"""
    username = update.message.text.strip().lstrip('@')

    if not username.lower().endswith('bot'):
        await update.message.reply_text(
            "❌ Bot 用户名必须以 <code>bot</code> 结尾，请重新输入。\n\n"
            "例如：<code>myfile_bot</code>",
            parse_mode="HTML"
        )
        return INPUT_BOT_USERNAME

    context.user_data['new_bot_username'] = username

    await update.message.reply_text(
        f"✅ Bot 用户名：<code>@{escape(username)}</code>\n\n"
        f"请输入 Bot 的 <b>显示名称</b>：\n\n"
        f"例如：<code>我的文件Bot</code>",
        parse_mode="HTML"
    )
    return INPUT_BOT_NAME


async def new_bot_input_name(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """接收 Bot 显示名称，生成 BotFather 创建链接，等待 Token"""
    bot_name = update.message.text.strip()
    bot_username = context.user_data.get('new_bot_username', '')

    if not bot_username:
        await update.message.reply_text("❌ 出错了，请重新使用 /newbot 开始。")
        return ConversationHandler.END

    context.user_data['new_bot_name'] = bot_name
    master_username = context.bot.username

    # 生成 BotFather newbot 深度链接
    encoded_name = urllib.parse.quote(bot_name, safe='')
    create_link = f"https://t.me/newbot/{master_username}/{bot_username}?name={encoded_name}"

    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("🤖 一键创建 Bot", url=create_link)],
    ])

    text = (
        f"✅ <b>创建信息确认</b>\n\n"
        f"Bot 名称：<code>{escape(bot_name)}</code>\n"
        f"Bot 用户名：<code>@{escape(bot_username)}</code>\n\n"
        f"📌 <b>创建链接：</b>\n"
        f"<code>{escape(create_link)}</code>\n\n"
        f"👇 <b>下一步：</b>\n"
        f"1. 点击上方按钮创建 Bot\n"
        f"2. BotFather 会给你一个 <b>Token</b>\n"
        f"3. <b>直接把 Token 发到这里</b>，我会自动启动你的 Bot\n\n"
        f"💡 输入 /cancel 取消操作"
    )

    await update.message.reply_text(text, parse_mode="HTML", reply_markup=keyboard)
    return INPUT_BOT_TOKEN


async def new_bot_input_token(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """接收 Token 并自动注册启动 Bot"""
    token = update.message.text.strip()
    user_id = update.effective_user.id

    # 检查 Token 格式
    if ":" not in token or len(token) < 10:
        await update.message.reply_text(
            "❌ 这不像是一个有效的 Token，请重新输入。\n\n"
            "Token 格式类似：<code>123456789:ABCdefGHIjklMNOpqrS</code>",
            parse_mode="HTML"
        )
        return INPUT_BOT_TOKEN

    # 检查 Token 是否已存在
    existing = get_user_bot_by_token(token)
    if existing:
        await update.message.reply_text(
            f"⚠️ Bot @{escape(existing['bot_username'])} 已经添加过了。"
        )
        context.user_data.pop('new_bot_username', None)
        context.user_data.pop('new_bot_name', None)
        return ConversationHandler.END

    status_msg = await update.message.reply_text("⏳ 正在校验 Token 并启动 Bot...")

    # 校验 Token
    from telegram import Bot
    test_bot = None
    try:
        test_bot = Bot(token=token)
        bot_info = await test_bot.get_me()
    except Exception as e:
        await status_msg.edit_text(
            f"❌ Token 校验失败：{escape(str(e)[:100])}\n\n请重新输入正确的 Token。"
        )
        return INPUT_BOT_TOKEN
    finally:
        if test_bot:
            try:
                await test_bot.shutdown()
            except Exception:
                pass

    # 检查 Bot 是否已被添加
    existing_by_id = get_user_bot_by_telegram_id(bot_info.id)
    if existing_by_id:
        await status_msg.edit_text(
            f"⚠️ Bot @{escape(bot_info.username)} 已被添加（可能是不同 Token）。",
            parse_mode="HTML"
        )
        context.user_data.pop('new_bot_username', None)
        context.user_data.pop('new_bot_name', None)
        return ConversationHandler.END

    # 检查用户 Bot 数量
    from config import MAX_BOTS_PER_USER
    user_bots = get_user_bots_by_owner(user_id)
    if len(user_bots) >= MAX_BOTS_PER_USER:
        await status_msg.edit_text(
            f"⚠️ 每个用户最多添加 {MAX_BOTS_PER_USER} 个 Bot。\n\n"
            f"请先使用 /delbot 删除已有 Bot。"
        )
        context.user_data.pop('new_bot_username', None)
        context.user_data.pop('new_bot_name', None)
        return ConversationHandler.END

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
        return INPUT_BOT_TOKEN

    # 启动 Bot
    mgr = get_bot_manager()
    if mgr:
        bot_record = get_user_bot_by_id(record_id)
        success = await mgr.start_bot(bot_record)
        if success:
            await status_msg.edit_text(
                f"✅ <b>Bot 创建成功并已启动！</b>\n\n"
                f"🤖 名称：{escape(bot_info.first_name)}\n"
                f"📌 用户名：@{escape(bot_info.username)}\n"
                f"🆔 Bot ID：<code>{bot_info.id}</code>\n\n"
                f"现在直接向 @{escape(bot_info.username)} 发送文件即可使用！",
                parse_mode="HTML"
            )
            logger.info("用户 %s 通过 /newbot 创建并启动了 Bot @%s", user_id, bot_info.username)
        else:
            await status_msg.edit_text(
                f"⚠️ Bot 已保存但启动失败，请联系管理员。\n\n"
                f"🤖 @{escape(bot_info.username)}",
                parse_mode="HTML"
            )
    else:
        await status_msg.edit_text("❌ BotManager 未初始化，请联系管理员。")

    # 清理
    context.user_data.pop('new_bot_username', None)
    context.user_data.pop('new_bot_name', None)
    return ConversationHandler.END


async def new_bot_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """取消创建"""
    context.user_data.pop('new_bot_username', None)
    context.user_data.pop('new_bot_name', None)
    await update.message.reply_text("❌ 已取消创建 Bot。")
    return ConversationHandler.END


# ==================== /addbot ====================

async def add_bot_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/addbot 添加用户Bot"""
    user_id = update.effective_user.id

    if not context.args:
        await update.message.reply_text(
            "🔑 <b>添加 Bot</b>\n\n"
            "请使用以下命令格式：\n"
            "<code>/addbot &lt;Token&gt;</code>\n\n"
            "例如：<code>/addbot 123456:ABCdefGHIjklMNOpqrS</code>\n\n"
            "💡 不知道怎么获取 Token？使用 /newbot 一键创建！",
            parse_mode="HTML"
        )
        return

    token = context.args[0].strip()

    if ":" not in token:
        await update.message.reply_text("❌ Token 格式不正确，请检查后重试。")
        return

    existing = get_user_bot_by_token(token)
    if existing:
        await update.message.reply_text(
            f"⚠️ Bot @{escape(existing['bot_username'])} 已经添加过了。"
        )
        return

    status_msg = await update.message.reply_text("⏳ 正在校验 Token...")

    from telegram import Bot
    test_bot = None
    try:
        test_bot = Bot(token=token)
        bot_info = await test_bot.get_me()
    except Exception as e:
        await status_msg.edit_text(f"❌ Token 校验失败：{escape(str(e)[:100])}\n\n请检查Token是否正确。")
        return
    finally:
        if test_bot:
            try:
                await test_bot.shutdown()
            except Exception:
                pass

    existing_by_id = get_user_bot_by_telegram_id(bot_info.id)
    if existing_by_id:
        await status_msg.edit_text(
            f"⚠️ Bot @{escape(bot_info.username)} 已被添加（可能是不同 Token）。",
            parse_mode="HTML"
        )
        return

    from config import MAX_BOTS_PER_USER
    user_bots = get_user_bots_by_owner(user_id)
    if len(user_bots) >= MAX_BOTS_PER_USER:
        await status_msg.edit_text(
            f"⚠️ 每个用户最多添加 {MAX_BOTS_PER_USER} 个 Bot。\n\n"
            f"请先使用 /delbot 删除已有 Bot。"
        )
        return

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

    mgr = get_bot_manager()
    if mgr:
        bot_record = get_user_bot_by_id(record_id)
        success = await mgr.start_bot(bot_record)
        if success:
            await status_msg.edit_text(
                f"✅ <b>Bot 添加成功！</b>\n\n"
                f"🤖 名称：{escape(bot_info.first_name)}\n"
                f"📌 用户名：@{escape(bot_info.username)}\n"
                f"🆔 Bot ID：<code>{bot_info.id}</code>\n\n"
                f"现在直接向 @{escape(bot_info.username)} 发送文件即可使用！\n"
                f"发送代码即可获取文件。",
                parse_mode="HTML"
            )
            logger.info("用户 %s 添加了 Bot @%s", user_id, bot_info.username)
        else:
            await status_msg.edit_text(
                f"⚠️ Bot 已保存但启动失败，请联系管理员。\n\n"
                f"🤖 @{escape(bot_info.username)}",
                parse_mode="HTML"
            )
    else:
        await status_msg.edit_text("❌ BotManager 未初始化，请联系管理员。")


# ==================== 其他命令 ====================

async def my_bots_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/mybots 查看用户的Bot列表"""
    user_id = update.effective_user.id
    bots = get_user_bots_by_owner(user_id)

    if not bots:
        await update.message.reply_text(
            "📭 你还没有添加任何 Bot。\n\n使用 /newbot 一键创建！"
        )
        return

    mgr = get_bot_manager()
    text = "📋 <b>我的 Bot 列表：</b>\n\n"
    for i, bot in enumerate(bots, 1):
        is_running = mgr and bot['id'] in mgr.get_all_apps()
        status_emoji = "🟢" if is_running else "🔴"
        text += (
            f"{i}. {status_emoji} <b>{escape(bot['bot_firstname'])}</b>\n"
            f"   @{escape(bot['bot_username'])} | ID: <code>{bot['bot_id']}</code>\n\n"
        )

    text += f"共 {len(bots)} 个 Bot"
    await update.message.reply_text(text, parse_mode="HTML")


async def delete_bot_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/delbot 删除用户Bot"""
    user_id = update.effective_user.id

    if not context.args:
        await update.message.reply_text(
            "请提供 Bot 的用户名或编号。\n"
            "用法：<code>/delbot @用户名</code> 或 <code>/delbot 编号</code>\n\n"
            "使用 /mybots 查看你的 Bot 列表。",
            parse_mode="HTML"
        )
        return

    bots = get_user_bots_by_owner(user_id)
    if not bots:
        await update.message.reply_text("📭 你没有可删除的 Bot。")
        return

    arg = context.args[0].strip()
    target_bot = None

    try:
        idx = int(arg) - 1
        if 0 <= idx < len(bots):
            target_bot = bots[idx]
    except ValueError:
        pass

    if not target_bot:
        username = arg.lstrip('@')
        for bot in bots:
            if bot['bot_username'].lower() == username.lower():
                target_bot = bot
                break

    if not target_bot:
        await update.message.reply_text(
            "❌ 未找到指定的 Bot。使用 /mybots 查看列表。",
            parse_mode="HTML"
        )
        return

    mgr = get_bot_manager()
    if mgr:
        await mgr.stop_bot(target_bot['id'])

    db_delete_user_bot(target_bot['id'])

    await update.message.reply_text(
        f"✅ Bot @{escape(target_bot['bot_username'])} 已删除。"
    )
    logger.info("用户 %s 删除了 Bot @%s", user_id, target_bot['bot_username'])


async def bot_status_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/botstatus 查看Bot运行状态"""
    user_id = update.effective_user.id
    bots = get_user_bots_by_owner(user_id)

    if not bots:
        await update.message.reply_text(
            "📭 你没有 Bot。使用 /newbot 创建！"
        )
        return

    mgr = get_bot_manager()
    text = "🚀 <b>Bot 运行状态：</b>\n\n"
    for bot in bots:
        is_running = mgr and bot['id'] in mgr.get_all_apps()
        status = "🟢 运行中" if is_running else "🔴 已停止"
        text += f"- @{escape(bot['bot_username'])}: {status}\n"

    await update.message.reply_text(text, parse_mode="HTML")


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
        f"📊 <b>平台统计</b>\n\n"
        f"🤖 活跃 Bot 数: {stats['bot_count']} (运行中: {running})\n"
        f"👥 Bot 所有者数: {stats['owner_count']}\n"
        f"📁 总文件数: {stats['file_count']}\n"
        f"📦 总集合数: {stats['col_count']}"
    )
    await update.message.reply_text(text, parse_mode="HTML")