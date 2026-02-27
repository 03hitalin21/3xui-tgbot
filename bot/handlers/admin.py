from telegram import Update
from telegram.ext import ContextTypes, ConversationHandler

import db
from bot.constants import DEFAULT_LIMIT_IP
from bot import utils
from bot.utils import *

async def register_agent_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    u = update.effective_user
    db.ensure_agent(u.id, u.username or "", u.full_name or "", role="buyer")
    context.user_data["flow"] = "register_agent_experience"
    await update.message.reply_text(
        "برای ثبت‌نام نماینده، لطفاً تعداد سال سابقه فروش VPN را ارسال کنید (مثال: 2).",
        reply_markup=cancel_keyboard(),
    )

async def use_plan_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if len(context.args) != 1:
        await update.message.reply_text("فرمت: /useplan <id>")
        return
    pid = as_int(context.args[0])
    if not pid:
        await update.message.reply_text("شناسه پلن نامعتبر است")
        return
    plans = db.list_plan_templates(get_user_role(update.effective_user.id))
    plan = core_orders.validate_plan_selection(pid, plans)
    if not plan:
        await update.message.reply_text("پلن پیدا نشد")
        return
    context.user_data["flow"] = "wizard_inbound"
    context.user_data["wizard"] = {
        "kind": "single",
        "tg_id": update.effective_user.id,
        "days": int(plan["days"]),
        "gb": int(plan["gb"]),
        "limit_ip": int(plan["limit_ip"]),
    }
    await update.message.reply_text("پلن انتخاب شد. حالا شناسه اینباند را ارسال کنید (یا default).")

async def start_broadcast(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    ctx = context.application.bot_data["ctx"]
    if not is_admin(update.effective_user.id):
        await update.effective_message.reply_text("فقط ادمین می‌تواند از این دستور استفاده کند.")
        return ConversationHandler.END
    context.user_data.pop("broadcast", None)
    await update.effective_message.reply_text(
        "ارسال به: همه کاربران / فقط نمایندگان؟",
        reply_markup=broadcast_target_keyboard(),
    )
    return BROADCAST_CHOOSE_TARGET

async def choose_broadcast_target(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    if not is_admin(update.effective_user.id):
        return ConversationHandler.END
    query = update.callback_query
    await query.answer()
    data = query.data.split(":")[-1]
    if data not in {"all", "agents"}:
        await query.edit_message_text("گیرنده نامعتبر است. دوباره /broadcast را اجرا کنید.")
        return ConversationHandler.END
    context.user_data["broadcast"] = {
        "target": data,
    }
    await query.edit_message_text(
        "حالا پیام موردنظر برای ارسال همگانی را بفرستید (متن/عکس/فایل). برای لغو /cancel را بزنید."
    )
    return BROADCAST_SEND_MESSAGE

async def receive_broadcast_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    if not is_admin(update.effective_user.id):
        return ConversationHandler.END
    message = update.effective_message
    if message.text and is_cancel(message.text):
        await message.reply_text("ارسال همگانی لغو شد.")
        return ConversationHandler.END

    broadcast = context.user_data.get("broadcast") or {}
    broadcast["source_chat_id"] = message.chat_id
    broadcast["source_message_id"] = message.message_id
    if message.text:
        broadcast["preview_text"] = message.text
    else:
        broadcast["preview_text"] = message.caption or "[پیام رسانه‌ای]"
    context.user_data["broadcast"] = broadcast

    target = broadcast.get("target", "all")
    target_title = "همه کاربران" if target == "all" else "نمایندگان"
    count = db.count_broadcast_targets(target)

    if message.text is None:
        await context.bot.copy_message(
            chat_id=message.chat_id,
            from_chat_id=message.chat_id,
            message_id=message.message_id,
        )
        preview_message = (
            "پیش‌نمایش پیام همگانی:\n\n"
            f"[پیش‌نمایش رسانه در بالا]\n\n"
            f"برای: {count} {target_title}\nتأیید می‌کنید؟"
        )
    else:
        preview_message = (
            "پیش‌نمایش پیام همگانی:\n\n"
            f"{broadcast['preview_text']}\n\n"
            f"برای: {count} {target_title}\nتأیید می‌کنید؟"
        )

    await message.reply_text(preview_message, reply_markup=broadcast_confirm_keyboard())
    return BROADCAST_PREVIEW_CONFIRM

async def broadcast_preview_action(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    if not is_admin(update.effective_user.id):
        return ConversationHandler.END
    query = update.callback_query
    await query.answer()
    action = query.data.split(":")[-1]
    if action == "edit":
        await query.edit_message_text(
            "پیام جدید برای ارسال همگانی را بفرستید (متن/عکس/فایل). برای لغو /cancel را بزنید."
        )
        return BROADCAST_SEND_MESSAGE
    if action == "cancel":
        await query.edit_message_text("ارسال همگانی لغو شد.")
        return ConversationHandler.END
    if action != "confirm":
        await query.edit_message_text("عملیات نامعتبر است.")
        return ConversationHandler.END

    broadcast = context.user_data.get("broadcast") or {}
    target = broadcast.get("target", "all")
    ids = db.list_broadcast_target_ids(target)
    ids = [uid for uid in ids if uid != ADMIN_TELEGRAM_ID]

    sent = 0
    failed = 0
    for uid in ids:
        try:
            await context.bot.copy_message(
                chat_id=uid,
                from_chat_id=broadcast.get("source_chat_id"),
                message_id=broadcast.get("source_message_id"),
            )
            sent += 1
        except Exception as exc:
            failed += 1
            logger.warning("broadcast_failed | user=%s | error=%s", uid, exc)
    logger.info("broadcast_complete | target=%s | sent=%s | failed=%s", target, sent, failed)
    await query.edit_message_text(f"پیام همگانی با موفقیت برای {sent} از {sent + failed} کاربر ارسال شد.")
    return ConversationHandler.END

async def broadcast_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    if is_admin(update.effective_user.id):
        await update.effective_message.reply_text("ارسال همگانی لغو شد.")
    return ConversationHandler.END
