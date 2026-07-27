"""用户购买 Telegram Premium 会员 - 用 ✨ 星星支付，Bot 赠送官方 Premium 订阅。

价格 = 官方基础价（PREMIUM_GIFT_PRICES）+ 服务费（PREMIUM_USER_MARKUP）。
Bot 收到用户支付的星星后，调用 giftPremiumSubscription 为用户开通 Premium，
成本为基础价，服务费部分为 Bot 收益。若开通失败则自动退款。
"""
import logging
import time
from senders import _retry_send

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, LabeledPrice
from telegram.error import TelegramError
from telegram.ext import ContextTypes

from config import PREMIUM_GIFT_PRICES, PREMIUM_USER_MARKUP
from db.vip import record_star_payment
from handlers.master._utils import escape
# 复用 VIP 支付的二次验证逻辑（stars.py 不在模块级反向 import 本模块，无循环依赖）
from handlers.master.stars import _verify_star_payment

logger = logging.getLogger(__name__)


def _premium_user_price(months: int) -> int:
    """用户购买价 = 官方基础价 + 服务费"""
    return PREMIUM_GIFT_PRICES[months] + PREMIUM_USER_MARKUP


async def premium_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/premium 查看 Telegram Premium 会员方案并购买"""
    markup = PREMIUM_USER_MARKUP
    text = (
        "💎 <b>购买 Telegram Premium 会员</b>\n\n"
        "用 ✨ 星星支付，Bot 将为你开通官方 Telegram Premium 会员。\n"
        f"价格 = 官方基础价 + {markup}⭐ 服务费。\n\n"
        "━━━━━━━━━━━━━━━━━━\n"
    )

    keyboard = []
    for months in sorted(PREMIUM_GIFT_PRICES.keys()):
        base = PREMIUM_GIFT_PRICES[months]
        price = base + markup
        text += f"⏱ <b>{months} 个月</b>：{price}⭐（含 {markup}⭐ 服务费）\n"
        keyboard.append([InlineKeyboardButton(
            f"💎 {months} 个月  {price}⭐",
            callback_data=f"buy_premium|{months}",
        )])

    text += (
        "\n💡 Premium 会员享受：无广告、4G 文件上传、专属贴纸/表情、更快的下载速度等。"
    )

    await _retry_send(update.message.reply_text, text, parse_mode="HTML",
                      reply_markup=InlineKeyboardMarkup(keyboard))


async def buy_premium_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """发起 Premium 购买支付（发送 Invoice）"""
    query = update.callback_query
    parts = query.data.split("|")
    if len(parts) != 2:
        await query.answer("❌ 参数错误", show_alert=True)
        return

    try:
        months = int(parts[1])
    except ValueError:
        await query.answer("❌ 参数错误", show_alert=True)
        return

    if months not in PREMIUM_GIFT_PRICES:
        await query.answer("❌ 无效的方案", show_alert=True)
        return

    await query.answer()

    user_id = update.effective_user.id
    price = _premium_user_price(months)
    base = PREMIUM_GIFT_PRICES[months]

    title = f"Telegram Premium {months} 个月"
    description = (
        f"购买 Telegram Premium 会员 {months} 个月\n"
        f"由 Bot 赠送官方 Premium 订阅\n"
        f"基础价 {base}⭐ + 服务费 {PREMIUM_USER_MARKUP}⭐ = {price}⭐"
    )
    # payload: premium_{months}_{user_id}_{timestamp}
    payload = f"premium_{months}_{user_id}_{int(time.time())}"
    prices = [LabeledPrice(label=title, amount=price)]

    try:
        await context.bot.send_invoice(
            chat_id=user_id,
            title=title,
            description=description,
            payload=payload,
            provider_token="",  # Telegram Stars 支付留空
            currency="XTR",
            prices=prices,
        )
    except TelegramError as e:
        logger.error("发送 Premium Invoice 失败: %s", e)
        await _retry_send(query.message.reply_text,
                          f"❌ 发起支付失败：{escape(str(e)[:100])}\n请稍后重试。")


async def handle_premium_payment_success(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Premium 支付成功后：二次验证 -> 赠送 Premium -> 失败则退款。

    由 stars.successful_payment_handler 在 payload 前缀为 premium_ 时调用。
    """
    payment = update.message.successful_payment
    user_id = update.effective_user.id
    payload = payment.invoice_payload
    # premium_{months}_{user_id}_{ts}
    parts = payload.split("_")
    try:
        months = int(parts[1])
    except (ValueError, IndexError):
        logger.error("解析 Premium payload 失败: %s", payload)
        await _retry_send(update.message.reply_text, "⚠️ 支付信息异常，请联系管理员。")
        return

    if months not in PREMIUM_GIFT_PRICES:
        logger.error("无效的 Premium months=%s, payload=%s", months, payload)
        await _retry_send(update.message.reply_text, "⚠️ 支付信息异常，请联系管理员。")
        return

    base_price = PREMIUM_GIFT_PRICES[months]
    telegram_charge_id = payment.telegram_payment_charge_id

    # ======== 二次验证：确认支付真实性 ========
    verified = await _verify_star_payment(
        bot=context.bot,
        telegram_charge_id=telegram_charge_id,
        expected_user_id=user_id,
        expected_amount=payment.total_amount,
    )
    if not verified:
        logger.warning("用户 %s Premium 支付验证失败 charge_id=%s", user_id, telegram_charge_id)
        await _retry_send(update.message.reply_text,
                          "⚠️ 支付验证失败，请稍后重试或联系管理员。")
        return

    # 记录支付（vip_level=0 表示非 VIP 升级；payload 前缀 premium_ 用于区分）
    await record_star_payment(
        user_id=user_id,
        amount=payment.total_amount,
        vip_level=0,
        months=months,
        payload=payload,
        telegram_charge_id=telegram_charge_id,
    )

    # ======== 赠送 Premium 会员（从 Bot 星星余额支付基础价） ========
    try:
        await context.bot.gift_premium_subscription(
            user_id=user_id,
            month_count=months,
            star_count=base_price,
        )
        text = (
            f"🎉 <b>Premium 会员开通成功！</b>\n\n"
            f"💎 <b>时长：</b>{months} 个月\n"
            f"⭐ <b>支付：</b>{payment.total_amount} 星星\n\n"
            f"Telegram Premium 已发放到你的账户，请重新进入 Telegram 查看会员生效。"
        )
        logger.info("用户 %s 购买 Premium %d 个月成功（支付 %d⭐，成本 %d⭐，收益 %d⭐）",
                    user_id, months, payment.total_amount, base_price,
                    payment.total_amount - base_price)
        await _retry_send(update.message.reply_text, text, parse_mode="HTML")
    except TelegramError as e:
        # 已收款但赠送失败：必须退款，否则用户付了钱却没收到会员
        logger.error("赠送 Premium 给用户 %s 失败（已收 %d⭐）: %s",
                     user_id, payment.total_amount, e)
        refunded = await _refund_premium(context, user_id, telegram_charge_id)
        if refunded:
            await _retry_send(update.message.reply_text,
                "⚠️ Premium 开通失败，已自动退还你支付的星星。\n请稍后重试或联系管理员。")
        else:
            await _retry_send(update.message.reply_text,
                "⚠️ Premium 开通失败且自动退款异常。请联系管理员，并保留你的支付凭证。")


async def _refund_premium(context, user_id: int, telegram_charge_id: str) -> bool:
    """退还用户的星标支付，返回是否成功"""
    try:
        await context.bot.refund_star_payment(
            user_id=user_id,
            telegram_payment_charge_id=telegram_charge_id,
        )
        logger.info("已退款用户 %s 的 Premium 购买 charge_id=%s", user_id, telegram_charge_id)
        return True
    except TelegramError as e:
        logger.error("退款失败 charge_id=%s: %s", telegram_charge_id, e)
        return False
