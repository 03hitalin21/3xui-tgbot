from telegram import Update
from telegram.ext import ContextTypes

import db
from bot import utils
from bot.utils import *

async def photo_flow(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if context.user_data.get("flow") != "topup_receipt":
        await update.message.reply_text("برای این عکس عملیاتی تعریف نشده است.")
        return
    req_id = context.user_data.get("topup_request_id")
    if not req_id:
        await update.message.reply_text("درخواست شارژ یافت نشد. دوباره /topup بزنید.")
        return
    photo = update.message.photo[-1]
    db.attach_topup_receipt(int(req_id), photo.file_id)
    context.user_data["flow"] = None
    context.user_data.pop("topup_request_id", None)
    await update.message.reply_text(f"✅ رسید برای درخواست #{req_id} ثبت شد. منتظر تایید ادمین باشید.")
    await context.bot.send_message(chat_id=ADMIN_TELEGRAM_ID, text=f"درخواست شارژ جدید #{req_id} برای تایید: /approvetopupid {req_id}")
    try:
        await context.bot.send_photo(chat_id=ADMIN_TELEGRAM_ID, photo=photo.file_id, caption=f"Receipt #{req_id}")
    except Exception:
        pass

async def topup(update: Update, context: ContextTypes.DEFAULT_TYPE):
    ctx = context.application.bot_data["ctx"]
    if len(context.args) != 1:
        await update.message.reply_text("فرمت: /topup <amount>")
        return
    amt, err = core_wallet.validate_topup_request(context.args[0])
    if err:
        await update.message.reply_text(err)
        return
    req_id = db.create_topup_request(update.effective_user.id, amt)
    context.user_data["flow"] = "topup_receipt"
    context.user_data["topup_request_id"] = req_id
    details = manual_payment_text()
    msg = [f"درخواست #{req_id} ثبت شد."]
    if details:
        msg.append(details)
    msg.append("پس از انتقال، لطفاً رسید پرداخت را به صورت عکس همینجا ارسال کنید.")
    msg.append("پس از ارسال رسید، ادمین می‌تواند با /approvetopupid درخواست را تأیید کند.")
    await update.message.reply_text("\n\n".join(msg))

async def approve_topup_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        await update.message.reply_text("دسترسی ندارید")
        return
    if len(context.args) != 1:
        await update.message.reply_text("فرمت: /approvetopupid <topupid>")
        return
    req_id = as_int(context.args[0])
    if not req_id:
        await update.message.reply_text("شناسه نامعتبر است")
        return
    try:
        bal = core_wallet.apply_topup(req_id, update.effective_user.id, db)
    except ValueError as exc:
        await update.message.reply_text(str(exc))
        return
    req = db.get_topup_request(req_id)
    await update.message.reply_text(f"✅ درخواست #{req_id} تایید شد. موجودی جدید کاربر: {toman(bal)}")
    if req:
        await context.bot.send_message(chat_id=int(req["tg_id"]), text=f"✅ درخواست شارژ #{req_id} تایید شد.")
